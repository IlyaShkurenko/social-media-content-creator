# RFC-0005: Hypothesis-aware advertising candidate orchestration

- Status: Accepted
- Date: 2026-08-18
- Decision owners: Project maintainers and product owner
- Decision type: Creative planning, paid batch orchestration, and candidate selection

## Summary

Introduce a plan-first campaign orchestration layer that turns one product brief
into three to five distinct advertising concepts, compiles each concept against
the same exact product-demonstration and CTA template, generates independent
Runway hooks, screens every hook, and renders only an eligible selected concept.

The default batch size is three to preserve the current shared iteration budget.
All candidate costs are preflighted together before the first provider
submission. Every candidate retains its hypothesis, storyboard, prompt,
operation and provider job identifiers, output hash, screening evidence, cost,
latency, and disposition.

## Context

The accepted experiment 012 demonstrated that independent Runway sampling can
produce a usable hook, but its five jobs were orchestrated manually. The current
production path accepts one user-supplied hypothesis, generates one storyboard,
submits one Runway job, and has no durable campaign-level state.

The product objective is broader: the system should propose different creative
hypotheses, describe what happens and when, generate several hooks, reject
technical failures, and assemble the strongest viable advertisement with exact
tict product media, narration, subtitles, and CTA.

## Decision

### 1. Planning produces structured concepts before storyboards

One provider-neutral planning response contains three to five concepts. Every
concept declares a stable ID, falsifiable hypothesis, audience problem, target
emotion, emotional arc, 0–5 second timed hook beats, hook narration, transition
into the product demonstration, and observable hook-quality criteria.

Concepts must be meaningfully distinct in both hypothesis and opening action.
They remain untrusted input and are validated before any generated-media call.

### 2. Product demonstration and CTA remain controlled

Each concept is compiled by replacing only the hook of an approved storyboard
template. Product screen, product narration, brand assets, CTA copy, duration,
and output geometry remain identical across candidates. Runway never redraws the
approved application UI.

### 3. The batch is planned and budgeted atomically

The orchestrator persists a campaign manifest before paid generation. It
calculates the complete maximum Runway cost for all planned jobs and verifies
that amount against the shared ledger before the first submission. Individual
adapter reservations and charges remain unchanged.

If the whole batch does not fit, no job is submitted. A provider failure after
one or more accepted jobs preserves those charges and candidate states; it does
not resubmit completed or ambiguous operations.

### 4. Candidate identity is durable and idempotent

Every candidate receives one deterministic operation ID derived from the
campaign and concept IDs. A completed or ambiguously submitted operation is
never silently reused as a new generation. Resume polls or ingests known jobs;
it does not create another provider job.

### 5. Screening precedes full rendering

Every downloaded hook receives temporal evidence. Missing, malformed, pending,
ambiguous, or confirmed-defect evidence makes it ineligible. A confirmed false
positive follows RFC-0004. Only an eligible candidate may be rendered with the
shared narration, exact product capture, subtitles, music policy, and CTA.

### 6. Ranking dimensions are explicit diagnostics

Candidate scorecards separate:

- hypothesis match;
- target-emotion and hook strength;
- first-two-second clarity;
- hook-to-product bridge coherence;
- storyboard action alignment;
- temporal eligibility;
- audiovisual correctness;
- product and brand fidelity;
- CTA clarity;
- human acceptance.

Unavailable dimensions remain `null` with a reason. Until these scores are
calibrated against accepted/rejected human labels, they rank review order but do
not automatically keep a final advertisement.

## State and artifacts

The campaign manifest records:

- brief and planning-response hashes;
- ordered concepts and compiled storyboard hashes;
- complete cost preflight;
- candidate lifecycle: `planned`, `submitted`, `downloaded`, `screened`,
  `eligible`, `selected`, `rendered`, `rejected`, or `failed`;
- provider provenance and local output SHA-256;
- screening and scorecard references;
- selected candidate and final rendered-video SHA-256.

Large media remains in ignored managed artifact directories. Tracked records do
not contain credentials or expiring provider URLs.

## Failure behaviour

- Invalid or duplicate concepts stop before paid work.
- A batch outside the range three to five is rejected.
- Insufficient total budget stops before the first Runway submission.
- A failed candidate does not erase successful sibling candidates.
- No eligible candidate means no final render and no silent stock fallback.
- Missing exact product or brand assets stop before paid work.
- A scorecard cannot invent values for unavailable evaluation dimensions.

## Consequences

The code gains a campaign-level orchestration boundary while preserving the
existing single-storyboard renderer and Runway adapter. Paid generation becomes
repeatable and auditable. Creative planning can evolve independently from
provider execution, and accepted/rejected results accumulate the labels needed
for later automatic ranking.

## References

- `records/rfcs/0001-experimental-mixed-media-advertising-pipeline.md`
- `records/rfcs/0003-temporal-screening-and-artifact-retention.md`
- `records/rfcs/0004-human-calibrated-video-acceptance.md`
- `docs/specs/mixed-media-advertising-pipeline.md`
- `docs/specs/video-quality-feedback-loop.md`
