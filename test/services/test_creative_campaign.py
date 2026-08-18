from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.creative.campaign import (
    CampaignBrief,
    CampaignPlanningError,
    build_campaign_plan,
    build_candidate_scorecard,
    build_hypothesis_prompt,
    compile_concept_storyboards,
    plan_hypotheses,
)
from app.services.creative.storyboard import validate_storyboard


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = (
    REPO_ROOT
    / "feedback-loop/video-quality/evals/dataset/mixed-media-first-slice-001.json"
)


def campaign_brief() -> CampaignBrief:
    return CampaignBrief(
        product_name="tict",
        audience="independent travellers overwhelmed by fragmented planning",
        product_facts=[
            "tict creates one trip plan from bookings, places, and next steps."
        ],
        available_asset_ids=[
            "screens/Plan_Overview_Screen.png",
            "tict-logo.png",
            "tict-mascot-1.png",
        ],
        concept_count=3,
    )


def concept(
    concept_id: str,
    hypothesis: str,
    action: str,
    emotion: str,
) -> dict:
    return {
        "concept_id": concept_id,
        "hypothesis": hypothesis,
        "audience_problem": "Planning is fragmented across too many tools.",
        "target_emotion": emotion,
        "emotional_arc": "Tension becomes relief and control.",
        "hook_setting": "a bright international airport departure hall",
        "hook_camera": "natural handheld push-in",
        "hook_voiceover": "Planning a trip should not feel like another job.",
        "hook_beats": [
            {
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "visible_action": action,
                "expected_evidence": ["planning_stress_visible"],
            },
            {
                "start_seconds": 2.0,
                "end_seconds": 5.0,
                "visible_action": "The traveller exhales and lowers the phone.",
                "expected_evidence": ["traveller_visible", "phone_visible"],
            },
        ],
        "product_bridge": "Match the lowered phone into the exact tict plan.",
        "quality_criteria": [
            "The problem is readable without audio in two seconds.",
            "The emotion is visible and natural.",
        ],
    }


def response_payload() -> dict:
    return {
        "schema_version": "1.0",
        "product_name": "tict",
        "content_language": "en-US",
        "concepts": [
            concept(
                "tab-overload",
                "Tab overload makes planning fragmentation immediate.",
                "The traveller rapidly switches between planning tabs.",
                "overwhelm",
            ),
            concept(
                "missed-detail",
                "A missed detail creates relatable travel anxiety.",
                "The traveller notices a conflicting booking time.",
                "anxiety",
            ),
            concept(
                "decision-fatigue",
                "Decision fatigue makes one clear plan feel valuable.",
                "The traveller compares notes and closes her eyes.",
                "frustration",
            ),
        ],
    }


def planned_batch():
    return plan_hypotheses(
        campaign_brief(),
        response_generator=lambda _: json.dumps(response_payload()),
    )


def test_story_2_1_prompt_requests_exact_distinct_concept_count() -> None:
    prompt = build_hypothesis_prompt(campaign_brief())
    assert "exactly 3" in prompt
    assert "meaningfully distinct" in prompt
    assert "0.0" in prompt and "5.0" in prompt
    assert "raw JSON" in prompt


def test_story_2_1_duplicate_opening_actions_are_rejected() -> None:
    payload = response_payload()
    payload["concepts"][1]["hook_beats"][0]["visible_action"] = payload[
        "concepts"
    ][0]["hook_beats"][0]["visible_action"]

    with pytest.raises(CampaignPlanningError, match="opening action"):
        plan_hypotheses(
            campaign_brief(),
            response_generator=lambda _: json.dumps(payload),
        )


def test_story_2_3_compilation_changes_only_hook_intent() -> None:
    template = validate_storyboard(
        json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    )
    compiled = compile_concept_storyboards(
        planned_batch(),
        template_storyboard=template,
    )

    assert len(compiled) == 3
    assert len({item.storyboard.hypothesis for item in compiled}) == 3
    for item in compiled:
        assert item.storyboard.scenes[1:] == template.scenes[1:]
        assert item.storyboard.scenes[0] != template.scenes[0]


def test_adpipe_2_1_campaign_plan_has_unique_jobs_and_total_cost() -> None:
    template = validate_storyboard(
        json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    )
    plan = build_campaign_plan(
        planned_batch(),
        template_storyboard=template,
        operation_prefix="campaign-013",
    )

    assert len(plan.candidates) == 3
    assert len({item.operation_id for item in plan.candidates}) == 3
    assert plan.total_estimated_cost_microusd == 1_800_000
    assert all(item.state == "planned" for item in plan.candidates)


def test_eval_7_1_unavailable_scorecard_dimensions_are_explicit() -> None:
    scorecard = build_candidate_scorecard(
        candidate_id="tab-overload",
        eligible=True,
        measured={"temporal_eligibility": 1.0},
    )

    assert scorecard.dimensions["temporal_eligibility"].value == 1.0
    assert scorecard.dimensions["hypothesis_match"].value is None
    assert scorecard.dimensions["hypothesis_match"].unavailable_reason
