# 008-baseline

- Created: `2026-08-17T17:02:28.869556+00:00`
- Kind: `baseline`
- Scenario: `mixed-media-stock-baseline-001.json`
- Hypothesis: Current controlled artifact establishes evaluator 0.4 behavior after the accepted Runway hook and local brand-language correction.

## Change

This baseline keeps the accepted Runway visual track and exact local product/CTA composition. It changes canonical subtitle copy from `TICT` to `tict`, sends the provider-specific alias `tickt` to TTS, records scene-level screen policies, and establishes the compact replayable experiment format for evaluator `0.4.0`. No new paid generation was submitted.

## Results

- Primary `timeline_alignment_f1`: `0.952381`
- Previous comparable score: `n/a`
- Delta: `n/a`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `human_fixture`

## Decision

`baseline_established`

## Notes

The structured human observation fixture is reproducible but does not replace automated vision/ASR evaluation. Actual brand pronunciation remains pending until audio evidence is enabled or the user reviews the rendered result.
