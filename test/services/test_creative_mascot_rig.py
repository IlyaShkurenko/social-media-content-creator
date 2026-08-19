from __future__ import annotations

from pathlib import Path

import pytest

from app.services.creative.mascot_rig import (
    MascotRig,
    MascotRigError,
    arm_raise_factor,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = REPO_ROOT / "feedback-loop/video-quality/evals/assets/brand"


def test_voice_1_1_arm_raise_factor_performs_several_beats_not_one_gesture() -> None:
    """[RFC-0009] A multi-second arc: raise-overshoot, two waves, then idle."""

    assert arm_raise_factor(0.0) == 0.0
    # Rises with an overshoot past the raised pose before settling.
    peak = max(arm_raise_factor(step / 100) for step in range(0, 30))
    assert peak > 1.05
    assert abs(arm_raise_factor(0.5) - 1.0) < 1e-9

    # The wave phase genuinely oscillates — not a flat hold at 1.0.
    wave_samples = [arm_raise_factor(0.5 + step * 0.1) for step in range(14)]
    assert min(wave_samples) < 0.85
    assert max(wave_samples) > 0.95

    # It settles to a relaxed, non-neutral, non-fully-raised idle rest,
    # and keeps gently swaying rather than freezing dead.
    idle_samples = [arm_raise_factor(3.0 + step * 0.3) for step in range(10)]
    assert all(0.1 < sample < 0.35 for sample in idle_samples)
    assert max(idle_samples) - min(idle_samples) > 0.01

    # Never asked to perform behind the current time.
    assert arm_raise_factor(-5.0) == arm_raise_factor(0.0)


def test_voice_1_1_rig_loads_the_real_brand_asset_pair() -> None:
    """[RFC-0009] The two Figma exports actually share one 8-path rig."""

    rig = MascotRig(ASSET_ROOT)
    neutral_d, _, _ = rig.arm_path_d(0.0)
    excited_d, stroke, stroke_width = rig.arm_path_d(1.0)
    assert neutral_d != excited_d
    assert stroke and stroke_width


def test_voice_1_1_arm_interpolates_smoothly_between_the_two_real_poses() -> None:
    """A halfway blend must sit strictly between the endpoints, not equal either."""

    rig = MascotRig(ASSET_ROOT)
    neutral_d, _, _ = rig.arm_path_d(0.0)
    half_d, _, _ = rig.arm_path_d(0.5)
    excited_d, _, _ = rig.arm_path_d(1.0)
    assert half_d != neutral_d
    assert half_d != excited_d


def test_voice_1_1_svg_declares_fill_none_so_stroke_only_paths_stay_transparent() -> None:
    """Regression: omitting the root fill=none filled the arms/centerline
    solid black behind their stroke — caught by eye, now pinned by a test."""

    rig = MascotRig(ASSET_ROOT)
    svg = rig.svg(1.0)
    assert 'fill="none"' in svg.split(">", 1)[0]


def test_voice_1_1_render_produces_a_transparent_raster_at_the_requested_size() -> None:
    # resvg fits the SVG's own aspect ratio inside the given box rather than
    # stretching to it, so the requested size must match the rig's real
    # proportions (704:504, the same ratio the approved PNG hero asset
    # already uses) or the rendered image comes back smaller on one axis.
    rig = MascotRig(ASSET_ROOT)
    image = rig.render(0.5, width=352, height=252)  # exactly half the 704x504 viewBox
    assert image.size == (352, 252)
    assert image.mode == "RGBA"
    # Some pixels must be transparent (outside the character) — a solid
    # fill=black regression would produce a fully opaque frame instead.
    alphas = image.getchannel("A").getdata()
    assert any(value == 0 for value in alphas)
    assert any(value > 0 for value in alphas)


def test_voice_1_1_missing_source_file_fails_closed() -> None:
    with pytest.raises((MascotRigError, OSError)):
        MascotRig(REPO_ROOT)  # repo root has neither persona_tict SVG
