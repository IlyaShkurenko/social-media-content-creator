from __future__ import annotations

import hashlib
import importlib.util
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
