# Video-quality feedback-loop acceptance

## Purpose

Define the deterministic acceptance boundary for a candidate advertising-video experiment. Perceptual scoring remains owned by the versioned evaluator; this contract governs how its results are acted upon.

## Requirements

### EVAL-1.1 — Honest metric availability

Any declared metric that cannot currently be measured MUST be emitted as `null` with a machine- or human-readable reason. The system MUST NOT omit the metric or invent a value.

### EVAL-1.2 — Pending constraints block automatic acceptance

When a candidate improves the primary metric but one or more required goal constraints remain unverified, the experiment decision MUST be `provisional_requires_review`. It MUST NOT be marked `keep` automatically.

### EVAL-1.3 — Verified improvement may be kept

When a comparable candidate improves the primary metric and all required goal constraints are verified, the experiment decision MUST be `keep`.

### EVAL-1.4 — No primary improvement is rejected

When a comparable candidate does not improve the primary metric, the experiment decision MUST be `reject_no_primary_improvement`, regardless of verified constraints.

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

## Executable coverage

`test/bdd/features/video_quality_acceptance.feature` covers the high-value acceptance boundary in EVAL-1.2, EVAL-1.3, EVAL-2.4, and the pre-change plan freeze in EVAL-3.2. Lower-level evaluator output, exact brand spelling, metric calculations, lifecycle transitions, hash verification, and compact-record serialization remain covered by ordinary evaluator tests.
