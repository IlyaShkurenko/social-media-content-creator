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


@dataclass(frozen=True)
class LayoutBox:
    x: int
    y: int
    width: int
    height: int

    @property
    def top(self) -> int:
        return self.y

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    def contains(self, other: "LayoutBox") -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )


@dataclass(frozen=True)
class CtaLayout:
    canvas_width: int
    canvas_height: int
    safe_area: LayoutBox
    stack: LayoutBox
    logo: LayoutBox
    hero: LayoutBox
    headline: LayoutBox
    action: LayoutBox
    headline_text: str
    action_text: str
    headline_font_size: int
    action_font_size: int


@dataclass(frozen=True)
class _PreparedCtaLayout:
    layout: CtaLayout
    logo_image: Image.Image
    hero_image: Image.Image
    headline_font: ImageFont.FreeTypeFont
    headline_wrapped: str
    headline_text_bbox: tuple[int, int, int, int]
    action_font: ImageFont.FreeTypeFont
    action_text_bbox: tuple[int, int, int, int]


def _font_path(name: str) -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "resource" / "fonts" / name


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "BeVietnamPro-Bold.ttf" if bold else "BeVietnamPro-Medium.ttf"
    return ImageFont.truetype(str(_font_path(name)), size=size)


def _contain(image: Image.Image, maximum: tuple[int, int]) -> Image.Image:
    return ImageOps.contain(image, maximum, method=Image.Resampling.LANCZOS)


def _trim_transparent(image: Image.Image) -> Image.Image:
    alpha_bounds = image.getchannel("A").getbbox()
    if alpha_bounds is None:
        raise StoryboardValidationError("brand asset has no visible pixels")
    return image.crop(alpha_bounds)


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


def _brand_layer_for_role(
    scene: StoryboardScene,
    role: str,
    *,
    legacy_name_fragment: str,
):
    explicit = next(
        (
            layer
            for layer in scene.media_plan.overlays
            if layer.kind == "brand_asset"
            and layer.asset_id
            and layer.role == role
        ),
        None,
    )
    if explicit is not None:
        return explicit
    return next(
        (
            layer
            for layer in scene.media_plan.overlays
            if layer.kind == "brand_asset"
            and layer.asset_id
            and legacy_name_fragment in layer.asset_id.lower()
        ),
        None,
    )


def _wrap_text_for_width(
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
        bounds = draw.textbbox((0, 0), candidate, font=font)
        if current and bounds[2] - bounds[0] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def _fit_text_block(
    text: str,
    *,
    max_width: int,
    max_height: int,
    maximum_font_size: int,
    minimum_font_size: int,
    multiline: bool,
) -> tuple[ImageFont.FreeTypeFont, str, tuple[int, int, int, int]]:
    measurement = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
    for font_size in range(maximum_font_size, minimum_font_size - 1, -1):
        font = _font(font_size, bold=True)
        wrapped = (
            _wrap_text_for_width(
                measurement,
                text,
                font=font,
                max_width=max_width,
            )
            if multiline
            else text
        )
        bounds = measurement.multiline_textbbox(
            (0, 0),
            wrapped,
            font=font,
            align="center",
            spacing=max(4, round(font_size * 0.16)),
        )
        if bounds[2] - bounds[0] <= max_width and bounds[3] - bounds[1] <= max_height:
            return font, wrapped, bounds
    raise StoryboardValidationError(
        f"brand copy does not fit the portrait safe area: {text!r}"
    )


def _prepare_cta_layout(
    scene: StoryboardScene,
    *,
    asset_root: Path,
    size: tuple[int, int],
) -> _PreparedCtaLayout:
    canvas_width, canvas_height = size
    if canvas_width <= 0 or canvas_height <= 0:
        raise StoryboardValidationError("brand canvas dimensions must be positive")
    logo_layer = _brand_layer_for_role(
        scene,
        "logo",
        legacy_name_fragment="logo",
    )
    hero_layer = _brand_layer_for_role(
        scene,
        "hero",
        legacy_name_fragment="mascot",
    )
    if logo_layer is None or hero_layer is None:
        raise StoryboardValidationError(
            "cta requires managed brand assets with logo and hero roles"
        )
    headline_text = scene.onscreen_text.strip()
    action_text = scene.call_to_action.strip()
    if not headline_text or not action_text:
        raise StoryboardValidationError(
            "cta requires storyboard-owned headline and call_to_action copy"
        )
    intent_by_id = (
        {
            item.element_id: item
            for item in scene.layout_intent.elements
        }
        if scene.layout_intent is not None
        else {}
    )
    scale_factors = {"small": 0.82, "medium": 1.0, "large": 1.15}

    def scale_for(element_id: str) -> float:
        intent = intent_by_id.get(element_id)
        return scale_factors[intent.scale] if intent is not None else 1.0

    with Image.open(resolve_managed_asset(asset_root, logo_layer.asset_id or "")) as raw:
        logo_source = _trim_transparent(raw.convert("RGBA"))
    with Image.open(
        resolve_managed_asset(asset_root, hero_layer.asset_id or "")
    ) as raw:
        hero_source = _trim_transparent(raw.convert("RGBA"))

    horizontal_margin = max(1, round(canvas_width * 0.07))
    vertical_margin = max(1, round(canvas_height * 0.06))
    safe_area = LayoutBox(
        x=horizontal_margin,
        y=vertical_margin,
        width=canvas_width - horizontal_margin * 2,
        height=canvas_height - vertical_margin * 2,
    )
    if safe_area.width <= 0 or safe_area.height <= 0:
        raise StoryboardValidationError("brand canvas is too small for its safe area")

    logo = _contain(
        logo_source,
        (
            round(safe_area.width * 0.66 * scale_for("logo")),
            round(canvas_height * 0.12 * scale_for("logo")),
        ),
    )
    hero = _contain(
        hero_source,
        (
            round(safe_area.width * 0.72 * scale_for("hero")),
            round(canvas_height * 0.25 * scale_for("hero")),
        ),
    )
    headline_scale = scale_for("headline")
    headline_font, headline_wrapped, headline_bounds = _fit_text_block(
        headline_text,
        max_width=round(safe_area.width * 0.94),
        max_height=round(canvas_height * 0.16),
        maximum_font_size=max(1, round(canvas_width * 0.067 * headline_scale)),
        minimum_font_size=max(1, round(canvas_width * 0.04 * headline_scale)),
        multiline=True,
    )
    action_horizontal_padding = round(canvas_width * 0.07)
    action_scale = scale_for("action")
    action_font, _, action_bounds = _fit_text_block(
        action_text,
        max_width=safe_area.width - action_horizontal_padding * 2,
        max_height=round(canvas_height * 0.075),
        maximum_font_size=max(1, round(canvas_width * 0.046 * action_scale)),
        minimum_font_size=max(1, round(canvas_width * 0.04 * action_scale)),
        multiline=False,
    )
    headline_width = round(headline_bounds[2] - headline_bounds[0])
    headline_height = round(headline_bounds[3] - headline_bounds[1])
    action_text_width = round(action_bounds[2] - action_bounds[0])
    action_text_height = round(action_bounds[3] - action_bounds[1])
    action_width = min(
        safe_area.width,
        max(
            round(canvas_width * 0.58),
            action_text_width + action_horizontal_padding * 2,
        ),
    )
    action_height = max(
        round(canvas_height * 0.075),
        action_text_height + round(canvas_height * 0.036),
    )

    if intent_by_id:
        vertical_anchors = {
            "top": 0.0,
            "upper": 0.25,
            "center": 0.5,
            "lower": 0.75,
            "bottom": 1.0,
        }

        def intended_box(element_id: str, width: int, height: int) -> LayoutBox:
            intent = intent_by_id[element_id]
            horizontal_positions = {
                "left": safe_area.x,
                "center": safe_area.x + (safe_area.width - width) // 2,
                "right": safe_area.right - width,
            }
            anchor = vertical_anchors[intent.vertical_region]
            if intent.vertical_region == "top":
                y = safe_area.y
            elif intent.vertical_region == "bottom":
                y = safe_area.bottom - height
            else:
                y = safe_area.y + round(safe_area.height * anchor - height / 2)
            return LayoutBox(
                x=horizontal_positions[intent.horizontal_alignment],
                y=y,
                width=width,
                height=height,
            )

        logo_box = intended_box("logo", logo.width, logo.height)
        hero_box = intended_box("hero", hero.width, hero.height)
        headline_box = intended_box(
            "headline",
            headline_width,
            headline_height,
        )
        action_box = intended_box("action", action_width, action_height)
    else:
        block_heights = (
            logo.height,
            hero.height,
            headline_height,
            action_height,
        )
        minimum_gap = max(1, round(canvas_height * 0.025))
        maximum_gap = max(minimum_gap, round(canvas_height * 0.075))
        remaining_for_gaps = safe_area.height - sum(block_heights)
        if remaining_for_gaps < minimum_gap * 3:
            raise StoryboardValidationError(
                "brand content does not fit the portrait safe area without overlap"
            )
        gap = min(maximum_gap, max(minimum_gap, remaining_for_gaps // 3))
        stack_height = sum(block_heights) + gap * 3
        cursor_y = safe_area.y + (safe_area.height - stack_height) // 2

        def centered_box(width: int, height: int) -> LayoutBox:
            nonlocal cursor_y
            box = LayoutBox(
                x=(canvas_width - width) // 2,
                y=cursor_y,
                width=width,
                height=height,
            )
            cursor_y = box.bottom + gap
            return box

        logo_box = centered_box(logo.width, logo.height)
        hero_box = centered_box(hero.width, hero.height)
        headline_box = centered_box(headline_width, headline_height)
        action_box = centered_box(action_width, action_height)

    element_boxes = (logo_box, hero_box, headline_box, action_box)
    if not all(safe_area.contains(box) for box in element_boxes):
        raise StoryboardValidationError(
            "brand layout intent places content outside the portrait safe area"
        )
    for index, first in enumerate(element_boxes):
        for second in element_boxes[index + 1 :]:
            separated = (
                first.right <= second.x
                or second.right <= first.x
                or first.bottom <= second.y
                or second.bottom <= first.y
            )
            if not separated:
                raise StoryboardValidationError(
                    "brand layout intent produces overlapping elements"
                )
    stack = LayoutBox(
        x=min(logo_box.x, hero_box.x, headline_box.x, action_box.x),
        y=logo_box.y,
        width=max(logo_box.right, hero_box.right, headline_box.right, action_box.right)
        - min(logo_box.x, hero_box.x, headline_box.x, action_box.x),
        height=action_box.bottom - logo_box.y,
    )
    layout = CtaLayout(
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        safe_area=safe_area,
        stack=stack,
        logo=logo_box,
        hero=hero_box,
        headline=headline_box,
        action=action_box,
        headline_text=headline_text,
        action_text=action_text,
        headline_font_size=headline_font.size,
        action_font_size=action_font.size,
    )
    return _PreparedCtaLayout(
        layout=layout,
        logo_image=logo,
        hero_image=hero,
        headline_font=headline_font,
        headline_wrapped=headline_wrapped,
        headline_text_bbox=headline_bounds,
        action_font=action_font,
        action_text_bbox=action_bounds,
    )


def measure_cta_layout(
    scene: StoryboardScene,
    *,
    asset_root: Path,
    size: tuple[int, int] = (720, 1280),
) -> CtaLayout:
    """Measure a reusable portrait end card without writing an artifact."""

    return _prepare_cta_layout(
        scene,
        asset_root=asset_root,
        size=size,
    ).layout


def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    *,
    box: LayoutBox,
    text: str,
    font: ImageFont.FreeTypeFont,
    text_bbox: tuple[int, int, int, int],
    fill: str,
    spacing: int = 4,
) -> None:
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    draw.multiline_text(
        (
            box.x + (box.width - text_width) / 2 - text_bbox[0],
            box.y + (box.height - text_height) / 2 - text_bbox[1],
        ),
        text,
        font=font,
        fill=fill,
        align="center",
        spacing=spacing,
    )


def _render_cta_card(
    scene: StoryboardScene,
    *,
    asset_root: Path,
    size: tuple[int, int],
) -> Image.Image:
    prepared = _prepare_cta_layout(
        scene,
        asset_root=asset_root,
        size=size,
    )
    layout = prepared.layout
    canvas = Image.new("RGBA", size, _BACKGROUND)
    canvas.alpha_composite(
        prepared.logo_image,
        (layout.logo.x, layout.logo.y),
    )
    canvas.alpha_composite(
        prepared.hero_image,
        (layout.hero.x, layout.hero.y),
    )
    draw = ImageDraw.Draw(canvas)
    _draw_text_in_box(
        draw,
        box=layout.headline,
        text=prepared.headline_wrapped,
        font=prepared.headline_font,
        text_bbox=prepared.headline_text_bbox,
        fill=_TEXT,
        spacing=max(4, round(prepared.headline_font.size * 0.16)),
    )
    corner_radius = layout.action.height // 2
    draw.rounded_rectangle(
        (
            layout.action.x,
            layout.action.y,
            layout.action.right,
            layout.action.bottom,
        ),
        radius=corner_radius,
        fill=_YELLOW,
    )
    _draw_text_in_box(
        draw,
        box=layout.action,
        text=layout.action_text,
        font=prepared.action_font,
        text_bbox=prepared.action_text_bbox,
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
