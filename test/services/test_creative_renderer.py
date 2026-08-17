from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image

from app.services.creative.renderer import (
    render_brand_scene_card,
    render_mixed_media_video,
    render_subtitle_overlay,
    write_scene_subtitles,
)
from app.services.creative.storyboard import validate_storyboard


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = REPO_ROOT / "feedback-loop/video-quality/evals/assets/brand"
STORYBOARD_PATH = (
    REPO_ROOT
    / "feedback-loop/video-quality/evals/dataset/mixed-media-first-slice-001.json"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_brand_1_1_product_card_uses_exact_source_without_mutating_it(
    tmp_path: Path,
) -> None:
    storyboard = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    source = ASSET_ROOT / "screens/Plan_Overview_Screen.png"
    original_digest = digest(source)
    output = render_brand_scene_card(
        storyboard.scenes[1],
        asset_root=ASSET_ROOT,
        output_path=tmp_path / "product-demo.png",
        size=(720, 1280),
    )
    assert output.is_file()
    assert Image.open(output).size == (720, 1280)
    assert digest(source) == original_digest


def test_brand_1_1_cta_card_uses_approved_logo_and_mascot(tmp_path: Path) -> None:
    storyboard = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    output = render_brand_scene_card(
        storyboard.scenes[2],
        asset_root=ASSET_ROOT,
        output_path=tmp_path / "cta.png",
        size=(720, 1280),
    )
    with Image.open(output) as image:
        assert image.size == (720, 1280)
        assert image.getbbox() is not None


def test_adpipe_1_3_renderer_creates_exact_fifteen_second_portrait_video(
    tmp_path: Path,
) -> None:
    hook = tmp_path / "hook.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#335577:s=360x640:r=30:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(hook),
        ],
        check=True,
    )
    model = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    rendered = render_mixed_media_video(
        model,
        hook_video_path=hook,
        asset_root=ASSET_ROOT,
        output_dir=tmp_path / "render",
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(rendered.video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(probe.stdout)
    video_stream = next(
        stream for stream in metadata["streams"] if stream["codec_type"] == "video"
    )
    assert int(video_stream["width"]) == 720
    assert int(video_stream["height"]) == 1280
    assert 14.9 <= float(metadata["format"]["duration"]) <= 15.1
    assert rendered.subtitles_burned is True


def test_story_1_1_scene_subtitles_follow_storyboard_timestamps(tmp_path: Path) -> None:
    model = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    subtitle = write_scene_subtitles(model, tmp_path / "storyboard.srt")
    content = subtitle.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:05,000" in content
    assert "00:00:05,000 --> 00:00:11,000" in content
    assert "00:00:11,000 --> 00:00:15,000" in content
    assert "Planning a trip should not feel like another job." in content


def test_eval_2_3_subtitle_overlay_stays_inside_safe_area(tmp_path: Path) -> None:
    model = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    layout = render_subtitle_overlay(
        model.scenes[1],
        output_path=tmp_path / "subtitle.png",
        size=(720, 1280),
    )
    assert layout.safe_area_pass is True
    assert layout.height <= 192
    assert layout.x >= 36
    assert layout.y + layout.height <= 1280 - 64
    assert layout.y >= 1080


def test_eval_2_3_cta_subtitle_is_suppressed_when_cta_is_already_visible(
    tmp_path: Path,
) -> None:
    model = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    layout = render_subtitle_overlay(
        model.scenes[2],
        output_path=tmp_path / "cta-subtitle.png",
        size=(720, 1280),
    )
    assert layout.safe_area_pass is True
    assert layout.suppressed is True
    assert layout.width == 0
    assert layout.height == 0
