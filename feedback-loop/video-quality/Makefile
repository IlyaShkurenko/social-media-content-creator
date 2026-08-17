SHELL := /bin/sh

LOOP_ROOT := $(abspath .)
REPO_ROOT := $(abspath ../..)
PYTHON := $(REPO_ROOT)/.venv/bin/python
SCENARIO ?= evals/dataset/coverr-controlled-smoke-001.json
EXPERIMENT_SCENARIO ?= evals/dataset/mixed-media-stock-baseline-001.json
BASELINE ?= experiments/008-baseline
MIXED_STORYBOARD ?= evals/dataset/mixed-media-first-slice-001.json
MIXED_OUTPUT ?= .state/prepared/mixed-media-first-slice-001

.PHONY: baseline judge temporal-judge experiment experiment-start experiment-evaluate experiment-finish experiment-review evaluate verify mixed-prepare mixed-render-stock mixed-rerender-runway mixed-runway

baseline:
	$(PYTHON) scripts/run_experiment.py --kind baseline --slug baseline --scenario $(SCENARIO) $(if $(VIDEO),--video "$(VIDEO)",) $(if $(OBSERVATIONS),--observations "$(OBSERVATIONS)",) $(if $(PIPELINE_RECORD),--pipeline-record "$(PIPELINE_RECORD)",) $(if $(SUBTITLE),--subtitle "$(SUBTITLE)",) $(if $(JUDGE_EVIDENCE),--judge-evidence "$(JUDGE_EVIDENCE)",) $(if $(TEMPORAL_EVIDENCE),--temporal-evidence "$(TEMPORAL_EVIDENCE)",)

judge:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(BASELINE_VIDEO)" || (echo "BASELINE_VIDEO is required" >&2; exit 2)
	@test -n "$(CANDIDATE_VIDEO)" || (echo "CANDIDATE_VIDEO is required" >&2; exit 2)
	@test -n "$(JUDGE_OUTPUT)" || (echo "JUDGE_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_PREFIX)" || (echo "OPERATION_PREFIX is required" >&2; exit 2)
	$(PYTHON) evals/gemini_judge.py --scenario "$(EXPERIMENT_SCENARIO)" --baseline-video "$(BASELINE_VIDEO)" --candidate-video "$(CANDIDATE_VIDEO)" --output "$(JUDGE_OUTPUT)" --operation-prefix "$(OPERATION_PREFIX)" --confirm-paid YES

temporal-judge:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(TEMPORAL_VIDEO)" || (echo "TEMPORAL_VIDEO is required" >&2; exit 2)
	@test -n "$(TEMPORAL_OUTPUT)" || (echo "TEMPORAL_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_ID)" || (echo "OPERATION_ID is required" >&2; exit 2)
	$(PYTHON) evals/temporal_judge.py --storyboard "$(MIXED_STORYBOARD)" --video "$(TEMPORAL_VIDEO)" --scene-id hook --output "$(TEMPORAL_OUTPUT)" --operation-id "$(OPERATION_ID)" --confirm-paid YES

experiment: experiment-start

experiment-start:
	@test -n "$(SLUG)" || (echo "SLUG is required" >&2; exit 2)
	@test -n "$(PROBLEM)" || (echo "PROBLEM is required" >&2; exit 2)
	@test -n "$(HYPOTHESIS)" || (echo "HYPOTHESIS is required" >&2; exit 2)
	@test -n "$(CHANGE)" || (echo "CHANGE is required" >&2; exit 2)
	@test -n "$(EXPECTED)" || (echo "EXPECTED is required" >&2; exit 2)
	@test -n "$(BASELINE)" || (echo "BASELINE is required" >&2; exit 2)
	$(PYTHON) scripts/run_experiment.py --kind experiment --phase start --slug "$(SLUG)" --scenario "$(EXPERIMENT_SCENARIO)" --baseline "$(BASELINE)" --problem "$(PROBLEM)" --hypothesis "$(HYPOTHESIS)" --planned-change "$(CHANGE)" --expected-impact "$(EXPECTED)"

experiment-evaluate:
	@test -n "$(EXPERIMENT)" || (echo "EXPERIMENT is required" >&2; exit 2)
	$(PYTHON) scripts/run_experiment.py --kind experiment --phase evaluate --experiment "$(EXPERIMENT)" $(if $(VIDEO),--video "$(VIDEO)",) $(if $(OBSERVATIONS),--observations "$(OBSERVATIONS)",) $(if $(PIPELINE_RECORD),--pipeline-record "$(PIPELINE_RECORD)",) $(if $(SUBTITLE),--subtitle "$(SUBTITLE)",) $(if $(JUDGE_EVIDENCE),--judge-evidence "$(JUDGE_EVIDENCE)",) $(if $(TEMPORAL_EVIDENCE),--temporal-evidence "$(TEMPORAL_EVIDENCE)",)

experiment-finish:
	@test -n "$(EXPERIMENT)" || (echo "EXPERIMENT is required" >&2; exit 2)
	@test -n "$(DECISION)" || (echo "DECISION=keep or DECISION=revert is required" >&2; exit 2)
	@test -n "$(LEARNING)" || (echo "LEARNING is required" >&2; exit 2)
	$(PYTHON) scripts/run_experiment.py --kind experiment --phase finish --experiment "$(EXPERIMENT)" --decision "$(DECISION)" --learning "$(LEARNING)" $(if $(HUMAN_REVIEW),--human-review "$(HUMAN_REVIEW)",) $(if $(REVIEWER),--reviewer "$(REVIEWER)",) $(if $(REVIEW_OUTCOME),--review-outcome "$(REVIEW_OUTCOME)",) $(if $(REVIEW_REASON),--review-reason "$(REVIEW_REASON)",)

experiment-review:
	@test -n "$(EXPERIMENT)" || (echo "EXPERIMENT is required" >&2; exit 2)
	@test -n "$(REVIEW_OUTCOME)" || (echo "REVIEW_OUTCOME=accept or reject is required" >&2; exit 2)
	@test -n "$(REVIEWER)" || (echo "REVIEWER is required" >&2; exit 2)
	@test -n "$(REVIEW_REASON)" || (echo "REVIEW_REASON is required" >&2; exit 2)
	@test -n "$(LEARNING)" || (echo "LEARNING is required" >&2; exit 2)
	$(PYTHON) scripts/run_experiment.py --kind experiment --phase review --experiment "$(EXPERIMENT)" --review-outcome "$(REVIEW_OUTCOME)" --reviewer "$(REVIEWER)" --review-reason "$(REVIEW_REASON)" --learning "$(LEARNING)"

evaluate:
	@test -n "$(EXPERIMENT)" || (echo "EXPERIMENT is required" >&2; exit 2)
	@if test -f "$(EXPERIMENT)/inputs.json"; then \
		$(PYTHON) scripts/replay_experiment.py --experiment "$(EXPERIMENT)" --verify-only; \
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
