from __future__ import annotations

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
