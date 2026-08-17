# Video-quality feedback-loop rules

This subtree owns measurable experiments for the final MP4 produced by MoneyPrinterTurbo. The parent `AGENTS.md` still applies.

## Required experiment protocol

1. Read `goal.md`, `docs/evaluation.md`, and the latest experiment README before changing product code.
2. Run `make evaluate EXPERIMENT=experiments/<current-baseline>` to confirm that the current evaluator is reproducible.
3. Create exactly one sequential experiment with `make experiment SLUG=<short-name> HYPOTHESIS="<one falsifiable claim>"`.
4. Change one coherent variable, generate or select the declared artifact, and evaluate the same fixed scenario.
5. Keep product-code changes only when the primary metric improves and every required constraint is verified. Until the pending ASR and visual-safe-area checks are implemented, improvements are provisional and require human review.
6. If an experiment fails or regresses, revert its product-code changes but retain the experiment README, metrics, and failure evidence. Never reuse an experiment number.

## Evaluator integrity

- Do not change the evaluator, scenario assertions, observation fixture, or thresholds merely to improve a score.
- Any intentional evaluator change requires an evaluator-version bump, a new baseline, and an explanation in that baseline README.
- `human_fixture` observations are frozen evidence for evaluator v0. They are not an automatic vision judgement and must never be reported as one.
- Never log credentials or copy `config.toml` into an experiment.
- Paid video generation stays disabled unless the user explicitly authorizes a provider and a per-run budget.
- Store large decoded frames and transient media under an experiment's ignored `artifacts/` directory. Keep metrics and experiment documentation tracked.

## Stable commands

- `make baseline` creates the next immutable baseline experiment.
- `make experiment SLUG=... HYPOTHESIS="..."` creates and evaluates the next experiment against the fixed scenario.
- `make evaluate EXPERIMENT=experiments/NNN-name` reproduces one existing experiment.
- `make verify` validates evaluator code and fixtures without paid API calls.
