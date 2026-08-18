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
import run_experiment  # noqa: E402
import replay_experiment  # noqa: E402
import record_candidate_evaluator_baseline as evaluator_baseline  # noqa: E402


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
            "constraints": {
                "all_enforced_pass": True,
                "all_goal_constraints_verified": False,
            },
        }
        baseline = {
            "primary": {"name": "timeline_alignment_f1", "value": 0.8},
            "metrics": {},
        }

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
                human_review_outcome="accept",
            ),
            "kept_after_human_review",
        )

    def test_human_acceptance_can_keep_without_model_preference_improvement(self) -> None:
        metrics = {
            "primary": {"name": "timeline_alignment_f1", "value": 0.8},
            "metrics": {"timeline_alignment_f1": 1.0},
            "constraints": {
                "all_enforced_pass": True,
                "all_goal_constraints_verified": False,
            },
        }
        baseline = {
            "primary": {"name": "timeline_alignment_f1", "value": 0.8},
            "metrics": {"timeline_alignment_f1": 1.0},
        }

        self.assertEqual(
            run_experiment.resolve_final_decision(
                requested="keep",
                metrics=metrics,
                baseline_metrics=baseline,
                human_reviewed=True,
                human_review_outcome="accept",
            ),
            "kept_after_human_review",
        )

    def test_human_acceptance_cannot_override_enforced_failure(self) -> None:
        metrics = {
            "primary": {"name": "visual_judge_win_rate", "value": 0.25},
            "metrics": {"timeline_alignment_f1": 1.0},
            "constraints": {
                "all_enforced_pass": False,
                "all_goal_constraints_verified": False,
            },
        }
        baseline = {
            "primary": {"name": "visual_judge_win_rate", "value": 0.5},
            "metrics": {"timeline_alignment_f1": 1.0},
        }

        with self.assertRaisesRegex(ValueError, "constraint regression"):
            run_experiment.resolve_final_decision(
                requested="keep",
                metrics=metrics,
                baseline_metrics=baseline,
                human_reviewed=True,
                human_review_outcome="accept",
            )

    def test_human_review_record_is_bound_to_retained_artifact(self) -> None:
        manifest = {
            "candidate": {
                "video": {
                    "snapshot_path": "artifacts/video.mp4",
                    "snapshot_sha256": "d" * 64,
                }
            }
        }

        record = run_experiment.build_human_review_record(
            manifest,
            outcome="accept",
            reviewer="user",
            reason="Candidate 05 is acceptable as the production reference.",
            reviewed_at="2026-08-18T10:00:00+00:00",
        )

        self.assertEqual(record["outcome"], "accept")
        self.assertEqual(record["artifact_sha256"], "d" * 64)
        self.assertEqual(record["reviewer"], "user")
        self.assertIn("Candidate 05", record["reason"])

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


class CandidateEvaluatorBaselineRecordTests(unittest.TestCase):
    @staticmethod
    def _document(content: dict) -> dict:
        return {
            "source_path": "ignored/source.json",
            "source_sha256": "a" * 64,
            "content_sha256": evaluator_baseline.sha256_json(content),
            "content": content,
        }

    def test_verify_only_dispatch_is_offline_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            loop_root = (Path(directory) / "video-quality").resolve()
            experiment = loop_root / "experiments" / "014-evaluator-baseline"
            experiment.mkdir(parents=True)
            manifest = {
                "kind": "candidate_evaluator_baseline",
            }
            metrics = {
                "decision": "evaluator_baseline_established",
                "acceptance_authority": False,
            }
            (experiment / "inputs.json").write_text(json.dumps(manifest))
            (experiment / "metrics.json").write_text(json.dumps(metrics))

            with (
                mock.patch.object(replay_experiment, "LOOP_ROOT", loop_root),
                mock.patch.object(
                    replay_experiment,
                    "parse_args",
                    return_value=SimpleNamespace(
                        experiment=str(experiment),
                        verify_only=True,
                    ),
                ),
                mock.patch.object(
                    replay_experiment,
                    "replay_candidate_evaluator_baseline",
                    return_value=metrics,
                ) as rebuild,
                mock.patch.object(replay_experiment.subprocess, "run") as network_path,
            ):
                result = replay_experiment.main()

            self.assertEqual(result, 0)
            rebuild.assert_called_once()
            network_path.assert_not_called()

    def test_embedded_evidence_tampering_is_rejected(self) -> None:
        entry = self._document({"status": "complete"})
        entry["content"]["status"] = "partial"

        with self.assertRaisesRegex(ValueError, "embedded content hash changed"):
            evaluator_baseline._validated_document(entry, label="candidate evidence")

    def test_evidence_sanitizer_preserves_ordinary_relative_strings(self) -> None:
        absolute = str(Path(evaluator_baseline.__file__).resolve())
        payload = {
            "status": "complete",
            "evaluator_version": "1.1.0",
            "sha256": "a" * 64,
            "relative_artifact": "artifacts/video.mp4",
            "absolute_artifact": absolute,
        }

        sanitized = evaluator_baseline._sanitize(payload)

        self.assertEqual(sanitized["status"], "complete")
        self.assertEqual(sanitized["evaluator_version"], "1.1.0")
        self.assertEqual(sanitized["sha256"], "a" * 64)
        self.assertEqual(sanitized["relative_artifact"], "artifacts/video.mp4")
        self.assertTrue(sanitized["absolute_artifact"].startswith("<repo>/"))

    def test_new_baseline_rejects_superseded_candidate_evaluator(self) -> None:
        payload = {
            "schema_version": evaluator_baseline.CANDIDATE_EVIDENCE_SCHEMA_VERSION,
            "evaluator_version": "1.0.0",
            "observation_mode": evaluator_baseline.CANDIDATE_OBSERVATION_MODE,
            "status": "complete",
        }

        with self.assertRaisesRegex(ValueError, "unsupported evaluator_version"):
            evaluator_baseline._validate_candidate_evidence(
                payload,
                plan={"candidates": []},
                candidate_sha256="b" * 64,
            )

    def test_invalid_evidence_leaves_no_poisoned_experiment_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = (Path(directory) / "repo").resolve()
            loop_root = repo_root / "feedback-loop" / "video-quality"
            experiments_root = loop_root / "experiments"
            inputs = repo_root / "inputs"
            inputs.mkdir(parents=True)
            scenario = inputs / "scenario.json"
            plan = inputs / "campaign-plan.json"
            candidate_evidence = inputs / "candidate-evidence.json"
            invariant_evidence = inputs / "invariant-evidence.json"
            reference = inputs / "reference.mp4"
            candidate = inputs / "candidate.mp4"
            scenario.write_text(json.dumps({"id": "scenario"}))
            plan.write_text(json.dumps({"candidates": []}))
            candidate_evidence.write_text(json.dumps({"status": "stale"}))
            invariant_evidence.write_text(json.dumps({"status": "complete"}))
            reference.write_bytes(b"reference")
            candidate.write_bytes(b"candidate")
            args = SimpleNamespace(
                experiment="014-evaluator-baseline",
                scenario=str(scenario),
                campaign_plan=str(plan),
                reference_video=str(reference),
                candidate_video=str(candidate),
                candidate_evidence=str(candidate_evidence),
                invariant_evidence=str(invariant_evidence),
                reference_label="accept",
                candidate_label="pending",
                reviewer="product-owner",
                reference_reason="Accepted exact reference MP4.",
                candidate_reason="Exact candidate MP4 review is pending.",
            )
            target = experiments_root / args.experiment

            with (
                mock.patch.object(evaluator_baseline, "REPO_ROOT", repo_root),
                mock.patch.object(evaluator_baseline, "LOOP_ROOT", loop_root),
                mock.patch.object(
                    evaluator_baseline,
                    "EXPERIMENTS_ROOT",
                    experiments_root,
                ),
                self.assertRaisesRegex(ValueError, "candidate evidence"),
            ):
                evaluator_baseline.record_baseline(args)

            self.assertFalse(target.exists())
            self.assertEqual(list(experiments_root.glob(".*.staging-*")), [])

    def test_real_record_and_offline_replay_use_exact_evaluator_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = (Path(directory) / "repo").resolve()
            loop_root = repo_root / "feedback-loop" / "video-quality"
            experiments_root = loop_root / "experiments"
            inputs = repo_root / "inputs"
            inputs.mkdir(parents=True)
            concept = {
                "concept_id": "map-overload",
                "hypothesis": "Map clutter makes one clear plan feel valuable.",
                "audience_problem": "Saved places are fragmented.",
                "target_emotion": "overwhelm",
                "emotional_arc": "confusion to relief",
                "hook_setting": "a table covered in maps",
                "hook_camera": "top-down push-in",
                "hook_voiceover": "Saved everything, planned nothing?",
                "hook_beats": [
                    {
                        "start_seconds": 0.0,
                        "end_seconds": 2.0,
                        "visible_action": "Hands shuffle map notes.",
                        "expected_evidence": ["map notes move"],
                    }
                ],
                "product_bridge": "Cut to the exact product plan.",
                "quality_criteria": ["The problem is visible without audio."],
            }
            storyboard = {
                "schema_version": "1.2",
                "storyboard_id": "map-overload-storyboard",
                "content_language": "en-US",
                "aspect_ratio": "9:16",
                "target_duration_seconds": 15.0,
                "hypothesis": concept["hypothesis"],
                "scenes": [
                    {
                        "scene_id": "hook",
                        "start_seconds": 0.0,
                        "end_seconds": 5.0,
                        "purpose": "hook",
                        "visual_intent": "Hands visibly reorganize map notes.",
                        "voiceover": concept["hook_voiceover"],
                        "onscreen_text": "Too many saved places?",
                        "expected_evidence": ["moving map notes"],
                    },
                    {
                        "scene_id": "product_demo",
                        "start_seconds": 5.0,
                        "end_seconds": 11.0,
                        "purpose": "product_demo",
                        "visual_intent": "The exact product screen remains legible.",
                        "voiceover": "tict turns saved places into one plan.",
                        "onscreen_text": "One clear trip.",
                        "expected_evidence": ["approved product UI"],
                    },
                    {
                        "scene_id": "cta",
                        "start_seconds": 11.0,
                        "end_seconds": 15.0,
                        "purpose": "cta",
                        "visual_intent": "One CTA remains visible.",
                        "voiceover": "Plan less with tict.",
                        "onscreen_text": "Create your trip",
                        "expected_evidence": ["single CTA"],
                    },
                ],
            }
            scenario_payload = {
                "id": "shared-downstream",
                "expected": {
                    "aspect_ratio": "9:16",
                    "duration_seconds": {"min": 14.9, "max": 15.1},
                    "audio_required": True,
                    "brand_assets_required": True,
                    "storyboard": storyboard["scenes"],
                },
            }
            plan_payload = {
                "schema_version": "1.0",
                "operation_prefix": "campaign-test",
                "candidates": [
                    {
                        "candidate_id": "map-overload",
                        "concept_id": "map-overload",
                        "concept": concept,
                        "storyboard": storyboard,
                    }
                ],
            }
            reference = inputs / "reference.mp4"
            candidate = inputs / "candidate.mp4"
            reference.write_bytes(b"exact-reference-mp4")
            candidate.write_bytes(b"exact-candidate-mp4")
            reference_sha = evaluator_baseline.sha256_file(reference)
            candidate_sha = evaluator_baseline.sha256_file(candidate)

            def assessment(timestamp_ms: int) -> dict:
                return {
                    "status": "met",
                    "evidence": [
                        {
                            "timestamp_ms": timestamp_ms,
                            "observation": "The declared action is visible.",
                        }
                    ],
                    "reason": "Timestamped evidence supports the verdict.",
                }

            candidate_response = {
                "observed_hook_summary": "Hands reorganize notes on a map.",
                "observed_bridge_summary": "The map cuts to the product plan.",
                "hypothesis_match": assessment(1000),
                "target_emotion_strength": assessment(1200),
                "first_two_seconds_hook_clarity": assessment(1500),
                "hook_to_product_bridge_coherence": assessment(5100),
                "storyboard_action_alignment": assessment(1800),
            }
            candidate_evidence_payload = {
                "schema_version": evaluator_baseline.CANDIDATE_EVIDENCE_SCHEMA_VERSION,
                "evaluator_version": evaluator_baseline.CANDIDATE_EVALUATOR_VERSION,
                "observation_mode": evaluator_baseline.CANDIDATE_OBSERVATION_MODE,
                "status": "complete",
                "concept_id": "map-overload",
                "storyboard_id": storyboard["storyboard_id"],
                "concept_sha256": evaluator_baseline.candidate_sha256_json(concept),
                "storyboard_sha256": evaluator_baseline.candidate_sha256_json(
                    storyboard
                ),
                "contract_sha256": evaluator_baseline.candidate_sha256_json(
                    evaluator_baseline._candidate_contract(concept, storyboard)
                ),
                "video_sha256": candidate_sha,
                "video_duration_ms": 15000,
                "prompt_sha256": "1" * 64,
                "response_schema_sha256": "2" * 64,
                "requested_model": "gemini-test",
                "model_version": "gemini-test",
                "response": candidate_response,
                "dimension_scores": evaluator_baseline.map_dimension_statuses(
                    candidate_response
                ),
                "provider": {"response_id": "candidate-response"},
                "budget": {
                    "operation_id": "candidate-judge",
                    "preflight_maximum_microusd": 100,
                    "charged_microusd": 80,
                },
            }

            def invariant_assessment(scene: str, timestamp_ms: int) -> dict:
                return {
                    "status": "met",
                    "evidence": [
                        {
                            "scene_purpose": scene,
                            "timestamp_ms": timestamp_ms,
                            "observation": f"Visible {scene} evidence.",
                        }
                    ],
                    "reason": "The downstream evidence is visible.",
                }

            paired = {
                name: {
                    "video_a": invariant_assessment(scene, timestamp),
                    "video_b": invariant_assessment(scene, timestamp),
                }
                for name, scene, timestamp in (
                    ("transition_mechanics", "product_demo", 6000),
                    ("audiovisual_correctness", "product_demo", 7000),
                    ("product_brand_fidelity", "product_demo", 8000),
                    ("cta_clarity", "cta", 12000),
                    ("professional_finish", "cta", 13000),
                )
            }
            invariant_response = {
                "winner": "tie",
                "winner_reason": "Both downstream edits show equal evidence.",
                "criteria": paired,
                "scene_evidence": [
                    {
                        "video_label": label,
                        "scene_purpose": scene,
                        "timestamp_ms": timestamp,
                        "observation": f"Video {label} shows {scene}.",
                    }
                    for label in ("A", "B")
                    for scene, timestamp in (
                        ("product_demo", 8000),
                        ("cta", 13000),
                    )
                ],
            }
            invariant_scores = evaluator_baseline.map_invariant_statuses(
                invariant_response
            )
            passes = []
            for index, (pass_id, order) in enumerate(
                (
                    (
                        "baseline-a",
                        {"A": reference_sha, "B": candidate_sha},
                    ),
                    (
                        "candidate-a",
                        {"A": candidate_sha, "B": reference_sha},
                    ),
                ),
                start=1,
            ):
                passes.append(
                    {
                        "pass_id": pass_id,
                        "order": order,
                        "response": invariant_response,
                        "criterion_scores": invariant_scores,
                        "provider": {
                            "requested_model": "gemini-test",
                            "model_version": "gemini-test",
                            "response_id": f"invariant-response-{index}",
                        },
                        "budget": {
                            "operation_id": f"invariant-pass-{index}",
                            "preflight_maximum_microusd": 100,
                            "charged_microusd": 70,
                        },
                    }
                )

            scenario = inputs / "scenario.json"
            plan = inputs / "campaign-plan.json"
            candidate_evidence = inputs / "candidate-evidence.json"
            invariant_evidence = inputs / "invariant-evidence.json"
            scenario.write_text(json.dumps(scenario_payload))
            plan.write_text(json.dumps(plan_payload))
            candidate_evidence.write_text(json.dumps(candidate_evidence_payload))
            invariant_payload = {
                "schema_version": evaluator_baseline.INVARIANT_EVIDENCE_SCHEMA_VERSION,
                "evaluator_version": evaluator_baseline.INVARIANT_EVALUATOR_VERSION,
                "observation_mode": evaluator_baseline.INVARIANT_OBSERVATION_MODE,
                "status": "complete",
                "scenario_id": scenario_payload["id"],
                "scenario_sha256": evaluator_baseline.sha256_file(scenario),
                "shared_contract_sha256": evaluator_baseline.invariant_sha256_json(
                    evaluator_baseline.build_shared_invariant_contract(
                        scenario_payload
                    )
                ),
                "prompt_sha256": "3" * 64,
                "response_schema_sha256": "4" * 64,
                "requested_model": "gemini-test",
                "model_versions": ["gemini-test"],
                "baseline_sha256": reference_sha,
                "candidate_sha256": candidate_sha,
                "self_comparison": False,
                "passes": passes,
                "winner_diagnostic": evaluator_baseline.build_winner_diagnostic(
                    passes,
                    baseline_sha256=reference_sha,
                    candidate_sha256=candidate_sha,
                ),
            }
            invariant_evidence.write_text(json.dumps(invariant_payload))
            args = SimpleNamespace(
                experiment="014-evaluator-baseline",
                scenario=str(scenario),
                campaign_plan=str(plan),
                reference_video=str(reference),
                candidate_video=str(candidate),
                candidate_evidence=str(candidate_evidence),
                invariant_evidence=str(invariant_evidence),
                reference_label="accept",
                candidate_label="pending",
                reviewer="product-owner",
                reference_reason="Accepted exact reference MP4.",
                candidate_reason="Exact candidate MP4 review is pending.",
            )
            target = experiments_root / args.experiment

            with (
                mock.patch.object(evaluator_baseline, "REPO_ROOT", repo_root),
                mock.patch.object(evaluator_baseline, "LOOP_ROOT", loop_root),
                mock.patch.object(
                    evaluator_baseline,
                    "EXPERIMENTS_ROOT",
                    experiments_root,
                ),
            ):
                recorded = evaluator_baseline.record_baseline(args)

            self.assertEqual(recorded, target)
            with (
                mock.patch.object(replay_experiment, "LOOP_ROOT", loop_root),
                mock.patch.object(
                    replay_experiment,
                    "parse_args",
                    return_value=SimpleNamespace(
                        experiment=str(target),
                        verify_only=True,
                    ),
                ),
                mock.patch.object(replay_experiment.subprocess, "run") as provider_path,
            ):
                result = replay_experiment.main()

            self.assertEqual(result, 0)
            provider_path.assert_not_called()
            metrics = json.loads((target / "metrics.json").read_text())
            self.assertEqual(metrics["decision"], "evaluator_baseline_established")
            self.assertIsNone(
                metrics["scorecard"]["dimensions"]["human_acceptance"]["value"]
            )
            readme = (target / "README.md").read_text(encoding="utf-8")
            self.assertIn("Lifecycle: `baseline_established`", readme)
            self.assertIn("Decision: `evaluator_baseline_established`", readme)
            self.assertIn("Acceptance authority: `false`", readme)
            self.assertIn(
                f"Candidate semantic evaluator: `{evaluator_baseline.CANDIDATE_EVALUATOR_VERSION}`",
                readme,
            )
            self.assertIn(
                f"Shared invariant evaluator: `{evaluator_baseline.INVARIANT_EVALUATOR_VERSION}`",
                readme,
            )
            self.assertIn("Label: `accept`", readme)
            self.assertIn("Label: `pending`", readme)
            self.assertIn("Human-acceptance value: `null`", readme)
            self.assertIn("Product improvement assessed: `false`", readme)
            self.assertIn("Keep decision made: `false`", readme)

    def test_artifact_tampering_is_rejected_before_evidence_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            experiment = Path(directory)
            artifacts = experiment / "artifacts"
            artifacts.mkdir()
            reference = artifacts / "reference.mp4"
            candidate = artifacts / "video.mp4"
            reference.write_bytes(b"reference")
            candidate.write_bytes(b"candidate")
            manifest = {
                "schema_version": 1,
                "kind": "candidate_evaluator_baseline",
                "lifecycle": {
                    "status": "baseline_established",
                    "decision": "evaluator_baseline_established",
                    "acceptance_authority": False,
                },
                "artifacts": {
                    "reference": {
                        "source_path": "storage/reference.mp4",
                        "snapshot_path": "artifacts/reference.mp4",
                        "source_sha256": "0" * 64,
                        "snapshot_sha256": "0" * 64,
                    },
                    "candidate": {
                        "source_path": "storage/candidate.mp4",
                        "snapshot_path": "artifacts/video.mp4",
                        "source_sha256": evaluator_baseline.sha256_file(candidate),
                        "snapshot_sha256": evaluator_baseline.sha256_file(candidate),
                    },
                },
            }

            with self.assertRaisesRegex(ValueError, "artifact source_sha256 changed"):
                evaluator_baseline.rebuild_candidate_evaluator_baseline_metrics(
                    manifest,
                    experiment=experiment,
                )

    def test_pending_owner_label_remains_unmeasured_without_improvement_claims(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            experiment = Path(directory)
            artifacts = experiment / "artifacts"
            artifacts.mkdir()
            reference = artifacts / "reference.mp4"
            candidate = artifacts / "video.mp4"
            reference.write_bytes(b"reference")
            candidate.write_bytes(b"candidate")
            reference_sha = evaluator_baseline.sha256_file(reference)
            candidate_sha = evaluator_baseline.sha256_file(candidate)
            manifest = {
                "schema_version": 1,
                "kind": "candidate_evaluator_baseline",
                "lifecycle": {
                    "status": "baseline_established",
                    "decision": "evaluator_baseline_established",
                    "acceptance_authority": False,
                },
                "scenario": self._document({"id": "shared"}),
                "campaign_plan": self._document({"candidates": []}),
                "artifacts": {
                    "reference": {
                        "source_path": "storage/reference.mp4",
                        "snapshot_path": "artifacts/reference.mp4",
                        "source_sha256": reference_sha,
                        "snapshot_sha256": reference_sha,
                    },
                    "candidate": {
                        "source_path": "storage/candidate.mp4",
                        "snapshot_path": "artifacts/video.mp4",
                        "source_sha256": candidate_sha,
                        "snapshot_sha256": candidate_sha,
                    },
                },
                "evidence": {
                    "candidate": self._document(
                        {
                            "evaluator_version": evaluator_baseline.CANDIDATE_EVALUATOR_VERSION,
                            "video_duration_ms": 15000,
                        }
                    ),
                    "invariant": self._document(
                        {
                            "evaluator_version": "1.0.0",
                            "status": "complete",
                        }
                    ),
                },
                "product_owner_labels": [
                    {
                        "role": "reference",
                        "label": "accept",
                        "artifact_sha256": reference_sha,
                        "reviewer": "product-owner",
                        "reason": "Accepted reference.",
                    },
                    {
                        "role": "candidate",
                        "label": "pending",
                        "artifact_sha256": candidate_sha,
                        "reviewer": "product-owner",
                        "reason": "Exact MP4 review is still pending.",
                    },
                ],
            }
            semantic = {
                "hypothesis_match": 1.0,
                "target_emotion_strength": 0.5,
                "first_two_seconds_hook_clarity": 1.0,
                "hook_to_product_bridge_coherence": 0.5,
                "storyboard_action_alignment": 1.0,
            }
            invariant = {
                "transition_mechanics": 0.5,
                "audiovisual_correctness": 1.0,
                "product_brand_fidelity": 1.0,
                "cta_clarity": 1.0,
                "professional_finish": 0.5,
            }
            candidate_cost = {
                "operation_id": "candidate-judge",
                "charged_microusd": 100,
                "preflight_maximum_microusd": 120,
            }
            invariant_costs = [
                {
                    "operation_id": "invariant-1",
                    "charged_microusd": 200,
                    "preflight_maximum_microusd": 220,
                },
                {
                    "operation_id": "invariant-2",
                    "charged_microusd": 210,
                    "preflight_maximum_microusd": 230,
                },
            ]
            diagnostic = {"diagnostic_only": True, "outcome": "tie"}
            with (
                mock.patch.object(
                    evaluator_baseline,
                    "_validate_candidate_evidence",
                    return_value=("candidate-05", semantic, candidate_cost),
                ),
                mock.patch.object(
                    evaluator_baseline,
                    "_validate_invariant_evidence",
                    return_value=(invariant, diagnostic, invariant_costs),
                ),
            ):
                metrics = (
                    evaluator_baseline.rebuild_candidate_evaluator_baseline_metrics(
                        manifest,
                        experiment=experiment,
                    )
                )

            self.assertEqual(metrics["decision"], "evaluator_baseline_established")
            self.assertFalse(metrics["acceptance_authority"])
            self.assertFalse(metrics["claims"]["product_improvement"])
            self.assertFalse(metrics["claims"]["keep_decision"])
            self.assertIsNone(
                metrics["scorecard"]["dimensions"]["human_acceptance"]["value"]
            )
            self.assertEqual(
                metrics["scorecard"]["dimensions"]["human_acceptance"][
                    "unavailable_reason"
                ],
                "exact MP4 product-owner review is pending",
            )
            self.assertEqual(
                metrics["evaluator_costs"]["total_charged_microusd"],
                510,
            )


if __name__ == "__main__":
    unittest.main()
