from __future__ import annotations

from pathlib import Path

import pytest

from app.services.creative.budget import (
    BudgetExceededError,
    BudgetStateError,
    IterationBudgetLedger,
)


def ledger(path: Path) -> IterationBudgetLedger:
    return IterationBudgetLedger(
        path,
        scope_id="iteration-001",
        cap_microusd=10_000_000,
    )


def test_runway_1_2_reservation_reduces_remaining_budget(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    reservation = budget.reserve("hook-1", 600_000, "five-second gen4.5 hook")
    snapshot = budget.snapshot()
    assert reservation.amount_microusd == 600_000
    assert snapshot.reserved_microusd == 600_000
    assert snapshot.remaining_microusd == 9_400_000


def test_runway_1_2_reservations_are_durable_across_instances(tmp_path: Path) -> None:
    database = tmp_path / "budget.sqlite3"
    ledger(database).reserve("hook-1", 600_000, "hook")
    assert ledger(database).snapshot().reserved_microusd == 600_000


def test_runway_1_2_overspend_is_rejected_atomically(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.record_manual_charge("prior", 9_800_000, "prior work")
    with pytest.raises(BudgetExceededError):
        budget.reserve("hook-1", 600_000, "hook")
    snapshot = budget.snapshot()
    assert snapshot.charged_microusd == 9_800_000
    assert snapshot.reserved_microusd == 0


def test_runway_1_3_accepted_job_becomes_charged(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("hook-1", 600_000, "hook")
    budget.mark_submitted("hook-1", provider_job_id="job-123")
    snapshot = budget.snapshot()
    assert snapshot.reserved_microusd == 0
    assert snapshot.charged_microusd == 600_000
    assert snapshot.remaining_microusd == 9_400_000


def test_runway_1_3_unaccepted_reservation_can_be_released(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("hook-1", 600_000, "hook")
    budget.release("hook-1", reason="request rejected before job creation")
    assert budget.snapshot().remaining_microusd == 10_000_000


def test_runway_1_3_submitted_charge_cannot_be_released(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("hook-1", 600_000, "hook")
    budget.mark_submitted("hook-1", provider_job_id="job-123")
    with pytest.raises(BudgetStateError, match="submitted"):
        budget.release("hook-1", reason="creative rejected")


def test_duplicate_operation_is_idempotent_only_for_same_amount(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    first = budget.reserve("hook-1", 600_000, "hook")
    second = budget.reserve("hook-1", 600_000, "hook")
    assert first == second
    with pytest.raises(BudgetStateError, match="different amount"):
        budget.reserve("hook-1", 700_000, "changed hook")
