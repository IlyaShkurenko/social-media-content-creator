# Video-quality feedback-loop acceptance

## Purpose

Define the deterministic acceptance boundary for a candidate advertising-video experiment. Perceptual scoring remains owned by the versioned evaluator; this contract governs how its results are acted upon.

## Referenced decisions

- `records/rfcs/0001-experimental-mixed-media-advertising-pipeline.md`
- `records/rfcs/0002-versioned-gemini-video-quality-judge.md`
- `records/rfcs/0003-temporal-screening-and-artifact-retention.md`
- `records/rfcs/0004-human-calibrated-video-acceptance.md`
- `records/rfcs/0005-hypothesis-aware-candidate-orchestration.md`

## Requirements

### EVAL-1.1 — Honest metric availability

Any declared metric that cannot currently be measured MUST be emitted as `null` with a machine- or human-readable reason. The system MUST NOT omit the metric or invent a value.

### EVAL-1.2 — Pending constraints block automatic acceptance

When a candidate improves the primary metric but one or more required goal constraints remain unverified, the experiment decision MUST be `provisional_requires_review`. It MUST NOT be marked `keep` automatically.

### EVAL-1.3 — Verified improvement may be kept

When a comparable candidate improves the primary metric and all required goal constraints are verified, the experiment decision MUST be `keep`.

### EVAL-1.4 — No automatic improvement requires review or rejection

When a comparable candidate does not improve the automated preference metric, it MUST NOT be kept automatically. A candidate that passes every enforced constraint MAY advance to explicit product-owner review under EVAL-6.2; otherwise the decision MUST be `reject_no_primary_improvement`.

### EVAL-2.1 — Comparable storyboard variants

Stock and generated-media candidates MUST be compared against the same storyboard version, narration, timing contract, required assets, and evaluator version. Any deliberate difference MUST be declared as the experiment variable.

### EVAL-2.2 — Cost and latency evidence

Every paid candidate MUST report estimated cost, recorded charge, generation latency, and remaining iteration budget. An unavailable value MUST be `null` with a reason rather than omitted or invented.

### EVAL-2.3 — Deterministic subtitle safe area

The mixed-media renderer MUST emit per-scene subtitle bounding boxes. Every box MUST remain inside the declared portrait safe area, stay outside reserved product-UI and CTA content zones, and remain below the maximum subtitle-height ratio before `subtitle_safe_area_pass` may be reported as verified. A CTA subtitle MAY be deterministically suppressed when the same call to action is already rendered as approved on-screen text.

### EVAL-2.4 — Screen-policy-aware evidence

Screen observations MUST be compared with each scene's declared screen policy. A generic screen under `non_product_context` is compliant; the same screen under `approved_product_ui` is non-compliant. Reports MUST identify the scene, declared policy, observed screen class, pass/fail result, evidence timestamp, and reason. Until a versioned vision judge produces those fields, human fixtures MAY supply them but automatic acceptance MUST remain blocked.

### EVAL-2.5 — Exact brand-text and pronunciation evidence

The evaluator MUST report exact-case canonical brand spelling independently from case-insensitive subtitle token F1. It MUST report brand pronunciation as unavailable until ASR or phoneme evidence can distinguish `/tɪkt/` from spelled-out initials. A deterministic assertion about the text sent to TTS is implementation evidence, not proof of the rendered audio.

### EVAL-3.1 — Compact reproducible experiment records

New experiment directories SHOULD contain only the human decision record, machine metrics, one content-addressed input manifest, and useful failure logs. Unchanged shared fixtures MAY be referenced by repository path plus SHA-256 instead of copied. Large MP4s and decoded frames remain ignored artifacts. The stable evaluate command MUST verify referenced hashes and MUST retain enough sanitized inputs to reproduce the decision without storing credentials or signed provider URLs. Existing historical experiment directories remain immutable.

### EVAL-3.2 — Staged experiment lifecycle

A product experiment MUST move through explicit `planned`, `evaluated`, and final `kept` or `reverted` states. `experiment-start` MUST reproduce and freeze a comparable baseline before allocating the next identity, and MUST record the observed problem, falsifiable engineering hypothesis, one coherent planned change, expected metric impact, starting revision, and baseline evidence before candidate code or metrics are introduced. `experiment-evaluate` MUST add candidate inputs and results to that same identity without rewriting the frozen plan. `experiment-finish` MUST record learning and a final decision; it MUST reject `keep` when the primary metric did not improve, and MUST require explicit human-review evidence before keeping a provisional result. Final git revert, validation, commit, and push remain deliberate agent actions governed by the scoped experiment protocol.

### EVAL-3.3 — Mandatory final artifact retention

Every `evaluated`, `kept`, or `reverted` experiment MUST retain its exact final MP4 at `artifacts/video.mp4`, record its SHA-256 in `inputs.json`, and verify that path and hash before replay or finish. A `planned` or `failed_before_evaluation` experiment MAY have no final MP4 but MUST NOT claim candidate metrics or a completed result. Large media remains ignored by Git; tracked records MUST distinguish stored, not-created, and invalid artifacts explicitly. Existing historical experiments remain immutable.

### EVAL-4.1 — Versioned structured judge evidence

Evaluator `0.5` and later live pairwise evidence MUST record the evaluator version, response-schema version, requested model, provider-reported model version when available, prompt SHA-256, scenario SHA-256, and SHA-256 of both MP4 inputs. It MUST contain structured scene observations and MUST NOT contain credentials, authorization headers, provider file URIs, or unrestricted external paths.

### EVAL-4.2 — Order-balanced pairwise preference

Every perceptual comparison MUST contain two blind passes with reversed A/B input order. Candidate credit MUST map a candidate win to `1.0`, a tie to `0.5`, and a baseline win to `0.0`; `visual_judge_win_rate` MUST be the mean of both credits. Missing, duplicated, or contradictory input mappings MUST make the metric unavailable. A self-comparison baseline MUST have primary value `0.5`.

### EVAL-4.3 — Deterministic scene metrics from closed observations

The judge MUST select observed visual tags only from the union of storyboard-declared expected tags and MUST support every selection with scene identity and timestamped evidence. Deterministic evaluator code, rather than the hosted model, MUST calculate timeline-alignment and screen-policy metrics from the aggregated observations. Disagreement that prevents a reliable structured observation MUST remain explicit and fail closed.

### EVAL-4.4 — Pairwise preference cannot override regressions

`visual_judge_win_rate` is a diagnostic pairwise preference metric. A candidate with an improved preference MUST still be rejected as `reject_constraint_regression` when `timeline_alignment_f1` is below its comparable baseline or any enforced technical/product constraint fails. Pending required constraints continue to produce `provisional_requires_review` unless resolved by an explicit review allowed under EVAL-6.2.

### EVAL-4.5 — Paid judge calls share the iteration budget

Every Gemini inference pass MUST check a fail-closed maximum cost against scope `mixed-media-iteration-001` before submission without creating a durable reservation. A returned provider response records its usage-based charge; an ambiguous outcome records the fail-closed maximum as a worst-case charge. The evidence MUST report the preflight maximum, recorded charge, token usage when available, estimated actual cost, and remaining shared budget. No judge call may retry automatically after an ambiguous submission failure.

### EVAL-4.6 — Offline replay and comparability

Historical `make evaluate` MUST consume stored judge evidence and MUST NOT call Gemini. It MUST verify evidence and input hashes. Candidate and baseline are comparable only when scenario, evaluator version, judge schema, prompt hash, requested model, and provider-reported model version match; otherwise a new baseline is required.

### EVAL-5.1 — High-frequency temporal evidence

Evaluator `0.6` generated-scene screening MUST sample the declared hook at 10 FPS and produce chronological, timestamped frame strips. Evidence MUST record the source-video SHA-256, scene range, sample rate, sampled frame count, strip hashes, temporal prompt hash, response schema, requested model, provider-reported model version, usage, recorded charge, and remaining budget. Provider credentials, provider file URIs, and unrestricted external paths MUST NOT be persisted.

### EVAL-5.2 — Closed temporal event contract

Temporal events MUST use only `object_disappearance`, `object_duplication`, `orientation_discontinuity`, `screen_visibility_contradiction`, `geometry_deformation`, or `hand_interaction_discontinuity`. Every event MUST include `low`, `medium`, or `high` severity, source timestamps, supporting frame indices, affected object, and an evidence-based reason. Deterministic evaluator code MUST calculate event counts and MUST NOT accept provider-supplied metric values.

### EVAL-5.3 — Temporal events require confirmation before final veto

Any reported high-severity temporal event MUST set `temporal_consistency_pass=false` and make the generated hook ineligible for automatic selection while confirmation is absent, ambiguous, or `confirmed_defect`. A review outcome of `false_positive` MAY clear only the reviewed event and make the hook eligible when no other high event remains. Missing, partial, malformed, hash-mismatched, or wrong-protocol temporal evidence MUST fail closed. The original provider evidence MUST remain immutable.

### EVAL-5.4 — Temporal evidence replays offline

Historical evaluation MUST consume stored temporal evidence and MUST NOT re-extract frames or call Gemini. Candidate and baseline temporal evidence are comparable only when evaluator version, temporal schema, prompt hash, sampling rate, requested model, and provider-reported model version match.

### EVAL-6.1 — Model preference is diagnostic evidence

`visual_judge_win_rate` MUST remain available and reproducible, but MUST NOT be presented as a percentage of video quality or the sole product-acceptance authority. Until a versioned human-labelled calibration demonstrates alignment with the advertising objective, model preference MAY rank candidates for review but MUST NOT overrule an explicit product-owner decision that satisfies EVAL-6.2.

### EVAL-6.2 — Explicit human acceptance may keep a constrained candidate

The product owner MAY keep a retained candidate whose automated preference does not improve when every enforced technical and product constraint passes and no deterministic comparison metric regresses. Human acceptance MUST NOT override any failed enforced constraint. Pending subjective metrics MAY be resolved for that artifact by explicit review.

### EVAL-6.3 — Human review is artifact-bound evidence

A human-review override MUST record the outcome (`accept` or `reject`), reviewer identity, timestamp, retained final-video SHA-256, and an English reason. It MUST preserve the original automated metrics and provider evidence unchanged.

### EVAL-6.4 — Accepted videos seed calibration

Every explicitly accepted or rejected rendered video SHOULD retain its review label as calibration evidence. A future hypothesis-aware or composite automatic metric MUST introduce a new evaluator version, publish its labelled-set agreement, and establish a fresh baseline before becoming an automatic keep rule.

### EVAL-7.1 — Separated candidate scorecard dimensions

Candidate-pool evaluation MUST report hypothesis match, target-emotion strength, first-two-second hook clarity, hook-to-product bridge coherence, storyboard action alignment, temporal eligibility, audiovisual correctness, product/brand fidelity, CTA clarity, and human acceptance as separate fields. An unavailable dimension MUST be `null` with a reason.

### EVAL-7.2 — Eligibility precedes subjective ranking

A candidate with failed enforced technical, temporal, product, or brand constraints MUST be excluded before subjective diagnostic ranking. Diagnostic scores MUST NOT make an ineligible candidate selectable.

### EVAL-7.3 — Uncalibrated ranking cannot auto-keep

Until the scorecard demonstrates agreement against artifact-bound human labels under a versioned evaluator, it MAY order eligible candidates for review but MUST NOT automatically keep a final advertisement. Final acceptance continues to follow EVAL-6.2 and EVAL-6.3.

## Executable coverage

`test/bdd/features/video_quality_acceptance.feature` covers the high-value acceptance boundary in EVAL-1.2, EVAL-1.3, EVAL-2.4, the pre-change plan freeze in EVAL-3.2, mandatory artifact verification in EVAL-3.3, order-balanced preference in EVAL-4.2, regression blocking in EVAL-4.4, temporal confirmation in EVAL-5.3, and human acceptance in EVAL-6.2. Lower-level evaluator output, exact brand spelling, temporal extraction, metric calculations, lifecycle transitions, budget transitions, hash verification, and compact-record serialization remain covered by ordinary evaluator tests.
