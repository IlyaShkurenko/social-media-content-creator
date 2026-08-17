# RFC-0002: Versioned Gemini video-quality judge

- Status: Accepted
- Date: 2026-08-17
- Decision owners: Project maintainers and product owner
- Decision type: Evaluation architecture and paid external integration

## Summary

Introduce evaluator `0.5` with a versioned Gemini 3.6 Flash video judge. The
judge compares a candidate MP4 with the current baseline in two blind passes,
swapping the A/B order between passes. It returns structured scene observations
and a rubric-based pairwise preference. Deterministic code converts that evidence
into metrics and acceptance constraints.

For evaluator `0.5`, `visual_judge_win_rate` is the experiment primary metric.
It is never sufficient by itself: timeline alignment, technical media checks,
screen policy, exact brand requirements, evaluator comparability, and any other
verified goal constraints remain non-regression gates. Unverified constraints
continue to require explicit human review.

Every live judge call uses the existing `mixed-media-iteration-001` budget. The
provider response is persisted as sanitized evidence so historical experiments
replay offline without another paid or nondeterministic request.

## Context

Evaluator `0.4` deterministically probes and decodes the final MP4, checks its
technical contract, and compares a frozen human observation fixture with the
storyboard. Its `timeline_alignment_f1` is already `0.952381` for baseline 008,
but it cannot distinguish a hard cut from a coherent transition or compare the
overall quality of two otherwise valid edits.

Optimizing the remaining false positive would reward removal of visible luggage
from an airport scene rather than improve the advertisement. A perceptual judge
is therefore required before further paid product experiments are meaningful.

Gemini 3.6 Flash accepts video and audio inputs, supports structured JSON output,
and exposes token usage. Gemini video sampling is approximately one frame per
second, so evaluator-owned boundary frames supplement the full videos when fast
transitions require closer evidence.

## Decision

### 1. Live judgement and deterministic evaluation are separate stages

The live judge produces a compact evidence document. The evaluator consumes that
document without network access. It verifies the scenario, evaluator protocol,
prompt, model, and video hashes before calculating metrics.

This separation keeps `make evaluate EXPERIMENT=...` reproducible and prevents a
historical score from drifting when a hosted model changes.

### 2. Pairwise judgement is order-balanced

Every comparison contains exactly two independent passes:

1. A = baseline, B = candidate;
2. A = candidate, B = baseline.

The prompt does not identify which label is the baseline. Each verdict maps to
candidate credit: candidate win `1.0`, tie `0.5`, baseline win `0.0`.
`visual_judge_win_rate` is the arithmetic mean of both credits.

Contradictory positional wins therefore produce `0.5` and cannot beat the
self-comparison baseline. Both passes and both video hashes are mandatory.

### 3. The response is structured evidence, not an unrestricted opinion

Each pass returns:

- A/B/tie preference and confidence;
- rubric scores for editing continuity, storyboard alignment, audiovisual
  coherence, product-demonstration clarity, and professional finish;
- timestamped reasons;
- scene observations for both videos;
- observed visual tags selected from the storyboard's closed tag vocabulary;
- screen class and tict identity/approved-asset evidence;
- brand-asset fidelity evidence when required.

Deterministic code aggregates the two passes. It computes timeline alignment and
screen-policy metrics from the structured observations; it does not ask Gemini
to invent the final metric values.

### 4. Acceptance is lexicographic and fail-closed

A candidate may advance only when all of the following are true:

- it is comparable with the baseline under evaluator `0.5`;
- `visual_judge_win_rate` is greater than the baseline value;
- `timeline_alignment_f1` does not regress below the baseline;
- every enforced technical and product constraint passes;
- the judge evidence is complete, order-balanced, and hash-valid.

If a required goal constraint remains unavailable, the decision is
`provisional_requires_review`. A perceptual win never overrides a deterministic
failure or a comparison-constraint regression.

### 5. Model drift invalidates direct comparison

The evidence records the requested model ID, provider-reported model version when
available, evaluator version, prompt SHA-256, response schema version, and input
hashes. A candidate produced under a different evaluator protocol or
provider-reported model version requires a new baseline.

### 6. Paid calls share the existing iteration ledger

Before each inference request, the judge checks a fail-closed maximum cost against
the existing `$10.00` iteration scope using the documented model price snapshot,
video duration estimate, prompt allowance, and output-token cap. This preflight
check creates no durable reservation. A returned response records its usage-based
charge even if parsing or evaluation later fails. An ambiguous request failure
records the maximum as a worst-case charge and is not automatically retried.

Actual token usage and estimated actual cost are recorded separately from the
conservative ledger charge. Uploading a file does not authorize inference.

### 7. Provider files are temporary

Videos are uploaded only for the comparison, awaited with a bounded timeout, and
deleted in a `finally` path. API keys, authorization headers, provider file URIs,
and unrestricted local paths are never persisted in experiment records.

## Calibration and baseline

Evaluator `0.5` first evaluates baseline 008 against itself. The expected primary
score is `0.5`; the evidence exposes positional preference or non-tie behaviour
rather than silently hiding it. That result becomes a new immutable baseline.

The first candidate comparison remains subject to human review. Its result and
the existing human preference for the accepted Runway hook provide the initial
calibration record. Later evaluator versions may add a larger fixed human-labelled
pair set before enabling fully automatic perceptual acceptance.

## Failure behaviour

- Missing Gemini credentials fail before upload or budget preflight.
- Missing or changed input hashes fail before metric calculation.
- Incomplete, malformed, or single-pass evidence is unavailable, never inferred.
- Provider rejection before inference records no charge when the request is known
  not to have been accepted.
- Ambiguous network failures are not retried and record a worst-case charge.
- Model-version drift, prompt drift, or evaluator-version drift makes the pair
  non-comparable and requires a new baseline.

## Alternatives considered

### Optimize `timeline_alignment_f1` from the frozen human fixture

Rejected because the remaining error is not the visible quality problem and
would encourage evaluator gaming.

### Use one unstructured aesthetic score

Rejected because it is order-sensitive, difficult to audit, and cannot supply
scene-level evidence for deterministic constraints.

### Call Gemini during every replay

Rejected because hosted-model output is nondeterministic, paid, and subject to
model drift. Historical evaluation must remain offline.

### Make perceptual preference the only acceptance rule

Rejected because a visually attractive candidate may still show the wrong
product screen, damage the brand, regress storyboard alignment, or fail technical
media requirements.

## Consequences

The feedback loop can now optimize montage and whole-video coherence rather than
only fixture tags. It also gains a paid evaluator dependency, a new evidence
schema, conservative cost accounting, and an explicit calibration obligation.

The judge remains experimental. Versioning, two-pass order balancing, structured
evidence, deterministic gates, offline replay, and human review are deliberate
controls against treating an uncalibrated hosted-model opinion as ground truth.

## References

- [Gemini video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)
- [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Gemini Files API](https://ai.google.dev/gemini-api/docs/files)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- `records/rfcs/0001-experimental-mixed-media-advertising-pipeline.md`
- `docs/specs/video-quality-feedback-loop.md`
- `feedback-loop/video-quality/goal.md`
