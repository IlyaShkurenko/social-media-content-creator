# RFC-0004: Human-calibrated video acceptance

- Status: Accepted
- Date: 2026-08-18
- Decision owners: Project maintainers and product owner
- Decision type: Evaluation acceptance policy and generated-media selection
- Supersedes: RFC-0002 section 4 and RFC-0003 section 4 where they make an
  uncalibrated model verdict the final acceptance authority

## Summary

Treat Gemini pairwise preference and temporal event reports as structured
diagnostic evidence rather than ground truth. A candidate that passes every
enforced technical and product constraint may be kept after the product owner
explicitly accepts the rendered final MP4, even when its
`visual_judge_win_rate` does not improve. The accepted artifact becomes the
human-labelled reference for later calibration.

A Gemini high-severity temporal event blocks automatic selection until its cited
frames are reviewed. A confirmed production defect vetoes the hook; a confirmed
false positive clears only that reported event. Missing, malformed, or ambiguous
evidence continues to fail closed.

## Context

Experiment 012 generated five independent Runway hooks. Candidate 05 passed all
available enforced constraints and the product owner accepted its final narrated
advertisement. The pairwise judge nevertheless assigned `0.25` because it
preferred the previous hook's subtler emotion, while the product objective
explicitly favored a stronger planning-frustration hook.

The same experiment exposed a temporal false positive: Gemini described a smooth
phone turn in candidate 01 as an orientation discontinuity. The product owner and
dense timestamped frame review agreed that the claimed discontinuity was absent.
These observations prove that neither uncalibrated preference nor a single
temporal model verdict can be the final product-quality authority.

## Decision

### 1. Pairwise preference is diagnostic

`visual_judge_win_rate` remains reproducible, order-balanced evidence and may
rank candidates for review. It is not a percentage quality score and is not the
sole primary acceptance condition. Automatic acceptance based on model
preference remains disabled until a human-labelled calibration set demonstrates
that the metric matches the advertising objective.

### 2. Explicit product-owner acceptance may keep a candidate

The experiment controller may keep a candidate without model-preference
improvement only when:

- the exact final MP4 is retained and hash-verified;
- every enforced technical and product constraint passes;
- no deterministic comparison constraint regresses;
- the product owner explicitly records `accept` for that rendered artifact; and
- reviewer identity, timestamp, artifact hash, outcome, and reason are retained.

Human acceptance cannot override a corrupt render, missing audio, policy failure,
confirmed temporal defect, brand failure, or other enforced constraint.

### 3. Temporal reports require event confirmation

A reported high event has state `pending_confirmation` and is ineligible for
automatic selection. Review uses the cited before/during/after frames or denser
timestamped strips:

- `confirmed_defect` keeps the veto;
- `false_positive` clears the reported event;
- `ambiguous` or absent confirmation remains ineligible.

The confirmation record contains the reviewer, timestamp, event identity,
outcome, and evidence-frame references. It does not change the original Gemini
evidence.

### 4. Human labels calibrate future automation

Accepted and rejected final videos form a labelled calibration history. Future
evaluator work should separate hypothesis/emotional-hook strength, storyboard
coherence, temporal integrity, audiovisual correctness, and overall preference.
No new composite score becomes an automatic keep rule without a new evaluator
version and baseline.

### 5. Experiment 012 is accepted as the first calibrated reference

Candidate 05 from experiment 012 is kept by explicit product-owner review. Its
`0.25` pairwise result remains unchanged as diagnostic evidence. The prompt
constraints that produced the accepted candidate are restored to production
code, and the exact existing final MP4 remains the reviewed artifact.

## Failure behaviour

- A keep without an explicit accepted review and without automatic verified
  improvement is rejected.
- A human accept with any failed enforced constraint is rejected.
- A temporal high event without confirmation remains blocked.
- Review metadata that does not identify the retained artifact hash is invalid.
- Original provider evidence and model scores are never rewritten to match the
  human decision.

## Consequences

The loop now optimizes against the product owner's actual advertising objective
instead of an uncalibrated proxy. Fully autonomous product-quality acceptance is
deferred until enough labelled examples exist. Technical gates and reproducible
provider evidence remain automated, while subjective acceptance is explicit and
auditable.

## References

- `records/rfcs/0002-versioned-gemini-video-quality-judge.md`
- `records/rfcs/0003-temporal-screening-and-artifact-retention.md`
- `docs/specs/video-quality-feedback-loop.md`
- `docs/specs/mixed-media-advertising-pipeline.md`
- `feedback-loop/video-quality/experiments/012-screen-stable-runway-batch/README.md`
