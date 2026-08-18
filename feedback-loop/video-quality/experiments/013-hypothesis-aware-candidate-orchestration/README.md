# 013-hypothesis-aware-candidate-orchestration

- Started: `2026-08-18T11:52:59.725439+00:00`
- Status: `requires_review`
- Start revision: `01fd020c60de8f7d05de67a0ea38a5a8c4c1ff6b`
- Scenario: `evals/dataset/mixed-media-stock-baseline-001.json`

## Frozen baseline

- Experiment: `experiments/012-screen-stable-runway-batch`
- Evaluator: `0.6.0`
- Primary `visual_judge_win_rate`: `0.250000`
- Metrics SHA-256: `2c9ea3e6392c3ca36ba43fb3250f9e0b0e4f432785f92a1930c784e324e80c6b`

## Observed problem

The production path accepts one supplied hypothesis and one Runway job, so concept diversity and candidate selection are manual.

## Engineering hypothesis

A plan-first batch of three distinct timed concepts with whole-batch budget preflight and screened candidate states will automate the accepted experiment structure without changing exact product-demo or CTA assets.

## Planned change

Introduce one campaign orchestration boundary covering structured concept planning, controlled storyboard compilation, three independent Runway candidate plans, screening eligibility, and diagnostic scorecards.

## Expected metric impact

Three distinct candidates are planned reproducibly, total paid cost is preflighted before submission, only eligible hooks can be selected, and the final render preserves exact product and CTA composition.

## Results

- Gemini produced three distinct, strictly validated English advertising concepts.
- Three independent Runway `gen4.5` jobs produced three new 5-second hook MP4s.
- `browser-tab-chaos` passed a complete 50-frame artifact review after two explicit, non-billable temporal-provider `503` responses.
- `lost-reservation-panic` was rejected because the phone disappearance at `1.7-1.8s` was confirmed in the cited frames.
- `scattered-map-pins` passed after the reported phone disappearance was confirmed as a camera-crop false positive.
- Two eligible 15-second advertisements were rendered with H.264 video, AAC English narration, exact local product and brand assets, subtitles, and the shared CTA.
- Both rendered videos probe and decode successfully at `720x1280`, last exactly `15.0s`, and pass subtitle safe-area checks.
- Every one of the ten EVAL-7.1 scorecard dimensions is present; unmeasured subjective dimensions remain explicitly unavailable.
- Total paid cost attributable to this campaign run and its diagnostic judge was `$2.009682`; the shared iteration has `$4.007412` remaining.
- The legacy fixed-scenario pairwise judge preferred candidate 05 over `scattered-map-pins` in both orders, while scoring the new video's shared downstream composition `4-5/5`. This is not a valid hook-quality comparison because the judge required the legacy airport hypothesis.

## Decision

`requires_product_owner_review`

## Learning

Plan-first candidate generation works end to end and turns one brief into three independent, durable hypotheses and provider jobs. Event-level confirmation prevented a camera-crop false positive from vetoing a usable video, while a real disappearing-phone defect remained blocked. The remaining evaluator gap is campaign-specific hypothesis scoring: direct pairwise comparison under one fixed hook scenario is invalid across intentionally different creative hypotheses. No candidate is automatically selected or committed as accepted until the product owner reviews the exact retained MP4.

- Candidate provenance: `candidate-pool.json`
- Human reviewed: `False`
