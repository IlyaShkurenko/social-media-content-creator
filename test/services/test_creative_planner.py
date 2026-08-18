from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.creative.planner import (
    StoryboardBrief,
    StoryboardPlanningError,
    build_storyboard_prompt,
    plan_storyboard,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO_ROOT
    / "feedback-loop/video-quality/evals/dataset/mixed-media-first-slice-001.json"
)


def brief() -> StoryboardBrief:
    return StoryboardBrief(
        product_name="tict",
        audience="independent travellers overwhelmed by fragmented planning",
        hypothesis=(
            "Showing trip-planning stress before a real tict trip plan makes "
            "the product benefit immediately understandable."
        ),
        product_facts=[
            "tict creates one trip plan from bookings, places, and next steps."
        ],
        available_asset_ids=[
            "screens/Plan_Overview_Screen.png",
            "tict-logo.png",
            "tict-mascot-1.png",
        ],
    )


def test_story_1_4_prompt_keeps_user_hypothesis_and_english_contract() -> None:
    prompt = build_storyboard_prompt(brief())
    assert brief().hypothesis in prompt
    assert "en-US" in prompt
    assert "15" in prompt
    assert "raw JSON" in prompt
    assert "Do not invent another hypothesis" in prompt
    assert 'canonical lowercase "tict"' in prompt
    assert "screen_content_policy" in prompt
    assert 'schema_version="1.2"' in prompt
    assert "layout_intent" in prompt
    assert "top/upper/center/lower/bottom" in prompt
    assert "never emit pixel coordinates" in prompt


def test_adpipe_1_1_planner_returns_typed_storyboard_from_llm_json() -> None:
    raw = FIXTURE.read_text(encoding="utf-8")
    storyboard = plan_storyboard(brief(), response_generator=lambda _: raw)
    assert storyboard.storyboard_id == "mixed-media-first-slice-001"
    assert storyboard.hypothesis == brief().hypothesis
    assert storyboard.scenes[1].media_plan.overlays[0].kind == "product_capture"
    assert storyboard.scenes[2].layout_intent is not None
    assert storyboard.scenes[2].layout_intent.elements[0].element_id == "logo"


def test_story_1_4_planner_rejects_hypothesis_drift() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["hypothesis"] = "A different model-generated hypothesis."
    with pytest.raises(StoryboardPlanningError, match="hypothesis"):
        plan_storyboard(brief(), response_generator=lambda _: json.dumps(payload))


def test_story_1_2_planner_rejects_llm_explanatory_prose() -> None:
    raw = f"Here is the plan: {FIXTURE.read_text(encoding='utf-8')}"
    with pytest.raises(StoryboardPlanningError, match="raw JSON"):
        plan_storyboard(brief(), response_generator=lambda _: raw)
