# 007-runway-generated-hook

- Created: `2026-08-17T16:36:47.054695+00:00`
- Kind: `experiment`
- Scenario: `scenario.json`
- Hypothesis: Replacing only the hook source with a gen4.5 airport traveller visibly struggling with phone-based planning improves hook timeline alignment while preserving the exact product demo and CTA

## Change

Only the first five-second hook source changed: the controlled Pexels clip was
replaced with one Runway `gen4.5` text-to-video generation depicting an
overwhelmed traveller using a phone in an airport. Scene timing, English
narration, exact TICT product capture, approved logo and mascot, CTA, subtitle
layout, and local FFmpeg composition remained unchanged. The provider job cost
`$0.60` and completed in `158.877` seconds.

## Results

- Primary `timeline_alignment_f1`: `0.952381`
- Previous comparable score: `0.842105`
- Delta: `+0.110276`
- Enforced constraints pass: `True`
- All goal constraints verified: `False`
- Observation mode: `human_fixture`

## Decision

`provisional_requires_review`

The primary metric improved by `0.110276` and all currently automated hard
constraints pass. Publication still awaits explicit user review because ASR
WER and automated brand-fidelity checks are not yet implemented.

## Manual disposition

`kept_after_user_review`

On 2026-08-17 the user accepted the generated non-product phone screen as
correct for this hook: the scene describes planning frustration before the
tict product demonstration, so it must not be judged as approved product UI.
The accepted follow-up requirement is to make screen intent explicit in each
storyboard scene and evaluate the rendered result against that intent.

## Notes

The v0 human observation fixture is reproducible but does not replace automated vision/ASR evaluation. Pending required constraints block automatic acceptance.
