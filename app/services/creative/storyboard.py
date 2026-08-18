from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class StoryboardValidationError(ValueError):
    """Raised when a storyboard is unsafe or ambiguous to execute."""


class CreativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisualIntent(CreativeModel):
    setting: str = Field(min_length=1)
    subject_action: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    screen_content_policy: Literal[
        "approved_product_ui",
        "non_product_context",
        "screen_hidden",
        "unconstrained",
    ] | None = None


class BrandPronunciation(CreativeModel):
    canonical: str = Field(min_length=1)
    spoken_alias: str = Field(min_length=1)
    ipa: str = Field(min_length=1)


class MediaLayer(CreativeModel):
    kind: Literal[
        "stock",
        "generated",
        "solid_or_graphic",
        "uploaded_video",
        "uploaded_image",
        "product_capture",
        "brand_asset",
        "graphic",
    ]
    intent: str | None = None
    asset_id: str | None = None
    placement: str | None = None
    role: Literal["logo", "hero"] | None = None
    provider: str | None = None
    model: str | None = None
    mode: str | None = None
    duration_seconds: float | None = Field(default=None, gt=0)


class MediaPlan(CreativeModel):
    base: MediaLayer
    overlays: list[MediaLayer] = Field(default_factory=list)


class LayoutElementIntent(CreativeModel):
    element_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    vertical_region: Literal["top", "upper", "center", "lower", "bottom"]
    horizontal_alignment: Literal["left", "center", "right"] = "center"
    scale: Literal["small", "medium", "large"] = "medium"


class SceneLayoutIntent(CreativeModel):
    mode: Literal["portrait_regions"] = "portrait_regions"
    elements: list[LayoutElementIntent] = Field(min_length=1)


class StoryboardScene(CreativeModel):
    scene_id: str = Field(min_length=1)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    purpose: Literal["hook", "product_demo", "cta", "support"]
    visual_intent: VisualIntent
    media_plan: MediaPlan
    voiceover: str = ""
    onscreen_text: str = ""
    call_to_action: str = ""
    layout_intent: SceneLayoutIntent | None = None
    expected_evidence: list[str] = Field(default_factory=list)
    voice_instructions: str | None = Field(default=None, min_length=1)


class Storyboard(CreativeModel):
    schema_version: Literal["1.0", "1.1", "1.2"]
    storyboard_id: str = Field(min_length=1)
    content_language: str = Field(min_length=1)
    aspect_ratio: str = Field(min_length=1)
    target_duration_seconds: float = Field(gt=0)
    hypothesis: str = Field(min_length=1)
    brand_pronunciations: list[BrandPronunciation] = Field(default_factory=list)
    scenes: list[StoryboardScene] = Field(min_length=1)


class NarrationSettings(CreativeModel):
    content_language: str
    voice_name: str


_DEFAULT_VOICES = {
    "en-US": "en-US-JennyNeural-Female",
}


def validate_storyboard(payload: Storyboard | dict) -> Storyboard:
    """Parse and preflight a first-slice storyboard before any paid work."""

    try:
        storyboard = (
            payload if isinstance(payload, Storyboard) else Storyboard.model_validate(payload)
        )
    except ValidationError as exc:
        raise StoryboardValidationError(f"invalid storyboard schema: {exc}") from exc

    if storyboard.content_language != "en-US":
        raise StoryboardValidationError(
            "the first advertising slice requires content_language en-US"
        )
    if storyboard.aspect_ratio != "9:16":
        raise StoryboardValidationError(
            "the first advertising slice requires aspect_ratio 9:16"
        )

    seen_scene_ids: set[str] = set()
    seen_pronunciations: set[str] = set()
    for pronunciation in storyboard.brand_pronunciations:
        canonical = pronunciation.canonical
        if canonical != canonical.lower():
            raise StoryboardValidationError(
                f"canonical brand spelling must be lowercase: {canonical!r}"
            )
        if canonical in seen_pronunciations:
            raise StoryboardValidationError(
                f"duplicate brand pronunciation {canonical!r} is not allowed"
            )
        seen_pronunciations.add(canonical)
        if canonical == "tict" and (
            pronunciation.spoken_alias.lower() != "tickt"
            or pronunciation.ipa.strip("/") != "tɪkt"
        ):
            raise StoryboardValidationError(
                "tict narration must declare spoken alias 'tickt' and IPA 'tɪkt'"
            )

    previous_end = 0.0
    for index, scene in enumerate(storyboard.scenes):
        if scene.scene_id in seen_scene_ids:
            raise StoryboardValidationError(
                f"duplicate scene_id {scene.scene_id!r} is not allowed"
            )
        seen_scene_ids.add(scene.scene_id)

        if scene.end_seconds <= scene.start_seconds:
            raise StoryboardValidationError(
                f"scene {scene.scene_id!r} must end after it starts"
            )
        if index == 0 and scene.start_seconds != 0:
            raise StoryboardValidationError("the first scene must start at 0 seconds")
        if scene.start_seconds < previous_end:
            raise StoryboardValidationError(
                f"scene {scene.scene_id!r} overlaps the previous scene"
            )
        if scene.end_seconds > storyboard.target_duration_seconds:
            raise StoryboardValidationError(
                f"scene {scene.scene_id!r} exceeds the target duration"
            )
        if storyboard.schema_version in {"1.1", "1.2"}:
            policy = scene.visual_intent.screen_content_policy
            if policy is None:
                raise StoryboardValidationError(
                    f"scene {scene.scene_id!r} must declare screen_content_policy"
                )
            layers = [scene.media_plan.base, *scene.media_plan.overlays]
            has_product_capture = any(
                layer.kind == "product_capture" for layer in layers
            )
            if policy == "approved_product_ui" and not has_product_capture:
                raise StoryboardValidationError(
                    f"scene {scene.scene_id!r} requires an approved product_capture"
                )
            if has_product_capture and policy != "approved_product_ui":
                raise StoryboardValidationError(
                    f"scene {scene.scene_id!r} with product_capture must declare "
                    "approved_product_ui"
                )
            if scene.purpose == "cta" and not scene.call_to_action.strip():
                raise StoryboardValidationError(
                    f"scene {scene.scene_id!r} must declare call_to_action copy"
                )
            for layer in layers:
                if layer.role is not None and layer.kind != "brand_asset":
                    raise StoryboardValidationError(
                        f"scene {scene.scene_id!r} assigns a brand role to a "
                        f"non-brand layer"
                    )
            if storyboard.schema_version == "1.2" and scene.purpose == "cta":
                if scene.layout_intent is None:
                    raise StoryboardValidationError(
                        f"scene {scene.scene_id!r} must declare layout_intent"
                    )
                element_ids = [
                    item.element_id for item in scene.layout_intent.elements
                ]
                if len(element_ids) != len(set(element_ids)):
                    raise StoryboardValidationError(
                        f"scene {scene.scene_id!r} has duplicate layout element IDs"
                    )
                required_layout_ids = {
                    "headline",
                    "action",
                    *(
                        layer.role
                        for layer in layers
                        if layer.kind == "brand_asset" and layer.role is not None
                    ),
                }
                if set(element_ids) != required_layout_ids:
                    raise StoryboardValidationError(
                        f"scene {scene.scene_id!r} layout elements must be exactly "
                        f"{sorted(required_layout_ids)}"
                    )
            for pronunciation in storyboard.brand_pronunciations:
                for copy in (
                    scene.voiceover,
                    scene.onscreen_text,
                    scene.call_to_action,
                ):
                    matches = re.findall(
                        rf"(?<!\w){re.escape(pronunciation.canonical)}(?!\w)",
                        copy,
                        flags=re.IGNORECASE,
                    )
                    if any(match != pronunciation.canonical for match in matches):
                        raise StoryboardValidationError(
                            "visible brand copy must use canonical lowercase "
                            f"{pronunciation.canonical!r}"
                        )
        previous_end = scene.end_seconds

    return storyboard


def apply_brand_pronunciations(storyboard: Storyboard, text: str) -> str:
    """Convert canonical display copy to provider-friendly spoken aliases."""

    result = text
    for pronunciation in storyboard.brand_pronunciations:
        result = re.sub(
            rf"(?<!\w){re.escape(pronunciation.canonical)}(?!\w)",
            pronunciation.spoken_alias,
            result,
        )
    return result


def parse_storyboard_json(raw: str) -> Storyboard:
    """Accept strict JSON, with at most one surrounding Markdown code fence."""

    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if (
            len(lines) < 3
            or lines[0].strip().lower() not in {"```", "```json"}
            or lines[-1].strip() != "```"
        ):
            raise StoryboardValidationError(
                "storyboard response must contain raw JSON with one optional code fence"
            )
        candidate = "\n".join(lines[1:-1]).strip()

    if not candidate.startswith("{") or not candidate.endswith("}"):
        raise StoryboardValidationError(
            "storyboard response must contain raw JSON without explanatory prose"
        )

    try:
        payload = json.loads(candidate)
    except (TypeError, json.JSONDecodeError) as exc:
        raise StoryboardValidationError(
            "storyboard response must contain valid raw JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise StoryboardValidationError("storyboard raw JSON must be an object")
    return validate_storyboard(payload)


# Non-Azure providers key their own catalogues with a "provider:..." prefix
# rather than a "{content_language}-..." one; this project only supports
# en-US content today, so any of these is already implicitly en-US-only.
_NON_LOCALE_PREFIXED_PROVIDERS = (
    "openai:",
    "elevenlabs:",
    "minimax:",
    "siliconflow:",
    "gemini:",
    "mimo:",
)


def resolve_narration_voice(
    *,
    content_language: str,
    requested_voice: str | None,
    interface_locale: str | None = None,
) -> NarrationSettings:
    """Resolve narration from content language, never from the UI locale."""

    del interface_locale
    default_voice = _DEFAULT_VOICES.get(content_language)
    if default_voice is None:
        raise StoryboardValidationError(
            f"no default narration voice is configured for {content_language}"
        )

    voice_name = (requested_voice or "").strip() or default_voice
    is_locale_prefixed = voice_name.startswith(f"{content_language}-")
    is_provider_prefixed = voice_name.startswith(_NON_LOCALE_PREFIXED_PROVIDERS)
    if not is_locale_prefixed and not is_provider_prefixed:
        raise StoryboardValidationError(
            f"voice {voice_name!r} is incompatible with {content_language} content"
        )
    return NarrationSettings(
        content_language=content_language,
        voice_name=voice_name,
    )


def resolve_managed_asset(root: Path, asset_id: str) -> Path:
    """Resolve a user asset while preventing absolute paths and traversal."""

    managed_root = root.resolve()
    supplied = Path(asset_id)
    if supplied.is_absolute():
        raise StoryboardValidationError(
            "asset must resolve inside the managed asset root"
        )
    candidate = (managed_root / supplied).resolve()
    try:
        candidate.relative_to(managed_root)
    except ValueError as exc:
        raise StoryboardValidationError(
            "asset must resolve inside the managed asset root"
        ) from exc
    if not candidate.is_file():
        raise StoryboardValidationError(
            f"managed asset does not exist: {asset_id!r}"
        )
    return candidate
