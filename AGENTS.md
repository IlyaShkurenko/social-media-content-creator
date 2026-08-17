# MoneyPrinterTurbo agent guide

MoneyPrinterTurbo is a Python 3.11+ application that turns a subject or script into short-form video through configurable LLM, media, TTS, subtitle, music, and publishing integrations. Production entrypoints are the Streamlit WebUI (`webui/Main.py`), FastAPI service (`main.py`), and synchronous CLI (`cli.py`).

## Discovering project context

- Read `.harness/manifest.yaml` to see which engineering harness modules are active.
- `docs/` is the current project context graph. For non-trivial work, start with `mq docs/ .tree`, then retrieve only the relevant documents or sections.
- Do not load the entire documentation tree or repository by default. Route from `docs/architecture.md` to the relevant subsystem first.
- `records/`, when present, contains historical material and is not default context. Consult it only when history is relevant.
- Current code, tests, configuration, and executable definitions override stale documentation.

## Maintaining project knowledge

- Update the canonical document in `docs/` only when a change affects durable architecture, system boundaries, integration contracts, runtime behavior, important invariants, or externally visible behavior.
- Correct or remove stale current-state claims instead of appending parallel explanations.
- Do not create documentation for routine refactors, formatting, minor dependency changes, or ordinary unit tests.

## Spec-driven changes

- Follow `docs/spec-process.md` for non-trivial features and cross-cutting behavior changes.
- Stable contract requirements use domain IDs such as `ADPIPE-*`, `RUNWAY-*`, `STORY-*`, `BRAND-*`, and `EVAL-*`.
- Put current feature specifications in `docs/specs/`. Architectural or cross-cutting proposals require an RFC under `records/rfcs/` before implementation.
- Link executable tests and BDD scenarios to the requirement IDs they verify. Keep small, local fixes lightweight.

## Behavior-driven tests

- Use BDD only for selected externally meaningful pipeline, provider, safety, and business behaviors; do not encode subjective visual taste or low-level helper details as Gherkin.
- Features live in `test/bdd/features/` and step definitions in `test/bdd/steps/`.
- Prefix scenario names with the governing requirement ID, for example `[EVAL-1.2]`.
- Run the stable BDD suite with `make test-bdd`. See `docs/testing/bdd.md` for authoring rules.

## Project rules and validation

- Keep credentials in the untracked `config.toml`; never add API keys or secrets to source, tests, logs, or documentation.
- Preserve path validation around uploads, task artifacts, and local media.
- Install the locked environment with `uv sync --frozen`.
- Run lint with `uv run --no-sync ruff check app cli.py main.py webui test`.
- Run tests with `uv run --no-sync python -X utf8 -m coverage run -m pytest -q test` and check the threshold with `uv run --no-sync python -m coverage report`.
