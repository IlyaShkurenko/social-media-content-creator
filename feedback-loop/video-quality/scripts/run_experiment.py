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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Allocate and run a sequential video-quality experiment")
    parser.add_argument("--kind", choices=("baseline", "experiment"), required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument(
        "--hypothesis",
        default="Current controlled artifact establishes the active evaluator behavior.",
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--video")
    parser.add_argument("--observations")
    parser.add_argument("--pipeline-record")
    parser.add_argument("--subtitle")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    EXPERIMENTS_ROOT.mkdir(parents=True, exist_ok=True)
    scenario_raw = Path(args.scenario)
    scenario = scenario_raw if scenario_raw.is_absolute() else LOOP_ROOT / scenario_raw
    scenario = scenario.resolve()
    if not scenario.is_file() or LOOP_ROOT.resolve() not in scenario.parents:
        raise ValueError(f"scenario must be a file inside {LOOP_ROOT}: {scenario}")

    scenario_payload = json.loads(scenario.read_text(encoding="utf-8"))

    video_raw = Path(args.video or scenario_payload["artifact"]["video"])
    video = (video_raw if video_raw.is_absolute() else REPO_ROOT / video_raw).resolve()
    if not video.is_file() or REPO_ROOT.resolve() not in video.parents:
        raise ValueError(f"video must be a file inside {REPO_ROOT}: {video}")

    observations = None
    if args.observations:
        observations_raw = Path(args.observations)
        observations = (
            observations_raw
            if observations_raw.is_absolute()
            else LOOP_ROOT / observations_raw
        ).resolve()
        if (
            not observations.is_file()
            or LOOP_ROOT.resolve() not in observations.parents
        ):
            raise ValueError(
                f"observations must be a file inside {LOOP_ROOT}: {observations}"
            )

    record = None
    if args.pipeline_record:
        record_raw = Path(args.pipeline_record)
        record = (
            record_raw if record_raw.is_absolute() else REPO_ROOT / record_raw
        ).resolve()
        if not record.is_file() or REPO_ROOT.resolve() not in record.parents:
            raise ValueError(
                f"pipeline record must be a file inside {REPO_ROOT}: {record}"
            )

    subtitle = None
    if args.subtitle:
        subtitle_raw = Path(args.subtitle)
        subtitle = (
            subtitle_raw if subtitle_raw.is_absolute() else REPO_ROOT / subtitle_raw
        ).resolve()
        if not subtitle.is_file() or REPO_ROOT.resolve() not in subtitle.parents:
            raise ValueError(
                f"subtitle must be a file inside {REPO_ROOT}: {subtitle}"
            )

    experiment_id = next_experiment_id()
    output_dir = EXPERIMENTS_ROOT / f"{experiment_id:03d}-{safe_slug(args.slug)}"
    output_dir.mkdir(parents=False, exist_ok=False)
    video_snapshot = output_dir / "artifacts" / f"video{video.suffix.lower()}"
    video_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, video_snapshot)

    input_manifest: dict[str, Any] = {
        "schema_version": 1,
        "scenario": {
            "path": _relative(scenario, LOOP_ROOT, "scenario"),
            "sha256": sha256_file(scenario),
        },
        "video": {
            "source_path": _relative(video, REPO_ROOT, "video"),
            "source_sha256": sha256_file(video),
            "snapshot_path": _relative(video_snapshot, REPO_ROOT, "video snapshot"),
        },
        "observations": None,
        "pipeline_record": None,
        "subtitle": None,
    }
    if observations is not None:
        input_manifest["observations"] = {
            "path": _relative(observations, LOOP_ROOT, "observations"),
            "sha256": sha256_file(observations),
        }
    if record is not None:
        input_manifest["pipeline_record"] = {
            "source_path": _relative(record, REPO_ROOT, "pipeline record"),
            "source_sha256": sha256_file(record),
            "content": _sanitize_record(
                json.loads(record.read_text(encoding="utf-8"))
            ),
        }
    if subtitle is not None:
        input_manifest["subtitle"] = {
            "source_path": _relative(subtitle, REPO_ROOT, "subtitle"),
            "source_sha256": sha256_file(subtitle),
            "content": subtitle.read_text(encoding="utf-8"),
        }
    (output_dir / "inputs.json").write_text(
        json.dumps(input_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    command = [
        sys.executable,
        str(LOOP_ROOT / "evals" / "evaluate.py"),
        "--scenario",
        str(scenario),
        "--output",
        str(output_dir),
    ]
    command.extend(["--video", str(video_snapshot)])
    if observations is not None:
        command.extend(["--observations", str(observations)])
    if record is not None:
        command.extend(["--pipeline-record", str(record)])
    if subtitle is not None:
        command.extend(["--subtitle", str(subtitle)])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
            kind=args.kind,
            hypothesis=args.hypothesis,
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
        kind=args.kind,
        hypothesis=args.hypothesis,
        scenario=scenario,
        metrics=metrics,
        previous=previous,
    )
    (output_dir / "summary.md").unlink(missing_ok=True)
    print(result.stdout.strip())
    print(f"experiment={output_dir.relative_to(LOOP_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
