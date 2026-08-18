from __future__ import annotations

import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from google.genai import errors

LOOP_ROOT = Path(__file__).resolve().parents[2]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from evals.gemini_judge import (  # noqa: E402
    GeminiVideoJudge,
    JUDGE_MODEL,
    estimate_judge_pass_cost_microusd,
    is_definite_nonbillable_gemini_error,
    sanitize_judge_evidence,
)
from app.services.creative.budget import IterationBudgetLedger  # noqa: E402


class GeminiJudgePricingTests(unittest.TestCase):
    def test_two_short_videos_reserve_a_positive_fail_closed_cost(self) -> None:
        estimated = estimate_judge_pass_cost_microusd(
            model=JUDGE_MODEL,
            video_duration_seconds=30.0,
            prompt_characters=8_000,
        )

        self.assertGreaterEqual(estimated, 50_000)
        self.assertLessEqual(estimated, 100_000)

    def test_unknown_model_has_no_implicit_price(self) -> None:
        with self.assertRaisesRegex(ValueError, "pricing"):
            estimate_judge_pass_cost_microusd(
                model="unpriced-model",
                video_duration_seconds=30.0,
                prompt_characters=8_000,
            )

    def test_only_permanent_client_errors_are_definite_rejections(self) -> None:
        self.assertTrue(
            is_definite_nonbillable_gemini_error(
                errors.ClientError(400, {"error": {"message": "invalid"}})
            )
        )
        for status in (408, 409, 425, 429, 500, 503):
            error_type = errors.ServerError if status >= 500 else errors.ClientError
            with self.subTest(status=status):
                self.assertFalse(
                    is_definite_nonbillable_gemini_error(
                        error_type(status, {"error": {"message": "ambiguous"}})
                    )
                )


class GeminiJudgeEvidenceTests(unittest.TestCase):
    def test_provider_file_references_and_secrets_are_not_persisted(self) -> None:
        sanitized = sanitize_judge_evidence(
            {
                "model": JUDGE_MODEL,
                "api_key": "secret-value",
                "provider_file_uri": "https://provider.invalid/files/secret",
                "response": {"winner": "B"},
            }
        )

        self.assertNotIn("secret-value", str(sanitized))
        self.assertNotIn("provider.invalid", str(sanitized))
        self.assertEqual(sanitized["response"]["winner"], "B")


class _FakeFiles:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.upload_count = 0

    def upload(self, *, file: str, config: dict) -> SimpleNamespace:
        self.upload_count += 1
        return SimpleNamespace(
            name=f"files/{self.upload_count}",
            uri=f"https://provider.invalid/files/{self.upload_count}",
            mime_type=config["mime_type"],
            state=SimpleNamespace(name="ACTIVE"),
        )

    def get(self, *, name: str) -> SimpleNamespace:
        raise AssertionError(f"active fake file should not be polled: {name}")

    def delete(self, *, name: str) -> None:
        self.deleted.append(name)


class _FakeModels:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        api_error_on_call: int | None = None,
        api_error_code: int = 400,
    ) -> None:
        self.fail_on_call = fail_on_call
        self.api_error_on_call = api_error_on_call
        self.api_error_code = api_error_code
        self.calls = 0

    def generate_content(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        if self.calls == self.api_error_on_call:
            error_type = (
                errors.ServerError if self.api_error_code >= 500 else errors.ClientError
            )
            raise error_type(
                self.api_error_code,
                {"error": {"message": "provider error"}},
            )
        if self.calls == self.fail_on_call:
            raise ConnectionError("ambiguous transport failure")
        winner = "B" if self.calls == 1 else "A"
        observations = []
        for label in ("A", "B"):
            observations.append(
                {
                    "video_label": label,
                    "scene_id": "hook",
                    "observed_tags": ["traveller_visible"],
                    "evidence_timestamp_seconds": 2.5,
                    "screen_class": "screen_not_visible",
                    "claims_tict_identity": False,
                    "approved_asset_match": False,
                    "brand_asset_fidelity": None,
                    "reason": "A traveller is visible.",
                }
            )
        criterion = {"video_a": 4, "video_b": 4, "reason": "Comparable."}
        return SimpleNamespace(
            parsed={
                "winner": winner,
                "confidence": 0.9,
                "summary": "Candidate preference is supported by the edit.",
                "rubric": {
                    "editing_continuity": criterion,
                    "storyboard_alignment": criterion,
                    "audiovisual_coherence": criterion,
                    "product_demo_clarity": criterion,
                    "professional_finish": criterion,
                },
                "scene_observations": observations,
            },
            response_id=f"response-{self.calls}",
            model_version="gemini-3.6-flash-001",
            usage_metadata=SimpleNamespace(
                prompt_token_count=10_000,
                candidates_token_count=1_000,
                thoughts_token_count=0,
                total_token_count=11_000,
            ),
        )


class _FakeClient:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        api_error_on_call: int | None = None,
        api_error_code: int = 400,
    ) -> None:
        self.files = _FakeFiles()
        self.models = _FakeModels(
            fail_on_call=fail_on_call,
            api_error_on_call=api_error_on_call,
            api_error_code=api_error_code,
        )


def _scenario() -> dict:
    return {
        "id": "scenario-001",
        "purpose": "Test pairwise judging.",
        "expected": {
            "aspect_ratio": "9:16",
            "storyboard": [
                {
                    "id": "hook",
                    "start_seconds": 0,
                    "end_seconds": 5,
                    "screen_content_policy": "screen_hidden",
                    "expected_tags": ["traveller_visible"],
                }
            ],
        },
    }


class GeminiVideoJudgeTests(unittest.TestCase):
    def test_existing_paid_operation_blocks_legacy_resubmission_before_upload(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            ledger.record_manual_charge(
                "judge-existing-pass-1",
                57_000,
                "Ambiguous legacy pass",
            )
            client = _FakeClient()
            judge = GeminiVideoJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            )

            with self.assertRaisesRegex(RuntimeError, "resubmission is blocked"):
                judge.compare(
                    baseline_video=baseline,
                    candidate_video=candidate,
                    baseline_duration_seconds=15,
                    candidate_duration_seconds=15,
                    scenario=_scenario(),
                    scenario_sha256="a" * 64,
                    operation_prefix="judge-existing",
                )

        self.assertEqual(client.models.calls, 0)
        self.assertEqual(client.files.upload_count, 0)

    def test_explicit_provider_rejection_does_not_consume_budget(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            client = _FakeClient(api_error_on_call=1)
            judge = GeminiVideoJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            )

            with self.assertRaisesRegex(RuntimeError, "no charge was recorded"):
                judge.compare(
                    baseline_video=baseline,
                    candidate_video=candidate,
                    baseline_duration_seconds=15,
                    candidate_duration_seconds=15,
                    scenario=_scenario(),
                    scenario_sha256="a" * 64,
                    operation_prefix="judge-rejected",
                )

            budget_snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertEqual(budget_snapshot.charged_microusd, 0)
        self.assertEqual(budget_snapshot.reserved_microusd, 0)
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])

    def test_comparison_swaps_order_charges_both_passes_and_deletes_uploads(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            client = _FakeClient()
            judge = GeminiVideoJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            )

            evidence = judge.compare(
                baseline_video=baseline,
                candidate_video=candidate,
                baseline_duration_seconds=15,
                candidate_duration_seconds=15,
                scenario=_scenario(),
                scenario_sha256="a" * 64,
                operation_prefix="judge-001",
            )

            budget_snapshot = ledger.snapshot()

        self.assertEqual(len(evidence["passes"]), 2)
        self.assertEqual(
            evidence["passes"][0]["order"]["A"],
            evidence["baseline_sha256"],
        )
        self.assertEqual(
            evidence["passes"][1]["order"]["A"],
            evidence["candidate_sha256"],
        )
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])
        self.assertEqual(client.models.calls, 2)
        self.assertGreater(budget_snapshot.charged_microusd, 0)
        self.assertEqual(budget_snapshot.reserved_microusd, 0)
        self.assertNotIn("provider.invalid", str(evidence))
        self.assertEqual(evidence["status"], "complete")

    def test_server_error_is_ambiguous_and_charged_once(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            client = _FakeClient(api_error_on_call=1, api_error_code=503)
            judge = GeminiVideoJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            )

            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                judge.compare(
                    baseline_video=baseline,
                    candidate_video=candidate,
                    baseline_duration_seconds=15,
                    candidate_duration_seconds=15,
                    scenario=_scenario(),
                    scenario_sha256="a" * 64,
                    operation_prefix="judge-server-error",
                )
            snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertGreater(snapshot.charged_microusd, 0)

    def test_ambiguous_inference_failure_keeps_reservation_without_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            client = _FakeClient(fail_on_call=1)
            judge = GeminiVideoJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            )

            with self.assertRaisesRegex(RuntimeError, "was not retried"):
                judge.compare(
                    baseline_video=baseline,
                    candidate_video=candidate,
                    baseline_duration_seconds=15,
                    candidate_duration_seconds=15,
                    scenario=_scenario(),
                    scenario_sha256="a" * 64,
                    operation_prefix="judge-failure",
                )

            budget_snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertEqual(budget_snapshot.reserved_microusd, 0)
        self.assertGreater(budget_snapshot.charged_microusd, 0)
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])

    def test_first_successful_pass_is_checkpointed_before_second_pass_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline")
            candidate.write_bytes(b"candidate")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            client = _FakeClient(fail_on_call=2)
            judge = GeminiVideoJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            )
            checkpoints: list[dict] = []

            with self.assertRaisesRegex(RuntimeError, "was not retried"):
                judge.compare(
                    baseline_video=baseline,
                    candidate_video=candidate,
                    baseline_duration_seconds=15,
                    candidate_duration_seconds=15,
                    scenario=_scenario(),
                    scenario_sha256="a" * 64,
                    operation_prefix="judge-partial",
                    checkpoint=checkpoints.append,
                )

        self.assertEqual(client.models.calls, 2)
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0]["status"], "partial")
        self.assertEqual(len(checkpoints[0]["passes"]), 1)
        self.assertEqual(checkpoints[0]["passes"][0]["pass_id"], "baseline-a")


if __name__ == "__main__":
    unittest.main()
