# RFC-0009: A real vector rig for the mascot, not a raster pose swap

- Status: Accepted
- Date: 2026-08-19
- Decision owners: Project maintainers and product owner
- Decision type: New asset dependency (SVG source art), new runtime dependency, renderer architecture

## Summary

Replace RFC-0008's two-raster-pose mascot ("neutral"/"excited" PNG swap,
static except for whole-body scale) with a small vector rig built from two
clean Figma SVG exports that turned out to share one skeleton. The mascot's
arms now interpolate continuously between the two real poses and perform a
multi-beat arc — raise, wave twice, settle to a relaxed idle — instead of
sitting in one fixed pose for the whole card. Everything else about the CTA
(background, speech bubble, logo, headline, button) is unchanged.

## Context

RFC-0008 shipped a real, working bounce/idle animation, but only of a single
static raster image — genuine motion was limited to whole-body scale.
Asked for something more alive, two paths were investigated and rejected in
RFC-0008's own "Consequences": generative image-to-video (brand-fidelity risk
on a flat, minimal-detail character) and rig-style cutout animation from the
mascot's other exported artwork (which turned out to be inconsistent whole
illustrations, not interchangeable parts).

A fresh batch of Figma exports changed this. Inspecting them found: `Mascot.svg`
and five of seven `persona_tict *.svg` files are PNG raster data wrapped in an
SVG container (Figma flattens layers with effects it can't export as vectors) —
no more usable than the original PNGs. But two files, `persona_tict 1.svg` and
`persona_tict 2.svg`, are genuine vector paths, and — checked directly — have
**identical structure**: the same 8 `<path>` elements in the same order (arms,
body fill, mask-internal body duplicate, mask-clipped centerline, face, left
eye, right eye, smile). Only the first path — the arms — differs in geometry: a
straight horizontal line in one file, a two-segment raised curve in the other.
That is a real, if minimal, two-pose rig that happened to already exist.

## Decision

### 1. The arm is interpolated as matching cubic-bezier segments, not swapped

`MascotRig` (`app/services/creative/mascot_rig.py`) parses both source SVGs
with `xml.etree.ElementTree`, asserts each has exactly 8 `<path>` elements
(`MascotRigError` otherwise — a structural guard, not a soft guess), and
extracts the arm, body, centerline, face, eyes, and smile by their fixed
position. The neutral file's straight-line arm (`M x0,y0 H x1`) is rewritten
as an equivalent degenerate two-segment cubic Bezier — control points placed
on the line itself — so it has the exact same command shape as the excited
file's genuine two-segment curve. The two files' coordinate spaces are then
aligned (their `viewBox`es differ slightly, a normal Figma per-frame export
artifact) by translating the neutral arm so its eye-anchor point matches the
excited file's, using the left eye's leading coordinate as the shared
landmark. With both arms expressed as the same eight-point structure, an arm
at any blend `t` is a plain per-coordinate linear interpolation — `t` outside
`[0, 1]` legitimately extrapolates past either pose, which is what produces
the raise's overshoot bounce.

### 2. The performance is a pure function of elapsed time, several beats long

`arm_raise_factor(t)` composes: a quick raise with an 8% overshoot and
settle, two sinusoidal wave beats, then a smoothstep ease down to a relaxed
(not fully neutral) idle sway — one continuous, directly unit-testable
function, the same pattern as `renderer.mascot_pop_scale` from RFC-0008. The
two now compose independently: the rig drives the arms' shape, `mascot_pop_scale`
still drives the whole rendered character's entrance scale and idle breathing,
applied on top of whichever image is on screen. `mascot_pose` from RFC-0008
keeps selecting the still-image hero asset used everywhere the rig doesn't
apply (measurement, the non-rig fallback), but no longer determines the CTA
performance itself — the arc always runs through the full neutral-to-excited
range regardless of which pose was chosen for the static asset.

### 3. Rendering goes through resvg, not cairosvg

`cairosvg` was tried first and rejected: it dynamically loads the system
`libcairo` via `cairocffi`, and in this project's `uv`-managed Python it
could not find Homebrew's `libcairo.dylib` even though the library was
installed — a path-resolution problem tied to this one machine's
configuration, not a property of the code. `resvg-py` — Python bindings
distributed as a wheel that bundles a compiled `resvg` (Rust) binary — has no
external system-library dependency, rendered a hand-inspected test case
(including the `<mask>` element the mascot's centerline depends on)
correctly, and is the new dependency instead. Every frame is rendered to a
raster PNG at the storyboard's exact hero size, matching how every other
pixel in this renderer is already produced, then composited exactly as
RFC-0008 already does.

### 4. Optional, with a real fallback

`_render_animated_cta_clip` tries to construct a `MascotRig` from the
storyboard's asset root; a missing file or a structural mismatch
(`MascotRigError`) falls back to RFC-0008's static-image pop/idle path rather
than failing the render. Any storyboard or asset root that doesn't carry
these exact two source files keeps working exactly as before.

## Consequences

The mascot now performs — raises its arms, waves, settles — built entirely
from two pieces of real, approved vector art and a documented interpolation,
never a generative model's guess at the character. The two files this
depends on have unusual, Figma-default names (`persona_tict 1.svg`,
`persona_tict 2.svg`); if a future brand-kit refresh renames or restructures
them, `MascotRig`'s exact-path-count check fails closed into the RFC-0008
fallback rather than silently misinterpreting a different SVG's paths as the
rig's parts. Real mouth movement / lip-sync remains out of scope, as
RFC-0008 already noted.

## References

- `records/rfcs/0008-mascot-speech-and-motion.md`
- `feedback-loop/video-quality/evals/assets/brand/persona_tict 1.svg`
- `feedback-loop/video-quality/evals/assets/brand/persona_tict 2.svg`
