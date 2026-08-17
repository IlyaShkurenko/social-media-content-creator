# RFC-0003: High-frequency temporal screening and experiment artifact retention

- Status: Accepted
- Date: 2026-08-17
- Decision owners: Project maintainers and product owner
- Decision type: Evaluation architecture, generated-media selection, and experiment persistence

## Summary

Introduce evaluator `0.6` with a high-frequency temporal screening stage for
generated hook footage. The stage samples the declared generated scene at 10 FPS,
builds timestamped frame strips, and asks a versioned structured Gemini judge to
identify short-lived continuity failures that normal video sampling can miss.

Generated-hook experiments may request three to five independent Runway jobs.
Every output is retained locally and screened independently. Only a hook with no
high-severity temporal event may be selected for final rendering and the existing
order-balanced whole-video comparison.

Every evaluated or finished experiment must also retain a hash-matching final MP4
under its ignored `artifacts/` directory. A planned or failed-before-evaluation
record may lack a final video, but it must not claim an evaluated result.

## Context

Experiment 010 changed a hard cut into a 0.35-second dissolve. Both Gemini
pairwise passes reported the baseline and candidate as visually identical even
though their MP4 hashes and boundary frames differed. Manual 5–10 FPS inspection
also exposed a more important failure in the accepted Runway hook: the phone
display and device orientation change discontinuously between adjacent frames.

The current evaluator sends full video to Gemini, whose normal video sampling is
approximately one frame per second. That is adequate for scene identity and
whole-video comparison but is not a reliable detector for subsecond generative
hallucinations. Continuing to optimize with this blind spot would admit visibly
broken footage.

Historical experiments also use several artifact layouts. Some early evaluated
records reference videos outside their experiment directory, while newer records
copy the final MP4 into an ignored artifact directory. The user requires every
real iteration result to remain locally viewable.

## Decision

### 1. Temporal screening is a separate preselection stage

Temporal screening runs on the generated hook before final composition and before
the two-pass whole-video judge. It is an eligibility gate, not the pairwise
primary metric. This avoids paying to render and compare a candidate that already
contains an obvious generative continuity failure.

### 2. Sampling is explicit and content-addressed

The screener extracts frames at exactly 10 FPS over the declared hook range. It
assembles chronological strips with visible frame index and source timestamp.
Evidence records the source-video hash, sample rate, scene bounds, every strip
hash, prompt hash, schema version, requested model, provider model version, usage,
recorded charge, and remaining shared budget.

Historical replay consumes the stored structured evidence and does not call the
provider or regenerate strips.

### 3. The event vocabulary is closed

The structured judge may report only:

- `object_disappearance`;
- `object_duplication`;
- `orientation_discontinuity`;
- `screen_visibility_contradiction`;
- `geometry_deformation`;
- `hand_interaction_discontinuity`.

Each event includes severity, start/end timestamps, supporting frame indices, the
affected object, and an evidence-based reason. The judge is instructed to use
`high` only for a clearly visible production-breaking contradiction.

### 4. Eligibility fails closed

Deterministic code calculates event counts and `temporal_consistency_pass`. Any
high-severity event makes the generated hook ineligible. Missing, partial,
hash-mismatched, malformed, or wrong-protocol evidence is also ineligible. Medium
and low events remain visible metrics but do not independently veto evaluator
`0.6` candidates.

### 5. Runway candidate pools contain independent jobs

A batch contains three to five independently submitted Runway jobs with unique
operation and provider job identifiers. Reusing an existing output is permitted
only when provenance explicitly identifies the run as a zero-cost rerender; it
must never be presented as a new generation.

Every generated output is downloaded before screening and retained under the
experiment's ignored artifact tree. Selection may rank eligible candidates, but
it must never return a temporally failing candidate. When all candidates fail,
the batch ends without a final pairwise judge and retains all failure evidence.

### 6. Final experiment artifacts are mandatory

`experiment-evaluate` copies the exact final MP4 to
`experiments/<id>/artifacts/video.mp4` and records its SHA-256. Replay and finish
verify that file and hash. An evaluated, kept, or reverted record cannot complete
without it. Large media remains ignored by Git; compact manifests and metrics
remain tracked.

Existing historical experiment records remain immutable and are not retroactively
claimed to contain artifacts they never retained.

### 7. Paid calls share the existing budget

Runway generations and Gemini temporal calls use the existing
`mixed-media-iteration-001` scope and `$10.00` ceiling. Gemini calls use the
non-reserving preflight and usage-based accounting accepted in RFC-0002. Runway
keeps its existing accepted-job accounting. No ambiguous provider submission is
automatically retried.

## Failure behaviour

- Frame extraction failure makes temporal evidence unavailable and the hook
  ineligible.
- A partial or malformed temporal response is retained but cannot pass.
- A high-severity event preserves the candidate MP4 and evidence for review but
  blocks selection.
- If every generated candidate fails, no candidate is silently substituted and
  no whole-video pairwise judge runs.
- A missing or hash-mismatched final artifact blocks experiment finish.

## Alternatives considered

### Continue using the full-video judge alone

Rejected because experiment 010 demonstrated that it can describe visibly
different subsecond edits as identical.

### Sample only one midpoint frame per scene

Rejected because orientation and screen hallucinations are temporal transitions,
not single-frame scene-class errors.

### Track phones entirely with deterministic computer vision

Deferred. Object tracking and optical flow can support later evaluator versions,
but small devices, occlusion, and semantic front/back state still require a
vision interpretation layer in the current project.

### Store MP4 files in Git

Rejected because generated media is large and unsuitable for normal source
history. Local ignored retention plus tracked content hashes preserves usability
and compact history.

## Consequences

Generated-media experiments become more expensive than a single Runway output,
but obviously broken candidates are rejected before final rendering. Every real
iteration remains locally inspectable. Evaluator `0.6` requires a new baseline
because its acceptance constraints and evidence protocol differ from `0.5`.

## References

- `records/rfcs/0001-experimental-mixed-media-advertising-pipeline.md`
- `records/rfcs/0002-versioned-gemini-video-quality-judge.md`
- `docs/specs/mixed-media-advertising-pipeline.md`
- `docs/specs/video-quality-feedback-loop.md`
- `feedback-loop/video-quality/experiments/010-semantic-crossfade-bridge/README.md`
