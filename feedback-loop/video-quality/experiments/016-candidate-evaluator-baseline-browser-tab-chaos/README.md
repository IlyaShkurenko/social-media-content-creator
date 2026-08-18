# Candidate evaluator baseline

This record calibrates deterministic evaluator replay against two exact MP4
artifacts. It is not a product-improvement experiment and it is not a keep
decision.

## Lifecycle and authority

- Lifecycle: `baseline_established`
- Decision: `evaluator_baseline_established`
- Acceptance authority: `false`
- Product improvement assessed: `false`
- Keep decision made: `false`

## Versioned evaluators

- Candidate semantic evaluator: `1.2.0`
- Shared invariant evaluator: `1.0.0`
- Record schema: `1`
- Scorecard schema: `1.0`

## Exact artifacts and product-owner labels

- Accepted reference baseline: `feedback-loop/video-quality/experiments/012-screen-stable-runway-batch/artifacts/video.mp4`
  - SHA-256: `bd3a17ef31339160eb19062568e6f4c0aac3e17ec8afbfa2007f841aeae13c52`
  - Label: `accept`
  - Reviewer: `user`
  - Reason: The exact reference MP4 was previously accepted
- Candidate: `feedback-loop/video-quality/experiments/013-hypothesis-aware-candidate-orchestration/artifacts/campaign/renders/browser-tab-chaos/final.mp4`
  - SHA-256: `fb48ec133c903e1bb6e74c77e06b6dab1a511fc787f57ebca7802d4e9022ef5f`
  - Label: `pending`
  - Reviewer: `user`
  - Reason: Exact candidate MP4 review is still pending
  - Human-acceptance value: `null`
  - Availability: exact MP4 product-owner review is pending

`artifacts/reference.mp4` and `artifacts/video.mp4` are immutable snapshots.
`inputs.json` embeds sanitized evidence and source hashes; `metrics.json` is
recomputed from those exact inputs during offline replay.
