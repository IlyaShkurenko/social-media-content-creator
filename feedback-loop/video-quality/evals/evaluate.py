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


EVALUATOR_VERSION = "0.6.0"
JUDGE_SCHEMA_VERSION = 1
TEMPORAL_SCHEMA_VERSION = 1
TEMPORAL_EVENT_TYPES = {
    "object_disappearance",
    "object_duplication",
    "orientation_discontinuity",
    "screen_visibility_contradiction",
    "geometry_deformation",
    "hand_interaction_discontinuity",
}
TEMPORAL_SEVERITIES = {"low", "medium", "high"}
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


def calculate_visual_judge_win_rate(
    passes: list[dict[str, Any]],
    *,
    baseline_sha256: str,
    candidate_sha256: str,
) -> dict[str, Any]:
    """Map two reversed blind A/B verdicts into candidate win credit."""

    if len(passes) != 2:
        raise ValueError("order-balanced judge evidence requires exactly two passes")
    if baseline_sha256 == candidate_sha256:
        for item in passes:
            order = item.get("order", {})
            if order.get("A") != baseline_sha256 or order.get("B") != baseline_sha256:
                raise ValueError("self-comparison pass hashes do not match the artifact")
            if item.get("response", {}).get("winner") not in {"A", "B", "tie"}:
                raise ValueError("judge pass has an invalid winner")
        return {
            "win_rate": 0.5,
            "credits": [0.5, 0.5],
            "self_comparison": True,
            "position_balanced": True,
        }

    expected_orders = {
        (("A", baseline_sha256), ("B", candidate_sha256)),
        (("A", candidate_sha256), ("B", baseline_sha256)),
    }
    actual_orders: set[tuple[tuple[str, str], ...]] = set()
    credits: list[float] = []
    for item in passes:
        order = item.get("order", {})
        normalized_order = tuple(sorted((str(key), str(value)) for key, value in order.items()))
        actual_orders.add(normalized_order)
        winner = item.get("response", {}).get("winner")
        if winner == "tie":
            credits.append(0.5)
        elif winner in {"A", "B"} and order.get(winner) == candidate_sha256:
            credits.append(1.0)
        elif winner in {"A", "B"} and order.get(winner) == baseline_sha256:
            credits.append(0.0)
        else:
            raise ValueError("judge pass winner does not map to either input artifact")
    if actual_orders != expected_orders:
        raise ValueError("judge passes must contain both reversed A/B input orders")
    return {
        "win_rate": round(sum(credits) / len(credits), 6),
        "credits": credits,
        "self_comparison": False,
        "position_balanced": True,
    }


def calculate_temporal_consistency(evidence: dict[str, Any]) -> dict[str, Any]:
    """Convert closed, timestamped temporal events into deterministic metrics."""

    if evidence.get("status") != "complete":
        raise ValueError("temporal evidence is incomplete")
    events = evidence.get("events")
    if not isinstance(events, list):
        raise ValueError("temporal evidence events must be a list")
    counts = {severity: 0 for severity in sorted(TEMPORAL_SEVERITIES)}
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("temporal event must be an object")
        event_type = str(event.get("event_type", ""))
        severity = str(event.get("severity", ""))
        if event_type not in TEMPORAL_EVENT_TYPES:
            raise ValueError(f"unknown temporal event type: {event_type!r}")
        if severity not in TEMPORAL_SEVERITIES:
            raise ValueError(f"unknown temporal severity: {severity!r}")
        start = float(event.get("start_seconds", -1))
        end = float(event.get("end_seconds", -1))
        frames = event.get("frame_indices")
        affected_object = str(event.get("affected_object", "")).strip()
        reason = str(event.get("reason", "")).strip()
        if start < 0 or end < start:
            raise ValueError("temporal event has an invalid time range")
        if not isinstance(frames, list) or len(frames) < 2:
            raise ValueError("temporal event requires at least two supporting frames")
        if not affected_object or not reason:
            raise ValueError("temporal event requires object and reason")
        counts[severity] += 1
        normalized.append(
            {
                **event,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "frame_indices": [int(value) for value in frames],
            }
        )
    return {
        "temporal_consistency_pass": counts["high"] == 0,
        "high_severity_event_count": counts["high"],
        "medium_severity_event_count": counts["medium"],
        "low_severity_event_count": counts["low"],
        "total_event_count": len(normalized),
        "events": normalized,
    }


def aggregate_candidate_observations(
    passes: list[dict[str, Any]],
    *,
    candidate_sha256: str,
    storyboard: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic scene evidence from both candidate-labelled passes."""

    scene_ids = [str(scene["id"]) for scene in storyboard]
    allowed_tags = {
        str(tag)
        for scene in storyboard
        for tag in scene.get("expected_tags", [])
    }
    observations_by_scene: dict[str, list[dict[str, Any]]] = {
        scene_id: [] for scene_id in scene_ids
    }
    for item in passes:
        order = item.get("order", {})
        candidate_labels = [
            label for label in ("A", "B") if order.get(label) == candidate_sha256
        ]
        if not candidate_labels:
            raise ValueError("judge pass does not contain the candidate artifact")
        # A self-comparison contains the same artifact under both labels. Use both
        # labelled observations as equivalent evidence rather than inventing one.
        labels = candidate_labels if len(candidate_labels) == 1 else ["A", "B"]
        pass_observations = item.get("response", {}).get("scene_observations", [])
        for label in labels:
            for scene_id in scene_ids:
                matches = [
                    observation
                    for observation in pass_observations
                    if observation.get("video_label") == label
                    and observation.get("scene_id") == scene_id
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "judge response must contain exactly one observation for "
                        f"video {label} scene {scene_id}"
                    )
                tags = {str(tag) for tag in matches[0].get("observed_tags", [])}
                unknown_tags = tags - allowed_tags
                if unknown_tags:
                    raise ValueError(
                        "judge returned tags outside the storyboard vocabulary: "
                        + ", ".join(sorted(unknown_tags))
                    )
                observations_by_scene[scene_id].append(matches[0])

    scenes: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    fidelity_values: list[float] = []
    for scene_id in scene_ids:
        items = observations_by_scene[scene_id]
        if len(items) < 2:
            raise ValueError(f"scene {scene_id} lacks order-balanced observations")
        tag_sets = [{str(tag) for tag in item.get("observed_tags", [])} for item in items]
        agreed_tags = set.intersection(*tag_sets)
        disputed_tags = set.union(*tag_sets) - agreed_tags
        if disputed_tags:
            disagreements.append(
                {
                    "scene_id": scene_id,
                    "field": "observed_tags",
                    "values": sorted(disputed_tags),
                }
            )

        screen_facts = {
            (
                item.get("screen_class"),
                bool(item.get("claims_tict_identity", False)),
                bool(item.get("approved_asset_match", False)),
            )
            for item in items
        }
        screen_observation = None
        if len(screen_facts) == 1:
            screen_class, claims_tict, approved_match = next(iter(screen_facts))
            screen_observation = {
                "screen_class": screen_class,
                "claims_tict_identity": claims_tict,
                "approved_asset_match": approved_match,
                "evidence_timestamp_seconds": round(
                    sum(float(item["evidence_timestamp_seconds"]) for item in items)
                    / len(items),
                    3,
                ),
            }
        else:
            disagreements.append(
                {
                    "scene_id": scene_id,
                    "field": "screen_observation",
                    "values": [list(value) for value in sorted(screen_facts, key=str)],
                }
            )

        scene_fidelity = [
            float(item["brand_asset_fidelity"])
            for item in items
            if item.get("brand_asset_fidelity") is not None
        ]
        if scene_fidelity:
            fidelity_values.append(sum(scene_fidelity) / len(scene_fidelity))
        scene_record: dict[str, Any] = {
            "scene_id": scene_id,
            "observed_tags": sorted(agreed_tags),
            "notes": "Order-balanced Gemini observation consensus.",
        }
        if screen_observation is not None:
            scene_record["screen_observation"] = screen_observation
        scenes.append(scene_record)

    return {
        "scenes": scenes,
        "disagreements": disagreements,
        "brand_asset_fidelity": (
            round(sum(fidelity_values) / len(fidelity_values), 6)
            if fidelity_values
            else None
        ),
    }


def calculate_screen_policy_metrics(
    storyboard: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Judge observed device screens against explicit per-scene intent."""

    observed_by_scene = {item["scene_id"]: item for item in observations}
    evidence: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    pending = 0
    passed = 0
    evaluated = 0
    for scene in storyboard:
        scene_id = scene["id"]
        policy = scene.get("screen_content_policy") or scene.get(
            "visual_intent", {}
        ).get("screen_content_policy")
        if not policy:
            continue
        observation = observed_by_scene.get(scene_id, {}).get("screen_observation")
        if policy == "unconstrained":
            result = {
                "scene_id": scene_id,
                "declared_policy": policy,
                "status": "pass",
                "reason": "the scene declares no device-screen constraint",
                "evidence_timestamp_seconds": (
                    observation or {}
                ).get("evidence_timestamp_seconds"),
            }
            evidence.append(result)
            evaluated += 1
            passed += 1
            continue
        if not isinstance(observation, dict):
            pending += 1
            evidence.append(
                {
                    "scene_id": scene_id,
                    "declared_policy": policy,
                    "status": "pending",
                    "reason": "no structured screen observation is available",
                    "evidence_timestamp_seconds": None,
                }
            )
            continue

        screen_class = observation.get("screen_class")
        claims_tict = bool(observation.get("claims_tict_identity", False))
        approved_match = bool(observation.get("approved_asset_match", False))
        if policy == "non_product_context":
            compliant = screen_class in {
                "generic_non_product",
                "screen_not_visible",
                "none",
            } and not claims_tict
            reason = (
                "generic or hidden non-product screen does not claim tict identity"
                if compliant
                else "observed screen conflicts with non-product context"
            )
        elif policy == "approved_product_ui":
            compliant = (
                screen_class == "approved_tict_ui"
                and claims_tict
                and approved_match
            )
            reason = (
                "observed screen matches the approved tict product capture"
                if compliant
                else "approved tict UI was required but exact asset evidence failed"
            )
        elif policy == "screen_hidden":
            compliant = screen_class in {"screen_not_visible", "none"}
            reason = (
                "device screen is not visible"
                if compliant
                else "a device screen is visible despite screen_hidden policy"
            )
        else:
            raise ValueError(f"unknown screen_content_policy: {policy!r}")

        result = {
            "scene_id": scene_id,
            "declared_policy": policy,
            "observed_screen_class": screen_class,
            "status": "pass" if compliant else "fail",
            "reason": reason,
            "evidence_timestamp_seconds": observation.get(
                "evidence_timestamp_seconds"
            ),
        }
        evidence.append(result)
        evaluated += 1
        if compliant:
            passed += 1
        else:
            failures.append(result)

    compliance = round(passed / evaluated, 6) if evaluated else None
    return {
        "compliance": compliance,
        "evaluated_scenes": evaluated,
        "passed_scenes": passed,
        "failed_scenes": len(failures),
        "pending_scenes": pending,
        "failures": failures,
        "evidence": evidence,
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


def exact_brand_text_match(
    reference: str,
    candidate: str,
    *,
    canonical: str = "tict",
) -> dict[str, Any]:
    """Measure canonical brand casing independently from token similarity."""

    pattern = re.compile(rf"(?<!\w){re.escape(canonical)}(?!\w)", re.IGNORECASE)
    reference_occurrences = pattern.findall(reference)
    candidate_occurrences = pattern.findall(candidate)
    available = bool(reference_occurrences or candidate_occurrences)
    exact = (
        available
        and reference_occurrences == [canonical] * len(reference_occurrences)
        and candidate_occurrences == [canonical] * len(candidate_occurrences)
        and len(reference_occurrences) == len(candidate_occurrences)
    )
    return {
        "available": available,
        "canonical": canonical,
        "reference_occurrences": reference_occurrences,
        "candidate_occurrences": candidate_occurrences,
        "exact_match": exact if available else None,
        "score": (1.0 if exact else 0.0) if available else None,
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
    judge_evidence_override: str | None = None,
    temporal_evidence_override: str | None = None,
) -> dict[str, Any]:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    video_value = video_override or scenario["artifact"]["video"]
    video_path = resolve_repo_path(video_value, "video")
    if not video_path.is_file():
        raise FileNotFoundError(f"video artifact does not exist: {video_path}")
    artifact_sha256 = sha256_file(video_path)

    judge_evidence: dict[str, Any] | None = None
    judge_evidence_path: Path | None = None
    if judge_evidence_override:
        raw_judge_path = Path(judge_evidence_override)
        judge_evidence_path = (
            raw_judge_path if raw_judge_path.is_absolute() else LOOP_ROOT / raw_judge_path
        )
        judge_evidence_path = ensure_within(
            judge_evidence_path,
            LOOP_ROOT,
            "judge evidence",
        )
        judge_evidence = json.loads(
            judge_evidence_path.read_text(encoding="utf-8")
        )
    if judge_evidence is None:
        raise ValueError("evaluator 0.6 requires versioned --judge-evidence")

    if not temporal_evidence_override:
        raise ValueError("evaluator 0.6 requires versioned --temporal-evidence")
    raw_temporal_path = Path(temporal_evidence_override)
    temporal_evidence_path = (
        raw_temporal_path
        if raw_temporal_path.is_absolute()
        else LOOP_ROOT / raw_temporal_path
    )
    temporal_evidence_path = ensure_within(
        temporal_evidence_path,
        LOOP_ROOT,
        "temporal evidence",
    )
    temporal_evidence = json.loads(
        temporal_evidence_path.read_text(encoding="utf-8")
    )
    if judge_evidence is not None:
        observations_path = judge_evidence_path
        observations_contract = {
            "scenario_id": scenario["id"],
            "mode": judge_evidence.get("observation_mode", "gemini_pairwise_v1"),
            "scenes": [],
        }
    elif observations_override:
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
    if judge_evidence.get("schema_version") != JUDGE_SCHEMA_VERSION:
        raise ValueError("unsupported judge evidence schema version")
    if judge_evidence.get("status") != "complete":
        raise ValueError("judge evidence is partial and cannot be scored")
    if judge_evidence.get("evaluator_version") != EVALUATOR_VERSION:
        raise ValueError("judge evidence belongs to a different evaluator version")
    if judge_evidence.get("scenario_id") != scenario["id"]:
        raise ValueError("judge evidence belongs to a different scenario")
    if judge_evidence.get("scenario_sha256") != sha256_file(scenario_path):
        raise ValueError("judge evidence scenario hash does not match")
    if judge_evidence.get("candidate_sha256") != artifact_sha256:
        raise ValueError("judge evidence candidate hash does not match the video")
    pairwise = calculate_visual_judge_win_rate(
        judge_evidence.get("passes", []),
        baseline_sha256=str(judge_evidence.get("baseline_sha256", "")),
        candidate_sha256=artifact_sha256,
    )
    automated_observations = aggregate_candidate_observations(
        judge_evidence["passes"],
        candidate_sha256=artifact_sha256,
        storyboard=storyboard,
    )
    observations = automated_observations["scenes"]
    observations_contract = {
        "mode": judge_evidence.get("observation_mode", "gemini_pairwise_v1"),
        "scenes": observations,
    }
    alignment = calculate_tag_metrics(storyboard, observations)
    screen_policy = calculate_screen_policy_metrics(storyboard, observations)
    frame_evidence = extract_frames(video_path, storyboard, artifacts_dir / "frames")

    subtitle_metric: dict[str, float | int] | None = None
    brand_text_metric: dict[str, Any] | None = None
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
    if pipeline_record is None:
        raise ValueError(
            "evaluator 0.6 requires a pipeline record with temporal source provenance"
        )
    if temporal_evidence.get("schema_version") != TEMPORAL_SCHEMA_VERSION:
        raise ValueError("unsupported temporal evidence schema version")
    if temporal_evidence.get("evaluator_version") != EVALUATOR_VERSION:
        raise ValueError("temporal evidence belongs to a different evaluator version")
    if temporal_evidence.get("status") != "complete":
        raise ValueError("temporal evidence is partial and cannot be scored")
    if temporal_evidence.get("scene_id") != "hook":
        raise ValueError("temporal evidence must screen the generated hook")
    if temporal_evidence.get("sample_fps") != 10:
        raise ValueError("temporal evidence must use the frozen 10 FPS protocol")
    temporal_source_sha256 = pipeline_record.get("temporal_source_sha256")
    if temporal_evidence.get("video_sha256") != temporal_source_sha256:
        raise ValueError(
            "temporal evidence does not match the pipeline temporal source"
        )
    temporal_consistency = calculate_temporal_consistency(temporal_evidence)
    if subtitle_path_value and pipeline_record is not None:
        subtitle_path = resolve_repo_path(subtitle_path_value, "subtitle")
        if subtitle_path.is_file():
            subtitle_text = parse_srt_text(subtitle_path)
            subtitle_metric = token_f1(
                pipeline_record.get("script", ""),
                subtitle_text,
            )
            brand_text_metric = exact_brand_text_match(
                pipeline_record.get("script", ""),
                subtitle_text,
            )
    record_metrics, record_metric_reasons = pipeline_record_metrics(pipeline_record)
    evidence_constraints, evidence_pending = pipeline_constraint_evidence(
        pipeline_record
    )

    screen_automated_pass = (
        screen_policy["compliance"] == 1.0
        and screen_policy["pending_scenes"] == 0
    )
    brand_fidelity = automated_observations["brand_asset_fidelity"]
    enforced_constraints = {
        "render_success": decode_success,
        "audio_present": audio_stream is not None if expected.get("audio_required", False) else True,
        "aspect_ratio_pass": aspect_ratio_pass,
        "duration_pass": duration_pass,
        "no_sustained_black_segments": len(black_segments) == 0,
        "all_evidence_frames_extracted": all(item["extracted"] for item in frame_evidence),
        "screen_policy_automated_pass": screen_automated_pass,
        "temporal_consistency_pass": temporal_consistency[
            "temporal_consistency_pass"
        ],
        **evidence_constraints,
    }
    brand_assets_required = bool(expected.get("brand_assets_required", False))
    if brand_assets_required and brand_fidelity is not None:
        enforced_constraints["brand_asset_fidelity_pass"] = brand_fidelity >= 0.9
    pending_constraints = {
        "voiceover_wer_pass": "timestamped ASR is not enabled in the active evaluator",
        **evidence_pending,
        "brand_pronunciation_pass": (
            "rendered-audio ASR or phoneme evidence is not enabled"
        ),
    }
    if brand_assets_required and brand_fidelity is None:
        pending_constraints["brand_asset_fidelity_pass"] = (
            "the versioned judge did not return brand fidelity evidence"
        )

    judge_charged_microusd = sum(
        int(item.get("budget", {}).get("charged_microusd", 0))
        for item in judge_evidence["passes"]
    )
    judge_actual_costs = [
        item.get("provider", {}).get("estimated_actual_cost_microusd")
        for item in judge_evidence["passes"]
    ]
    judge_actual_microusd = (
        sum(int(value) for value in judge_actual_costs)
        if all(value is not None for value in judge_actual_costs)
        else None
    )
    remaining_microusd = judge_evidence["passes"][-1].get("budget", {}).get(
        "remaining_scope_microusd"
    )

    metrics = {
        "schema_version": 1,
        "evaluator_version": EVALUATOR_VERSION,
        "generated_at": utc_now(),
        "scenario_id": scenario["id"],
        "observation_mode": observations_contract["mode"],
        "judge_protocol": {
            "schema_version": judge_evidence["schema_version"],
            "prompt_sha256": judge_evidence["prompt_sha256"],
            "requested_model": judge_evidence["requested_model"],
            "model_versions": judge_evidence["model_versions"],
        },
        "temporal_protocol": {
            "schema_version": temporal_evidence["schema_version"],
            "evaluator_version": temporal_evidence["evaluator_version"],
            "observation_mode": temporal_evidence["observation_mode"],
            "sample_fps": temporal_evidence["sample_fps"],
            "prompt_sha256": temporal_evidence["prompt_sha256"],
            "requested_model": temporal_evidence["requested_model"],
            "model_version": temporal_evidence["model_version"],
        },
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
        "primary": {
            "name": "visual_judge_win_rate",
            "value": pairwise["win_rate"],
        },
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
            "brand_text_exact_match": (
                brand_text_metric["score"] if brand_text_metric else None
            ),
            "brand_pronunciation_pass": None,
            "screen_policy_compliance": screen_policy["compliance"],
            "visual_judge_win_rate": pairwise["win_rate"],
            "temporal_consistency_pass": temporal_consistency[
                "temporal_consistency_pass"
            ],
            "temporal_high_severity_event_count": temporal_consistency[
                "high_severity_event_count"
            ],
            "temporal_medium_severity_event_count": temporal_consistency[
                "medium_severity_event_count"
            ],
            "temporal_low_severity_event_count": temporal_consistency[
                "low_severity_event_count"
            ],
            "brand_asset_fidelity": brand_fidelity,
            "voiceover_wer": None,
            "word_timing_mae_ms": None,
            "shot_boundary_mae_ms": None,
            "generation_latency_seconds": record_metrics[
                "generation_latency_seconds"
            ],
            "estimated_cost_usd": record_metrics["estimated_cost_usd"],
            "judge_charged_cost_usd": round(
                judge_charged_microusd / 1_000_000,
                6,
            ),
            "judge_estimated_actual_cost_usd": (
                round(judge_actual_microusd / 1_000_000, 6)
                if judge_actual_microusd is not None
                else None
            ),
            "remaining_iteration_budget_usd": (
                round(float(remaining_microusd) / 1_000_000, 6)
                if remaining_microusd is not None
                else None
            ),
            "cost_per_accepted_video_usd": None,
        },
        "constraints": {
            "enforced": enforced_constraints,
            "all_enforced_pass": all(enforced_constraints.values()),
            "pending": pending_constraints,
            "all_goal_constraints_verified": (
                all(enforced_constraints.values()) and not pending_constraints
            ),
        },
        "evidence": {
            "frames": frame_evidence,
            "black_segments": black_segments,
            "decode_error": decode_error or None,
            "subtitle_token_comparison": subtitle_metric,
            "brand_text_comparison": brand_text_metric,
            "screen_policy": screen_policy,
            "automated_observation_consensus": automated_observations,
            "pairwise_preference": pairwise,
            "temporal_consistency": temporal_consistency,
            "temporal_judge": temporal_evidence,
            "temporal_evidence_sha256": sha256_file(temporal_evidence_path),
            "judge": judge_evidence,
            "judge_evidence_sha256": sha256_file(judge_evidence_path),
            "observations_source": "stored_versioned_judge_evidence",
        },
        "unavailable_metric_reasons": {
            **(
                {
                    "brand_asset_fidelity": (
                        "the versioned judge did not return brand fidelity evidence"
                    )
                }
                if brand_fidelity is None
                else {}
            ),
            **(
                {
                    "brand_text_exact_match": (
                        "subtitle or canonical pipeline script is unavailable"
                    )
                }
                if not brand_text_metric
                else {}
            ),
            "brand_pronunciation_pass": (
                "rendered-audio ASR or phoneme evidence is not enabled"
            ),
            "voiceover_wer": "timestamped ASR is not enabled in the active evaluator",
            "word_timing_mae_ms": "word-level ASR alignment is not enabled in the active evaluator",
            "shot_boundary_mae_ms": "automatic scene-boundary comparison is not enabled in the active evaluator",
            "cost_per_accepted_video_usd": "cost and acceptance are unavailable",
            **(
                {
                    "screen_policy_compliance": (
                        "no scene has evaluable screen-policy evidence"
                    )
                }
                if screen_policy["compliance"] is None
                else {}
            ),
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
    parser.add_argument("--judge-evidence")
    parser.add_argument("--temporal-evidence")
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
        args.judge_evidence,
        args.temporal_evidence,
    )
    (output_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(metrics, output_dir)
    print(json.dumps({"output": str(output_dir), "primary": metrics["primary"], "constraints": metrics["constraints"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
