# Agent-authored motion workflow

This opt-in path creates React/Remotion source at runtime from a request. It does
not select a fixed hook/product/CTA template. The default author is GPT-6 Astra;
Gemini 3.6 Flash remains explicitly selectable. The author and material-tool
interfaces are injected Python callables. Video judging still uses Gemini.
Remotion renders the composition; it is not the creative or video-generation model.

## Setup and first run

Python uses the repository `.venv`. JavaScript dependencies and the dedicated
headless browser are installed only in `motion/node_modules`:

```bash
make motion-install
make motion-test
make motion-create \
  BRIEF=motion/briefs/tict-first.json \
  CATALOG=feedback-loop/video-quality/evals/assets/brand/motion-catalog.json \
  OUTPUT=feedback-loop/video-quality/experiments/NNN-example/artifacts/agent-01 \
  CONFIRM_PAID=YES MOTION_ARGS="--review"
```

Use a new output directory for a new run. The CLI reads existing untracked
`config.toml` keys. All author, generated-media and judge charges share the existing
`mixed-media-iteration-001` ledger and $10 total cap. No global installs, automatic
top-ups or retries of ambiguous paid submissions. Remotion licensing must be
reviewed before commercial deployment: https://www.remotion.dev/license.

Author settings are independent of the legacy `llm_provider` and judge. Astra
uses `OPENAI_API_KEY` or `[app].openai_api_key` and the official Responses API,
not `openai_base_url` or `openai_model_name`. Set `[app].motion_author_provider`
to `astra` (default) or `gemini`, or pass `--author-provider` in `MOTION_ARGS`.
Astra defaults to `high` reasoning; override with `--author-reasoning` or
`[app].motion_author_reasoning`. Model IDs are deliberately limited to verified
adapters/pricing; unknown models fail rather than silently use another provider.

Each run retains `author.json` and per-call public output/usage. Astra first counts
text/image tokens, then checks input plus maximum output cost without reserving
funds. Its conservative charge uses the input cache-write ceiling and all output
tokens (reasoning included once). No hidden reasoning is saved. Incomplete/refused
responses fail visibly; account access or budget failures do not fall back to Gemini.
Changing author settings on resume is rejected; use a new run for comparisons.

For a controlled comparison add `--materials-from path/to/completed-run`.
This verifies the rendered snapshot and reuses its exact resolved material pool,
including provenance, while disabling new acquisition. It requires the identical
brief and asks for a fresh plan/source without supplying the old source as a
template. It does not require the new author to choose the same scene structure.
`--materials-from` cannot be combined with `--revision-of`.

Official adapter references: https://developers.openai.com/api/docs/models/gpt-6-astra,
https://developers.openai.com/api/docs/guides/token-counting.

An interrupted run with a completed author response and hash-verified material
files can continue with `--resume`; this appends a new source attempt without
regenerating its plan or media. An ambiguous author submission requires
reconciliation and cannot silently resume. All failed attempts remain on disk.
Use `allowed_assets` and `allowed_tools` in a brief to narrow what that request's
agent is permitted to use. `motion/briefs/tict-ten-second.json` is a deliberately
different request (graphical/music-only teaser), not another renderer preset.

## Data flow

1. Validate the brief and managed local catalog. Keep requested duration, geometry,
   language, required assets and audio policy immutable.
2. The author chooses a construction, timed beats and material requests. Validate
   the plan before material tools run; malformed plans get one bounded repair.
3. Resolve catalog assets and requested tools: Pexels photos, the existing Runway
   video adapter, Edge voice with word boundaries, or a small original procedural
   music sketch. Tools are explicitly advertised according to configured keys.
4. The author writes a complete `Scene.tsx`, using exact staged references and
   timing/provenance evidence. The infrastructure supplies `project` and `assets`.
5. Bundle and render inside the local OS sandbox. Compilation failures return to
   the author, up to the declared attempt limit. No fixed number of visual scenes.
6. Probe and fully decode the MP4. Optional `--review` invokes the existing agentic
   video evaluator against this project's own contract, never the old airport story.

The agent may choose different constructions and media, but tool support is not
unlimited. The current Runway adapter is text-to-video only; there is no new
image-to-video integration here. Procedural music is a simple sketch score, not
a Suno-quality model. Tool failures are explicit, not replaced silently. Complex
new tools, multitenant execution, editing UI and campaign-wide selection remain
future work. This CLI does not yet replace the Streamlit generation button.

## Inspect and revise

`result.json` points to the final `video.mp4` and editable project. Each attempt
contains its prompt, public response, TSX, exact assets/contract, render logs,
provenance and technical metrics or failure. The review directory retains model
observations separately. A successful render does not measure brand fidelity,
storytelling effectiveness or advertising conversion; creative quality remains null.

To re-render an edited project without model calls:

```bash
make motion-render PROJECT=path/to/attempt-01/project.json OUTPUT=path/to/new-render
make motion-verify OUTPUT=path/to/new-render
```

To ask the author for an evidence-backed revision, create feedback JSON containing
`confirmed: true`, the exact previous `video_sha256`, `evidence` and `hypothesis`.
Only confirm after inspecting the claimed event; a judge's criticism alone is not
confirmation. Then run `motion-create` with the same brief and
`MOTION_ARGS="--revision-of path/to/previous-run --feedback path/to/feedback.json --review"`.
The previous plan/materials are reused; original artifacts are not overwritten.
This first revision path changes composition code, not material requests. An agent
that needs different generated material must open an explicitly recorded new run.

After an executor-only fix, an incomplete run with a completed TSX/project can use
`--rerender` to render that source again without another author/material call.
The new render has its own directory and does not overwrite the failed one.

Local isolation integration test (requires permission to create a sandboxed process):

```bash
MOTION_ISOLATION_TEST=1 .venv/bin/python -m pytest -q test/services/test_motion_composition.py
```

Current worker platform: macOS Apple Silicon with Homebrew Node/FFmpeg. Other
platforms fail closed pending a dedicated isolated runner. The headless browser
is separate from the user's normal browser profile. It receives only staged files,
read-only dependencies/system runtime, scrubbed environment and two loopback ports.
This is not a security-audited service for adversarial arbitrary code.

API references: https://www.remotion.dev/docs/bundle and
https://www.remotion.dev/docs/renderer/render-media.
