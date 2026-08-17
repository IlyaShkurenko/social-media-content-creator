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
