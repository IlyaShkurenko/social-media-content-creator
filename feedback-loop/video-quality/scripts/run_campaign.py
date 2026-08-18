from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from app.services.creative.budget import (  # noqa: E402
    BudgetSnapshot,
    IterationBudgetLedger,
)
from app.services.creative.campaign import (  # noqa: E402
    CampaignBrief,
    HypothesisBatch,
    build_campaign_plan,
    execute_candidate_pool,
    plan_hypotheses,
)
from app.services.creative.campaign_preflight import (  # noqa: E402
    PLANNER_OPERATION_SUFFIX,
    CampaignPreflightReport,
    TemporalPreflightContract,
    build_campaign_preflight,
    build_gemini_concept_config,
    require_matching_preflight,
)
from app.services.creative.narration import generate_scene_narration  # noqa: E402
from app.services.creative.renderer import render_mixed_media_video  # noqa: E402
from app.services.creative.runway import RunwayAdapter  # noqa: E402
from app.services.creative.storyboard import validate_storyboard  # noqa: E402
from evals.gemini_judge import (  # noqa: E402
    JUDGE_MODEL,
    actual_usage_cost_microusd,
    is_definite_nonbillable_gemini_error,
    sanitize_judge_evidence,
    sha256_text,
)
from evals import temporal_judge as temporal_judge_module  # noqa: E402
from evals.temporal_judge import (  # noqa: E402
    EVENT_TYPES as TEMPORAL_EVENT_TYPES,
    EVALUATOR_VERSION as TEMPORAL_EVALUATOR_VERSION,
    FRAMES_PER_STRIP as TEMPORAL_FRAMES_PER_STRIP,
    MAX_OUTPUT_TOKENS as TEMPORAL_MAX_OUTPUT_TOKENS,
    GeminiTemporalJudge,
    MAXIMUM_COST_MICROUSD as TEMPORAL_MAXIMUM_COST_MICROUSD,
    TEMPORAL_SAMPLE_FPS,
    TEMPORAL_SCHEMA_VERSION,
    TemporalJudgeResponse,
    validate_existing_temporal_evidence,
)


ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_BUDGET_DATABASE = (
    LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
)
DEFAULT_STORYBOARD = (
    LOOP_ROOT / "evals" / "dataset" / "mixed-media-first-slice-001.json"
)
DEFAULT_ASSET_ROOT = LOOP_ROOT / "evals" / "assets" / "brand"
DEFAULT_BRIEF = LOOP_ROOT / "evals" / "dataset" / "tict-campaign-brief-001.json"
PLANNER_MAXIMUM_COST_MICROUSD = 100_000


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(value.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_file(value: str | Path, *, label: str) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else LOOP_ROOT / path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    return resolved


def _resolve_output(value: str | Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else LOOP_ROOT / path).resolve()
    if LOOP_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"campaign output must stay inside {LOOP_ROOT}")
    return resolved


def _configured_runway_contract() -> tuple[str, str]:
    config_path = REPO_ROOT / "config.toml"
    if not config_path.is_file():
        return RunwayAdapter.BASE_URL, RunwayAdapter.API_VERSION
    from app.config import config

    runway_base_url = str(
        config.app.get("runway_base_url", RunwayAdapter.BASE_URL)
        or RunwayAdapter.BASE_URL
    ).strip()
    runway_api_version = str(
        config.app.get("runway_api_version", RunwayAdapter.API_VERSION)
        or RunwayAdapter.API_VERSION
    ).strip()
    return runway_base_url, runway_api_version


def _configured_keys() -> tuple[str, str, str, str]:
    from app.config import config

    gemini_key = str(config.app.get("gemini_api_key", "") or "").strip()
    gemini_key = gemini_key or os.getenv("GEMINI_API_KEY", "").strip()
    runway_key = str(config.app.get("runway_api_key", "") or "").strip()
    runway_key = runway_key or os.getenv("RUNWAYML_API_SECRET", "").strip()
    if not gemini_key:
        raise RuntimeError("Gemini API key is not configured")
    if not runway_key:
        raise RuntimeError("Runway API key is not configured")
    runway_base_url, runway_api_version = _configured_runway_contract()
    return gemini_key, runway_key, runway_base_url, runway_api_version


def _read_budget_snapshot(database_path: Path) -> BudgetSnapshot:
    """Read the existing iteration ledger without opening a write transaction."""

    return IterationBudgetLedger.read_only_audit(
        database_path,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    ).snapshot


def _temporal_preflight_contract(gemini_model: str) -> TemporalPreflightContract:
    implementation_path = Path(temporal_judge_module.__file__).resolve()
    return TemporalPreflightContract(
        evaluator_version=TEMPORAL_EVALUATOR_VERSION,
        evidence_schema_version=TEMPORAL_SCHEMA_VERSION,
        response_schema_sha256=_sha256_json(
            TemporalJudgeResponse.model_json_schema()
        ),
        implementation_sha256=_sha256_file(implementation_path),
        sample_fps=TEMPORAL_SAMPLE_FPS,
        frames_per_strip=TEMPORAL_FRAMES_PER_STRIP,
        max_output_tokens=TEMPORAL_MAX_OUTPUT_TOKENS,
        event_types=list(TEMPORAL_EVENT_TYPES),
        model=gemini_model,
        scene_id="hook",
        start_seconds=0.0,
        end_seconds=5.0,
    )


class BudgetedGeminiConceptPlanner:
    def __init__(
        self,
        *,
        api_key: str,
        ledger: IterationBudgetLedger,
        operation_id: str,
        model: str = JUDGE_MODEL,
    ) -> None:
        self.client = genai.Client(api_key=api_key)
        self.ledger = ledger
        self.operation_id = operation_id
        self.model = model
        self.last_record: dict[str, Any] | None = None

    def __call__(self, prompt: str) -> str:
        existing = self.ledger.find_operation(self.operation_id)
        if existing is not None:
            raise RuntimeError(
                f"Gemini planner operation {self.operation_id!r} is already "
                f"{existing.status}; automatic paid resubmission is blocked"
            )
        self.ledger.ensure_available(PLANNER_MAXIMUM_COST_MICROUSD)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=build_gemini_concept_config(),
            )
        except errors.APIError as exc:
            detail = str(getattr(exc, "message", "") or "").strip()[:500]
            if is_definite_nonbillable_gemini_error(exc):
                raise RuntimeError(
                    "Gemini rejected concept planning before returning a billable "
                    f"result (HTTP {exc.code}); no charge was recorded"
                    + (f": {detail}" if detail else "")
                ) from exc
            self.ledger.record_manual_charge(
                self.operation_id,
                PLANNER_MAXIMUM_COST_MICROUSD,
                "Gemini concept planner; ambiguous HTTP "
                f"{exc.code}, worst-case charge",
            )
            raise RuntimeError(
                "Gemini concept-planning outcome is ambiguous; the worst-case "
                "charge was recorded and the request was not retried"
            ) from exc
        except Exception as exc:
            self.ledger.record_manual_charge(
                self.operation_id,
                PLANNER_MAXIMUM_COST_MICROUSD,
                "Gemini concept planner; unknown provider outcome, worst-case charge",
            )
            raise RuntimeError(
                "Gemini concept-planning outcome is unknown; the worst-case "
                "charge was recorded and the request was not retried"
            ) from exc

        usage_metadata = getattr(response, "usage_metadata", None)
        usage = {
            "prompt_tokens": getattr(usage_metadata, "prompt_token_count", None),
            "output_tokens": getattr(
                usage_metadata,
                "candidates_token_count",
                None,
            ),
            "thinking_tokens": getattr(
                usage_metadata,
                "thoughts_token_count",
                None,
            ),
            "total_tokens": getattr(usage_metadata, "total_token_count", None),
        }
        actual_cost = actual_usage_cost_microusd(
            model=self.model,
            prompt_tokens=usage["prompt_tokens"],
            output_tokens=usage["output_tokens"],
        )
        charged = actual_cost or PLANNER_MAXIMUM_COST_MICROUSD
        response_id = str(
            getattr(response, "response_id", "")
            or f"gemini-{sha256_text(self.operation_id)[:24]}"
        )
        self.ledger.record_manual_charge(
            self.operation_id,
            charged,
            f"Gemini {self.model} concept planner; provider response {response_id}",
        )
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if hasattr(parsed, "model_dump"):
                payload = parsed.model_dump(mode="json")
            else:
                payload = parsed
            raw = json.dumps(payload, ensure_ascii=False)
        else:
            raw = str(response.text)
        snapshot = self.ledger.snapshot()
        self.last_record = sanitize_judge_evidence(
            {
                "provider": "google-gemini",
                "requested_model": self.model,
                "model_version": str(
                    getattr(response, "model_version", "") or self.model
                ),
                "response_id": response_id,
                "operation_id": self.operation_id,
                "prompt_sha256": sha256_text(prompt),
                "usage": usage,
                "charged_microusd": charged,
                "remaining_microusd": snapshot.remaining_microusd,
            }
        )
        return raw


def _load_or_plan_concepts(
    *,
    args: argparse.Namespace,
    brief: CampaignBrief,
    gemini_key: str,
    ledger: IterationBudgetLedger,
    output_dir: Path,
) -> HypothesisBatch:
    if args.concepts:
        concept_path = _resolve_file(args.concepts, label="concept batch")
        raw = concept_path.read_text(encoding="utf-8")
        source_record = {"mode": "replay", "source": str(concept_path)}
        planner = None
    else:
        operation_id = f"{args.operation_prefix}-{PLANNER_OPERATION_SUFFIX}"
        prompt = build_hypothesis_prompt_for_record(brief)
        raw_path = output_dir / "concept-planning-raw.json"
        record_path = output_dir / "concept-planning.json"
        existing_files = (raw_path.exists(), record_path.exists())
        if any(existing_files) and not all(existing_files):
            raise RuntimeError(
                "concept-planning checkpoint is incomplete; paid planning will not "
                "be retried automatically"
            )
        if all(existing_files):
            raw = raw_path.read_text(encoding="utf-8")
            source_record = json.loads(record_path.read_text(encoding="utf-8"))
            expected = {
                "mode": "generated",
                "operation_id": operation_id,
                "requested_model": args.gemini_model,
                "prompt_sha256": sha256_text(prompt),
            }
            for key, value in expected.items():
                if source_record.get(key) != value:
                    raise RuntimeError(
                        f"concept-planning checkpoint has stale {key}; paid planning "
                        "will not be retried automatically"
                    )
            if ledger.find_operation(operation_id) is None:
                raise RuntimeError(
                    "concept-planning checkpoint has no matching durable ledger entry"
                )
            planner = None
        else:
            planner = BudgetedGeminiConceptPlanner(
                api_key=gemini_key,
                ledger=ledger,
                operation_id=operation_id,
                model=args.gemini_model,
            )
            raw = planner(prompt)
            source_record = {"mode": "generated", **(planner.last_record or {})}

    # Preserve the exact provider result before strict domain validation so a
    # rejected planning call remains reproducible evidence for the experiment.
    _write_text(output_dir / "concept-planning-raw.json", raw)
    _write_json(output_dir / "concept-planning.json", source_record)

    # Revalidate through the same production boundary for both live and replay.
    batch = plan_hypotheses(brief, response_generator=lambda _: raw)
    _write_json(output_dir / "concepts.json", batch.model_dump(mode="json"))
    return batch


def build_hypothesis_prompt_for_record(brief: CampaignBrief) -> str:
    from app.services.creative.campaign import build_hypothesis_prompt

    return build_hypothesis_prompt(brief, include_json_schema=False)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan, generate, screen, and render a real advertising candidate pool"
    )
    parser.add_argument("--brief", default=str(DEFAULT_BRIEF))
    parser.add_argument("--concepts")
    parser.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT))
    parser.add_argument("--output", required=True)
    parser.add_argument("--operation-prefix", required=True)
    parser.add_argument("--gemini-model", default=JUDGE_MODEL)
    parser.add_argument("--budget-database", default=str(DEFAULT_BUDGET_DATABASE))
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--require-preflight")
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--confirm-paid", default="")
    args = parser.parse_args()
    if args.execute_paid and args.confirm_paid != "YES":
        parser.error("paid execution requires --confirm-paid YES")
    if args.execute_paid and not args.require_preflight:
        parser.error("paid execution requires --require-preflight")
    if args.preflight_only and args.execute_paid:
        parser.error("--preflight-only and --execute-paid are mutually exclusive")
    if not args.execute_paid and not args.preflight_only and not args.concepts:
        parser.error("plan-only mode requires --concepts to avoid an implicit paid call")
    return args


def main() -> int:
    args = _parse_args()
    output_dir = _resolve_output(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    brief = CampaignBrief.model_validate(
        json.loads(
            _resolve_file(args.brief, label="campaign brief").read_text(
                encoding="utf-8"
            )
        )
    )
    storyboard = validate_storyboard(
        json.loads(
            _resolve_file(args.storyboard, label="storyboard template").read_text(
                encoding="utf-8"
            )
        )
    )
    asset_root = _resolve_file(
        Path(args.asset_root) / "brand-kit.json",
        label="brand asset root",
    ).parent
    budget_path = Path(args.budget_database)
    if not budget_path.is_absolute():
        budget_path = (LOOP_ROOT / budget_path).resolve()
    budget_snapshot = _read_budget_snapshot(budget_path)
    runway_base_url, runway_api_version = _configured_runway_contract()
    temporal_contract = _temporal_preflight_contract(args.gemini_model)
    planning_mode = "replay" if args.concepts else "live"
    replay_batch: HypothesisBatch | None = None
    if args.concepts:
        concept_path = _resolve_file(args.concepts, label="concept batch")
        concept_raw = concept_path.read_text(encoding="utf-8")
        replay_batch = plan_hypotheses(
            brief,
            response_generator=lambda _: concept_raw,
        )
    current_preflight = build_campaign_preflight(
        brief=brief,
        template_storyboard=storyboard,
        asset_root=asset_root,
        operation_prefix=args.operation_prefix,
        gemini_model=args.gemini_model,
        planning_mode=planning_mode,
        budget_snapshot=budget_snapshot,
        planner_maximum_cost_microusd=PLANNER_MAXIMUM_COST_MICROUSD,
        temporal_maximum_cost_microusd=TEMPORAL_MAXIMUM_COST_MICROUSD,
        runway_base_url=runway_base_url,
        runway_api_version=runway_api_version,
        temporal_contract=temporal_contract,
        concepts=replay_batch,
        orchestrator_sha256=_sha256_file(Path(__file__).resolve()),
    )
    if args.preflight_only:
        preflight_path = output_dir / "campaign-preflight.json"
        _write_json(preflight_path, current_preflight.model_dump(mode="json"))
        print(json.dumps(current_preflight.model_dump(mode="json"), indent=2))
        return 0

    if args.execute_paid:
        stored_path = _resolve_file(
            args.require_preflight,
            label="campaign preflight",
        )
        stored_preflight = CampaignPreflightReport.model_validate(
            json.loads(stored_path.read_text(encoding="utf-8"))
        )
        require_matching_preflight(
            stored_preflight_id=stored_preflight.preflight_id,
            current_preflight_id=current_preflight.preflight_id,
        )
        if stored_preflight.stage != current_preflight.stage:
            raise RuntimeError("campaign preflight stage does not match execution")

    if not args.execute_paid:
        assert replay_batch is not None
        plan = build_campaign_plan(
            replay_batch,
            template_storyboard=storyboard,
            operation_prefix=args.operation_prefix,
        )
        _write_json(output_dir / "campaign-plan.json", plan.model_dump(mode="json"))
        print(json.dumps(plan.model_dump(mode="json"), indent=2))
        return 0

    ledger = IterationBudgetLedger(
        budget_path,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    )
    gemini_key, runway_key, runway_base_url, runway_api_version = _configured_keys()
    batch = _load_or_plan_concepts(
        args=args,
        brief=brief,
        gemini_key=gemini_key,
        ledger=ledger,
        output_dir=output_dir,
    )
    generation_preflight = build_campaign_preflight(
        brief=brief,
        template_storyboard=storyboard,
        asset_root=asset_root,
        operation_prefix=args.operation_prefix,
        gemini_model=args.gemini_model,
        planning_mode=planning_mode,
        budget_snapshot=ledger.snapshot(),
        planner_maximum_cost_microusd=PLANNER_MAXIMUM_COST_MICROUSD,
        temporal_maximum_cost_microusd=TEMPORAL_MAXIMUM_COST_MICROUSD,
        runway_base_url=runway_base_url,
        runway_api_version=runway_api_version,
        temporal_contract=temporal_contract,
        concepts=batch,
        orchestrator_sha256=_sha256_file(Path(__file__).resolve()),
    )
    _write_json(
        output_dir / "generation-preflight.json",
        generation_preflight.model_dump(mode="json"),
    )
    plan = build_campaign_plan(
        batch,
        template_storyboard=storyboard,
        operation_prefix=args.operation_prefix,
    )
    _write_json(output_dir / "campaign-plan.json", plan.model_dump(mode="json"))
    adapter = RunwayAdapter(
        api_key=runway_key,
        budget_ledger=ledger,
        base_url=runway_base_url,
        api_version=runway_api_version,
    )
    temporal = GeminiTemporalJudge(
        api_key=gemini_key,
        budget_ledger=ledger,
        model=args.gemini_model,
    )

    def screen_hook(candidate, video_path: Path) -> dict[str, Any]:
        hook = candidate.storyboard.scenes[0]
        intent = hook.visual_intent
        description = "; ".join(
            [
                intent.setting,
                intent.subject_action,
                intent.camera,
                f"screen policy: {intent.screen_content_policy}",
            ]
        )
        evidence_path = output_dir / "temporal" / f"{candidate.candidate_id}.json"
        failure_path = (
            output_dir / "temporal" / f"{candidate.candidate_id}.failure.json"
        )
        operation_id = f"{args.operation_prefix}-{candidate.candidate_id}-temporal"
        try:
            if evidence_path.is_file():
                loaded = json.loads(evidence_path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError(
                        "existing temporal evidence must contain one JSON object"
                    )
                evidence = validate_existing_temporal_evidence(
                    loaded,
                    video_path=video_path,
                    scene_id="hook",
                    scene_description=description,
                    start_seconds=0.0,
                    end_seconds=5.0,
                    operation_id=operation_id,
                    model=args.gemini_model,
                    budget_ledger=ledger,
                )
            else:
                evidence = temporal.inspect(
                    video_path=video_path,
                    scene_id="hook",
                    scene_description=description,
                    start_seconds=0.0,
                    end_seconds=5.0,
                    working_dir=output_dir
                    / "temporal"
                    / f"{candidate.candidate_id}-strips",
                    operation_id=operation_id,
                )
                _write_json(evidence_path, evidence)
            events = evidence["events"]
            return {
                "temporal_consistency_pass": not any(
                    event.get("severity") == "high" for event in events
                ),
                "temporal_events": events,
                "temporal_evidence": str(evidence_path),
                "temporal_status": "complete",
            }
        except Exception as exc:
            failure = {
                "status": "failed_closed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _write_json(failure_path, failure)
            return {
                "temporal_consistency_pass": False,
                "temporal_events": [
                    {
                        "event_id": "screening-unavailable",
                        "severity": "high",
                        "reason": "Temporal screening did not return valid evidence.",
                    }
                ],
                "temporal_evidence": str(failure_path),
                "temporal_status": "failed_closed",
            }

    pool = execute_candidate_pool(
        plan,
        adapter=adapter,
        output_dir=output_dir,
        screen_hook=screen_hook,
    )
    rendered: list[dict[str, Any]] = []
    plan_by_id = {candidate.candidate_id: candidate for candidate in plan.candidates}
    records_by_id = {
        record["candidate_id"]: record for record in pool["candidates"]
    }
    for candidate_id in pool["eligible_candidate_ids"]:
        candidate = plan_by_id[candidate_id]
        record = records_by_id[candidate_id]
        narration = generate_scene_narration(
            candidate.storyboard,
            output_dir=output_dir / "narration" / candidate_id,
            interface_locale="en-US",
        )
        render = render_mixed_media_video(
            candidate.storyboard,
            hook_video_path=Path(record["video_path"]),
            asset_root=asset_root,
            output_dir=output_dir / "renders" / candidate_id,
            narration_audio_path=narration.audio_path,
        )
        rendered.append(
            {
                "candidate_id": candidate_id,
                "video_path": str(render.video_path),
                "subtitle_path": str(render.subtitle_path),
                "subtitle_safe_area_pass": all(
                    layout.safe_area_pass for layout in render.subtitle_layouts
                ),
                "narration_voice": narration.plan.settings.voice_name,
            }
        )
    snapshot = ledger.snapshot()
    summary = {
        "schema_version": "1.0",
        "operation_prefix": args.operation_prefix,
        "concept_count": len(batch.concepts),
        "candidate_count": len(pool["candidates"]),
        "eligible_candidate_ids": pool["eligible_candidate_ids"],
        "rendered_candidates": rendered,
        "automatic_selection": None,
        "budget": {
            "cap_microusd": snapshot.cap_microusd,
            "charged_microusd": snapshot.charged_microusd,
            "remaining_microusd": snapshot.remaining_microusd,
        },
    }
    _write_json(output_dir / "campaign-summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
