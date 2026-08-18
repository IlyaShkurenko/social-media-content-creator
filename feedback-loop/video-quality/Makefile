SHELL := /bin/sh

LOOP_ROOT := $(abspath .)
REPO_ROOT := $(abspath ../..)
PYTHON := $(REPO_ROOT)/.venv/bin/python
SCENARIO ?= evals/dataset/coverr-controlled-smoke-001.json
EXPERIMENT_SCENARIO ?= evals/dataset/mixed-media-stock-baseline-001.json
BASELINE ?= experiments/008-baseline
MIXED_STORYBOARD ?= evals/dataset/mixed-media-first-slice-001.json
MIXED_OUTPUT ?= .state/prepared/mixed-media-first-slice-001
CAMPAIGN_BRIEF ?= evals/dataset/tict-campaign-brief-001.json
CAMPAIGN_STORYBOARD ?= evals/dataset/mixed-media-first-slice-001.json
CAMPAIGN_ASSET_ROOT ?= evals/assets/brand
CAMPAIGN_BUDGET_DATABASE ?= .state/mixed-media-iteration-001.sqlite3
CAMPAIGN_GEMINI_MODEL ?= gemini-3.6-flash
CAMPAIGN_PREFLIGHT ?= $(CAMPAIGN_OUTPUT)/campaign-preflight.json
CAMPAIGN_INVARIANT_SCENARIO ?= $(EXPERIMENT_SCENARIO)
BUDGET_STATUS ?= open

.PHONY: baseline judge candidate-judge invariant-judge candidate-evaluator-baseline temporal-judge budget-audit experiment experiment-start experiment-evaluate experiment-finish experiment-review evaluate verify mixed-prepare mixed-render-stock mixed-rerender-runway mixed-runway campaign-preflight campaign-run campaign-finalize

baseline:
	$(PYTHON) scripts/run_experiment.py --kind baseline --slug baseline --scenario $(SCENARIO) $(if $(VIDEO),--video "$(VIDEO)",) $(if $(OBSERVATIONS),--observations "$(OBSERVATIONS)",) $(if $(PIPELINE_RECORD),--pipeline-record "$(PIPELINE_RECORD)",) $(if $(SUBTITLE),--subtitle "$(SUBTITLE)",) $(if $(JUDGE_EVIDENCE),--judge-evidence "$(JUDGE_EVIDENCE)",) $(if $(TEMPORAL_EVIDENCE),--temporal-evidence "$(TEMPORAL_EVIDENCE)",)

judge:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(BASELINE_VIDEO)" || (echo "BASELINE_VIDEO is required" >&2; exit 2)
	@test -n "$(CANDIDATE_VIDEO)" || (echo "CANDIDATE_VIDEO is required" >&2; exit 2)
	@test -n "$(JUDGE_OUTPUT)" || (echo "JUDGE_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_PREFIX)" || (echo "OPERATION_PREFIX is required" >&2; exit 2)
	$(PYTHON) evals/gemini_judge.py --scenario "$(EXPERIMENT_SCENARIO)" --baseline-video "$(BASELINE_VIDEO)" --candidate-video "$(CANDIDATE_VIDEO)" --output "$(JUDGE_OUTPUT)" --operation-prefix "$(OPERATION_PREFIX)" --confirm-paid YES

candidate-judge:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(CANDIDATE_CONCEPT)" || (echo "CANDIDATE_CONCEPT is required" >&2; exit 2)
	@test -n "$(CANDIDATE_STORYBOARD)" || (echo "CANDIDATE_STORYBOARD is required" >&2; exit 2)
	@test -n "$(CANDIDATE_VIDEO)" || (echo "CANDIDATE_VIDEO is required" >&2; exit 2)
	@test -n "$(CANDIDATE_JUDGE_OUTPUT)" || (echo "CANDIDATE_JUDGE_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_ID)" || (echo "OPERATION_ID is required" >&2; exit 2)
	$(PYTHON) evals/candidate_judge.py --concept "$(CANDIDATE_CONCEPT)" --storyboard "$(CANDIDATE_STORYBOARD)" $(if $(CANDIDATE_ID),--concept-id "$(CANDIDATE_ID)",) --video "$(CANDIDATE_VIDEO)" --output "$(CANDIDATE_JUDGE_OUTPUT)" --operation-id "$(OPERATION_ID)" --model "$(CAMPAIGN_GEMINI_MODEL)" --budget-database "$(CAMPAIGN_BUDGET_DATABASE)" --confirm-paid YES

invariant-judge:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(BASELINE_VIDEO)" || (echo "BASELINE_VIDEO is required" >&2; exit 2)
	@test -n "$(CANDIDATE_VIDEO)" || (echo "CANDIDATE_VIDEO is required" >&2; exit 2)
	@test -n "$(INVARIANT_JUDGE_OUTPUT)" || (echo "INVARIANT_JUDGE_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_PREFIX)" || (echo "OPERATION_PREFIX is required" >&2; exit 2)
	$(PYTHON) evals/invariant_judge.py --scenario "$(CAMPAIGN_INVARIANT_SCENARIO)" --baseline-video "$(BASELINE_VIDEO)" --candidate-video "$(CANDIDATE_VIDEO)" --output "$(INVARIANT_JUDGE_OUTPUT)" --operation-prefix "$(OPERATION_PREFIX)" --model "$(CAMPAIGN_GEMINI_MODEL)" --budget-database "$(CAMPAIGN_BUDGET_DATABASE)" --confirm-paid YES

candidate-evaluator-baseline:
	@test -n "$(EVALUATOR_BASELINE_EXPERIMENT)" || (echo "EVALUATOR_BASELINE_EXPERIMENT is required" >&2; exit 2)
	@test -n "$(CAMPAIGN_PLAN)" || (echo "CAMPAIGN_PLAN is required" >&2; exit 2)
	@test -n "$(REFERENCE_VIDEO)" || (echo "REFERENCE_VIDEO is required" >&2; exit 2)
	@test -n "$(CANDIDATE_VIDEO)" || (echo "CANDIDATE_VIDEO is required" >&2; exit 2)
	@test -n "$(CANDIDATE_JUDGE_EVIDENCE)" || (echo "CANDIDATE_JUDGE_EVIDENCE is required" >&2; exit 2)
	@test -n "$(INVARIANT_JUDGE_EVIDENCE)" || (echo "INVARIANT_JUDGE_EVIDENCE is required" >&2; exit 2)
	@test -n "$(CANDIDATE_LABEL)" || (echo "CANDIDATE_LABEL=accept|reject|pending is required" >&2; exit 2)
	@test -n "$(REVIEWER)" || (echo "REVIEWER is required" >&2; exit 2)
	@test -n "$(REFERENCE_REASON)" || (echo "REFERENCE_REASON is required" >&2; exit 2)
	@test -n "$(CANDIDATE_REASON)" || (echo "CANDIDATE_REASON is required" >&2; exit 2)
	$(PYTHON) scripts/record_candidate_evaluator_baseline.py --experiment "$(EVALUATOR_BASELINE_EXPERIMENT)" --scenario "$(LOOP_ROOT)/$(EXPERIMENT_SCENARIO)" --campaign-plan "$(CAMPAIGN_PLAN)" --reference-video "$(REFERENCE_VIDEO)" --candidate-video "$(CANDIDATE_VIDEO)" --candidate-evidence "$(CANDIDATE_JUDGE_EVIDENCE)" --invariant-evidence "$(INVARIANT_JUDGE_EVIDENCE)" --candidate-label "$(CANDIDATE_LABEL)" --reviewer "$(REVIEWER)" --reference-reason "$(REFERENCE_REASON)" --candidate-reason "$(CANDIDATE_REASON)"

temporal-judge:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(TEMPORAL_VIDEO)" || (echo "TEMPORAL_VIDEO is required" >&2; exit 2)
	@test -n "$(TEMPORAL_OUTPUT)" || (echo "TEMPORAL_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_ID)" || (echo "OPERATION_ID is required" >&2; exit 2)
	$(PYTHON) evals/temporal_judge.py --storyboard "$(MIXED_STORYBOARD)" --video "$(TEMPORAL_VIDEO)" --scene-id hook --output "$(TEMPORAL_OUTPUT)" --operation-id "$(OPERATION_ID)" --confirm-paid YES

budget-audit:
	$(PYTHON) scripts/audit_budget.py --budget-database "$(CAMPAIGN_BUDGET_DATABASE)" --status "$(BUDGET_STATUS)"

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

campaign-preflight:
	@test -n "$(CAMPAIGN_OUTPUT)" || (echo "CAMPAIGN_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_PREFIX)" || (echo "OPERATION_PREFIX is required" >&2; exit 2)
	$(PYTHON) scripts/run_campaign.py --brief "$(CAMPAIGN_BRIEF)" --storyboard "$(CAMPAIGN_STORYBOARD)" --asset-root "$(CAMPAIGN_ASSET_ROOT)" --output "$(CAMPAIGN_OUTPUT)" --operation-prefix "$(OPERATION_PREFIX)" --gemini-model "$(CAMPAIGN_GEMINI_MODEL)" --budget-database "$(CAMPAIGN_BUDGET_DATABASE)" $(if $(CAMPAIGN_CONCEPTS),--concepts "$(CAMPAIGN_CONCEPTS)",) --preflight-only

campaign-run:
	@test "$(CONFIRM_PAID)" = "YES" || (echo "CONFIRM_PAID=YES is required" >&2; exit 2)
	@test -n "$(CAMPAIGN_OUTPUT)" || (echo "CAMPAIGN_OUTPUT is required" >&2; exit 2)
	@test -n "$(OPERATION_PREFIX)" || (echo "OPERATION_PREFIX is required" >&2; exit 2)
	@test -f "$(CAMPAIGN_PREFLIGHT)" || (echo "campaign preflight is missing: $(CAMPAIGN_PREFLIGHT)" >&2; exit 2)
	$(PYTHON) scripts/run_campaign.py --brief "$(CAMPAIGN_BRIEF)" --storyboard "$(CAMPAIGN_STORYBOARD)" --asset-root "$(CAMPAIGN_ASSET_ROOT)" --output "$(CAMPAIGN_OUTPUT)" --operation-prefix "$(OPERATION_PREFIX)" --gemini-model "$(CAMPAIGN_GEMINI_MODEL)" --budget-database "$(CAMPAIGN_BUDGET_DATABASE)" $(if $(CAMPAIGN_CONCEPTS),--concepts "$(CAMPAIGN_CONCEPTS)",) --require-preflight "$(CAMPAIGN_PREFLIGHT)" --execute-paid --confirm-paid YES

campaign-finalize:
	@test -n "$(CAMPAIGN_OUTPUT)" || (echo "CAMPAIGN_OUTPUT is required" >&2; exit 2)
	@test -n "$(CAMPAIGN_REVIEW)" || (echo "CAMPAIGN_REVIEW is required" >&2; exit 2)
	$(PYTHON) scripts/finalize_campaign.py --output "$(CAMPAIGN_OUTPUT)" --review "$(CAMPAIGN_REVIEW)" --budget-database "$(CAMPAIGN_BUDGET_DATABASE)" --invariant-scenario "$(CAMPAIGN_INVARIANT_SCENARIO)" $(foreach evidence,$(CAMPAIGN_CANDIDATE_EVIDENCE),--candidate-evidence "$(evidence)") $(foreach evidence,$(CAMPAIGN_INVARIANT_EVIDENCE),--invariant-evidence "$(evidence)")
