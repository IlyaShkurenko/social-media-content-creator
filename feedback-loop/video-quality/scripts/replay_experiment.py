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
    return parser.parse_args()


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
    video_entry = manifest["video"]
    video = (REPO_ROOT / video_entry["snapshot_path"]).resolve()
    if REPO_ROOT.resolve() not in video.parents or not video.is_file():
        raise ValueError(f"missing ignored video snapshot: {video}")
    if sha256_file(video) != video_entry["source_sha256"]:
        raise ValueError(f"video snapshot hash changed: {video}")

    command = [
        sys.executable,
        str(LOOP_ROOT / "evals" / "evaluate.py"),
        "--scenario",
        str(scenario),
        "--output",
        str(experiment),
        "--video",
        str(video),
    ]
    observations_entry = manifest.get("observations")
    if observations_entry:
        observations = verified_path(LOOP_ROOT, observations_entry)
        command.extend(["--observations", str(observations)])

    replay_dir = LOOP_ROOT / ".state" / "replay" / experiment.name
    replay_dir.mkdir(parents=True, exist_ok=True)
    record_entry = manifest.get("pipeline_record")
    if record_entry:
        record = replay_dir / "pipeline-record.json"
        record.write_text(
            json.dumps(record_entry["content"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        command.extend(["--pipeline-record", str(record)])
    subtitle_entry = manifest.get("subtitle")
    if subtitle_entry:
        subtitle = replay_dir / "subtitle.srt"
        subtitle.write_text(subtitle_entry["content"], encoding="utf-8")
        command.extend(["--subtitle", str(subtitle)])

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
    (experiment / "summary.md").unlink(missing_ok=True)
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
