from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services.creative.budget import IterationBudgetLedger
from app.services.creative.narration import (
    OPENAI_TTS_SCENE_WORST_CASE_MICROUSD,
    build_narration_plan,
    generate_scene_narration,
)
from app.services.creative.storyboard import StoryboardValidationError, validate_storyboard


REPO_ROOT = Path(__file__).resolve().parents[2]
STORYBOARD_PATH = (
    REPO_ROOT
    / "feedback-loop/video-quality/evals/dataset/mixed-media-first-slice-001.json"
)


def storyboard():
    return validate_storyboard(json.loads(STORYBOARD_PATH.read_text(encoding="utf-8")))


def test_story_1_3_narration_plan_is_english_despite_russian_interface() -> None:
    plan = build_narration_plan(storyboard(), interface_locale="ru-RU")
    assert plan.settings.content_language == "en-US"
    assert plan.settings.voice_name == "en-US-JennyNeural-Female"
    assert [item.duration_seconds for item in plan.scenes] == [5, 6, 4]
    assert "tict" in plan.scenes[1].display_text
    assert "tickt" in plan.scenes[1].spoken_text
    assert "tict" in plan.scenes[2].display_text
    assert "tickt" in plan.scenes[2].spoken_text


def test_story_1_1_scene_audio_is_padded_to_exact_storyboard_timing(
    tmp_path: Path,
) -> None:
    requested_voices: list[str] = []
    synthesized_texts: list[str] = []

    def fake_synthesizer(
        text: str, voice_name: str, output_path: Path, instructions: str | None
    ) -> bool:
        del instructions
        synthesized_texts.append(text)
        requested_voices.append(voice_name)
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "1",
                "-c:a",
                "libmp3lame",
                str(output_path),
            ],
            check=True,
        )
        return True

    artifacts = generate_scene_narration(
        storyboard(),
        output_dir=tmp_path / "narration",
        interface_locale="ru-RU",
        synthesizer=fake_synthesizer,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(artifacts.audio_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert 14.9 <= float(probe.stdout.strip()) <= 15.1
    assert requested_voices == ["en-US-JennyNeural-Female"] * 3
    assert "tickt" in synthesized_texts[1]
    assert "tickt" in synthesized_texts[2]
    assert all("TICT" not in text for text in synthesized_texts)
    assert artifacts.subtitle_path.is_file()
    subtitle_text = artifacts.subtitle_path.read_text(encoding="utf-8")
    assert "tict" in subtitle_text
    assert "tickt" not in subtitle_text


def storyboard_with_hook_instructions(text: str):
    payload = json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    payload["scenes"][0]["voice_instructions"] = text
    return validate_storyboard(payload)


def _write_silent_clip(output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "1",
            "-c:a",
            "libmp3lame",
            str(output_path),
        ],
        check=True,
    )


def test_voice_1_1_scene_instructions_are_threaded_to_the_synthesizer(
    tmp_path: Path,
) -> None:
    captured_instructions: list[str | None] = []

    def fake_synthesizer(
        text: str, voice_name: str, output_path: Path, instructions: str | None
    ) -> bool:
        captured_instructions.append(instructions)
        _write_silent_clip(output_path)
        return True

    generate_scene_narration(
        storyboard_with_hook_instructions("A genuinely confused person."),
        output_dir=tmp_path / "narration",
        synthesizer=fake_synthesizer,
    )

    assert captured_instructions[0] == "A genuinely confused person."
    assert captured_instructions[1] is None
    assert captured_instructions[2] is None


def test_voice_1_2_paid_voice_without_ledger_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(StoryboardValidationError, match="budget_ledger"):
        generate_scene_narration(
            storyboard(),
            output_dir=tmp_path / "narration",
            requested_voice="openai:cedar",
        )


def test_voice_1_2_paid_voice_charges_ledger_exactly_once_per_scene(
    tmp_path: Path,
) -> None:
    def fake_synthesizer(
        text: str, voice_name: str, output_path: Path, instructions: str | None
    ) -> bool:
        _write_silent_clip(output_path)
        return True

    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="narration-tests",
        cap_microusd=10_000_000,
    )

    generate_scene_narration(
        storyboard(),
        output_dir=tmp_path / "narration",
        requested_voice="openai:cedar",
        synthesizer=fake_synthesizer,
        budget_ledger=ledger,
        operation_prefix="narration-test-001",
    )

    snapshot = ledger.snapshot()
    assert snapshot.charged_microusd == OPENAI_TTS_SCENE_WORST_CASE_MICROUSD * 3
    assert snapshot.reserved_microusd == 0
    # Retrying under the same prefix must not resubmit an already-charged scene.
    with pytest.raises(Exception):
        ledger.record_manual_charge(
            "narration-test-001-hook", 1, "duplicate charge attempt"
        )


def test_voice_1_2_paid_voice_charges_even_when_synthesis_fails(
    tmp_path: Path,
) -> None:
    def failing_synthesizer(
        text: str, voice_name: str, output_path: Path, instructions: str | None
    ) -> bool:
        return False

    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="narration-tests",
        cap_microusd=10_000_000,
    )

    with pytest.raises(StoryboardValidationError, match="synthesis failed"):
        generate_scene_narration(
            storyboard(),
            output_dir=tmp_path / "narration",
            requested_voice="openai:cedar",
            synthesizer=failing_synthesizer,
            budget_ledger=ledger,
            operation_prefix="narration-test-002",
        )

    # Fail-closed: the ambiguous provider outcome is still charged.
    assert ledger.snapshot().charged_microusd == OPENAI_TTS_SCENE_WORST_CASE_MICROUSD


def test_voice_1_2_free_voice_never_touches_the_ledger(tmp_path: Path) -> None:
    def fake_synthesizer(
        text: str, voice_name: str, output_path: Path, instructions: str | None
    ) -> bool:
        _write_silent_clip(output_path)
        return True

    ledger = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="narration-tests",
        cap_microusd=10_000_000,
    )

    generate_scene_narration(
        storyboard(),
        output_dir=tmp_path / "narration",
        synthesizer=fake_synthesizer,
        budget_ledger=ledger,
        operation_prefix="unused-prefix",
    )

    assert ledger.snapshot().charged_microusd == 0
