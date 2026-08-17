from __future__ import annotations

import hashlib
import json

from pydantic import Field

from app.services.creative.storyboard import (
    CreativeModel,
    Storyboard,
    StoryboardScene,
    StoryboardValidationError,
    validate_storyboard,
)


class CreativeExecutionPlan(CreativeModel):
    variant_id: str
    storyboard_fingerprint: str
    narration_script: str
    scenes: list[StoryboardScene] = Field(min_length=1)


def _fingerprint(storyboard: Storyboard) -> str:
    canonical = json.dumps(
        storyboard.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_comparison_plans(
    storyboard: Storyboard,
) -> dict[str, CreativeExecutionPlan]:
    """Compile controlled stock and Runway plans from one storyboard."""

    storyboard = validate_storyboard(storyboard)
    if storyboard.scenes[0].purpose != "hook":
        raise StoryboardValidationError(
            "the first comparison scene must have purpose 'hook'"
        )

    hook_duration = (
        storyboard.scenes[0].end_seconds - storyboard.scenes[0].start_seconds
    )
    if hook_duration != 5:
        raise StoryboardValidationError(
            "the first Runway benchmark requires a five-second hook"
        )

    baseline_scenes = [scene.model_copy(deep=True) for scene in storyboard.scenes]
    candidate_scenes = [scene.model_copy(deep=True) for scene in storyboard.scenes]

    baseline_base = baseline_scenes[0].media_plan.base
    baseline_scenes[0].media_plan.base = baseline_base.model_copy(
        update={
            "kind": "stock",
            "provider": baseline_base.provider or "pexels",
            "model": None,
            "mode": "search",
            "duration_seconds": hook_duration,
        }
    )

    candidate_base = candidate_scenes[0].media_plan.base
    candidate_scenes[0].media_plan.base = candidate_base.model_copy(
        update={
            "kind": "generated",
            "provider": "runway",
            "model": "gen4.5",
            "mode": "text_to_video",
            "duration_seconds": hook_duration,
        }
    )

    fingerprint = _fingerprint(storyboard)
    narration_script = " ".join(
        scene.voiceover.strip() for scene in storyboard.scenes if scene.voiceover.strip()
    )
    return {
        "stock-baseline": CreativeExecutionPlan(
            variant_id="stock-baseline",
            storyboard_fingerprint=fingerprint,
            narration_script=narration_script,
            scenes=baseline_scenes,
        ),
        "runway-candidate": CreativeExecutionPlan(
            variant_id="runway-candidate",
            storyboard_fingerprint=fingerprint,
            narration_script=narration_script,
            scenes=candidate_scenes,
        ),
    }
