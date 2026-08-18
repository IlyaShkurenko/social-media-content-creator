from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LOOP_ROOT.parents[1]
EXPERIMENTS_ROOT = LOOP_ROOT / "experiments"
for _path in (REPO_ROOT, LOOP_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.services.creative.campaign import build_candidate_scorecard  # noqa: E402
from evals.candidate_judge import (  # noqa: E402
    CandidateJudgeResponse,
    EVALUATOR_VERSION as CANDIDATE_EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION as CANDIDATE_EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_MODE as CANDIDATE_OBSERVATION_MODE,
    _candidate_contract,
    _validate_response as validate_candidate_response,
    map_dimension_statuses,
    sha256_json as candidate_sha256_json,
)
from evals.invariant_judge import (  # noqa: E402
    CRITERION_NAMES,
    EVALUATOR_VERSION as INVARIANT_EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION as INVARIANT_EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_MODE as INVARIANT_OBSERVATION_MODE,
    InvariantJudgeResponse,
    _validate_response as validate_invariant_response,
    build_shared_invariant_contract,
    build_winner_diagnostic,
    map_invariant_statuses,
    sha256_json as invariant_sha256_json,
)


MANIFEST_SCHEMA_VERSION = 1
METRICS_SCHEMA_VERSION = "1.0"
RECORD_KIND = "candidate_evaluator_baseline"
DECISION = "evaluator_baseline_established"
INVARIANT_SCORECARD_DIMENSIONS = (
    "audiovisual_correctness",
    "product_brand_fidelity",
    "cta_clarity",
)
SUPPORTED_INVARIANT_EVALUATOR_VERSIONS = {
    "1.0.0",
    INVARIANT_EVALUATOR_VERSION,
}
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if str(key).lower() in _SENSITIVE_KEYS
                else _sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        try:
            path = Path(value)
            if not path.is_absolute():
                return value
            resolved = path.resolve()
            if REPO_ROOT.resolve() in resolved.parents:
                return f"<repo>/{resolved.relative_to(REPO_ROOT)}"
        except (OSError, RuntimeError, ValueError):
            pass
    return value


def _load_json_object(path: Path, *, label: str, sanitize: bool) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return _sanitize(payload) if sanitize else payload


def _managed_file(value: str, *, label: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else REPO_ROOT / raw
    resolved = candidate.resolve()
    if not resolved.is_file() or REPO_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"{label} must be a file inside {REPO_ROOT}: {resolved}")
    return resolved


def _experiment_path(value: str) -> Path:
    raw = Path(value)
    candidate = raw if raw.is_absolute() else EXPERIMENTS_ROOT / raw
    resolved = candidate.resolve()
    if EXPERIMENTS_ROOT.resolve() not in resolved.parents:
        raise ValueError(
            f"experiment must be a child of {EXPERIMENTS_ROOT}: {resolved}"
        )
    return resolved


def _source_document(path: Path, *, label: str, sanitize: bool) -> dict[str, Any]:
    content = _load_json_object(path, label=label, sanitize=sanitize)
    return {
        "source_path": str(path.relative_to(REPO_ROOT)),
        "source_sha256": sha256_file(path),
        "content_sha256": sha256_json(content),
        "content": content,
    }


def _validated_document(entry: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(entry, dict) or not isinstance(entry.get("content"), dict):
        raise ValueError(f"{label} must embed one JSON object")
    source_path = entry.get("source_path")
    if (
        not isinstance(source_path, str)
        or not source_path.strip()
        or Path(source_path).is_absolute()
        or ".." in Path(source_path).parts
    ):
        raise ValueError(f"{label} has an invalid managed source path")
    if not _is_sha256(entry.get("source_sha256")):
        raise ValueError(f"{label} has an invalid source hash")
    content = entry["content"]
    if entry.get("content_sha256") != sha256_json(content):
        raise ValueError(f"{label} embedded content hash changed")
    return content


def _validated_cost(budget: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(budget, dict):
        raise ValueError(f"{label} requires a paid budget record")
    operation_id = budget.get("operation_id")
    maximum = budget.get("preflight_maximum_microusd")
    charged = budget.get("charged_microusd")
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
        raise ValueError(f"{label} has an invalid conservative cost binding")
    return {
        "operation_id": operation_id,
        "charged_microusd": charged,
        "preflight_maximum_microusd": maximum,
    }


def _candidate_from_plan(
    plan: dict[str, Any],
    *,
    concept_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    candidates = plan.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("campaign plan requires candidates")
    matches = [
        item
        for item in candidates
        if isinstance(item, dict) and item.get("concept_id") == concept_id
    ]
    if len(matches) != 1:
        raise ValueError("candidate evidence must match exactly one campaign candidate")
    candidate = matches[0]
    candidate_id = str(candidate.get("candidate_id", "")).strip()
    concept = candidate.get("concept")
    storyboard = candidate.get("storyboard")
    if not candidate_id or not isinstance(concept, dict) or not isinstance(
        storyboard, dict
    ):
        raise ValueError("campaign candidate has an invalid identity or contract")
    return candidate_id, concept, storyboard


def _validate_candidate_evidence(
    payload: dict[str, Any],
    *,
    plan: dict[str, Any],
    candidate_sha256: str,
) -> tuple[str, dict[str, float | None], dict[str, Any]]:
    expected_header = {
        "schema_version": CANDIDATE_EVIDENCE_SCHEMA_VERSION,
        "observation_mode": CANDIDATE_OBSERVATION_MODE,
        "status": "complete",
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            raise ValueError(f"candidate evidence has a mismatched {key}")
    if payload.get("evaluator_version") != CANDIDATE_EVALUATOR_VERSION:
        raise ValueError("candidate evidence has an unsupported evaluator_version")
    if (
        not str(payload.get("requested_model", "")).strip()
        or not str(payload.get("model_version", "")).strip()
        or not _is_sha256(payload.get("prompt_sha256"))
        or not _is_sha256(payload.get("response_schema_sha256"))
    ):
        raise ValueError("candidate evidence has an invalid provider binding")
    provider = payload.get("provider")
    if not isinstance(provider, dict) or not str(
        provider.get("response_id", "")
    ).strip():
        raise ValueError("candidate evidence requires a provider response ID")
    concept_id = str(payload.get("concept_id", "")).strip()
    candidate_id, concept, storyboard = _candidate_from_plan(
        plan,
        concept_id=concept_id,
    )
    contract = _candidate_contract(concept, storyboard)
    bindings = {
        "concept_sha256": candidate_sha256_json(concept),
        "storyboard_sha256": candidate_sha256_json(storyboard),
        "contract_sha256": candidate_sha256_json(contract),
        "video_sha256": candidate_sha256,
    }
    for key, expected in bindings.items():
        if payload.get(key) != expected:
            raise ValueError(f"candidate evidence has a mismatched {key}")
    duration_ms = payload.get("video_duration_ms")
    if (
        not isinstance(duration_ms, int)
        or isinstance(duration_ms, bool)
        or duration_ms <= 0
    ):
        raise ValueError("candidate evidence requires a positive video duration")
    response = CandidateJudgeResponse.model_validate(payload.get("response"))
    validate_candidate_response(
        response,
        video_duration_seconds=duration_ms / 1000,
        contract=contract,
    )
    scores = map_dimension_statuses(response)
    if payload.get("dimension_scores") != scores:
        raise ValueError("candidate evidence has stale deterministic scores")
    cost = _validated_cost(payload.get("budget"), label="candidate evidence")
    return candidate_id, scores, cost


def _duration_for_label(
    payload: dict[str, Any],
    *,
    label: str,
    candidate_duration_seconds: float,
) -> float:
    if payload["order"][label] == payload["candidate_sha256"]:
        return candidate_duration_seconds
    contract_duration = payload.get("contract_duration_seconds")
    if isinstance(contract_duration, (int, float)) and contract_duration > 0:
        return float(contract_duration)
    return candidate_duration_seconds


def _validate_invariant_evidence(
    payload: dict[str, Any],
    *,
    scenario: dict[str, Any],
    scenario_source_sha256: str,
    reference_sha256: str,
    candidate_sha256: str,
    candidate_duration_seconds: float,
) -> tuple[dict[str, float | None], dict[str, Any], list[dict[str, Any]]]:
    expected_header = {
        "schema_version": INVARIANT_EVIDENCE_SCHEMA_VERSION,
        "observation_mode": INVARIANT_OBSERVATION_MODE,
        "status": "complete",
        "scenario_id": scenario.get("id"),
        "scenario_sha256": scenario_source_sha256,
        "baseline_sha256": reference_sha256,
        "candidate_sha256": candidate_sha256,
        "self_comparison": False,
    }
    for key, expected in expected_header.items():
        if payload.get(key) != expected:
            raise ValueError(f"invariant evidence has a mismatched {key}")
    if payload.get("evaluator_version") not in SUPPORTED_INVARIANT_EVALUATOR_VERSIONS:
        raise ValueError("invariant evidence has an unsupported evaluator_version")
    requested_model = str(payload.get("requested_model", "")).strip()
    if (
        not requested_model
        or not _is_sha256(payload.get("prompt_sha256"))
        or not _is_sha256(payload.get("response_schema_sha256"))
    ):
        raise ValueError("invariant evidence has an invalid provider binding")
    contract = build_shared_invariant_contract(scenario)
    if payload.get("shared_contract_sha256") != invariant_sha256_json(contract):
        raise ValueError("invariant evidence has a stale shared contract")
    passes = payload.get("passes")
    if not isinstance(passes, list) or len(passes) != 2:
        raise ValueError("invariant evidence requires two position-balanced passes")
    expected_orders = {
        (("A", reference_sha256), ("B", candidate_sha256)),
        (("A", candidate_sha256), ("B", reference_sha256)),
    }
    actual_orders: set[tuple[tuple[str, str], ...]] = set()
    values: dict[str, list[float | None]] = {
        name: [] for name in CRITERION_NAMES
    }
    costs: list[dict[str, Any]] = []
    for index, item in enumerate(passes):
        if not isinstance(item, dict):
            raise ValueError("invariant pass must be an object")
        order = item.get("order")
        if not isinstance(order, dict) or set(order) != {"A", "B"}:
            raise ValueError("invariant pass has an invalid A/B order")
        actual_orders.add(tuple(sorted((key, str(value)) for key, value in order.items())))
        candidate_labels = [
            label for label in ("A", "B") if order[label] == candidate_sha256
        ]
        if len(candidate_labels) != 1:
            raise ValueError("invariant pass does not bind exactly one candidate")
        expected_pass_id = "baseline-a" if order["A"] == reference_sha256 else "candidate-a"
        if item.get("pass_id") != expected_pass_id:
            raise ValueError("invariant pass ID does not match its A/B order")
        provider = item.get("provider")
        if (
            not isinstance(provider, dict)
            or provider.get("requested_model") != requested_model
            or not str(provider.get("model_version", "")).strip()
            or not str(provider.get("response_id", "")).strip()
        ):
            raise ValueError("invariant pass has an invalid provider binding")
        response = InvariantJudgeResponse.model_validate(item.get("response"))
        validate_invariant_response(
            response,
            contract=contract,
            video_duration_seconds_by_label={
                label: _duration_for_label(
                    {**payload, "order": order},
                    label=label,
                    candidate_duration_seconds=candidate_duration_seconds,
                )
                for label in ("A", "B")
            },
        )
        mapped = map_invariant_statuses(response)
        if item.get("criterion_scores") != mapped:
            raise ValueError("invariant evidence has stale deterministic scores")
        score_key = "video_a" if candidate_labels[0] == "A" else "video_b"
        for name in CRITERION_NAMES:
            values[name].append(mapped[score_key][name])
        costs.append(
            _validated_cost(item.get("budget"), label=f"invariant pass {index + 1}")
        )
    if actual_orders != expected_orders:
        raise ValueError("invariant passes are not exact reversed A/B orders")
    if len({item["operation_id"] for item in costs}) != len(costs):
        raise ValueError("invariant passes reuse a paid operation ID")
    expected_model_versions = sorted(
        {str(item["provider"]["model_version"]) for item in passes}
    )
    if payload.get("model_versions") != expected_model_versions:
        raise ValueError("invariant evidence has stale model-version bindings")
    scores = {
        name: (
            sum(float(value) for value in criterion_values)
            / len(criterion_values)
            if all(value is not None for value in criterion_values)
            else None
        )
        for name, criterion_values in values.items()
    }
    expected_diagnostic = build_winner_diagnostic(
        passes,
        baseline_sha256=reference_sha256,
        candidate_sha256=candidate_sha256,
    )
    if payload.get("winner_diagnostic") != expected_diagnostic:
        raise ValueError("invariant evidence has a stale winner diagnostic")
    return scores, expected_diagnostic, costs


def _artifact_path(experiment: Path, entry: Any, *, expected: str) -> Path:
    if not isinstance(entry, dict) or entry.get("snapshot_path") != expected:
        raise ValueError(f"baseline record requires exact {expected}")
    source_path = entry.get("source_path")
    if (
        not isinstance(source_path, str)
        or not source_path.strip()
        or Path(source_path).is_absolute()
        or ".." in Path(source_path).parts
    ):
        raise ValueError("baseline artifact has an invalid managed source path")
    path = (experiment / expected).resolve()
    if experiment.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"baseline artifact is missing: {path}")
    actual = sha256_file(path)
    for key in ("source_sha256", "snapshot_sha256"):
        if entry.get(key) != actual:
            raise ValueError(f"baseline artifact {key} changed: {path}")
    return path


def rebuild_candidate_evaluator_baseline_metrics(
    manifest: dict[str, Any],
    *,
    experiment: Path,
) -> dict[str, Any]:
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported evaluator-baseline manifest schema")
    if manifest.get("kind") != RECORD_KIND:
        raise ValueError("not a candidate evaluator-baseline record")
    lifecycle = manifest.get("lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("status") != "baseline_established":
        raise ValueError("candidate evaluator baseline is not established")
    if (
        lifecycle.get("decision") != DECISION
        or lifecycle.get("acceptance_authority") is not False
    ):
        raise ValueError("candidate evaluator baseline overclaims acceptance authority")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("candidate evaluator baseline requires artifacts")
    reference = _artifact_path(
        experiment,
        artifacts.get("reference"),
        expected="artifacts/reference.mp4",
    )
    candidate = _artifact_path(
        experiment,
        artifacts.get("candidate"),
        expected="artifacts/video.mp4",
    )
    reference_sha256 = sha256_file(reference)
    candidate_sha256 = sha256_file(candidate)
    if reference_sha256 == candidate_sha256:
        raise ValueError("reference and candidate evaluator artifacts must be distinct")

    scenario_entry = manifest.get("scenario")
    plan_entry = manifest.get("campaign_plan")
    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("candidate evaluator baseline requires embedded evidence")
    scenario = _validated_document(scenario_entry, label="scenario")
    plan = _validated_document(plan_entry, label="campaign plan")
    candidate_evidence = _validated_document(
        evidence.get("candidate"),
        label="candidate evidence",
    )
    invariant_evidence = _validated_document(
        evidence.get("invariant"),
        label="invariant evidence",
    )
    candidate_id, semantic_scores, candidate_cost = _validate_candidate_evidence(
        candidate_evidence,
        plan=plan,
        candidate_sha256=candidate_sha256,
    )
    duration_ms = candidate_evidence["video_duration_ms"]
    invariant_scores, winner_diagnostic, invariant_costs = (
        _validate_invariant_evidence(
            invariant_evidence,
            scenario=scenario,
            scenario_source_sha256=scenario_entry["source_sha256"],
            reference_sha256=reference_sha256,
            candidate_sha256=candidate_sha256,
            candidate_duration_seconds=duration_ms / 1000,
        )
    )

    labels = manifest.get("product_owner_labels")
    if not isinstance(labels, list) or len(labels) != 2:
        raise ValueError("baseline record requires reference and candidate owner labels")
    by_role = {
        item.get("role"): item for item in labels if isinstance(item, dict)
    }
    if set(by_role) != {"reference", "candidate"}:
        raise ValueError("baseline record has invalid product-owner label roles")
    expected_label_hashes = {
        "reference": reference_sha256,
        "candidate": candidate_sha256,
    }
    for role, item in by_role.items():
        allowed_labels = {"accept"} if role == "reference" else {
            "accept",
            "reject",
            "pending",
        }
        if item.get("label") not in allowed_labels:
            raise ValueError("product-owner label has an invalid state")
        if item.get("artifact_sha256") != expected_label_hashes[role]:
            raise ValueError("product-owner label is bound to the wrong artifact")
        if not str(item.get("reviewer", "")).strip() or not str(
            item.get("reason", "")
        ).strip():
            raise ValueError("product-owner label requires reviewer and reason")
    if by_role["reference"]["label"] != "accept":
        raise ValueError("the evaluator reference must be product-owner accepted")

    measured = dict(semantic_scores)
    measured.update(
        {name: invariant_scores[name] for name in INVARIANT_SCORECARD_DIMENSIONS}
    )
    candidate_label = by_role["candidate"]["label"]
    measured["human_acceptance"] = (
        1.0
        if candidate_label == "accept"
        else 0.0
        if candidate_label == "reject"
        else None
    )
    scorecard = build_candidate_scorecard(
        candidate_id=candidate_id,
        eligible=True,
        measured=measured,
    ).model_dump(mode="json")
    if candidate_label == "pending":
        scorecard["dimensions"]["human_acceptance"]["unavailable_reason"] = (
            "exact MP4 product-owner review is pending"
        )

    all_costs = [candidate_cost, *invariant_costs]
    costs = {
        "accounting": "conservative_recorded_charge",
        "operations": all_costs,
        "total_charged_microusd": sum(
            item["charged_microusd"] for item in all_costs
        ),
        "total_preflight_maximum_microusd": sum(
            item["preflight_maximum_microusd"] for item in all_costs
        ),
    }
    recorded_costs = manifest.get("evaluator_costs")
    if recorded_costs is not None and recorded_costs != costs:
        raise ValueError("recorded evaluator costs differ from paid evidence")

    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "kind": RECORD_KIND,
        "decision": DECISION,
        "acceptance_authority": False,
        "candidate_id": candidate_id,
        "reference_sha256": reference_sha256,
        "candidate_sha256": candidate_sha256,
        "evaluator_versions": {
            "candidate": candidate_evidence["evaluator_version"],
            "invariant": invariant_evidence["evaluator_version"],
        },
        "source_bindings": {
            "scenario": {
                key: scenario_entry[key]
                for key in ("source_path", "source_sha256", "content_sha256")
            },
            "campaign_plan": {
                key: plan_entry[key]
                for key in ("source_path", "source_sha256", "content_sha256")
            },
            "candidate_evidence": {
                key: evidence["candidate"][key]
                for key in ("source_path", "source_sha256", "content_sha256")
            },
            "invariant_evidence": {
                key: evidence["invariant"][key]
                for key in ("source_path", "source_sha256", "content_sha256")
            },
            "reference_artifact": artifacts["reference"]["source_path"],
            "candidate_artifact": artifacts["candidate"]["source_path"],
        },
        "scorecard": scorecard,
        "invariant_diagnostics": {
            "transition_mechanics": invariant_scores["transition_mechanics"],
            "professional_finish": invariant_scores["professional_finish"],
            "winner_diagnostic": winner_diagnostic,
        },
        "evaluator_costs": costs,
        "claims": {
            "product_improvement": False,
            "keep_decision": False,
            "purpose": "calibrate and replay the evaluator against owner-labelled artifacts",
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_record_readme(
    manifest: dict[str, Any],
    metrics: dict[str, Any],
) -> str:
    """Describe exactly what the immutable evaluator baseline does and does not prove."""

    lifecycle = manifest["lifecycle"]
    artifacts = manifest["artifacts"]
    labels = {
        item["role"]: item for item in manifest["product_owner_labels"]
    }
    versions = metrics["evaluator_versions"]
    human_acceptance = metrics["scorecard"]["dimensions"]["human_acceptance"]
    human_acceptance_value = json.dumps(human_acceptance["value"])
    return f"""# Candidate evaluator baseline

This record calibrates deterministic evaluator replay against two exact MP4
artifacts. It is not a product-improvement experiment and it is not a keep
decision.

## Lifecycle and authority

- Lifecycle: `{lifecycle["status"]}`
- Decision: `{metrics["decision"]}`
- Acceptance authority: `{str(metrics["acceptance_authority"]).lower()}`
- Product improvement assessed: `{str(metrics["claims"]["product_improvement"]).lower()}`
- Keep decision made: `{str(metrics["claims"]["keep_decision"]).lower()}`

## Versioned evaluators

- Candidate semantic evaluator: `{versions["candidate"]}`
- Shared invariant evaluator: `{versions["invariant"]}`
- Record schema: `{manifest["schema_version"]}`
- Scorecard schema: `{metrics["schema_version"]}`

## Exact artifacts and product-owner labels

- Accepted reference baseline: `{artifacts["reference"]["source_path"]}`
  - SHA-256: `{metrics["reference_sha256"]}`
  - Label: `{labels["reference"]["label"]}`
  - Reviewer: `{labels["reference"]["reviewer"]}`
  - Reason: {labels["reference"]["reason"]}
- Candidate: `{artifacts["candidate"]["source_path"]}`
  - SHA-256: `{metrics["candidate_sha256"]}`
  - Label: `{labels["candidate"]["label"]}`
  - Reviewer: `{labels["candidate"]["reviewer"]}`
  - Reason: {labels["candidate"]["reason"]}
  - Human-acceptance value: `{human_acceptance_value}`
  - Availability: {human_acceptance["unavailable_reason"] or "measured"}

`artifacts/reference.mp4` and `artifacts/video.mp4` are immutable snapshots.
`inputs.json` embeds sanitized evidence and source hashes; `metrics.json` is
recomputed from those exact inputs during offline replay.
"""


def record_baseline(args: argparse.Namespace) -> Path:
    experiment = _experiment_path(args.experiment)
    scenario_path = _managed_file(args.scenario, label="scenario")
    plan_path = _managed_file(args.campaign_plan, label="campaign plan")
    reference_path = _managed_file(args.reference_video, label="reference video")
    candidate_path = _managed_file(args.candidate_video, label="candidate video")
    candidate_evidence_path = _managed_file(
        args.candidate_evidence,
        label="candidate evidence",
    )
    invariant_evidence_path = _managed_file(
        args.invariant_evidence,
        label="invariant evidence",
    )
    if args.reference_label != "accept":
        raise ValueError("reference-label must be accept")
    if experiment.exists():
        raise ValueError("refusing to overwrite an existing experiment record")

    reference_sha256 = sha256_file(reference_path)
    candidate_sha256 = sha256_file(candidate_path)
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": RECORD_KIND,
        "lifecycle": {
            "status": "baseline_established",
            "established_at": datetime.now(timezone.utc).isoformat(),
            "decision": DECISION,
            "acceptance_authority": False,
        },
        "scenario": _source_document(scenario_path, label="scenario", sanitize=False),
        "campaign_plan": _source_document(
            plan_path,
            label="campaign plan",
            sanitize=False,
        ),
        "artifacts": {
            "reference": {
                "source_path": str(reference_path.relative_to(REPO_ROOT)),
                "source_sha256": reference_sha256,
                "snapshot_path": "artifacts/reference.mp4",
                "snapshot_sha256": reference_sha256,
            },
            "candidate": {
                "source_path": str(candidate_path.relative_to(REPO_ROOT)),
                "source_sha256": candidate_sha256,
                "snapshot_path": "artifacts/video.mp4",
                "snapshot_sha256": candidate_sha256,
            },
        },
        "evidence": {
            "candidate": _source_document(
                candidate_evidence_path,
                label="candidate evidence",
                sanitize=True,
            ),
            "invariant": _source_document(
                invariant_evidence_path,
                label="invariant evidence",
                sanitize=True,
            ),
        },
        "product_owner_labels": [
            {
                "role": "reference",
                "label": args.reference_label,
                "artifact_sha256": reference_sha256,
                "reviewer": args.reviewer,
                "reason": args.reference_reason,
            },
            {
                "role": "candidate",
                "label": args.candidate_label,
                "artifact_sha256": candidate_sha256,
                "reviewer": args.reviewer,
                "reason": args.candidate_reason,
            },
        ],
    }

    experiment.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{experiment.name}.staging-",
            dir=experiment.parent,
        )
    )
    try:
        artifacts = staging / "artifacts"
        artifacts.mkdir()
        shutil.copy2(reference_path, artifacts / "reference.mp4")
        shutil.copy2(candidate_path, artifacts / "video.mp4")
        metrics = rebuild_candidate_evaluator_baseline_metrics(
            manifest,
            experiment=staging,
        )
        manifest["evaluator_costs"] = metrics["evaluator_costs"]
        # Rebuild after the derived cost binding is frozen into the manifest.
        metrics = rebuild_candidate_evaluator_baseline_metrics(
            manifest,
            experiment=staging,
        )
        _write_json(staging / "inputs.json", manifest)
        _write_json(staging / "metrics.json", metrics)
        (staging / "README.md").write_text(
            build_record_readme(manifest, metrics),
            encoding="utf-8",
        )
        if experiment.exists():
            raise ValueError("refusing to overwrite an existing experiment record")
        staging.replace(experiment)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return experiment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record a replayable, owner-labelled candidate evaluator baseline"
    )
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--campaign-plan", required=True)
    parser.add_argument("--reference-video", required=True)
    parser.add_argument("--candidate-video", required=True)
    parser.add_argument("--candidate-evidence", required=True)
    parser.add_argument("--invariant-evidence", required=True)
    parser.add_argument("--reference-label", choices=("accept", "reject"), default="accept")
    parser.add_argument(
        "--candidate-label",
        choices=("accept", "reject", "pending"),
        required=True,
    )
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reference-reason", required=True)
    parser.add_argument("--candidate-reason", required=True)
    return parser.parse_args()


def main() -> int:
    experiment = record_baseline(parse_args())
    print(f"recorded={experiment.relative_to(LOOP_ROOT)}")
    print(f"decision={DECISION}")
    print("acceptance_authority=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
