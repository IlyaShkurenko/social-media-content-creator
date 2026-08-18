from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Iterator


_BUDGET_OPERATION_STATUSES = frozenset(
    {"reserved", "submitted", "released", "manual_charge"}
)


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
class BudgetOperation:
    """Auditable persisted state for one paid-operation ledger entry."""

    scope_id: str
    operation_id: str
    amount_microusd: int
    description: str
    status: str
    provider_job_id: str | None
    status_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class BudgetSnapshot:
    scope_id: str
    cap_microusd: int
    reserved_microusd: int
    charged_microusd: int
    remaining_microusd: int


@dataclass(frozen=True)
class BudgetAuditView:
    """Immutable snapshot loaded without creating or updating a ledger."""

    database_exists: bool
    snapshot: BudgetSnapshot
    operations: tuple[BudgetOperation, ...]


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
        *,
        allow_existing: bool = True,
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
                if not allow_existing:
                    raise BudgetStateError(
                        f"operation {operation_id!r} already has a reservation"
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

    def reconcile_submitted_as_manual_charge(
        self,
        operation_id: str,
        *,
        reason: str,
    ) -> BudgetOperation:
        """Retain an accepted charge while closing a legacy submitted entry.

        This transition is intentionally narrow: it never releases or changes the
        conservative amount, and it preserves any provider job/response identifier.
        """

        if not operation_id.strip():
            raise ValueError("operation_id is required")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("reconciliation reason is required")

        with self._transaction() as connection:
            row = self._get_operation(connection, operation_id)
            if row["status"] == "manual_charge":
                if row["release_reason"] == normalized_reason:
                    return self._operation_from_row(row)
                raise BudgetStateError(
                    f"operation {operation_id!r} is already manual_charge with "
                    "different reconciliation evidence"
                )
            if row["status"] != "submitted":
                raise BudgetStateError(
                    f"operation {operation_id!r} cannot be reconciled from "
                    f"{row['status']}"
                )
            connection.execute(
                """
                UPDATE budget_operations
                SET status = 'manual_charge', release_reason = ?, updated_at = ?
                WHERE scope_id = ? AND operation_id = ?
                """,
                (
                    normalized_reason,
                    self._now(),
                    self.scope_id,
                    operation_id,
                ),
            )
            updated = self._get_operation(connection, operation_id)
            return self._operation_from_row(updated)

    def list_operations(
        self,
        *,
        status: str | None = None,
    ) -> tuple[BudgetOperation, ...]:
        """Return an ordered, read-only audit view without changing operation state."""

        if status is not None and status not in _BUDGET_OPERATION_STATUSES:
            raise ValueError(f"unknown budget operation status: {status!r}")
        connection = self._connect()
        try:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT * FROM budget_operations
                    WHERE scope_id = ?
                    ORDER BY created_at, operation_id
                    """,
                    (self.scope_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM budget_operations
                    WHERE scope_id = ? AND status = ?
                    ORDER BY created_at, operation_id
                    """,
                    (self.scope_id, status),
                ).fetchall()
        finally:
            connection.close()
        return tuple(self._operation_from_row(row) for row in rows)

    def find_operation(self, operation_id: str) -> BudgetOperation | None:
        """Look up an operation without changing its durable state."""

        if not operation_id.strip():
            raise ValueError("operation_id is required")
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM budget_operations
                WHERE scope_id = ? AND operation_id = ?
                """,
                (self.scope_id, operation_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return self._operation_from_row(row)

    @classmethod
    def read_only_audit(
        cls,
        database_path: str | Path,
        *,
        scope_id: str,
        cap_microusd: int,
        statuses: Iterable[str] | None = None,
    ) -> BudgetAuditView:
        """Inspect a ledger without initializing the database or its scope.

        A missing database represents a clean, zero-spend iteration. Existing
        databases are opened through SQLite's immutable read-only URI so the
        audit cannot create tables, journal files, or update timestamps.
        """

        if not scope_id.strip():
            raise ValueError("scope_id is required")
        if cap_microusd <= 0:
            raise ValueError("cap_microusd must be positive")
        normalized_statuses = None
        if statuses is not None:
            normalized_statuses = tuple(dict.fromkeys(statuses))
            unknown = set(normalized_statuses) - _BUDGET_OPERATION_STATUSES
            if unknown:
                unknown_status = sorted(unknown)[0]
                raise ValueError(
                    f"unknown budget operation status: {unknown_status!r}"
                )

        path = Path(database_path).expanduser().resolve()
        empty_snapshot = BudgetSnapshot(
            scope_id=scope_id,
            cap_microusd=int(cap_microusd),
            reserved_microusd=0,
            charged_microusd=0,
            remaining_microusd=int(cap_microusd),
        )
        if not path.is_file():
            return BudgetAuditView(
                database_exists=False,
                snapshot=empty_snapshot,
                operations=(),
            )

        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            scope = connection.execute(
                "SELECT cap_microusd FROM budget_scopes WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
            if scope is None:
                return BudgetAuditView(
                    database_exists=True,
                    snapshot=empty_snapshot,
                    operations=(),
                )
            stored_cap = int(scope["cap_microusd"])
            if stored_cap != int(cap_microusd):
                raise BudgetStateError(
                    f"scope {scope_id!r} already has a different budget cap"
                )
            reserved, charged = cls._read_totals(connection, scope_id)
            rows: list[sqlite3.Row]
            if normalized_statuses is None:
                rows = connection.execute(
                    """
                    SELECT * FROM budget_operations
                    WHERE scope_id = ?
                    ORDER BY created_at, operation_id
                    """,
                    (scope_id,),
                ).fetchall()
            elif not normalized_statuses:
                rows = []
            else:
                placeholders = ", ".join("?" for _ in normalized_statuses)
                rows = connection.execute(
                    f"""
                    SELECT * FROM budget_operations
                    WHERE scope_id = ? AND status IN ({placeholders})
                    ORDER BY created_at, operation_id
                    """,
                    (scope_id, *normalized_statuses),
                ).fetchall()
        except sqlite3.Error as exc:
            raise BudgetStateError(
                f"cannot read budget ledger at {path}: invalid or incomplete schema"
            ) from exc
        finally:
            connection.close()

        snapshot = BudgetSnapshot(
            scope_id=scope_id,
            cap_microusd=stored_cap,
            reserved_microusd=reserved,
            charged_microusd=charged,
            remaining_microusd=stored_cap - reserved - charged,
        )
        return BudgetAuditView(
            database_exists=True,
            snapshot=snapshot,
            operations=tuple(cls._operation_from_row(row) for row in rows),
        )

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
    def _read_totals(
        connection: sqlite3.Connection,
        scope_id: str,
    ) -> tuple[int, int]:
        row = connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status = 'reserved'
                    THEN amount_microusd ELSE 0 END), 0) AS reserved,
                COALESCE(SUM(CASE WHEN status IN ('submitted', 'manual_charge')
                    THEN amount_microusd ELSE 0 END), 0) AS charged
            FROM budget_operations
            WHERE scope_id = ?
            """,
            (scope_id,),
        ).fetchone()
        return int(row["reserved"]), int(row["charged"])

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

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> BudgetOperation:
        return BudgetOperation(
            scope_id=str(row["scope_id"]),
            operation_id=str(row["operation_id"]),
            amount_microusd=int(row["amount_microusd"]),
            description=str(row["description"]),
            status=str(row["status"]),
            provider_job_id=row["provider_job_id"],
            status_reason=row["release_reason"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
