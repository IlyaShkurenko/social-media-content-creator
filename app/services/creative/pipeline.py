from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.creative.compiler import (
    CreativeExecutionPlan,
    compile_comparison_plans,
)
from app.services.creative.runway import (
    RunwayVideoRequest,
    estimate_runway_cost_microusd,
)
from app.services.creative.storyboard import (
    Storyboard,
    StoryboardValidationError,
    resolve_managed_asset,
    validate_storyboard,
)


class CreativePipelineError(RuntimeError):
    """Raised during deterministic preflight or artifact preparation."""


@dataclass(frozen=True)
class PreparedCreativeRun:
    output_dir: Path
    storyboard_path: Path
    stock_plan_path: Path
    runway_plan_path: Path
    runway_request_path: Path
    manifest_path: Path
    estimated_runway_cost_microusd: int


def build_runway_request(storyboard: Storyboard) -> RunwayVideoRequest:
    """Compile the controlled hook intent into the first Runway benchmark."""

    storyboard = validate_storyboard(storyboard)
    plans = compile_comparison_plans(storyboard)
    hook = plans["runway-candidate"].scenes[0]
    policy = hook.visual_intent.screen_content_policy or "unconstrained"
    screen_instruction = {
        "approved_product_ui": (
            "Do not invent application UI; keep the device display blank or "
            "occluded so the approved product capture can be composited locally."
        ),
        "non_product_context": (
            "A generic non-product phone interface may be visible when required "
            "by the action. It must not resemble tict, claim tict identity, or "
            "contain readable text."
        ),
        "screen_hidden": (
            "Keep every device screen facing away from the camera or fully hidden."
        ),
        "unconstrained": (
            "Any incidental device screen must not claim tict identity."
        ),
    }[policy]
    prompt = (
        f"{hook.visual_intent.setting}. "
        f"{hook.visual_intent.subject_action}. "
        f"Camera: {hook.visual_intent.camera}. "
        "Photorealistic, authentic premium travel advertisement, natural human motion. "
        f"{screen_instruction} "
        "No logos, watermarks, or subtitles."
    )
    base = hook.media_plan.base
    return RunwayVideoRequest(
        prompt_text=prompt,
        model=base.model or "gen4.5",
        mode="text_to_video",
        ratio="720:1280",
        duration_seconds=int(base.duration_seconds or 5),
    )


def _preflight_assets(storyboard: Storyboard, asset_root: Path) -> None:
    for scene in storyboard.scenes:
        layers = [scene.media_plan.base, *scene.media_plan.overlays]
        for layer in layers:
            if not layer.asset_id:
                continue
            try:
                resolve_managed_asset(asset_root, layer.asset_id)
            except StoryboardValidationError as exc:
                raise CreativePipelineError(
                    f"scene {scene.scene_id!r} has invalid asset {layer.asset_id!r}: {exc}"
                ) from exc


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_plan(path: Path, plan: CreativeExecutionPlan) -> None:
    _write_json(path, plan.model_dump(mode="json"))


def prepare_creative_run(
    storyboard: Storyboard,
    *,
    asset_root: Path,
    output_dir: Path,
) -> PreparedCreativeRun:
    """Validate all local inputs and persist a reproducible, unpaid run plan."""

    storyboard = validate_storyboard(storyboard)
    _preflight_assets(storyboard, asset_root)
    plans = compile_comparison_plans(storyboard)
    runway_request = build_runway_request(storyboard)
    estimated_cost = estimate_runway_cost_microusd(runway_request)

    prepared_dir = output_dir.resolve()
    prepared_dir.mkdir(parents=True, exist_ok=True)
    storyboard_path = prepared_dir / "storyboard.json"
    stock_plan_path = prepared_dir / "stock-baseline-plan.json"
    runway_plan_path = prepared_dir / "runway-candidate-plan.json"
    runway_request_path = prepared_dir / "runway-request.json"
    manifest_path = prepared_dir / "manifest.json"

    _write_json(storyboard_path, storyboard.model_dump(mode="json"))
    _write_plan(stock_plan_path, plans["stock-baseline"])
    _write_plan(runway_plan_path, plans["runway-candidate"])
    _write_json(runway_request_path, runway_request.model_dump(mode="json"))
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "storyboard_id": storyboard.storyboard_id,
            "storyboard_fingerprint": plans[
                "runway-candidate"
            ].storyboard_fingerprint,
            "variants": ["stock-baseline", "runway-candidate"],
            "runway": {
                "model": runway_request.model,
                "mode": runway_request.mode,
                "duration_seconds": runway_request.duration_seconds,
                "estimated_cost_microusd": estimated_cost,
                "submitted": False,
            },
        },
    )
    return PreparedCreativeRun(
        output_dir=prepared_dir,
        storyboard_path=storyboard_path,
        stock_plan_path=stock_plan_path,
        runway_plan_path=runway_plan_path,
        runway_request_path=runway_request_path,
        manifest_path=manifest_path,
        estimated_runway_cost_microusd=estimated_cost,
    )
