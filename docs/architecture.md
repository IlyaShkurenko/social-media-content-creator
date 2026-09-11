# Architecture

MoneyPrinterTurbo is a single Python application with several entrypoints over the same service layer. It orchestrates external text, media, and speech providers and renders the final video locally with MoviePy and FFmpeg. It does not contain a general-purpose video-generation model.

## Entrypoints

- `webui/Main.py` is the Streamlit interface. It persists UI configuration and submits generation work through `app/services/webui_task.py`.
- `main.py` starts the FastAPI application defined in `app/asgi.py`. API routes under `app/controllers/v1/` create queued tasks and expose task status and artifacts.
- `cli.py` validates local file arguments, constructs `VideoParams`, and invokes the same task pipeline synchronously.
- `docs/skill/mpt_agent.py` is the helper used by the published agent workflow.

## Ownership map

- `app/models/`: request schemas, enums, constants, and the LLM provider registry.
- `app/controllers/`: HTTP routing, upload/download boundaries, and API task managers.
- `app/services/task.py`: orchestration and task lifecycle.
- `app/services/llm.py`: script, search-term, and social-metadata text generation.
- `app/services/material.py`: stock-media search, caching, download, provenance, and optional semantic reranking.
- `app/services/voice.py` and `app/services/subtitle.py`: narration and subtitle timing/transcription.
- `app/services/video.py`: local media preprocessing, clip assembly, compositing, subtitles, audio mixing, and encoding.
- `app/services/state.py`: selects in-memory or Redis-backed task state.
- `app/config/config.py`: loads and atomically persists runtime configuration.

## Runtime boundaries

The opt-in **motion agent** (`app/services/creative/motion_agent.py`) is a separate
construction path, not another legacy template. It asks a model to produce a plan
and React source from a brief and approved asset catalog. It uses a separately
selected author: GPT-6 Astra through Responses by default, or
explicit Gemini 3.6 Flash. Video judging remains Gemini and legacy LLM settings
are unchanged. Author identity is persisted and immutable on resume. For controlled
comparisons, `--materials-from` verifies and reuses a completed material pool but
replans without the old source or new acquisition. Trusted registered tools
acquire requested media/audio. Python stages each source/asset revision and invokes
the pinned Remotion worker in `motion/` through macOS Seatbelt. The worker receives
no provider keys and cannot read user files outside its staged project/dependency
root or make outbound requests beyond its two rendering loopback ports. System
read/IPC access needed by headless Chromium is allowed; this is a local development
boundary, not a hardened multi-tenant execution service.

The trusted wrapper owns duration, dimensions and audio attachment; generated React
owns visual construction. Compile failures feed bounded code repair. Optional
request-specific video judging records diagnostics without accepting/rejecting
creative work automatically; a revision requires confirmed, video-hash-bound
evidence and a hypothesis. Every attempt remains inspectable. Existing MoviePy,
API and Streamlit paths are unchanged. See [motion workflow](../motion/README.md)
and RFC-0011 for scope, constraints and reproducible commands.

The WebUI can run without the FastAPI process and calls the service layer directly. The API uses an in-memory or Redis-backed queue manager; WebUI generation uses its own in-process manager. Cross-platform publishing also runs in the current process and is not a durable external worker queue.

Generated state and artifacts live under `storage/`. The API mounts task artifacts at `/tasks`; `resource/public` is mounted as the root static site. Uploaded paths are resolved inside managed directories before they reach the media pipeline.

## Related context

- [Specification process](spec-process.md)
- [Video pipeline](video-pipeline.md)
- [External integrations](integrations.md)
- [Runtime and validation](runtime.md)
- [Video-quality feedback loop](evaluation.md)
