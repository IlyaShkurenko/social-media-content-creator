from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path

LOOP_ROOT = Path(__file__).resolve().parents[2]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from evals.evaluate import (  # noqa: E402
    aggregate_candidate_observations,
    calculate_visual_judge_win_rate,
    calculate_screen_policy_metrics,
    calculate_tag_metrics,
    exact_brand_text_match,
    parse_srt_text,
    pipeline_record_metrics,
    pipeline_constraint_evidence,
    token_f1,
)


class PairwiseJudgeMetricTests(unittest.TestCase):
    def test_reversed_candidate_wins_produce_full_credit(self) -> None:
        baseline = "a" * 64
        candidate = "b" * 64
        result = calculate_visual_judge_win_rate(
            [
                {
                    "order": {"A": baseline, "B": candidate},
                    "response": {"winner": "B"},
                },
                {
                    "order": {"A": candidate, "B": baseline},
                    "response": {"winner": "A"},
                },
            ],
            baseline_sha256=baseline,
            candidate_sha256=candidate,
        )

        self.assertEqual(result["win_rate"], 1.0)
        self.assertTrue(result["position_balanced"])

    def test_same_position_wins_cancel_as_position_bias(self) -> None:
        baseline = "a" * 64
        candidate = "b" * 64
        result = calculate_visual_judge_win_rate(
            [
                {
                    "order": {"A": baseline, "B": candidate},
                    "response": {"winner": "A"},
                },
                {
                    "order": {"A": candidate, "B": baseline},
                    "response": {"winner": "A"},
                },
            ],
            baseline_sha256=baseline,
            candidate_sha256=candidate,
        )

        self.assertEqual(result["win_rate"], 0.5)

    def test_candidate_observations_use_only_cross_pass_consensus(self) -> None:
        candidate = "b" * 64
        baseline = "a" * 64

        def response(label: str, tags: list[str]) -> dict:
            return {
                "video_label": label,
                "scene_id": "hook",
                "observed_tags": tags,
                "evidence_timestamp_seconds": 2.5,
                "screen_class": "screen_not_visible",
                "claims_tict_identity": False,
                "approved_asset_match": False,
                "brand_asset_fidelity": None,
            }

        result = aggregate_candidate_observations(
            [
                {
                    "order": {"A": baseline, "B": candidate},
                    "response": {
                        "scene_observations": [
                            response("B", ["traveller_visible", "phone_visible"])
                        ]
                    },
                },
                {
                    "order": {"A": candidate, "B": baseline},
                    "response": {
                        "scene_observations": [response("A", ["traveller_visible"])]
                    },
                },
            ],
            candidate_sha256=candidate,
            storyboard=[
                {
                    "id": "hook",
                    "expected_tags": ["traveller_visible", "phone_visible"],
                }
            ],
        )

        self.assertEqual(
            result["scenes"][0]["observed_tags"],
            ["traveller_visible"],
        )
        self.assertEqual(result["disagreements"][0]["values"], ["phone_visible"])


class TagMetricTests(unittest.TestCase):
    def test_scene_identity_is_part_of_alignment(self) -> None:
        storyboard = [
            {"id": "a", "expected_tags": ["phone"]},
            {"id": "b", "expected_tags": ["office"]},
        ]
        observations = [
            {"scene_id": "a", "observed_tags": ["office"]},
            {"scene_id": "b", "observed_tags": ["phone"]},
        ]

        result = calculate_tag_metrics(storyboard, observations)

        self.assertEqual(result["true_positive"], 0)
        self.assertEqual(result["false_positive"], 2)
        self.assertEqual(result["false_negative"], 2)
        self.assertEqual(result["f1"], 0.0)

    def test_partial_match(self) -> None:
        storyboard = [{"id": "a", "expected_tags": ["office", "computer"]}]
        observations = [{"scene_id": "a", "observed_tags": ["computer", "keyboard"]}]

        result = calculate_tag_metrics(storyboard, observations)

        self.assertEqual(result["true_positive"], 1)
        self.assertEqual(result["f1"], 0.5)


class TranscriptMetricTests(unittest.TestCase):
    def test_token_f1_is_case_insensitive(self) -> None:
        result = token_f1("Hello world", "hello WORLD")
        self.assertEqual(result["f1"], 1.0)

    def test_parse_srt_ignores_sequence_and_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.srt"
            path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nworld\n", encoding="utf-8")
            self.assertEqual(parse_srt_text(path), "Hello world")

    def test_exact_brand_text_is_case_sensitive(self) -> None:
        self.assertEqual(
            exact_brand_text_match("tict plans trips", "tict plans trips")["score"],
            1.0,
        )
        self.assertEqual(
            exact_brand_text_match("tict plans trips", "TICT plans trips")["score"],
            0.0,
        )


class ScreenPolicyMetricTests(unittest.TestCase):
    def test_non_product_screen_is_compliant_in_context(self) -> None:
        result = calculate_screen_policy_metrics(
            [{"id": "hook", "screen_content_policy": "non_product_context"}],
            [
                {
                    "scene_id": "hook",
                    "screen_observation": {
                        "screen_class": "generic_non_product",
                        "claims_tict_identity": False,
                        "evidence_timestamp_seconds": 2.5,
                    },
                }
            ],
        )
        self.assertEqual(result["compliance"], 1.0)
        self.assertEqual(result["failures"], [])

    def test_generic_screen_fails_when_approved_product_ui_is_required(self) -> None:
        result = calculate_screen_policy_metrics(
            [{"id": "demo", "screen_content_policy": "approved_product_ui"}],
            [
                {
                    "scene_id": "demo",
                    "screen_observation": {
                        "screen_class": "generic_non_product",
                        "claims_tict_identity": False,
                        "approved_asset_match": False,
                        "evidence_timestamp_seconds": 8.0,
                    },
                }
            ],
        )
        self.assertEqual(result["compliance"], 0.0)
        self.assertEqual(result["failures"][0]["scene_id"], "demo")


class PipelineRecordMetricTests(unittest.TestCase):
    def test_zero_cost_is_available_evidence(self) -> None:
        metrics, reasons = pipeline_record_metrics(
            {"actual_paid_cost_usd": 0.0, "generation_latency_seconds": None}
        )
        self.assertEqual(metrics["estimated_cost_usd"], 0.0)
        self.assertNotIn("estimated_cost_usd", reasons)
        self.assertIsNone(metrics["generation_latency_seconds"])
        self.assertIn("generation_latency_seconds", reasons)

    def test_runway_latency_and_cost_are_extracted(self) -> None:
        metrics, reasons = pipeline_record_metrics(
            {"actual_paid_cost_usd": 0.6, "generation_latency_seconds": 42.25}
        )
        self.assertEqual(metrics["estimated_cost_usd"], 0.6)
        self.assertEqual(metrics["generation_latency_seconds"], 42.25)
        self.assertEqual(reasons, {})

    def test_recorded_subtitle_safe_area_becomes_enforced_evidence(self) -> None:
        enforced, pending = pipeline_constraint_evidence(
            {"subtitle_safe_area_pass": True}
        )
        self.assertTrue(enforced["subtitle_safe_area_pass"])
        self.assertNotIn("subtitle_safe_area_pass", pending)

    def test_missing_subtitle_geometry_remains_pending(self) -> None:
        enforced, pending = pipeline_constraint_evidence({})
        self.assertNotIn("subtitle_safe_area_pass", enforced)
        self.assertIn("subtitle_safe_area_pass", pending)


if __name__ == "__main__":
    unittest.main()
