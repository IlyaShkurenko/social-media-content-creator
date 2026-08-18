from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from google.genai import _transformers, errors


LOOP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = LOOP_ROOT.parents[1]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from evals.invariant_judge import (  # noqa: E402
    EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    GeminiInvariantJudge,
    InvariantJudgeResponse,
    build_invariant_judge_prompt,
    build_shared_invariant_contract,
    build_winner_diagnostic,
    map_invariant_statuses,
    sha256_json,
)


def _scenario() -> dict:
    return {
        "schema_version": 1,
        "id": "shared-downstream-001",
        "title": "A concept-specific title that must not enter the prompt",
        "purpose": "Require an airport, a map, and a browser-tab opening.",
        "hypothesis": "HOOK-SENTINEL: compare an unrelated opening premise.",
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
                        "HOOK-SENTINEL traveller uses an airport map and browser."
                    ),
                    "expected_tags": [
                        "airport_visible",
                        "map_visible",
                        "browser_visible",
                    ],
                },
                {
                    "id": "product_demo",
                    "start_seconds": 5.0,
                    "end_seconds": 11.0,
                    "screen_content_policy": "approved_product_ui",
                    "description": "The exact approved tict trip overview is legible.",
                    "voiceover": "tict turns every place into one clear trip plan.",
                    "expected_tags": [
                        "approved_tict_ui_visible",
                        "product_text_legible",
                    ],
                },
                {
                    "id": "cta",
                    "start_seconds": 11.0,
                    "end_seconds": 15.0,
                    "screen_content_policy": "unconstrained",
                    "description": "Approved logo and mascot support one CTA.",
                    "voiceover": "Plan less. Travel more with tict.",
                    "expected_tags": [
                        "approved_logo_visible",
                        "approved_mascot_visible",
                        "single_cta_visible",
                    ],
                },
            ],
        },
    }


def _citation(scene: str, timestamp_ms: int) -> dict:
    return {
        "scene_purpose": scene,
        "timestamp_ms": timestamp_ms,
        "observation": f"Visible evidence in {scene}.",
    }


def _assessment(status: str, scene: str, timestamp_ms: int) -> dict:
    return {
        "status": status,
        "evidence": (
            [] if status == "unverifiable" else [_citation(scene, timestamp_ms)]
        ),
        "reason": "The cited downstream evidence supports this verdict.",
    }


def _paired(status_a: str, status_b: str, scene: str, timestamp_ms: int) -> dict:
    return {
        "video_a": _assessment(status_a, scene, timestamp_ms),
        "video_b": _assessment(status_b, scene, timestamp_ms),
    }


def _response_payload(*, winner: str = "B") -> dict:
    return {
        "winner": winner,
        "winner_reason": "The selected downstream edit has cleaner evidence.",
        "criteria": {
            "transition_mechanics": _paired(
                "partially_met", "met", "product_demo", 5100
            ),
            "audiovisual_correctness": _paired(
                "partially_met", "met", "product_demo", 7000
            ),
            "product_brand_fidelity": _paired(
                "met", "met", "product_demo", 9000
            ),
            "cta_clarity": _paired("partially_met", "met", "cta", 12000),
            "professional_finish": _paired(
                "partially_met", "met", "cta", 14000
            ),
        },
        "scene_evidence": [
            {
                "video_label": label,
                "scene_purpose": scene,
                "timestamp_ms": timestamp_ms,
                "observation": f"Video {label} visibly shows the {scene}.",
            }
            for label in ("A", "B")
            for scene, timestamp_ms in (("product_demo", 8000), ("cta", 13000))
        ],
    }


class InvariantJudgeContractTests(unittest.TestCase):
    def test_prompt_contains_only_shared_downstream_contract(self) -> None:
        prompt = build_invariant_judge_prompt(_scenario())
        lowered = prompt.lower()

        self.assertIn("product_demo", prompt)
        self.assertIn("approved product UI", prompt)
        self.assertIn("diagnostic only", prompt)
        self.assertIn("Do not return numeric scores", prompt)
        self.assertNotIn("HOOK-SENTINEL", prompt)
        self.assertNotIn("airport", lowered)
        self.assertNotIn("map_visible", lowered)
        self.assertNotIn("browser", lowered)

    def test_shared_contract_hash_is_independent_of_hook_requirements(self) -> None:
        original = _scenario()
        changed = copy.deepcopy(original)
        changed["purpose"] = "Entirely different concept-specific purpose"
        changed["hypothesis"] = "Entirely different hypothesis"
        changed["expected"]["storyboard"][0] = {
            "id": "hook",
            "start_seconds": 0.0,
            "end_seconds": 5.0,
            "description": "A completely unrelated opening.",
        }

        original_contract = build_shared_invariant_contract(original)
        changed_contract = build_shared_invariant_contract(changed)

        self.assertEqual(original_contract, changed_contract)
        self.assertEqual(
            sha256_json(original_contract),
            sha256_json(changed_contract),
        )
        self.assertEqual(
            [scene["purpose"] for scene in original_contract["downstream_scenes"]],
            ["product_demo", "cta"],
        )

    def test_response_schema_is_supported_by_gemini_transport(self) -> None:
        schema = _transformers.t_schema(None, InvariantJudgeResponse)
        payload = schema.model_dump(mode="json", by_alias=True, exclude_none=True)

        self.assertTrue(payload)
        self.assertNotIn("additionalProperties", json.dumps(payload))

    def test_closed_statuses_map_to_scores_deterministically(self) -> None:
        payload = _response_payload()
        payload["criteria"]["professional_finish"]["video_a"] = _assessment(
            "unverifiable",
            "cta",
            14000,
        )

        mapped = map_invariant_statuses(payload)

        self.assertEqual(mapped["video_a"]["transition_mechanics"], 0.5)
        self.assertEqual(mapped["video_b"]["transition_mechanics"], 1.0)
        self.assertIsNone(mapped["video_a"]["professional_finish"])

    def test_winner_diagnostic_uses_artifacts_not_ab_labels(self) -> None:
        baseline = "a" * 64
        candidate = "b" * 64

        diagnostic = build_winner_diagnostic(
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

        self.assertEqual(diagnostic["candidate_credit"], 1.0)
        self.assertEqual(diagnostic["outcome"], "candidate")
        self.assertTrue(diagnostic["position_balanced"])
        self.assertTrue(diagnostic["diagnostic_only"])


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
        response_payload: dict | None = None,
    ) -> None:
        self.fail_on_call = fail_on_call
        self.api_error_on_call = api_error_on_call
        self.api_error_code = api_error_code
        self.response_payload = response_payload
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
        if self.response_payload is None:
            winner = "B" if self.calls == 1 else "A"
            payload = _response_payload(winner=winner)
        else:
            payload = self.response_payload
        return SimpleNamespace(
            parsed=payload,
            response_id=f"invariant-response-{self.calls}",
            model_version="gemini-3.6-flash-001",
            usage_metadata=SimpleNamespace(
                prompt_token_count=5_000,
                candidates_token_count=700,
                thoughts_token_count=0,
                total_token_count=5_700,
            ),
        )


class _FakeClient:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        api_error_on_call: int | None = None,
        api_error_code: int = 400,
        response_payload: dict | None = None,
    ) -> None:
        self.files = _FakeFiles()
        self.models = _FakeModels(
            fail_on_call=fail_on_call,
            api_error_on_call=api_error_on_call,
            api_error_code=api_error_code,
            response_payload=response_payload,
        )


class GeminiInvariantJudgeTests(unittest.TestCase):
    def _judge(
        self,
        root: Path,
        client: _FakeClient,
    ) -> tuple[GeminiInvariantJudge, IterationBudgetLedger]:
        ledger = IterationBudgetLedger(
            root / "budget.sqlite3",
            scope_id="invariant-judge-tests",
            cap_microusd=10_000_000,
        )
        return (
            GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            ),
            ledger,
        )

    def _compare(
        self,
        judge: GeminiInvariantJudge,
        *,
        baseline: Path,
        candidate: Path,
        checkpoint=None,
        existing_evidence: dict | None = None,
    ) -> dict:
        return judge.compare(
            baseline_video=baseline,
            candidate_video=candidate,
            baseline_duration_seconds=15.0,
            candidate_duration_seconds=15.0,
            scenario=_scenario(),
            scenario_sha256="c" * 64,
            operation_prefix="invariant-001",
            checkpoint=checkpoint,
            existing_evidence=existing_evidence,
        )

    def test_two_reversed_passes_record_hashed_sanitized_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            client = _FakeClient()
            judge, ledger = self._judge(root, client)
            checkpoints: list[dict] = []

            evidence = self._compare(
                judge,
                baseline=baseline,
                candidate=candidate,
                checkpoint=checkpoints.append,
            )
            snapshot = ledger.snapshot()

        self.assertEqual(evidence["schema_version"], EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(evidence["evaluator_version"], EVALUATOR_VERSION)
        self.assertRegex(evidence["shared_contract_sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(evidence["response_schema_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(len(evidence["passes"]), 2)
        self.assertEqual(
            evidence["passes"][0]["order"]["A"], evidence["baseline_sha256"]
        )
        self.assertEqual(
            evidence["passes"][1]["order"]["A"], evidence["candidate_sha256"]
        )
        self.assertEqual(evidence["winner_diagnostic"]["candidate_credit"], 1.0)
        self.assertNotIn("provider.invalid", str(evidence))
        self.assertEqual(client.models.calls, 2)
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])
        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(snapshot.reserved_microusd, 0)
        self.assertEqual([item["status"] for item in checkpoints], ["partial", "partial", "complete"])

    def test_explicit_provider_rejection_does_not_charge_or_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            client = _FakeClient(api_error_on_call=1)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "no charge was recorded"):
                self._compare(judge, baseline=baseline, candidate=candidate)
            snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertEqual(snapshot.charged_microusd, 0)
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])

    def test_ambiguous_result_is_charged_fail_closed_without_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            client = _FakeClient(fail_on_call=1)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "was not retried"):
                self._compare(judge, baseline=baseline, candidate=candidate)
            snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(snapshot.reserved_microusd, 0)
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])

    def test_server_error_is_ambiguous_and_charged_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            client = _FakeClient(api_error_on_call=1, api_error_code=503)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                self._compare(judge, baseline=baseline, candidate=candidate)
            snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertGreater(snapshot.charged_microusd, 0)

    def test_invalid_scene_timestamp_is_rejected_after_charging_response(self) -> None:
        payload = _response_payload()
        payload["scene_evidence"][0]["timestamp_ms"] = 2_000
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            client = _FakeClient(response_payload=payload)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(ValueError, "outside its scene"):
                self._compare(judge, baseline=baseline, candidate=candidate)
            snapshot = ledger.snapshot()

        self.assertEqual(client.models.calls, 1)
        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(client.files.deleted, ["files/1", "files/2"])

    def test_partial_checkpoint_resumes_only_missing_original_pass(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            first_client = _FakeClient(api_error_on_call=2)
            first_judge, ledger = self._judge(root, first_client)
            checkpoints: list[dict] = []

            with self.assertRaisesRegex(RuntimeError, "no charge was recorded"):
                self._compare(
                    first_judge,
                    baseline=baseline,
                    candidate=candidate,
                    checkpoint=checkpoints.append,
                )
            partial = checkpoints[-1]
            first_operations = ledger.list_operations()
            first_charge = ledger.snapshot().charged_microusd

            resumed_client = _FakeClient()
            resumed_judge = GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=resumed_client,
            )
            complete = self._compare(
                resumed_judge,
                baseline=baseline,
                candidate=candidate,
                existing_evidence=partial,
            )
            final_operations = ledger.list_operations()
            final_charge = ledger.snapshot().charged_microusd

        self.assertEqual(partial["status"], "partial")
        self.assertEqual(len(partial["passes"]), 1)
        self.assertEqual(first_client.models.calls, 2)
        self.assertEqual(resumed_client.models.calls, 1)
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(
            [item["pass_id"] for item in complete["passes"]],
            ["baseline-a", "candidate-a"],
        )
        self.assertEqual(
            [item.operation_id for item in final_operations],
            ["invariant-001-pass-1", "invariant-001-pass-2"],
        )
        self.assertEqual(len(first_operations), 1)
        self.assertEqual(
            final_operations[0].amount_microusd,
            first_operations[0].amount_microusd,
        )
        self.assertGreater(final_charge, first_charge)

    def test_stale_partial_checkpoint_is_rejected_before_provider_calls(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            first_client = _FakeClient(api_error_on_call=2)
            first_judge, ledger = self._judge(root, first_client)
            checkpoints: list[dict] = []
            with self.assertRaises(RuntimeError):
                self._compare(
                    first_judge,
                    baseline=baseline,
                    candidate=candidate,
                    checkpoint=checkpoints.append,
                )
            stale = copy.deepcopy(checkpoints[-1])
            stale["shared_contract_sha256"] = "0" * 64
            resumed_client = _FakeClient()
            resumed_judge = GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=resumed_client,
            )

            with self.assertRaisesRegex(ValueError, "shared_contract_sha256"):
                self._compare(
                    resumed_judge,
                    baseline=baseline,
                    candidate=candidate,
                    existing_evidence=stale,
                )

        self.assertEqual(resumed_client.models.calls, 0)
        self.assertEqual(resumed_client.files.upload_count, 0)

    def test_partial_checkpoint_cannot_change_pass_order_or_operation_id(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            first_client = _FakeClient(api_error_on_call=2)
            first_judge, ledger = self._judge(root, first_client)
            checkpoints: list[dict] = []
            with self.assertRaises(RuntimeError):
                self._compare(
                    first_judge,
                    baseline=baseline,
                    candidate=candidate,
                    checkpoint=checkpoints.append,
                )
            wrong_order = copy.deepcopy(checkpoints[-1])
            wrong_order["passes"][0]["order"] = {
                "A": wrong_order["candidate_sha256"],
                "B": wrong_order["baseline_sha256"],
            }
            wrong_operation = copy.deepcopy(checkpoints[-1])
            wrong_operation["passes"][0]["budget"]["operation_id"] = (
                "different-operation-pass-1"
            )
            resumed_client = _FakeClient()
            resumed_judge = GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=resumed_client,
            )

            with self.assertRaisesRegex(ValueError, "A/B order"):
                self._compare(
                    resumed_judge,
                    baseline=baseline,
                    candidate=candidate,
                    existing_evidence=wrong_order,
                )
            with self.assertRaisesRegex(ValueError, "paid-operation binding"):
                self._compare(
                    resumed_judge,
                    baseline=baseline,
                    candidate=candidate,
                    existing_evidence=wrong_operation,
                )

        self.assertEqual(resumed_client.models.calls, 0)
        self.assertEqual(resumed_client.files.upload_count, 0)

    def test_complete_checkpoint_is_an_idempotent_provider_noop(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            initial_client = _FakeClient()
            initial_judge, ledger = self._judge(root, initial_client)
            complete = self._compare(
                initial_judge,
                baseline=baseline,
                candidate=candidate,
            )
            charged_before = ledger.snapshot().charged_microusd
            resumed_client = _FakeClient()
            resumed_judge = GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=resumed_client,
            )

            returned = self._compare(
                resumed_judge,
                baseline=baseline,
                candidate=candidate,
                existing_evidence=complete,
            )
            charged_after = ledger.snapshot().charged_microusd

        self.assertEqual(returned, complete)
        self.assertEqual(resumed_client.models.calls, 0)
        self.assertEqual(resumed_client.files.upload_count, 0)
        self.assertEqual(charged_after, charged_before)

    def test_complete_checkpoint_requires_matching_paid_ledger(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            initial_client = _FakeClient()
            initial_judge, _ = self._judge(root, initial_client)
            complete = self._compare(
                initial_judge,
                baseline=baseline,
                candidate=candidate,
            )
            fresh_ledger = IterationBudgetLedger(
                root / "fresh-budget.sqlite3",
                scope_id="invariant-judge-tests",
                cap_microusd=10_000_000,
            )
            replay_client = _FakeClient()
            replay_judge = GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=fresh_ledger,
                client=replay_client,
            )

            with self.assertRaisesRegex(RuntimeError, "does not match the paid ledger"):
                self._compare(
                    replay_judge,
                    baseline=baseline,
                    candidate=candidate,
                    existing_evidence=complete,
                )
            fresh_operations = fresh_ledger.list_operations()

        self.assertEqual(replay_client.models.calls, 0)
        self.assertEqual(replay_client.files.upload_count, 0)
        self.assertEqual(fresh_operations, ())

    def test_ambiguous_missing_pass_is_never_retried_from_partial_checkpoint(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.mp4"
            candidate = root / "candidate.mp4"
            baseline.write_bytes(b"baseline-video")
            candidate.write_bytes(b"candidate-video")
            first_client = _FakeClient(fail_on_call=2)
            first_judge, ledger = self._judge(root, first_client)
            checkpoints: list[dict] = []
            with self.assertRaisesRegex(RuntimeError, "was not retried"):
                self._compare(
                    first_judge,
                    baseline=baseline,
                    candidate=candidate,
                    checkpoint=checkpoints.append,
                )
            charged_before = ledger.snapshot().charged_microusd
            operations_before = ledger.list_operations()
            resumed_client = _FakeClient()
            resumed_judge = GeminiInvariantJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=resumed_client,
            )

            with self.assertRaisesRegex(RuntimeError, "will not be retried"):
                self._compare(
                    resumed_judge,
                    baseline=baseline,
                    candidate=candidate,
                    existing_evidence=checkpoints[-1],
                )
            charged_after = ledger.snapshot().charged_microusd
            operations_after = ledger.list_operations()

        self.assertEqual(resumed_client.models.calls, 0)
        self.assertEqual(resumed_client.files.upload_count, 0)
        self.assertEqual(charged_after, charged_before)
        self.assertEqual(operations_after, operations_before)


if __name__ == "__main__":
    unittest.main()
