from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.services.creative.narration import (
    build_narration_plan,
    generate_scene_narration,
)
from app.services.creative.storyboard import validate_storyboard


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

    def fake_synthesizer(text: str, voice_name: str, output_path: Path) -> bool:
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
