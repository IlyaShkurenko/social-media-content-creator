# Video-quality feedback loop

The project has an installed measurable feedback loop at `feedback-loop/video-quality/`. It evaluates final MP4 artifacts against a fixed storyboard, records immutable sequential experiments, and separates verified technical constraints from pending model-based judgements.

The current evaluator version is intentionally conservative. It verifies media structure, full decode, duration, aspect ratio, audio presence, sustained black segments, extracted scene evidence, and subtitle/script token agreement. Timeline alignment currently uses a frozen human-labelled visual fixture. Timestamped ASR, automatic scene detection, vision-based action/object/text checks, brand fidelity, subtitle safe-area checks, latency, and cost are declared but unavailable until their evaluators are implemented.

TICT logo, mascot, and selected product-screen exports are stored under `feedback-loop/video-quality/evals/assets/brand/` with Figma provenance and confirmed color variables. They are evaluation references and future production inputs; the current MoneyPrinterTurbo renderer does not animate the mascot or synthesize product UI.

Run the loop from its directory with:

```bash
make verify
make baseline
make evaluate EXPERIMENT=experiments/001-baseline
```

See `feedback-loop/video-quality/goal.md` and `feedback-loop/video-quality/docs/evaluation.md` for metric contracts and experiment rules.
