from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.creative.pipeline import (
    CreativePipelineError,
    build_runway_request,
    prepare_creative_run,
)
from app.services.creative.storyboard import validate_storyboard


REPO_ROOT = Path(__file__).resolve().parents[2]
STORYBOARD_PATH = (
    REPO_ROOT
    / "feedback-loop/video-quality/evals/dataset/mixed-media-first-slice-001.json"
)
ASSET_ROOT = REPO_ROOT / "feedback-loop/video-quality/evals/assets/brand"


def storyboard():
    return validate_storyboard(json.loads(STORYBOARD_PATH.read_text(encoding="utf-8")))


def test_adpipe_1_3_prepare_run_resolves_assets_and_writes_reproducible_plans(
    tmp_path: Path,
) -> None:
    prepared = prepare_creative_run(
        storyboard(),
        asset_root=ASSET_ROOT,
        output_dir=tmp_path / "run",
    )
    assert prepared.storyboard_path.is_file()
    assert prepared.stock_plan_path.is_file()
    assert prepared.runway_plan_path.is_file()
    assert prepared.runway_request_path.is_file()
    assert prepared.estimated_runway_cost_microusd == 600_000
    request_payload = json.loads(prepared.runway_request_path.read_text())
    assert request_payload["model"] == "gen4.5"
    assert request_payload["duration_seconds"] == 5


def test_runway_1_1_prompt_describes_action_and_excludes_generated_ui() -> None:
    request = build_runway_request(storyboard())
    assert "switches between several planning tabs" in request.prompt_text
    assert "No readable text, logos, application UI" in request.prompt_text
    assert request.ratio == "720:1280"


def test_brand_1_3_prepare_run_fails_before_generation_for_missing_asset(
    tmp_path: Path,
) -> None:
    changed = storyboard().model_copy(deep=True)
    changed.scenes[1].media_plan.overlays[0].asset_id = "screens/missing.png"
    with pytest.raises(CreativePipelineError, match="missing.png"):
        prepare_creative_run(
            changed,
            asset_root=ASSET_ROOT,
            output_dir=tmp_path / "run",
        )
