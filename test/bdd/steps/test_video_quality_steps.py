from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from pytest_bdd import given, scenarios, then, when


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_SCRIPT = (
    REPOSITORY_ROOT
    / "feedback-loop"
    / "video-quality"
    / "scripts"
    / "run_experiment.py"
)
EVALUATOR_SCRIPT = (
    REPOSITORY_ROOT
    / "feedback-loop"
    / "video-quality"
    / "evals"
    / "evaluate.py"
)
FINALIZE_CAMPAIGN_SCRIPT = (
    REPOSITORY_ROOT
    / "feedback-loop"
    / "video-quality"
    / "scripts"
    / "finalize_campaign.py"
)
CANDIDATE_JUDGE_SCRIPT = (
    REPOSITORY_ROOT
    / "feedback-loop"
    / "video-quality"
    / "evals"
    / "candidate_judge.py"
)
INVARIANT_JUDGE_SCRIPT = (
    REPOSITORY_ROOT
    / "feedback-loop"
    / "video-quality"
    / "evals"
    / "invariant_judge.py"
)
EVALUATOR_BASELINE_RECORDER_SCRIPT = (
    REPOSITORY_ROOT
    / "feedback-loop"
    / "video-quality"
    / "scripts"
    / "record_candidate_evaluator_baseline.py"
)


def load_experiment_module() -> ModuleType:
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_run_experiment",
        EXPERIMENT_SCRIPT,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load experiment module: {EXPERIMENT_SCRIPT}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


EXPERIMENT_MODULE = load_experiment_module()


def load_evaluator_module() -> ModuleType:
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_evaluator",
        EVALUATOR_SCRIPT,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load evaluator module: {EVALUATOR_SCRIPT}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


EVALUATOR_MODULE = load_evaluator_module()
scenarios("../features/video_quality_acceptance.feature")


@given(
    "a candidate experiment improves its primary metric",
    target_fixture="experiment_case",
)
def improved_candidate() -> dict[str, Any]:
    return {
        "current": {
            "primary": {"name": "timeline_alignment_f1", "value": 0.8},
            "constraints": {
                "all_enforced_pass": True,
                "all_goal_constraints_verified": False,
            },
            "observation_mode": "automated",
        },
        "previous": {
            "primary": {"name": "timeline_alignment_f1", "value": 0.7}
        },
    }


@given("one or more required goal constraints remain unverified")
def pending_constraints(experiment_case: dict[str, Any]) -> None:
    experiment_case["current"]["constraints"][
        "all_goal_constraints_verified"
    ] = False


@given("all required goal constraints are verified")
def verified_constraints(experiment_case: dict[str, Any]) -> None:
    experiment_case["current"]["constraints"][
        "all_goal_constraints_verified"
    ] = True


@when("the experiment decision is recorded", target_fixture="decision_document")
def record_decision(
    tmp_path: Path,
    experiment_case: dict[str, Any],
) -> str:
    output_dir = tmp_path / "002-bdd-candidate"
    output_dir.mkdir()
    scenario = tmp_path / "scenario.json"
    scenario.write_text("{}", encoding="utf-8")
    EXPERIMENT_MODULE.write_readme(
        output_dir,
        kind="experiment",
        hypothesis="BDD acceptance-boundary fixture",
        scenario=scenario,
        metrics=experiment_case["current"],
        previous=experiment_case["previous"],
    )
    return (output_dir / "README.md").read_text(encoding="utf-8")


@then("the decision is provisional and requires review")
def decision_requires_review(decision_document: str) -> None:
    assert "`provisional_requires_review`" in decision_document
    assert "\n`keep`\n" not in decision_document


@then("the decision keeps the candidate")
def decision_keeps_candidate(decision_document: str) -> None:
    assert "\n`keep`\n" in decision_document


@given(
    "a scene declares a non-product contextual screen",
    target_fixture="screen_policy_case",
)
def non_product_screen_policy_case() -> dict[str, Any]:
    return {
        "storyboard": [
            {
                "id": "hook",
                "screen_content_policy": "non_product_context",
            }
        ],
        "observations": [],
    }


@given("the observed screen is generic and does not claim tict identity")
def observe_generic_screen(screen_policy_case: dict[str, Any]) -> None:
    screen_policy_case["observations"] = [
        {
            "scene_id": "hook",
            "screen_observation": {
                "screen_class": "generic_non_product",
                "evidence_timestamp_seconds": 2.5,
                "claims_tict_identity": False,
            },
        }
    ]


@when("screen-policy compliance is calculated", target_fixture="screen_result")
def calculate_screen_compliance(
    screen_policy_case: dict[str, Any],
) -> dict[str, Any]:
    return EVALUATOR_MODULE.calculate_screen_policy_metrics(
        screen_policy_case["storyboard"],
        screen_policy_case["observations"],
    )


@then("the scene screen policy passes")
def screen_policy_passes(screen_result: dict[str, Any]) -> None:
    assert screen_result["compliance"] == 1.0
    assert screen_result["failures"] == []


@given("a reproduced comparable baseline", target_fixture="baseline_evidence")
def reproduced_baseline() -> dict[str, Any]:
    return {
        "experiment": "experiments/008-baseline",
        "metrics_sha256": "a" * 64,
        "metrics": {
            "scenario_id": "mixed-media-stock-baseline-001",
            "evaluator_version": "0.4.0",
            "primary": {
                "name": "timeline_alignment_f1",
                "value": 0.952381,
            },
        },
    }


@when(
    "a new experiment is started with a problem, hypothesis, change, and expected impact",
    target_fixture="planned_experiment",
)
def start_planned_experiment(baseline_evidence: dict[str, Any]) -> dict[str, Any]:
    return EXPERIMENT_MODULE.build_started_manifest(
        scenario={"path": "evals/dataset/scenario.json", "sha256": "b" * 64},
        baseline=baseline_evidence,
        observed_problem="The product reveal begins too abruptly.",
        hypothesis="A short visual bridge will improve scene alignment.",
        planned_change="Add one local transition before the product capture.",
        expected_metric_impact="Increase timeline_alignment_f1 without regressions.",
        start_revision="c94f01e",
        started_at="2026-08-17T18:00:00+00:00",
    )


@then("the experiment is planned without candidate metrics")
def plan_precedes_metrics(planned_experiment: dict[str, Any]) -> None:
    assert planned_experiment["lifecycle"]["status"] == "planned"
    assert "candidate" not in planned_experiment
    assert "metrics" not in planned_experiment


@then("its engineering hypothesis and baseline evidence are frozen")
def plan_and_baseline_are_frozen(planned_experiment: dict[str, Any]) -> None:
    assert planned_experiment["plan"]["hypothesis"].startswith(
        "A short visual bridge"
    )
    assert planned_experiment["baseline"]["experiment"] == (
        "experiments/008-baseline"
    )
    assert planned_experiment["baseline"]["primary"]["value"] == 0.952381


@given(
    "an evaluated experiment with a hash-matching local video artifact",
    target_fixture="artifact_case",
)
def evaluated_experiment_artifact(tmp_path: Path) -> dict[str, Any]:
    experiment = tmp_path / "010-candidate"
    artifact = experiment / "artifacts" / "video.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"real-candidate-video")
    return {
        "experiment_dir": experiment,
        "manifest": {
            "lifecycle": {"status": "evaluated"},
            "candidate": {
                "video": {
                    "snapshot_path": "artifacts/video.mp4",
                    "snapshot_sha256": hashlib.sha256(
                        artifact.read_bytes()
                    ).hexdigest(),
                }
            },
        },
    }


@when(
    "final artifact retention is verified",
    target_fixture="artifact_retention_result",
)
def verify_artifact_retention(artifact_case: dict[str, Any]) -> bool:
    return EXPERIMENT_MODULE.verify_retained_artifact(
        artifact_case["experiment_dir"],
        artifact_case["manifest"],
    )


@then("the experiment artifact is accepted as retained")
def retained_artifact_passes(artifact_retention_result: bool) -> None:
    assert artifact_retention_result is True


@given(
    "a baseline and candidate are judged in both A/B orders",
    target_fixture="pairwise_case",
)
def pairwise_orders() -> dict[str, Any]:
    baseline_sha256 = "a" * 64
    candidate_sha256 = "b" * 64
    return {
        "baseline_sha256": baseline_sha256,
        "candidate_sha256": candidate_sha256,
        "passes": [
            {
                "order": {"A": baseline_sha256, "B": candidate_sha256},
                "response": {"winner": "B"},
            },
            {
                "order": {"A": candidate_sha256, "B": baseline_sha256},
                "response": {"winner": "A"},
            },
        ],
    }


@given("the candidate is preferred in both judge passes")
def candidate_preferred(pairwise_case: dict[str, Any]) -> None:
    assert [item["response"]["winner"] for item in pairwise_case["passes"]] == [
        "B",
        "A",
    ]


@when(
    "the order-balanced visual win rate is calculated",
    target_fixture="visual_win_rate",
)
def calculate_order_balanced_win_rate(pairwise_case: dict[str, Any]) -> float:
    return EVALUATOR_MODULE.calculate_visual_judge_win_rate(
        pairwise_case["passes"],
        baseline_sha256=pairwise_case["baseline_sha256"],
        candidate_sha256=pairwise_case["candidate_sha256"],
    )["win_rate"]


@then("the candidate visual win rate is 1.0")
def visual_win_rate_is_one(visual_win_rate: float) -> None:
    assert visual_win_rate == 1.0


@given(
    "a candidate improves its visual judge primary metric",
    target_fixture="comparison_case",
)
def visual_primary_improvement() -> dict[str, Any]:
    return {
        "candidate": {
            "primary": {"name": "visual_judge_win_rate", "value": 1.0},
            "metrics": {"timeline_alignment_f1": 0.9},
            "constraints": {
                "all_enforced_pass": True,
                "all_goal_constraints_verified": True,
            },
        },
        "baseline": {
            "primary": {"name": "visual_judge_win_rate", "value": 0.5},
            "metrics": {"timeline_alignment_f1": 0.95},
        },
    }


@given("its timeline alignment regresses below the comparable baseline")
def timeline_regresses(comparison_case: dict[str, Any]) -> None:
    assert (
        comparison_case["candidate"]["metrics"]["timeline_alignment_f1"]
        < comparison_case["baseline"]["metrics"]["timeline_alignment_f1"]
    )


@when(
    "the comparison-aware experiment decision is calculated",
    target_fixture="comparison_decision",
)
def comparison_decision(comparison_case: dict[str, Any]) -> str:
    return EXPERIMENT_MODULE.recommended_decision(
        comparison_case["candidate"],
        comparison_case["baseline"],
    )


@then("the candidate is rejected for a constraint regression")
def rejected_for_constraint_regression(comparison_decision: str) -> None:
    assert comparison_decision == "reject_constraint_regression"


@given(
    "temporal evidence contains a high-severity screen visibility contradiction",
    target_fixture="temporal_evidence",
)
def high_severity_temporal_event() -> dict[str, Any]:
    return {
        "status": "complete",
        "events": [
            {
                "event_type": "screen_visibility_contradiction",
                "severity": "high",
                "start_seconds": 0.2,
                "end_seconds": 0.5,
                "frame_indices": [2, 3, 4, 5],
                "affected_object": "phone",
                "reason": "The display appears before the phone turns toward camera.",
            }
        ],
    }


@when("temporal consistency is calculated", target_fixture="temporal_result")
def calculate_temporal_consistency(
    temporal_evidence: dict[str, Any],
) -> dict[str, Any]:
    return EVALUATOR_MODULE.calculate_temporal_consistency(temporal_evidence)


@then("temporal screening rejects the generated hook")
def temporal_screening_rejects(temporal_result: dict[str, Any]) -> None:
    assert temporal_result["high_severity_event_count"] == 1
    assert temporal_result["temporal_consistency_pass"] is False


@given(
    "a candidate has lower model preference but passes every enforced constraint",
    target_fixture="reviewed_comparison_case",
)
def lower_preference_constrained_candidate() -> dict[str, Any]:
    return {
        "candidate": {
            "primary": {"name": "visual_judge_win_rate", "value": 0.25},
            "metrics": {"timeline_alignment_f1": 1.0},
            "constraints": {
                "all_enforced_pass": True,
                "all_goal_constraints_verified": False,
            },
        },
        "baseline": {
            "primary": {"name": "visual_judge_win_rate", "value": 0.5},
            "metrics": {"timeline_alignment_f1": 1.0},
        },
    }


@given(
    "a candidate has lower model preference and a failed enforced constraint",
    target_fixture="reviewed_comparison_case",
)
def lower_preference_failed_candidate() -> dict[str, Any]:
    case = lower_preference_constrained_candidate()
    case["candidate"]["constraints"]["all_enforced_pass"] = False
    return case


@given("the product owner explicitly accepts the retained final video")
def product_owner_accepts(reviewed_comparison_case: dict[str, Any]) -> None:
    reviewed_comparison_case["review"] = {
        "outcome": "accept",
        "reviewer": "user",
        "artifact_sha256": "a" * 64,
        "reason": "Candidate 05 is acceptable as the production reference.",
    }


@when(
    "the reviewed final decision is calculated",
    target_fixture="reviewed_decision_result",
)
def calculate_reviewed_decision(
    reviewed_comparison_case: dict[str, Any],
) -> dict[str, str]:
    try:
        decision = EXPERIMENT_MODULE.resolve_final_decision(
            requested="keep",
            metrics=reviewed_comparison_case["candidate"],
            baseline_metrics=reviewed_comparison_case["baseline"],
            human_reviewed=True,
            human_review_outcome=reviewed_comparison_case["review"]["outcome"],
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"decision": decision}


@then("the candidate is kept after human review")
def kept_after_human_review(reviewed_decision_result: dict[str, str]) -> None:
    assert reviewed_decision_result == {"decision": "kept_after_human_review"}


@then("the reviewed keep is rejected for a constraint regression")
def reviewed_keep_rejects_regression(
    reviewed_decision_result: dict[str, str],
) -> None:
    assert "constraint regression" in reviewed_decision_result["error"]


@given(
    "an eligible rendered candidate without semantic judge evidence",
    target_fixture="rendered_candidate_records",
)
def rendered_candidate_without_semantic_evidence() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": "map-fragmentation",
            "eligible": True,
            "final_video_sha256": "c" * 64,
            "subtitle_safe_area_pass": True,
        }
    ]


@when("its campaign scorecard is finalized", target_fixture="campaign_scorecard")
def finalize_rendered_candidate_scorecard(
    rendered_candidate_records: list[dict[str, Any]],
) -> dict[str, Any]:
    scripts_dir = str(FINALIZE_CAMPAIGN_SCRIPT.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_finalize_campaign",
        FINALIZE_CAMPAIGN_SCRIPT,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load campaign finalizer: {FINALIZE_CAMPAIGN_SCRIPT}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module._build_scorecards(rendered_candidate_records)[0]


@then("only temporal eligibility has a measured score")
def only_temporal_eligibility_is_measured(campaign_scorecard: dict[str, Any]) -> None:
    assert campaign_scorecard["dimensions"]["temporal_eligibility"] == {
        "value": 1.0,
        "unavailable_reason": None,
    }


@then("audiovisual brand and CTA semantic scores remain unavailable")
def rendered_semantics_are_unavailable(campaign_scorecard: dict[str, Any]) -> None:
    for name in ("audiovisual_correctness", "product_brand_fidelity", "cta_clarity"):
        dimension = campaign_scorecard["dimensions"][name]
        assert dimension["value"] is None
        assert dimension["unavailable_reason"]


@given(
    "a map-fragmentation candidate with its own hypothesis and storyboard",
    target_fixture="candidate_semantic_contract",
)
def map_fragmentation_contract() -> dict[str, Any]:
    return {
        "concept": {
            "concept_id": "map-fragmentation",
            "hypothesis": "Scattered map pins make fragmented planning instantly visible.",
            "audience_problem": "Travel ideas are scattered across disconnected places.",
            "target_emotion": "frustration turning into relief",
            "emotional_arc": "Visible clutter resolves into one clear plan.",
            "hook_setting": "A table covered in maps, notes, and saved-place pins.",
            "hook_camera": "A controlled overhead push toward the scattered map.",
            "hook_voiceover": "Saved everything, but planned nothing?",
            "hook_voice_delivery": "A tired, matter-of-fact person stating the obvious.",
            "hook_beats": [
                {
                    "start_seconds": 0.0,
                    "end_seconds": 2.0,
                    "visible_action": "Hands shuffle notes and pins across a city map.",
                    "expected_evidence": ["map_visible", "scattered_pins_visible"],
                },
                {
                    "start_seconds": 2.0,
                    "end_seconds": 5.0,
                    "visible_action": "The clutter clears toward one centered plan.",
                    "expected_evidence": ["clutter_clears", "single_plan_focus"],
                },
            ],
            "product_bridge": "The scattered pins resolve into one exact tict trip plan.",
            "quality_criteria": ["Map fragmentation is clear without audio."],
        },
        "storyboard": {
            "storyboard_id": "map-fragmentation",
            "scenes": [
                {
                    "scene_id": "hook",
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "purpose": "hook",
                    "visual_intent": {
                        "subject_action": "A traveller corrals scattered map pins and notes."
                    },
                    "expected_evidence": ["map_visible", "scattered_pins_visible"],
                },
                {
                    "scene_id": "product_demo",
                    "start_seconds": 5.0,
                    "end_seconds": 11.0,
                    "purpose": "product_demo",
                    "visual_intent": {
                        "subject_action": "The exact tict trip plan appears."
                    },
                    "expected_evidence": ["approved_tict_ui_visible"],
                },
            ],
        },
    }


@when(
    "its candidate-specific judge prompt is built",
    target_fixture="candidate_judge_prompt",
)
def build_candidate_specific_prompt(candidate_semantic_contract: dict[str, Any]) -> str:
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_candidate_judge",
        CANDIDATE_JUDGE_SCRIPT,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load candidate judge: {CANDIDATE_JUDGE_SCRIPT}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module.build_candidate_judge_prompt(**candidate_semantic_contract)


@then("the prompt requires the map-fragmentation contract")
def prompt_uses_candidate_contract(candidate_judge_prompt: str) -> None:
    prompt = candidate_judge_prompt.lower()
    assert "scattered map pins" in prompt
    assert "map_visible" in prompt


@then("the prompt does not require the unrelated airport hook")
def prompt_excludes_unrelated_airport(candidate_judge_prompt: str) -> None:
    assert "airport" not in candidate_judge_prompt.lower()


@given(
    "a shared downstream contract and an unrelated airport hook",
    target_fixture="shared_invariant_scenario",
)
def shared_downstream_contract() -> dict[str, Any]:
    return {
        "id": "shared-downstream-invariants",
        "purpose": "AIRPORT-HOOK-SENTINEL",
        "hypothesis": "AIRPORT-HOOK-SENTINEL",
        "expected": {
            "aspect_ratio": "9:16",
            "duration_seconds": {"min": 14.9, "max": 15.1},
            "audio_required": True,
            "brand_assets_required": True,
            "storyboard": [
                {
                    "id": "hook",
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                    "description": (
                        "AIRPORT-HOOK-SENTINEL: a traveller runs through an airport."
                    ),
                    "expected_tags": ["airport_hook_only"],
                },
                {
                    "id": "product_demo",
                    "start_seconds": 5.0,
                    "end_seconds": 11.0,
                    "description": (
                        "The exact approved tict product demonstration remains legible."
                    ),
                    "expected_tags": ["approved_tict_ui_visible"],
                },
                {
                    "id": "cta",
                    "start_seconds": 11.0,
                    "end_seconds": 15.0,
                    "description": (
                        "One exact Create your trip CTA remains readable."
                    ),
                    "expected_tags": ["single_cta_visible"],
                },
            ],
        },
    }


@when(
    "its shared-invariant judge prompt is built",
    target_fixture="shared_invariant_prompt",
)
def build_shared_invariant_prompt(shared_invariant_scenario: dict[str, Any]) -> str:
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_invariant_judge",
        INVARIANT_JUDGE_SCRIPT,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load invariant judge: {INVARIANT_JUDGE_SCRIPT}")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module.build_invariant_judge_prompt(shared_invariant_scenario)


@then("the prompt requires the exact product demonstration and CTA")
def prompt_requires_shared_downstream_contract(shared_invariant_prompt: str) -> None:
    prompt = shared_invariant_prompt.lower()
    assert "exact approved tict product demonstration" in prompt
    assert "create your trip" in prompt
    assert "approved_tict_ui_visible" in prompt
    assert "single_cta_visible" in prompt


@then("the prompt excludes airport hook requirements")
def prompt_excludes_hook_contract(shared_invariant_prompt: str) -> None:
    prompt = shared_invariant_prompt.lower()
    assert "airport-hook-sentinel" not in prompt
    assert "airport_hook_only" not in prompt


@given(
    "candidate evidence from a superseded semantic evaluator version",
    target_fixture="superseded_candidate_evidence",
)
def superseded_semantic_evidence() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evaluator_version": "1.0.0",
        "observation_mode": "gemini_candidate_contract_v1",
        "status": "complete",
    }


@when(
    "a new evaluator baseline validates that evidence",
    target_fixture="baseline_validation_result",
)
def validate_superseded_baseline_evidence(
    superseded_candidate_evidence: dict[str, Any],
) -> dict[str, str]:
    scripts_dir = str(EVALUATOR_BASELINE_RECORDER_SCRIPT.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    module_spec = importlib.util.spec_from_file_location(
        "video_quality_evaluator_baseline_recorder",
        EVALUATOR_BASELINE_RECORDER_SCRIPT,
    )
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(
            "cannot load evaluator baseline recorder: "
            f"{EVALUATOR_BASELINE_RECORDER_SCRIPT}"
        )
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    assert (
        superseded_candidate_evidence["evaluator_version"]
        != module.CANDIDATE_EVALUATOR_VERSION
    )
    try:
        module._validate_candidate_evidence(
            superseded_candidate_evidence,
            plan={"candidates": []},
            candidate_sha256="b" * 64,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"result": "accepted"}


@then("baseline establishment is rejected as version-incompatible")
def baseline_rejects_superseded_evidence(
    baseline_validation_result: dict[str, str],
) -> None:
    assert "unsupported evaluator_version" in baseline_validation_result["error"]
