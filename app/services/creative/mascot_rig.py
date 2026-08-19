from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import resvg_py
from PIL import Image


_SVG_NS = "{http://www.w3.org/2000/svg}"

# Two Figma exports of the same character share one rig: identical body,
# center line, face, eyes, and smile — only the first <path> (the arms)
# differs in shape between the neutral and excited pose. See RFC-0009.
_NEUTRAL_FILENAME = "persona_tict 1.svg"
_EXCITED_FILENAME = "persona_tict 2.svg"

_ARM_INDEX = 0
_BODY_INDEX = 1
# index 2 is the <mask>'s internal duplicate of the body outline — geometry
# only, never drawn directly.
_CENTERLINE_INDEX = 3
_FACE_INDEX = 4
_EYE_LEFT_INDEX = 5
_EYE_RIGHT_INDEX = 6
_SMILE_INDEX = 7
_EXPECTED_PATH_COUNT = 8


class MascotRigError(ValueError):
    """Raised when a source mascot SVG doesn't match the expected rig shape."""


@dataclass(frozen=True)
class _CubicSegment:
    start: tuple[float, float]
    c1: tuple[float, float]
    c2: tuple[float, float]
    end: tuple[float, float]

    def lerp(self, other: "_CubicSegment", t: float) -> "_CubicSegment":
        def point(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

        return _CubicSegment(
            start=point(self.start, other.start),
            c1=point(self.c1, other.c1),
            c2=point(self.c2, other.c2),
            end=point(self.end, other.end),
        )


@dataclass(frozen=True)
class _MascotSource:
    view_box: str
    arm_d: str
    arm_stroke: str
    arm_stroke_width: str
    body_d: str
    body_fill: str
    centerline_d: str
    centerline_stroke: str
    centerline_stroke_width: str
    face_d: str
    face_fill: str
    eye_left_d: str
    eye_right_d: str
    eye_stroke_width: str
    smile_d: str
    smile_stroke_width: str


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(match) for match in _NUMBER.findall(text)]


def _parse_paths(svg_path: Path) -> tuple[str, list[ET.Element]]:
    root = ET.parse(svg_path).getroot()
    view_box = root.get("viewBox")
    if not view_box:
        raise MascotRigError(f"{svg_path} has no viewBox")
    elements = list(root.iter(f"{_SVG_NS}path"))
    if len(elements) != _EXPECTED_PATH_COUNT:
        raise MascotRigError(
            f"{svg_path} has {len(elements)} <path> elements, expected "
            f"{_EXPECTED_PATH_COUNT} — the rig's positional indices no longer apply"
        )
    return view_box, elements


def _load_source(brand_asset_root: Path, filename: str) -> _MascotSource:
    view_box, paths = _parse_paths(brand_asset_root / filename)
    return _MascotSource(
        view_box=view_box,
        arm_d=paths[_ARM_INDEX].get("d", ""),
        arm_stroke=paths[_ARM_INDEX].get("stroke", "#000000"),
        arm_stroke_width=paths[_ARM_INDEX].get("stroke-width", "1"),
        body_d=paths[_BODY_INDEX].get("d", ""),
        body_fill=paths[_BODY_INDEX].get("fill", "#000000"),
        centerline_d=paths[_CENTERLINE_INDEX].get("d", ""),
        centerline_stroke=paths[_CENTERLINE_INDEX].get("stroke", "#000000"),
        centerline_stroke_width=paths[_CENTERLINE_INDEX].get("stroke-width", "1"),
        face_d=paths[_FACE_INDEX].get("d", ""),
        face_fill=paths[_FACE_INDEX].get("fill", "#000000"),
        eye_left_d=paths[_EYE_LEFT_INDEX].get("d", ""),
        eye_right_d=paths[_EYE_RIGHT_INDEX].get("d", ""),
        eye_stroke_width=paths[_EYE_LEFT_INDEX].get("stroke-width", "1"),
        smile_d=paths[_SMILE_INDEX].get("d", ""),
        smile_stroke_width=paths[_SMILE_INDEX].get("stroke-width", "1"),
    )


def _eye_anchor(source: _MascotSource) -> tuple[float, float]:
    values = _numbers(source.eye_left_d)
    if len(values) < 2:
        raise MascotRigError("eye path has no leading coordinate to anchor on")
    return values[0], values[1]


def _parse_excited_arm(d: str) -> tuple[_CubicSegment, _CubicSegment]:
    """Parse `M x0,y0 Cc1x,c1y c2x,c2y ex,ey Cc3x,c3y c4x,c4y x1,y1`."""

    values = _numbers(d)
    if len(values) != 14:
        raise MascotRigError(
            f"excited arm path has {len(values)} numbers, expected 14 "
            "(a moveto plus two cubic-bezier segments)"
        )
    x0, y0, c1x, c1y, c2x, c2y, mx, my, c3x, c3y, c4x, c4y, x1, y1 = values
    seg_a = _CubicSegment((x0, y0), (c1x, c1y), (c2x, c2y), (mx, my))
    seg_b = _CubicSegment((mx, my), (c3x, c3y), (c4x, c4y), (x1, y1))
    return seg_a, seg_b


def _parse_neutral_arm_as_flat_cubics(
    d: str, *, translate: tuple[float, float]
) -> tuple[_CubicSegment, _CubicSegment]:
    """Parse `M x0 y0H x1`, translate into the excited file's coordinate
    space, and represent it as two degenerate (perfectly straight) cubic
    segments so it has the same interpolatable shape as the excited arm."""

    values = _numbers(d)
    if len(values) != 3:
        raise MascotRigError(
            f"neutral arm path has {len(values)} numbers, expected 3 "
            "(a moveto plus a horizontal lineto)"
        )
    x0, y0, x1 = values
    dx, dy = translate
    x0, x1 = x0 + dx, x1 + dx
    y = y0 + dy
    mid = (x0 + x1) / 2

    def flat(a: float, b: float) -> _CubicSegment:
        third = a + (b - a) / 3
        two_thirds = a + (b - a) * 2 / 3
        return _CubicSegment((a, y), (third, y), (two_thirds, y), (b, y))

    return flat(x0, mid), flat(mid, x1)


def _cubics_to_d(seg_a: _CubicSegment, seg_b: _CubicSegment) -> str:
    def fmt(value: float) -> str:
        return f"{value:.2f}"

    return (
        f"M{fmt(seg_a.start[0])} {fmt(seg_a.start[1])}"
        f"C{fmt(seg_a.c1[0])} {fmt(seg_a.c1[1])} {fmt(seg_a.c2[0])} {fmt(seg_a.c2[1])} "
        f"{fmt(seg_a.end[0])} {fmt(seg_a.end[1])}"
        f"C{fmt(seg_b.c1[0])} {fmt(seg_b.c1[1])} {fmt(seg_b.c2[0])} {fmt(seg_b.c2[1])} "
        f"{fmt(seg_b.end[0])} {fmt(seg_b.end[1])}"
    )


class MascotRig:
    """Interpolates the tict mascot's arms between two real Figma poses.

    Body, face, eyes, and smile are drawn exactly as approved — only the
    arm path (a two-point line in the neutral pose, a two-segment raised
    curve in the excited pose) is generated, by linear interpolation
    between the two, never by a generative model. See RFC-0009.
    """

    def __init__(self, brand_asset_root: Path):
        excited = _load_source(brand_asset_root, _EXCITED_FILENAME)
        neutral = _load_source(brand_asset_root, _NEUTRAL_FILENAME)
        excited_anchor = _eye_anchor(excited)
        neutral_anchor = _eye_anchor(neutral)
        translate = (
            excited_anchor[0] - neutral_anchor[0],
            excited_anchor[1] - neutral_anchor[1],
        )
        self._excited = excited
        self._seg_neutral = _parse_neutral_arm_as_flat_cubics(
            neutral.arm_d, translate=translate
        )
        self._seg_excited = _parse_excited_arm(excited.arm_d)

    def arm_path_d(self, blend: float) -> tuple[str, str, str]:
        """Interpolated arm `d`, stroke color, and stroke width at `blend`
        (0 = neutral, 1 = excited; values outside [0, 1] extrapolate, which
        reads as a bounce overshoot past the raised pose)."""

        seg_a = self._seg_neutral[0].lerp(self._seg_excited[0], blend)
        seg_b = self._seg_neutral[1].lerp(self._seg_excited[1], blend)
        return (
            _cubics_to_d(seg_a, seg_b),
            self._excited.arm_stroke,
            self._excited.arm_stroke_width,
        )

    def svg(self, blend: float) -> str:
        """A standalone SVG of the full mascot at the given arm blend."""

        arm_d, arm_stroke, arm_stroke_width = self.arm_path_d(blend)
        e = self._excited
        return f"""<svg viewBox="{e.view_box}" fill="none" xmlns="http://www.w3.org/2000/svg">
<path d="{arm_d}" stroke="{arm_stroke}" stroke-width="{arm_stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>
<path d="{e.body_d}" fill="{e.body_fill}"/>
<mask id="tict-mascot-rig-mask" style="mask-type:luminance" maskUnits="userSpaceOnUse">
<path d="{e.body_d}" fill="white"/>
</mask>
<g mask="url(#tict-mascot-rig-mask)">
<path d="{e.centerline_d}" stroke="{e.centerline_stroke}" stroke-width="{e.centerline_stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>
</g>
<path d="{e.face_d}" fill="{e.face_fill}"/>
<path d="{e.eye_left_d}" stroke="black" stroke-width="{e.eye_stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>
<path d="{e.eye_right_d}" stroke="black" stroke-width="{e.eye_stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>
<path d="{e.smile_d}" stroke="black" stroke-width="{e.smile_stroke_width}" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

    def render(self, blend: float, *, width: int, height: int) -> Image.Image:
        png_bytes = resvg_py.svg_to_bytes(
            svg_string=self.svg(blend), width=width, height=height
        )
        return Image.open(io.BytesIO(bytes(png_bytes))).convert("RGBA")


def arm_raise_factor(t: float) -> float:
    """A multi-beat performance across several seconds, not one gesture:
    raise with a bounce overshoot, two waving beats, then settle to a
    relaxed (not fully neutral) idle sway. Pure function of elapsed
    seconds — directly unit-testable, same pattern as
    ``renderer.mascot_pop_scale``. See RFC-0009."""

    if t < 0:
        t = 0.0

    raise_duration = 0.5
    wave_duration = 1.4
    settle_duration = 0.5
    wave_start = raise_duration
    wave_end = wave_start + wave_duration
    settle_end = wave_end + settle_duration
    idle_rest = 0.22

    if t < raise_duration:
        rise = raise_duration * 0.6
        if t < rise:
            return (t / rise) * 1.12
        settle_progress = (t - rise) / (raise_duration - rise)
        return 1.12 - settle_progress * 0.12
    if t < wave_end:
        wave_t = t - wave_start
        return 1.0 - 0.22 * (0.5 - 0.5 * math.cos(2 * math.pi * wave_t / 0.7))
    if t < settle_end:
        settle_t = (t - wave_end) / settle_duration
        eased = settle_t * settle_t * (3 - 2 * settle_t)  # smoothstep
        return 1.0 - eased * (1.0 - idle_rest)
    idle_t = t - settle_end
    return idle_rest + 0.05 * math.sin(2 * math.pi * idle_t / 2.2)
