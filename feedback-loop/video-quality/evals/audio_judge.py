from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Literal

from google import genai
from google.genai import errors, types
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
    is_definite_nonbillable_gemini_error,
    sanitize_judge_evidence,
    sha256_file,
    sha256_text,
)


EVALUATOR_VERSION = "2.0.0"
EVIDENCE_SCHEMA_VERSION = 1
OBSERVATION_MODE = "gemini_audio_pairwise_v1"
MAX_OUTPUT_TOKENS = 2048
# Gemini's published audio-input rate is far below its video rate (no visual
# frames to encode); this stays a deliberate over-estimate for a fail-closed
# preflight ceiling covering both clips in a pass, not a claimed exact figure.
AUDIO_TOKENS_PER_SECOND = 32
PROMPT_CHARACTERS_PER_TOKEN = 4
COST_SAFETY_MULTIPLIER = 1.25
MAX_INLINE_AUDIO_BYTES = 20 * 1024 * 1024
ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_BUDGET_DATABASE = LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"

DIMENSION_NAMES = ("naturalness", "emotional_match", "ending_consistency")


class PairwiseDimension(BaseModel):
    winner: Literal["A", "B", "tie"]
    reason: str = Field(min_length=1)


class PairwiseAudioResponse(BaseModel):
    transcription_a: str = Field(min_length=1)
    transcription_b: str = Field(min_length=1)
    naturalness: PairwiseDimension
    emotional_match: PairwiseDimension
    ending_consistency: PairwiseDimension
    overall_winner: Literal["A", "B", "tie"]
    overall_reason: str = Field(min_length=1)


def build_pairwise_audio_prompt(*, spoken_text: str, target_emotion: str) -> str:
    return f"""You are a strict, evidence-based pairwise reviewer of two narration
clips for the same ad line. The first attached audio is CLIP A and the second
is CLIP B. Both should speak this exact line:

"{spoken_text}"

Target emotion: {target_emotion}

First transcribe each clip separately (transcription_a, transcription_b) so we
can confirm each is actually the intended line, not silence, noise, or a
different clip.

Then compare the two clips directly against each other, not against an
absolute standard, on each dimension below. For each, choose "A", "B", or
"tie", with a one-sentence reason grounded in what you actually hear in both
clips:

- naturalness: which sounds more like a real, unscripted person, and which
  sounds more synthetic or mechanical? Choose "tie" only if you genuinely
  cannot tell them apart on this dimension.
- emotional_match: which clip's delivery better matches the target emotion
  above?
- ending_consistency: which clip holds its emotional tone and energy through
  to the very last word more consistently — without trailing off, turning
  inappropriately soft or tender, or otherwise shifting tone right at the end
  regardless of how the rest of the line sounds?

Finally give one overall_winner ("A", "B", or "tie") with overall_reason,
weighing all three dimensions together. Do not default to "tie" to avoid
committing — only use it when the two clips are genuinely indistinguishable on
that specific dimension. Ground every judgment in what is actually audible;
do not speculate about anything you cannot hear.
"""


def _audio_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(suffix, "audio/mpeg")


def _api_error_code(exc: errors.APIError) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def _usage_record(response: Any) -> dict[str, int | None]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {"prompt_tokens": None, "output_tokens": None}
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
    }


def estimate_pairwise_pass_cost_microusd(
    *,
    model: str,
    total_audio_duration_seconds: float,
    prompt_characters: int,
) -> int:
    pricing = MODEL_PRICING_USD_PER_MILLION_TOKENS.get(model)
    if pricing is None:
        raise ValueError(f"no fail-closed pricing is configured for model {model!r}")
    if total_audio_duration_seconds <= 0 or prompt_characters <= 0:
        raise ValueError("audio duration and prompt size must be positive")

    input_tokens = math.ceil(
        total_audio_duration_seconds * AUDIO_TOKENS_PER_SECOND
        + prompt_characters / PROMPT_CHARACTERS_PER_TOKEN
    )
    estimated_microusd = (
        input_tokens * pricing["input"] + MAX_OUTPUT_TOKENS * pricing["output"]
    )
    safe_estimate = math.ceil(estimated_microusd * COST_SAFETY_MULTIPLIER / 1_000) * 1_000
    return max(20_000, safe_estimate)


def build_pairwise_diagnostic(
    passes: list[dict[str, Any]],
    *,
    clip_a_sha256: str,
    clip_b_sha256: str,
) -> dict[str, Any]:
    """Convert order-balanced label winners into a clip_b-credit diagnostic.

    Mirrors invariant_judge.py's build_winner_diagnostic, generalised from
    baseline/candidate to two arbitrary clips: credit is toward clip_b.
    """

    if not passes:
        return {
            "diagnostic_only": True,
            "position_balanced": False,
            "clip_b_credit": None,
            "pass_credits": [],
            "outcome": "incomplete",
        }
    credits: list[float] = []
    for item in passes:
        order = item.get("order", {})
        winner = item.get("response", {}).get("overall_winner")
        if winner == "tie" or clip_a_sha256 == clip_b_sha256:
            credits.append(0.5)
        elif winner in {"A", "B"} and order.get(winner) == clip_b_sha256:
            credits.append(1.0)
        elif winner in {"A", "B"} and order.get(winner) == clip_a_sha256:
            credits.append(0.0)
        else:
            raise ValueError("audio judge winner does not map to an input clip")
    credit = sum(credits) / len(credits)
    outcome = "clip_b" if credit > 0.5 else "clip_a" if credit < 0.5 else "tie"
    position_balanced = (
        len(passes) == 2
        and passes[0].get("order", {}).get("A") == passes[1].get("order", {}).get("B")
        and passes[0].get("order", {}).get("B") == passes[1].get("order", {}).get("A")
    )
    return {
        "diagnostic_only": True,
        "position_balanced": position_balanced,
        "clip_b_credit": credit,
        "pass_credits": credits,
        "outcome": outcome,
    }


def _require_unused_operation(ledger: IterationBudgetLedger, operation_id: str) -> None:
    existing = ledger.find_operation(operation_id)
    if existing is not None:
        raise RuntimeError(
            f"audio judge operation {operation_id!r} is already {existing.status}; "
            "a paid pass with this id already has a ledger entry and will not be "
            "resubmitted"
        )


def _audio_duration_seconds(path: Path) -> float:
    import subprocess

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
        check=False,
    )
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot determine audio duration for {path.name!r}") from exc
    if result.returncode != 0 or duration <= 0:
        raise ValueError(f"invalid audio file: {path.name!r}")
    return duration


class GeminiAudioJudge:
    """Fail-closed, ledger-tracked pairwise naturalness/emotion audio judge.

    Diagnostic only: no automatic accept/reject decision is wired to this
    evidence. See RFC-0007. Compares exactly two clips per call with two
    reversed-order passes; round_robin_compare() orchestrates every unique
    pair across more than two clips.
    """

    def __init__(
        self,
        *,
        api_key: str,
        budget_ledger: IterationBudgetLedger,
        model: str = JUDGE_MODEL,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key is required")
        self.client = client or genai.Client(api_key=api_key)
        self.budget_ledger = budget_ledger
        self.model = model

    def _judge_pass(
        self,
        *,
        pass_id: str,
        audio_a: tuple[Path, bytes],
        audio_b: tuple[Path, bytes],
        order: dict[str, str],
        prompt: str,
        total_audio_duration_seconds: float,
        operation_id: str,
    ) -> dict[str, Any]:
        maximum_cost_microusd = estimate_pairwise_pass_cost_microusd(
            model=self.model,
            total_audio_duration_seconds=total_audio_duration_seconds,
            prompt_characters=len(prompt),
        )
        self.budget_ledger.ensure_available(maximum_cost_microusd)
        description = f"Gemini {self.model} audio evaluator {EVALUATOR_VERSION} {pass_id}"

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
                "Gemini audio judge submission outcome is ambiguous"
                f"{http_detail}; a worst-case charge was recorded under operation "
                f"{operation_id!r} and the request will not be retried"
            ) from exc

        parts = [
            types.Part.from_bytes(
                data=audio_a[1], mime_type=_audio_mime_type(audio_a[0])
            ),
            types.Part.from_bytes(
                data=audio_b[1], mime_type=_audio_mime_type(audio_b[0])
            ),
            types.Part.from_text(text=prompt),
        ]
        try:
            provider_response = self.client.models.generate_content(
                model=self.model,
                contents=types.Content(role="user", parts=parts),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PairwiseAudioResponse,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
        except errors.APIError as exc:
            code = _api_error_code(exc)
            if is_definite_nonbillable_gemini_error(exc):
                raise RuntimeError(
                    "Gemini rejected the audio judge request before returning a "
                    f"billable result (HTTP {code}); no charge was recorded"
                ) from exc
            raise_ambiguous(exc, http_code=code)
            raise
        except Exception as exc:
            raise_ambiguous(exc)
            raise

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
        parsed_response = PairwiseAudioResponse.model_validate(parsed)
        snapshot = self.budget_ledger.snapshot()
        return sanitize_judge_evidence(
            {
                "pass_id": pass_id,
                "order": order,
                "response": parsed_response.model_dump(mode="json"),
                "provider": {
                    "requested_model": self.model,
                    "model_version": str(
                        getattr(provider_response, "model_version", "") or self.model
                    ),
                    "response_id": response_id,
                    "usage": usage,
                },
                "budget": {
                    "operation_id": operation_id,
                    "charged_microusd": charged_microusd,
                    "remaining_scope_microusd": snapshot.remaining_microusd,
                },
            }
        )

    def compare(
        self,
        *,
        clip_a: Path,
        clip_b: Path,
        spoken_text: str,
        target_emotion: str,
        operation_prefix: str,
    ) -> dict[str, Any]:
        if not operation_prefix.strip():
            raise ValueError("audio judge operation_prefix is required")
        for path in (clip_a, clip_b):
            if not path.is_file():
                raise ValueError(f"audio clip does not exist: {path}")
            if path.stat().st_size > MAX_INLINE_AUDIO_BYTES:
                raise ValueError(f"audio clip exceeds the inline upload limit: {path}")

        clip_a_bytes = clip_a.read_bytes()
        clip_b_bytes = clip_b.read_bytes()
        clip_a_sha256 = sha256_file(clip_a)
        clip_b_sha256 = sha256_file(clip_b)
        duration_a = _audio_duration_seconds(clip_a)
        duration_b = _audio_duration_seconds(clip_b)
        prompt = build_pairwise_audio_prompt(
            spoken_text=spoken_text, target_emotion=target_emotion
        )

        expected_passes = (
            {
                "pass_id": "clip-a-first",
                "order": {"A": clip_a_sha256, "B": clip_b_sha256},
                "audio": {"A": (clip_a, clip_a_bytes), "B": (clip_b, clip_b_bytes)},
                "operation_id": f"{operation_prefix}-pass-1",
            },
            {
                "pass_id": "clip-b-first",
                "order": {"A": clip_b_sha256, "B": clip_a_sha256},
                "audio": {"A": (clip_b, clip_b_bytes), "B": (clip_a, clip_a_bytes)},
                "operation_id": f"{operation_prefix}-pass-2",
            },
        )
        ledger_operations = {
            item.operation_id: item for item in self.budget_ledger.list_operations()
        }
        for expected_pass in expected_passes:
            if expected_pass["operation_id"] in ledger_operations:
                raise RuntimeError(
                    "an audio judge pass already has a ledger operation; its "
                    "provider outcome may be ambiguous, so it will not be retried"
                )

        passes: list[dict[str, Any]] = []
        for expected_pass in expected_passes:
            passes.append(
                self._judge_pass(
                    pass_id=expected_pass["pass_id"],
                    audio_a=expected_pass["audio"]["A"],
                    audio_b=expected_pass["audio"]["B"],
                    order=expected_pass["order"],
                    prompt=prompt,
                    total_audio_duration_seconds=duration_a + duration_b,
                    operation_id=expected_pass["operation_id"],
                )
            )

        model_versions = sorted({item["provider"]["model_version"] for item in passes})
        return sanitize_judge_evidence(
            {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "evaluator_version": EVALUATOR_VERSION,
                "observation_mode": OBSERVATION_MODE,
                "status": "complete",
                "spoken_text": spoken_text,
                "target_emotion": target_emotion,
                "requested_model": self.model,
                "model_versions": model_versions,
                "clip_a_sha256": clip_a_sha256,
                "clip_b_sha256": clip_b_sha256,
                "clip_b_credit_diagnostic": build_pairwise_diagnostic(
                    passes, clip_a_sha256=clip_a_sha256, clip_b_sha256=clip_b_sha256
                ),
                "passes": passes,
            }
        )


def round_robin_compare(
    judge: GeminiAudioJudge,
    clips: list[tuple[str, Path]],
    *,
    spoken_text: str,
    target_emotion: str,
    operation_prefix_base: str,
) -> dict[str, Any]:
    """Run every unique pair (label_i, label_j) through GeminiAudioJudge.compare().

    Returns per-pair evidence plus a simple leaderboard: each clip's average
    preference credit across all pairs it appears in (0..1, higher = more
    often preferred). This is diagnostic ranking evidence, not an acceptance
    decision — see RFC-0007.
    """

    if len(clips) < 2:
        raise ValueError("round robin requires at least two clips")
    labels = [label for label, _ in clips]
    if len(set(labels)) != len(labels):
        raise ValueError("round robin clip labels must be unique")

    pair_results: list[dict[str, Any]] = []
    credit_totals: dict[str, float] = {label: 0.0 for label in labels}
    credit_counts: dict[str, int] = {label: 0 for label in labels}

    for (label_a, path_a), (label_b, path_b) in itertools.combinations(clips, 2):
        operation_prefix = f"{operation_prefix_base}-{label_a}-vs-{label_b}"
        evidence = judge.compare(
            clip_a=path_a,
            clip_b=path_b,
            spoken_text=spoken_text,
            target_emotion=target_emotion,
            operation_prefix=operation_prefix,
        )
        credit_b = evidence["clip_b_credit_diagnostic"]["clip_b_credit"]
        pair_results.append(
            {
                "clip_a_label": label_a,
                "clip_b_label": label_b,
                "evidence": evidence,
            }
        )
        if credit_b is not None:
            credit_totals[label_b] += credit_b
            credit_counts[label_b] += 1
            credit_totals[label_a] += 1 - credit_b
            credit_counts[label_a] += 1

    leaderboard = sorted(
        (
            {
                "label": label,
                "average_preference_credit": (
                    credit_totals[label] / credit_counts[label]
                    if credit_counts[label]
                    else None
                ),
                "pairs_judged": credit_counts[label],
            }
            for label in labels
        ),
        key=lambda item: (
            item["average_preference_credit"] is None,
            -(item["average_preference_credit"] or 0.0),
        ),
    )
    return {
        "diagnostic_only": True,
        "spoken_text": spoken_text,
        "target_emotion": target_emotion,
        "pairs_judged": len(pair_results),
        "leaderboard": leaderboard,
        "pairs": pair_results,
    }


def _resolve_file(value: str, *, root: Path, label: str) -> Path:
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if root not in path.parents and path != root:
        raise ValueError(f"{label} must be a file inside {root}: {path}")
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _resolve_output(value: str) -> Path:
    path = (LOOP_ROOT / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if LOOP_ROOT not in path.parents:
        raise ValueError(f"output must be a file inside {LOOP_ROOT}: {path}")
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the paid Gemini pairwise narration naturalness/emotion judge"
    )
    parser.add_argument(
        "--clip",
        action="append",
        dest="clips",
        metavar="LABEL=PATH",
        required=True,
        help="Repeatable. Two clips run one pairwise comparison; three or more "
        "run a full round robin over every unique pair.",
    )
    parser.add_argument("--spoken-text", required=True)
    parser.add_argument("--target-emotion", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--operation-prefix", required=True)
    parser.add_argument("--model", default=JUDGE_MODEL)
    parser.add_argument("--budget-database", default=str(DEFAULT_BUDGET_DATABASE))
    parser.add_argument("--confirm-paid", choices=("YES",), required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    clips: list[tuple[str, Path]] = []
    for item in args.clips:
        if "=" not in item:
            raise ValueError(f"--clip must be LABEL=PATH, got {item!r}")
        label, raw_path = item.split("=", 1)
        clips.append((label, _resolve_file(raw_path, root=REPO_ROOT, label=f"clip {label!r}")))

    output = _resolve_output(args.output)
    budget_database = (
        Path(args.budget_database)
        if Path(args.budget_database).is_absolute()
        else (LOOP_ROOT / args.budget_database)
    ).resolve()
    ledger = IterationBudgetLedger(
        budget_database,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    )

    from app.config import config

    api_key = str(config.app.get("gemini_api_key", "") or "").strip()
    api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is not configured")

    judge = GeminiAudioJudge(api_key=api_key, budget_ledger=ledger, model=args.model)

    if len(clips) == 2:
        (label_a, path_a), (label_b, path_b) = clips
        result = judge.compare(
            clip_a=path_a,
            clip_b=path_b,
            spoken_text=args.spoken_text,
            target_emotion=args.target_emotion,
            operation_prefix=args.operation_prefix,
        )
        summary = {
            "clip_a_label": label_a,
            "clip_b_label": label_b,
            "clip_b_credit_diagnostic": result["clip_b_credit_diagnostic"],
        }
    else:
        result = round_robin_compare(
            judge,
            clips,
            spoken_text=args.spoken_text,
            target_emotion=args.target_emotion,
            operation_prefix_base=args.operation_prefix,
        )
        summary = {"leaderboard": result["leaderboard"]}

    _write_json(output, result)
    snapshot = ledger.snapshot()
    print(
        json.dumps(
            {
                "output": str(output.relative_to(LOOP_ROOT)),
                **summary,
                "charged_microusd": snapshot.charged_microusd,
                "remaining_microusd": snapshot.remaining_microusd,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
