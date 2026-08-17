# RFC-0001: Experimental mixed-media advertising pipeline

- Status: Accepted
- Date: 2026-08-17
- Decision owners: Project maintainers
- Decision type: Architecture and external integration

## Summary

Introduce a provider-agnostic advertising-production pipeline that converts a product brief and approved brand assets into a timed storyboard, resolves every scene through stock, generated, product, or brand media, renders a short-form video, and evaluates the result against the storyboard.

Runway will be the first generated-video adapter. Pexels, Pixabay, and Coverr remain available as stock-media adapters. Exact product screens, logos, text, and other identity-sensitive assets will be composited locally by default instead of being redrawn by a generative video model.

The current implementation iteration has one shared external-spend ceiling of `$10.00` across the complete feedback loop. The first Runway benchmark uses `gen4.5` text-to-video for the generated hook. Model and mode remain configurable after this accepted baseline rather than becoming permanent architecture.

The first vertical slice will produce a comparable stock baseline and Runway candidate for one English-language, approximately 15-second, `9:16` advertisement. It will validate one user-supplied advertising hypothesis before the system expands to autonomous hypothesis generation and batches of up to five creatives.

## Context

The current pipeline accepts a subject or script, asks an LLM for prose and stock-search terms, retrieves or accepts media, synthesizes narration, creates subtitles, and concatenates clips. It has no structured representation of scenes, actions, camera direction, product interactions, or acceptance criteria.

As a result, the current LLM acts primarily as a script and keyword generator. It does not operate as a creative director, and the renderer has no machine-readable description of what should happen at each point in the final video. The evaluator therefore cannot reliably compare intended actions with rendered actions.

Adding Runway directly to the existing keyword pipeline would generate clips without solving this planning gap. It would also couple the product to one paid provider and make controlled stock-versus-generated comparisons difficult.

## Goals

- Represent an advertisement as a versioned, timed storyboard with explicit scene actions.
- Separate creative planning from provider-specific prompt compilation.
- Select stock, generated, product, and brand media independently for each scene and layer.
- Use Runway as the first generated-video provider without making it the system architecture.
- Preserve exact application UI, logos, typography, and approved assets when fidelity is required.
- Make English narration and voice selection explicit for the first vertical slice.
- Record prompts, inputs, seeds when supported, provider jobs, costs, latency, outputs, and evaluator results.
- Compare one coherent variable per experiment and retain immutable experiment evidence.
- Keep paid generation disabled unless a run is explicitly authorized and budgeted.
- Preserve the existing subject/script pipeline while the new path is experimental.

## Non-goals

- Generating five advertising hypotheses in the first vertical slice.
- Selecting one permanent generated-video model before comparative experiments exist.
- Asking a video model to reproduce exact product interfaces or readable branded text by default.
- Replacing MoviePy or FFmpeg as the final compositor.
- Building a distributed durable worker platform in this RFC.
- Automating publication or optimizing campaign metrics in the first vertical slice.
- Treating an uncalibrated vision-model opinion as ground truth.
- Defining the final set of visual effects before individual effects are evaluated.

## Architectural decisions

### 1. The pipeline is provider-agnostic

The core pipeline will work with capability-based media adapters. Runway is the first generated-video adapter, while existing stock providers continue behind their own adapters. Storyboards and evaluation contracts must not contain Runway-specific request fields.

Provider-specific models, API versions, ratios, durations, and prompt rules belong in adapter capabilities and compiled execution plans. They do not belong in the provider-neutral storyboard.

### 2. A Creative Planner owns scene intent

A new Creative Planner will use a configurable LLM to convert a product brief and one advertising hypothesis into a structured storyboard. Kimi may be the first configured planner because it is already integrated, but the planner contract will not be tied to Kimi.

The planner describes:

- the purpose of each scene;
- its start and end time;
- setting, subjects, actions, and product behavior;
- camera framing and motion;
- narration and on-screen text;
- transition intent;
- required brand or product assets;
- visual evidence that the evaluator should expect.

The planner does not call Runway or stock providers directly.

### 3. A validated storyboard is the canonical intermediate representation

The storyboard becomes the contract between planning, media acquisition, rendering, and evaluation. It will have a schema version and deterministic validation before any paid request is submitted.

At minimum, validation will reject:

- overlapping or negative scene ranges;
- a scene ending beyond the declared target duration;
- missing action or source strategy;
- narration that cannot plausibly fit its allocated duration;
- references to unavailable required assets;
- an unsupported output aspect ratio;
- a missing explicit content language;
- a paid source plan without authorization and budget.

### 4. Scenes use layered media plans

A single `source_strategy` is insufficient for mixed-media scenes. Each scene will declare a base source and zero or more ordered overlays.

Supported provider-neutral source kinds are initially:

- `stock` — retrieved footage from Pexels, Pixabay, or Coverr;
- `generated` — text-to-video or image-to-video output from an adapter such as Runway;
- `product_capture` — an exact approved application screenshot or screen recording;
- `brand_asset` — an approved logo, mascot, illustration, or other brand file;
- `solid_or_graphic` — deterministic locally rendered background or graphic layer.

An illustrative scene fragment is:

```json
{
  "scene_id": "demonstration",
  "start_seconds": 5.0,
  "end_seconds": 11.0,
  "purpose": "product_demo",
  "visual_intent": {
    "setting": "traveller holding a phone in an airport",
    "subject_action": "raises the phone and checks the completed trip plan",
    "camera": "controlled push-in toward the phone"
  },
  "media_plan": {
    "base": {
      "kind": "generated",
      "intent": "moving person, phone, environment, and camera"
    },
    "overlays": [
      {
        "kind": "product_capture",
        "asset_id": "trip-plan-screen",
        "placement": "tracked_phone_screen"
      }
    ]
  },
  "voiceover": "Your complete trip, ready in one place.",
  "expected_evidence": [
    "traveller_visible",
    "phone_visible",
    "approved_trip_plan_screen_visible"
  ]
}
```

This example is explanatory. The feature specification will own the normative schema.

### 5. Exact product UI is composited locally by default

The baseline product-demonstration technique will generate or retrieve the moving environment, then place the exact application capture into a tracked or perspective-corrected screen region. Local composition may add deterministic scrolling, tap indicators, masks, highlights, zoom, and transitions while preserving source pixels.

Direct image-to-video use of an application screenshot is an experimental strategy, not the default. It will be evaluated separately because generative models may deform text, icons, spacing, and product behavior.

### 6. Storyboards are compiled into provider execution plans

A Storyboard Compiler will translate provider-neutral intent into an execution plan:

- stock search queries and selection constraints;
- generated-video prompts, references, duration, ratio, and provider settings;
- local asset lookup and layer composition instructions;
- voiceover requests and explicit voice-language constraints;
- subtitle and transition instructions;
- per-scene validation and evaluation targets.

The compiled plan is saved as an artifact. This makes provider behavior reproducible without contaminating the storyboard with provider-specific syntax.

### 7. Paid generated-video calls are asynchronous and persisted

The generated-video adapter contract will support task submission, status retrieval, cancellation when available, output download, and terminal failure reporting. Provider job identifiers and sanitized request metadata will be persisted before polling continues.

The first implementation may poll within the current task process, but it must use bounded timeouts, backoff, and resumable task metadata. A timeout must not silently submit another paid generation. Provider outputs must be downloaded into managed task storage rather than exposing temporary provider URLs as final artifacts.

### 8. Language is an explicit production constraint

The first vertical slice uses English script, English narration, and English subtitles. The brief and storyboard must declare `content_language`; the voice must be compatible with that language.

The pipeline must not infer narration language from the WebUI locale, an earlier task, a default Chinese voice, or the natural language used to discuss the task. Automatic detection may be offered later, but the resolved language and voice are recorded before synthesis.

### 9. Feedback is hierarchical and experiments remain controlled

The system will not try to optimize planning, provider choice, prompt, montage, voice, music, and visual effects simultaneously.

The feedback system has three layers:

1. A cheap preflight loop validates the brief, storyboard, assets, duration, language, and budget before generation.
2. A creative-production loop changes one coherent variable, generates comparable artifacts, and evaluates technical, timeline, visual, brand, cost, and latency evidence.
3. A later campaign loop may incorporate retention, click-through, conversion, and cost-per-result metrics after publication data is available.

Automatic acceptance remains blocked while required evaluator constraints are unavailable or unverified.

## First vertical slice

### Input

- One product brief.
- One user-supplied advertising hypothesis or product promise.
- An approved brand pack containing the required logo, product screen, and optional mascot.
- Explicit English content language and an English voice selection.
- Explicit authorization and the accepted `$10.00` shared iteration budget for all paid feedback-loop requests.

### Output

- One validated, versioned storyboard.
- One provider execution plan per variant.
- A stock baseline and Runway candidate using the same narrative and timing contract.
- Final portrait MP4 files with narration, subtitles, music policy, and provenance manifests.
- Evaluation reports containing available scores, unavailable metric reasons, cost, latency, and acceptance status.

### Narrative shape

The approximately 15-second target contains three functional scenes:

1. `hook` — a travel or lifestyle problem, tested as stock footage and as a Runway-generated alternative;
2. `product_demo` — a moving person/device/environment with an exact application capture composited into the screen;
3. `cta` — deterministic brand treatment with logo, message, and optional approved mascot asset.

Exact durations remain adjustable in the storyboard. The 15-second total is the first experiment target, not a permanent product limit.

### Success conditions

- Both variants follow the same validated storyboard and narration.
- The final files decode, contain the expected audio, use `9:16`, and satisfy the duration tolerance.
- Script, voice, and subtitles are English.
- Exact UI and required brand assets remain recognizable and unmodified where fidelity is required.
- Expected scene evidence is recorded and evaluated without fabricated metrics.
- Provider, model, sanitized prompt, input references, job ID, latency, and estimated cost are recorded.
- The Runway candidate is compared with the stock baseline; it is not accepted merely because generation succeeded.

## Component boundaries

```text
Product brief + brand pack
          │
          ▼
Creative Planner
          │ provider-neutral storyboard
          ▼
Storyboard Validator
          │ validated storyboard
          ▼
Storyboard Compiler
          │ provider execution plans
          ▼
Media Router
  ┌───────┼───────────┬──────────────┐
  ▼       ▼           ▼              ▼
Stock   Generated   Product       Brand/local
adapters adapter    captures      graphic assets
          │
          ▼
Renderer / Compositor
          │ final MP4 + provenance
          ▼
Evaluator + Experiment Runner
```

The current task service remains the task lifecycle owner. The experimental path may initially be selected by an explicit pipeline mode and delegate to these components. The existing subject/script path must remain operational during migration.

## Artifact contract

Each experimental task will retain sanitized, versioned artifacts under its managed task directory:

- `brief.json`;
- `storyboard.json`;
- `execution-plan-<variant>.json`;
- `provider-jobs.json`;
- downloaded or local scene media;
- `provenance-<variant>.json`;
- narration and subtitles;
- final MP4 files;
- evaluator metrics and reports.

Artifacts must never include API keys, authorization headers, signed temporary output URLs after download, or unrestricted external filesystem paths.

## Generated-video adapter responsibilities

The provider-neutral adapter must expose equivalent operations for:

- capability discovery or declared capabilities;
- cost estimation before submission when possible;
- request validation and compilation;
- task submission;
- task status retrieval;
- bounded waiting with backoff and jitter;
- cancellation when supported;
- terminal failure normalization;
- output download into managed storage;
- provenance and usage reporting.

The Runway implementation will initially support only the generation modes required by the first experiments. Model selection remains configuration, not an RFC constant.

## Failure and retry behavior

- Invalid storyboards fail before paid work begins.
- Missing brand or product assets produce an actionable validation error.
- Provider moderation, rate-limit, timeout, and generation failures retain provider job evidence without leaking sensitive request data.
- Polling may retry status requests, but generation submission is not retried automatically unless idempotency is proven.
- Expired provider output URLs are treated as an ingestion failure; successfully retrieved outputs are copied into managed storage immediately.
- A failed generated scene may fall back to stock only when the execution plan explicitly permits that fallback. The fallback must be visible in provenance and evaluation.
- Partial scene completion remains inspectable and must not be reported as a successful final video.

## Cost and security controls

- Generated-video providers are disabled by default.
- Every live experiment records provider authorization, a maximum estimated budget, and the person or process authorizing it.
- The current research iteration shares one `$10.00` ceiling across planning, media generation, speech, and other externally billed calls that can be measured. Runway reservations and charges are enforced automatically; any paid integration without automatic usage reporting requires a manual ledger entry before further generation.
- The initial `gen4.5` text-to-video benchmark estimates 12 Runway credits per output second at the pricing snapshot accepted on 2026-08-17. At `$0.01` per credit, one five-second hook reserves `$0.60`.
- Budget is reserved before a provider submit. A request that would exceed the remaining iteration balance is rejected before network submission.
- Accepted provider jobs remain charged against the iteration budget even if their creative result is rejected. Failed requests may release a reservation only when the provider did not accept a billable job.
- There is no automatic top-up and no automatic paid resubmission after an ambiguous timeout.
- The compiler estimates cost before submission when provider pricing and planned duration make that possible.
- A run exceeding its budget estimate is blocked unless reauthorized.
- Provider credentials remain in untracked configuration or environment-backed secret storage.
- All uploaded and downloaded media stays inside existing managed asset and task roots.
- Prompts and provider errors are sanitized before being persisted or logged.

## Evaluation strategy

The existing video-quality feedback loop remains the decision owner for experimental comparisons. This RFC extends its inputs from a fixed storyboard fixture toward task-generated, versioned storyboards.

Evaluation will develop incrementally:

1. existing decode, duration, aspect, audio, black-frame, and subtitle checks;
2. explicit English language and compatible-voice checks;
3. scene-boundary and narration-timing checks;
4. exact product/brand asset fidelity checks;
5. versioned vision evidence for actions, objects, and on-screen text;
6. pairwise visual judgement calibrated against human review;
7. cost and latency comparison;
8. campaign metrics when real publication data is integrated.

Unavailable measurements remain `null` with reasons. They cannot be replaced with invented scores.

## Planned experiment sequence

Experiments are ordered to isolate uncertainty:

1. Validate a manually reviewed storyboard fixture without any paid generation.
2. Render a stock-only baseline with exact UI composition.
3. Replace only the hook with a Runway-generated scene and compare it with the stock hook.
4. Improve hybrid phone-screen tracking and deterministic product interaction.
5. Test direct screenshot-as-reference generation against the exact-composite baseline.
6. Test mascot image-to-video identity preservation separately.
7. Improve voiceover, word timing, scene boundaries, and subtitle placement.
8. Freeze successful scene recipes, then generate several complete creative hypotheses.
9. Introduce batches of up to five distinct advertisements.
10. Add real campaign-performance feedback when publishing and analytics data are trustworthy.

An experiment may combine implementation steps needed for one coherent hypothesis, but it must not change several unrelated creative dimensions merely to chase an aggregate score.

## Compatibility and migration

- Existing WebUI, API, and CLI behavior remains available.
- The new pipeline is opt-in while its contracts and evaluators mature.
- Existing local images and videos remain valid media inputs.
- Existing stock providers are adapted rather than removed.
- Existing voice, subtitle, music, renderer, storage, and publishing services should be reused behind the new storyboard execution path where their behavior satisfies the new contracts.
- Legacy script and search-term artifacts are not automatically considered valid storyboards.

## Alternatives considered

### Generate the entire advertisement with Runway

Rejected as the baseline because exact UI, text, brand fidelity, editability, attribution, and cost would be difficult to control.

### Use stock footage only

Rejected as the target architecture because product-specific actions, mascot behavior, unique hero shots, and controlled transitions may not exist in stock libraries.

### Let the video model decide scene actions

Rejected because provider prompts would become the only source of intent, making evaluation, provider comparison, and deterministic composition weak.

### Keep unstructured script and keyword output

Rejected because keywords do not express timing, actions, layers, transitions, product behavior, or expected evidence.

### Optimize every creative dimension in one autonomous loop

Rejected because score changes would be difficult to attribute and paid experiments would become noisy and expensive.

### Ask the video model to redraw application screens

Rejected as the default because generated text and UI structure may drift. It remains an explicit experiment against the hybrid composite baseline.

## Risks and mitigations

- **Planner produces unrealistic actions:** validate capability and duration constraints before compilation; retain human review for early fixtures.
- **Generated scene ignores the prompt:** compare expected evidence with vision/manual observations and retain stock fallbacks only when declared.
- **UI overlay looks artificial:** test tracking, perspective, lighting, masks, and transition recipes as isolated compositor experiments.
- **Brand or mascot identity drifts:** require approved references and a fidelity gate before acceptance.
- **Provider costs grow unexpectedly:** estimate costs, enforce budgets, record usage, and avoid automatic paid retries.
- **Provider API or models change:** isolate versioning and capabilities inside the adapter and record them in provenance.
- **Language and voice mismatch:** resolve and validate explicit content language and voice before synthesis.
- **Evaluation rewards the wrong behavior:** version evaluators, establish a new baseline after evaluator changes, and calibrate model judgements against humans.

## Rollout

1. Use the accepted first-slice defaults and preserve remaining product choices as experiment parameters.
2. Create stable `STORY-*`, `ADPIPE-*`, `RUNWAY-*`, `BRAND-*`, and additional `EVAL-*` contract requirements.
3. Write the implementation-ready feature specification and selected BDD scenarios.
4. Establish RED executable specifications without calling paid providers.
5. Implement planner, schema validation, compiler, and dry-run execution plans.
6. Implement stock baseline and exact UI composition.
7. Add the Runway adapter behind explicit authorization and budget controls.
8. Run the controlled experiment sequence and update accepted decisions only from recorded evidence.

## Open questions

The following decisions remain intentionally unsettled after accepting the first implementation slice:

1. Which exact TICT product screen, logo, and mascot assets form the first approved brand pack?
2. Which English voice and narration style form the first fixed audio baseline?
3. Which vision model and human-review protocol will produce the first trustworthy action-alignment observations?
4. What duration tolerance and visual-quality thresholds are required before the first candidate may be accepted?
5. Should the first CTA use a static approved mascot or omit the mascot until its dedicated animation experiment?

## Consequences

This design adds planning, validation, compilation, provenance, and adapter layers before generated video reaches the existing renderer. That is more work than adding one Runway API call, but it creates a measurable system in which stock and generated media can coexist and providers can be replaced.

The first result is deliberately narrower than the eventual autonomous five-video production system. It establishes a trustworthy unit of experimentation: one storyboard, comparable variants, explicit costs, and evidence explaining why a candidate was kept or rejected.

## References

- [Runway API reference](https://docs.dev.runwayml.com/api/)
- [Runway API task workflow](https://docs.dev.runwayml.com/api-details/sdks/)
- [Runway output handling](https://docs.dev.runwayml.com/assets/outputs/)
- [Runway API pricing](https://docs.dev.runwayml.com/guides/pricing/)
- `docs/spec-process.md`
- `docs/video-pipeline.md`
- `docs/integrations.md`
- `feedback-loop/video-quality/goal.md`
