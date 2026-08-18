from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from google import genai
from google.genai import _transformers, errors, types
from pydantic import BaseModel, Field


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(LOOP_ROOT) not in sys.path:
    sys.path.insert(0, str(LOOP_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from evals.gemini_judge import (  # noqa: E402
    JUDGE_MODEL,
    MODEL_PRICING_USD_PER_MILLION_TOKENS,
    actual_usage_cost_microusd,
    estimate_judge_pass_cost_microusd,
    is_definite_nonbillable_gemini_error,
    sanitize_judge_evidence,
    sha256_file,
    sha256_text,
)


EVALUATOR_VERSION = "1.0.0"
EVIDENCE_SCHEMA_VERSION = 1
OBSERVATION_MODE = "gemini_invariant_pairwise_v1"
MAX_OUTPUT_TOKENS = 4096
ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_BUDGET_DATABASE = (
    LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
)

CRITERION_NAMES = (
    "transition_mechanics",
    "audiovisual_correctness",
    "product_brand_fidelity",
    "cta_clarity",
    "professional_finish",
)
STATUS_SCORES: dict[str, float | None] = {
    "met": 1.0,
    "partially_met": 0.5,
    "not_met": 0.0,
    "unverifiable": None,
}


class DownstreamEvidenceCitation(BaseModel):
    scene_purpose: Literal["product_demo", "cta"]
    timestamp_ms: int = Field(ge=0)
    observation: str = Field(min_length=1)


class InvariantAssessment(BaseModel):
    status: Literal["met", "partially_met", "not_met", "unverifiable"]
    evidence: list[DownstreamEvidenceCitation]
    reason: str = Field(min_length=1)


class PairedInvariantAssessment(BaseModel):
    video_a: InvariantAssessment
    video_b: InvariantAssessment


class InvariantCriteria(BaseModel):
    transition_mechanics: PairedInvariantAssessment
    audiovisual_correctness: PairedInvariantAssessment
    product_brand_fidelity: PairedInvariantAssessment
    cta_clarity: PairedInvariantAssessment
    professional_finish: PairedInvariantAssessment


class DownstreamSceneObservation(BaseModel):
    video_label: Literal["A", "B"]
    scene_purpose: Literal["product_demo", "cta"]
    timestamp_ms: int = Field(ge=0)
    observation: str = Field(min_length=1)


class InvariantJudgeResponse(BaseModel):
    winner: Literal["A", "B", "tie"]
    winner_reason: str = Field(min_length=1)
    criteria: InvariantCriteria
    scene_evidence: list[DownstreamSceneObservation]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _scene_purpose(scene: dict[str, Any]) -> str:
    value = scene.get("purpose") or scene.get("id") or scene.get("scene_id")
    return str(value or "").strip().lower()


def build_shared_invariant_contract(scenario: dict[str, Any]) -> dict[str, Any]:
    """Return only the downstream contract shared by unlike creative hooks."""

    if not isinstance(scenario, dict):
        raise ValueError("scenario must be a JSON object")
    expected = scenario.get("expected")
    if not isinstance(expected, dict):
        raise ValueError("scenario requires an 'expected' object")
    storyboard = expected.get("storyboard")
    if not isinstance(storyboard, list):
        raise ValueError("scenario requires an expected storyboard list")

    downstream_scenes: list[dict[str, Any]] = []
    for scene in storyboard:
        if not isinstance(scene, dict):
            raise ValueError("every storyboard scene must be an object")
        purpose = _scene_purpose(scene)
        if purpose not in {"product_demo", "cta"}:
            continue
        scene_contract = {
            key: scene.get(key)
            for key in (
                "id",
                "scene_id",
                "start_seconds",
                "end_seconds",
                "description",
                "visual_intent",
                "voiceover",
                "onscreen_text",
                "screen_content_policy",
                "expected_tags",
                "expected_evidence",
            )
            if key in scene
        }
        scene_contract["purpose"] = purpose
        downstream_scenes.append(scene_contract)

    purposes = [_scene_purpose(scene) for scene in downstream_scenes]
    if purposes.count("product_demo") != 1 or purposes.count("cta") != 1:
        raise ValueError(
            "shared invariant contract requires exactly one product_demo and one CTA"
        )

    return {
        "aspect_ratio": expected.get("aspect_ratio"),
        "duration_seconds": expected.get("duration_seconds"),
        "audio_required": expected.get("audio_required"),
        "brand_assets_required": expected.get("brand_assets_required"),
        "downstream_scenes": downstream_scenes,
    }


def build_invariant_judge_prompt(scenario: dict[str, Any]) -> str:
    """Build a shared-contract prompt that cannot score candidate hook semantics."""

    contract = build_shared_invariant_contract(scenario)
    return f"""
You are a strict evidence-based pairwise reviewer of the shared downstream
portion of two short-form advertisements. The first attached video is VIDEO A
and the second is VIDEO B. Evaluate only the declared product demonstration,
call-to-action, and downstream production contract below.

Opening-scene semantics are outside this evaluation. Do not assess any opening
hypothesis, setting, actor, prop, action, emotional premise, or creative idea.
Do not reward either video for matching an opening scene from another concept.
You may inspect the cut at the product_demo boundary only for its mechanical and
audiovisual execution; ignore the narrative content before that boundary.

Return only the requested structured response. Criterion statuses use this
closed vocabulary only: "met", "partially_met", "not_met", or "unverifiable".
Do not return numeric scores or confidence values. The caller maps statuses and
the order-balanced winner diagnostic deterministically. The winner is diagnostic only
and is not an acceptance decision. Choose "tie" whenever a downstream
difference is not supported by visible or audible evidence.

For both videos, assess these criteria independently:

1. transition_mechanics: mechanically clean cuts or transitions into the exact
   product demonstration and onward into the CTA, without judging the opening
   idea itself.
2. audiovisual_correctness: downstream narration, sound, subtitles, visible
   actions, and scene timing agree; render success or an audio stream alone is
   not proof.
3. product_brand_fidelity: approved product UI, logo, mascot, spelling, geometry,
   and composition remain exact and legible; merely using a source asset is not
   proof of its final visible fidelity.
4. cta_clarity: the CTA is singular, readable, unobstructed, and understandable;
   subtitle safe-area geometry alone is not proof.
5. professional_finish: the product_demo and CTA feel intentionally edited and
   production-ready, without downstream visual or audio defects.

Base every verdict on visible or audible evidence from the attached MP4s, not
on what the contract says should appear. Every non-unverifiable assessment must
include timestamped evidence from product_demo or CTA. An unverifiable
assessment must contain no evidence. Also produce exactly one scene_evidence
observation for each (video_label, scene_purpose) pair, at a timestamp inside
that scene. Observations describe what is actually present, including defects
or absence; never infer an approved asset merely because it is required.

SHARED DOWNSTREAM CONTRACT:
{json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)}
""".strip()


def map_invariant_statuses(
    response: InvariantJudgeResponse | dict[str, Any],
) -> dict[str, dict[str, float | None]]:
    """Map closed model verdicts to deterministic per-video values."""

    parsed = (
        response
        if isinstance(response, InvariantJudgeResponse)
        else InvariantJudgeResponse.model_validate(response)
    )
    result: dict[str, dict[str, float | None]] = {"video_a": {}, "video_b": {}}
    for criterion_name in CRITERION_NAMES:
        criterion = getattr(parsed.criteria, criterion_name)
        result["video_a"][criterion_name] = STATUS_SCORES[criterion.video_a.status]
        result["video_b"][criterion_name] = STATUS_SCORES[criterion.video_b.status]
    return result


def build_winner_diagnostic(
    passes: list[dict[str, Any]],
    *,
    baseline_sha256: str,
    candidate_sha256: str,
) -> dict[str, Any]:
    """Convert order-balanced label winners into an artifact-bound diagnostic."""

    if not passes:
        return {
            "diagnostic_only": True,
            "position_balanced": False,
            "candidate_credit": None,
            "pass_candidate_credits": [],
            "outcome": "incomplete",
        }

    credits: list[float] = []
    for item in passes:
        order = item.get("order", {})
        winner = item.get("response", {}).get("winner")
        if winner == "tie" or baseline_sha256 == candidate_sha256:
            credits.append(0.5)
        elif winner in {"A", "B"} and order.get(winner) == candidate_sha256:
            credits.append(1.0)
        elif winner in {"A", "B"} and order.get(winner) == baseline_sha256:
            credits.append(0.0)
        else:
            raise ValueError("invariant judge winner does not map to an input artifact")

    credit = sum(credits) / len(credits)
    if credit > 0.5:
        outcome = "candidate"
    elif credit < 0.5:
        outcome = "baseline"
    else:
        outcome = "tie"
    position_balanced = (
        len(passes) == 2
        and passes[0].get("order", {}).get("A")
        == passes[1].get("order", {}).get("B")
        and passes[0].get("order", {}).get("B")
        == passes[1].get("order", {}).get("A")
    )
    return {
        "diagnostic_only": True,
        "position_balanced": position_balanced,
        "candidate_credit": credit,
        "pass_candidate_credits": credits,
        "outcome": outcome,
    }


def validate_provider_response_schema(api_client: Any | None = None) -> None:
    """Fail before upload if the SDK cannot encode the response schema."""

    _transformers.t_schema(api_client, InvariantJudgeResponse)


def _usage_record(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }


def _state_name(uploaded_file: Any) -> str:
    state = getattr(uploaded_file, "state", None)
    return str(getattr(state, "name", state or "")).upper()


def _scene_ranges_ms(contract: dict[str, Any]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for scene in contract["downstream_scenes"]:
        purpose = _scene_purpose(scene)
        try:
            start_ms = round(float(scene["start_seconds"]) * 1000)
            end_ms = round(float(scene["end_seconds"]) * 1000)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"shared {purpose!r} scene requires numeric start/end times"
            ) from exc
        if start_ms < 0 or end_ms <= start_ms:
            raise ValueError(f"shared {purpose!r} scene has an invalid time range")
        result[purpose] = (start_ms, end_ms)
    return result


def _timestamp_is_in_scene(
    timestamp_ms: int,
    scene_range: tuple[int, int],
    *,
    tolerance_ms: int = 250,
) -> bool:
    return scene_range[0] - tolerance_ms <= timestamp_ms <= scene_range[1] + tolerance_ms


def _validate_response(
    response: InvariantJudgeResponse,
    *,
    contract: dict[str, Any],
    video_duration_seconds_by_label: dict[str, float],
) -> None:
    if set(video_duration_seconds_by_label) != {"A", "B"} or any(
        duration <= 0 for duration in video_duration_seconds_by_label.values()
    ):
        raise ValueError("positive video durations are required for A and B")
    scene_ranges = _scene_ranges_ms(contract)

    observed_pairs: list[tuple[str, str]] = []
    for observation in response.scene_evidence:
        pair = (observation.video_label, observation.scene_purpose)
        observed_pairs.append(pair)
        video_end_ms = (
            round(video_duration_seconds_by_label[observation.video_label] * 1000)
            + 250
        )
        if (
            observation.timestamp_ms > video_end_ms
            or not _timestamp_is_in_scene(
                observation.timestamp_ms,
                scene_ranges[observation.scene_purpose],
            )
        ):
            raise ValueError(
                "invariant judge scene evidence cites a timestamp outside its scene"
            )
    expected_pairs = {
        (video_label, scene_purpose)
        for video_label in ("A", "B")
        for scene_purpose in ("product_demo", "cta")
    }
    if len(observed_pairs) != 4 or set(observed_pairs) != expected_pairs:
        raise ValueError(
            "invariant judge requires one product_demo and CTA observation per video"
        )

    for criterion_name in CRITERION_NAMES:
        criterion = getattr(response.criteria, criterion_name)
        for label, assessment in (
            ("A", criterion.video_a),
            ("B", criterion.video_b),
        ):
            if assessment.status == "unverifiable":
                if assessment.evidence:
                    raise ValueError(
                        f"{criterion_name} for video {label} is unverifiable but cites evidence"
                    )
                continue
            if not assessment.evidence:
                raise ValueError(
                    f"{criterion_name} for video {label} requires timestamped evidence"
                )
            video_end_ms = (
                round(video_duration_seconds_by_label[label] * 1000) + 250
            )
            for citation in assessment.evidence:
                if (
                    citation.timestamp_ms > video_end_ms
                    or not _timestamp_is_in_scene(
                        citation.timestamp_ms,
                        scene_ranges[citation.scene_purpose],
                    )
                ):
                    raise ValueError(
                        f"{criterion_name} for video {label} cites a timestamp outside its scene"
                    )


def _expected_passes(
    *,
    baseline_sha256: str,
    candidate_sha256: str,
    baseline_duration_seconds: float,
    candidate_duration_seconds: float,
    operation_prefix: str,
) -> tuple[dict[str, Any], ...]:
    return (
        {
            "pass_id": "baseline-a",
            "order": {"A": baseline_sha256, "B": candidate_sha256},
            "durations": {
                "A": baseline_duration_seconds,
                "B": candidate_duration_seconds,
            },
            "operation_id": f"{operation_prefix}-pass-1",
        },
        {
            "pass_id": "candidate-a",
            "order": {"A": candidate_sha256, "B": baseline_sha256},
            "durations": {
                "A": candidate_duration_seconds,
                "B": baseline_duration_seconds,
            },
            "operation_id": f"{operation_prefix}-pass-2",
        },
    )


def _validate_existing_evidence(
    evidence: dict[str, Any],
    *,
    expected_bindings: dict[str, Any],
    expected_passes: tuple[dict[str, Any], ...],
    contract: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate a checkpoint completely before it can suppress paid work."""

    if not isinstance(evidence, dict):
        raise ValueError("existing invariant judge evidence must be a JSON object")
    status = evidence.get("status")
    if status not in {"partial", "complete"}:
        raise ValueError("existing invariant judge evidence has an invalid status")
    for key, expected_value in expected_bindings.items():
        if evidence.get(key) != expected_value:
            raise ValueError(
                f"existing invariant judge evidence has a mismatched {key} binding"
            )

    raw_passes = evidence.get("passes")
    if not isinstance(raw_passes, list) or len(raw_passes) > len(expected_passes):
        raise ValueError("existing invariant judge evidence has invalid passes")
    if status == "complete" and len(raw_passes) != len(expected_passes):
        raise ValueError("complete invariant judge evidence requires both passes")

    validated_passes: list[dict[str, Any]] = []
    for index, raw_pass in enumerate(raw_passes):
        if not isinstance(raw_pass, dict):
            raise ValueError("existing invariant judge pass must be an object")
        expected_pass = expected_passes[index]
        if raw_pass.get("pass_id") != expected_pass["pass_id"]:
            raise ValueError(
                "existing invariant judge passes are stale, duplicated, or out of order"
            )
        if raw_pass.get("order") != expected_pass["order"]:
            raise ValueError("existing invariant judge pass has a mismatched A/B order")
        budget = raw_pass.get("budget")
        if (
            not isinstance(budget, dict)
            or budget.get("operation_id") != expected_pass["operation_id"]
            or not isinstance(budget.get("charged_microusd"), int)
            or budget["charged_microusd"] <= 0
        ):
            raise ValueError(
                "existing invariant judge pass has an invalid paid-operation binding"
            )
        provider = raw_pass.get("provider")
        if (
            not isinstance(provider, dict)
            or provider.get("requested_model")
            != expected_bindings["requested_model"]
            or not str(provider.get("model_version", "")).strip()
        ):
            raise ValueError(
                "existing invariant judge pass has an invalid provider binding"
            )
        parsed_response = InvariantJudgeResponse.model_validate(raw_pass.get("response"))
        _validate_response(
            parsed_response,
            contract=contract,
            video_duration_seconds_by_label=expected_pass["durations"],
        )
        expected_scores = map_invariant_statuses(parsed_response)
        if raw_pass.get("criterion_scores") != expected_scores:
            raise ValueError(
                "existing invariant judge pass has stale deterministic criterion scores"
            )
        validated_passes.append(raw_pass)

    expected_model_versions = sorted(
        {item["provider"]["model_version"] for item in validated_passes}
    )
    if evidence.get("model_versions") != expected_model_versions:
        raise ValueError(
            "existing invariant judge evidence has stale model-version bindings"
        )
    expected_diagnostic = build_winner_diagnostic(
        validated_passes,
        baseline_sha256=expected_bindings["baseline_sha256"],
        candidate_sha256=expected_bindings["candidate_sha256"],
    )
    if evidence.get("winner_diagnostic") != expected_diagnostic:
        raise ValueError(
            "existing invariant judge evidence has a stale winner diagnostic"
        )
    return validated_passes


class GeminiInvariantJudge:
    def __init__(
        self,
        *,
        api_key: str,
        budget_ledger: IterationBudgetLedger,
        client: Any | None = None,
        model: str = JUDGE_MODEL,
        sleep: Any = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key is required")
        if model not in MODEL_PRICING_USD_PER_MILLION_TOKENS:
            raise ValueError(f"no fail-closed pricing is configured for model {model!r}")
        self.client = client or genai.Client(api_key=api_key)
        self.budget_ledger = budget_ledger
        self.model = model
        self.sleep = sleep

    def _upload_and_wait(
        self,
        path: Path,
        *,
        timeout_seconds: float = 180,
    ) -> Any:
        uploaded = self.client.files.upload(
            file=str(path),
            config={"mime_type": "video/mp4"},
        )
        deadline = time.monotonic() + timeout_seconds
        while _state_name(uploaded) not in {"ACTIVE", "FAILED"}:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Gemini file processing did not finish before timeout"
                )
            self.sleep(2)
            uploaded = self.client.files.get(name=uploaded.name)
        if _state_name(uploaded) != "ACTIVE":
            raise RuntimeError("Gemini rejected a video during file processing")
        return uploaded

    def _judge_pass(
        self,
        *,
        pass_id: str,
        video_a: Any,
        video_b: Any,
        order: dict[str, str],
        prompt: str,
        contract: dict[str, Any],
        total_video_duration_seconds: float,
        video_duration_seconds_by_label: dict[str, float],
        operation_id: str,
    ) -> dict[str, Any]:
        maximum_cost_microusd = estimate_judge_pass_cost_microusd(
            model=self.model,
            video_duration_seconds=total_video_duration_seconds,
            prompt_characters=len(prompt),
        )
        self.budget_ledger.ensure_available(maximum_cost_microusd)
        description = (
            f"Gemini {self.model} invariant evaluator {EVALUATOR_VERSION} {pass_id}"
        )
        parts = [
            types.Part.from_uri(
                file_uri=video_a.uri,
                mime_type=video_a.mime_type or "video/mp4",
                media_resolution="MEDIA_RESOLUTION_HIGH",
            ),
            types.Part.from_uri(
                file_uri=video_b.uri,
                mime_type=video_b.mime_type or "video/mp4",
                media_resolution="MEDIA_RESOLUTION_HIGH",
            ),
            types.Part.from_text(text=prompt),
        ]
        try:
            provider_response = self.client.models.generate_content(
                model=self.model,
                contents=types.Content(role="user", parts=parts),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=InvariantJudgeResponse,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=False,
                        thinking_level="medium",
                    ),
                ),
            )
        except errors.APIError as exc:
            if is_definite_nonbillable_gemini_error(exc):
                raise RuntimeError(
                    "Gemini rejected the invariant judge request before returning a "
                    f"billable result (HTTP {exc.code}); no charge was recorded"
                ) from exc
            self.budget_ledger.record_manual_charge(
                operation_id,
                maximum_cost_microusd,
                f"{description}; ambiguous HTTP {exc.code}, worst-case charge",
            )
            raise RuntimeError(
                "Gemini invariant judge submission outcome is ambiguous; a worst-case "
                f"charge was recorded under operation {operation_id!r} and was not retried"
            ) from exc
        except Exception as exc:
            self.budget_ledger.record_manual_charge(
                operation_id,
                maximum_cost_microusd,
                f"{description}; unknown provider outcome, worst-case charge",
            )
            raise RuntimeError(
                "Gemini invariant judge submission outcome is unknown; a worst-case "
                f"charge was recorded under operation {operation_id!r} and was not retried"
            ) from exc

        response_id = str(
            getattr(provider_response, "response_id", "")
            or f"gemini-{sha256_text(operation_id)[:24]}"
        )
        usage = _usage_record(provider_response)
        actual_cost = actual_usage_cost_microusd(
            model=self.model,
            prompt_tokens=usage["prompt_tokens"],
            output_tokens=usage["output_tokens"],
        )
        charged_microusd = actual_cost or maximum_cost_microusd
        self.budget_ledger.record_manual_charge(
            operation_id,
            charged_microusd,
            f"{description}; provider response {response_id}",
        )

        parsed = getattr(provider_response, "parsed", None)
        if parsed is None:
            parsed = json.loads(provider_response.text)
        parsed_response = InvariantJudgeResponse.model_validate(parsed)
        _validate_response(
            parsed_response,
            contract=contract,
            video_duration_seconds_by_label=video_duration_seconds_by_label,
        )
        snapshot = self.budget_ledger.snapshot()
        return sanitize_judge_evidence(
            {
                "pass_id": pass_id,
                "order": order,
                "response": parsed_response.model_dump(mode="json"),
                "criterion_scores": map_invariant_statuses(parsed_response),
                "provider": {
                    "requested_model": self.model,
                    "model_version": str(
                        getattr(provider_response, "model_version", "") or self.model
                    ),
                    "response_id": response_id,
                    "usage": usage,
                    "estimated_actual_cost_microusd": actual_cost,
                },
                "budget": {
                    "operation_id": operation_id,
                    "preflight_maximum_microusd": maximum_cost_microusd,
                    "charged_microusd": charged_microusd,
                    "remaining_scope_microusd": snapshot.remaining_microusd,
                },
            }
        )

    def compare(
        self,
        *,
        baseline_video: Path,
        candidate_video: Path,
        baseline_duration_seconds: float,
        candidate_duration_seconds: float,
        scenario: dict[str, Any],
        scenario_sha256: str,
        operation_prefix: str,
        checkpoint: Callable[[dict[str, Any]], None] | None = None,
        existing_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not operation_prefix.strip():
            raise ValueError("invariant judge operation_prefix is required")
        if not baseline_video.is_file() or not candidate_video.is_file():
            raise ValueError("both invariant judge videos must exist")
        if baseline_duration_seconds <= 0 or candidate_duration_seconds <= 0:
            raise ValueError("both invariant judge video durations must be positive")

        contract = build_shared_invariant_contract(scenario)
        prompt = build_invariant_judge_prompt(scenario)
        validate_provider_response_schema(getattr(self.client, "_api_client", None))
        shared_contract_sha256 = sha256_json(contract)
        baseline_sha256 = sha256_file(baseline_video)
        candidate_sha256 = sha256_file(candidate_video)
        response_schema_sha256 = sha256_json(
            InvariantJudgeResponse.model_json_schema()
        )
        expected_bindings = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "observation_mode": OBSERVATION_MODE,
            "scenario_id": scenario.get("id"),
            "scenario_sha256": scenario_sha256,
            "shared_contract_sha256": shared_contract_sha256,
            "prompt_sha256": sha256_text(prompt),
            "response_schema_sha256": response_schema_sha256,
            "requested_model": self.model,
            "baseline_sha256": baseline_sha256,
            "candidate_sha256": candidate_sha256,
            "self_comparison": baseline_sha256 == candidate_sha256,
        }
        expected_passes = _expected_passes(
            baseline_sha256=baseline_sha256,
            candidate_sha256=candidate_sha256,
            baseline_duration_seconds=baseline_duration_seconds,
            candidate_duration_seconds=candidate_duration_seconds,
            operation_prefix=operation_prefix,
        )
        uploaded_by_sha: dict[str, Any] = {}
        uploaded_names: list[str] = []
        passes: list[dict[str, Any]] = []

        if existing_evidence is not None:
            passes = _validate_existing_evidence(
                existing_evidence,
                expected_bindings=expected_bindings,
                expected_passes=expected_passes,
                contract=contract,
            )
            ledger_operations = {
                item.operation_id: item
                for item in self.budget_ledger.list_operations()
            }
            for item in passes:
                operation_id = item["budget"]["operation_id"]
                charged_microusd = item["budget"]["charged_microusd"]
                operation = ledger_operations.get(operation_id)
                if (
                    operation is None
                    or operation.status not in {"manual_charge", "submitted"}
                    or operation.amount_microusd != charged_microusd
                ):
                    raise RuntimeError(
                        "existing invariant checkpoint does not match the paid ledger; "
                        "refusing to resume"
                    )
            if existing_evidence["status"] == "complete":
                return sanitize_judge_evidence(existing_evidence)

        def build_evidence(*, status: Literal["partial", "complete"]) -> dict[str, Any]:
            model_versions = sorted(
                {item["provider"]["model_version"] for item in passes}
            )
            return sanitize_judge_evidence(
                {
                    **expected_bindings,
                    "status": status,
                    "model_versions": model_versions,
                    "winner_diagnostic": build_winner_diagnostic(
                        passes,
                        baseline_sha256=baseline_sha256,
                        candidate_sha256=candidate_sha256,
                    ),
                    "passes": passes,
                }
            )

        if len(passes) == len(expected_passes):
            evidence = build_evidence(status="complete")
            if checkpoint is not None:
                checkpoint(evidence)
            return evidence

        ledger_operations = {
            item.operation_id: item for item in self.budget_ledger.list_operations()
        }
        for expected_pass in expected_passes[len(passes) :]:
            if expected_pass["operation_id"] in ledger_operations:
                raise RuntimeError(
                    "a missing invariant judge pass already has a ledger operation; "
                    "its provider outcome may be ambiguous, so it will not be retried"
                )

        try:
            for digest, path in (
                (baseline_sha256, baseline_video),
                (candidate_sha256, candidate_video),
            ):
                if digest in uploaded_by_sha:
                    continue
                uploaded = self._upload_and_wait(path)
                uploaded_by_sha[digest] = uploaded
                uploaded_names.append(uploaded.name)

            for expected_pass in expected_passes[len(passes) :]:
                order = expected_pass["order"]
                passes.append(
                    self._judge_pass(
                        pass_id=expected_pass["pass_id"],
                        video_a=uploaded_by_sha[order["A"]],
                        video_b=uploaded_by_sha[order["B"]],
                        order=order,
                        prompt=prompt,
                        contract=contract,
                        total_video_duration_seconds=(
                            baseline_duration_seconds + candidate_duration_seconds
                        ),
                        video_duration_seconds_by_label=expected_pass["durations"],
                        operation_id=expected_pass["operation_id"],
                    )
                )
                if checkpoint is not None:
                    checkpoint(build_evidence(status="partial"))
        finally:
            for name in uploaded_names:
                try:
                    self.client.files.delete(name=name)
                except Exception:
                    pass

        evidence = build_evidence(status="complete")
        if checkpoint is not None:
            checkpoint(evidence)
        return evidence


def _resolve_managed_file(value: str, *, root: Path, label: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve()
    if not resolved.is_file() or root.resolve() not in resolved.parents:
        raise ValueError(f"{label} must be a file inside {root}: {resolved}")
    return resolved


def _resolve_output(value: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else LOOP_ROOT / raw
    resolved = candidate.resolve()
    if LOOP_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"output must stay inside {LOOP_ROOT}: {resolved}")
    return resolved


def _resolve_budget_database(value: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else LOOP_ROOT / raw
    resolved = candidate.resolve()
    if LOOP_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"budget database must stay inside {LOOP_ROOT}: {resolved}")
    return resolved


def _video_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise ValueError("video duration must be positive")
    return duration


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paid, invariant-only Gemini pairwise judge"
    )
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--baseline-video", required=True)
    parser.add_argument("--candidate-video", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--operation-prefix", required=True)
    parser.add_argument("--model", default=JUDGE_MODEL)
    parser.add_argument("--budget-database", default=str(DEFAULT_BUDGET_DATABASE))
    parser.add_argument("--confirm-paid", choices=("YES",), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenario_path = _resolve_managed_file(
        args.scenario,
        root=LOOP_ROOT,
        label="scenario",
    )
    baseline_video = _resolve_managed_file(
        args.baseline_video,
        root=REPO_ROOT,
        label="baseline video",
    )
    candidate_video = _resolve_managed_file(
        args.candidate_video,
        root=REPO_ROOT,
        label="candidate video",
    )
    output = _resolve_output(args.output)
    budget_database = _resolve_budget_database(args.budget_database)
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    existing_evidence: dict[str, Any] | None = None
    if output.is_file():
        try:
            loaded_evidence = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "existing invariant judge output is not valid JSON; refusing to overwrite it"
            ) from exc
        if not isinstance(loaded_evidence, dict):
            raise ValueError(
                "existing invariant judge output must contain a JSON object"
            )
        existing_evidence = loaded_evidence

    from app.config import config

    api_key = str(config.app.get("gemini_api_key", "") or "").strip()
    api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")
    ledger = IterationBudgetLedger(
        budget_database,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    )
    judge = GeminiInvariantJudge(
        api_key=api_key,
        budget_ledger=ledger,
        model=args.model,
    )
    evidence = judge.compare(
        baseline_video=baseline_video,
        candidate_video=candidate_video,
        baseline_duration_seconds=_video_duration(baseline_video),
        candidate_duration_seconds=_video_duration(candidate_video),
        scenario=scenario,
        scenario_sha256=sha256_file(scenario_path),
        operation_prefix=args.operation_prefix,
        checkpoint=lambda payload: _write_json(output, payload),
        existing_evidence=existing_evidence,
    )
    _write_json(output, evidence)
    total_charged = sum(
        item["budget"]["charged_microusd"] for item in evidence["passes"]
    )
    print(
        json.dumps(
            {
                "output": str(output.relative_to(LOOP_ROOT)),
                "model": evidence["requested_model"],
                "passes": len(evidence["passes"]),
                "winner_diagnostic": evidence["winner_diagnostic"],
                "charged_microusd": total_charged,
                "remaining_microusd": evidence["passes"][-1]["budget"][
                    "remaining_scope_microusd"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
