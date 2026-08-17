# Video-quality feedback-loop rules

This subtree owns measurable experiments for the final MP4 produced by MoneyPrinterTurbo. The parent `AGENTS.md` still applies.

## Required experiment protocol

1. Read `goal.md`, `docs/evaluation.md`, and the latest experiment README before changing product code.
2. From a clean worktree, run `make experiment-start BASELINE=experiments/<current-baseline> SLUG=<short-name> PROBLEM="..." HYPOTHESIS="<one falsifiable claim>" CHANGE="<one coherent variable>" EXPECTED="<metric expectation>"`. This command reproduces the baseline and freezes the plan before candidate code changes.
3. Inspect previous experiment history, then implement only the frozen planned change. Do not edit the plan to fit the result.
4. Generate or select the declared artifact and run `make experiment-evaluate EXPERIMENT=experiments/<current-experiment> ...` against the frozen scenario and baseline.
5. Run `make experiment-finish EXPERIMENT=... DECISION=keep|revert LEARNING="..."`. A reviewed keep additionally requires `HUMAN_REVIEW=YES REVIEWER=user REVIEW_OUTCOME=accept REVIEW_REASON="..."` after explicit user review. Use `make experiment-review` when that review arrives after a result was finalized.
6. Keep product-code changes only when the configured automatic acceptance contract is satisfied with every required constraint verified, or when the user explicitly accepts the exact retained artifact and every enforced constraint passes. `visual_judge_win_rate` alone is diagnostic. After a keep, run normal repository validation, commit the implementation and tracked experiment evidence together, and push the current branch to `origin` before starting another experiment.
7. After `revert`, revert the candidate implementation but retain and commit the finalized experiment README, metrics, and useful failure evidence. Never reuse an experiment number. Do not push reverted product code as an improvement.

## Evaluator integrity

- Do not change the evaluator, scenario assertions, observation fixture, or thresholds merely to improve a score.
- Any intentional evaluator measurement change requires an evaluator-version bump, a new baseline, and an explanation in that baseline README. Acceptance-policy changes must preserve original metrics and record artifact-bound review evidence.
- `human_fixture` observations are frozen evidence for their recorded evaluator version. They are not an automatic vision judgement and must never be reported as one.
- Never log credentials or copy `config.toml` into an experiment.
- Paid video generation stays disabled unless the user explicitly authorizes a provider and a per-run budget.
- Store large decoded frames and transient media under an experiment's ignored `artifacts/` directory. New compact records keep tracked `README.md`, `metrics.json`, and `inputs.json`; a failure adds a tracked `evaluator.stderr.log`. Do not copy unchanged shared fixtures into new experiment directories.

## Stable commands

- `make baseline` creates the next immutable baseline experiment.
- `make experiment-start ...` reproduces the selected baseline and creates the next planned experiment without candidate metrics. `make experiment` is an alias.
- `make experiment-evaluate EXPERIMENT=experiments/NNN-name ...` evaluates the candidate under the frozen plan.
- `make experiment-finish EXPERIMENT=... DECISION=... LEARNING="..."` records the final disposition without automatically changing git state.
- `make experiment-review EXPERIMENT=... REVIEW_OUTCOME=... REVIEWER=... REVIEW_REASON=... LEARNING="..."` applies a later artifact-bound product-owner label.
- `make evaluate EXPERIMENT=experiments/NNN-name` reproduces one existing experiment under ignored state and verifies that its metrics did not drift.
- `make verify` validates evaluator code and fixtures without paid API calls.
