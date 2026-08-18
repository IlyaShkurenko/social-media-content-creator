from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


LOOP_ROOT = Path(__file__).resolve().parents[2]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from evals.temporal_judge import (  # noqa: E402
    EVALUATOR_VERSION,
    FRAMES_PER_STRIP,
    MAXIMUM_COST_MICROUSD,
    TEMPORAL_SAMPLE_FPS,
    TEMPORAL_SCHEMA_VERSION,
    TemporalJudgeResponse,
    build_temporal_prompt,
    validate_provider_response_schema,
    validate_existing_temporal_evidence,
)
from evals.gemini_judge import sha256_file, sha256_text  # noqa: E402
from app.services.creative.budget import IterationBudgetLedger  # noqa: E402


class TemporalJudgeSchemaTests(unittest.TestCase):
    def test_response_schema_is_supported_by_gemini_sdk(self) -> None:
        validate_provider_response_schema()

    def test_positive_frame_count_uses_inclusive_minimum(self) -> None:
        schema = TemporalJudgeResponse.model_json_schema()
        frame_count = schema["properties"]["inspected_frame_count"]

        self.assertEqual(frame_count["minimum"], 1)
        self.assertNotIn("exclusiveMinimum", frame_count)

    def test_complete_paid_checkpoint_replays_without_provider(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "hook.mp4"
            video.write_bytes(b"video")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )
            operation_id = "temporal-hook-1"
            ledger.record_manual_charge(
                operation_id,
                18_000,
                "Completed temporal response",
            )
            description = "airport; traveller turns a phone; push-in"
            frame_count = 5 * TEMPORAL_SAMPLE_FPS
            response = {
                "summary": "No discontinuity is visible.",
                "inspected_frame_count": frame_count,
                "events": [],
            }
            evidence = {
                "schema_version": TEMPORAL_SCHEMA_VERSION,
                "evaluator_version": EVALUATOR_VERSION,
                "observation_mode": "gemini_temporal_strips_v1",
                "status": "complete",
                "video_sha256": sha256_file(video),
                "scene_id": "hook",
                "scene_range_seconds": [0.0, 5.0],
                "sample_fps": TEMPORAL_SAMPLE_FPS,
                "sampled_frame_count": frame_count,
                "strip_hashes": [
                    f"{index:064x}"
                    for index in range(
                        (frame_count + FRAMES_PER_STRIP - 1) // FRAMES_PER_STRIP
                    )
                ],
                "prompt_sha256": sha256_text(
                    build_temporal_prompt(
                        scene_id="hook",
                        scene_description=description,
                        sample_fps=TEMPORAL_SAMPLE_FPS,
                        frame_count=frame_count,
                    )
                ),
                "requested_model": "gemini-3.6-flash",
                "model_version": "gemini-3.6-flash-001",
                "response": response,
                "events": [],
                "provider": {"response_id": "response-1"},
                "budget": {
                    "operation_id": operation_id,
                    "preflight_maximum_microusd": MAXIMUM_COST_MICROUSD,
                    "charged_microusd": 18_000,
                    "remaining_scope_microusd": 9_982_000,
                },
            }

            replayed = validate_existing_temporal_evidence(
                evidence,
                video_path=video,
                scene_id="hook",
                scene_description=description,
                start_seconds=0.0,
                end_seconds=5.0,
                operation_id=operation_id,
                model="gemini-3.6-flash",
                budget_ledger=ledger,
            )

            self.assertEqual(replayed, evidence)

    def test_checkpoint_with_wrong_video_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "hook.mp4"
            video.write_bytes(b"video")
            ledger = IterationBudgetLedger(
                root / "budget.sqlite3",
                scope_id="iteration-001",
                cap_microusd=10_000_000,
            )

            with self.assertRaisesRegex(ValueError, "video_sha256"):
                validate_existing_temporal_evidence(
                    {
                        "schema_version": TEMPORAL_SCHEMA_VERSION,
                        "evaluator_version": EVALUATOR_VERSION,
                        "observation_mode": "gemini_temporal_strips_v1",
                        "status": "complete",
                        "video_sha256": "0" * 64,
                    },
                    video_path=video,
                    scene_id="hook",
                    scene_description="description",
                    start_seconds=0.0,
                    end_seconds=5.0,
                    operation_id="missing",
                    model="gemini-3.6-flash",
                    budget_ledger=ledger,
                )


if __name__ == "__main__":
    unittest.main()
