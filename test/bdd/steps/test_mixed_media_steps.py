from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, scenarios, then, when

from app.services.creative.budget import BudgetExceededError, IterationBudgetLedger
from app.services.creative.compiler import compile_comparison_plans
from app.services.creative.narration import build_narration_plan
from app.services.creative.pipeline import build_runway_request
from app.services.creative.storyboard import (
    StoryboardValidationError,
    resolve_narration_voice,
    validate_storyboard,
)


scenarios("../features/mixed_media_pipeline.feature")


@pytest.fixture
def scenario_context() -> dict[str, Any]:
    return {}


def storyboard_payload(*, overlap: bool = False) -> dict[str, Any]:
    demonstration_start = 4.0 if overlap else 5.0
    return {
        "schema_version": "1.0",
        "storyboard_id": "tict-first-slice",
        "content_language": "en-US",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 15.0,
        "hypothesis": "A complete trip plan removes planning stress.",
        "scenes": [
            {
                "scene_id": "hook",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "purpose": "hook",
                "visual_intent": {
                    "setting": "airport departure hall",
                    "subject_action": "a traveller looks overwhelmed by planning",
                    "camera": "controlled handheld push-in",
                },
                "media_plan": {
                    "base": {"kind": "stock", "intent": "overwhelmed traveller"},
                    "overlays": [],
                },
                "voiceover": "Planning a trip should not feel like another job.",
                "onscreen_text": "Too much planning?",
                "expected_evidence": ["traveller_visible", "airport_visible"],
            },
            {
                "scene_id": "product_demo",
                "start_seconds": demonstration_start,
                "end_seconds": 11.0,
                "purpose": "product_demo",
                "visual_intent": {
                    "setting": "airport lounge",
                    "subject_action": "the traveller raises a phone with a trip plan",
                    "camera": "smooth push-in toward the phone",
                },
                "media_plan": {
                    "base": {"kind": "stock", "intent": "traveller holding phone"},
                    "overlays": [
                        {
                            "kind": "product_capture",
                            "asset_id": "screens/trip-plan.png",
                            "placement": "tracked_phone_screen",
                        }
                    ],
                },
                "voiceover": "Your complete trip, ready in one place.",
                "onscreen_text": "One plan. One place.",
                "expected_evidence": ["phone_visible", "trip_plan_screen_visible"],
            },
            {
                "scene_id": "cta",
                "start_seconds": 11.0,
                "end_seconds": 15.0,
                "purpose": "cta",
                "visual_intent": {
                    "setting": "brand end card",
                    "subject_action": "the call to action appears",
                    "camera": "static end card",
                },
                "media_plan": {
                    "base": {"kind": "solid_or_graphic", "intent": "brand end card"},
                    "overlays": [
                        {
                            "kind": "brand_asset",
                            "asset_id": "logo.png",
                            "placement": "center",
                        }
                    ],
                },
                "voiceover": "Start planning your next trip today.",
                "onscreen_text": "Plan less. Travel more.",
                "call_to_action": "Create your trip",
                "expected_evidence": ["logo_visible", "cta_visible"],
            },
        ],
    }


@given(
    "an English advertising storyboard with overlapping scenes",
    target_fixture="storyboard_input",
)
def overlapping_storyboard() -> dict[str, Any]:
    return storyboard_payload(overlap=True)


@given(
    "a valid three-scene English advertising storyboard",
    target_fixture="storyboard_input",
)
def valid_storyboard() -> dict[str, Any]:
    return storyboard_payload()


@when("comparison execution plans are requested")
def request_comparison_plans(
    storyboard_input: dict[str, Any],
    scenario_context: dict[str, Any],
) -> None:
    try:
        storyboard = validate_storyboard(storyboard_input)
        plans = compile_comparison_plans(storyboard)
    except StoryboardValidationError as exc:
        scenario_context["preflight_error"] = exc
        return
    scenario_context["comparison_plans"] = plans


@then("storyboard preflight is rejected")
def preflight_rejected(scenario_context: dict[str, Any]) -> None:
    assert "preflight_error" in scenario_context
    assert "overlap" in str(scenario_context["preflight_error"]).lower()


@given("the first advertising slice has no selected voice")
def no_selected_voice(scenario_context: dict[str, Any]) -> None:
    scenario_context["requested_voice"] = ""


@given("the interface locale is Russian")
def russian_interface_locale(scenario_context: dict[str, Any]) -> None:
    scenario_context["interface_locale"] = "ru-RU"


@when("narration settings are resolved")
def resolve_narration(scenario_context: dict[str, Any]) -> None:
    settings = resolve_narration_voice(
        content_language="en-US",
        requested_voice=scenario_context["requested_voice"],
        interface_locale=scenario_context["interface_locale"],
    )
    scenario_context["narration_settings"] = settings


@then("the content language is en-US")
def content_language_is_english(scenario_context: dict[str, Any]) -> None:
    assert scenario_context["narration_settings"].content_language == "en-US"


@then("the narration voice is compatible with en-US")
def voice_is_english(scenario_context: dict[str, Any]) -> None:
    assert scenario_context["narration_settings"].voice_name.startswith("en-US-")


@then("the baseline hook uses stock media")
def baseline_hook_uses_stock(scenario_context: dict[str, Any]) -> None:
    plans = scenario_context["comparison_plans"]
    assert plans["stock-baseline"].scenes[0].media_plan.base.kind == "stock"


@then("the candidate hook uses Runway generated media")
def candidate_hook_uses_runway(scenario_context: dict[str, Any]) -> None:
    plans = scenario_context["comparison_plans"]
    candidate_base = plans["runway-candidate"].scenes[0].media_plan.base
    assert candidate_base.kind == "generated"
    assert candidate_base.provider == "runway"
    assert candidate_base.model == "gen4.5"


@then("all controlled scene intent remains equivalent")
def controlled_intent_is_equivalent(scenario_context: dict[str, Any]) -> None:
    plans = scenario_context["comparison_plans"]
    baseline = plans["stock-baseline"]
    candidate = plans["runway-candidate"]
    assert baseline.storyboard_fingerprint == candidate.storyboard_fingerprint
    assert baseline.narration_script == candidate.narration_script
    assert baseline.scenes[1:] == candidate.scenes[1:]


@given(
    "a ten dollar iteration budget with nine dollars and eighty cents charged",
    target_fixture="budget_ledger",
)
def nearly_exhausted_budget(tmp_path: Path) -> IterationBudgetLedger:
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="iteration-001",
        cap_microusd=10_000_000,
    )
    ledger.record_manual_charge(
        operation_id="prior-work",
        amount_microusd=9_800_000,
        description="prior feedback-loop work",
    )
    return ledger


@when("a sixty cent generation is reserved")
def reserve_sixty_cents(
    budget_ledger: IterationBudgetLedger,
    scenario_context: dict[str, Any],
) -> None:
    try:
        budget_ledger.reserve(
            operation_id="runway-hook",
            amount_microusd=600_000,
            description="five-second gen4.5 hook",
        )
    except BudgetExceededError as exc:
        scenario_context["budget_error"] = exc


@then("the reservation is rejected before provider submission")
def reservation_is_rejected(
    budget_ledger: IterationBudgetLedger,
    scenario_context: dict[str, Any],
) -> None:
    assert "budget_error" in scenario_context
    snapshot = budget_ledger.snapshot()
    assert snapshot.charged_microusd == 9_800_000
    assert snapshot.reserved_microusd == 0


def storyboard_payload_v11() -> dict[str, Any]:
    payload = storyboard_payload()
    payload["schema_version"] = "1.1"
    payload["brand_pronunciations"] = [
        {
            "canonical": "tict",
            "spoken_alias": "tickt",
            "ipa": "tɪkt",
        }
    ]
    payload["scenes"][0]["visual_intent"]["screen_content_policy"] = (
        "non_product_context"
    )
    payload["scenes"][1]["visual_intent"]["screen_content_policy"] = (
        "approved_product_ui"
    )
    payload["scenes"][2]["visual_intent"]["screen_content_policy"] = (
        "unconstrained"
    )
    payload["scenes"][1]["voiceover"] = (
        "tict turns every booking into one clear trip plan."
    )
    payload["scenes"][2]["voiceover"] = "Plan less. Travel more with tict."
    return payload


@given(
    "a hook storyboard declaring a non-product contextual screen",
    target_fixture="screen_storyboard_input",
)
def non_product_screen_storyboard() -> dict[str, Any]:
    return storyboard_payload_v11()


@when("the Runway hook request is compiled")
def compile_runway_hook_request(
    screen_storyboard_input: dict[str, Any],
    scenario_context: dict[str, Any],
) -> None:
    storyboard = validate_storyboard(screen_storyboard_input)
    scenario_context["runway_request"] = build_runway_request(storyboard)


@then("the generated hook may show a generic phone screen")
def generic_phone_screen_is_allowed(scenario_context: dict[str, Any]) -> None:
    prompt = scenario_context["runway_request"].prompt_text.lower()
    assert "generic non-product phone interface may be visible" in prompt


@then("the generated hook must not claim that screen is tict UI")
def product_identity_is_forbidden(scenario_context: dict[str, Any]) -> None:
    prompt = scenario_context["runway_request"].prompt_text.lower()
    assert "must not resemble tict" in prompt


@given(
    "a storyboard with lowercase tict copy and its tickt pronunciation",
    target_fixture="brand_storyboard_input",
)
def lowercase_brand_storyboard() -> dict[str, Any]:
    return storyboard_payload_v11()


@when("narration settings are resolved from the storyboard")
def resolve_storyboard_narration(
    brand_storyboard_input: dict[str, Any],
    scenario_context: dict[str, Any],
) -> None:
    storyboard = validate_storyboard(brand_storyboard_input)
    scenario_context["narration_plan"] = build_narration_plan(storyboard)


@then("the subtitle narration retains lowercase tict")
def subtitle_retains_canonical_brand(scenario_context: dict[str, Any]) -> None:
    plan = scenario_context["narration_plan"]
    assert "tict" in plan.scenes[1].display_text
    assert "tickt" not in plan.scenes[1].display_text


@then("the synthesis narration uses the tickt pronunciation")
def synthesis_uses_brand_pronunciation(scenario_context: dict[str, Any]) -> None:
    plan = scenario_context["narration_plan"]
    assert "tickt" in plan.scenes[1].spoken_text


@given("three generated hooks with one passing temporal screen")
def temporal_candidate_pool(scenario_context: dict[str, Any]) -> None:
    scenario_context["generated_hooks"] = [
        {"candidate_id": "hook-1", "temporal_consistency_pass": False},
        {"candidate_id": "hook-2", "temporal_consistency_pass": True},
        {"candidate_id": "hook-3", "temporal_consistency_pass": False},
    ]


@given("a generated hook with a reviewed false-positive temporal event")
def reviewed_temporal_false_positive(scenario_context: dict[str, Any]) -> None:
    scenario_context["generated_hooks"] = [
        {
            "candidate_id": "hook-reviewed",
            "temporal_consistency_pass": False,
            "temporal_events": [
                {
                    "event_id": "orientation-001",
                    "severity": "high",
                    "confirmation": {
                        "outcome": "false_positive",
                        "reviewer": "user",
                        "reviewed_at": "2026-08-18T10:00:00+00:00",
                        "reason": "Dense frames show one continuous phone turn.",
                        "evidence_frames": ["F04", "F05"],
                    },
                }
            ],
        }
    ]


@given(
    "a generated hook whose temporal provider is unavailable after frame extraction"
)
def unavailable_temporal_provider(scenario_context: dict[str, Any]) -> None:
    scenario_context["generated_hooks"] = [
        {
            "candidate_id": "hook-provider-unavailable",
            "temporal_consistency_pass": False,
            "sampled_frame_count": 50,
            "temporal_events": [
                {
                    "event_id": "screening-unavailable",
                    "severity": "high",
                    "reason": "The temporal provider returned HTTP 503.",
                }
            ],
        }
    ]


@given("every extracted temporal frame receives a passing artifact review")
def complete_temporal_artifact_review(scenario_context: dict[str, Any]) -> None:
    scenario_context["generated_hooks"][0]["artifact_review"] = {
        "outcome": "pass",
        "reviewer": "artifact-reviewer",
        "reviewed_at": "2026-08-18T12:42:15Z",
        "reason": "All timestamped 10 FPS frames show continuous motion.",
        "reviewed_frame_count": 50,
        "evidence_frames": ["review/hook-10fps-contact.jpg"],
    }


@when("generated hook selection is requested")
def request_temporal_selection(scenario_context: dict[str, Any]) -> None:
    from app.services.creative.temporal import eligible_temporal_candidates

    scenario_context["eligible_hooks"] = eligible_temporal_candidates(
        scenario_context["generated_hooks"]
    )


@then("only the passing generated hook is eligible")
def only_passing_hook_is_eligible(scenario_context: dict[str, Any]) -> None:
    assert [
        item["candidate_id"] for item in scenario_context["eligible_hooks"]
    ] == ["hook-2"]


@then("the reviewed hook is eligible")
def reviewed_hook_is_eligible(scenario_context: dict[str, Any]) -> None:
    assert len(scenario_context["eligible_hooks"]) == 1
    assert scenario_context["eligible_hooks"][0]["candidate_id"] == (
        scenario_context["generated_hooks"][0]["candidate_id"]
    )


def _campaign_concept(
    concept_id: str,
    *,
    hypothesis: str,
    opening_action: str,
    target_emotion: str,
) -> dict[str, Any]:
    return {
        "concept_id": concept_id,
        "hypothesis": hypothesis,
        "audience_problem": "Travel planning is fragmented across too many tools.",
        "target_emotion": target_emotion,
        "emotional_arc": "Immediate tension resolves into relief and control.",
        "hook_setting": "a bright international airport departure hall",
        "hook_camera": "natural handheld push-in",
        "hook_voiceover": "Planning a trip should not feel like another job.",
        "hook_voice_delivery": "A tired, matter-of-fact person stating the obvious.",
        "hook_beats": [
            {
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "visible_action": opening_action,
                "expected_evidence": ["planning_stress_visible"],
            },
            {
                "start_seconds": 2.0,
                "end_seconds": 5.0,
                "visible_action": "The traveller pauses, exhales, and lowers the phone.",
                "expected_evidence": ["traveller_visible", "phone_visible"],
            },
        ],
        "product_bridge": "Match the lowered phone motion into the exact tict plan.",
        "quality_criteria": [
            "The problem is understandable without audio in the first two seconds.",
            "The target emotion is visibly readable.",
        ],
    }


@given("a tict campaign brief requesting three concepts")
def campaign_brief_request(scenario_context: dict[str, Any]) -> None:
    scenario_context["campaign_brief"] = {
        "product_name": "tict",
        "audience": "independent travellers overwhelmed by fragmented planning",
        "product_facts": [
            "tict creates one trip plan from bookings, places, and next steps."
        ],
        "available_asset_ids": [
            "screens/Plan_Overview_Screen.png",
            "tict-logo.png",
            "tict-mascot-1.png",
        ],
        "concept_count": 3,
    }


@given("the planning model returns three distinct timed concepts")
def distinct_campaign_response(scenario_context: dict[str, Any]) -> None:
    concepts = [
        _campaign_concept(
            "tab-overload",
            hypothesis="Visible tab overload makes planning fragmentation immediate.",
            opening_action="The traveller rapidly switches between planning tabs.",
            target_emotion="overwhelm",
        ),
        _campaign_concept(
            "missed-detail",
            hypothesis="A missed booking detail creates relatable travel anxiety.",
            opening_action="The traveller notices a conflicting booking time.",
            target_emotion="anxiety",
        ),
        _campaign_concept(
            "decision-fatigue",
            hypothesis="Visible decision fatigue makes one clear plan feel valuable.",
            opening_action="The traveller compares several notes and closes her eyes.",
            target_emotion="frustration",
        ),
    ]
    scenario_context["campaign_response"] = json.dumps(
        {
            "schema_version": "1.0",
            "product_name": "tict",
            "content_language": "en-US",
            "concepts": concepts,
        }
    )


@given("the planning model returns a concept with overlapping hook beats")
def overlapping_campaign_response(scenario_context: dict[str, Any]) -> None:
    distinct_campaign_response(scenario_context)
    payload = json.loads(scenario_context["campaign_response"])
    payload["concepts"][0]["hook_beats"][1]["start_seconds"] = 1.5
    scenario_context["campaign_response"] = json.dumps(payload)


@when("the campaign hypotheses are planned")
def plan_campaign_hypotheses(scenario_context: dict[str, Any]) -> None:
    from app.services.creative.campaign import (
        CampaignBrief,
        CampaignPlanningError,
        plan_hypotheses,
    )

    try:
        scenario_context["campaign"] = plan_hypotheses(
            CampaignBrief.model_validate(scenario_context["campaign_brief"]),
            response_generator=lambda _: scenario_context["campaign_response"],
        )
    except CampaignPlanningError as exc:
        scenario_context["campaign_error"] = str(exc)


@then("the campaign contains three distinct concepts")
def campaign_has_three_concepts(scenario_context: dict[str, Any]) -> None:
    concepts = scenario_context["campaign"].concepts
    assert len(concepts) == 3
    assert len({concept.concept_id for concept in concepts}) == 3
    assert len({concept.hypothesis for concept in concepts}) == 3


@then("every concept covers the complete five-second hook")
def campaign_covers_hook(scenario_context: dict[str, Any]) -> None:
    for concept in scenario_context["campaign"].concepts:
        assert concept.hook_beats[0].start_seconds == 0.0
        assert concept.hook_beats[-1].end_seconds == 5.0


@then("campaign planning is rejected before paid generation")
def campaign_rejected_before_generation(scenario_context: dict[str, Any]) -> None:
    assert "overlap" in scenario_context["campaign_error"]


@given("three planned Runway hooks exceed the shared remaining budget")
def unaffordable_candidate_pool(
    tmp_path: Path,
    scenario_context: dict[str, Any],
) -> None:
    from app.services.creative.campaign import build_campaign_plan

    campaign_brief_request(scenario_context)
    distinct_campaign_response(scenario_context)
    plan_campaign_hypotheses(scenario_context)
    template = validate_storyboard(storyboard_payload_v11())
    plan = build_campaign_plan(
        scenario_context["campaign"],
        template_storyboard=template,
        operation_prefix="bdd-campaign",
    )
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="bdd-campaign",
        cap_microusd=1_000_000,
    )

    class FakeAdapter:
        def __init__(self) -> None:
            self.budget_ledger = ledger
            self.submit_calls = 0

        def submit(self, request, *, operation_id):
            del request, operation_id
            self.submit_calls += 1
            raise AssertionError("batch preflight must run before submit")

    scenario_context["campaign_plan"] = plan
    scenario_context["campaign_adapter"] = FakeAdapter()
    scenario_context["campaign_output"] = tmp_path / "campaign"


@when("candidate-pool execution is requested")
def execute_unaffordable_pool(scenario_context: dict[str, Any]) -> None:
    from app.services.creative.campaign import execute_candidate_pool

    try:
        execute_candidate_pool(
            scenario_context["campaign_plan"],
            adapter=scenario_context["campaign_adapter"],
            output_dir=scenario_context["campaign_output"],
            screen_hook=lambda *_: {"temporal_consistency_pass": True},
        )
    except BudgetExceededError as exc:
        scenario_context["campaign_error"] = str(exc)


@then("no Runway candidate is submitted")
def no_candidate_submitted(scenario_context: dict[str, Any]) -> None:
    assert "iteration budget exceeded" in scenario_context["campaign_error"]
    assert scenario_context["campaign_adapter"].submit_calls == 0


@given("an offline campaign preflight for the current semantic inputs")
def current_campaign_preflight(scenario_context: dict[str, Any]) -> None:
    scenario_context["stored_preflight_id"] = "a" * 64
    scenario_context["current_preflight_id"] = "a" * 64


@given("the campaign inputs change after preflight")
def mutate_campaign_inputs(scenario_context: dict[str, Any]) -> None:
    scenario_context["current_preflight_id"] = "b" * 64


@when("paid campaign preflight is verified")
def verify_paid_campaign_preflight(scenario_context: dict[str, Any]) -> None:
    from app.services.creative.campaign_preflight import (
        CampaignPreflightError,
        require_matching_preflight,
    )

    try:
        require_matching_preflight(
            stored_preflight_id=scenario_context["stored_preflight_id"],
            current_preflight_id=scenario_context["current_preflight_id"],
        )
    except CampaignPreflightError as exc:
        scenario_context["campaign_preflight_error"] = str(exc)


@then("campaign execution is blocked before provider submission")
def stale_preflight_blocks_execution(scenario_context: dict[str, Any]) -> None:
    assert "stale" in scenario_context["campaign_preflight_error"].lower()
