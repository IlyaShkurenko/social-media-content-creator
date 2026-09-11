# RFC-0011: Agent-authored motion compositions

- Status: Accepted for an opt-in local pilot
- Date: 2026-09-11

## Decision

Keep the existing generation pipeline. Add a Remotion render worker for
agent-authored React compositions, with Python owning validation, approved asset
staging, execution and artifact provenance. The agent chooses the construction
for the brief; the executor does not impose hook/product/CTA slots or a brand.
An explicit contract separates user constraints from creative choices, including
duration, frame rate, geometry, timed beats and intentional audio policy.

This pilot tests montage expressiveness, not a new text-to-video model. Use exact
approved product/brand assets and whichever advertised material tools the agent
chooses subject to the request and budget. Preserve an editable source snapshot, inputs,
asset hashes, rendered MP4 and verification in experiment 020. The historical
airport pairwise score is not a valid comparator for this different construction.
Technical validity is not advertising effectiveness or subjective improvement.

Generated compositions run in a local macOS sandbox with scrubbed environment,
read-only runtime/dependencies, a staged project and loopback rendering only.
No secrets or provider clients belong inside the composition. External acquisition
and optional diagnostic judging remain trusted Python work outside the worker.
Unsupported isolation must fail closed; do not execute arbitrary downloaded code.

## Scope and limits

Implement a CLI-driven model agent: brief and approved catalog -> creative plan
and material requests -> resolved assets -> authored React source -> isolated
render -> technical evidence and request-specific review -> bounded revision.
The agent generates source at runtime, not a selection of fixed templates. User
constraints are immutable across retries. Persist every prompt, public response,
source revision and result. A revision must explain the evidence it addresses;
unconfirmed model criticism is diagnostic, not an automatic production rejection.
No editor UI or campaign-wide replacement is included. Remotion
does not make creative decisions or guarantee quality. Keep new dependencies
project-local and pinned. Recheck Remotion licensing before commercial deployment;
this evaluation is not a blanket assertion of free commercial eligibility.

## Validation

The earlier narrow hand-authored-pilot plan was superseded before production
implementation after explicit user correction. The deliverable is the agent
architecture plus retained execution evidence, not only an example composition.

Contract tests cover different durations, scene counts, timing, asset/path safety
and explicit audio. A real sandboxed render, media probes, sampled frames and a
request-specific diagnostic judge provide pilot evidence; user review decides taste.

References: https://www.remotion.dev/docs/renderer/render-media,
https://www.remotion.dev/license

## Addendum: independent author and evaluator (2026-09-11)

User-approved: use GPT-6 Astra as the default motion director/source author;
retain explicit Gemini authorship for comparison and the existing Gemini video
judge. This extends the injected author boundary, not the renderer architecture.
Use OpenAI Responses with text/image references and no unsupported sampling
parameters. Credentials stay in untracked configuration or OPENAI_API_KEY.
Do not reuse a custom Chat Completions endpoint for this separate integration.

Persist provider/model/reasoning identity and reject silent provider changes on
resume. Check counted input plus bounded output cost against the shared ledger
without reservations. No retry/fallback after an ambiguous paid submission.
Public responses and usage are retained; hidden reasoning and credentials are not.
Compare against experiment 020 using its exact brief and resolved materials, but
allow a new plan/source. Rendering success is not proof Astra is creatively better.

References: https://developers.openai.com/api/docs/models/gpt-6-astra,
https://developers.openai.com/api/docs/guides/token-counting
