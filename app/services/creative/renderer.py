from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.services.creative.storyboard import (
    StoryboardScene,
    Storyboard,
    StoryboardValidationError,
    resolve_managed_asset,
    validate_storyboard,
)


_BACKGROUND = "#FFFCEE"
_TEXT = "#1D1E18"
_YELLOW = "#FFCC00"


@dataclass(frozen=True)
class RenderedCreative:
    video_path: Path
    subtitle_path: Path
    scene_card_paths: tuple[Path, ...]
    intermediate_video_paths: tuple[Path, ...]
    subtitles_burned: bool
    subtitle_layout_path: Path
    subtitle_layouts: tuple["SubtitleLayout", ...]


@dataclass(frozen=True)
class SubtitleLayout:
    scene_id: str
    overlay_path: Path
    x: int
    y: int
    width: int
    height: int
    canvas_width: int
    canvas_height: int
    safe_area_pass: bool
    suppressed: bool = False

    def to_record(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "overlay_path": str(self.overlay_path),
            "bbox": {
                "x": self.x,
                "y": self.y,
                "width": self.width,
                "height": self.height,
            },
            "canvas": {
                "width": self.canvas_width,
                "height": self.canvas_height,
            },
            "safe_area_pass": self.safe_area_pass,
            "suppressed": self.suppressed,
        }


def _font_path(name: str) -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "resource" / "fonts" / name


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "BeVietnamPro-Bold.ttf" if bold else "BeVietnamPro-Medium.ttf"
    return ImageFont.truetype(str(_font_path(name)), size=size)


def _contain(image: Image.Image, maximum: tuple[int, int]) -> Image.Image:
    return ImageOps.contain(image, maximum, method=Image.Resampling.LANCZOS)


def _paste_center(canvas: Image.Image, image: Image.Image, *, y: int) -> None:
    x = (canvas.width - image.width) // 2
    canvas.alpha_composite(image, (x, y))


def _draw_centered_text(
    canvas: Image.Image,
    text: str,
    *,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: str = _TEXT,
) -> None:
    draw = ImageDraw.Draw(canvas)
    box = draw.multiline_textbbox((0, 0), text, font=font, align="center")
    width = box[2] - box[0]
    draw.multiline_text(
        ((canvas.width - width) / 2, y),
        text,
        font=font,
        fill=fill,
        align="center",
        spacing=8,
    )


def _render_product_card(
    scene: StoryboardScene,
    *,
    asset_root: Path,
    size: tuple[int, int],
) -> Image.Image:
    product_layers = [
        layer for layer in scene.media_plan.overlays if layer.kind == "product_capture"
    ]
    if len(product_layers) != 1 or not product_layers[0].asset_id:
        raise StoryboardValidationError(
            "product_demo requires exactly one managed product_capture"
        )

    canvas = Image.new("RGBA", size, _BACKGROUND)
    _draw_centered_text(
        canvas,
        scene.onscreen_text,
        y=62,
        font=_font(42, bold=True),
    )
    source_path = resolve_managed_asset(asset_root, product_layers[0].asset_id)
    with Image.open(source_path) as source:
        screenshot = _contain(
            source.convert("RGBA"),
            (size[0] - 180, size[1] - 400),
        )

    frame_padding = 14
    phone = Image.new(
        "RGBA",
        (screenshot.width + frame_padding * 2, screenshot.height + frame_padding * 2),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(phone)
    draw.rounded_rectangle(
        (0, 0, phone.width - 1, phone.height - 1),
        radius=38,
        fill="#151515",
    )
    mask = Image.new("L", screenshot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, screenshot.width - 1, screenshot.height - 1),
        radius=28,
        fill=255,
    )
    phone.paste(screenshot, (frame_padding, frame_padding), mask)
    _paste_center(canvas, phone, y=154)
    return canvas


def _render_cta_card(
    scene: StoryboardScene,
    *,
    asset_root: Path,
    size: tuple[int, int],
) -> Image.Image:
    canvas = Image.new("RGBA", size, _BACKGROUND)
    logo_layer = next(
        (
            layer
            for layer in scene.media_plan.overlays
            if layer.kind == "brand_asset"
            and layer.asset_id
            and "logo" in layer.asset_id.lower()
        ),
        None,
    )
    mascot_layer = next(
        (
            layer
            for layer in scene.media_plan.overlays
            if layer.kind == "brand_asset"
            and layer.asset_id
            and "mascot" in layer.asset_id.lower()
        ),
        None,
    )
    if logo_layer is None or mascot_layer is None:
        raise StoryboardValidationError(
            "cta requires approved managed logo and mascot assets"
        )

    with Image.open(resolve_managed_asset(asset_root, logo_layer.asset_id or "")) as raw:
        logo = _contain(raw.convert("RGBA"), (430, 150))
    with Image.open(
        resolve_managed_asset(asset_root, mascot_layer.asset_id or "")
    ) as raw:
        mascot = _contain(raw.convert("RGBA"), (440, 500))

    _paste_center(canvas, logo, y=100)
    _paste_center(canvas, mascot, y=340)
    _draw_centered_text(
        canvas,
        scene.onscreen_text,
        y=900,
        font=_font(48, bold=True),
    )
    draw = ImageDraw.Draw(canvas)
    button_width = 390
    button_height = 86
    button_x = (size[0] - button_width) // 2
    button_y = 1030
    draw.rounded_rectangle(
        (button_x, button_y, button_x + button_width, button_y + button_height),
        radius=43,
        fill=_YELLOW,
    )
    button_text = "Create your trip"
    button_font = _font(26, bold=True)
    text_box = draw.textbbox((0, 0), button_text, font=button_font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    draw.text(
        (
            button_x + (button_width - text_width) / 2,
            button_y + (button_height - text_height) / 2 - text_box[1],
        ),
        button_text,
        font=button_font,
        fill=_TEXT,
    )
    return canvas


def render_brand_scene_card(
    scene: StoryboardScene,
    *,
    asset_root: Path,
    output_path: Path,
    size: tuple[int, int] = (720, 1280),
) -> Path:
    """Locally composite approved product/brand assets without generative redraw."""

    if scene.purpose == "product_demo":
        canvas = _render_product_card(scene, asset_root=asset_root, size=size)
    elif scene.purpose == "cta":
        canvas = _render_cta_card(scene, asset_root=asset_root, size=size)
    else:
        raise StoryboardValidationError(
            f"scene purpose {scene.purpose!r} has no deterministic brand renderer"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path.resolve()


def _srt_timestamp(seconds: float) -> str:
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def write_scene_subtitles(storyboard: Storyboard, output_path: Path) -> Path:
    """Create deterministic scene-level narration timings for the first slice."""

    storyboard = validate_storyboard(storyboard)
    blocks = []
    for index, scene in enumerate(storyboard.scenes, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{_srt_timestamp(scene.start_seconds)} --> "
                    f"{_srt_timestamp(scene.end_seconds)}",
                    scene.voiceover.strip(),
                ]
            )
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return output_path.resolve()


def _wrap_text_pixels(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font)
        if current and box[2] - box[0] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def render_subtitle_overlay(
    scene: StoryboardScene,
    *,
    output_path: Path,
    size: tuple[int, int] = (720, 1280),
) -> SubtitleLayout:
    """Render a measured subtitle overlay and prove its safe-area geometry."""

    canvas_width, canvas_height = size
    horizontal_safe_margin = round(canvas_width * 0.05)
    vertical_safe_margin = round(canvas_height * 0.05)
    maximum_box_height = round(canvas_height * 0.15)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    if scene.purpose == "cta" and scene.onscreen_text.strip():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        overlay.save(output_path, format="PNG", optimize=True)
        return SubtitleLayout(
            scene_id=scene.scene_id,
            overlay_path=output_path.resolve(),
            x=0,
            y=0,
            width=0,
            height=0,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            safe_area_pass=True,
            suppressed=True,
        )
    draw = ImageDraw.Draw(overlay)
    font = _font(27, bold=True)
    wrapped = _wrap_text_pixels(
        draw,
        scene.voiceover.strip(),
        font=font,
        max_width=canvas_width - (horizontal_safe_margin * 2) - 48,
    )
    if not wrapped:
        raise StoryboardValidationError(
            f"scene {scene.scene_id!r} has no subtitle text"
        )
    text_box = draw.multiline_textbbox(
        (0, 0),
        wrapped,
        font=font,
        align="center",
        spacing=7,
        stroke_width=1,
    )
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    pad_x = 20
    pad_y = 14
    box_width = text_width + pad_x * 2
    box_height = text_height + pad_y * 2
    x = (canvas_width - box_width) // 2
    y = canvas_height - vertical_safe_margin - box_height
    safe_area_pass = (
        x >= horizontal_safe_margin
        and y >= vertical_safe_margin
        and x + box_width <= canvas_width - horizontal_safe_margin
        and y + box_height <= canvas_height - vertical_safe_margin
        and box_height <= maximum_box_height
    )
    if not safe_area_pass:
        raise StoryboardValidationError(
            f"subtitle for scene {scene.scene_id!r} exceeds the portrait safe area"
        )
    draw.rounded_rectangle(
        (x, y, x + box_width, y + box_height),
        radius=18,
        fill=(29, 30, 24, 190),
    )
    draw.multiline_text(
        (canvas_width / 2, y + pad_y - text_box[1]),
        wrapped,
        font=font,
        fill="#FFFFFF",
        anchor="ma",
        align="center",
        spacing=7,
        stroke_width=1,
        stroke_fill=_TEXT,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(output_path, format="PNG", optimize=True)
    return SubtitleLayout(
        scene_id=scene.scene_id,
        overlay_path=output_path.resolve(),
        x=x,
        y=y,
        width=box_width,
        height=box_height,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        safe_area_pass=safe_area_pass,
    )


def _run_ffmpeg(arguments: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise StoryboardValidationError(f"mixed-media render failed: {detail[-2000:]}")


def _normalize_hook(
    source: Path,
    target: Path,
    *,
    duration_seconds: float,
    size: tuple[int, int],
) -> None:
    width, height = size
    _run_ffmpeg(
        [
            "-stream_loop",
            "-1",
            "-i",
            str(source),
            "-t",
            f"{duration_seconds:.3f}",
            "-an",
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps=30"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ]
    )


def _animate_card(
    source: Path,
    target: Path,
    *,
    duration_seconds: float,
    size: tuple[int, int],
) -> None:
    width, height = size
    _run_ffmpeg(
        [
            "-loop",
            "1",
            "-i",
            str(source),
            "-t",
            f"{duration_seconds:.3f}",
            "-an",
            "-vf",
            (
                "zoompan="
                "z='min(zoom+0.00025,1.035)':"
                "x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':"
                f"d=1:s={width}x{height}:fps=30,"
                "setsar=1,format=yuv420p"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ]
    )


def _concat_scenes(scene_paths: list[Path], target: Path) -> None:
    arguments: list[str] = []
    for scene_path in scene_paths:
        arguments.extend(["-i", str(scene_path)])
    inputs = "".join(f"[{index}:v]" for index in range(len(scene_paths)))
    arguments.extend(
        [
            "-filter_complex",
            f"{inputs}concat=n={len(scene_paths)}:v=1:a=0[v]",
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ]
    )
    _run_ffmpeg(arguments)


def _attach_audio(
    video_path: Path,
    audio_path: Path,
    target: Path,
    *,
    duration_seconds: float,
) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            "[1:a]apad[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-t",
            f"{duration_seconds:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(target),
        ]
    )


def _overlay_subtitle(
    video_path: Path,
    overlay_path: Path,
    target: Path,
    *,
    duration_seconds: float,
) -> None:
    _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-loop",
            "1",
            "-i",
            str(overlay_path),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto:shortest=1[v]",
            "-map",
            "[v]",
            "-t",
            f"{duration_seconds:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ]
    )


def render_mixed_media_video(
    storyboard: Storyboard,
    *,
    hook_video_path: Path,
    asset_root: Path,
    output_dir: Path,
    narration_audio_path: Path | None = None,
    size: tuple[int, int] = (720, 1280),
) -> RenderedCreative:
    """Render the first hook + exact product capture + deterministic CTA slice."""

    storyboard = validate_storyboard(storyboard)
    if [scene.purpose for scene in storyboard.scenes] != [
        "hook",
        "product_demo",
        "cta",
    ]:
        raise StoryboardValidationError(
            "the first mixed-media renderer requires hook, product_demo, and cta"
        )
    hook_video_path = hook_video_path.resolve()
    if not hook_video_path.is_file():
        raise StoryboardValidationError(
            f"hook video does not exist: {hook_video_path}"
        )

    render_dir = output_dir.resolve()
    render_dir.mkdir(parents=True, exist_ok=True)
    product_card = render_brand_scene_card(
        storyboard.scenes[1],
        asset_root=asset_root,
        output_path=render_dir / "product-demo-card.png",
        size=size,
    )
    cta_card = render_brand_scene_card(
        storyboard.scenes[2],
        asset_root=asset_root,
        output_path=render_dir / "cta-card.png",
        size=size,
    )
    subtitle_path = write_scene_subtitles(
        storyboard,
        render_dir / "storyboard.srt",
    )
    subtitle_layouts = tuple(
        render_subtitle_overlay(
            scene,
            output_path=render_dir / f"subtitle-{index:02d}-{scene.scene_id}.png",
            size=size,
        )
        for index, scene in enumerate(storyboard.scenes, start=1)
    )
    subtitle_layout_path = render_dir / "subtitle-layout.json"
    subtitle_layout_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "all_safe_area_pass": all(
                    layout.safe_area_pass for layout in subtitle_layouts
                ),
                "layouts": [layout.to_record() for layout in subtitle_layouts],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    hook_base_clip = render_dir / "01-hook-base.mp4"
    product_base_clip = render_dir / "02-product-demo-base.mp4"
    cta_base_clip = render_dir / "03-cta-base.mp4"
    _normalize_hook(
        hook_video_path,
        hook_base_clip,
        duration_seconds=(
            storyboard.scenes[0].end_seconds - storyboard.scenes[0].start_seconds
        ),
        size=size,
    )
    _animate_card(
        product_card,
        product_base_clip,
        duration_seconds=(
            storyboard.scenes[1].end_seconds - storyboard.scenes[1].start_seconds
        ),
        size=size,
    )
    _animate_card(
        cta_card,
        cta_base_clip,
        duration_seconds=(
            storyboard.scenes[2].end_seconds - storyboard.scenes[2].start_seconds
        ),
        size=size,
    )

    hook_clip = render_dir / "01-hook.mp4"
    product_clip = render_dir / "02-product-demo.mp4"
    cta_clip = render_dir / "03-cta.mp4"
    scene_clips = [hook_clip, product_clip, cta_clip]
    base_clips = [hook_base_clip, product_base_clip, cta_base_clip]
    for scene, base_clip, subtitle_layout, scene_clip in zip(
        storyboard.scenes,
        base_clips,
        subtitle_layouts,
        scene_clips,
        strict=True,
    ):
        _overlay_subtitle(
            base_clip,
            subtitle_layout.overlay_path,
            scene_clip,
            duration_seconds=scene.end_seconds - scene.start_seconds,
        )

    visual_path = render_dir / "visual-only.mp4"
    _concat_scenes([hook_clip, product_clip, cta_clip], visual_path)
    final_path = render_dir / "final.mp4"
    if narration_audio_path is not None:
        resolved_audio = narration_audio_path.resolve()
        if not resolved_audio.is_file():
            raise StoryboardValidationError(
                f"narration audio does not exist: {resolved_audio}"
            )
        _attach_audio(
            visual_path,
            resolved_audio,
            final_path,
            duration_seconds=storyboard.target_duration_seconds,
        )
    else:
        visual_path.replace(final_path)

    return RenderedCreative(
        video_path=final_path,
        subtitle_path=subtitle_path,
        scene_card_paths=(product_card, cta_card),
        intermediate_video_paths=(hook_clip, product_clip, cta_clip),
        subtitles_burned=True,
        subtitle_layout_path=subtitle_layout_path,
        subtitle_layouts=subtitle_layouts,
    )
