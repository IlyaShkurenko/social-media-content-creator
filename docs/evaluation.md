# Video-quality feedback loop

The project has an installed measurable feedback loop at `feedback-loop/video-quality/`. It evaluates final MP4 artifacts against a fixed storyboard, records immutable sequential experiments, and separates verified technical constraints from pending model-based judgements.

Evaluator `0.6.0` preserves the deterministic media, subtitle, brand-text, and screen-policy checks and uses two complementary Gemini 3.6 Flash protocols. The pairwise whole-video judge runs blind passes with reversed A/B order. Before a generated hook is selectable, the temporal judge samples its declared scene at 10 FPS and inspects timestamped adjacent-frame strips for a closed set of continuity failures. A high-severity event vetoes the hook. Both judges store sanitized structured evidence and metrics are calculated offline. `visual_judge_win_rate` remains the experiment primary metric; temporal consistency, `timeline_alignment_f1`, technical validity, screen policy, and brand evidence are non-regression gates. Timestamped ASR and rendered-audio pronunciation are still pending and therefore keep early perceptual improvements subject to explicit review.

tict logo, mascot, and selected product-screen exports are stored under `feedback-loop/video-quality/evals/assets/brand/` with Figma provenance and confirmed color variables. The experimental mixed-media renderer composites approved product screens exactly; it does not ask Runway to redraw them and does not yet animate the mascot.

Run the loop from its directory with:

```bash
make verify
make temporal-judge CONFIRM_PAID=YES ...
make judge CONFIRM_PAID=YES ...
make baseline JUDGE_EVIDENCE=.state/judges/<evidence>.json TEMPORAL_EVIDENCE=.state/temporal/<evidence>.json ...
make evaluate EXPERIMENT=experiments/<evaluator-0.6-baseline>
```

See `feedback-loop/video-quality/goal.md` and `feedback-loop/video-quality/docs/evaluation.md` for metric contracts and experiment rules.

New experiment directories are intentionally compact: tracked decision notes, metrics, one content-addressed input manifest, and a failure log only when needed. The exact final MP4 is mandatory at `artifacts/video.mp4` for every evaluated result, while large video/frame artifacts remain local and ignored. Sanitized pairwise and temporal evidence is embedded in the manifest, so replay validates hashes and never repeats a paid or nondeterministic Gemini call.

Product experiments use a staged lifecycle. `experiment-start` reproduces and freezes a baseline plus the pre-change engineering hypothesis; `experiment-evaluate` attaches the candidate and metrics without changing that plan; `experiment-finish` records keep/revert learning and requires explicit review evidence for provisional results. Git revert, validation, commit, and push remain deliberate post-decision actions.

The acceptance-decision contract is specified in `docs/specs/video-quality-feedback-loop.md` and its selected executable behavior is covered by `test/bdd/features/video_quality_acceptance.feature`.
