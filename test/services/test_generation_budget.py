from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.creative.budget import (
    BudgetExceededError,
    BudgetStateError,
    IterationBudgetLedger,
)


AUDIT_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "feedback-loop"
    / "video-quality"
    / "scripts"
    / "audit_budget.py"
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


def test_provider_can_atomically_reject_an_existing_reservation(
    tmp_path: Path,
) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("hook-1", 600_000, "hook")

    with pytest.raises(BudgetStateError, match="already has a reservation"):
        budget.reserve(
            "hook-1",
            600_000,
            "hook",
            allow_existing=False,
        )


def test_runway_1_3_submitted_audit_is_read_only(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("hook-1", 600_000, "Runway accepted job")
    budget.mark_submitted("hook-1", provider_job_id="job-123")
    budget.record_manual_charge("judge-1", 20_000, "Gemini completed response")
    before = budget.snapshot()

    submitted = budget.list_operations(status="submitted")

    assert [operation.operation_id for operation in submitted] == ["hook-1"]
    assert submitted[0].provider_job_id == "job-123"
    assert submitted[0].status_reason is None
    assert budget.snapshot() == before


def test_budget_audit_rejects_an_unknown_status(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")

    with pytest.raises(ValueError, match="unknown budget operation status"):
        budget.list_operations(status="stale")


def test_runway_1_3_reconciliation_preserves_the_conservative_charge(
    tmp_path: Path,
) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("legacy-judge", 57_000, "Legacy Gemini evaluator")
    budget.mark_submitted(
        "legacy-judge",
        provider_job_id="provider-response-123",
    )
    original = budget.list_operations(status="submitted")[0]
    before = budget.snapshot()
    reason = (
        "Provider response ID exists; exact usage evidence is unavailable; "
        "worst-case charge retained."
    )

    reconciled = budget.reconcile_submitted_as_manual_charge(
        "legacy-judge",
        reason=reason,
    )

    assert reconciled.status == "manual_charge"
    assert reconciled.amount_microusd == original.amount_microusd == 57_000
    assert reconciled.provider_job_id == original.provider_job_id
    assert reconciled.description == original.description
    assert reconciled.created_at == original.created_at
    assert reconciled.status_reason == reason
    assert budget.snapshot() == before
    assert budget.list_operations(status="submitted") == ()
    assert budget.list_operations(status="manual_charge") == (reconciled,)


def test_runway_1_3_same_reconciliation_is_idempotent(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("legacy-judge", 57_000, "Legacy Gemini evaluator")
    budget.mark_submitted("legacy-judge", provider_job_id="response-123")
    first = budget.reconcile_submitted_as_manual_charge(
        "legacy-judge",
        reason="Worst-case charge retained.",
    )

    second = budget.reconcile_submitted_as_manual_charge(
        "legacy-judge",
        reason="Worst-case charge retained.",
    )

    assert second == first
    with pytest.raises(BudgetStateError, match="different reconciliation evidence"):
        budget.reconcile_submitted_as_manual_charge(
            "legacy-judge",
            reason="Conflicting reason.",
        )


def test_runway_1_3_only_submitted_operations_can_be_reconciled(
    tmp_path: Path,
) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("reserved", 10_000, "Not submitted")
    budget.reserve("released", 10_000, "Rejected before submission")
    budget.release("released", reason="No billable provider job was accepted")

    with pytest.raises(BudgetStateError, match="from reserved"):
        budget.reconcile_submitted_as_manual_charge(
            "reserved",
            reason="Invalid transition.",
        )
    with pytest.raises(BudgetStateError, match="from released"):
        budget.reconcile_submitted_as_manual_charge(
            "released",
            reason="Invalid transition.",
        )


def test_runway_1_3_reconciled_charge_cannot_be_released(tmp_path: Path) -> None:
    budget = ledger(tmp_path / "budget.sqlite3")
    budget.reserve("legacy-judge", 57_000, "Legacy Gemini evaluator")
    budget.mark_submitted("legacy-judge", provider_job_id="response-123")
    budget.reconcile_submitted_as_manual_charge(
        "legacy-judge",
        reason="Worst-case charge retained.",
    )

    with pytest.raises(BudgetStateError, match="manual_charge"):
        budget.release("legacy-judge", reason="Do not release a retained charge")


def test_budget_audit_cli_is_read_only_by_default(tmp_path: Path) -> None:
    database = tmp_path / "budget.sqlite3"
    budget = ledger(database)
    budget.reserve("uncertain-job", 20_000, "Ambiguous provider response")
    budget.reserve("accepted-job", 30_000, "Accepted asynchronous job")
    budget.mark_submitted("accepted-job", provider_job_id="job-123")
    budget.record_manual_charge("closed-call", 10_000, "Completed response")
    before = budget.snapshot()
    before_bytes = database.read_bytes()
    before_mtime_ns = database.stat().st_mtime_ns

    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--budget-database",
            str(database),
            "--scope-id",
            "iteration-001",
            "--cap-microusd",
            "10000000",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["mode"] == "audit"
    assert payload["status_filter"] == "open"
    assert payload["database_exists"] is True
    assert payload["operation_count"] == 2
    assert {
        operation["operation_id"] for operation in payload["operations"]
    } == {"accepted-job", "uncertain-job"}
    assert ledger(database).snapshot() == before
    assert database.read_bytes() == before_bytes
    assert database.stat().st_mtime_ns == before_mtime_ns


def test_budget_audit_cli_missing_database_reports_clean_zero_without_creating_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing" / "budget.sqlite3"

    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--budget-database",
            str(database),
            "--scope-id",
            "iteration-001",
            "--cap-microusd",
            "10000000",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["mode"] == "audit"
    assert payload["database_exists"] is False
    assert payload["operation_count"] == 0
    assert payload["operations"] == []
    assert payload["budget"] == {
        "scope_id": "iteration-001",
        "cap_microusd": 10_000_000,
        "reserved_microusd": 0,
        "charged_microusd": 0,
        "remaining_microusd": 10_000_000,
    }
    assert not database.exists()
    assert not database.parent.exists()


def test_budget_audit_cli_requires_explicit_retain_charge_confirmation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "budget.sqlite3"
    ledger(database)

    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--budget-database",
            str(database),
            "--scope-id",
            "iteration-001",
            "--cap-microusd",
            "10000000",
            "--reconcile-operation",
            "legacy-call",
            "--reason",
            "Keep the conservative charge.",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--confirm-retain-charge YES is required" in result.stderr


def test_budget_audit_cli_explicit_reconciliation_preserves_charge(
    tmp_path: Path,
) -> None:
    database = tmp_path / "budget.sqlite3"
    budget = ledger(database)
    budget.reserve("legacy-call", 57_000, "Legacy one-shot inference")
    budget.mark_submitted("legacy-call", provider_job_id="response-123")

    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--budget-database",
            str(database),
            "--scope-id",
            "iteration-001",
            "--cap-microusd",
            "10000000",
            "--reconcile-operation",
            "legacy-call",
            "--reason",
            "Exact usage unavailable; worst-case charge retained.",
            "--confirm-retain-charge",
            "YES",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    operation = payload["operation"]
    assert payload["mode"] == "reconcile_retain_charge"
    assert operation["status"] == "manual_charge"
    assert operation["amount_microusd"] == 57_000
    assert operation["provider_job_id"] == "response-123"
    assert payload["budget"]["charged_microusd"] == 57_000
    assert payload["budget"]["remaining_microusd"] == 9_943_000
