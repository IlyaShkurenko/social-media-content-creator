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

## Executable coverage

`test/bdd/features/video_quality_acceptance.feature` covers the high-value acceptance boundary in EVAL-1.2 and EVAL-1.3. Lower-level evaluator output and metric calculations remain covered by ordinary evaluator tests.
