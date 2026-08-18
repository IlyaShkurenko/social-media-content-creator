from __future__ import annotations

import json
import socket
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.creative.budget import BudgetSnapshot, IterationBudgetLedger
from app.services.creative.campaign import (
    CampaignBrief,
    build_campaign_plan,
    plan_hypotheses,
)
from app.services.creative.campaign_preflight import (
    PLANNER_OPERATION_SUFFIX,
    CampaignPreflightError,
    TemporalPreflightContract,
    build_campaign_preflight,
    build_gemini_concept_transport,
    require_matching_preflight,
)
from app.services.creative.runway import RunwayAdapter, build_runway_payload
from app.services.creative.storyboard import validate_storyboard


REPO_ROOT = Path(__file__).resolve().parents[2]
LOOP_ROOT = REPO_ROOT / "feedback-loop/video-quality"
BRIEF_PATH = LOOP_ROOT / "evals/dataset/tict-campaign-brief-001.json"
STORYBOARD_PATH = (
    LOOP_ROOT / "evals/dataset/mixed-media-first-slice-001.json"
)
ASSET_ROOT = LOOP_ROOT / "evals/assets/brand"
sys.path.insert(0, str(LOOP_ROOT / "scripts"))
import run_campaign  # noqa: E402
from run_campaign import (  # noqa: E402
    PLANNER_MAXIMUM_COST_MICROUSD,
    BudgetedGeminiConceptPlanner,
    _load_or_plan_concepts,
    _read_budget_snapshot,
    build_hypothesis_prompt_for_record,
)
from evals.gemini_judge import sha256_text  # noqa: E402


def _brief() -> CampaignBrief:
    return CampaignBrief.model_validate(json.loads(BRIEF_PATH.read_text()))


def _storyboard():
    return validate_storyboard(json.loads(STORYBOARD_PATH.read_text()))


def _concept(
    concept_id: str,
    *,
    hypothesis: str,
    opening_action: str,
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
                "visible_action": opening_action,
                "expected_evidence": ["planning_stress_visible"],
            },
            {
                "start_seconds": 2.0,
                "end_seconds": 5.0,
                "visible_action": "The traveller exhales and lowers the phone.",
                "expected_evidence": ["traveller_visible", "phone_visible"],
            },
        ],
        "product_bridge": "Match the motion into the exact tict plan.",
        "quality_criteria": ["The problem is readable without audio."],
    }


def _batch():
    payload = {
        "schema_version": "1.0",
        "product_name": "tict",
        "content_language": "en-US",
        "concepts": [
            _concept(
                "tab-overload",
                hypothesis="Tab overload makes fragmented planning immediate.",
                opening_action="The traveller rapidly switches planning tabs.",
                emotion="overwhelm",
            ),
            _concept(
                "missed-detail",
                hypothesis="A missed detail creates relatable anxiety.",
                opening_action="The traveller notices a conflicting booking time.",
                emotion="anxiety",
            ),
            _concept(
                "decision-fatigue",
                hypothesis="Decision fatigue makes one plan feel valuable.",
                opening_action="The traveller compares notes and closes her eyes.",
                emotion="frustration",
            ),
        ],
    }
    return plan_hypotheses(
        _brief(),
        response_generator=lambda _: json.dumps(payload),
    )


def _snapshot(*, remaining: int = 10_000_000) -> BudgetSnapshot:
    return BudgetSnapshot(
        scope_id="mixed-media-iteration-001",
        cap_microusd=10_000_000,
        reserved_microusd=0,
        charged_microusd=10_000_000 - remaining,
        remaining_microusd=remaining,
    )


def _preflight(**overrides):
    values = {
        "brief": _brief(),
        "template_storyboard": _storyboard(),
        "asset_root": ASSET_ROOT,
        "operation_prefix": "campaign-014",
        "gemini_model": "gemini-3.6-flash",
        "planning_mode": "live",
        "budget_snapshot": _snapshot(),
        "planner_maximum_cost_microusd": 100_000,
        "temporal_maximum_cost_microusd": 50_000,
        "runway_base_url": RunwayAdapter.BASE_URL,
        "runway_api_version": RunwayAdapter.API_VERSION,
        "temporal_contract": TemporalPreflightContract(
            evaluator_version="0.6.0",
            evidence_schema_version=1,
            response_schema_sha256="d" * 64,
            implementation_sha256="e" * 64,
            sample_fps=10,
            frames_per_strip=5,
            max_output_tokens=4096,
            event_types=[
                "object_disappearance",
                "orientation_discontinuity",
            ],
            model="gemini-3.6-flash",
            scene_id="hook",
            start_seconds=0.0,
            end_seconds=5.0,
        ),
        "concepts": None,
        "orchestrator_sha256": "c" * 64,
    }
    values.update(overrides)
    return build_campaign_preflight(**values)


def test_adpipe_2_6_planner_preflight_accounts_for_complete_campaign() -> None:
    report = _preflight()

    assert report.stage == "planner_ready"
    assert report.budget["planner_maximum_microusd"] == 100_000
    assert report.budget["runway_maximum_microusd"] == 1_800_000
    assert report.budget["temporal_maximum_microusd"] == 150_000
    assert report.budget["campaign_maximum_microusd"] == 2_050_000
    assert report.budget["required_remaining_microusd"] == 2_050_000
    assert report.budget["check_type"] == "point_in_time_non_reserving"


def test_adpipe_2_6_generation_preflight_compiles_every_exact_payload() -> None:
    batch = _batch()
    report = _preflight(concepts=batch)
    plan = build_campaign_plan(
        batch,
        template_storyboard=_storyboard(),
        operation_prefix="campaign-014",
    )

    assert report.stage == "generation_ready"
    assert report.checks["exact_concepts"] == "passed"
    assert report.budget["campaign_maximum_microusd"] == 2_050_000
    assert report.budget["required_remaining_microusd"] == 1_950_000
    records = report.semantic_manifest["candidate_requests"]
    assert len(records) == 3
    assert all(record["prompt_chars"] <= 1000 for record in records)
    assert all(record["payload_sha256"] for record in records)
    assert all(
        len(build_runway_payload(candidate.request)["promptText"]) <= 1000
        for candidate in plan.candidates
    )
    assert report.semantic_manifest["runway_provider"] == {
        "base_url": RunwayAdapter.BASE_URL,
        "api_version": RunwayAdapter.API_VERSION,
    }
    assert report.semantic_manifest["temporal_evaluator"]["evaluator_version"] == (
        "0.6.0"
    )
    assert report.semantic_manifest["implementation_sha256"]["temporal_judge"] == (
        "e" * 64
    )


def test_adpipe_2_6_replay_preflight_has_no_planner_cost() -> None:
    report = _preflight(
        concepts=_batch(),
        planning_mode="replay",
    )

    assert report.stage == "generation_ready"
    assert report.budget["planner_maximum_microusd"] == 0
    assert report.budget["campaign_maximum_microusd"] == 1_950_000
    assert report.budget["required_remaining_microusd"] == 1_950_000


def test_adpipe_2_6_gemini_transport_uses_supported_exact_schema() -> None:
    transport = build_gemini_concept_transport(
        model="gemini-3.6-flash",
        prompt="Return JSON.",
    )
    schema = transport["generationConfig"]["responseSchema"]
    serialized = json.dumps(schema)

    assert schema["properties"]["schema_version"] == {
        "enum": ["1.0"],
        "title": "Schema Version",
        "type": "STRING",
    }
    for unsupported in (
        "$defs",
        "$ref",
        "additionalProperties",
        "additional_properties",
        "const",
        "exclusiveMinimum",
    ):
        assert unsupported not in serialized


def test_adpipe_2_6_preflight_is_offline_and_does_not_mutate_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "budget.sqlite3"
    ledger = IterationBudgetLedger(
        database,
        scope_id="mixed-media-iteration-001",
        cap_microusd=10_000_000,
    )

    def reject_network(*_args, **_kwargs):
        raise AssertionError("offline preflight attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    before = database.read_bytes()
    report = _preflight(budget_snapshot=ledger.snapshot())
    after = database.read_bytes()
    with sqlite3.connect(database) as connection:
        operations = connection.execute(
            "SELECT COUNT(*) FROM budget_operations"
        ).fetchone()[0]

    assert report.status == "passed"
    assert before == after
    assert operations == 0


def test_adpipe_2_6_absent_ledger_is_zero_spend_and_remains_absent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ignored" / "budget.sqlite3"

    snapshot = _read_budget_snapshot(database)

    assert snapshot.cap_microusd == 10_000_000
    assert snapshot.reserved_microusd == 0
    assert snapshot.charged_microusd == 0
    assert snapshot.remaining_microusd == 10_000_000
    assert not database.exists()
    assert not database.parent.exists()


def test_adpipe_2_6_clean_checkout_uses_provider_defaults_without_config_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clean_root = tmp_path / "clean-checkout"
    monkeypatch.setattr(run_campaign, "REPO_ROOT", clean_root)

    provider = run_campaign._configured_runway_contract()

    assert provider == (RunwayAdapter.BASE_URL, RunwayAdapter.API_VERSION)
    assert not (clean_root / "config.toml").exists()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"runway_base_url": "http://api.dev.runwayml.com"}, "official HTTPS"),
        ({"runway_base_url": "https://example.com"}, "official HTTPS"),
        ({"runway_api_version": "2099-01-01"}, "API version"),
    ],
)
def test_adpipe_2_6_runway_provider_contract_fails_closed(
    override: dict,
    message: str,
) -> None:
    with pytest.raises(CampaignPreflightError, match=message):
        _preflight(**override)


def test_adpipe_2_6_temporal_config_change_invalidates_hash() -> None:
    stored = _preflight()
    changed_contract = stored.semantic_manifest["temporal_evaluator"] | {
        "sample_fps": 12
    }
    current = _preflight(
        temporal_contract=TemporalPreflightContract.model_validate(changed_contract)
    )

    assert stored.preflight_id != current.preflight_id


def test_adpipe_2_6_semantic_input_change_invalidates_hash() -> None:
    stored = _preflight()
    changed_brief = _brief().model_copy(
        update={"audience": "travellers coordinating a complex group holiday"}
    )
    current = _preflight(brief=changed_brief)

    assert stored.preflight_id != current.preflight_id
    with pytest.raises(CampaignPreflightError, match="stale"):
        require_matching_preflight(
            stored_preflight_id=stored.preflight_id,
            current_preflight_id=current.preflight_id,
        )


def test_adpipe_2_6_matching_hash_passes() -> None:
    report = _preflight()
    require_matching_preflight(
        stored_preflight_id=report.preflight_id,
        current_preflight_id=report.preflight_id,
    )


def test_adpipe_2_6_missing_asset_fails_before_provider_work() -> None:
    changed = _brief().model_copy(
        update={
            "available_asset_ids": [
                *_brief().available_asset_ids,
                "screens/does-not-exist.png",
            ]
        }
    )

    with pytest.raises(CampaignPreflightError, match="does-not-exist"):
        _preflight(brief=changed)


def test_adpipe_2_6_complete_budget_failure_is_non_reserving() -> None:
    with pytest.raises(CampaignPreflightError, match="budget exceeded"):
        _preflight(budget_snapshot=_snapshot(remaining=2_049_999))


class _PlannerModels:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def generate_content(self, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            text=_batch().model_dump_json(),
            parsed=None,
            response_id="planner-response-1",
            model_version="gemini-3.6-flash-001",
            usage_metadata=SimpleNamespace(
                prompt_token_count=1_000,
                candidates_token_count=500,
                thoughts_token_count=0,
                total_token_count=1_500,
            ),
        )


def _planner(tmp_path: Path, *, error: Exception | None = None):
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="mixed-media-iteration-001",
        cap_microusd=10_000_000,
    )
    planner = BudgetedGeminiConceptPlanner(
        api_key="offline-key",
        ledger=ledger,
        operation_id="campaign-planner",
    )
    models = _PlannerModels(error)
    planner.client = SimpleNamespace(models=models)
    return planner, ledger, models


def test_adpipe_2_3_planner_server_error_is_charged_and_not_retried(
    tmp_path: Path,
) -> None:
    from google.genai import errors

    planner, ledger, models = _planner(
        tmp_path,
        error=errors.ServerError(503, {"error": {"message": "high demand"}}),
    )

    with pytest.raises(RuntimeError, match="ambiguous"):
        planner("Return concepts.")
    assert ledger.snapshot().charged_microusd == PLANNER_MAXIMUM_COST_MICROUSD
    with pytest.raises(RuntimeError, match="resubmission is blocked"):
        planner("Return concepts.")
    assert models.calls == 1


def test_adpipe_2_3_planner_checkpoint_replays_without_provider_call(
    tmp_path: Path,
) -> None:
    output = tmp_path / "campaign"
    output.mkdir()
    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="mixed-media-iteration-001",
        cap_microusd=10_000_000,
    )
    operation_id = f"campaign-014-{PLANNER_OPERATION_SUFFIX}"
    ledger.record_manual_charge(operation_id, 25_000, "Completed planner response")
    raw = _batch().model_dump_json()
    prompt = build_hypothesis_prompt_for_record(_brief())
    (output / "concept-planning-raw.json").write_text(raw, encoding="utf-8")
    (output / "concept-planning.json").write_text(
        json.dumps(
            {
                "mode": "generated",
                "operation_id": operation_id,
                "requested_model": "gemini-3.6-flash",
                "prompt_sha256": sha256_text(prompt),
            }
        ),
        encoding="utf-8",
    )
    args = SimpleNamespace(
        concepts=None,
        operation_prefix="campaign-014",
        gemini_model="gemini-3.6-flash",
    )

    replayed = _load_or_plan_concepts(
        args=args,
        brief=_brief(),
        gemini_key="unused",
        ledger=ledger,
        output_dir=output,
    )

    assert replayed == _batch()
    assert ledger.snapshot().charged_microusd == 25_000
