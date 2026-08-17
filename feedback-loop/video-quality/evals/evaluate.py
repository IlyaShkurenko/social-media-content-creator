from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any


EVALUATOR_VERSION = "0.3.0"
LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"{label} must stay inside {root_resolved}: {resolved}")
    return resolved


def resolve_repo_path(value: str, label: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else REPO_ROOT / raw
    return ensure_within(candidate, REPO_ROOT, label)


def resolve_output_path(value: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else LOOP_ROOT / raw
    return ensure_within(candidate, LOOP_ROOT, "output")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def probe_video(video_path: Path) -> dict[str, Any]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(video_path),
        ]
    )
    return json.loads(result.stdout)


def full_decode_succeeds(video_path: Path) -> tuple[bool, str]:
    result = run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(video_path), "-f", "null", "-"],
        check=False,
    )
    return result.returncode == 0, result.stderr.strip()


def detect_black_segments(video_path: Path) -> list[dict[str, float]]:
    result = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(video_path),
            "-vf",
            "blackdetect=d=0.50:pic_th=0.98",
            "-an",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    pattern = re.compile(
        r"black_start:(?P<start>[0-9.]+) black_end:(?P<end>[0-9.]+) black_duration:(?P<duration>[0-9.]+)"
    )
    return [
        {name: float(value) for name, value in match.groupdict().items()}
        for match in pattern.finditer(result.stderr)
    ]


def parse_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None


def extract_frames(video_path: Path, storyboard: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence: list[dict[str, Any]] = []
    for index, scene in enumerate(storyboard, start=1):
        timestamp = (float(scene["start_seconds"]) + float(scene["end_seconds"])) / 2
        frame_path = output_dir / f"{index:02d}-{scene['id']}.jpg"
        result = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(frame_path),
            ],
            check=False,
        )
        evidence.append(
            {
                "scene_id": scene["id"],
                "timestamp_seconds": round(timestamp, 3),
                "path": str(frame_path.relative_to(output_dir.parent.parent)),
                "extracted": result.returncode == 0 and frame_path.exists(),
            }
        )
    return evidence


def normalized_tags(values: list[str]) -> set[str]:
    return {re.sub(r"\s+", " ", value.strip().lower()) for value in values if value.strip()}


def calculate_tag_metrics(
    storyboard: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> dict[str, float | int]:
    observed_by_scene = {item["scene_id"]: normalized_tags(item.get("observed_tags", [])) for item in observations}
    expected_pairs: set[tuple[str, str]] = set()
    observed_pairs: set[tuple[str, str]] = set()
    for scene in storyboard:
        scene_id = scene["id"]
        expected_pairs.update((scene_id, tag) for tag in normalized_tags(scene.get("expected_tags", [])))
        observed_pairs.update((scene_id, tag) for tag in observed_by_scene.get(scene_id, set()))

    true_positive = len(expected_pairs & observed_pairs)
    false_positive = len(observed_pairs - expected_pairs)
    false_negative = len(expected_pairs - observed_pairs)
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def tokenize(value: str) -> list[str]:
    return re.findall(r"[\w]+(?:[-’'][\w]+)*", value.lower(), flags=re.UNICODE)


def token_f1(reference: str, candidate: str) -> dict[str, float | int]:
    reference_tokens = tokenize(reference)
    candidate_tokens = tokenize(candidate)
    reference_counts: dict[str, int] = {}
    candidate_counts: dict[str, int] = {}
    for token in reference_tokens:
        reference_counts[token] = reference_counts.get(token, 0) + 1
    for token in candidate_tokens:
        candidate_counts[token] = candidate_counts.get(token, 0) + 1
    overlap = sum(min(count, candidate_counts.get(token, 0)) for token, count in reference_counts.items())
    precision = overlap / len(candidate_tokens) if candidate_tokens else 0.0
    recall = overlap / len(reference_tokens) if reference_tokens else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "reference_tokens": len(reference_tokens),
        "candidate_tokens": len(candidate_tokens),
        "matching_tokens": overlap,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def parse_srt_text(path: Path) -> str:
    content = path.read_text(encoding="utf-8-sig")
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines)


def pipeline_record_metrics(
    record: dict[str, Any] | None,
) -> tuple[dict[str, float | None], dict[str, str]]:
    """Extract cost/latency evidence without inventing unavailable values."""

    record = record or {}
    result: dict[str, float | None] = {
        "generation_latency_seconds": None,
        "estimated_cost_usd": None,
    }
    reasons: dict[str, str] = {}
    cost = record.get("actual_paid_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
        result["estimated_cost_usd"] = float(cost)
    else:
        reasons["estimated_cost_usd"] = (
            "the source task did not record provider cost"
        )

    latency = record.get("generation_latency_seconds")
    if (
        isinstance(latency, (int, float))
        and not isinstance(latency, bool)
        and latency >= 0
    ):
        result["generation_latency_seconds"] = float(latency)
    else:
        reasons["generation_latency_seconds"] = (
            "the source task did not record paid-provider generation latency"
        )
    return result, reasons


def pipeline_constraint_evidence(
    record: dict[str, Any] | None,
) -> tuple[dict[str, bool], dict[str, str]]:
    record = record or {}
    safe_area = record.get("subtitle_safe_area_pass")
    if isinstance(safe_area, bool):
        return {"subtitle_safe_area_pass": safe_area}, {}
    return {}, {
        "subtitle_safe_area_pass": (
            "the source task did not record deterministic subtitle geometry"
        )
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duration_from_probe(probe: dict[str, Any]) -> float:
    format_duration = probe.get("format", {}).get("duration")
    if format_duration not in {None, "N/A"}:
        return float(format_duration)
    durations = [
        float(stream["duration"])
        for stream in probe.get("streams", [])
        if stream.get("duration") not in {None, "N/A"}
    ]
    if not durations:
        raise ValueError("ffprobe did not report a duration")
    return max(durations)


def evaluate(
    scenario_path: Path,
    output_dir: Path,
    video_override: str | None = None,
    observations_override: str | None = None,
    pipeline_record_override: str | None = None,
    subtitle_override: str | None = None,
) -> dict[str, Any]:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    video_value = video_override or scenario["artifact"]["video"]
    video_path = resolve_repo_path(video_value, "video")
    if not video_path.is_file():
        raise FileNotFoundError(f"video artifact does not exist: {video_path}")
    artifact_sha256 = sha256_file(video_path)

    if observations_override:
        raw_observations_path = Path(observations_override)
        observations_path = (
            raw_observations_path
            if raw_observations_path.is_absolute()
            else LOOP_ROOT / raw_observations_path
        )
        observations_path = ensure_within(
            observations_path,
            LOOP_ROOT,
            "observations",
        )
        observations_contract = json.loads(
            observations_path.read_text(encoding="utf-8")
        )
    else:
        observations_path = scenario_path
        observations_contract = scenario["observations"]
    if observations_contract.get("scenario_id", scenario["id"]) != scenario["id"]:
        raise ValueError("observations do not belong to the evaluated scenario")
    if observations_contract.get("mode") == "human_fixture":
        reviewed_hash = observations_contract.get("artifact_sha256")
        if reviewed_hash != artifact_sha256:
            raise ValueError(
                "human_fixture observations do not match the evaluated artifact SHA-256; "
                "review this artifact and provide a matching observations file"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = output_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    probe = probe_video(video_path)
    streams = probe.get("streams", [])
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise ValueError("artifact has no video stream")

    duration = duration_from_probe(probe)
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    expected = scenario["expected"]
    duration_contract = expected["duration_seconds"]
    duration_pass = float(duration_contract["min"]) <= duration <= float(duration_contract["max"])
    expected_aspect = expected["aspect_ratio"]
    expected_width, expected_height = (int(value) for value in expected_aspect.split(":"))
    actual_ratio = width / height if height else 0.0
    expected_ratio = expected_width / expected_height
    aspect_ratio_pass = abs(actual_ratio - expected_ratio) <= 0.02

    decode_success, decode_error = full_decode_succeeds(video_path)
    black_segments = detect_black_segments(video_path)
    storyboard = expected["storyboard"]
    observations = observations_contract["scenes"]
    alignment = calculate_tag_metrics(storyboard, observations)
    frame_evidence = extract_frames(video_path, storyboard, artifacts_dir / "frames")

    subtitle_metric: dict[str, float | int] | None = None
    pipeline_record: dict[str, Any] | None = None
    subtitle_path_value = subtitle_override or scenario.get("artifact", {}).get(
        "subtitle"
    )
    record_path_value = pipeline_record_override or scenario.get("artifact", {}).get(
        "pipeline_record"
    )
    if record_path_value:
        record_path = resolve_repo_path(record_path_value, "pipeline record")
        if record_path.is_file():
            pipeline_record = json.loads(record_path.read_text(encoding="utf-8"))
    if subtitle_path_value and pipeline_record is not None:
        subtitle_path = resolve_repo_path(subtitle_path_value, "subtitle")
        if subtitle_path.is_file():
            subtitle_metric = token_f1(
                pipeline_record.get("script", ""),
                parse_srt_text(subtitle_path),
            )
    record_metrics, record_metric_reasons = pipeline_record_metrics(pipeline_record)
    evidence_constraints, evidence_pending = pipeline_constraint_evidence(
        pipeline_record
    )

    enforced_constraints = {
        "render_success": decode_success,
        "audio_present": audio_stream is not None if expected.get("audio_required", False) else True,
        "aspect_ratio_pass": aspect_ratio_pass,
        "duration_pass": duration_pass,
        "no_sustained_black_segments": len(black_segments) == 0,
        "all_evidence_frames_extracted": all(item["extracted"] for item in frame_evidence),
        **evidence_constraints,
    }
    brand_assets_required = bool(expected.get("brand_assets_required", False))
    pending_constraints = {
        "voiceover_wer_pass": "timestamped ASR is not enabled in evaluator v0",
        **evidence_pending,
        "brand_asset_fidelity_pass": (
            "brand assets are required but the fidelity judge is not enabled"
            if brand_assets_required
            else "the scenario does not require a brand asset"
        ),
    }

    metrics = {
        "schema_version": 1,
        "evaluator_version": EVALUATOR_VERSION,
        "generated_at": utc_now(),
        "scenario_id": scenario["id"],
        "observation_mode": observations_contract["mode"],
        "artifact": {
            "video": str(video_path.relative_to(REPO_ROOT)),
            "duration_seconds": round(duration, 3),
            "width": width,
            "height": height,
            "fps": parse_rate(video_stream.get("avg_frame_rate")),
            "video_codec": video_stream.get("codec_name"),
            "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
            "sha256": artifact_sha256,
        },
        "primary": {"name": "timeline_alignment_f1", "value": alignment["f1"]},
        "metrics": {
            "timeline_alignment_precision": alignment["precision"],
            "timeline_alignment_recall": alignment["recall"],
            "timeline_alignment_f1": alignment["f1"],
            "timeline_alignment_counts": {
                "true_positive": alignment["true_positive"],
                "false_positive": alignment["false_positive"],
                "false_negative": alignment["false_negative"],
            },
            "subtitle_text_token_f1": subtitle_metric["f1"] if subtitle_metric else None,
            "visual_judge_win_rate": None,
            "brand_asset_fidelity": None,
            "voiceover_wer": None,
            "word_timing_mae_ms": None,
            "shot_boundary_mae_ms": None,
            "generation_latency_seconds": record_metrics[
                "generation_latency_seconds"
            ],
            "estimated_cost_usd": record_metrics["estimated_cost_usd"],
            "cost_per_accepted_video_usd": None,
        },
        "constraints": {
            "enforced": enforced_constraints,
            "all_enforced_pass": all(enforced_constraints.values()),
            "pending": pending_constraints,
            "all_goal_constraints_verified": False,
        },
        "evidence": {
            "frames": frame_evidence,
            "black_segments": black_segments,
            "decode_error": decode_error or None,
            "subtitle_token_comparison": subtitle_metric,
            "observations_source": str(observations_path.relative_to(LOOP_ROOT)),
        },
        "unavailable_metric_reasons": {
            "visual_judge_win_rate": "pairwise visual judge is not enabled in evaluator v0",
            "brand_asset_fidelity": (
                "brand fidelity judge is not enabled"
                if brand_assets_required
                else "scenario has no required brand asset"
            ),
            "voiceover_wer": "timestamped ASR is not enabled in evaluator v0",
            "word_timing_mae_ms": "word-level ASR alignment is not enabled in evaluator v0",
            "shot_boundary_mae_ms": "automatic scene-boundary comparison is not enabled in evaluator v0",
            "cost_per_accepted_video_usd": "cost and acceptance are unavailable",
            **record_metric_reasons,
        },
    }
    return metrics


def write_summary(metrics: dict[str, Any], output_dir: Path) -> None:
    primary = metrics["primary"]
    constraints = metrics["constraints"]
    lines = [
        "# Evaluation summary",
        "",
        f"- Scenario: `{metrics['scenario_id']}`",
        f"- Evaluator: `{metrics['evaluator_version']}`",
        f"- Observation mode: `{metrics['observation_mode']}`",
        f"- Primary metric `{primary['name']}`: **{primary['value']:.6f}**",
        f"- Enforced constraints pass: **{constraints['all_enforced_pass']}**",
        f"- All goal constraints verified: **{constraints['all_goal_constraints_verified']}**",
        "",
        "Pending checks prevent automatic acceptance; see `metrics.json` for reasons and evidence.",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a rendered short video against a fixed storyboard")
    parser.add_argument("--scenario", required=True, help="Scenario JSON path")
    parser.add_argument("--output", required=True, help="Experiment output directory inside the loop root")
    parser.add_argument("--video", help="Optional video path override inside the repository")
    parser.add_argument(
        "--observations",
        help="Artifact-specific observation JSON inside the feedback-loop root",
    )
    parser.add_argument("--pipeline-record")
    parser.add_argument("--subtitle")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenario_raw = Path(args.scenario)
    scenario_path = scenario_raw if scenario_raw.is_absolute() else LOOP_ROOT / scenario_raw
    scenario_path = ensure_within(scenario_path, LOOP_ROOT, "scenario")
    output_dir = resolve_output_path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = evaluate(
        scenario_path,
        output_dir,
        args.video,
        args.observations,
        args.pipeline_record,
        args.subtitle,
    )
    (output_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(metrics, output_dir)
    print(json.dumps({"output": str(output_dir), "primary": metrics["primary"], "constraints": metrics["constraints"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
