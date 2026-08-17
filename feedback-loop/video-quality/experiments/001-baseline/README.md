# 001-baseline

- Created: `2026-08-17T14:53:58.052872+00:00`
- Kind: `baseline`
- Scenario: `scenario.json`
- Hypothesis: Current controlled artifact establishes evaluator-v0 behavior.

## Change

Baseline records the current artifact and evaluator. For a product experiment, replace this paragraph with the exact code/configuration change before accepting the result.

## Results

- Primary `timeline_alignment_f1`: `0.153846`
- Previous comparable score: `n/a`
- Delta: `n/a`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `human_fixture`

## Decision

`baseline_established`

## Notes

The v0 human observation fixture is reproducible but does not replace automated vision/ASR evaluation. Pending required constraints block automatic acceptance.
