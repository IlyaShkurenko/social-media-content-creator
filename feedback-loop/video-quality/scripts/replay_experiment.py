from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_path(root: Path, entry: dict, *, key: str = "path") -> Path:
    path = (root / entry[key]).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"missing managed replay input: {path}")
    expected = entry.get("sha256") or entry.get("source_sha256")
    if expected and sha256_file(path) != expected:
        raise ValueError(f"replay input hash changed: {path}")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a compact video-quality experiment"
    )
    parser.add_argument("--experiment", required=True)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="re-evaluate under ignored state and compare without mutating the record",
    )
    return parser.parse_args()


def comparable_metrics(metrics: dict) -> dict:
    comparable = dict(metrics)
    comparable.pop("generated_at", None)
    return comparable


def main() -> int:
    args = parse_args()
    experiment_raw = Path(args.experiment)
    experiment = (
        experiment_raw
        if experiment_raw.is_absolute()
        else LOOP_ROOT / experiment_raw
    ).resolve()
    if LOOP_ROOT.resolve() not in experiment.parents or not experiment.is_dir():
        raise ValueError(f"experiment must stay inside {LOOP_ROOT}: {experiment}")
    manifest = json.loads((experiment / "inputs.json").read_text(encoding="utf-8"))
    scenario = verified_path(LOOP_ROOT, manifest["scenario"])
    candidate = manifest.get("candidate", manifest)
    if not candidate.get("video"):
        raise ValueError("planned experiment has no candidate artifact to replay")
    video_entry = candidate["video"]
    snapshot_value = Path(video_entry["snapshot_path"])
    video = (
        experiment / snapshot_value
        if snapshot_value.parts[:1] == ("artifacts",)
        else REPO_ROOT / snapshot_value
    ).resolve()
    if REPO_ROOT.resolve() not in video.parents or not video.is_file():
        raise ValueError(f"missing ignored video snapshot: {video}")
    expected_video_hash = video_entry.get("snapshot_sha256") or video_entry[
        "source_sha256"
    ]
    if sha256_file(video) != expected_video_hash:
        raise ValueError(f"video snapshot hash changed: {video}")

    output_dir = (
        LOOP_ROOT / ".state" / "replay-check" / experiment.name
        if args.verify_only
        else experiment
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(LOOP_ROOT / "evals" / "evaluate.py"),
        "--scenario",
        str(scenario),
        "--output",
        str(output_dir),
        "--video",
        str(video),
    ]
    observations_entry = candidate.get("observations")
    if observations_entry:
        observations = verified_path(LOOP_ROOT, observations_entry)
        command.extend(["--observations", str(observations)])

    replay_dir = LOOP_ROOT / ".state" / "replay" / experiment.name
    replay_dir.mkdir(parents=True, exist_ok=True)
    record_entry = candidate.get("pipeline_record")
    if record_entry:
        record = replay_dir / "pipeline-record.json"
        record.write_text(
            json.dumps(record_entry["content"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        command.extend(["--pipeline-record", str(record)])
    subtitle_entry = candidate.get("subtitle")
    if subtitle_entry:
        subtitle = replay_dir / "subtitle.srt"
        subtitle.write_text(subtitle_entry["content"], encoding="utf-8")
        command.extend(["--subtitle", str(subtitle)])
    judge_entry = candidate.get("judge_evidence")
    if judge_entry:
        judge_evidence = replay_dir / "judge-evidence.json"
        judge_evidence.write_text(
            json.dumps(
                judge_entry["content"],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if sha256_file(judge_evidence) != judge_entry["source_sha256"]:
            raise ValueError("stored judge evidence content hash changed")
        command.extend(["--judge-evidence", str(judge_evidence)])
    temporal_entry = candidate.get("temporal_evidence")
    if temporal_entry:
        temporal_evidence = replay_dir / "temporal-evidence.json"
        temporal_evidence.write_text(
            json.dumps(
                temporal_entry["content"],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if sha256_file(temporal_evidence) != temporal_entry["source_sha256"]:
            raise ValueError("stored temporal evidence content hash changed")
        command.extend(["--temporal-evidence", str(temporal_evidence)])

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode
    (output_dir / "summary.md").unlink(missing_ok=True)
    if args.verify_only:
        stored_metrics_path = experiment / "metrics.json"
        replayed_metrics_path = output_dir / "metrics.json"
        if not stored_metrics_path.is_file():
            raise ValueError(f"experiment has no stored metrics: {experiment}")
        stored = json.loads(stored_metrics_path.read_text(encoding="utf-8"))
        replayed = json.loads(replayed_metrics_path.read_text(encoding="utf-8"))
        if comparable_metrics(stored) != comparable_metrics(replayed):
            raise ValueError("replayed metrics differ from the stored experiment")
        print(f"verified={experiment.relative_to(LOOP_ROOT)}")
        print(f"primary={stored['primary']['value']}")
        return 0
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
