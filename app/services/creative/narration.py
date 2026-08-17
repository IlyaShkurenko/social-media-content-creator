from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.services.creative.renderer import write_scene_subtitles
from app.services.creative.storyboard import (
    NarrationSettings,
    Storyboard,
    StoryboardValidationError,
    resolve_narration_voice,
    validate_storyboard,
)


Synthesizer = Callable[[str, str, Path], bool]


@dataclass(frozen=True)
class SceneNarration:
    scene_id: str
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    text: str


@dataclass(frozen=True)
class NarrationPlan:
    settings: NarrationSettings
    scenes: tuple[SceneNarration, ...]


@dataclass(frozen=True)
class NarrationArtifacts:
    audio_path: Path
    subtitle_path: Path
    raw_scene_audio_paths: tuple[Path, ...]
    fitted_scene_audio_paths: tuple[Path, ...]
    plan: NarrationPlan


def build_narration_plan(
    storyboard: Storyboard,
    *,
    requested_voice: str | None = None,
    interface_locale: str | None = None,
) -> NarrationPlan:
    storyboard = validate_storyboard(storyboard)
    settings = resolve_narration_voice(
        content_language=storyboard.content_language,
        requested_voice=requested_voice,
        interface_locale=interface_locale,
    )
    return NarrationPlan(
        settings=settings,
        scenes=tuple(
            SceneNarration(
                scene_id=scene.scene_id,
                start_seconds=scene.start_seconds,
                end_seconds=scene.end_seconds,
                duration_seconds=scene.end_seconds - scene.start_seconds,
                text=scene.voiceover.strip(),
            )
            for scene in storyboard.scenes
        ),
    )


def _run(command: list[str], *, label: str) -> None:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise StoryboardValidationError(f"{label} failed: {detail[-2000:]}")


def _audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise StoryboardValidationError(
            f"cannot determine narration duration for {path.name!r}"
        ) from exc
    if result.returncode != 0 or duration <= 0:
        raise StoryboardValidationError(
            f"invalid narration audio for {path.name!r}"
        )
    return duration


def _default_synthesizer(text: str, voice_name: str, output_path: Path) -> bool:
    from app.services import voice

    return (
        voice.tts(
            text=text,
            voice_name=voice_name,
            voice_rate=1.0,
            voice_file=str(output_path),
            voice_volume=1.0,
        )
        is not None
    )


def _fit_scene_audio(source: Path, target: Path, duration_seconds: float) -> None:
    source_duration = _audio_duration(source)
    speed = max(1.0, source_duration / duration_seconds)
    if speed > 1.25:
        raise StoryboardValidationError(
            f"narration for {source.name!r} is too long for its scene "
            f"({source_duration:.2f}s > {duration_seconds:.2f}s at safe speed)"
        )
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-filter:a",
            f"atempo={speed:.8f},apad",
            "-t",
            f"{duration_seconds:.3f}",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(target),
        ],
        label="scene narration fitting",
    )


def _concat_audio(sources: list[Path], target: Path) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for source in sources:
        command.extend(["-i", str(source)])
    inputs = "".join(f"[{index}:a]" for index in range(len(sources)))
    command.extend(
        [
            "-filter_complex",
            f"{inputs}concat=n={len(sources)}:v=0:a=1[a]",
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(target),
        ]
    )
    _run(command, label="narration concatenation")


def generate_scene_narration(
    storyboard: Storyboard,
    *,
    output_dir: Path,
    requested_voice: str | None = None,
    interface_locale: str | None = None,
    synthesizer: Synthesizer | None = None,
) -> NarrationArtifacts:
    """Synthesize one English line per scene and fit it to the declared timeline."""

    storyboard = validate_storyboard(storyboard)
    plan = build_narration_plan(
        storyboard,
        requested_voice=requested_voice,
        interface_locale=interface_locale,
    )
    synthesize = synthesizer or _default_synthesizer
    narration_dir = output_dir.resolve()
    narration_dir.mkdir(parents=True, exist_ok=True)

    raw_paths: list[Path] = []
    fitted_paths: list[Path] = []
    for index, scene in enumerate(plan.scenes, start=1):
        raw_path = narration_dir / f"{index:02d}-{scene.scene_id}-raw.mp3"
        fitted_path = narration_dir / f"{index:02d}-{scene.scene_id}.m4a"
        if not scene.text:
            raise StoryboardValidationError(
                f"scene {scene.scene_id!r} has no narration text"
            )
        if not synthesize(scene.text, plan.settings.voice_name, raw_path):
            raise StoryboardValidationError(
                f"narration synthesis failed for scene {scene.scene_id!r}"
            )
        if not raw_path.is_file() or raw_path.stat().st_size <= 0:
            raise StoryboardValidationError(
                f"narration synthesizer produced no audio for {scene.scene_id!r}"
            )
        _fit_scene_audio(raw_path, fitted_path, scene.duration_seconds)
        raw_paths.append(raw_path)
        fitted_paths.append(fitted_path)

    audio_path = narration_dir / "narration.m4a"
    _concat_audio(fitted_paths, audio_path)
    subtitle_path = write_scene_subtitles(
        storyboard,
        narration_dir / "storyboard.srt",
    )
    return NarrationArtifacts(
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        raw_scene_audio_paths=tuple(raw_paths),
        fitted_scene_audio_paths=tuple(fitted_paths),
        plan=plan,
    )
