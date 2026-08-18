from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
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
    actual_usage_cost_microusd,
    estimate_judge_pass_cost_microusd,
    is_definite_nonbillable_gemini_error,
    sanitize_judge_evidence,
    sha256_file,
    sha256_text,
)


EVALUATOR_VERSION = "1.2.0"
EVIDENCE_SCHEMA_VERSION = 1
OBSERVATION_MODE = "gemini_candidate_contract_v1"
MAX_OUTPUT_TOKENS = 4096
FIRST_TWO_SECONDS_END_MS = 2_000
BRIDGE_EVIDENCE_WINDOW_MS = 1_000
ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_BUDGET_DATABASE = (
    LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
)

DIMENSION_NAMES = (
    "hypothesis_match",
    "target_emotion_strength",
    "first_two_seconds_hook_clarity",
    "hook_to_product_bridge_coherence",
    "storyboard_action_alignment",
)
STATUS_SCORES: dict[str, float | None] = {
    "met": 1.0,
    "partially_met": 0.5,
    "not_met": 0.0,
    "unverifiable": None,
}


class EvidenceCitation(BaseModel):
    timestamp_ms: int = Field(ge=0)
    observation: str = Field(min_length=1)


class DimensionAssessment(BaseModel):
    status: Literal["met", "partially_met", "not_met", "unverifiable"]
    evidence: list[EvidenceCitation]
    reason: str = Field(min_length=1)


class CandidateJudgeResponse(BaseModel):
    observed_hook_summary: str = Field(min_length=1)
    observed_bridge_summary: str = Field(min_length=1)
    hypothesis_match: DimensionAssessment
    target_emotion_strength: DimensionAssessment
    first_two_seconds_hook_clarity: DimensionAssessment
    hook_to_product_bridge_coherence: DimensionAssessment
    storyboard_action_alignment: DimensionAssessment


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(payload: dict[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} requires non-empty {key!r}")
    return value.strip()


def _candidate_contract(
    concept: dict[str, Any],
    storyboard: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(concept, dict) or not isinstance(storyboard, dict):
        raise ValueError("concept and storyboard must be JSON objects")

    concept_contract = {
        key: concept.get(key)
        for key in (
            "concept_id",
            "hypothesis",
            "audience_problem",
            "target_emotion",
            "emotional_arc",
            "hook_setting",
            "hook_camera",
            "hook_voiceover",
            "product_bridge",
            "quality_criteria",
        )
    }
    for key in (
        "concept_id",
        "hypothesis",
        "audience_problem",
        "target_emotion",
        "emotional_arc",
        "hook_setting",
        "hook_camera",
        "hook_voiceover",
        "product_bridge",
    ):
        _required_text(concept_contract, key, label="concept")
    hook_beats = concept.get("hook_beats")
    quality_criteria = concept_contract["quality_criteria"]
    if not isinstance(hook_beats, list) or not hook_beats:
        raise ValueError("concept requires non-empty 'hook_beats'")
    if not isinstance(quality_criteria, list) or not quality_criteria:
        raise ValueError("concept requires non-empty 'quality_criteria'")

    scenes = storyboard.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("storyboard requires non-empty 'scenes'")
    relevant_scenes: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            raise ValueError("every storyboard scene must be an object")
        if scene.get("purpose") not in {"hook", "product_demo"}:
            continue
        relevant_scenes.append(
            {
                key: scene.get(key)
                for key in (
                    "scene_id",
                    "start_seconds",
                    "end_seconds",
                    "purpose",
                    "visual_intent",
                    "voiceover",
                    "onscreen_text",
                    "expected_evidence",
                )
            }
        )
    purposes = [scene.get("purpose") for scene in relevant_scenes]
    if purposes.count("hook") != 1 or purposes.count("product_demo") != 1:
        raise ValueError(
            "storyboard requires exactly one hook and one product_demo scene"
        )

    storyboard_contract = {
        "storyboard_id": storyboard.get("storyboard_id"),
        "content_language": storyboard.get("content_language"),
        "aspect_ratio": storyboard.get("aspect_ratio"),
        "target_duration_seconds": storyboard.get("target_duration_seconds"),
        "hypothesis": storyboard.get("hypothesis"),
        "scenes": relevant_scenes,
    }
    _required_text(storyboard_contract, "storyboard_id", label="storyboard")
    return {
        "concept": concept_contract,
        "storyboard": storyboard_contract,
    }


def _scene_range_ms(
    contract: dict[str, Any],
    *,
    purpose: Literal["hook", "product_demo"],
) -> tuple[int, int]:
    scenes = contract.get("storyboard", {}).get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("candidate contract has no storyboard scenes")
    matches = [scene for scene in scenes if scene.get("purpose") == purpose]
    if len(matches) != 1:
        raise ValueError(f"candidate contract requires exactly one {purpose} scene")
    scene = matches[0]
    start = scene.get("start_seconds")
    end = scene.get("end_seconds")
    if (
        not isinstance(start, (int, float))
        or isinstance(start, bool)
        or not isinstance(end, (int, float))
        or isinstance(end, bool)
    ):
        raise ValueError(f"candidate {purpose} scene requires numeric start/end times")
    start_ms = round(float(start) * 1_000)
    end_ms = round(float(end) * 1_000)
    if start_ms < 0 or end_ms <= start_ms:
        raise ValueError(f"candidate {purpose} scene has an invalid time range")
    return start_ms, end_ms


def _evidence_time_domains(
    contract: dict[str, Any],
) -> dict[str, tuple[int, int]]:
    hook_start_ms, hook_end_ms = _scene_range_ms(contract, purpose="hook")
    product_start_ms, product_end_ms = _scene_range_ms(
        contract,
        purpose="product_demo",
    )
    if hook_start_ms != 0:
        raise ValueError("candidate hook must begin at 0 ms")
    if hook_end_ms != product_start_ms:
        raise ValueError(
            "candidate hook and product_demo scenes must share one transition boundary"
        )
    bridge_start_ms = max(
        hook_start_ms,
        hook_end_ms - BRIDGE_EVIDENCE_WINDOW_MS,
    )
    bridge_end_ms = min(
        product_end_ms,
        product_start_ms + BRIDGE_EVIDENCE_WINDOW_MS,
    )
    return {
        "hypothesis_match": (hook_start_ms, hook_end_ms),
        "target_emotion_strength": (hook_start_ms, hook_end_ms),
        "first_two_seconds_hook_clarity": (
            hook_start_ms,
            min(hook_end_ms, FIRST_TWO_SECONDS_END_MS),
        ),
        "hook_to_product_bridge_coherence": (
            bridge_start_ms,
            bridge_end_ms,
        ),
        "storyboard_action_alignment": (hook_start_ms, hook_end_ms),
    }


def build_candidate_judge_prompt(
    concept: dict[str, Any],
    storyboard: dict[str, Any],
) -> str:
    """Build an own-contract prompt for one candidate, never a ranking prompt."""

    contract = _candidate_contract(concept, storyboard)
    evidence_time_domains = _evidence_time_domains(contract)
    return f"""
You are a strict evidence-based reviewer of one short-form advertisement.
Evaluate the attached candidate only against its own concept and compiled
storyboard contract below. There is no baseline and no competing concept. Do not
rank this creative against another idea, and do not penalize it for differing
from any scenario absent from its own contract.

Return only the requested structured response. The five dimension statuses use
this closed vocabulary only: "met", "partially_met", "not_met", or
"unverifiable". Do not return numeric scores or confidence values. The caller
maps statuses to scores deterministically.

Base every verdict on visible or audible evidence from the attached MP4, not on
what the contract says should appear. Cite evidence with integer source
timestamps in milliseconds. A status other than "unverifiable" must cite at
least one observation. Use "unverifiable" only when the video genuinely cannot
support a verdict and leave its evidence list empty.

Every citation must stay inside its deterministic evidence window below. Hook
semantic dimensions may cite only the compiled hook scene. First-two-second
clarity may cite only 0-2000 ms. Bridge coherence may cite only the explicit
1000 ms window on either side of the shared hook/product boundary.

EVIDENCE WINDOWS (inclusive, milliseconds):
{json.dumps(evidence_time_domains, ensure_ascii=False, sort_keys=True, indent=2)}

The compiled storyboard is the only authority for executable visible actions,
expected evidence, timing, and device-screen policy. The concept supplies the
advertising hypothesis, audience problem, emotion, and creative rationale. Do
not invent or recover action requirements from superseded concept-generation
metadata. If planning metadata and the compiled storyboard differ, follow the
compiled storyboard and do not penalize the candidate for the removed detail.

Assess these dimensions independently:

1. hypothesis_match: does the observed hook communicate the candidate's stated
   audience problem and advertising hypothesis?
2. target_emotion_strength: is the declared target emotion visibly legible and
   consistent with the declared emotional arc?
3. first_two_seconds_hook_clarity: can a viewer understand the opening problem
   from the visible action during 0-2000 ms without relying on narration?
4. hook_to_product_bridge_coherence: does the observed end of the hook connect
   coherently into the exact product demonstration beginning at its declared
   storyboard time? Judge semantic continuity, not similarity to another hook.
5. storyboard_action_alignment: do the observed hook actions and their timing
   materially follow the compiled storyboard hook's visual_intent and
   expected_evidence? Compare only against that compiled hook scene, never
   against raw or superseded concept beats.

Do not treat generated or generic UI as approved product UI. Do not infer an
object, emotion, action, or transition merely because it is named in the
contract. Summaries must describe what is actually observed.

CANDIDATE CONTRACT:
{json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)}
""".strip()


def map_dimension_statuses(
    response: CandidateJudgeResponse | dict[str, Any],
) -> dict[str, float | None]:
    """Map closed model verdicts to deterministic scorecard values."""

    parsed = (
        response
        if isinstance(response, CandidateJudgeResponse)
        else CandidateJudgeResponse.model_validate(response)
    )
    return {
        name: STATUS_SCORES[getattr(parsed, name).status]
        for name in DIMENSION_NAMES
    }


def validate_provider_response_schema(api_client: Any | None = None) -> None:
    """Fail locally if the SDK cannot encode the structured response schema."""

    _transformers.t_schema(api_client, CandidateJudgeResponse)


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
        raise ValueError("candidate video duration must be positive")
    return duration


def _validate_response(
    response: CandidateJudgeResponse,
    *,
    video_duration_seconds: float,
    contract: dict[str, Any],
) -> None:
    maximum_timestamp_ms = round(video_duration_seconds * 1000) + 250
    evidence_time_domains = _evidence_time_domains(contract)
    for name in DIMENSION_NAMES:
        assessment = getattr(response, name)
        if assessment.status == "unverifiable":
            if assessment.evidence:
                raise ValueError(
                    f"candidate judge dimension {name!r} is unverifiable but cites evidence"
                )
            continue
        if not assessment.evidence:
            raise ValueError(
                f"candidate judge dimension {name!r} requires timestamped evidence"
            )
        if any(
            citation.timestamp_ms > maximum_timestamp_ms
            for citation in assessment.evidence
        ):
            raise ValueError(
                f"candidate judge dimension {name!r} cites a timestamp outside the video"
            )
        start_ms, end_ms = evidence_time_domains[name]
        if any(
            not start_ms <= citation.timestamp_ms <= end_ms
            for citation in assessment.evidence
        ):
            if name == "first_two_seconds_hook_clarity":
                domain_label = "the 0-2000 ms first-two-seconds window"
            elif name == "hook_to_product_bridge_coherence":
                domain_label = (
                    f"the {start_ms}-{end_ms} ms hook/product transition window"
                )
            else:
                domain_label = (
                    f"the {start_ms}-{end_ms} ms compiled hook range"
                )
            raise ValueError(
                f"candidate judge dimension {name!r} cites evidence outside "
                f"{domain_label}"
            )


def _api_error_code(exc: errors.APIError) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, bool):
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _operation_by_id(
    ledger: IterationBudgetLedger,
    operation_id: str,
) -> Any | None:
    return next(
        (
            operation
            for operation in ledger.list_operations()
            if operation.operation_id == operation_id
        ),
        None,
    )


def _require_unused_operation(
    ledger: IterationBudgetLedger,
    operation_id: str,
) -> None:
    existing = _operation_by_id(ledger, operation_id)
    if existing is not None:
        raise RuntimeError(
            f"candidate judge operation {operation_id!r} is already "
            f"{existing.status}; matching complete output is required and the "
            "paid request will not be retried"
        )


def _validate_existing_evidence(
    evidence: dict[str, Any],
    *,
    concept: dict[str, Any],
    storyboard: dict[str, Any],
    video_path: Path,
    video_duration_seconds: float,
    operation_id: str,
    model: str,
    ledger: IterationBudgetLedger,
) -> dict[str, Any]:
    """Validate a complete artifact-bound checkpoint before suppressing paid work."""

    contract = _candidate_contract(concept, storyboard)
    prompt = build_candidate_judge_prompt(concept, storyboard)
    maximum_cost_microusd = estimate_judge_pass_cost_microusd(
        model=model,
        video_duration_seconds=video_duration_seconds,
        prompt_characters=len(prompt),
    )
    expected = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "observation_mode": OBSERVATION_MODE,
        "status": "complete",
        "concept_id": contract["concept"]["concept_id"],
        "concept_sha256": sha256_json(concept),
        "storyboard_id": contract["storyboard"]["storyboard_id"],
        "storyboard_sha256": sha256_json(storyboard),
        "contract_sha256": sha256_json(contract),
        "video_sha256": sha256_file(video_path),
        "video_duration_ms": round(video_duration_seconds * 1000),
        "prompt_sha256": sha256_text(prompt),
        "response_schema_sha256": sha256_json(
            CandidateJudgeResponse.model_json_schema()
        ),
        "requested_model": model,
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise ValueError(
                f"existing candidate judge output has a mismatched {key} binding"
            )

    response = CandidateJudgeResponse.model_validate(evidence.get("response"))
    _validate_response(
        response,
        video_duration_seconds=video_duration_seconds,
        contract=contract,
    )
    scores = map_dimension_statuses(response)
    if evidence.get("dimension_scores") != scores:
        raise ValueError(
            "existing candidate judge output has stale deterministic scores"
        )
    budget = evidence.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("existing candidate judge output has no budget binding")
    charged = budget.get("charged_microusd")
    if (
        budget.get("operation_id") != operation_id
        or budget.get("preflight_maximum_microusd") != maximum_cost_microusd
        or not isinstance(charged, int)
        or isinstance(charged, bool)
        or charged <= 0
        or charged > maximum_cost_microusd
    ):
        raise ValueError(
            "existing candidate judge output has an invalid paid-operation binding"
        )
    operation = _operation_by_id(ledger, operation_id)
    if (
        operation is None
        or operation.status != "manual_charge"
        or operation.amount_microusd != charged
    ):
        raise ValueError(
            "existing candidate judge output does not match the paid ledger"
        )
    return sanitize_judge_evidence(evidence)


class GeminiCandidateJudge:
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
            raise RuntimeError("Gemini rejected the candidate video during processing")
        return uploaded

    def inspect(
        self,
        *,
        video_path: Path,
        concept: dict[str, Any],
        storyboard: dict[str, Any],
        operation_id: str,
        video_duration_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not operation_id.strip():
            raise ValueError("candidate judge operation_id is required")
        if not video_path.is_file():
            raise ValueError(f"candidate video does not exist: {video_path}")
        duration = video_duration_seconds or _video_duration(video_path)
        if duration <= 0:
            raise ValueError("candidate video duration must be positive")
        _require_unused_operation(self.budget_ledger, operation_id)

        contract = _candidate_contract(concept, storyboard)
        prompt = build_candidate_judge_prompt(concept, storyboard)
        validate_provider_response_schema(getattr(self.client, "_api_client", None))
        maximum_cost_microusd = estimate_judge_pass_cost_microusd(
            model=self.model,
            video_duration_seconds=duration,
            prompt_characters=len(prompt),
        )
        self.budget_ledger.ensure_available(maximum_cost_microusd)
        description = (
            f"Gemini {self.model} candidate evaluator {EVALUATOR_VERSION}"
        )

        def raise_ambiguous(exc: Exception, *, http_code: int | None = None) -> None:
            outcome = (
                f"ambiguous HTTP {http_code}"
                if http_code is not None
                else "unknown provider outcome"
            )
            self.budget_ledger.record_manual_charge(
                operation_id,
                maximum_cost_microusd,
                f"{description}; {outcome}, worst-case charge",
            )
            http_detail = f" (HTTP {http_code})" if http_code is not None else ""
            raise RuntimeError(
                "Gemini candidate judge submission outcome is ambiguous"
                f"{http_detail}; a worst-case charge was recorded under operation "
                f"{operation_id!r} and the request will not be retried"
            ) from exc

        uploaded: Any | None = None
        try:
            try:
                uploaded = self._upload_and_wait(video_path)
            except errors.APIError as exc:
                code = _api_error_code(exc)
                if is_definite_nonbillable_gemini_error(exc):
                    raise RuntimeError(
                        "Gemini rejected the candidate video before returning a "
                        f"billable result (HTTP {code}); no charge was recorded"
                    ) from exc
                raise_ambiguous(exc, http_code=code)
            except Exception as exc:
                raise_ambiguous(exc)
            parts = [
                types.Part.from_uri(
                    file_uri=uploaded.uri,
                    mime_type=uploaded.mime_type or "video/mp4",
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
                        response_schema=CandidateJudgeResponse,
                        max_output_tokens=MAX_OUTPUT_TOKENS,
                        thinking_config=types.ThinkingConfig(
                            include_thoughts=False,
                            thinking_level="medium",
                        ),
                    ),
                )
            except errors.APIError as exc:
                code = _api_error_code(exc)
                if is_definite_nonbillable_gemini_error(exc):
                    raise RuntimeError(
                        "Gemini rejected the candidate judge request before returning "
                        f"a billable result (HTTP {code}); no charge was recorded"
                    ) from exc
                raise_ambiguous(exc, http_code=code)
            except Exception as exc:
                raise_ambiguous(exc)

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
            parsed_response = CandidateJudgeResponse.model_validate(parsed)
            _validate_response(
                parsed_response,
                video_duration_seconds=duration,
                contract=contract,
            )
            snapshot = self.budget_ledger.snapshot()
            response_payload = parsed_response.model_dump(mode="json")
            return sanitize_judge_evidence(
                {
                    "schema_version": EVIDENCE_SCHEMA_VERSION,
                    "evaluator_version": EVALUATOR_VERSION,
                    "observation_mode": OBSERVATION_MODE,
                    "status": "complete",
                    "concept_id": contract["concept"]["concept_id"],
                    "concept_sha256": sha256_json(concept),
                    "storyboard_id": contract["storyboard"]["storyboard_id"],
                    "storyboard_sha256": sha256_json(storyboard),
                    "contract_sha256": sha256_json(contract),
                    "video_sha256": sha256_file(video_path),
                    "video_duration_ms": round(duration * 1000),
                    "prompt_sha256": sha256_text(prompt),
                    "response_schema_sha256": sha256_json(
                        CandidateJudgeResponse.model_json_schema()
                    ),
                    "requested_model": self.model,
                    "model_version": str(
                        getattr(provider_response, "model_version", "")
                        or self.model
                    ),
                    "response": response_payload,
                    "dimension_scores": map_dimension_statuses(parsed_response),
                    "provider": {
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
        finally:
            if uploaded is not None:
                try:
                    self.client.files.delete(name=uploaded.name)
                except Exception:
                    pass


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _extract_candidate_payload(
    payload: dict[str, Any],
    *,
    concept_id: str | None,
    field: Literal["concept", "storyboard"],
) -> dict[str, Any]:
    if field == "concept" and "hypothesis" in payload and "hook_beats" in payload:
        return payload
    if field == "storyboard" and "scenes" in payload:
        return payload

    if field == "concept" and isinstance(payload.get("concepts"), list):
        candidates = payload["concepts"]
        identity_key = "concept_id"
    elif isinstance(payload.get("candidates"), list):
        candidates = payload["candidates"]
        identity_key = "concept_id"
    else:
        raise ValueError(f"input does not contain a usable {field}")
    if not concept_id:
        raise ValueError("--concept-id is required for a batch or campaign plan")
    matches = [item for item in candidates if item.get(identity_key) == concept_id]
    if len(matches) != 1:
        raise ValueError(f"input has no unique concept {concept_id!r}")
    selected = matches[0]
    if field == "concept" and "concept" in selected:
        selected = selected["concept"]
    elif field == "storyboard":
        selected = selected.get("storyboard")
    if not isinstance(selected, dict):
        raise ValueError(f"selected candidate has no {field}")
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paid candidate-specific Gemini semantic judge"
    )
    parser.add_argument("--concept", required=True)
    parser.add_argument("--storyboard", required=True)
    parser.add_argument("--concept-id")
    parser.add_argument("--video", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--model", default=JUDGE_MODEL)
    parser.add_argument("--budget-database", default=str(DEFAULT_BUDGET_DATABASE))
    parser.add_argument("--confirm-paid", choices=("YES",), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    concept_path = _resolve_managed_file(
        args.concept,
        root=LOOP_ROOT,
        label="concept",
    )
    storyboard_path = _resolve_managed_file(
        args.storyboard,
        root=LOOP_ROOT,
        label="storyboard",
    )
    video_path = _resolve_managed_file(
        args.video,
        root=REPO_ROOT,
        label="video",
    )
    output = _resolve_output(args.output)
    budget_database = _resolve_budget_database(args.budget_database)
    concept_payload = json.loads(concept_path.read_text(encoding="utf-8"))
    storyboard_payload = json.loads(storyboard_path.read_text(encoding="utf-8"))
    concept = _extract_candidate_payload(
        concept_payload,
        concept_id=args.concept_id,
        field="concept",
    )
    storyboard = _extract_candidate_payload(
        storyboard_payload,
        concept_id=args.concept_id,
        field="storyboard",
    )
    duration = _video_duration(video_path)
    ledger = IterationBudgetLedger(
        budget_database,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    )

    if output.exists():
        if not output.is_file():
            raise ValueError(
                "existing candidate judge output is not a file; refusing to overwrite it"
            )
        try:
            loaded_evidence = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                "existing candidate judge output is not valid JSON; refusing to overwrite it"
            ) from exc
        if not isinstance(loaded_evidence, dict):
            raise ValueError(
                "existing candidate judge output must contain a JSON object"
            )
        evidence = _validate_existing_evidence(
            loaded_evidence,
            concept=concept,
            storyboard=storyboard,
            video_path=video_path,
            video_duration_seconds=duration,
            operation_id=args.operation_id,
            model=args.model,
            ledger=ledger,
        )
    else:
        _require_unused_operation(ledger, args.operation_id)

        from app.config import config

        api_key = str(config.app.get("gemini_api_key", "") or "").strip()
        api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Gemini API key is not configured")
        judge = GeminiCandidateJudge(
            api_key=api_key,
            budget_ledger=ledger,
            model=args.model,
        )
        evidence = judge.inspect(
            video_path=video_path,
            video_duration_seconds=duration,
            concept=concept,
            storyboard=storyboard,
            operation_id=args.operation_id,
        )
        _write_json(output, evidence)
    print(
        json.dumps(
            {
                "output": str(output.relative_to(LOOP_ROOT)),
                "concept_id": evidence["concept_id"],
                "video_sha256": evidence["video_sha256"],
                "dimension_scores": evidence["dimension_scores"],
                "charged_microusd": evidence["budget"]["charged_microusd"],
                "remaining_microusd": evidence["budget"][
                    "remaining_scope_microusd"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
