# Mixed-media advertising pipeline

## Purpose

Define the first implementation slice of the provider-neutral advertising pipeline accepted in RFC-0001. The slice converts one product brief and one user-supplied hypothesis into a validated English storyboard, compiles comparable stock and Runway execution plans, enforces one shared `$10.00` iteration budget, and retains enough evidence for controlled evaluation.

## Referenced decision

- `records/rfcs/0001-experimental-mixed-media-advertising-pipeline.md`
- `records/rfcs/0004-human-calibrated-video-acceptance.md`
- `records/rfcs/0005-hypothesis-aware-candidate-orchestration.md`
- `records/rfcs/0006-evidence-bound-candidate-evaluation.md`

## Contract requirements

### STORY-1 — Versioned storyboard contract

#### STORY-1.1 — Explicit timed intent

A storyboard MUST declare its schema version, content language, output aspect ratio, target duration, and ordered scenes. Every scene MUST declare a stable identifier, non-overlapping time range, purpose, visual action, camera intent, narration, expected evidence, and a layered media plan.

#### STORY-1.2 — Preflight before paid work

The pipeline MUST validate the complete storyboard and referenced required assets before any paid provider request. Invalid timing, missing intent, incompatible language/voice, unsupported output geometry, unavailable required assets, or unauthorized paid work MUST stop the run without provider submission.

#### STORY-1.3 — Explicit English baseline

The first vertical slice MUST resolve `en-US` as its content language and MUST use a compatible English narration voice. UI locale, conversation language, previous-task settings, and generic provider defaults MUST NOT override this resolved language.

#### STORY-1.4 — One supplied hypothesis in the legacy first slice

The legacy first vertical slice MUST continue to accept one user-supplied advertising hypothesis and produce one canonical storyboard. The hypothesis-aware campaign path defined by STORY-2 supersedes this limitation without removing compatibility.

#### STORY-1.5 — Explicit device-screen intent

Every scene MUST declare one device-screen policy: `approved_product_ui`, `non_product_context`, `screen_hidden`, or `unconstrained`. The planner and provider compiler MUST preserve that policy. A generic or third-party screen is valid under `non_product_context`; it MUST NOT be treated as a product-fidelity failure merely because it is not tict UI.

### ADPIPE-1 — Provider-neutral execution

#### ADPIPE-1.1 — Planner output is provider-neutral

The Creative Planner MUST describe scene intent and evidence without embedding Runway request fields, Pexels response objects, signed URLs, or provider credentials in the canonical storyboard.

#### ADPIPE-1.2 — Compiled comparable variants

The Storyboard Compiler MUST produce a stock baseline plan and a Runway candidate plan from the same validated storyboard. In the first comparison, only the hook base source changes from stock to generated; narration, scene timing, product capture, CTA, and other declared controls remain equivalent.

#### ADPIPE-1.3 — Layered mixed media

An execution plan MUST support a base layer plus ordered overlays. Initial source kinds are `stock`, `generated`, `product_capture`, `brand_asset`, and `solid_or_graphic`.

#### ADPIPE-1.4 — Opt-in compatibility

The new pipeline MUST be opt-in. Existing subject/script WebUI, API, and CLI generation MUST remain operational while the experimental pipeline is introduced.

### BRAND-1 — Identity-sensitive media

#### BRAND-1.1 — Exact product capture by default

When a scene requires an approved application screen, the default execution plan MUST preserve the approved capture as a local composition layer. It MUST NOT ask the generated-video provider to redraw that screen.

#### BRAND-1.2 — Generative UI is an explicit experiment

Direct use of product UI as generative reference material MUST be marked as an experimental strategy in provenance and evaluated against the exact-composite baseline. It MUST NOT silently replace the baseline strategy.

#### BRAND-1.3 — Managed asset resolution

Product and brand asset identifiers MUST resolve to files inside approved managed asset roots before execution. Raw arbitrary filesystem paths from planner output MUST be rejected.

#### BRAND-1.4 — Canonical spelling and spoken pronunciation

Visible brand copy and subtitles MUST use the canonical lowercase spelling `tict`. Narration synthesis MUST pronounce the brand as `/tɪkt/` ("tickt") without replacing the canonical spelling in subtitles, storyboard copy, logs, or on-screen text. Display text and provider-specific synthesis text MUST remain separately inspectable.

#### BRAND-1.5 — Policy-bound screen handling

An `approved_product_ui` scene MUST resolve at least one approved `product_capture` layer and MUST NOT ask a generative provider to invent the product interface. A `non_product_context` scene MAY show a generic non-tict screen when consistent with the declared action. A `screen_hidden` scene MUST compile an instruction that keeps device displays away from the camera. `unconstrained` MUST NOT imply product identity.

#### BRAND-1.6 — Adaptive deterministic brand layout

The local brand renderer MUST derive end-card placement, spacing, typography, and action sizing from the output geometry, measured visual bounds, and validated semantic layout intent supplied by the storyboard. Layout intent MUST address elements by semantic ID and express relative vertical region, horizontal alignment, and scale without provider-specific pixels. The renderer MUST honor that intent rather than replacing it with generic stack centering. Logo, hero asset, headline, and action MUST remain inside a portrait safe area without overlap and preserve source proportions; an impossible layout MUST fail preflight. Action copy MUST come from the storyboard rather than a product-specific renderer literal. The same layout contract MUST support alternate approved assets, copy lengths, portrait resolutions, and valid compositions without per-asset pixel-coordinate changes. Storyboards without semantic layout intent MAY use the measured automatic flow only as an explicit compatibility fallback.

### RUNWAY-1 — Budgeted generated-video adapter

#### RUNWAY-1.1 — Accepted first benchmark

The first generated hook benchmark MUST compile to Runway `gen4.5` text-to-video with portrait output and a five-second target unless a recorded experiment deliberately supersedes that baseline.

#### RUNWAY-1.2 — Shared iteration budget

All measurable paid work in the current feedback-loop iteration MUST share one `$10.00` ceiling. The system MUST reserve estimated cost before provider submission and MUST reject a submission that would exceed the remaining balance.

#### RUNWAY-1.3 — Conservative charge accounting

Once a provider accepts a billable job, its reserved amount MUST remain charged to the iteration even if the creative output is rejected. A reservation MAY be released only when no billable provider job was accepted.

#### RUNWAY-1.4 — No ambiguous paid retry

The adapter MUST NOT automatically resubmit generation after an ambiguous timeout or unknown submission result. Status polling may retry with bounded backoff without creating another generation.

#### RUNWAY-1.5 — Durable provider evidence

The adapter MUST record a provider job identifier, sanitized request metadata, model, mode, duration, aspect ratio, pricing snapshot, estimated cost, timestamps, terminal status, and downloaded managed output path. API keys, authorization headers, and persistent signed output URLs MUST NOT be stored.

#### RUNWAY-1.6 — Managed output ingestion

Successful provider output MUST be downloaded into managed task storage before the result is exposed to rendering. A temporary provider URL is not a final project artifact.

### RUNWAY-2 — Temporally screened candidate pools

#### RUNWAY-2.1 — Independent three-to-five candidate batch

A temporal-selection experiment MUST request between three and five independent Runway jobs with unique operation and provider job identifiers. Reused provider output MUST be labelled as a zero-cost rerender and MUST NOT be reported as a new generation.

#### RUNWAY-2.2 — Screen before selection

Every downloaded generated hook MUST receive EVAL-5 temporal evidence before selection. A hook with missing or invalid evidence MUST be ineligible. A reported high event MUST remain ineligible until reviewed; `confirmed_defect` or `ambiguous` confirmation remains ineligible, while a `false_positive` confirmation MAY clear only that event. Selection MUST return only eligible hooks; if none pass, the batch MUST finish without silently substituting stock or a rejected generation.

An explicit temporal-provider failure after complete evidence-frame extraction
MAY be cleared only by the complete artifact review defined by EVAL-5.3. This
does not permit a manual pass to override an actual reported defect.

#### RUNWAY-2.3 — Retain every generated candidate

Every generated output in the batch MUST be retained under the experiment's ignored artifact tree with provider job ID, local SHA-256, recorded charge, generation latency, temporal evidence status, and selection disposition in the tracked manifest.

### STORY-2 — Hypothesis-aware concept batches

#### STORY-2.1 — Three-to-five distinct concepts

Given one product brief, the campaign planner MUST return the explicitly requested count of three to five concepts. Each concept MUST have a stable unique ID, distinct hypothesis statement, audience problem, target emotion, emotional arc, hook narration, product bridge, and observable quality criteria. Duplicate hypotheses or opening actions MUST be rejected before paid generation.

#### STORY-2.2 — Complete timed hook intent

Every concept MUST contain ordered, non-overlapping hook beats that begin at `0.0`, end at `5.0`, and describe concrete visible actions plus expected evidence. Gaps, overlaps, empty actions, or beats outside the hook range MUST fail validation.

#### STORY-2.3 — Controlled storyboard compilation

The compiler MUST create one validated storyboard per concept by replacing only the approved template hook. Product demonstration, product assets, CTA, brand assets, output geometry, and total duration MUST remain identical across the candidate batch.

#### STORY-2.4 — English and factual planning boundary

The campaign plan MUST use `en-US`, reference only supplied product facts and approved asset IDs, preserve canonical lowercase `tict`, and remain provider-neutral. Invalid or explanatory planner output MUST stop before paid work.

### ADPIPE-2 — Campaign candidate orchestration

#### ADPIPE-2.1 — Plan-first durable campaign

The orchestrator MUST persist a sanitized campaign plan containing brief hash, concept plan, compiled storyboard hashes, candidate operation IDs, estimated costs, and initial `planned` states before provider submission.

#### ADPIPE-2.2 — Whole-batch budget preflight

Before submitting the first generated-media job, the orchestrator MUST verify that the sum of all planned Runway requests fits the shared iteration budget. An unaffordable batch MUST submit zero jobs.

#### ADPIPE-2.3 — Independent idempotent jobs

Every candidate MUST use a unique deterministic operation ID and retain its provider job ID, request hash, local output hash, charge, latency, state, and failure evidence. Completed or ambiguous operations MUST NOT be resubmitted as new generations. A rerun MUST reuse a hash-matching terminal candidate without provider work and MUST resume polling a known submitted provider job without resubmitting it. A stale state, mismatched artifact hash, or ambiguous reservation without a provider job ID MUST fail closed.

#### ADPIPE-2.4 — Screen before selection and rendering

Every downloaded hook MUST be screened under RUNWAY-2.2. Only an eligible candidate MAY be selected and rendered into a full advertisement. If none are eligible, the campaign MUST retain all evidence and produce no final video.

#### ADPIPE-2.5 — Exact shared downstream composition

The selected hook MUST be composed with the batch's unchanged exact product capture, narration contract, subtitle policy, brand assets, and CTA. The final manifest MUST identify the selected concept and exact rendered-video SHA-256.

#### ADPIPE-2.6 — Exact offline preflight gates paid execution

The campaign MUST produce an offline `planner_ready` preflight before a paid
planning request and an exact `generation_ready` preflight before the first
Runway request. Preflight MUST validate the real provider transport schema,
every compiled Runway request and payload, the 1000-character prompt limit,
managed assets, deterministic operation identities, fail-closed pricing, and
the complete worst-case campaign cost including planning, generation, and
temporal screening. It MUST perform no network call and create no budget
operation. Paid execution MUST recompute and match the preflight's semantic hash
from current code and inputs; a missing or stale report MUST submit no provider
request.

#### ADPIPE-2.7 — Auditable conservative budget reconciliation

A charged provider submission whose exact usage evidence cannot be recovered
MAY be reconciled to an explicit conservative manual charge without changing
its amount or provider job identifier. Reconciliation MUST retain an English
reason, MUST be idempotent only for the same reason, and MUST NOT release the
charge. Budget audit is read-only by default and MUST NOT infer a transition
from operation age or provider description alone.

## Current behavior

The legacy WebUI/API path still generates prose and stock keywords, retrieves or accepts clips, and concatenates them. The opt-in experimental path now validates a product brief, plans three to five provider-neutral hypotheses, compiles independent Runway hooks against one controlled storyboard template, gates paid calls with exact offline preflight hashes, retains each provider job and MP4, applies artifact-bound temporal review, renders every eligible hook with exact product/brand layers, judges hook semantics against each candidate's own compiled contract, and compares only shared downstream invariants across concepts. Calibrated automatic campaign ranking, mascot animation, and main-WebUI exposure remain absent.

## Expected first-slice behavior

1. Accept a product brief, one hypothesis, approved asset identifiers, explicit `en-US`, and the accepted iteration budget scope.
2. Ask the configured planner LLM for one provider-neutral storyboard JSON document.
3. Parse and validate the storyboard before any paid request.
4. Compile two execution plans from the same storyboard:
   - stock baseline;
   - Runway candidate with only the hook base source changed.
5. Resolve exact product and brand overlays from managed assets.
6. Estimate and reserve the Runway request cost against the shared iteration ledger.
7. Submit, poll, and download the generated hook through the Runway adapter.
8. Reuse the existing voice, subtitle, music, MoviePy, FFmpeg, artifact, and evaluator services where their contracts remain valid.
9. Preserve plans, provider evidence, outputs, metrics, and remaining budget for the experiment record.

## Storyboard validation

Validation is deterministic and local. It covers:

- supported schema version;
- explicit `en-US` content language for this slice;
- `9:16` output aspect;
- positive target duration;
- non-empty, unique scene identifiers;
- ordered, non-overlapping scene ranges within target duration;
- non-empty purpose, action, camera intent, and expected evidence;
- known media source kinds;
- required base layer;
- narration and voice-language compatibility;
- explicit screen policy and its required product-capture relationship;
- managed required-asset resolution;
- paid source authorization and available budget.

Creative quality is not a schema validation concern.

## Planner response handling

The planner prompt requires raw JSON matching the storyboard contract. The implementation MUST tolerate a single surrounding Markdown code fence, then parse strictly. It MUST NOT recover arbitrary JSON fragments from explanatory prose for paid execution. A non-conforming response produces an actionable planning error and no paid work.

Planner output is untrusted input. Length limits apply to the brief, hypothesis, narration, prompts, actions, and evidence labels before persistence or provider compilation.

## Budget semantics

- Scope identifier: one explicit feedback-loop iteration.
- Accepted cap for the current scope: `10_000_000` micro-USD (`$10.00`).
- Monetary values are stored as integer micro-USD to avoid floating-point overspend.
- The initial Runway pricing snapshot records `gen4.5 = 12 credits/output-second` and `1 credit = $0.01` as of 2026-08-17.
- A five-second `gen4.5` hook therefore reserves `$0.60`.
- Reservations and charges are atomic across local API/WebUI processes.
- Unknown model pricing fails closed unless the execution plan supplies an explicitly reviewed estimate and pricing provenance.
- There is no automatic top-up.
- Paid integrations that cannot report usage automatically require a manual ledger charge or reservation before subsequent paid work.

## Failure behavior

- Planner failure: store a sanitized error; do not compile or submit.
- Storyboard failure: report all deterministic validation errors; do not submit.
- Budget failure: report required, remaining, and cap amounts; do not submit.
- Provider rejection before a job is accepted: release the reservation.
- Accepted job later fails: retain the charge and terminal evidence.
- Poll timeout with a known job: retain the charge and job identifier; do not resubmit.
- Output download failure: retain the charge and provider job evidence; allow explicit resume of ingestion.
- Missing exact UI/brand asset: fail the affected plan rather than silently generate a substitute.

## Security and data

- Provider keys remain in untracked configuration or environment variables.
- Persisted provider requests are sanitized.
- External output URLs are used only for immediate ingestion and are not exposed as durable results.
- All output and asset paths pass existing managed-root validation.
- Budget state and provider job state are durable local records, not process-memory-only counters.

## Out of scope

- More than five advertising hypotheses in one campaign.
- Mascot animation acceptance.
- Direct generative product-UI mode.
- Automatic vision judgement as the sole trusted acceptance gate.
- Distributed workers and remote orchestration.
- Publication and campaign-metric optimization.
- A general visual timeline editor in the WebUI.

## Executable coverage plan

- `test/bdd/features/mixed_media_pipeline.feature`
  - STORY-1.2: invalid storyboard blocks paid submission.
  - STORY-1.3: the first slice resolves English narration explicitly.
  - ADPIPE-1.2: stock and Runway plans differ only in the hook base source.
  - RUNWAY-1.2: exhausted iteration budget blocks submission.
  - STORY-1.5 / BRAND-1.5: a generic screen is allowed only when the scene declares non-product context.
  - BRAND-1.4: canonical subtitle spelling remains separate from provider-specific pronunciation text.
- `test/services/test_storyboard.py`
  - STORY-1.1, STORY-1.2, STORY-1.3, ADPIPE-1.3, BRAND-1.3.
- `test/services/test_creative_renderer.py`
  - BRAND-1.1: exact product and brand source assets remain unchanged.
  - BRAND-1.6: measured end-card elements scale, honor storyboard-supplied relative regions and alignments, remain non-overlapping inside the safe area, and use storyboard-owned action copy at multiple portrait resolutions.
- `test/services/test_generation_budget.py`
  - RUNWAY-1.2, RUNWAY-1.3, RUNWAY-1.4.
- `test/services/test_runway.py`
  - RUNWAY-1.1, RUNWAY-1.4, RUNWAY-1.5, RUNWAY-1.6 without live paid requests.
- `test/bdd/features/mixed_media_pipeline.feature`
  - RUNWAY-2.2: temporally passing hooks and confirmed temporal false positives are eligible; confirmed defects remain blocked.
- `feedback-loop/video-quality/evals/tests/test_temporal_judge.py`
  - EVAL-5 provider-schema compatibility without live paid requests.
- `test/bdd/features/mixed_media_pipeline.feature`
  - STORY-2.1 / STORY-2.2: a requested concept batch is distinct and time-complete.
  - ADPIPE-2.2: an unaffordable complete batch submits no provider jobs.
  - ADPIPE-2.4: only screened eligible candidates may be selected.
- `test/services/test_creative_campaign.py`
  - strict concept parsing, duplicate rejection, controlled storyboard compilation, durable manifests, deterministic operation IDs, and scorecard null semantics.
- `test/bdd/features/mixed_media_pipeline.feature`
  - ADPIPE-2.6: a stale semantic preflight blocks paid execution without a provider request.
- `test/services/test_campaign_preflight.py`
  - ADPIPE-2.6: exact Gemini schema transport, prompt compilation, asset hashing, full-cost accounting, zero network/ledger mutation, and stale-hash rejection.
- `test/services/test_generation_budget.py`
  - ADPIPE-2.7: submitted-to-manual-charge reconciliation preserves conservative spend and audit provenance.

## Unresolved non-blocking choices

- The first approved product screen and CTA asset identifiers.
- The exact English voice within the `en-US` constraint.
- The first trusted vision and human-review protocol.
- Visual-quality thresholds beyond existing hard technical constraints.
- Whether the first CTA omits the mascot or uses a static approved asset.
