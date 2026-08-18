from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.genai import _transformers

from app.services.creative.campaign import (
    CampaignBrief,
    CampaignPlanningError,
    build_campaign_plan,
    build_candidate_scorecard,
    build_hypothesis_prompt,
    compile_concept_storyboards,
    execute_candidate_pool,
    plan_hypotheses,
)
from app.services.creative.budget import IterationBudgetLedger
from app.services.creative.runway import RunwayJob
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
    assert 'schema_version to exactly "1.0"' in prompt
    assert "never use underscores" in prompt


def test_story_2_1_hypothesis_schema_is_supported_by_gemini_sdk() -> None:
    from app.services.creative.campaign import HypothesisBatchResponse

    schema = _transformers.t_schema(None, HypothesisBatchResponse)
    payload = schema.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert payload
    assert "additionalProperties" not in json.dumps(payload)


def test_story_2_1_gemini_prompt_does_not_duplicate_transport_schema() -> None:
    prompt = build_hypothesis_prompt(campaign_brief(), include_json_schema=False)

    assert "meaningfully distinct" in prompt
    assert '"properties"' not in prompt


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


def test_story_2_3_product_reveal_is_kept_out_of_generated_hook() -> None:
    payload = response_payload()
    payload["concepts"][0]["hook_beats"][-1]["visible_action"] = (
        "The traveller opens tict and reveals the product interface."
    )
    batch = plan_hypotheses(
        campaign_brief(),
        response_generator=lambda _: json.dumps(payload),
    )
    template = validate_storyboard(
        json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    )

    compiled = compile_concept_storyboards(
        batch,
        template_storyboard=template,
    )[0]

    assert "reveals the product interface" not in (
        compiled.storyboard.scenes[0].visual_intent.subject_action
    )
    assert "device screen stays hidden" in (
        compiled.storyboard.scenes[0].visual_intent.subject_action
    )


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


def test_adpipe_2_3_execution_persists_independent_screened_candidates(
    tmp_path: Path,
) -> None:
    template = validate_storyboard(
        json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    )
    plan = build_campaign_plan(
        planned_batch(),
        template_storyboard=template,
        operation_prefix="campaign-013-execution",
    )
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )

    class FakeAdapter:
        budget_ledger = ledger

        def submit(self, request, *, operation_id):
            ledger.reserve(operation_id, 600_000, "fake Runway generation")
            ledger.mark_submitted(operation_id, provider_job_id=operation_id)
            return RunwayJob(
                provider_job_id=operation_id,
                operation_id=operation_id,
                request=request,
                estimated_cost_microusd=600_000,
            )

        def wait(self, job):
            return RunwayJob(
                provider_job_id=job.provider_job_id,
                operation_id=job.operation_id,
                request=job.request,
                estimated_cost_microusd=job.estimated_cost_microusd,
                status="SUCCEEDED",
                output_urls=("https://example.com/video.mp4",),
            )

        def download_outputs(self, job, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            video = output_dir / f"{job.provider_job_id}.mp4"
            video.write_bytes(job.operation_id.encode("utf-8"))
            return [video]

    result = execute_candidate_pool(
        plan,
        adapter=FakeAdapter(),
        output_dir=tmp_path / "campaign",
        screen_hook=lambda *_: {"temporal_consistency_pass": True},
    )

    assert result["eligible_candidate_ids"] == [
        "tab-overload",
        "missed-detail",
        "decision-fatigue",
    ]
    assert result["automatic_selection"] is None
    assert (tmp_path / "campaign" / "campaign-plan.json").is_file()
    assert (tmp_path / "campaign" / "candidate-pool.json").is_file()
    assert all(item["video_sha256"] for item in result["candidates"])
