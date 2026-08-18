# Video-quality evaluation

## What evaluator 0.6 measures

The evaluator consumes a fixed scenario, a rendered MP4, stored versioned
Gemini pairwise evidence, and stored high-frequency temporal evidence. It:

1. probes streams, duration, dimensions, and frame rate with FFprobe;
2. decodes the full video with FFmpeg to catch corrupt media;
3. detects sustained black segments;
4. extracts one evidence frame from the midpoint of every declared storyboard scene;
5. verifies two blind pairwise passes with reversed A/B order and calculates `visual_judge_win_rate`;
6. derives closed-vocabulary scene observations by cross-pass consensus;
7. calculates timeline-alignment precision, recall, and F1 deterministically;
8. compares subtitle tokens and exact canonical `tict` spelling with the stored script;
9. evaluates every scene's declared screen policy from structured screen evidence;
10. verifies a 10 FPS temporal screen of the generated hook and blocks reported high-severity events until confirmation;
11. records generation latency, judge usage, conservative ledger charge, and remaining budget;
12. records unavailable metrics explicitly.

The old `human_fixture` remains historical evaluator `0.4` evidence. Evaluator
`0.6` requires compact documents created by the paid `make temporal-judge` and
`make judge` stages. Both documents are embedded in tracked experiment inputs,
so `make evaluate` is offline and reproducible.

## Interpretation

`visual_judge_win_rate` is the mean candidate credit from the two reversed passes:
candidate win `1.0`, tie `0.5`, baseline win `0.0`. The self-comparison baseline
is `0.5`. Contradictory positional wins cancel to `0.5` instead of pretending the
candidate improved. This is a diagnostic model-preference signal, not a
percentage of video quality and not the sole acceptance authority.

`timeline_alignment_f1` keys every tag by scene ID. Observed tags must come from
the storyboard's closed vocabulary, and only cross-pass consensus becomes metric
evidence. Alignment is a non-regression gate even though pairwise preference is
the primary experiment metric.

Hard checks currently enforced are decode success, audio presence, aspect ratio,
duration, absence of sustained black segments, evidence-frame extraction,
subtitle safe-area compliance, automated screen policy, and available brand
fidelity. A reported high-severity `object_disappearance`, `object_duplication`,
`orientation_discontinuity`, `screen_visibility_contradiction`,
`geometry_deformation`, or `hand_interaction_discontinuity` event fails temporal
consistency and blocks automatic selection until its cited frames are reviewed.
A confirmed defect keeps the veto; a confirmed false positive clears only that
event. Exact `tict` spelling remains independently scored. Timestamped ASR,
rendered-audio pronunciation, word timing, and automatic shot-boundary error are
still pending. A candidate cannot be automatically accepted while a required
constraint is pending.

Screen policy is intent-aware:

- `approved_product_ui` requires the approved tict capture and identity evidence;
- `non_product_context` permits generic or hidden UI and forbids claiming it is tict;
- `screen_hidden` requires no readable device display;
- `unconstrained` does not require a screen class.

## Evaluator architecture

```text
generated hook + storyboard
  -> 10 FPS timestamped strips
  -> paid versioned Gemini temporal judge
  -> deterministic pending/confirmed temporal eligibility

baseline MP4 + candidate MP4 + storyboard
  -> paid versioned Gemini judge (two reversed A/B passes)
  -> sanitized structured judge evidence
  -> offline deterministic evaluator
  -> temporal, timeline, screen, brand, technical and pairwise metrics
  -> metrics.json + reproducible experiment record
```

The judge returns structured scene observations and rubric evidence rather than a
single unrestricted aesthetic score. Pairwise preference remains a research
diagnostic for evaluator `0.6`. The acceptance controller can keep a technically
compliant artifact after explicit product-owner acceptance even when model
preference does not improve. Temporal, alignment, brand, and deterministic
failures cannot be overridden.

## Commands

Run from `feedback-loop/video-quality`:

```bash
make verify
make temporal-judge \
  CONFIRM_PAID=YES \
  TEMPORAL_VIDEO=path/to/generated-hook.mp4 \
  TEMPORAL_OUTPUT=.state/temporal/hook.json \
  OPERATION_ID=unique-temporal-id

make judge \
  CONFIRM_PAID=YES \
  BASELINE_VIDEO=path/to/baseline.mp4 \
  CANDIDATE_VIDEO=path/to/candidate.mp4 \
  JUDGE_OUTPUT=.state/judges/comparison.json \
  OPERATION_PREFIX=unique-comparison-id

make baseline \
  SCENARIO=evals/dataset/mixed-media-stock-baseline-001.json \
  VIDEO=path/to/baseline.mp4 \
  JUDGE_EVIDENCE=.state/judges/self-comparison.json \
  TEMPORAL_EVIDENCE=.state/temporal/hook.json

make evaluate EXPERIMENT=experiments/<evaluator-0.6-baseline>
make experiment-start \
  BASELINE=experiments/<evaluator-0.6-baseline> \
  SLUG=ordered-materials \
  PROBLEM="The product reveal is visually disconnected from the hook" \
  HYPOTHESIS="A semantic bridge improves scene alignment" \
  CHANGE="Change only the transition into the product demonstration" \
  EXPECTED="Increase visual_judge_win_rate without alignment or constraint regressions"

make experiment-evaluate \
  EXPERIMENT=experiments/009-ordered-materials \
  VIDEO=path/to/candidate.mp4 \
  JUDGE_EVIDENCE=.state/judges/candidate-comparison.json \
  TEMPORAL_EVIDENCE=.state/temporal/candidate-hook.json

make experiment-finish \
  EXPERIMENT=experiments/009-ordered-materials \
  DECISION=keep \
  HUMAN_REVIEW=YES \
  REVIEWER=user \
  REVIEW_OUTCOME=accept \
  REVIEW_REASON="The retained final MP4 meets the advertising objective" \
  LEARNING="The bridge improved alignment and passed visual review"

make experiment-review \
  EXPERIMENT=experiments/012-screen-stable-runway-batch \
  REVIEW_OUTCOME=accept \
  REVIEWER=user \
  REVIEW_REASON="Candidate 05 is acceptable as the production reference" \
  LEARNING="Human preference overrides the uncalibrated model-preference diagnostic"

make campaign-finalize \
  CAMPAIGN_OUTPUT=experiments/<campaign>/artifacts/campaign \
  CAMPAIGN_REVIEW=experiments/<campaign>/artifacts/campaign/review/temporal-confirmations.json
```

`campaign-finalize` is idempotent. It applies artifact-bound event confirmations,
verifies that cited evidence stays inside the managed campaign tree, reuses valid
existing renders, emits all EVAL-7.1 scorecard dimensions, and renders only
eligible candidates. It does not select or accept a subjective winner.

`experiment-start` requires a clean worktree. It re-evaluates the selected baseline under ignored state, verifies metric equality, and then allocates the next identity. The resulting `inputs.json` schema v2 freezes the baseline hash, starting git revision, observed problem, hypothesis, planned change, expected impact, scenario hash, and plan hash before candidate code or metrics exist.

`experiment-evaluate` refuses a changed plan, scenario, or baseline. It appends sanitized candidate inputs, evaluator results, candidate revision, and worktree-diff hash to the same identity. `experiment-finish` enforces keep/revert policy and records learning. `experiment-review` can apply later explicit product-owner review to an evaluated or reverted result while retaining the previous decision in history. Neither command runs git revert, commit, or push.

Successful records track only `README.md`, `metrics.json`, and `inputs.json`;
failed evaluation also retains `evaluator.stderr.log`. Video snapshots and
extracted frames stay under ignored `artifacts/`; every evaluated record must
retain its exact final MP4 at `artifacts/video.mp4`. Sanitized pairwise and
temporal evidence is stored inside `inputs.json`. `make evaluate` reconstructs it
under ignored state, verifies hashes, and performs no network call. Historical
layouts remain intact.

## tict brand fixture

`evals/assets/brand/brand-kit.json` records Figma provenance, exported logo/mascot assets, product screens, and confirmed variables. The typography source currently fails through the Figma connector because the connected user lacks the required access; no font name has been guessed.
