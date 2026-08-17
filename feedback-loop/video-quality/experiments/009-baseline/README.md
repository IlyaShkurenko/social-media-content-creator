# 009-baseline

- Created: `2026-08-17T18:01:59.909944+00:00`
- Kind: `baseline`
- Scenario: `mixed-media-stock-baseline-001.json`
- Hypothesis: Current controlled artifact establishes the active evaluator behavior.

## Change

Establish evaluator 0.5 with two order-balanced Gemini 3.6 Flash passes over the
real accepted Runway MP4. The live responses are sanitized, embedded in the
experiment inputs, and replayed offline without another provider call.

## Results

- Primary `visual_judge_win_rate`: `0.500000`
- Previous comparable score: `n/a`
- Delta: `n/a`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `gemini_pairwise_v1`

## Decision

`baseline_established`

## Notes

Both self-comparison passes returned a tie with confidence 1.0. Automated video,
timeline, screen-policy, subtitle-layout, and brand-fidelity constraints pass.
Timestamped ASR and brand-pronunciation checks remain pending and therefore block
fully automatic acceptance.
