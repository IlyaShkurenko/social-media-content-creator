from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from google import genai
from google.genai import _transformers, errors, types
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, Field


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from evals.gemini_judge import (  # noqa: E402
    JUDGE_MODEL,
    actual_usage_cost_microusd,
    is_definite_nonbillable_gemini_error,
    sanitize_judge_evidence,
    sha256_file,
    sha256_text,
)


EVALUATOR_VERSION = "0.6.0"
TEMPORAL_SCHEMA_VERSION = 1
TEMPORAL_SAMPLE_FPS = 10
FRAMES_PER_STRIP = 5
MAX_OUTPUT_TOKENS = 4096
MAXIMUM_COST_MICROUSD = 50_000
ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_BUDGET_DATABASE = (
    LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
)
EVENT_TYPES = (
    "object_disappearance",
    "object_duplication",
    "orientation_discontinuity",
    "screen_visibility_contradiction",
    "geometry_deformation",
    "hand_interaction_discontinuity",
)


class TemporalEvent(BaseModel):
    event_type: Literal[
        "object_disappearance",
        "object_duplication",
        "orientation_discontinuity",
        "screen_visibility_contradiction",
        "geometry_deformation",
        "hand_interaction_discontinuity",
    ]
    severity: Literal["low", "medium", "high"]
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    frame_indices: list[int] = Field(min_length=2)
    affected_object: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class TemporalJudgeResponse(BaseModel):
    summary: str = Field(min_length=1)
    inspected_frame_count: int = Field(ge=1)
    events: list[TemporalEvent]


def validate_existing_temporal_evidence(
    evidence: dict[str, Any],
    *,
    video_path: Path,
    scene_id: str,
    scene_description: str,
    start_seconds: float,
    end_seconds: float,
    operation_id: str,
    model: str,
    budget_ledger: IterationBudgetLedger,
) -> dict[str, Any]:
    """Validate a complete paid temporal checkpoint for zero-cost replay."""

    expected = {
        "schema_version": TEMPORAL_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "observation_mode": "gemini_temporal_strips_v1",
        "status": "complete",
        "video_sha256": sha256_file(video_path),
        "scene_id": scene_id,
        "scene_range_seconds": [start_seconds, end_seconds],
        "sample_fps": TEMPORAL_SAMPLE_FPS,
        "requested_model": model,
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise ValueError(f"existing temporal evidence has stale {key}")

    frame_count = evidence.get("sampled_frame_count")
    if not isinstance(frame_count, int) or frame_count <= 0:
        raise ValueError("existing temporal evidence has invalid sampled_frame_count")
    expected_frame_count = round((end_seconds - start_seconds) * TEMPORAL_SAMPLE_FPS)
    if frame_count != expected_frame_count:
        raise ValueError("existing temporal evidence has an unexpected frame count")
    strip_hashes = evidence.get("strip_hashes")
    if (
        not isinstance(strip_hashes, list)
        or len(strip_hashes) != (frame_count + FRAMES_PER_STRIP - 1) // FRAMES_PER_STRIP
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in strip_hashes
        )
    ):
        raise ValueError("existing temporal evidence has invalid strip hashes")
    prompt = build_temporal_prompt(
        scene_id=scene_id,
        scene_description=scene_description,
        sample_fps=TEMPORAL_SAMPLE_FPS,
        frame_count=frame_count,
    )
    if evidence.get("prompt_sha256") != sha256_text(prompt):
        raise ValueError("existing temporal evidence has stale prompt_sha256")

    response = TemporalJudgeResponse.model_validate(evidence.get("response"))
    response_events = [item.model_dump(mode="json") for item in response.events]
    if evidence.get("events") != response_events:
        raise ValueError("existing temporal evidence events differ from its response")
    if response.inspected_frame_count != frame_count:
        raise ValueError("existing temporal response has an unexpected frame count")
    valid_indices = set(range(frame_count))
    for event in response.events:
        if not set(event.frame_indices).issubset(valid_indices):
            raise ValueError("existing temporal evidence cites an unknown frame")
        if (
            event.end_seconds < event.start_seconds
            or event.start_seconds < start_seconds
            or event.end_seconds > end_seconds
        ):
            raise ValueError("existing temporal evidence cites an invalid time range")

    budget = evidence.get("budget")
    if not isinstance(budget, dict) or budget.get("operation_id") != operation_id:
        raise ValueError("existing temporal evidence has stale budget operation")
    charged = budget.get("charged_microusd")
    operation = budget_ledger.find_operation(operation_id)
    if (
        not isinstance(charged, int)
        or charged <= 0
        or operation is None
        or operation.status not in {"manual_charge", "submitted"}
        or operation.amount_microusd != charged
    ):
        raise ValueError("existing temporal evidence does not match the paid ledger")
    return sanitize_judge_evidence(evidence)


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        str(REPO_ROOT / "resource" / "fonts" / "BeVietnamPro-Bold.ttf"),
        size=size,
    )


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def extract_temporal_frames(
    video_path: Path,
    *,
    output_dir: Path,
    start_seconds: float,
    end_seconds: float,
    sample_fps: int = TEMPORAL_SAMPLE_FPS,
) -> list[dict[str, Any]]:
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("temporal scene range is invalid")
    if sample_fps <= 0:
        raise ValueError("temporal sample rate must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_pattern = output_dir / "frame-%04d.jpg"
    result = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{end_seconds - start_seconds:.3f}",
            "-vf",
            f"fps={sample_fps}",
            "-q:v",
            "2",
            str(frame_pattern),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(
            "temporal frame extraction failed: "
            + (result.stderr.strip() or "ffmpeg failed")[-1200:]
        )
    frame_paths = sorted(output_dir.glob("frame-*.jpg"))
    expected = round((end_seconds - start_seconds) * sample_fps)
    if len(frame_paths) != expected:
        raise RuntimeError(
            f"temporal frame count mismatch: expected {expected}, got {len(frame_paths)}"
        )
    return [
        {
            "frame_index": index,
            "timestamp_seconds": round(start_seconds + index / sample_fps, 3),
            "path": path,
            "sha256": sha256_file(path),
        }
        for index, path in enumerate(frame_paths)
    ]


def build_timestamped_strips(
    frames: list[dict[str, Any]],
    *,
    output_dir: Path,
    frames_per_strip: int = FRAMES_PER_STRIP,
) -> list[dict[str, Any]]:
    if not frames or frames_per_strip <= 1:
        raise ValueError("temporal strips require at least two sampled frames")
    output_dir.mkdir(parents=True, exist_ok=True)
    strips: list[dict[str, Any]] = []
    frame_size = (360, 640)
    label_height = 38
    font = _font(18)
    for strip_index, offset in enumerate(
        range(0, len(frames), frames_per_strip),
        start=1,
    ):
        items = frames[offset : offset + frames_per_strip]
        canvas = Image.new(
            "RGB",
            (frame_size[0] * len(items), frame_size[1] + label_height),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for column, item in enumerate(items):
            with Image.open(item["path"]) as source:
                frame = ImageOps.fit(
                    source.convert("RGB"),
                    frame_size,
                    method=Image.Resampling.LANCZOS,
                )
            x = column * frame_size[0]
            canvas.paste(frame, (x, label_height))
            draw.text(
                (x + 8, 7),
                f"F{item['frame_index']:02d}  {item['timestamp_seconds']:.1f}s",
                fill="black",
                font=font,
            )
        strip_path = output_dir / f"strip-{strip_index:02d}.jpg"
        canvas.save(strip_path, format="JPEG", quality=92, optimize=True)
        strips.append(
            {
                "strip_index": strip_index,
                "frame_indices": [item["frame_index"] for item in items],
                "start_seconds": items[0]["timestamp_seconds"],
                "end_seconds": items[-1]["timestamp_seconds"],
                "path": strip_path,
                "sha256": sha256_file(strip_path),
            }
        )
    return strips


def build_temporal_prompt(
    *,
    scene_id: str,
    scene_description: str,
    sample_fps: int,
    frame_count: int,
) -> str:
    return f"""
You are a strict temporal-continuity inspector for generated advertising footage.
The attached images are chronological frame strips from scene {scene_id!r}. Each
cell is labelled with its exact frame index and source timestamp. Inspect adjacent
frames in order at {sample_fps} FPS ({frame_count} frames total).

Scene intent: {scene_description}

Report only clearly visible temporal contradictions from this closed vocabulary:
{json.dumps(EVENT_TYPES)}

Focus on persistent object identity, phone count, phone front/back/edge
orientation, whether a display can physically be visible at that orientation,
screen appearance before or after a real device turn, hand-to-device contact,
finger/hand geometry, and objects appearing, disappearing, duplicating, or
deforming between adjacent frames. Normal motion blur, occlusion, perspective,
and a physically continuous rotation are not errors.

Use severity "high" only for a production-breaking contradiction clearly
supported by at least two labelled frames. Use "medium" for a visible but less
certain defect and "low" for a minor discontinuity. Do not invent an event from
the scene description. If the sequence is physically coherent, return no events.
Every event must cite the exact supporting frame indices and timestamps.
""".strip()


def _usage_record(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }


def validate_provider_response_schema(api_client: Any | None = None) -> None:
    """Fail locally before submission when Gemini cannot encode our schema."""

    _transformers.t_schema(api_client, TemporalJudgeResponse)


class GeminiTemporalJudge:
    def __init__(
        self,
        *,
        api_key: str,
        budget_ledger: IterationBudgetLedger,
        client: Any | None = None,
        model: str = JUDGE_MODEL,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key is required")
        self.client = client or genai.Client(api_key=api_key)
        self.budget_ledger = budget_ledger
        self.model = model

    def inspect(
        self,
        *,
        video_path: Path,
        scene_id: str,
        scene_description: str,
        start_seconds: float,
        end_seconds: float,
        working_dir: Path,
        operation_id: str,
    ) -> dict[str, Any]:
        existing_operation = self.budget_ledger.find_operation(operation_id)
        if existing_operation is not None:
            raise RuntimeError(
                f"temporal operation {operation_id!r} is already "
                f"{existing_operation.status}; automatic paid resubmission is blocked"
            )
        frames = extract_temporal_frames(
            video_path,
            output_dir=working_dir / "frames",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        strips = build_timestamped_strips(
            frames,
            output_dir=working_dir / "strips",
        )
        prompt = build_temporal_prompt(
            scene_id=scene_id,
            scene_description=scene_description,
            sample_fps=TEMPORAL_SAMPLE_FPS,
            frame_count=len(frames),
        )
        validate_provider_response_schema(getattr(self.client, "_api_client", None))
        self.budget_ledger.ensure_available(MAXIMUM_COST_MICROUSD)
        parts = [
            types.Part.from_bytes(
                data=item["path"].read_bytes(),
                mime_type="image/jpeg",
            )
            for item in strips
        ]
        parts.append(types.Part.from_text(text=prompt))
        description = (
            f"Gemini {self.model} evaluator {EVALUATOR_VERSION} temporal {scene_id}"
        )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=types.Content(role="user", parts=parts),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=TemporalJudgeResponse,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=False,
                        thinking_level="medium",
                    ),
                ),
            )
        except errors.APIError as exc:
            if is_definite_nonbillable_gemini_error(exc):
                raise RuntimeError(
                    "Gemini rejected the temporal request before returning a "
                    f"billable result (HTTP {exc.code}); no charge was recorded"
                ) from exc
            self.budget_ledger.record_manual_charge(
                operation_id,
                MAXIMUM_COST_MICROUSD,
                f"{description}; ambiguous HTTP {exc.code}, worst-case charge",
            )
            raise RuntimeError(
                "Gemini temporal submission outcome is ambiguous; a worst-case "
                f"charge was recorded under operation {operation_id!r}"
            ) from exc
        except Exception as exc:
            self.budget_ledger.record_manual_charge(
                operation_id,
                MAXIMUM_COST_MICROUSD,
                f"{description}; unknown provider outcome, worst-case charge",
            )
            raise RuntimeError(
                "Gemini temporal submission outcome is unknown; a worst-case "
                f"charge was recorded under operation {operation_id!r}"
            ) from exc

        response_id = str(
            getattr(response, "response_id", "")
            or f"gemini-{sha256_text(operation_id)[:24]}"
        )
        usage = _usage_record(response)
        actual_cost = actual_usage_cost_microusd(
            model=self.model,
            prompt_tokens=usage["prompt_tokens"],
            output_tokens=usage["output_tokens"],
        )
        charged_microusd = actual_cost or MAXIMUM_COST_MICROUSD
        self.budget_ledger.record_manual_charge(
            operation_id,
            charged_microusd,
            f"{description}; provider response {response_id}",
        )
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            parsed = json.loads(response.text)
        parsed_response = TemporalJudgeResponse.model_validate(parsed)
        if parsed_response.inspected_frame_count != len(frames):
            raise ValueError("temporal judge did not confirm the supplied frame count")
        valid_indices = {item["frame_index"] for item in frames}
        for event in parsed_response.events:
            if not set(event.frame_indices).issubset(valid_indices):
                raise ValueError("temporal judge cited an unknown frame index")
            if event.end_seconds < event.start_seconds:
                raise ValueError("temporal judge returned an invalid time range")
        snapshot = self.budget_ledger.snapshot()
        return sanitize_judge_evidence(
            {
                "schema_version": TEMPORAL_SCHEMA_VERSION,
                "evaluator_version": EVALUATOR_VERSION,
                "observation_mode": "gemini_temporal_strips_v1",
                "status": "complete",
                "video_sha256": sha256_file(video_path),
                "scene_id": scene_id,
                "scene_range_seconds": [start_seconds, end_seconds],
                "sample_fps": TEMPORAL_SAMPLE_FPS,
                "sampled_frame_count": len(frames),
                "strip_hashes": [item["sha256"] for item in strips],
                "prompt_sha256": sha256_text(prompt),
                "requested_model": self.model,
                "model_version": str(
                    getattr(response, "model_version", "") or self.model
                ),
                "response": parsed_response.model_dump(mode="json"),
                "events": [
                    item.model_dump(mode="json") for item in parsed_response.events
                ],
                "provider": {
                    "response_id": response_id,
                    "usage": usage,
                    "estimated_actual_cost_microusd": actual_cost,
                },
                "budget": {
                    "operation_id": operation_id,
                    "preflight_maximum_microusd": MAXIMUM_COST_MICROUSD,
                    "charged_microusd": charged_microusd,
                    "remaining_scope_microusd": snapshot.remaining_microusd,
                },
            }
        )


def _resolve_managed_file(value: str, *, root: Path, label: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    if not resolved.is_file() or root.resolve() not in resolved.parents:
        raise ValueError(f"{label} must be a file inside {root}: {resolved}")
    return resolved


def _resolve_output(value: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else LOOP_ROOT / raw
    resolved = candidate.resolve()
    if LOOP_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"output must stay inside {LOOP_ROOT}: {resolved}")
    return resolved


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run high-frequency Gemini temporal screening"
    )
    parser.add_argument("--storyboard", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--scene-id", default="hook")
    parser.add_argument("--output", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--model", default=JUDGE_MODEL)
    parser.add_argument("--budget-database", default=str(DEFAULT_BUDGET_DATABASE))
    parser.add_argument("--confirm-paid", choices=("YES",), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    storyboard_path = _resolve_managed_file(
        args.storyboard,
        root=LOOP_ROOT,
        label="storyboard",
    )
    video_path = _resolve_managed_file(
        args.video,
        root=REPO_ROOT,
        label="video",
    )
    output = _resolve_output(args.output)
    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    scenes = [
        scene for scene in storyboard["scenes"] if scene["scene_id"] == args.scene_id
    ]
    if len(scenes) != 1:
        raise ValueError(f"storyboard has no unique scene {args.scene_id!r}")
    scene = scenes[0]
    intent = scene["visual_intent"]
    description = "; ".join(
        [
            str(intent["setting"]),
            str(intent["subject_action"]),
            str(intent["camera"]),
            f"screen policy: {intent['screen_content_policy']}",
        ]
    )
    ledger = IterationBudgetLedger(
        Path(args.budget_database),
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    )
    start_seconds = float(scene["start_seconds"])
    end_seconds = float(scene["end_seconds"])
    if output.exists():
        if not output.is_file():
            raise ValueError("existing temporal output is not a file")
        loaded = json.loads(output.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("existing temporal output must contain one JSON object")
        evidence = validate_existing_temporal_evidence(
            loaded,
            video_path=video_path,
            scene_id=args.scene_id,
            scene_description=description,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            operation_id=args.operation_id,
            model=args.model,
            budget_ledger=ledger,
        )
    else:
        from app.config import config

        api_key = str(config.app.get("gemini_api_key", "") or "").strip()
        api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Gemini API key is not configured")
        judge = GeminiTemporalJudge(
            api_key=api_key,
            budget_ledger=ledger,
            model=args.model,
        )
        working_dir = output.parent / f"{output.stem}-strips"
        evidence = judge.inspect(
            video_path=video_path,
            scene_id=args.scene_id,
            scene_description=description,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            working_dir=working_dir,
            operation_id=args.operation_id,
        )
        _write_json(output, evidence)
    print(
        json.dumps(
            {
                "output": str(output.relative_to(LOOP_ROOT)),
                "video_sha256": evidence["video_sha256"],
                "sampled_frames": evidence["sampled_frame_count"],
                "events": len(evidence["events"]),
                "high_severity_events": sum(
                    1 for item in evidence["events"] if item["severity"] == "high"
                ),
                "charged_microusd": evidence["budget"]["charged_microusd"],
                "remaining_microusd": evidence["budget"][
                    "remaining_scope_microusd"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
