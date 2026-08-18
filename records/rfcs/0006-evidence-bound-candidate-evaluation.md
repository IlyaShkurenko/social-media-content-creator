# RFC-0006: Evidence-bound candidate evaluation and paid-call preflight

- Status: Accepted
- Date: 2026-08-18
- Decision owners: Project maintainers and product owner
- Decision type: Evaluator architecture, paid execution safety, and campaign evidence

## Summary

Extend RFC-0005 with an offline paid-call preflight, candidate-specific semantic
evaluation, invariant-only cross-concept comparison, honest versioned
scorecards, and crash-safe provider operation recovery. These changes do not
automatically accept or rank different advertising hypotheses. They create the
evidence boundary required to calibrate that decision against future
artifact-bound product-owner labels.

## Context

Experiment 013 produced intentionally different hook concepts. The legacy
pairwise evaluator compared every hook to one airport-specific scenario, so it
penalized a map concept for not depicting an airport even when the video matched
its own hypothesis. The campaign finalizer also mapped render completion,
managed source assets, and subtitle geometry to semantic scores of `1.0`, which
overstated what had actually been measured.

Several paid-call failure paths could additionally resubmit an accepted provider
operation after a process crash or an ambiguous response. Local schema and
prompt defects were discoverable only after provider work began.

## Decision

### 1. Candidate semantics use the candidate's own compiled contract

One-video semantic evaluation is bound to the exact concept, compiled
storyboard, final MP4, prompt, response schema, model, and provider response.
It reports closed evidence-backed statuses for hypothesis match, target emotion,
first-two-second clarity, hook-to-product bridge coherence, and storyboard
action alignment. Statuses map deterministically to `1.0`, `0.5`, `0.0`, or
`null`; the model does not author aggregate numeric scores.

Raw concept beats that have been superseded during compilation remain hashed for
provenance but are not model-visible action requirements. Different concept
contracts are not directly comparable on these dimensions.

Candidate semantic evaluator `1.2.0` also derives deterministic citation domains
from the compiled storyboard. Hypothesis, emotion, and action-alignment evidence
must remain inside the hook; first-two-second clarity is limited to `0-2000 ms`;
and bridge evidence is limited to the one-second window on either side of the
hook/product boundary. This measurement correction requires a fresh labelled
baseline before the version can influence automatic acceptance.

### 2. Pairwise comparison is restricted to shared invariants

Cross-concept A/B evaluation may inspect only the declared shared downstream
contract: transition mechanics, product demonstration, audiovisual continuity,
brand composition, CTA clarity, and professional finish. It runs two blind
passes with reversed order and retains each pass separately. Hook setting,
actor, prop, emotion, and opening action from either concept are excluded.

The resulting candidate credit is diagnostic. It cannot establish product
acceptance, and a partial one-pass result cannot establish an evaluator
baseline.

### 3. Scorecards distinguish facts from semantic measurements

Render success, audio-stream presence, source-asset provenance, and subtitle
safe-area geometry remain named technical evidence. They do not prove
audiovisual correctness, final-frame brand fidelity, or CTA clarity. A semantic
dimension without direct validated evidence is stored as `null` with a reason.

Candidate, invariant, and scorecard evaluators declare independent versions.
Evidence is accepted only when its schema, hashes, operation identity, provider
metadata, deterministic mappings, and exact retained-video SHA-256 all validate.
Measurement changes require a new version and a fresh baseline or labelled
reference before automatic acceptance can consume them.

### 4. Offline preflight gates paid execution

Campaign execution has two offline stages. `planner_ready` validates the brief,
template, managed assets, Gemini transport schema, provider configuration,
operation identities, and worst-case campaign budget before planning.
`generation_ready` additionally validates the exact returned concepts, compiled
storyboards, Runway payloads, prompt-length limits, temporal evaluator contract,
and remaining generation plus screening budget before the first generated-media
submission.

Preflight performs no network request and creates no budget operation. Its
semantic hash binds code, inputs, assets, provider endpoint/version, and
evaluator transport while excluding timestamps and mutable point-in-time budget
snapshots. Paid execution recomputes that hash and fails closed when it is
missing or stale.

### 5. Paid provider operations are crash-safe and conservative

Every paid operation has a deterministic ledger identity. A complete,
hash-matching checkpoint may replay without provider work. A ledger operation
without matching complete evidence blocks automatic resubmission. Submitted
Runway jobs resume polling and download by their retained provider job ID;
terminal videos are reused only after exact request, plan, state, eligibility,
managed-path, and video-hash validation.

Only an explicit permanent provider 4xx rejection, excluding `408`, `409`,
`425`, and `429`, is treated as definitively non-billable. Timeouts, transport
failures, transient 4xx, malformed accepted responses, and 5xx outcomes remain
ambiguous and retain one conservative charge or reservation. Runway credentials
are sent only to the approved HTTPS origin and API version, with redirects
disabled.

### 6. Ledger reconciliation is explicit and non-destructive

A historical submitted entry with a provider response identifier but incomplete
usage evidence may be reconciled to `manual_charge` only by exact operation ID
and an English reason. The transition preserves amount, provider identifier,
and creation time; it cannot release the charge. Auditing is read-only by
default and never infers mutation from age or description.

## Baseline and activation policy

The evaluator implementation may land before calibration only when it remains
diagnostic and cannot grant automatic acceptance. A complete invariant baseline
requires both reversed passes and a tracked compact record. A provider-blocked
partial run is retained as a failed research attempt, not promoted to a
baseline, and its experiment number is never reused.

## Consequences

Creative hooks can be judged for whether they fulfill their own ideas without
being forced into one legacy story. Shared product and CTA quality remains
comparable across concepts. Paid retries become explicit and auditable, local
transport defects fail before network work, and scorecards stop presenting
unmeasured semantics as certainty.

The immediate limitation remains: selecting the best premise among different
concepts still requires artifact-bound product review until enough accepted and
rejected examples exist for calibration.

## References

- `records/rfcs/0005-hypothesis-aware-candidate-orchestration.md`
- `docs/specs/mixed-media-advertising-pipeline.md`
- `docs/specs/video-quality-feedback-loop.md`
- `feedback-loop/video-quality/goal.md`
