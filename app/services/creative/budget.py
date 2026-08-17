from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator


class BudgetExceededError(RuntimeError):
    """Raised before provider submission when the iteration cap is exhausted."""


class BudgetStateError(RuntimeError):
    """Raised for an invalid or unsafe budget operation transition."""


@dataclass(frozen=True)
class BudgetReservation:
    scope_id: str
    operation_id: str
    amount_microusd: int
    description: str
    status: str = "reserved"
    provider_job_id: str | None = None


@dataclass(frozen=True)
class BudgetSnapshot:
    scope_id: str
    cap_microusd: int
    reserved_microusd: int
    charged_microusd: int
    remaining_microusd: int


class IterationBudgetLedger:
    """A durable, process-safe paid-operation ledger backed by SQLite."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        scope_id: str,
        cap_microusd: int,
    ) -> None:
        if not scope_id.strip():
            raise ValueError("scope_id is required")
        if cap_microusd <= 0:
            raise ValueError("cap_microusd must be positive")

        self.database_path = Path(database_path)
        self.scope_id = scope_id
        self.cap_microusd = int(cap_microusd)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_scopes (
                    scope_id TEXT PRIMARY KEY,
                    cap_microusd INTEGER NOT NULL CHECK (cap_microusd > 0),
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_operations (
                    scope_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    amount_microusd INTEGER NOT NULL CHECK (amount_microusd > 0),
                    description TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('reserved', 'submitted', 'released', 'manual_charge')
                    ),
                    provider_job_id TEXT,
                    release_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scope_id, operation_id),
                    FOREIGN KEY (scope_id) REFERENCES budget_scopes(scope_id)
                )
                """
            )
            existing = connection.execute(
                "SELECT cap_microusd FROM budget_scopes WHERE scope_id = ?",
                (self.scope_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO budget_scopes(scope_id, cap_microusd, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (self.scope_id, self.cap_microusd, self._now()),
                )
            elif int(existing["cap_microusd"]) != self.cap_microusd:
                raise BudgetStateError(
                    f"scope {self.scope_id!r} already has a different budget cap"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _totals(self, connection: sqlite3.Connection) -> tuple[int, int]:
        row = connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status = 'reserved' THEN amount_microusd ELSE 0 END), 0)
                    AS reserved,
                COALESCE(SUM(CASE WHEN status IN ('submitted', 'manual_charge')
                    THEN amount_microusd ELSE 0 END), 0) AS charged
            FROM budget_operations
            WHERE scope_id = ?
            """,
            (self.scope_id,),
        ).fetchone()
        return int(row["reserved"]), int(row["charged"])

    def _assert_fits(
        self,
        connection: sqlite3.Connection,
        amount_microusd: int,
    ) -> None:
        reserved, charged = self._totals(connection)
        remaining = self.cap_microusd - reserved - charged
        if amount_microusd > remaining:
            raise BudgetExceededError(
                "iteration budget exceeded: "
                f"requested {amount_microusd} micro-USD, "
                f"remaining {remaining} micro-USD, cap {self.cap_microusd} micro-USD"
            )

    def reserve(
        self,
        operation_id: str,
        amount_microusd: int,
        description: str,
    ) -> BudgetReservation:
        if not operation_id.strip() or not description.strip():
            raise ValueError("operation_id and description are required")
        if amount_microusd <= 0:
            raise ValueError("amount_microusd must be positive")

        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM budget_operations
                WHERE scope_id = ? AND operation_id = ?
                """,
                (self.scope_id, operation_id),
            ).fetchone()
            if existing is not None:
                if int(existing["amount_microusd"]) != int(amount_microusd):
                    raise BudgetStateError(
                        f"operation {operation_id!r} already exists with a different amount"
                    )
                if existing["status"] != "reserved":
                    raise BudgetStateError(
                        f"operation {operation_id!r} is already {existing['status']}"
                    )
                return self._reservation_from_row(existing)

            self._assert_fits(connection, int(amount_microusd))
            timestamp = self._now()
            connection.execute(
                """
                INSERT INTO budget_operations(
                    scope_id, operation_id, amount_microusd, description, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (
                    self.scope_id,
                    operation_id,
                    int(amount_microusd),
                    description,
                    timestamp,
                    timestamp,
                ),
            )
            return BudgetReservation(
                scope_id=self.scope_id,
                operation_id=operation_id,
                amount_microusd=int(amount_microusd),
                description=description,
            )

    def mark_submitted(
        self,
        operation_id: str,
        *,
        provider_job_id: str,
    ) -> BudgetReservation:
        if not provider_job_id.strip():
            raise ValueError("provider_job_id is required")
        with self._transaction() as connection:
            row = self._get_operation(connection, operation_id)
            if row["status"] == "submitted":
                if row["provider_job_id"] != provider_job_id:
                    raise BudgetStateError(
                        f"submitted operation {operation_id!r} has a different provider job"
                    )
                return self._reservation_from_row(row)
            if row["status"] != "reserved":
                raise BudgetStateError(
                    f"operation {operation_id!r} cannot be submitted from {row['status']}"
                )
            connection.execute(
                """
                UPDATE budget_operations
                SET status = 'submitted', provider_job_id = ?, updated_at = ?
                WHERE scope_id = ? AND operation_id = ?
                """,
                (provider_job_id, self._now(), self.scope_id, operation_id),
            )
            updated = self._get_operation(connection, operation_id)
            return self._reservation_from_row(updated)

    def release(self, operation_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("release reason is required")
        with self._transaction() as connection:
            row = self._get_operation(connection, operation_id)
            if row["status"] == "released":
                return
            if row["status"] != "reserved":
                raise BudgetStateError(
                    f"operation {operation_id!r} is {row['status']} and cannot be released"
                )
            connection.execute(
                """
                UPDATE budget_operations
                SET status = 'released', release_reason = ?, updated_at = ?
                WHERE scope_id = ? AND operation_id = ?
                """,
                (reason, self._now(), self.scope_id, operation_id),
            )

    def record_manual_charge(
        self,
        operation_id: str,
        amount_microusd: int,
        description: str,
    ) -> BudgetReservation:
        if not operation_id.strip() or not description.strip():
            raise ValueError("operation_id and description are required")
        if amount_microusd <= 0:
            raise ValueError("amount_microusd must be positive")
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT * FROM budget_operations
                WHERE scope_id = ? AND operation_id = ?
                """,
                (self.scope_id, operation_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["status"] == "manual_charge"
                    and int(existing["amount_microusd"]) == int(amount_microusd)
                ):
                    return self._reservation_from_row(existing)
                raise BudgetStateError(
                    f"operation {operation_id!r} already exists with incompatible state"
                )

            self._assert_fits(connection, int(amount_microusd))
            timestamp = self._now()
            connection.execute(
                """
                INSERT INTO budget_operations(
                    scope_id, operation_id, amount_microusd, description, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'manual_charge', ?, ?)
                """,
                (
                    self.scope_id,
                    operation_id,
                    int(amount_microusd),
                    description,
                    timestamp,
                    timestamp,
                ),
            )
            row = self._get_operation(connection, operation_id)
            return self._reservation_from_row(row)

    def ensure_available(self, amount_microusd: int) -> BudgetSnapshot:
        """Check a prospective charge without creating a durable reservation."""

        if amount_microusd <= 0:
            raise ValueError("amount_microusd must be positive")
        connection = self._connect()
        try:
            self._assert_fits(connection, int(amount_microusd))
            reserved, charged = self._totals(connection)
        finally:
            connection.close()
        return BudgetSnapshot(
            scope_id=self.scope_id,
            cap_microusd=self.cap_microusd,
            reserved_microusd=reserved,
            charged_microusd=charged,
            remaining_microusd=self.cap_microusd - reserved - charged,
        )

    def snapshot(self) -> BudgetSnapshot:
        connection = self._connect()
        try:
            reserved, charged = self._totals(connection)
        finally:
            connection.close()
        return BudgetSnapshot(
            scope_id=self.scope_id,
            cap_microusd=self.cap_microusd,
            reserved_microusd=reserved,
            charged_microusd=charged,
            remaining_microusd=self.cap_microusd - reserved - charged,
        )

    def _get_operation(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM budget_operations
            WHERE scope_id = ? AND operation_id = ?
            """,
            (self.scope_id, operation_id),
        ).fetchone()
        if row is None:
            raise BudgetStateError(f"unknown budget operation {operation_id!r}")
        return row

    @staticmethod
    def _reservation_from_row(row: sqlite3.Row) -> BudgetReservation:
        return BudgetReservation(
            scope_id=str(row["scope_id"]),
            operation_id=str(row["operation_id"]),
            amount_microusd=int(row["amount_microusd"]),
            description=str(row["description"]),
            status=str(row["status"]),
            provider_job_id=row["provider_job_id"],
        )
