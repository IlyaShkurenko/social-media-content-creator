from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from google.genai import errors


LOOP_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = LOOP_ROOT.parents[1]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from evals.audio_judge import (  # noqa: E402
    EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    GeminiAudioJudge,
    build_pairwise_audio_prompt,
    round_robin_compare,
)


def _response_payload(*, overall_winner: str = "B") -> dict:
    return {
        "transcription_a": "You saved fifty spots on the map.",
        "transcription_b": "You saved fifty spots on the map.",
        "naturalness": {"winner": overall_winner, "reason": "Clearer human cadence."},
        "emotional_match": {"winner": overall_winner, "reason": "Matches confusion."},
        "ending_consistency": {
            "winner": overall_winner,
            "reason": "Holds tone through the last word.",
        },
        "overall_winner": overall_winner,
        "overall_reason": "Consistently more natural and holds emotion to the end.",
    }


def _fake_audio(root: Path, name: str, *, frequency: int = 440) -> Path:
    """A tiny valid, ffprobe-readable MP3. Frequency varies content/hash per
    clip — two silence clips built from identical parameters hash identical,
    which the real code correctly treats as a self-comparison tie."""
    path = root / name
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=44100",
            "-t",
            "1",
            "-c:a",
            "libmp3lame",
            str(path),
        ],
        check=True,
    )
    return path


class _FakeModels:
    def __init__(
        self,
        *,
        fail: bool = False,
        api_error_code: int | None = None,
        winner_by_call: list[str] | None = None,
    ) -> None:
        self.fail = fail
        self.api_error_code = api_error_code
        self.winner_by_call = winner_by_call
        self.calls = 0

    def generate_content(self, **kwargs) -> SimpleNamespace:
        self.calls += 1
        if self.api_error_code is not None:
            error_payload = {"error": {"message": "provider rejection"}}
            error_type = (
                errors.ClientError if self.api_error_code < 500 else errors.ServerError
            )
            raise error_type(self.api_error_code, error_payload)
        if self.fail:
            raise ConnectionError("ambiguous transport failure")
        winner = "B"
        if self.winner_by_call is not None:
            winner = self.winner_by_call[(self.calls - 1) % len(self.winner_by_call)]
        return SimpleNamespace(
            parsed=_response_payload(overall_winner=winner),
            response_id=f"audio-response-{self.calls}",
            model_version="gemini-3.6-flash-001",
            usage_metadata=SimpleNamespace(
                prompt_token_count=400, candidates_token_count=250
            ),
        )


class _FakeClient:
    def __init__(
        self,
        *,
        fail: bool = False,
        api_error_code: int | None = None,
        winner_by_call: list[str] | None = None,
    ) -> None:
        self.models = _FakeModels(
            fail=fail, api_error_code=api_error_code, winner_by_call=winner_by_call
        )


class GeminiAudioJudgeTests(unittest.TestCase):
    def _judge(
        self, root: Path, client: _FakeClient
    ) -> tuple[GeminiAudioJudge, IterationBudgetLedger]:
        ledger = IterationBudgetLedger(
            root / "budget.sqlite3",
            scope_id="audio-judge-tests",
            cap_microusd=10_000_000,
        )
        return (
            GeminiAudioJudge(api_key="fake-key", budget_ledger=ledger, client=client),
            ledger,
        )

    def test_prompt_asks_for_ending_consistency_between_two_clips(self) -> None:
        """[EVAL-8.1] The prompt compares A/B, not one clip in isolation."""
        prompt = build_pairwise_audio_prompt(
            spoken_text="hello", target_emotion="Confused"
        )
        self.assertIn("CLIP A", prompt)
        self.assertIn("CLIP B", prompt)
        self.assertIn("ending_consistency", prompt)
        self.assertIn("trailing off", prompt)

    def test_position_balanced_win_is_detected_regardless_of_slot(self) -> None:
        """[EVAL-8.1] A clip preferred in both reversed passes yields full credit."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip_a = _fake_audio(root, "a.mp3", frequency=330)
            clip_b = _fake_audio(root, "b.mp3", frequency=660)
            # Pass 1 order is (A=clip_a, B=clip_b); pass 2 swaps to
            # (A=clip_b, B=clip_a). "B" then "A" always names the *physical*
            # clip_b file as winner regardless of its slot — a real
            # preference, not positional bias (which "B" then "B" would be).
            client = _FakeClient(winner_by_call=["B", "A"])
            judge, ledger = self._judge(root, client)

            evidence = judge.compare(
                clip_a=clip_a,
                clip_b=clip_b,
                spoken_text="You saved fifty spots on the map.",
                target_emotion="Confused",
                operation_prefix="test-pair-001",
            )
            snapshot = ledger.snapshot()

        self.assertEqual(evidence["schema_version"], EVIDENCE_SCHEMA_VERSION)
        self.assertEqual(evidence["evaluator_version"], EVALUATOR_VERSION)
        diagnostic = evidence["clip_b_credit_diagnostic"]
        self.assertTrue(diagnostic["position_balanced"])
        self.assertEqual(diagnostic["clip_b_credit"], 1.0)
        self.assertEqual(diagnostic["outcome"], "clip_b")
        self.assertGreater(snapshot.charged_microusd, 0)
        self.assertEqual(client.models.calls, 2)

    def test_split_decision_across_passes_is_a_tie(self) -> None:
        """A clip winning only because of its slot cancels out to a tie."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip_a = _fake_audio(root, "a.mp3", frequency=330)
            clip_b = _fake_audio(root, "b.mp3", frequency=660)
            # "A" wins every call regardless of which physical file is A —
            # pure positional bias, must cancel to a tie, not a real winner.
            client = _FakeClient(winner_by_call=["A", "A"])
            judge, _ = self._judge(root, client)

            evidence = judge.compare(
                clip_a=clip_a,
                clip_b=clip_b,
                spoken_text="hello",
                target_emotion="Confused",
                operation_prefix="test-pair-002",
            )

        diagnostic = evidence["clip_b_credit_diagnostic"]
        self.assertEqual(diagnostic["clip_b_credit"], 0.5)
        self.assertEqual(diagnostic["outcome"], "tie")

    def test_explicit_api_rejection_does_not_charge_or_retry(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip_a = _fake_audio(root, "a.mp3")
            clip_b = _fake_audio(root, "b.mp3")
            client = _FakeClient(api_error_code=400)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "no charge was recorded"):
                judge.compare(
                    clip_a=clip_a,
                    clip_b=clip_b,
                    spoken_text="hello",
                    target_emotion="Confused",
                    operation_prefix="test-pair-003",
                )
            snapshot = ledger.snapshot()

        self.assertEqual(snapshot.charged_microusd, 0)
        self.assertEqual(client.models.calls, 1)

    def test_ambiguous_error_charges_first_pass_and_blocks_the_pair(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip_a = _fake_audio(root, "a.mp3")
            clip_b = _fake_audio(root, "b.mp3")
            client = _FakeClient(api_error_code=503)
            judge, ledger = self._judge(root, client)

            with self.assertRaisesRegex(RuntimeError, "worst-case charge"):
                judge.compare(
                    clip_a=clip_a,
                    clip_b=clip_b,
                    spoken_text="hello",
                    target_emotion="Confused",
                    operation_prefix="test-pair-004",
                )
            first_charge = ledger.snapshot().charged_microusd
            self.assertGreater(first_charge, 0)

            with self.assertRaisesRegex(RuntimeError, "will not be retried"):
                judge.compare(
                    clip_a=clip_a,
                    clip_b=clip_b,
                    spoken_text="hello",
                    target_emotion="Confused",
                    operation_prefix="test-pair-004",
                )

            self.assertEqual(ledger.snapshot().charged_microusd, first_charge)
            self.assertEqual(client.models.calls, 1)

    def test_round_robin_ranks_three_clips_from_pairwise_results(self) -> None:
        """[EVAL-8.1] N>2 clips run every unique pair, not one fixed anchor."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clips = [
                ("azure", _fake_audio(root, "azure.mp3", frequency=220)),
                ("v2", _fake_audio(root, "v2.mp3", frequency=440)),
                ("v3", _fake_audio(root, "v3.mp3", frequency=880)),
            ]
            # ["B", "A"] cycling always names the physical *second* clip of
            # each pair as winner (see the position-balance test above for
            # why "B" then "A" is a real preference, not positional bias).
            # combinations() yields (azure,v2), (azure,v3), (v2,v3), so v2
            # beats azure, v3 beats azure, and v3 beats v2 — v3 > v2 > azure.
            client = _FakeClient(winner_by_call=["B", "A"])
            judge, ledger = self._judge(root, client)

            result = round_robin_compare(
                judge,
                clips,
                spoken_text="You saved fifty spots on the map.",
                target_emotion="Confused",
                operation_prefix_base="test-roundrobin",
            )

            self.assertEqual(result["pairs_judged"], 3)
            self.assertEqual(client.models.calls, 6)  # 3 pairs x 2 reversed passes
            labels_by_rank = [row["label"] for row in result["leaderboard"]]
            self.assertEqual(labels_by_rank, ["v3", "v2", "azure"])
            self.assertGreater(ledger.snapshot().charged_microusd, 0)

    def test_round_robin_rejects_duplicate_labels(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            clip = _fake_audio(root, "a.mp3")
            client = _FakeClient()
            judge, _ = self._judge(root, client)

            with self.assertRaises(ValueError):
                round_robin_compare(
                    judge,
                    [("same", clip), ("same", clip)],
                    spoken_text="hello",
                    target_emotion="Confused",
                    operation_prefix_base="test-roundrobin-dup",
                )


if __name__ == "__main__":
    unittest.main()
