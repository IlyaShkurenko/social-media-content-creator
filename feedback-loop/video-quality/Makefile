SHELL := /bin/sh

LOOP_ROOT := $(abspath .)
REPO_ROOT := $(abspath ../..)
PYTHON := $(REPO_ROOT)/.venv/bin/python
SCENARIO ?= evals/dataset/coverr-controlled-smoke-001.json
MIXED_STORYBOARD ?= evals/dataset/mixed-media-first-slice-001.json
MIXED_OUTPUT ?= .state/prepared/mixed-media-first-slice-001

.PHONY: baseline experiment evaluate verify mixed-prepare mixed-render-stock mixed-rerender-runway mixed-runway

baseline:
	$(PYTHON) scripts/run_experiment.py --kind baseline --slug baseline --scenario $(SCENARIO) $(if $(VIDEO),--video "$(VIDEO)",) $(if $(OBSERVATIONS),--observations "$(OBSERVATIONS)",) $(if $(PIPELINE_RECORD),--pipeline-record "$(PIPELINE_RECORD)",) $(if $(SUBTITLE),--subtitle "$(SUBTITLE)",)

experiment:
	@test -n "$(SLUG)" || (echo "SLUG is required" >&2; exit 2)
	@test -n "$(HYPOTHESIS)" || (echo "HYPOTHESIS is required" >&2; exit 2)
	$(PYTHON) scripts/run_experiment.py --kind experiment --slug "$(SLUG)" --hypothesis "$(HYPOTHESIS)" --scenario $(SCENARIO) $(if $(VIDEO),--video "$(VIDEO)",) $(if $(OBSERVATIONS),--observations "$(OBSERVATIONS)",) $(if $(PIPELINE_RECORD),--pipeline-record "$(PIPELINE_RECORD)",) $(if $(SUBTITLE),--subtitle "$(SUBTITLE)",)

evaluate:
	@test -n "$(EXPERIMENT)" || (echo "EXPERIMENT is required" >&2; exit 2)
	@if test -f "$(EXPERIMENT)/inputs.json"; then \
		$(PYTHON) scripts/replay_experiment.py --experiment "$(EXPERIMENT)"; \
	else \
		$(PYTHON) evals/evaluate.py --scenario "$(EXPERIMENT)/scenario.json" --output "$(EXPERIMENT)" $(if $(VIDEO),--video "$(VIDEO)",) $(if $(OBSERVATIONS),--observations "$(OBSERVATIONS)",) $(if $(PIPELINE_RECORD),--pipeline-record "$(PIPELINE_RECORD)",) $(if $(SUBTITLE),--subtitle "$(SUBTITLE)",); \
	fi

verify:
	@test -x "$(PYTHON)" || (echo "project virtualenv is missing: $(PYTHON)" >&2; exit 2)
	@command -v ffmpeg >/dev/null || (echo "ffmpeg is required" >&2; exit 2)
	@command -v ffprobe >/dev/null || (echo "ffprobe is required" >&2; exit 2)
	$(PYTHON) -m compileall -q evals scripts
	$(PYTHON) -m ruff check evals scripts
	$(PYTHON) -m unittest discover -s evals/tests -v

mixed-prepare:
	$(PYTHON) scripts/run_mixed_media.py --storyboard "$(MIXED_STORYBOARD)" --output "$(MIXED_OUTPUT)"

mixed-render-stock:
	@test -n "$(HOOK_VIDEO)" || (echo "HOOK_VIDEO is required" >&2; exit 2)
	$(PYTHON) scripts/run_mixed_media.py --storyboard "$(MIXED_STORYBOARD)" --output "$(MIXED_OUTPUT)" --hook-video "$(HOOK_VIDEO)" --narrate

mixed-rerender-runway:
	@test -n "$(HOOK_VIDEO)" || (echo "HOOK_VIDEO is required" >&2; exit 2)
	$(PYTHON) scripts/run_mixed_media.py --storyboard "$(MIXED_STORYBOARD)" --output "$(MIXED_OUTPUT)" --hook-video "$(HOOK_VIDEO)" --variant-id runway-candidate --narrate

mixed-runway:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	$(PYTHON) scripts/run_mixed_media.py --storyboard "$(MIXED_STORYBOARD)" --output "$(MIXED_OUTPUT)" --execute-runway --confirm-paid YES --narrate
