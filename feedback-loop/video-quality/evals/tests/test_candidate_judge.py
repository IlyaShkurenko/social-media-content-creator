from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from google import genai
from google.genai import _transformers, errors


LOOP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = LOOP_ROOT.parents[1]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from evals import candidate_judge as candidate_judge_module  # noqa: E402
from evals.candidate_judge import (  # noqa: E402
    CandidateJudgeResponse,
    EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    GeminiCandidateJudge,
    _candidate_contract,
    _validate_response,
    build_candidate_judge_prompt,
    map_dimension_statuses,
    sha256_json,
)


def _concept() -> dict:
    return {
        "concept_id": "scattered-map-pins",
        "hypothesis": (
            "Map-pin overload makes a clear day-by-day trip plan feel valuable."
        ),
        "audience_problem": "Saved places do not form an actionable itinerary.",
        "target_emotion": "Confused",
        "emotional_arc": "Disorganized confusion to streamlined clarity",
        "hook_setting": "A cafe table covered by a city map and sticky notes.",
        "hook_camera": "Top-down push toward the cluttered map.",
        "hook_voiceover": (
            "You saved fifty spots, but how are you spending day one?"
        ),
        "hook_voice_delivery": "A genuinely confused, quietly anxious person thinking out loud.",
        "hook_beats": [
            {
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "visible_action": "Hands rapidly shuffle sticky notes on a map.",
                "expected_evidence": [
                    "cluttered map",
                    "hands shuffling sticky notes",
                ],
            },
            {
                "start_seconds": 2.0,
                "end_seconds": 5.0,
                "visible_action": "The clutter clears to one centered phone.",
                "expected_evidence": ["clear focal point on one phone"],
            },
        ],
        "product_bridge": "Match-cut into the exact tict plan overview.",
        "quality_criteria": [
            "The planning problem is legible without audio in two seconds.",
            "No generated screen is presented as tict.",
        ],
    }


def _storyboard() -> dict:
    return {
        "schema_version": "1.2",
        "storyboard_id": "map-campaign-scattered-map-pins",
        "content_language": "en-US",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 15.0,
        "hypothesis": _concept()["hypothesis"],
        "scenes": [
            {
                "scene_id": "hook",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
                "purpose": "hook",
                "visual_intent": {
                    "setting": "A cafe table with a city map.",
                    "subject_action": "Hands rearrange notes, then clear the table.",
                    "camera": "Top-down push-in.",
                    "screen_content_policy": "non_product_context",
                },
                "voiceover": _concept()["hook_voiceover"],
                "onscreen_text": "Too many saved places?",
                "expected_evidence": ["cluttered map", "sticky notes"],
            },
            {
                "scene_id": "product_demo",
                "start_seconds": 5.0,
                "end_seconds": 11.0,
                "purpose": "product_demo",
                "visual_intent": {
                    "setting": "Exact local tict product composition.",
                    "subject_action": "The approved plan remains legible.",
                    "camera": "Controlled push-in.",
                    "screen_content_policy": "approved_product_ui",
                },
                "voiceover": "tict turns every place into one clear trip plan.",
                "onscreen_text": "Your whole trip. One plan.",
                "expected_evidence": ["approved_tict_ui_visible"],
            },
            {
                "scene_id": "cta",
                "start_seconds": 11.0,
                "end_seconds": 15.0,
                "purpose": "cta",
                "visual_intent": {
                    "setting": "Brand end card.",
                    "subject_action": "Logo, mascot, and action appear.",
                    "camera": "Static.",
                    "screen_content_policy": "unconstrained",
                },
                "voiceover": "Plan less. Travel more with tict.",
                "onscreen_text": "Plan less. Travel more.",
                "expected_evidence": ["single_cta_visible"],
            },
        ],
    }


def _assessment(
    status: str,
    *,
    timestamp_ms: int = 1000,
) -> dict:
    return {
        "status": status,
        "evidence": (
            []
            if status == "unverifiable"
            else [
                {
                    "timestamp_ms": timestamp_ms,
                    "observation": "Hands visibly move sticky notes across the map.",
                }
            ]
        ),
        "reason": "The cited action supports this verdict.",
    }


def _response_payload() -> dict:
    return {
        "observed_hook_summary": "Hands rearrange sticky notes on a map.",
        "observed_bridge_summary": "The map cuts to a clean product card.",
        "hypothesis_match": _assessment("met"),
        "target_emotion_strength": _assessment("partially_met"),
        "first_two_seconds_hook_clarity": _assessment("met"),
        "hook_to_product_bridge_coherence": _assessment(
            "partially_met",
            timestamp_ms=5100,
        ),
        "storyboard_action_alignment": _assessment("not_met"),
    }


class CandidateJudgeContractTests(unittest.TestCase):
    def test_prompt_evaluates_only_the_candidates_own_contract(self) -> None:
        prompt = build_candidate_judge_prompt(_concept(), _storyboard())

        self.assertIn("scattered-map-pins", prompt)
        self.assertNotIn("Hands rapidly shuffle sticky notes", prompt)
        self.assertIn("Hands rearrange notes, then clear the table", prompt)
        self.assertIn("There is no baseline and no competing concept", prompt)
        self.assertIn("Do not return numeric scores", prompt)
        self.assertIn("compiled storyboard is the only authority", prompt)
        self.assertIn("Compare only against that compiled hook scene", prompt)
        self.assertNotIn("airport_visible", prompt)

    def test_superseded_raw_action_is_not_exposed_as_a_requirement(self) -> None:
        concept = _concept()
        concept["hook_beats"][0]["visible_action"] = (
            "The hands sweep every note off the table."
        )
        storyboard = _storyboard()
        storyboard["scenes"][0]["visual_intent"]["subject_action"] = (
            "The hands continue repositioning notes without clearing the table."
        )

        prompt = build_candidate_judge_prompt(concept, storyboard)

        self.assertNotIn("sweep every note", prompt)
        self.assertIn("continue repositioning notes", prompt)
        self.assertIn("never\n   against raw or superseded concept beats", prompt)

    def test_response_schema_is_supported_by_gemini_transport(self) -> None:
        client = genai.Client(api_key="offline-preflight")
        schema = _transformers.t_schema(
            client._api_client,
            CandidateJudgeResponse,
        )
        payload = schema.model_dump(mode="json", by_alias=True, exclude_none=True)

        self.assertTrue(payload)
        self.assertNotIn("additionalProperties", json.dumps(payload))

    def test_status_scores_are_mapped_deterministically(self) -> None:
        payload = _response_payload()
        payload["storyboard_action_alignment"] = _assessment("unverifiable")

        scores = map_dimension_statuses(payload)

        self.assertEqual(scores["hypothesis_match"], 1.0)
        self.assertEqual(scores["target_emotion_strength"], 0.5)
        self.assertEqual(scores["first_two_seconds_hook_clarity"], 1.0)
        self.assertEqual(scores["hook_to_product_bridge_coherence"], 0.5)
        self.assertIsNone(scores["storyboard_action_alignment"])

    def test_contract_hash_is_stable_across_dictionary_order(self) -> None:
        original = _concept()
        reversed_items = dict(reversed(list(original.items())))

        self.assertEqual(sha256_json(original), sha256_json(reversed_items))

    def test_first_two_seconds_evidence_cannot_cite_fourteen_seconds(self) -> None:
        payload = _response_payload()
        payload["first_two_seconds_hook_clarity"] = _assessment(
            "met",
            timestamp_ms=14_000,
        )

        with self.assertRaisesRegex(ValueError, "0-2000 ms"):
            _validate_response(
                CandidateJudgeResponse.model_validate(payload),
                video_duration_seconds=15.0,
                contract=_candidate_contract(_concept(), _storyboard()),
            )

    def test_hook_semantic_evidence_must_stay_inside_compiled_hook(self) -> None:
        payload = _response_payload()
        payload["hypothesis_match"] = _assessment("met", timestamp_ms=7_000)

        with self.assertRaisesRegex(ValueError, "compiled hook range"):
            _validate_response(
                CandidateJudgeResponse.model_validate(payload),
                video_duration_seconds=15.0,
                contract=_candidate_contract(_concept(), _storyboard()),
            )

    def test_bridge_evidence_must_stay_near_compiled_scene_boundary(self) -> None:
        payload = _response_payload()
        payload["hook_to_product_bridge_coherence"] = _assessment(
            "met",
            timestamp_ms=1_000,
        )

        with self.assertRaisesRegex(ValueError, "transition window"):
            _validate_response(
                CandidateJudgeResponse.model_validate(payload),
                video_duration_seconds=15.0,
                contract=_candidate_contract(_concept(), _storyboard()),
            )


class _FakeFiles:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def upload(self, *, file: str, config: dict) -> SimpleNamespace:
        return SimpleNamespace(
            name="files/candidate-1",
            uri="https://provider.invalid/files/candidate-1",
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
        fail: bool = False,
        api_error_code: int | None = None,
        response_payload: dict | None = None,
    ) -> None:
        self.fail = fail
        self.api_error_code = api_error_code
        self.response_payload = response_payload or _response_payload()
        self.calls = 0

    def generate_content(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        if self.api_error_code is not None:
            error_payload = {"error": {"message": "provider rejection"}}
            error_type = (
                errors.ClientError
                if self.api_error_code < 500
                else errors.ServerError
            )
            raise error_type(self.api_error_code, error_payload)
        if self.fail:
            raise ConnectionError("ambiguous transport failure")
        return SimpleNamespace(
            parsed=self.response_payload,
            response_id="candidate-response-1",
            model_version="gemini-3.6-flash-001",
            usage_metadata=SimpleNamespace(
                prompt_token_count=4_000,
                candidates_token_count=600,
                thoughts_token_count=0,
                total_token_count=4_600,
            ),
        )


class _FakeClient:
    def __init__(
        self,
        *,
        fail: bool = False,
        api_error_code: int | None = None,
        response_payload: dict | None = None,
    ) -> None:
        self.files = _FakeFiles()
        self.models = _FakeModels(
            fail=fail,
            api_error_code=api_error_code,
            response_payload=response_payload,
        )


class GeminiCandidateJudgeTests(unittest.TestCase):
    def _judge(
        self,
        root: Path,
        client: _FakeClient,
    ) -> tuple[GeminiCandidateJudge, IterationBudgetLedger]:
        ledger = IterationBudgetLedger(
            root / "budget.sqlite3",
            scope_id="candidate-judge-tests",
            cap_microusd=10_000_000,
        )
        return (
            GeminiCandidateJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=client,
            ),
            ledger,
        )

    def test_success_records_versioned_hashed_sanitized_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "candidate.mp4"
            video.write_bytes(b"candidate-video")
            client = _FakeClient()
            judge, ledger = self._judge(root, client)

            evidence = judge.inspect(
                video_path=video,
                video_duration_seconds=15.0,
                concept=_concept(),
                storyboard=_storyboard(),
                operation_id="candidate-semantic-001",
            )
            snapshot = ledger.snapshot()

        self.assertEqual(evidence["schema_version"], EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(evidence["evaluator_version"], EVALUATOR_VERSION)
        self.assertEqual(evidence["concept_id"], "scattered-map-pins")
        self.assertEqual(evidence["dimension_scores"]["hypothesis_match"], 1.0)
        self.assertRegex(evidence["contract_sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(evidence["video_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("provider.invalid", str(evidence))
        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(snapshot.reserved_microusd, 0)
        self.assertEqual(client.models.calls, 1)
        self.assertEqual(client.files.deleted, ["files/candidate-1"])

    def test_explicit_api_rejection_does_not_charge_or_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "candidate.mp4"
            video.write_bytes(b"candidate-video")
            client = _FakeClient(api_error_code=400)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "no charge was recorded"):
                judge.inspect(
                    video_path=video,
                    video_duration_seconds=15.0,
                    concept=_concept(),
                    storyboard=_storyboard(),
                    operation_id="candidate-semantic-rejected",
                )
            snapshot = ledger.snapshot()

        self.assertEqual(snapshot.charged_microusd, 0)
        self.assertEqual(client.models.calls, 1)
        self.assertEqual(client.files.deleted, ["files/candidate-1"])

    def test_ambiguous_http_error_charges_once_and_blocks_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "candidate.mp4"
            video.write_bytes(b"candidate-video")
            client = _FakeClient(api_error_code=503)
            judge, ledger = self._judge(root, client)
            arguments = {
                "video_path": video,
                "video_duration_seconds": 15.0,
                "concept": _concept(),
                "storyboard": _storyboard(),
                "operation_id": "candidate-semantic-http-ambiguous",
            }

            with self.assertRaisesRegex(RuntimeError, r"HTTP 503.*worst-case charge"):
                judge.inspect(**arguments)
            charged_after_first_call = ledger.snapshot().charged_microusd
            with self.assertRaisesRegex(RuntimeError, "already manual_charge"):
                judge.inspect(**arguments)
            charged_after_retry = ledger.snapshot().charged_microusd

        self.assertGreater(charged_after_first_call, 0)
        self.assertEqual(charged_after_retry, charged_after_first_call)
        self.assertEqual(client.models.calls, 1)
        self.assertEqual(client.files.deleted, ["files/candidate-1"])

    def test_retryable_4xx_is_treated_as_ambiguous(self) -> None:
        for status in (408, 409, 425, 429):
            with self.subTest(status=status), TemporaryDirectory() as directory:
                root = Path(directory)
                video = root / "candidate.mp4"
                video.write_bytes(b"candidate-video")
                client = _FakeClient(api_error_code=status)
                judge, ledger = self._judge(root, client)

                with self.assertRaisesRegex(RuntimeError, rf"HTTP {status}"):
                    judge.inspect(
                        video_path=video,
                        video_duration_seconds=15.0,
                        concept=_concept(),
                        storyboard=_storyboard(),
                        operation_id=f"candidate-semantic-http-{status}",
                    )

                self.assertGreater(ledger.snapshot().charged_microusd, 0)

    def test_ambiguous_failure_keeps_worst_case_charge_without_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "candidate.mp4"
            video.write_bytes(b"candidate-video")
            client = _FakeClient(fail=True)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "worst-case charge"):
                judge.inspect(
                    video_path=video,
                    video_duration_seconds=15.0,
                    concept=_concept(),
                    storyboard=_storyboard(),
                    operation_id="candidate-semantic-ambiguous",
                )
            with self.assertRaisesRegex(RuntimeError, "already manual_charge"):
                judge.inspect(
                    video_path=video,
                    video_duration_seconds=15.0,
                    concept=_concept(),
                    storyboard=_storyboard(),
                    operation_id="candidate-semantic-ambiguous",
                )
            snapshot = ledger.snapshot()

        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(client.models.calls, 1)
        self.assertEqual(client.files.deleted, ["files/candidate-1"])

    def test_invalid_timestamp_is_rejected_after_billable_response(self) -> None:
        payload = _response_payload()
        payload["hypothesis_match"] = _assessment("met", timestamp_ms=20_000)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "candidate.mp4"
            video.write_bytes(b"candidate-video")
            client = _FakeClient(response_payload=payload)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(ValueError, "outside the video"):
                judge.inspect(
                    video_path=video,
                    video_duration_seconds=15.0,
                    concept=_concept(),
                    storyboard=_storyboard(),
                    operation_id="candidate-semantic-invalid",
                )
            snapshot = ledger.snapshot()

        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(client.models.calls, 1)
        self.assertEqual(client.files.deleted, ["files/candidate-1"])

    def test_cli_reuses_complete_bound_output_without_provider_work(self) -> None:
        with TemporaryDirectory(dir=LOOP_ROOT) as directory:
            root = Path(directory)
            concept_path = root / "concept.json"
            storyboard_path = root / "storyboard.json"
            video = root / "candidate.mp4"
            output = root / "candidate-evidence.json"
            database = root / "budget.sqlite3"
            concept_path.write_text(json.dumps(_concept()), encoding="utf-8")
            storyboard_path.write_text(json.dumps(_storyboard()), encoding="utf-8")
            video.write_bytes(b"candidate-video")
            ledger = IterationBudgetLedger(
                database,
                scope_id=candidate_judge_module.ITERATION_SCOPE_ID,
                cap_microusd=candidate_judge_module.ITERATION_CAP_MICROUSD,
            )
            evidence = GeminiCandidateJudge(
                api_key="fake-key",
                budget_ledger=ledger,
                client=_FakeClient(),
            ).inspect(
                video_path=video,
                video_duration_seconds=15.0,
                concept=_concept(),
                storyboard=_storyboard(),
                operation_id="candidate-semantic-cli-complete",
            )
            output.write_text(json.dumps(evidence), encoding="utf-8")
            original_output = output.read_bytes()
            original_snapshot = ledger.snapshot()
            args = SimpleNamespace(
                concept=str(concept_path),
                storyboard=str(storyboard_path),
                concept_id=None,
                video=str(video),
                output=str(output),
                operation_id="candidate-semantic-cli-complete",
                model=candidate_judge_module.JUDGE_MODEL,
                budget_database=str(database),
                confirm_paid="YES",
            )

            with (
                mock.patch.object(candidate_judge_module, "parse_args", return_value=args),
                mock.patch.object(
                    candidate_judge_module, "_video_duration", return_value=15.0
                ),
                mock.patch.object(
                    candidate_judge_module, "GeminiCandidateJudge"
                ) as judge_class,
                mock.patch("builtins.print"),
            ):
                result = candidate_judge_module.main()

            self.assertEqual(result, 0)
            judge_class.assert_not_called()
            self.assertEqual(output.read_bytes(), original_output)
            self.assertEqual(ledger.snapshot(), original_snapshot)

    def test_cli_blocks_existing_operation_when_output_is_missing(self) -> None:
        with TemporaryDirectory(dir=LOOP_ROOT) as directory:
            root = Path(directory)
            concept_path = root / "concept.json"
            storyboard_path = root / "storyboard.json"
            video = root / "candidate.mp4"
            output = root / "missing-evidence.json"
            database = root / "budget.sqlite3"
            concept_path.write_text(json.dumps(_concept()), encoding="utf-8")
            storyboard_path.write_text(json.dumps(_storyboard()), encoding="utf-8")
            video.write_bytes(b"candidate-video")
            ledger = IterationBudgetLedger(
                database,
                scope_id=candidate_judge_module.ITERATION_SCOPE_ID,
                cap_microusd=candidate_judge_module.ITERATION_CAP_MICROUSD,
            )
            ledger.record_manual_charge(
                "candidate-semantic-cli-orphaned",
                1,
                "ambiguous provider outcome",
            )
            args = SimpleNamespace(
                concept=str(concept_path),
                storyboard=str(storyboard_path),
                concept_id=None,
                video=str(video),
                output=str(output),
                operation_id="candidate-semantic-cli-orphaned",
                model=candidate_judge_module.JUDGE_MODEL,
                budget_database=str(database),
                confirm_paid="YES",
            )

            with (
                mock.patch.object(candidate_judge_module, "parse_args", return_value=args),
                mock.patch.object(
                    candidate_judge_module, "_video_duration", return_value=15.0
                ),
                mock.patch.object(
                    candidate_judge_module, "GeminiCandidateJudge"
                ) as judge_class,
            ):
                with self.assertRaisesRegex(RuntimeError, "already manual_charge"):
                    candidate_judge_module.main()

            judge_class.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
