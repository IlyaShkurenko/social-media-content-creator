from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "feedback-loop/video-quality/scripts/run_mixed_media.py"


def test_adpipe_1_4_cli_dry_run_prepares_without_paid_submission(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(SCRIPT),
            "--output",
            str(tmp_path / "prepared"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"paid_submission": false' in result.stdout
    assert (tmp_path / "prepared/manifest.json").is_file()
    assert not (tmp_path / "prepared/budget.sqlite3").exists()


def test_runway_1_2_cli_requires_explicit_paid_confirmation(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(SCRIPT),
            "--output",
            str(tmp_path / "prepared"),
            "--execute-runway",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--confirm-paid YES" in result.stderr


def test_runway_1_5_rerender_requires_existing_provider_provenance(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/python"),
            str(SCRIPT),
            "--output",
            str(tmp_path / "prepared"),
            "--hook-video",
            str(tmp_path / "already-generated.mp4"),
            "--variant-id",
            "runway-candidate",
            "--narrate",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "existing paid run-summary.json" in result.stderr
