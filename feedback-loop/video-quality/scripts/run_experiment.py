from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOOP_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS_ROOT = LOOP_ROOT / "experiments"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError("slug must contain an ASCII letter or digit")
    return slug


def next_experiment_id() -> int:
    ids = []
    for path in EXPERIMENTS_ROOT.iterdir():
        if path.is_dir() and re.match(r"^\d{3}-", path.name):
            ids.append(int(path.name[:3]))
    return max(ids, default=0) + 1


def latest_prior_metrics(current: Path) -> dict[str, Any] | None:
    candidates = []
    for path in EXPERIMENTS_ROOT.glob("[0-9][0-9][0-9]-*/metrics.json"):
        if path.parent != current:
            candidates.append(path)
    if not candidates:
        return None
    latest = max(candidates, key=lambda path: int(path.parent.name[:3]))
    return json.loads(latest.read_text(encoding="utf-8"))


def write_readme(
    output_dir: Path,
    *,
    kind: str,
    hypothesis: str,
    scenario: Path,
    metrics: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    error: str | None = None,
) -> None:
    current_value = metrics["primary"]["value"] if metrics else None
    previous_value = previous["primary"]["value"] if previous else None
    delta = current_value - previous_value if current_value is not None and previous_value is not None else None

    if error:
        decision = "failed"
    elif kind == "baseline":
        decision = "baseline_established"
    elif delta is None or delta <= 0:
        decision = "reject_no_primary_improvement"
    elif metrics["constraints"]["all_goal_constraints_verified"]:
        decision = "keep"
    else:
        decision = "provisional_requires_review"

    lines = [
        f"# {output_dir.name}",
        "",
        f"- Created: `{utc_now()}`",
        f"- Kind: `{kind}`",
        f"- Scenario: `{scenario.name}`",
        f"- Hypothesis: {hypothesis}",
        "",
        "## Change",
        "",
        "Baseline records the current artifact and evaluator. For a product experiment, replace this paragraph with the exact code/configuration change before accepting the result.",
        "",
        "## Results",
        "",
    ]
    if metrics:
        lines.extend(
            [
                f"- Primary `{metrics['primary']['name']}`: `{current_value:.6f}`",
                f"- Previous comparable score: `{previous_value:.6f}`" if previous_value is not None else "- Previous comparable score: `n/a`",
                f"- Delta: `{delta:+.6f}`" if delta is not None else "- Delta: `n/a`",
                f"- Enforced constraints pass: `{metrics['constraints']['all_enforced_pass']}`",
                f"- All goal constraints verified: `{metrics['constraints']['all_goal_constraints_verified']}`",
                f"- Observation mode: `{metrics['observation_mode']}`",
            ]
        )
    if error:
        lines.extend([f"- Evaluator error: `{error}`"])
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"`{decision}`",
            "",
            "## Notes",
            "",
            "The v0 human observation fixture is reproducible but does not replace automated vision/ASR evaluation. Pending required constraints block automatic acceptance.",
            "",
        ]
    )
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Allocate and run a sequential video-quality experiment")
    parser.add_argument("--kind", choices=("baseline", "experiment"), required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--hypothesis", default="Current controlled artifact establishes evaluator-v0 behavior.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--video")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    EXPERIMENTS_ROOT.mkdir(parents=True, exist_ok=True)
    scenario_raw = Path(args.scenario)
    scenario = scenario_raw if scenario_raw.is_absolute() else LOOP_ROOT / scenario_raw
    scenario = scenario.resolve()
    if not scenario.is_file() or LOOP_ROOT.resolve() not in scenario.parents:
        raise ValueError(f"scenario must be a file inside {LOOP_ROOT}: {scenario}")

    experiment_id = next_experiment_id()
    output_dir = EXPERIMENTS_ROOT / f"{experiment_id:03d}-{safe_slug(args.slug)}"
    output_dir.mkdir(parents=False, exist_ok=False)
    scenario_snapshot = output_dir / "scenario.json"
    shutil.copy2(scenario, scenario_snapshot)

    command = [
        sys.executable,
        str(LOOP_ROOT / "evals" / "evaluate.py"),
        "--scenario",
        str(scenario_snapshot),
        "--output",
        str(output_dir),
    ]
    if args.video:
        command.extend(["--video", args.video])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    previous = latest_prior_metrics(output_dir)
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        (output_dir / "evaluator.stderr.log").write_text(error + "\n", encoding="utf-8")
        write_readme(
            output_dir,
            kind=args.kind,
            hypothesis=args.hypothesis,
            scenario=scenario_snapshot,
            metrics=None,
            previous=previous,
            error=error,
        )
        print(f"experiment failed: {output_dir}", file=sys.stderr)
        return result.returncode

    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    write_readme(
        output_dir,
        kind=args.kind,
        hypothesis=args.hypothesis,
        scenario=scenario_snapshot,
        metrics=metrics,
        previous=previous,
    )
    print(result.stdout.strip())
    print(f"experiment={output_dir.relative_to(LOOP_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
