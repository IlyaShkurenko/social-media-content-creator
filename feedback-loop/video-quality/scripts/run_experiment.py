from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]
EXPERIMENTS_ROOT = LOOP_ROOT / "experiments"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _relative(path: Path, root: Path, label: str) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside {root}: {path}") from exc


def _sanitize_record(value: Any, *, key: str = "") -> Any:
    sensitive_fragments = (
        "api_key",
        "authorization",
        "secret",
        "signed_url",
        "output_url",
    )
    if any(fragment in key.lower() for fragment in sensitive_fragments):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_record(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_record(item, key=key) for item in value]
    if isinstance(value, str):
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                return f"<repo>/{candidate.relative_to(REPO_ROOT.resolve())}"
            except ValueError:
                return value
    return value


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


def latest_comparable_metrics(
    current: Path,
    *,
    scenario_id: str,
    evaluator_version: str | None,
) -> dict[str, Any] | None:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for path in EXPERIMENTS_ROOT.glob("[0-9][0-9][0-9]-*/metrics.json"):
        if path.parent == current:
            continue
        metrics = json.loads(path.read_text(encoding="utf-8"))
        if metrics.get("scenario_id") != scenario_id:
            continue
        if (
            evaluator_version is not None
            and metrics.get("evaluator_version") != evaluator_version
        ):
            continue
        candidates.append((int(path.parent.name[:3]), metrics))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def build_started_manifest(
    *,
    scenario: dict[str, Any],
    baseline: dict[str, Any],
    observed_problem: str,
    hypothesis: str,
    planned_change: str,
    expected_metric_impact: str,
    start_revision: str,
    started_at: str | None = None,
) -> dict[str, Any]:
    """Build the immutable pre-change portion of a staged experiment record."""

    baseline_metrics = baseline["metrics"]
    plan = {
        "observed_problem": observed_problem.strip(),
        "hypothesis": hypothesis.strip(),
        "planned_change": planned_change.strip(),
        "expected_metric_impact": expected_metric_impact.strip(),
    }
    empty = [name for name, value in plan.items() if not value]
    if empty:
        raise ValueError(f"experiment plan fields must not be empty: {', '.join(empty)}")
    manifest = {
        "schema_version": 2,
        "kind": "experiment",
        "lifecycle": {
            "status": "planned",
            "started_at": started_at or utc_now(),
            "start_revision": start_revision,
        },
        "scenario": scenario,
        "baseline": {
            "experiment": baseline["experiment"],
            "metrics_sha256": baseline["metrics_sha256"],
            "scenario_id": baseline_metrics["scenario_id"],
            "evaluator_version": baseline_metrics["evaluator_version"],
            "primary": baseline_metrics["primary"],
            "constraints": baseline_metrics.get("constraints"),
        },
        "plan": plan,
    }
    manifest["lifecycle"]["plan_sha256"] = sha256_json(plan)
    return manifest


def recommended_decision(
    metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
) -> str:
    current_value = metrics["primary"]["value"]
    baseline_value = baseline_metrics["primary"]["value"]
    if current_value <= baseline_value:
        return "reject_no_primary_improvement"
    if metrics["constraints"]["all_goal_constraints_verified"]:
        return "keep"
    return "provisional_requires_review"


def resolve_final_decision(
    *,
    requested: str,
    metrics: dict[str, Any] | None,
    baseline_metrics: dict[str, Any] | None,
    human_reviewed: bool,
) -> str:
    if requested == "revert":
        return "reverted"
    if requested != "keep":
        raise ValueError("final decision must be 'keep' or 'revert'")
    if metrics is None or baseline_metrics is None:
        raise ValueError("a candidate without metrics cannot be kept")
    current_value = metrics["primary"]["value"]
    baseline_value = baseline_metrics["primary"]["value"]
    if current_value <= baseline_value:
        raise ValueError("primary metric did not improve; keep is not allowed")
    if metrics["constraints"]["all_goal_constraints_verified"]:
        return "kept"
    if not human_reviewed:
        raise ValueError("provisional improvement requires explicit human review")
    return "kept_after_human_review"


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
            "The structured human observation fixture is reproducible but does not replace automated vision/ASR evaluation. Pending required constraints block automatic acceptance.",
            "",
        ]
    )
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_staged_readme(
    output_dir: Path,
    manifest: dict[str, Any],
    *,
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    lifecycle = manifest["lifecycle"]
    baseline = manifest["baseline"]
    plan = manifest["plan"]
    current_value = metrics["primary"]["value"] if metrics else None
    baseline_value = baseline["primary"]["value"]
    delta = current_value - baseline_value if current_value is not None else None
    evaluation = manifest.get("evaluation", {})
    final = manifest.get("final")
    if final:
        decision = final["decision"]
    elif error:
        decision = "evaluation_failed"
    else:
        decision = evaluation.get("recommended_decision", "not_evaluated")

    lines = [
        f"# {output_dir.name}",
        "",
        f"- Started: `{lifecycle['started_at']}`",
        f"- Status: `{lifecycle['status']}`",
        f"- Start revision: `{lifecycle['start_revision']}`",
        f"- Scenario: `{manifest['scenario']['path']}`",
        "",
        "## Frozen baseline",
        "",
        f"- Experiment: `{baseline['experiment']}`",
        f"- Evaluator: `{baseline['evaluator_version']}`",
        f"- Primary `{baseline['primary']['name']}`: `{baseline_value:.6f}`",
        f"- Metrics SHA-256: `{baseline['metrics_sha256']}`",
        "",
        "## Observed problem",
        "",
        plan["observed_problem"],
        "",
        "## Engineering hypothesis",
        "",
        plan["hypothesis"],
        "",
        "## Planned change",
        "",
        plan["planned_change"],
        "",
        "## Expected metric impact",
        "",
        plan["expected_metric_impact"],
        "",
        "## Results",
        "",
    ]
    if metrics:
        lines.extend(
            [
                f"- Candidate `{metrics['primary']['name']}`: `{current_value:.6f}`",
                f"- Delta: `{delta:+.6f}`",
                f"- Enforced constraints pass: `{metrics['constraints']['all_enforced_pass']}`",
                f"- All goal constraints verified: `{metrics['constraints']['all_goal_constraints_verified']}`",
                f"- Observation mode: `{metrics['observation_mode']}`",
                f"- Candidate revision: `{evaluation.get('candidate_revision', 'unknown')}`",
            ]
        )
    elif error:
        lines.append(f"- Evaluator error: `{error}`")
    else:
        lines.append("Candidate evaluation has not run yet.")
    lines.extend(["", "## Decision", "", f"`{decision}`", ""])
    if final:
        lines.extend(
            [
                "## Learning",
                "",
                final["learning"],
                "",
                f"- Finished: `{final['finished_at']}`",
                f"- Human reviewed: `{final['human_reviewed']}`",
            ]
        )
        if final.get("reviewer"):
            lines.append(f"- Reviewer: `{final['reviewer']}`")
        lines.append("")
    else:
        lines.extend(
            [
                "## Learning",
                "",
                "Pending evaluation and final disposition.",
                "",
            ]
        )
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def _managed_file(raw: str, *, root: Path, label: str) -> Path:
    candidate = Path(raw)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_file() or root.resolve() not in path.parents:
        raise ValueError(f"{label} must be a file inside {root}: {path}")
    return path


def _experiment_directory(raw: str) -> Path:
    candidate = Path(raw)
    path = (
        candidate if candidate.is_absolute() else LOOP_ROOT / candidate
    ).resolve()
    if not path.is_dir() or EXPERIMENTS_ROOT.resolve() not in path.parents:
        raise ValueError(
            f"experiment must be a directory inside {EXPERIMENTS_ROOT}: {path}"
        )
    return path


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _candidate_inputs(
    args: argparse.Namespace,
    *,
    scenario_payload: dict[str, Any],
    output_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    video_raw = args.video or scenario_payload["artifact"]["video"]
    video = _managed_file(video_raw, root=REPO_ROOT, label="video")
    observations = (
        _managed_file(args.observations, root=LOOP_ROOT, label="observations")
        if args.observations
        else None
    )
    record = (
        _managed_file(args.pipeline_record, root=REPO_ROOT, label="pipeline record")
        if args.pipeline_record
        else None
    )
    subtitle = (
        _managed_file(args.subtitle, root=REPO_ROOT, label="subtitle")
        if args.subtitle
        else None
    )
    video_snapshot = output_dir / "artifacts" / f"video{video.suffix.lower()}"
    video_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, video_snapshot)
    candidate: dict[str, Any] = {
        "video": {
            "source_path": _relative(video, REPO_ROOT, "video"),
            "source_sha256": sha256_file(video),
            "snapshot_path": _relative(video_snapshot, REPO_ROOT, "video snapshot"),
        },
        "observations": None,
        "pipeline_record": None,
        "subtitle": None,
    }
    command_args = ["--video", str(video_snapshot)]
    if observations is not None:
        candidate["observations"] = {
            "path": _relative(observations, LOOP_ROOT, "observations"),
            "sha256": sha256_file(observations),
        }
        command_args.extend(["--observations", str(observations)])
    if record is not None:
        candidate["pipeline_record"] = {
            "source_path": _relative(record, REPO_ROOT, "pipeline record"),
            "source_sha256": sha256_file(record),
            "content": _sanitize_record(
                json.loads(record.read_text(encoding="utf-8"))
            ),
        }
        command_args.extend(["--pipeline-record", str(record)])
    if subtitle is not None:
        candidate["subtitle"] = {
            "source_path": _relative(subtitle, REPO_ROOT, "subtitle"),
            "source_sha256": sha256_file(subtitle),
            "content": subtitle.read_text(encoding="utf-8"),
        }
        command_args.extend(["--subtitle", str(subtitle)])
    return candidate, command_args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run baseline or staged video-quality experiments"
    )
    parser.add_argument("--kind", choices=("baseline", "experiment"), required=True)
    parser.add_argument("--phase", choices=("start", "evaluate", "finish"))
    parser.add_argument("--slug")
    parser.add_argument("--hypothesis")
    parser.add_argument("--scenario")
    parser.add_argument("--baseline")
    parser.add_argument("--experiment")
    parser.add_argument("--problem")
    parser.add_argument("--planned-change")
    parser.add_argument("--expected-impact")
    parser.add_argument("--video")
    parser.add_argument("--observations")
    parser.add_argument("--pipeline-record")
    parser.add_argument("--subtitle")
    parser.add_argument("--decision", choices=("keep", "revert"))
    parser.add_argument("--learning")
    parser.add_argument("--human-review", choices=("YES", "NO"), default="NO")
    parser.add_argument("--reviewer")
    return parser.parse_args()


def _require(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def _write_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    (output_dir / "inputs.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_baseline(args: argparse.Namespace) -> int:
    slug = _require(args.slug, "slug")
    scenario = _managed_file(
        _require(args.scenario, "scenario"),
        root=LOOP_ROOT,
        label="scenario",
    )
    scenario_payload = json.loads(scenario.read_text(encoding="utf-8"))
    hypothesis = (
        args.hypothesis
        or "Current controlled artifact establishes the active evaluator behavior."
    )
    _managed_file(
        args.video or scenario_payload["artifact"]["video"],
        root=REPO_ROOT,
        label="video",
    )
    if args.observations:
        _managed_file(args.observations, root=LOOP_ROOT, label="observations")
    if args.pipeline_record:
        _managed_file(args.pipeline_record, root=REPO_ROOT, label="pipeline record")
    if args.subtitle:
        _managed_file(args.subtitle, root=REPO_ROOT, label="subtitle")
    experiment_id = next_experiment_id()
    output_dir = EXPERIMENTS_ROOT / f"{experiment_id:03d}-{safe_slug(slug)}"
    output_dir.mkdir(parents=False, exist_ok=False)
    candidate, candidate_args = _candidate_inputs(
        args,
        scenario_payload=scenario_payload,
        output_dir=output_dir,
    )
    input_manifest: dict[str, Any] = {
        "schema_version": 1,
        "scenario": {
            "path": _relative(scenario, LOOP_ROOT, "scenario"),
            "sha256": sha256_file(scenario),
        },
        **candidate,
    }
    _write_manifest(output_dir, input_manifest)
    command = [
        sys.executable,
        str(LOOP_ROOT / "evals" / "evaluate.py"),
        "--scenario",
        str(scenario),
        "--output",
        str(output_dir),
        *candidate_args,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        previous = latest_comparable_metrics(
            output_dir,
            scenario_id=scenario_payload["id"],
            evaluator_version=None,
        )
        error = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        (output_dir / "evaluator.stderr.log").write_text(error + "\n", encoding="utf-8")
        write_readme(
            output_dir,
            kind="baseline",
            hypothesis=hypothesis,
            scenario=scenario,
            metrics=None,
            previous=previous,
            error=error,
        )
        print(f"experiment failed: {output_dir}", file=sys.stderr)
        return result.returncode

    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    previous = latest_comparable_metrics(
        output_dir,
        scenario_id=metrics["scenario_id"],
        evaluator_version=metrics["evaluator_version"],
    )
    write_readme(
        output_dir,
        kind="baseline",
        hypothesis=hypothesis,
        scenario=scenario,
        metrics=metrics,
        previous=previous,
    )
    (output_dir / "summary.md").unlink(missing_ok=True)
    print(result.stdout.strip())
    print(f"experiment={output_dir.relative_to(LOOP_ROOT)}")
    return 0


def start_experiment(args: argparse.Namespace) -> int:
    slug = _require(args.slug, "slug")
    scenario = _managed_file(
        _require(args.scenario, "scenario"),
        root=LOOP_ROOT,
        label="scenario",
    )
    baseline_dir = _experiment_directory(_require(args.baseline, "baseline"))
    baseline_metrics_path = baseline_dir / "metrics.json"
    if not baseline_metrics_path.is_file():
        raise ValueError(f"baseline has no metrics.json: {baseline_dir}")
    dirty = _git_output("status", "--porcelain")
    if dirty:
        raise ValueError(
            "experiment-start requires a clean worktree so the hypothesis is "
            "recorded before candidate code changes"
        )
    replay = subprocess.run(
        [
            sys.executable,
            str(LOOP_ROOT / "scripts" / "replay_experiment.py"),
            "--experiment",
            str(baseline_dir),
            "--verify-only",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if replay.returncode != 0:
        raise RuntimeError(
            "baseline reproduction failed: "
            + (replay.stderr.strip() or replay.stdout.strip())
        )
    baseline_metrics = json.loads(
        baseline_metrics_path.read_text(encoding="utf-8")
    )
    scenario_payload = json.loads(scenario.read_text(encoding="utf-8"))
    if baseline_metrics["scenario_id"] != scenario_payload["id"]:
        raise ValueError("baseline and planned experiment use different scenarios")
    baseline = {
        "experiment": _relative(baseline_dir, LOOP_ROOT, "baseline"),
        "metrics_sha256": sha256_file(baseline_metrics_path),
        "metrics": baseline_metrics,
    }
    manifest = build_started_manifest(
        scenario={
            "path": _relative(scenario, LOOP_ROOT, "scenario"),
            "sha256": sha256_file(scenario),
        },
        baseline=baseline,
        observed_problem=_require(args.problem, "problem"),
        hypothesis=_require(args.hypothesis, "hypothesis"),
        planned_change=_require(args.planned_change, "planned change"),
        expected_metric_impact=_require(args.expected_impact, "expected impact"),
        start_revision=_git_output("rev-parse", "HEAD"),
    )
    experiment_id = next_experiment_id()
    output_dir = EXPERIMENTS_ROOT / f"{experiment_id:03d}-{safe_slug(slug)}"
    output_dir.mkdir(parents=False, exist_ok=False)
    _write_manifest(output_dir, manifest)
    write_staged_readme(output_dir, manifest)
    print(replay.stdout.strip())
    print(f"experiment={output_dir.relative_to(LOOP_ROOT)}")
    print("status=planned")
    return 0


def evaluate_experiment(args: argparse.Namespace) -> int:
    output_dir = _experiment_directory(_require(args.experiment, "experiment"))
    manifest_path = output_dir / "inputs.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2:
        raise ValueError("experiment-evaluate requires a staged schema v2 record")
    if manifest["lifecycle"]["status"] != "planned":
        raise ValueError("only a planned experiment may be evaluated")
    if sha256_json(manifest["plan"]) != manifest["lifecycle"]["plan_sha256"]:
        raise ValueError("frozen experiment plan changed after experiment-start")
    scenario = _managed_file(
        manifest["scenario"]["path"],
        root=LOOP_ROOT,
        label="scenario",
    )
    if sha256_file(scenario) != manifest["scenario"]["sha256"]:
        raise ValueError("scenario changed after experiment-start")
    scenario_payload = json.loads(scenario.read_text(encoding="utf-8"))
    baseline_dir = _experiment_directory(manifest["baseline"]["experiment"])
    baseline_metrics_path = baseline_dir / "metrics.json"
    if sha256_file(baseline_metrics_path) != manifest["baseline"]["metrics_sha256"]:
        raise ValueError("frozen baseline metrics changed after experiment-start")
    baseline_metrics = json.loads(
        baseline_metrics_path.read_text(encoding="utf-8")
    )
    candidate, candidate_args = _candidate_inputs(
        args,
        scenario_payload=scenario_payload,
        output_dir=output_dir,
    )
    diff = _git_output("diff", "--binary", "HEAD", "--")
    evaluation = {
        "evaluated_at": utc_now(),
        "candidate_revision": _git_output("rev-parse", "HEAD"),
        "worktree_diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }
    command = [
        sys.executable,
        str(LOOP_ROOT / "evals" / "evaluate.py"),
        "--scenario",
        str(scenario),
        "--output",
        str(output_dir),
        *candidate_args,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    manifest["candidate"] = candidate
    manifest["lifecycle"]["status"] = "evaluated"
    if result.returncode != 0:
        error = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit code {result.returncode}"
        )
        evaluation["recommended_decision"] = "evaluation_failed"
        manifest["evaluation"] = evaluation
        _write_manifest(output_dir, manifest)
        (output_dir / "evaluator.stderr.log").write_text(
            error + "\n", encoding="utf-8"
        )
        write_staged_readme(output_dir, manifest, error=error)
        return result.returncode
    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    if (
        metrics["scenario_id"] != baseline_metrics["scenario_id"]
        or metrics["evaluator_version"] != baseline_metrics["evaluator_version"]
    ):
        raise ValueError("candidate metrics are not comparable with the frozen baseline")
    evaluation["recommended_decision"] = recommended_decision(
        metrics,
        baseline_metrics,
    )
    manifest["evaluation"] = evaluation
    _write_manifest(output_dir, manifest)
    (output_dir / "evaluator.stderr.log").unlink(missing_ok=True)
    (output_dir / "summary.md").unlink(missing_ok=True)
    write_staged_readme(output_dir, manifest, metrics=metrics)
    print(result.stdout.strip())
    print("status=evaluated")
    print(f"recommended_decision={evaluation['recommended_decision']}")
    return 0


def finish_experiment(args: argparse.Namespace) -> int:
    output_dir = _experiment_directory(_require(args.experiment, "experiment"))
    manifest = json.loads((output_dir / "inputs.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2:
        raise ValueError("experiment-finish requires a staged schema v2 record")
    if manifest["lifecycle"]["status"] != "evaluated":
        raise ValueError("only an evaluated experiment may be finished")
    baseline_dir = _experiment_directory(manifest["baseline"]["experiment"])
    baseline_metrics_path = baseline_dir / "metrics.json"
    if sha256_file(baseline_metrics_path) != manifest["baseline"]["metrics_sha256"]:
        raise ValueError("frozen baseline metrics changed after experiment-start")
    baseline_metrics = json.loads(
        baseline_metrics_path.read_text(encoding="utf-8")
    )
    metrics_path = output_dir / "metrics.json"
    metrics = (
        json.loads(metrics_path.read_text(encoding="utf-8"))
        if metrics_path.is_file()
        else None
    )
    human_reviewed = args.human_review == "YES"
    if human_reviewed and not args.reviewer:
        raise ValueError("reviewer is required when HUMAN_REVIEW=YES")
    decision = resolve_final_decision(
        requested=_require(args.decision, "decision"),
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        human_reviewed=human_reviewed,
    )
    learning = _require(args.learning, "learning")
    manifest["lifecycle"]["status"] = (
        "kept" if decision.startswith("kept") else "reverted"
    )
    manifest["final"] = {
        "decision": decision,
        "finished_at": utc_now(),
        "learning": learning,
        "human_reviewed": human_reviewed,
        "reviewer": args.reviewer if human_reviewed else None,
    }
    _write_manifest(output_dir, manifest)
    error_path = output_dir / "evaluator.stderr.log"
    error = error_path.read_text(encoding="utf-8").strip() if error_path.is_file() else None
    write_staged_readme(output_dir, manifest, metrics=metrics, error=error)
    print(f"status={manifest['lifecycle']['status']}")
    print(f"decision={decision}")
    if decision.startswith("kept"):
        print("next=run repository validation, commit, and push")
    else:
        print("next=revert candidate code and commit the experiment learning")
    return 0


def main() -> int:
    args = parse_args()
    EXPERIMENTS_ROOT.mkdir(parents=True, exist_ok=True)
    if args.kind == "baseline":
        if args.phase is not None:
            raise ValueError("baseline does not use staged phases")
        return run_baseline(args)
    if args.phase == "start":
        return start_experiment(args)
    if args.phase == "evaluate":
        return evaluate_experiment(args)
    if args.phase == "finish":
        return finish_experiment(args)
    raise ValueError(
        "product experiments require --phase start, evaluate, or finish"
    )


if __name__ == "__main__":
    raise SystemExit(main())
