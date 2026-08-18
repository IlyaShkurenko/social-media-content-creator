from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from google import genai
from google.genai import _transformers, errors, types


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from app.services.creative.campaign import (  # noqa: E402
    CampaignBrief,
    HypothesisBatch,
    HypothesisBatchResponse,
    build_campaign_plan,
    execute_candidate_pool,
    plan_hypotheses,
)
from app.services.creative.narration import generate_scene_narration  # noqa: E402
from app.services.creative.renderer import render_mixed_media_video  # noqa: E402
from app.services.creative.runway import RunwayAdapter  # noqa: E402
from app.services.creative.storyboard import validate_storyboard  # noqa: E402
from evals.gemini_judge import (  # noqa: E402
    JUDGE_MODEL,
    actual_usage_cost_microusd,
    sanitize_judge_evidence,
    sha256_text,
)
from evals.temporal_judge import (  # noqa: E402
    GeminiTemporalJudge,
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
    runway_base_url = str(
        config.app.get("runway_base_url", RunwayAdapter.BASE_URL)
        or RunwayAdapter.BASE_URL
    ).strip()
    runway_api_version = str(
        config.app.get("runway_api_version", RunwayAdapter.API_VERSION)
        or RunwayAdapter.API_VERSION
    ).strip()
    return gemini_key, runway_key, runway_base_url, runway_api_version


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
        # Schema transformation is local. Keep it outside the provider-outcome
        # guard so an incompatible contract cannot be recorded as a paid call.
        _transformers.t_schema(
            getattr(self.client, "_api_client", None),
            HypothesisBatchResponse,
        )
        self.ledger.ensure_available(PLANNER_MAXIMUM_COST_MICROUSD)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=HypothesisBatchResponse,
                    max_output_tokens=8192,
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=False,
                        thinking_level="medium",
                    ),
                ),
            )
        except errors.APIError as exc:
            detail = str(getattr(exc, "message", "") or "").strip()[:500]
            raise RuntimeError(
                "Gemini rejected concept planning before returning a billable "
                f"result (HTTP {exc.code}); no charge was recorded"
                + (f": {detail}" if detail else "")
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
        planner = BudgetedGeminiConceptPlanner(
            api_key=gemini_key,
            ledger=ledger,
            operation_id=f"{args.operation_prefix}-gemini-concepts-v5",
            model=args.gemini_model,
        )
        raw = planner(build_hypothesis_prompt_for_record(brief))
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
    parser.add_argument("--execute-paid", action="store_true")
    parser.add_argument("--confirm-paid", default="")
    args = parser.parse_args()
    if args.execute_paid and args.confirm_paid != "YES":
        parser.error("paid execution requires --confirm-paid YES")
    if not args.execute_paid and not args.concepts:
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
    plan = build_campaign_plan(
        batch,
        template_storyboard=storyboard,
        operation_prefix=args.operation_prefix,
    )
    _write_json(output_dir / "campaign-plan.json", plan.model_dump(mode="json"))
    if not args.execute_paid:
        print(json.dumps(plan.model_dump(mode="json"), indent=2))
        return 0

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
        try:
            evidence = temporal.inspect(
                video_path=video_path,
                scene_id="hook",
                scene_description=description,
                start_seconds=0.0,
                end_seconds=5.0,
                working_dir=output_dir
                / "temporal"
                / f"{candidate.candidate_id}-strips",
                operation_id=(
                    f"{args.operation_prefix}-{candidate.candidate_id}-temporal"
                ),
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
            _write_json(evidence_path, failure)
            return {
                "temporal_consistency_pass": False,
                "temporal_events": [
                    {
                        "event_id": "screening-unavailable",
                        "severity": "high",
                        "reason": "Temporal screening did not return valid evidence.",
                    }
                ],
                "temporal_evidence": str(evidence_path),
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
