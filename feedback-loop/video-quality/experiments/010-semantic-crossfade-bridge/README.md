# 010-semantic-crossfade-bridge

- Started: `2026-08-17T18:03:28.992051+00:00`
- Status: `reverted`
- Start revision: `bfd4256831fc60434e3527d5b748e5a3a8aaeb78`
- Scenario: `evals/dataset/mixed-media-stock-baseline-001.json`

## Frozen baseline

- Experiment: `experiments/009-baseline`
- Evaluator: `0.5.0`
- Primary `visual_judge_win_rate`: `0.500000`
- Metrics SHA-256: `2c2a5291094502567a2c8240778986e25462eec48ca96be386be627be0a9ef66`

## Observed problem

The live airport hook cuts abruptly to the static product demonstration at the five-second boundary.

## Engineering hypothesis

A short crossfade from the generated airport footage into the exact product card will make the product reveal feel intentionally connected and improve pairwise visual quality.

## Planned change

Replace only the hard scene concatenation with a 0.35-second dissolve while preserving the exact scene clips, narration timeline, product assets, and total duration.

## Expected metric impact

Increase visual_judge_win_rate above 0.5 without reducing timeline_alignment_f1 or violating any enforced constraint.

## Results

- Candidate `visual_judge_win_rate`: `0.500000`
- Delta: `+0.000000`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `gemini_pairwise_v1`
- Candidate revision: `bfd4256831fc60434e3527d5b748e5a3a8aaeb78`

## Decision

`reverted`

## Learning

A 0.35-second dissolve preserved all deterministic constraints but produced no pairwise preference; both Gemini passes incorrectly described the videos as identical. One-frame-per-second judging is not sensitive enough for subsecond transitions or phone-state hallucinations, so high-frequency temporal screening must be added before further generated-hook selection.

- Finished: `2026-08-17T18:11:45.846847+00:00`
- Human reviewed: `False`
