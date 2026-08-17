SHELL := /bin/sh

LOOP_ROOT := $(abspath .)
REPO_ROOT := $(abspath ../..)
PYTHON := $(REPO_ROOT)/.venv/bin/python
SCENARIO ?= evals/dataset/coverr-controlled-smoke-001.json

.PHONY: baseline experiment evaluate verify

baseline:
	$(PYTHON) scripts/run_experiment.py --kind baseline --slug baseline --scenario $(SCENARIO)

experiment:
	@test -n "$(SLUG)" || (echo "SLUG is required" >&2; exit 2)
	@test -n "$(HYPOTHESIS)" || (echo "HYPOTHESIS is required" >&2; exit 2)
	$(PYTHON) scripts/run_experiment.py --kind experiment --slug "$(SLUG)" --hypothesis "$(HYPOTHESIS)" --scenario $(SCENARIO) $(if $(VIDEO),--video "$(VIDEO)",)

evaluate:
	@test -n "$(EXPERIMENT)" || (echo "EXPERIMENT is required" >&2; exit 2)
	$(PYTHON) evals/evaluate.py --scenario "$(EXPERIMENT)/scenario.json" --output "$(EXPERIMENT)" $(if $(VIDEO),--video "$(VIDEO)",)

verify:
	@test -x "$(PYTHON)" || (echo "project virtualenv is missing: $(PYTHON)" >&2; exit 2)
	@command -v ffmpeg >/dev/null || (echo "ffmpeg is required" >&2; exit 2)
	@command -v ffprobe >/dev/null || (echo "ffprobe is required" >&2; exit 2)
	$(PYTHON) -m compileall -q evals scripts
	$(PYTHON) -m ruff check evals scripts
	$(PYTHON) -m unittest discover -s evals/tests -v
