from __future__ import annotations

import base64
import gzip
import hashlib
import json
from typing import Any

from pydantic import ValidationError

from evals.candidate_judge import (
    DIMENSION_NAMES,
    EVALUATOR_VERSION as REQUIRED_CANDIDATE_EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION as CANDIDATE_EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_MODE as CANDIDATE_OBSERVATION_MODE,
    CandidateJudgeResponse,
    _candidate_contract,
    map_dimension_statuses,
)
from evals.invariant_judge import (
    CRITERION_NAMES,
    EVALUATOR_VERSION as INVARIANT_EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION as INVARIANT_EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_MODE as INVARIANT_OBSERVATION_MODE,
    InvariantJudgeResponse,
    _validate_response as validate_invariant_response,
    build_winner_diagnostic,
    map_invariant_statuses,
)


MANIFEST_SCHEMA_VERSION = 1
METRICS_SCHEMA_VERSION = "1.0"
RECORD_KIND = "candidate_evaluator_calibration_attempt"
DECISION = "blocked_provider_unavailable"
HISTORICAL_CANDIDATE_EVALUATOR_VERSION = "1.1.0"
DOCUMENT_ENCODING = "gzip+base64+canonical-json"
MAX_EMBEDDED_DOCUMENT_BYTES = 1_000_000
_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "access_token",
    "refresh_token",
    "secret",
    "signed_url",
    "output_url",
    "file_uri",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _assert_sanitized(value: Any, *, path: str = "document") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in _SENSITIVE_KEYS and item != "[redacted]":
                raise ValueError(f"{path}.{key} contains unsanitized sensitive data")
            _assert_sanitized(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_sanitized(item, path=f"{path}[{index}]")
    elif isinstance(value, str) and (
        value.startswith("/Users/")
        or value.startswith("/home/")
        or ":\\Users\\" in value
    ):
        raise ValueError(f"{path} contains an unrestricted absolute path")


def decode_document(entry: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError(f"{label} must be an embedded document")
    if entry.get("encoding") != DOCUMENT_ENCODING:
        raise ValueError(f"{label} has an unsupported encoding")
    if not _is_sha256(entry.get("source_sha256")) or not _is_sha256(
        entry.get("content_sha256")
    ):
        raise ValueError(f"{label} has an invalid source or content hash")
    encoded = entry.get("content_gzip_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError(f"{label} has no embedded content")
    try:
        compressed = base64.b64decode(encoded, validate=True)
        content_bytes = gzip.decompress(compressed)
    except (ValueError, gzip.BadGzipFile, EOFError) as exc:
        raise ValueError(f"{label} embedded content is not valid gzip JSON") from exc
    if len(content_bytes) > MAX_EMBEDDED_DOCUMENT_BYTES:
        raise ValueError(f"{label} embedded content exceeds the replay limit")
    try:
        content = json.loads(content_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} embedded content is not valid JSON") from exc
    if not isinstance(content, dict):
        raise ValueError(f"{label} embedded content must be an object")
    if _canonical_json(content) != content_bytes:
        raise ValueError(f"{label} embedded content is not canonical JSON")
    if sha256_json(content) != entry["content_sha256"]:
        raise ValueError(f"{label} embedded content hash changed")
    _assert_sanitized(content, path=label)
    return content


def _validated_cost(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} requires a cost record")
    operation_id = value.get("operation_id")
    maximum = value.get("preflight_maximum_microusd")
    charged = value.get("charged_microusd")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValueError(f"{label} requires an operation ID")
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or maximum <= 0
        or not isinstance(charged, int)
        or isinstance(charged, bool)
        or charged <= 0
        or charged > maximum
    ):
        raise ValueError(f"{label} has invalid conservative cost values")
    return {
        "operation_id": operation_id,
        "charged_microusd": charged,
        "preflight_maximum_microusd": maximum,
    }


def _validate_candidate_evidence(
    evidence: dict[str, Any],
    *,
    contract_document: dict[str, Any],
    candidate_sha256: str,
) -> tuple[dict[str, float | None], dict[str, Any]]:
    expected = {
        "schema_version": CANDIDATE_EVIDENCE_SCHEMA_VERSION,
        "evaluator_version": HISTORICAL_CANDIDATE_EVALUATOR_VERSION,
        "observation_mode": CANDIDATE_OBSERVATION_MODE,
        "status": "complete",
        "video_sha256": candidate_sha256,
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise ValueError(f"candidate evidence has a mismatched {key}")
    concept = contract_document.get("concept")
    storyboard = contract_document.get("storyboard")
    if not isinstance(concept, dict) or not isinstance(storyboard, dict):
        raise ValueError("candidate contract requires concept and storyboard objects")
    contract = _candidate_contract(concept, storyboard)
    bindings = {
        "concept_id": concept.get("concept_id"),
        "concept_sha256": sha256_json(concept),
        "storyboard_sha256": sha256_json(storyboard),
        "contract_sha256": sha256_json(contract),
    }
    for key, value in bindings.items():
        if evidence.get(key) != value:
            raise ValueError(f"candidate evidence has a mismatched {key}")
    duration_ms = evidence.get("video_duration_ms")
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms <= 0
    ):
        raise ValueError("candidate evidence requires a positive duration")
    try:
        response = CandidateJudgeResponse.model_validate(evidence.get("response"))
    except ValidationError as exc:
        raise ValueError("candidate evidence response is invalid") from exc
    # Evaluator 1.2 introduced dimension-specific time domains. Preserve the
    # historical 1.1 validation boundary here instead of reinterpreting its
    # response as current evidence.
    maximum_timestamp_ms = duration_ms + 250
    for name in DIMENSION_NAMES:
        assessment = getattr(response, name)
        if assessment.status == "unverifiable":
            if assessment.evidence:
                raise ValueError(
                    f"historical candidate dimension {name!r} is unverifiable "
                    "but cites evidence"
                )
            continue
        if not assessment.evidence:
            raise ValueError(
                f"historical candidate dimension {name!r} requires evidence"
            )
        if any(
            citation.timestamp_ms > maximum_timestamp_ms
            for citation in assessment.evidence
        ):
            raise ValueError(
                f"historical candidate dimension {name!r} cites outside the video"
            )
    scores = map_dimension_statuses(response)
    if evidence.get("dimension_scores") != scores:
        raise ValueError("candidate evidence has stale deterministic scores")
    if (
        not _is_sha256(evidence.get("prompt_sha256"))
        or not _is_sha256(evidence.get("response_schema_sha256"))
        or not str(evidence.get("requested_model", "")).strip()
        or not str(evidence.get("model_version", "")).strip()
    ):
        raise ValueError("candidate evidence has an invalid evaluator binding")
    provider = evidence.get("provider")
    if not isinstance(provider, dict) or not str(
        provider.get("response_id", "")
    ).strip():
        raise ValueError("candidate evidence requires a provider response ID")
    return scores, _validated_cost(evidence.get("budget"), label="candidate evidence")


def _validate_partial_invariant_evidence(
    evidence: dict[str, Any],
    *,
    shared_contract: dict[str, Any],
    reference_sha256: str,
    candidate_sha256: str,
    reference_duration_ms: int,
    candidate_duration_ms: int,
) -> tuple[dict[str, dict[str, float | None]], dict[str, Any], dict[str, Any]]:
    expected = {
        "schema_version": INVARIANT_EVIDENCE_SCHEMA_VERSION,
        "evaluator_version": INVARIANT_EVALUATOR_VERSION,
        "observation_mode": INVARIANT_OBSERVATION_MODE,
        "status": "partial",
        "baseline_sha256": reference_sha256,
        "candidate_sha256": candidate_sha256,
        "self_comparison": False,
        "shared_contract_sha256": sha256_json(shared_contract),
    }
    for key, value in expected.items():
        if evidence.get(key) != value:
            raise ValueError(f"invariant evidence has a mismatched {key}")
    if (
        not _is_sha256(evidence.get("scenario_sha256"))
        or not _is_sha256(evidence.get("prompt_sha256"))
        or not _is_sha256(evidence.get("response_schema_sha256"))
        or not str(evidence.get("scenario_id", "")).strip()
        or not str(evidence.get("requested_model", "")).strip()
    ):
        raise ValueError("invariant evidence has an invalid evaluator binding")
    passes = evidence.get("passes")
    if not isinstance(passes, list) or len(passes) != 1:
        raise ValueError("calibration attempt requires exactly one completed invariant pass")
    item = passes[0]
    if not isinstance(item, dict) or item.get("pass_id") != "baseline-a":
        raise ValueError("partial invariant evidence must retain the baseline-a pass")
    expected_order = {"A": reference_sha256, "B": candidate_sha256}
    if item.get("order") != expected_order:
        raise ValueError("partial invariant pass has a mismatched A/B order")
    provider = item.get("provider")
    if (
        not isinstance(provider, dict)
        or provider.get("requested_model") != evidence.get("requested_model")
        or not str(provider.get("model_version", "")).strip()
        or not str(provider.get("response_id", "")).strip()
    ):
        raise ValueError("partial invariant pass has an invalid provider binding")
    try:
        response = InvariantJudgeResponse.model_validate(item.get("response"))
    except ValidationError as exc:
        raise ValueError("partial invariant response is invalid") from exc
    validate_invariant_response(
        response,
        contract=shared_contract,
        video_duration_seconds_by_label={
            "A": reference_duration_ms / 1000,
            "B": candidate_duration_ms / 1000,
        },
    )
    mapped = map_invariant_statuses(response)
    if item.get("criterion_scores") != mapped:
        raise ValueError("partial invariant evidence has stale deterministic scores")
    model_versions = [provider["model_version"]]
    if evidence.get("model_versions") != model_versions:
        raise ValueError("partial invariant evidence has stale model versions")
    diagnostic = build_winner_diagnostic(
        passes,
        baseline_sha256=reference_sha256,
        candidate_sha256=candidate_sha256,
    )
    if evidence.get("winner_diagnostic") != diagnostic:
        raise ValueError("partial invariant evidence has a stale winner diagnostic")
    if diagnostic.get("position_balanced") is not False:
        raise ValueError("partial invariant evidence unexpectedly claims balance")
    return (
        mapped,
        diagnostic,
        _validated_cost(item.get("budget"), label="partial invariant pass"),
    )


def _validate_related_costs(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValueError("related_costs must be a list")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("every related cost must be an object")
        operation_id = item.get("operation_id")
        charged = item.get("charged_microusd")
        if (
            not isinstance(operation_id, str)
            or not operation_id.strip()
            or operation_id in seen
            or not isinstance(charged, int)
            or isinstance(charged, bool)
            or charged <= 0
            or item.get("accounting") != "conservative_manual_charge"
            or item.get("outcome")
            not in {"superseded_measurement", "ambiguous_duplicate_response"}
            or not str(item.get("reason", "")).strip()
        ):
            raise ValueError("related cost has invalid or duplicate provenance")
        seen.add(operation_id)
        results.append(
            {
                "operation_id": operation_id,
                "charged_microusd": charged,
                "accounting": item["accounting"],
                "outcome": item["outcome"],
                "reason": item["reason"],
            }
        )
    return results


def rebuild_candidate_evaluator_attempt_metrics(
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate and rebuild one incomplete evaluator-research attempt offline."""

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported calibration-attempt manifest schema")
    if manifest.get("kind") != RECORD_KIND:
        raise ValueError("not a candidate evaluator calibration attempt")
    lifecycle = manifest.get("lifecycle")
    expected_lifecycle = {
        "status": "blocked",
        "decision": DECISION,
        "baseline_established": False,
        "acceptance_authority": False,
        "product_improvement_claim": False,
    }
    if not isinstance(lifecycle, dict) or any(
        lifecycle.get(key) != value for key, value in expected_lifecycle.items()
    ):
        raise ValueError("calibration attempt overclaims its lifecycle or authority")
    if manifest.get("required_evaluator_versions") != {
        "candidate": REQUIRED_CANDIDATE_EVALUATOR_VERSION,
        "invariant": INVARIANT_EVALUATOR_VERSION,
    }:
        raise ValueError("calibration attempt has stale required evaluator versions")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("calibration attempt requires artifact bindings")
    reference = artifacts.get("reference")
    candidate = artifacts.get("candidate")
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        raise ValueError("calibration attempt requires reference and candidate bindings")
    reference_sha256 = reference.get("sha256")
    candidate_sha256 = candidate.get("sha256")
    if (
        not _is_sha256(reference_sha256)
        or not _is_sha256(candidate_sha256)
        or reference_sha256 == candidate_sha256
    ):
        raise ValueError("calibration attempt has invalid artifact hashes")
    reference_duration_ms = reference.get("duration_ms")
    candidate_duration_ms = candidate.get("duration_ms")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (reference_duration_ms, candidate_duration_ms)
    ):
        raise ValueError("calibration attempt has invalid artifact durations")

    contract_document = decode_document(
        manifest.get("candidate_contract"),
        label="candidate contract",
    )
    shared_contract = decode_document(
        manifest.get("shared_invariant_contract"),
        label="shared invariant contract",
    )
    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("calibration attempt requires embedded evidence")
    candidate_evidence = decode_document(
        evidence.get("candidate"),
        label="candidate evidence",
    )
    invariant_evidence = decode_document(
        evidence.get("invariant"),
        label="invariant evidence",
    )
    candidate_scores, candidate_cost = _validate_candidate_evidence(
        candidate_evidence,
        contract_document=contract_document,
        candidate_sha256=candidate_sha256,
    )
    invariant_scores, diagnostic, invariant_cost = (
        _validate_partial_invariant_evidence(
            invariant_evidence,
            shared_contract=shared_contract,
            reference_sha256=reference_sha256,
            candidate_sha256=candidate_sha256,
            reference_duration_ms=reference_duration_ms,
            candidate_duration_ms=candidate_duration_ms,
        )
    )
    related_costs = _validate_related_costs(manifest.get("related_costs"))
    all_operation_ids = {
        candidate_cost["operation_id"],
        invariant_cost["operation_id"],
        *(item["operation_id"] for item in related_costs),
    }
    if len(all_operation_ids) != 2 + len(related_costs):
        raise ValueError("calibration attempt reuses a paid operation ID")
    costs = {
        "accounting": "conservative_recorded_charge",
        "evidence_operations": [candidate_cost, invariant_cost],
        "related_operations": related_costs,
        "total_charged_microusd": (
            candidate_cost["charged_microusd"]
            + invariant_cost["charged_microusd"]
            + sum(item["charged_microusd"] for item in related_costs)
        ),
    }
    missing_pass_ids = ["candidate-a"]
    invariant_dimension_scores = {
        name: {
            "value": None,
            "unavailable_reason": "reversed invariant pass is unavailable",
        }
        for name in CRITERION_NAMES
    }
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "kind": RECORD_KIND,
        "decision": DECISION,
        "acceptance_authority": False,
        "baseline_established": False,
        "product_improvement_claim": False,
        "reference_sha256": reference_sha256,
        "candidate_sha256": candidate_sha256,
        "candidate_evaluator": {
            "version": candidate_evidence["evaluator_version"],
            "required_version": REQUIRED_CANDIDATE_EVALUATOR_VERSION,
            "status": "superseded_diagnostic",
            "current_evidence_available": False,
            "dimension_scores": {
                name: candidate_scores[name] for name in DIMENSION_NAMES
            },
        },
        "invariant_evaluator": {
            "version": invariant_evidence["evaluator_version"],
            "status": "partial",
            "completed_pass_ids": ["baseline-a"],
            "missing_pass_ids": missing_pass_ids,
            "position_balanced": False,
            "dimension_scores": invariant_dimension_scores,
            "completed_pass_diagnostic": {
                "criterion_scores": invariant_scores,
                "winner_diagnostic": diagnostic,
            },
        },
        "costs": costs,
        "claims": {
            "candidate_semantic_result_is_diagnostic": True,
            "candidate_semantic_result_is_current": False,
            "invariant_comparison_is_complete": False,
            "automatic_acceptance": False,
            "product_keep_decision": False,
        },
    }
