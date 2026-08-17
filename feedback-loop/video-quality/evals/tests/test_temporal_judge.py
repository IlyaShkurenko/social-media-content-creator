from __future__ import annotations

import sys
import unittest
from pathlib import Path


LOOP_ROOT = Path(__file__).resolve().parents[2]
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from evals.temporal_judge import (  # noqa: E402
    TemporalJudgeResponse,
    validate_provider_response_schema,
)


class TemporalJudgeSchemaTests(unittest.TestCase):
    def test_response_schema_is_supported_by_gemini_sdk(self) -> None:
        validate_provider_response_schema()

    def test_positive_frame_count_uses_inclusive_minimum(self) -> None:
        schema = TemporalJudgeResponse.model_json_schema()
        frame_count = schema["properties"]["inspected_frame_count"]

        self.assertEqual(frame_count["minimum"], 1)
        self.assertNotIn("exclusiveMinimum", frame_count)


if __name__ == "__main__":
    unittest.main()
