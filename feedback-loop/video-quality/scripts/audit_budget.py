from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402


DEFAULT_BUDGET_DATABASE = LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
DEFAULT_SCOPE_ID = "mixed-media-iteration-001"
DEFAULT_CAP_MICROUSD = 10_000_000
_STATUSES = ("reserved", "submitted", "released", "manual_charge")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit paid-operation ledger entries without changing them by default"
        )
    )
    parser.add_argument(
        "--budget-database",
        default=str(DEFAULT_BUDGET_DATABASE),
    )
    parser.add_argument("--scope-id", default=DEFAULT_SCOPE_ID)
    parser.add_argument("--cap-microusd", type=int, default=DEFAULT_CAP_MICROUSD)
    parser.add_argument(
        "--status",
        choices=("open", "all", *_STATUSES),
        default="open",
        help=(
            "Operation status to list in read-only audit mode; open includes "
            "both reserved and submitted operations"
        ),
    )
    parser.add_argument(
        "--reconcile-operation",
        help=(
            "Exact submitted operation ID to retain as a conservative manual charge"
        ),
    )
    parser.add_argument(
        "--reason",
        help="Audit reason retained with an explicit reconciliation",
    )
    parser.add_argument(
        "--confirm-retain-charge",
        choices=("YES",),
        help="Required for the submitted-to-manual-charge transition",
    )
    args = parser.parse_args(argv)
    if args.reconcile_operation:
        if not args.reason or not args.reason.strip():
            parser.error("--reason is required with --reconcile-operation")
        if args.confirm_retain_charge != "YES":
            parser.error(
                "--confirm-retain-charge YES is required with "
                "--reconcile-operation"
            )
    elif args.reason or args.confirm_retain_charge:
        parser.error(
            "--reason and --confirm-retain-charge require --reconcile-operation"
        )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    database_path = Path(args.budget_database).expanduser().resolve()
    if args.reconcile_operation:
        if not database_path.is_file():
            raise FileNotFoundError(
                f"budget database does not exist: {database_path}"
            )
        ledger = IterationBudgetLedger(
            database_path,
            scope_id=args.scope_id,
            cap_microusd=args.cap_microusd,
        )
        operation = ledger.reconcile_submitted_as_manual_charge(
            args.reconcile_operation,
            reason=args.reason,
        )
        payload = {
            "mode": "reconcile_retain_charge",
            "operation": asdict(operation),
            "budget": asdict(ledger.snapshot()),
        }
    else:
        if args.status == "open":
            statuses = ("reserved", "submitted")
        elif args.status == "all":
            statuses = None
        else:
            statuses = (args.status,)
        audit = IterationBudgetLedger.read_only_audit(
            database_path,
            scope_id=args.scope_id,
            cap_microusd=args.cap_microusd,
            statuses=statuses,
        )
        payload = {
            "mode": "audit",
            "status_filter": args.status,
            "database_exists": audit.database_exists,
            "operation_count": len(audit.operations),
            "operations": [
                asdict(operation) for operation in audit.operations
            ],
            "budget": asdict(audit.snapshot),
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
