from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


LOOP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LOOP_ROOT / "scripts"))
import run_experiment


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


if __name__ == "__main__":
    unittest.main()
