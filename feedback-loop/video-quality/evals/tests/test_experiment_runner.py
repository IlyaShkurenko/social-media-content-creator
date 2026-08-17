from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


LOOP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOOP_ROOT / "scripts"))
import run_experiment
import replay_experiment


class ComparableBaselineTests(unittest.TestCase):
    def test_latest_prior_ignores_a_different_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "001-first"
            second = root / "002-second"
            current = root / "003-current"
            for path in (first, second, current):
                path.mkdir()
            (first / "metrics.json").write_text(
                json.dumps(
                    {
                        "scenario_id": "target",
                        "evaluator_version": "0.2.0",
                        "primary": {"value": 0.5},
                    }
                )
            )
            (second / "metrics.json").write_text(
                json.dumps(
                    {
                        "scenario_id": "unrelated",
                        "evaluator_version": "0.2.0",
                        "primary": {"value": 0.9},
                    }
                )
            )
            with mock.patch.object(run_experiment, "EXPERIMENTS_ROOT", root):
                previous = run_experiment.latest_comparable_metrics(
                    current,
                    scenario_id="target",
                    evaluator_version="0.2.0",
                )
            self.assertEqual(previous["primary"]["value"], 0.5)


class CompactExperimentRecordTests(unittest.TestCase):
    def test_start_phase_allocates_plan_only_after_baseline_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory) / "repo"
            loop_root = repo_root / "feedback-loop" / "video-quality"
            experiments_root = loop_root / "experiments"
            scenario = loop_root / "evals" / "dataset" / "scenario.json"
            baseline = experiments_root / "008-baseline"
            scenario.parent.mkdir(parents=True)
            baseline.mkdir(parents=True)
            scenario.write_text(
                json.dumps({"id": "scenario-001"}) + "\n",
                encoding="utf-8",
            )
            (baseline / "metrics.json").write_text(
                json.dumps(
                    {
                        "scenario_id": "scenario-001",
                        "evaluator_version": "0.4.0",
                        "primary": {
                            "name": "timeline_alignment_f1",
                            "value": 0.8,
                        },
                        "constraints": {
                            "all_goal_constraints_verified": False,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                slug="one-change",
                scenario="evals/dataset/scenario.json",
                baseline="experiments/008-baseline",
                problem="The hook is weak.",
                hypothesis="One prompt change improves alignment.",
                planned_change="Change only the hook prompt.",
                expected_impact="Increase timeline_alignment_f1.",
            )
            replay_result = SimpleNamespace(returncode=0, stdout="verified", stderr="")
            with (
                mock.patch.object(run_experiment, "REPO_ROOT", repo_root),
                mock.patch.object(run_experiment, "LOOP_ROOT", loop_root),
                mock.patch.object(
                    run_experiment,
                    "EXPERIMENTS_ROOT",
                    experiments_root,
                ),
                mock.patch.object(
                    run_experiment,
                    "_git_output",
                    side_effect=("", "abc1234"),
                ),
                mock.patch.object(
                    run_experiment.subprocess,
                    "run",
                    return_value=replay_result,
                ) as replay,
            ):
                result = run_experiment.start_experiment(args)

            self.assertEqual(result, 0)
            replay.assert_called_once()
            created = experiments_root / "009-one-change"
            manifest = json.loads(
                (created / "inputs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["lifecycle"]["status"], "planned")
            self.assertNotIn("candidate", manifest)
            self.assertFalse((created / "metrics.json").exists())

    def test_started_manifest_freezes_plan_before_candidate_inputs(self) -> None:
        manifest = run_experiment.build_started_manifest(
            scenario={"path": "evals/dataset/scenario.json", "sha256": "a" * 64},
            baseline={
                "experiment": "experiments/008-baseline",
                "metrics_sha256": "b" * 64,
                "metrics": {
                    "scenario_id": "scenario-001",
                    "evaluator_version": "0.4.0",
                    "primary": {
                        "name": "timeline_alignment_f1",
                        "value": 0.8,
                    },
                },
            },
            observed_problem="The hook lacks motion.",
            hypothesis="More motion improves alignment.",
            planned_change="Change only the hook prompt.",
            expected_metric_impact="Increase timeline_alignment_f1.",
            start_revision="abc1234",
            started_at="2026-08-17T18:00:00+00:00",
        )

        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["lifecycle"]["status"], "planned")
        self.assertEqual(manifest["plan"]["hypothesis"], "More motion improves alignment.")
        self.assertNotIn("candidate", manifest)

    def test_provisional_keep_requires_explicit_human_review(self) -> None:
        metrics = {
            "primary": {"name": "timeline_alignment_f1", "value": 0.9},
            "constraints": {"all_goal_constraints_verified": False},
        }
        baseline = {"primary": {"name": "timeline_alignment_f1", "value": 0.8}}

        with self.assertRaisesRegex(ValueError, "human review"):
            run_experiment.resolve_final_decision(
                requested="keep",
                metrics=metrics,
                baseline_metrics=baseline,
                human_reviewed=False,
            )

        self.assertEqual(
            run_experiment.resolve_final_decision(
                requested="keep",
                metrics=metrics,
                baseline_metrics=baseline,
                human_reviewed=True,
            ),
            "kept_after_human_review",
        )

    def test_keep_is_rejected_without_primary_improvement(self) -> None:
        metrics = {
            "primary": {"name": "timeline_alignment_f1", "value": 0.8},
            "constraints": {"all_goal_constraints_verified": True},
        }
        baseline = {"primary": {"name": "timeline_alignment_f1", "value": 0.8}}

        with self.assertRaisesRegex(ValueError, "primary metric did not improve"):
            run_experiment.resolve_final_decision(
                requested="keep",
                metrics=metrics,
                baseline_metrics=baseline,
                human_reviewed=True,
            )

    def test_sanitizer_redacts_credentials_and_normalizes_repo_paths(self) -> None:
        record = {
            "api_key": "runway-secret",
            "authorization": "Bearer secret",
            "output_url": "https://signed.example/video.mp4",
            "rendered_video": str(run_experiment.REPO_ROOT / "storage" / "video.mp4"),
            "nested": {"safe": "kept"},
        }

        sanitized = run_experiment._sanitize_record(record)

        self.assertEqual(sanitized["api_key"], "[redacted]")
        self.assertEqual(sanitized["authorization"], "[redacted]")
        self.assertEqual(sanitized["output_url"], "[redacted]")
        self.assertEqual(sanitized["rendered_video"], "<repo>/storage/video.mp4")
        self.assertEqual(sanitized["nested"]["safe"], "kept")

    def test_replay_rejects_a_changed_shared_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "scenario.json"
            fixture.write_text('{"version": 1}\n', encoding="utf-8")
            entry = {
                "path": fixture.name,
                "sha256": replay_experiment.sha256_file(fixture),
            }

            fixture.write_text('{"version": 2}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "replay input hash changed"):
                replay_experiment.verified_path(root, entry)


if __name__ == "__main__":
    unittest.main()
