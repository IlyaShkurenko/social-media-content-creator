from __future__ import annotations

import json
from pathlib import Path

import pytest
from google.genai import _transformers

from app.services.creative.campaign import (
    CampaignBrief,
    CampaignPlanningError,
    _request_sha256,
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


def execution_plan():
    template = validate_storyboard(
        json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    )
    return build_campaign_plan(
        planned_batch(),
        template_storyboard=template,
        operation_prefix="campaign-013-execution",
    )


class FakeCampaignAdapter:
    def __init__(self, ledger: IterationBudgetLedger) -> None:
        self.budget_ledger = ledger
        self.submit_calls: list[str] = []
        self.wait_calls: list[str] = []
        self.download_calls: list[str] = []

    def submit(self, request, *, operation_id):
        self.submit_calls.append(operation_id)
        self.budget_ledger.reserve(
            operation_id,
            600_000,
            "fake Runway generation",
        )
        self.budget_ledger.mark_submitted(
            operation_id,
            provider_job_id=operation_id,
        )
        return RunwayJob(
            provider_job_id=operation_id,
            operation_id=operation_id,
            request=request,
            estimated_cost_microusd=600_000,
        )

    def wait(self, job):
        self.wait_calls.append(job.operation_id)
        return RunwayJob(
            provider_job_id=job.provider_job_id,
            operation_id=job.operation_id,
            request=job.request,
            estimated_cost_microusd=job.estimated_cost_microusd,
            status="SUCCEEDED",
            output_urls=("https://example.com/video.mp4",),
        )

    def download_outputs(self, job, output_dir):
        self.download_calls.append(job.operation_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        video = output_dir / f"{job.provider_job_id}.mp4"
        video.write_bytes(job.operation_id.encode("utf-8"))
        return [video]


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


def test_eval_7_1_score_dimension_requires_exactly_one_evidence_state() -> None:
    from app.services.creative.campaign import ScoreDimension

    with pytest.raises(ValueError, match="requires a reason"):
        ScoreDimension()
    with pytest.raises(ValueError, match="cannot be unavailable"):
        ScoreDimension(value=1.0, unavailable_reason="not measured")

    assert ScoreDimension(value=0.5).unavailable_reason is None
    unavailable = ScoreDimension(unavailable_reason="  judge did not run  ")
    assert unavailable.value is None
    assert unavailable.unavailable_reason == "judge did not run"


def test_adpipe_2_3_execution_persists_independent_screened_candidates(
    tmp_path: Path,
) -> None:
    plan = execution_plan()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )

    result = execute_candidate_pool(
        plan,
        adapter=FakeCampaignAdapter(ledger),
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


def test_adpipe_2_3_complete_rerun_reuses_terminal_records_without_provider_calls(
    tmp_path: Path,
) -> None:
    plan = execution_plan()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )
    output_dir = tmp_path / "campaign"
    first_adapter = FakeCampaignAdapter(ledger)
    first = execute_candidate_pool(
        plan,
        adapter=first_adapter,
        output_dir=output_dir,
        screen_hook=lambda *_: {"temporal_consistency_pass": True},
    )
    screening_calls = 0

    def unexpected_screen(*_):
        nonlocal screening_calls
        screening_calls += 1
        raise AssertionError("terminal candidates must not be screened again")

    rerun_adapter = FakeCampaignAdapter(ledger)
    rerun = execute_candidate_pool(
        plan,
        adapter=rerun_adapter,
        output_dir=output_dir,
        screen_hook=unexpected_screen,
    )

    assert rerun == first
    assert rerun_adapter.submit_calls == []
    assert rerun_adapter.wait_calls == []
    assert rerun_adapter.download_calls == []
    assert screening_calls == 0


@pytest.mark.parametrize("state_form", ["submitted", "submitting", "missing"])
def test_adpipe_2_3_submitted_jobs_resume_polling_without_resubmission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_form: str,
) -> None:
    plan = execution_plan()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )
    output_dir = tmp_path / "campaign"
    output_dir.mkdir()
    (output_dir / "campaign-plan.json").write_text(
        plan.model_dump_json(),
        encoding="utf-8",
    )
    for candidate in plan.candidates:
        provider_job_id = f"provider-{candidate.candidate_id}"
        ledger.reserve(
            candidate.operation_id,
            candidate.estimated_cost_microusd,
            "fake Runway generation",
        )
        ledger.mark_submitted(
            candidate.operation_id,
            provider_job_id=provider_job_id,
        )
        if state_form != "missing":
            state = {
                "candidate_id": candidate.candidate_id,
                "concept_id": candidate.concept_id,
                "operation_id": candidate.operation_id,
                "state": state_form,
                "estimated_cost_microusd": candidate.estimated_cost_microusd,
                "request_sha256": _request_sha256(candidate.request),
            }
            if state_form == "submitted":
                state["provider_job_id"] = provider_job_id
            (output_dir / f"{candidate.candidate_id}.state.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )

    availability_checks: list[int] = []
    original_ensure_available = ledger.ensure_available

    def record_availability_check(amount_microusd: int):
        availability_checks.append(amount_microusd)
        return original_ensure_available(amount_microusd)

    monkeypatch.setattr(ledger, "ensure_available", record_availability_check)
    adapter = FakeCampaignAdapter(ledger)
    result = execute_candidate_pool(
        plan,
        adapter=adapter,
        output_dir=output_dir,
        screen_hook=lambda *_: {"temporal_consistency_pass": True},
    )

    assert adapter.submit_calls == []
    assert adapter.wait_calls == [
        candidate.operation_id for candidate in plan.candidates
    ]
    assert result["eligible_candidate_ids"] == [
        candidate.candidate_id for candidate in plan.candidates
    ]
    assert [
        record["provider_job_id"] for record in result["candidates"]
    ] == [f"provider-{candidate.candidate_id}" for candidate in plan.candidates]
    assert availability_checks == []


def test_adpipe_2_3_batch_availability_covers_only_new_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = execution_plan()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )
    output_dir = tmp_path / "campaign"
    output_dir.mkdir()
    (output_dir / "campaign-plan.json").write_text(
        plan.model_dump_json(),
        encoding="utf-8",
    )
    for candidate in plan.candidates[:2]:
        provider_job_id = f"provider-{candidate.candidate_id}"
        ledger.reserve(
            candidate.operation_id,
            candidate.estimated_cost_microusd,
            "fake Runway generation",
        )
        ledger.mark_submitted(
            candidate.operation_id,
            provider_job_id=provider_job_id,
        )
        state = {
            "candidate_id": candidate.candidate_id,
            "concept_id": candidate.concept_id,
            "operation_id": candidate.operation_id,
            "state": "submitted",
            "estimated_cost_microusd": candidate.estimated_cost_microusd,
            "request_sha256": _request_sha256(candidate.request),
            "provider_job_id": provider_job_id,
        }
        (output_dir / f"{candidate.candidate_id}.state.json").write_text(
            json.dumps(state),
            encoding="utf-8",
        )

    availability_checks: list[int] = []
    original_ensure_available = ledger.ensure_available

    def record_availability_check(amount_microusd: int):
        availability_checks.append(amount_microusd)
        return original_ensure_available(amount_microusd)

    monkeypatch.setattr(ledger, "ensure_available", record_availability_check)
    adapter = FakeCampaignAdapter(ledger)
    execute_candidate_pool(
        plan,
        adapter=adapter,
        output_dir=output_dir,
        screen_hook=lambda *_: {"temporal_consistency_pass": True},
    )

    new_candidate = plan.candidates[2]
    assert availability_checks == [new_candidate.estimated_cost_microusd]
    assert adapter.submit_calls == [new_candidate.operation_id]
    assert adapter.wait_calls == [
        candidate.operation_id for candidate in plan.candidates
    ]


@pytest.mark.parametrize("operation_status", ["reserved", "released", "manual_charge"])
def test_adpipe_2_3_ambiguous_ledger_state_blocks_the_whole_batch(
    tmp_path: Path,
    operation_status: str,
) -> None:
    plan = execution_plan()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )
    first = plan.candidates[0]
    if operation_status == "manual_charge":
        ledger.record_manual_charge(
            first.operation_id,
            first.estimated_cost_microusd,
            "ambiguous Runway generation",
        )
    else:
        ledger.reserve(
            first.operation_id,
            first.estimated_cost_microusd,
            "ambiguous Runway generation",
        )
        if operation_status == "released":
            ledger.release(first.operation_id, reason="definite rejection")
    adapter = FakeCampaignAdapter(ledger)

    output_dir = tmp_path / "campaign"
    output_dir.mkdir()
    plan_path = output_dir / "campaign-plan.json"
    plan_path.write_text(
        plan.model_dump_json(),
        encoding="utf-8",
    )
    plan_bytes = plan_path.read_bytes()
    with pytest.raises(CampaignPlanningError, match="ambiguous ledger state"):
        execute_candidate_pool(
            plan,
            adapter=adapter,
            output_dir=output_dir,
            screen_hook=lambda *_: {"temporal_consistency_pass": True},
        )

    assert adapter.submit_calls == []
    assert adapter.wait_calls == []
    assert [path.name for path in output_dir.iterdir()] == ["campaign-plan.json"]
    assert plan_path.read_bytes() == plan_bytes


@pytest.mark.parametrize("mutation", ["request", "video"])
def test_adpipe_2_3_stale_or_hash_mismatched_terminal_state_blocks_rerun(
    tmp_path: Path,
    mutation: str,
) -> None:
    plan = execution_plan()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="campaign-013",
        cap_microusd=10_000_000,
    )
    output_dir = tmp_path / "campaign"
    execute_candidate_pool(
        plan,
        adapter=FakeCampaignAdapter(ledger),
        output_dir=output_dir,
        screen_hook=lambda *_: {"temporal_consistency_pass": True},
    )
    first = plan.candidates[0]
    state_path = output_dir / f"{first.candidate_id}.state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if mutation == "request":
        state["request_sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        expected_error = "request_sha256"
    else:
        Path(state["video_path"]).write_bytes(b"tampered")
        expected_error = "video hash"
    adapter = FakeCampaignAdapter(ledger)

    with pytest.raises(CampaignPlanningError, match=expected_error):
        execute_candidate_pool(
            plan,
            adapter=adapter,
            output_dir=output_dir,
            screen_hook=lambda *_: {"temporal_consistency_pass": True},
        )

    assert adapter.submit_calls == []
    assert adapter.wait_calls == []
