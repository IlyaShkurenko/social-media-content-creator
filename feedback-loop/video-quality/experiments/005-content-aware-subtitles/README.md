# 005-content-aware-subtitles

- Created: `2026-08-17T16:20:06.607415+00:00`
- Kind: `experiment`
- Scenario: `scenario.json`
- Hypothesis: Reserving a product-safe caption zone and suppressing duplicate CTA subtitles removes visual false positives without changing the hook or storyboard intent

## Change

The deterministic renderer now measures subtitle overlays per scene, reserves a
caption-safe zone below the exact product UI capture, and suppresses the CTA
subtitle when the same approved CTA copy is already present in the composed
frame. The storyboard, narration, hook footage, scene durations, and product
assets are unchanged from the comparable baseline.

## Results

- Primary `timeline_alignment_f1`: `0.842105`
- Previous comparable score: `0.761905`
- Delta: `+0.080200`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `human_fixture`

## Decision

`provisional_requires_review`

## Manual disposition

`kept_after_user_review`

On 2026-08-17 the user explicitly instructed the feedback loop to retain,
commit, and push successful improvements. This accepts the product change for
publication while preserving the evaluator's provisional status and its
pending automated ASR and brand-fidelity checks.

## Notes

The v0 human observation fixture is reproducible but does not replace automated vision/ASR evaluation. Pending required constraints block automatic acceptance.
