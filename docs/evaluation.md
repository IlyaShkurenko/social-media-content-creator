# Video-quality feedback loop

The project has an installed measurable feedback loop at `feedback-loop/video-quality/`. It evaluates final MP4 artifacts against a fixed storyboard, records immutable sequential experiments, and separates verified technical constraints from pending model-based judgements.

Evaluator `0.4.0` verifies media structure, full decode, duration, aspect ratio, audio presence, sustained black segments, extracted scene evidence, subtitle/script agreement, exact lowercase `tict` spelling, and scene-specific screen policy. It also records safe-area, latency, and cost evidence when available. Timeline and screen alignment still use frozen human-labelled observations. Timestamped ASR, rendered-audio pronunciation, automatic scene detection, automated vision checks, and brand fidelity remain unavailable until their evaluators are implemented.

tict logo, mascot, and selected product-screen exports are stored under `feedback-loop/video-quality/evals/assets/brand/` with Figma provenance and confirmed color variables. The experimental mixed-media renderer composites approved product screens exactly; it does not ask Runway to redraw them and does not yet animate the mascot.

Run the loop from its directory with:

```bash
make verify
make baseline
make evaluate EXPERIMENT=experiments/008-baseline
```

See `feedback-loop/video-quality/goal.md` and `feedback-loop/video-quality/docs/evaluation.md` for metric contracts and experiment rules.

New experiment directories are intentionally compact: tracked decision notes, metrics, one content-addressed input manifest, and a failure log only when needed. Large video/frame artifacts remain local and ignored; replay validates their hashes and preserves the evidence needed to diagnose failures.

Product experiments use a staged lifecycle. `experiment-start` reproduces and freezes a baseline plus the pre-change engineering hypothesis; `experiment-evaluate` attaches the candidate and metrics without changing that plan; `experiment-finish` records keep/revert learning and requires explicit review evidence for provisional results. Git revert, validation, commit, and push remain deliberate post-decision actions.

The acceptance-decision contract is specified in `docs/specs/video-quality-feedback-loop.md` and its selected executable behavior is covered by `test/bdd/features/video_quality_acceptance.feature`.
