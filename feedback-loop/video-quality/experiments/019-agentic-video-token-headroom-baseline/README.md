# Agentic video evaluator baseline

Evaluator: 1.2.0. Status: complete.

Same video, model and contract; only processing mode varies. Findings are diagnostic.
No creative winner or evaluator accuracy is established without human labels.

The retained video is artifacts/video.mp4. Inputs and complete sanitized evidence
are tracked in inputs.json and metrics.json, including processing failures.

## Question and declared change

Can the same request-driven assessment run through both processing modes with
verifiable agentic navigation, without introducing scenario-specific rules?
After the incomplete 4096-token response in experiment 018, use 8192 response
tokens for both modes and preserve validation-failure diagnostics. This is an
evaluator integration baseline, not a generated-video improvement experiment.
The previous failure's exact cause remains unknown; a successful subsequent call
does not prove that token headroom alone fixed it.

## Live result

Both calls completed and passed response validation. Same 15-second MP4,
scattered-map-pins concept, compiled storyboard, model and prompt. Only processing
mode differs. Agentic returned two linked processing_call/processing_result pairs;
static returned none. No provider thought text or opaque signatures are retained.

| Mode | Wall time, including upload | Conservative accounted estimate | Tool-input tokens |
| --- | ---: | ---: | ---: |
| Static | 18.627 s | $0.021050 | 0 |
| Agentic | 35.869 s | $0.052230 | 7691 |

Both flagged missing hook headline text in favor of narration captions. Agentic
gave more detailed audio/script and scene observations. Neither report proves
frame-perfect continuity or approved-asset identity: no independent reference
images or event-level human labels were supplied. Findings about a missing face,
an illuminated display, or superseded concept beats require review against the
compiled contract, not automatic rejection. These are model claims, not verified
ground truth. The contract itself contains conceptual versus compiled-action
differences; the prompt prioritizes compiled actions, but model adherence is not
guaranteed. This is a remaining calibration concern, not a reason to force a
particular prop or visual concept into the evaluator.

## Relationship to existing evaluators

The same MP4 has candidate semantic 1.2.0 and invariant 1.0.0 evidence in
`../015-candidate-evaluator-baseline-scattered-map-pins/`. That record reports
storyboard_action_alignment=0.5 while hypothesis_match=1.0. The new modes also
report action discrepancies, but their requirement-level statuses are not the
same metric and must not be numerically compared. Existing acceptance gates,
temporal checks and human review remain unchanged. No automatic winner, accuracy
score or improved product-video quality is asserted from this one video.

## Cost, replay and disposition

This pair totals $0.073280. Across integration attempts 017–019, the shared ledger
accounts for $0.145653, including failed billable work. The resulting iteration
balance is $3.476724 of the original $10 cap. Estimates use conservative
undiscounted rates and are not invoices. The MP4 is an unchanged retained copy,
not a new Runway generation.

Offline verification:

```bash
make evaluate EXPERIMENT=experiments/019-agentic-video-token-headroom-baseline
```

Validation: 104 evaluator tests, 718 repository tests (11 skipped), 27 BDD
scenarios, lint, and 77% repository coverage. Replay verifies exact video,
contract, prompt, schema, generation settings and sanitized response hashes.

Disposition: retain the opt-in diagnostic integration requested by the user;
do not promote it to an acceptance gate. Before selecting a replacement judge,
calibrate on multiple artifact-bound human-labelled examples, including valid
stylistic transitions and confirmed temporal defects under different requests.
