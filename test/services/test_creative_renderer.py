from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from PIL import Image

from app.services.creative.renderer import (
    measure_cta_layout,
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


def test_brand_1_6_cta_layout_is_measured_and_balanced_at_portrait_sizes() -> None:
    storyboard = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )

    for size in ((720, 1280), (1080, 1920)):
        layout = measure_cta_layout(
            storyboard.scenes[2],
            asset_root=ASSET_ROOT,
            size=size,
        )
        elements = (layout.logo, layout.hero, layout.headline, layout.action)
        assert all(layout.safe_area.contains(element) for element in elements)
        assert all(abs(element.center_x - size[0] / 2) <= 1 for element in elements)
        assert all(
            first.bottom < second.top
            for first, second in zip(elements, elements[1:])
        )
        assert abs(layout.stack.center_y - size[1] / 2) <= size[1] * 0.02
        assert layout.action_font_size >= round(size[0] * 0.04)


def test_brand_1_6_cta_honors_storyboard_semantic_regions() -> None:
    storyboard = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )

    layout = measure_cta_layout(
        storyboard.scenes[2],
        asset_root=ASSET_ROOT,
        size=(720, 1280),
    )

    assert layout.logo.top == layout.safe_area.top
    assert abs(layout.hero.center_y - 1280 / 2) <= 1
    assert layout.action.bottom == layout.safe_area.bottom


def test_brand_1_6_cta_honors_llm_selected_horizontal_alignment() -> None:
    payload = json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    elements = payload["scenes"][2]["layout_intent"]["elements"]
    logo_intent = next(item for item in elements if item["element_id"] == "logo")
    logo_intent["horizontal_alignment"] = "left"
    storyboard = validate_storyboard(payload)

    layout = measure_cta_layout(
        storyboard.scenes[2],
        asset_root=ASSET_ROOT,
        size=(720, 1280),
    )

    assert layout.logo.x == layout.safe_area.x


def test_brand_1_6_cta_action_copy_belongs_to_storyboard() -> None:
    storyboard = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    custom_copy = "Explore your next journey"
    scene = storyboard.scenes[2].model_copy(
        update={"call_to_action": custom_copy}
    )

    layout = measure_cta_layout(
        scene,
        asset_root=ASSET_ROOT,
        size=(720, 1280),
    )

    assert layout.action_text == custom_copy
    assert layout.action.width <= layout.safe_area.width
    assert layout.action_font_size >= round(720 * 0.04)


def test_brand_1_6_cta_layout_accepts_alternate_hero_and_longer_copy() -> None:
    storyboard = validate_storyboard(
        json.loads(STORYBOARD_PATH.read_text(encoding="utf-8"))
    )
    scene = storyboard.scenes[2]
    alternate_layers = [
        (
            layer.model_copy(update={"asset_id": "tict-mascot-2.png"})
            if layer.role == "hero"
            else layer
        )
        for layer in scene.media_plan.overlays
    ]
    alternate_scene = scene.model_copy(
        update={
            "onscreen_text": "Turn scattered travel plans into one clear journey.",
            "call_to_action": "Explore your complete trip plan",
            "media_plan": scene.media_plan.model_copy(
                update={"overlays": alternate_layers}
            ),
        }
    )

    layout = measure_cta_layout(
        alternate_scene,
        asset_root=ASSET_ROOT,
        size=(1080, 1920),
    )

    elements = (layout.logo, layout.hero, layout.headline, layout.action)
    assert all(layout.safe_area.contains(element) for element in elements)
    assert all(
        first.bottom < second.top
        for first, second in zip(elements, elements[1:])
    )
    assert layout.action_text == "Explore your complete trip plan"


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
