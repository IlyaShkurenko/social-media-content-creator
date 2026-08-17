# Runtime and validation

## Local setup

Python 3.11 or later is required; Python 3.11 is the recommended baseline. The locked environment is managed with `uv`:

```bash
uv python install 3.11
uv sync --frozen
```

If `uv` is unavailable, use a project-local virtual environment with a CI-supported Python version. This installs only the direct pinned requirements; `uv sync --frozen` remains the canonical way to reproduce every transitive version from `uv.lock`:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On first application start, `config.toml` is created from `config.example.toml`. Keep it untracked because it contains provider credentials. Configure at least the integrations used by the selected workflow: an LLM for generated scripts/terms, a stock-media key unless local media is used, and any non-default TTS/music providers.

## Entrypoints

```bash
# Streamlit WebUI
sh webui.sh

# FastAPI service and Swagger UI at http://127.0.0.1:8080/docs
uv run python main.py

# Synchronous CLI example
uv run python cli.py --video-subject "How AI is changing everyday life"

# Inspect all CLI options and partial pipeline stop points
uv run python cli.py --help
```

The WebUI normally listens on a dynamically selected local port beginning with 8501. `MPT_WEBUI_HOST=0.0.0.0 sh webui.sh` exposes it to the local network.

## Docker

`docker compose -f docker-compose.release.yml up` runs the published GHCR image and mounts only `config.toml` and `storage/`. `docker compose up` builds locally and mounts the whole repository. Both expose the WebUI on `127.0.0.1:8501` and API on `127.0.0.1:8080`.

## Storage and process behavior

- `storage/tasks/` contains per-task artifacts and final videos.
- `storage/local_videos/` is the managed root for uploaded local images/video.
- `storage/cache_videos/` is the default shared stock-material cache unless `material_directory` changes it.
- `resource/fonts/` and `resource/songs/` contain managed bundled assets.
- API state and task concurrency can use memory or Redis. Cross-post workers remain in-process even when Redis task state is enabled.

## Validation

The CI baseline runs on Python 3.11 and 3.13:

```bash
uv run --no-sync python -m compileall app cli.py main.py webui test
uv run --no-sync ruff check app cli.py main.py webui test
uv run --no-sync python -X utf8 -m coverage run -m pytest -q test
uv run --no-sync python -m coverage report
```

Coverage is configured with a 70 percent failure threshold. CI also runs a Windows smoke subset and builds multi-architecture Docker images separately.

## Related context

- [Architecture](architecture.md)
- [Video pipeline](video-pipeline.md)
- [External integrations](integrations.md)
