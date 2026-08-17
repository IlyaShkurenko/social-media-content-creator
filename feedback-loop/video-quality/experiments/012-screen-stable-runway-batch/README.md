# 012-screen-stable-runway-batch

- Started: `2026-08-17T18:28:58.071058+00:00`
- Status: `kept`
- Start revision: `ae2666c269305151f2bf9b5f0821d2e994057170`
- Scenario: `evals/dataset/mixed-media-stock-baseline-001.json`

## Frozen baseline

- Experiment: `experiments/011-baseline`
- Evaluator: `0.6.0`
- Primary `visual_judge_win_rate`: `0.500000`
- Metrics SHA-256: `d7bbafaaf254232c9aa0ca0d0a7a9413733711ebaf85eaaf7c47056c2d683270`

## Observed problem

The current generated hook contains a high-severity phone screen visibility contradiction between 0.3 and 0.9 seconds.

## Engineering hypothesis

A screen-hidden, single-device motion constraint applied across three independent Runway generations will yield at least one hook with zero high-severity temporal events while preserving storyboard alignment.

## Planned change

Change the Runway hook generation policy to keep one phone screen facing away from camera throughout the shot, then generate and temporally screen three independent candidates before selection.

## Expected metric impact

At least one candidate passes temporal_consistency without regressing timeline_alignment_f1, screen policy, or final visual preference.

## Results

- Candidate `visual_judge_win_rate`: `0.250000`
- Delta: `-0.250000`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `gemini_pairwise_v1`
- Candidate revision: `ae2666c269305151f2bf9b5f0821d2e994057170`

## Decision

`kept_after_human_review`

## Learning

The retained candidate passes every enforced constraint and the product owner prefers its stronger hypothesis-aligned emotion. The 0.25 visual_judge_win_rate remains diagnostic evidence and must not overrule this artifact-bound acceptance label.

- Finished: `2026-08-18T11:36:56.076346+00:00`
- Human reviewed: `True`
- Reviewer: `user`
- Review outcome: `accept`
- Reviewed artifact SHA-256: `bd3a17ef31339160eb19062568e6f4c0aac3e17ec8afbfa2007f841aeae13c52`
- Review reason: Candidate 05 is acceptable as the production reference and better matches the intended emotional planning-frustration hook.
- Candidate provenance: `candidate-pool.json`
