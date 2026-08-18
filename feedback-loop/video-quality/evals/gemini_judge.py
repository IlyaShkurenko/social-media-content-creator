from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402


EVALUATOR_VERSION = "0.6.0"
JUDGE_SCHEMA_VERSION = 1
JUDGE_MODEL = "gemini-3.6-flash"
ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_BUDGET_DATABASE = (
    LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
)
MAX_OUTPUT_TOKENS = 4096
VIDEO_TOKENS_PER_SECOND = 300
PROMPT_CHARACTERS_PER_TOKEN = 4
COST_SAFETY_MULTIPLIER = 1.25
MODEL_PRICING_USD_PER_MILLION_TOKENS = {
    JUDGE_MODEL: {"input": 1.50, "output": 7.50},
}
_AMBIGUOUS_GEMINI_HTTP_CODES = frozenset({408, 409, 425, 429})
RUBRIC_NAMES = (
    "editing_continuity",
    "storyboard_alignment",
    "audiovisual_coherence",
    "product_demo_clarity",
    "professional_finish",
)


def is_definite_nonbillable_gemini_error(exc: errors.APIError) -> bool:
    """Return whether Gemini explicitly rejected a request before acceptance.

    Server failures, timeouts, conflicts, and rate-limit responses do not prove
    that the provider failed before accepting billable work. Those outcomes are
    therefore charged conservatively and must not be retried under the same
    operation contract.
    """

    try:
        status = int(exc.code)
    except (TypeError, ValueError):
        return False
    return 400 <= status < 500 and status not in _AMBIGUOUS_GEMINI_HTTP_CODES


class CriterionScore(BaseModel):
    video_a: int = Field(ge=1, le=5)
    video_b: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1)


class RubricScores(BaseModel):
    editing_continuity: CriterionScore
    storyboard_alignment: CriterionScore
    audiovisual_coherence: CriterionScore
    product_demo_clarity: CriterionScore
    professional_finish: CriterionScore


class SceneObservation(BaseModel):
    video_label: Literal["A", "B"]
    scene_id: str = Field(min_length=1)
    observed_tags: list[str]
    evidence_timestamp_seconds: float = Field(ge=0)
    screen_class: Literal[
        "approved_tict_ui",
        "generic_non_product",
        "screen_not_visible",
        "none",
        "uncertain",
    ]
    claims_tict_identity: bool
    approved_asset_match: bool
    brand_asset_fidelity: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(min_length=1)


class PairwiseJudgeResponse(BaseModel):
    winner: Literal["A", "B", "tie"]
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1)
    rubric: RubricScores
    scene_observations: list[SceneObservation]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_judge_pass_cost_microusd(
    *,
    model: str,
    video_duration_seconds: float,
    prompt_characters: int,
) -> int:
    """Reserve a conservative maximum cost before one Gemini inference pass."""

    pricing = MODEL_PRICING_USD_PER_MILLION_TOKENS.get(model)
    if pricing is None:
        raise ValueError(f"no fail-closed pricing is configured for model {model!r}")
    if video_duration_seconds <= 0 or prompt_characters <= 0:
        raise ValueError("video duration and prompt size must be positive")

    input_tokens = math.ceil(
        video_duration_seconds * VIDEO_TOKENS_PER_SECOND
        + prompt_characters / PROMPT_CHARACTERS_PER_TOKEN
    )
    estimated_microusd = (
        input_tokens * pricing["input"]
        + MAX_OUTPUT_TOKENS * pricing["output"]
    )
    safe_estimate = math.ceil(
        estimated_microusd * COST_SAFETY_MULTIPLIER / 1_000
    ) * 1_000
    return max(50_000, safe_estimate)


def actual_usage_cost_microusd(
    *,
    model: str,
    prompt_tokens: int | None,
    output_tokens: int | None,
) -> int | None:
    pricing = MODEL_PRICING_USD_PER_MILLION_TOKENS.get(model)
    if pricing is None or prompt_tokens is None or output_tokens is None:
        return None
    return math.ceil(
        max(0, prompt_tokens) * pricing["input"]
        + max(0, output_tokens) * pricing["output"]
    )


def sanitize_judge_evidence(value: Any, *, key: str = "") -> Any:
    """Remove credentials and ephemeral provider file references recursively."""

    sensitive_fragments = (
        "api_key",
        "authorization",
        "secret",
        "provider_file",
        "file_uri",
        "download_uri",
    )
    if any(fragment in key.lower() for fragment in sensitive_fragments):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_judge_evidence(
                item_value,
                key=str(item_key),
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_judge_evidence(item, key=key) for item in value]
    if isinstance(value, Path):
        return value.name
    return value


def build_judge_prompt(scenario: dict[str, Any]) -> str:
    expected = scenario["expected"]
    storyboard = expected["storyboard"]
    tag_vocabulary = sorted(
        {
            tag
            for scene in storyboard
            for tag in scene.get("expected_tags", [])
        }
    )
    contract = {
        "scenario_id": scenario["id"],
        "purpose": scenario.get("purpose"),
        "expected_aspect_ratio": expected["aspect_ratio"],
        "storyboard": storyboard,
        "allowed_observed_tags": tag_vocabulary,
    }
    return f"""
You are a strict, evidence-based short-form advertising video reviewer.
The first attached video is VIDEO A. The second attached video is VIDEO B.
Review the full visual and audio streams against the storyboard contract below.

Return only the requested structured response. Never infer an expected tag merely
because it appears in the contract. Select observed_tags only from
allowed_observed_tags, attach each selection to the correct scene, and use the
closest supporting timestamp. Do not add arbitrary tags. A generic phone screen
must not be described as tict. approved_asset_match is true only when the exact
approved product/brand composition appears intact, not when generated text merely
resembles it.

Choose the stronger advertisement using the complete rubric. Prefer a tie when
the difference is not supported by visible or audible evidence. Judge editing
continuity, semantic transitions, storyboard alignment, correspondence between
voiceover and visuals, product demonstration clarity, brand correctness, and
whether the result feels professionally edited. Ignore the labels themselves and
do not assume either video is the baseline.

Produce exactly one scene observation for every (video_label, scene_id) pair.
The rubric uses integer scores from 1 to 5 for each video.

STORYBOARD CONTRACT:
{json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)}
""".strip()


def _state_name(uploaded_file: Any) -> str:
    state = getattr(uploaded_file, "state", None)
    return str(getattr(state, "name", state or "")).upper()


def _usage_record(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }


class GeminiVideoJudge:
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
                raise TimeoutError("Gemini file processing did not finish before timeout")
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
        total_video_duration_seconds: float,
        operation_id: str,
    ) -> dict[str, Any]:
        maximum_cost_microusd = estimate_judge_pass_cost_microusd(
            model=self.model,
            video_duration_seconds=total_video_duration_seconds,
            prompt_characters=len(prompt),
        )
        self.budget_ledger.ensure_available(maximum_cost_microusd)
        description = f"Gemini {self.model} evaluator {EVALUATOR_VERSION} {pass_id}"
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
            response = self.client.models.generate_content(
                model=self.model,
                contents=types.Content(role="user", parts=parts),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PairwiseJudgeResponse,
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
                    "Gemini rejected the judge request before returning a billable "
                    f"result (HTTP {exc.code}); no charge was recorded"
                ) from exc
            self.budget_ledger.record_manual_charge(
                operation_id,
                maximum_cost_microusd,
                f"{description}; ambiguous HTTP {exc.code}, worst-case charge",
            )
            raise RuntimeError(
                "Gemini judge submission outcome is ambiguous; a worst-case charge "
                f"was recorded under operation {operation_id!r} and was not retried"
            ) from exc
        except Exception as exc:
            self.budget_ledger.record_manual_charge(
                operation_id,
                maximum_cost_microusd,
                f"{description}; unknown provider outcome, worst-case charge",
            )
            raise RuntimeError(
                "Gemini judge submission outcome is unknown; a worst-case charge "
                f"was recorded under operation {operation_id!r} and was not retried"
            ) from exc

        response_id = str(
            getattr(response, "response_id", "")
            or f"gemini-{sha256_text(operation_id)[:24]}"
        )
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            parsed = json.loads(response.text)
        parsed_response = PairwiseJudgeResponse.model_validate(parsed)
        usage = _usage_record(response)
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
        snapshot = self.budget_ledger.snapshot()
        return {
            "pass_id": pass_id,
            "order": order,
            "response": parsed_response.model_dump(mode="json"),
            "provider": {
                "requested_model": self.model,
                "model_version": str(
                    getattr(response, "model_version", "") or self.model
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
    ) -> dict[str, Any]:
        if not operation_prefix.strip():
            raise ValueError("judge operation_prefix is required")
        existing_operations = {
            operation_id: self.budget_ledger.find_operation(operation_id)
            for operation_id in (
                f"{operation_prefix}-pass-1",
                f"{operation_prefix}-pass-2",
            )
        }
        existing = [
            operation
            for operation in existing_operations.values()
            if operation is not None
        ]
        if existing:
            raise RuntimeError(
                "legacy pairwise judge operation already exists; automatic paid "
                "resubmission is blocked and stored evidence must be replayed offline"
            )
        prompt = build_judge_prompt(scenario)
        baseline_sha256 = sha256_file(baseline_video)
        candidate_sha256 = sha256_file(candidate_video)
        uploaded_by_sha: dict[str, Any] = {}
        uploaded_names: list[str] = []
        passes: list[dict[str, Any]] = []

        def build_evidence(*, status: Literal["partial", "complete"]) -> dict[str, Any]:
            model_versions = sorted(
                {item["provider"]["model_version"] for item in passes}
            )
            return sanitize_judge_evidence(
                {
                    "schema_version": JUDGE_SCHEMA_VERSION,
                    "evaluator_version": EVALUATOR_VERSION,
                    "observation_mode": "gemini_pairwise_v1",
                    "status": status,
                    "scenario_id": scenario["id"],
                    "scenario_sha256": scenario_sha256,
                    "prompt_sha256": sha256_text(prompt),
                    "requested_model": self.model,
                    "model_versions": model_versions,
                    "baseline_sha256": baseline_sha256,
                    "candidate_sha256": candidate_sha256,
                    "self_comparison": baseline_sha256 == candidate_sha256,
                    "passes": passes,
                }
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

            mappings = (
                ("baseline-a", baseline_sha256, candidate_sha256),
                ("candidate-a", candidate_sha256, baseline_sha256),
            )
            for index, (pass_id, a_sha, b_sha) in enumerate(mappings, start=1):
                passes.append(
                    self._judge_pass(
                        pass_id=pass_id,
                        video_a=uploaded_by_sha[a_sha],
                        video_b=uploaded_by_sha[b_sha],
                        order={"A": a_sha, "B": b_sha},
                        prompt=prompt,
                        total_video_duration_seconds=(
                            baseline_duration_seconds + candidate_duration_seconds
                        ),
                        operation_id=f"{operation_prefix}-pass-{index}",
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
    return float(result.stdout.strip())


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
        description="Run the paid, order-balanced Gemini video-quality judge"
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
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))

    from app.config import config

    api_key = str(config.app.get("gemini_api_key", "") or "").strip()
    api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Gemini is not configured: save Gemini API Key in the WebUI or "
            "set GEMINI_API_KEY"
        )
    ledger = IterationBudgetLedger(
        Path(args.budget_database),
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    )
    judge = GeminiVideoJudge(
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
    )
    _write_json(output, evidence)
    total_charged = sum(
        item["budget"]["charged_microusd"]
        for item in evidence["passes"]
    )
    print(
        json.dumps(
            {
                "output": str(output.relative_to(LOOP_ROOT)),
                "model": evidence["requested_model"],
                "passes": len(evidence["passes"]),
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
