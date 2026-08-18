from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from google import genai
from google.genai import _common, models as genai_models, types
from pydantic import Field

from app.services.creative.budget import BudgetSnapshot
from app.services.creative.campaign import (
    CampaignBrief,
    CampaignPlan,
    HypothesisBatch,
    HypothesisBatchResponse,
    build_campaign_plan,
    build_hypothesis_prompt,
)
from app.services.creative.pipeline import build_runway_request
from app.services.creative.runway import (
    RunwayAdapter,
    build_runway_payload,
    estimate_runway_cost_microusd,
)
from app.services.creative.storyboard import (
    CreativeModel,
    Storyboard,
    StoryboardValidationError,
    resolve_managed_asset,
    validate_storyboard,
)


PREFLIGHT_SCHEMA_VERSION = "1.0"
PLANNER_OPERATION_SUFFIX = "gemini-concepts-v5"
RUNWAY_OFFICIAL_BASE_URL = "https://api.dev.runwayml.com"
RUNWAY_REQUIRED_API_VERSION = "2024-11-06"
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_GEMINI_SCHEMA_KEYS = {
    "$defs",
    "$ref",
    "additionalProperties",
    "additional_properties",
    "const",
    "exclusiveMaximum",
    "exclusiveMinimum",
}


class CampaignPreflightError(RuntimeError):
    """Raised when deterministic campaign preflight cannot authorize a call."""


class CampaignPreflightReport(CreativeModel):
    schema_version: Literal["1.0"] = PREFLIGHT_SCHEMA_VERSION
    status: Literal["passed"] = "passed"
    stage: Literal["planner_ready", "generation_ready"]
    planning_mode: Literal["live", "replay"]
    preflight_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_prefix: str
    semantic_manifest: dict[str, Any]
    checks: dict[str, Any]
    budget: dict[str, Any]


class TemporalPreflightContract(CreativeModel):
    evaluator_version: str = Field(min_length=1)
    evidence_schema_version: int = Field(ge=1)
    response_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_fps: int = Field(gt=0)
    frames_per_strip: int = Field(gt=1)
    max_output_tokens: int = Field(gt=0)
    event_types: list[str] = Field(min_length=1)
    model: str = Field(min_length=1)
    scene_id: Literal["hook"] = "hook"
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(payload: Any) -> str:
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_gemini_concept_config() -> types.GenerateContentConfig:
    """Return the one config shared by preflight and live concept planning."""

    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=HypothesisBatchResponse,
        max_output_tokens=8192,
        thinking_config=types.ThinkingConfig(
            include_thoughts=False,
            thinking_level="medium",
        ),
    )


def build_gemini_concept_transport(
    *,
    model: str,
    prompt: str,
) -> dict[str, Any]:
    """Compile the real Gemini Developer API request without sending it."""

    # The SDK's schema transformer uses the client mode to reject fields that
    # Gemini Developer API does not accept. Client construction is local; only
    # Models.generate_content performs a request.
    client = genai.Client(api_key="offline-preflight-placeholder")
    try:
        parameters = types._GenerateContentParameters(
            model=model,
            contents=prompt,
            config=build_gemini_concept_config(),
        )
        payload = genai_models._GenerateContentParameters_to_mldev(
            client._api_client,
            parameters,
            None,
            parameters,
        )
        payload = _common.convert_to_dict(payload)
        payload = _common.encode_unserializable_types(payload)
    finally:
        client.close()
    if not isinstance(payload, dict):
        raise CampaignPreflightError("Gemini transport did not serialize to an object")
    return payload


def _find_forbidden_keys(payload: Any, *, path: str = "$") -> list[str]:
    failures: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            child = f"{path}.{key}"
            if key in _FORBIDDEN_GEMINI_SCHEMA_KEYS:
                failures.append(child)
            failures.extend(_find_forbidden_keys(value, path=child))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            failures.extend(_find_forbidden_keys(value, path=f"{path}[{index}]"))
    return failures


def _validate_gemini_transport(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        schema = payload["generationConfig"]["responseSchema"]
        schema_version = schema["properties"]["schema_version"]
    except (KeyError, TypeError) as exc:
        raise CampaignPreflightError(
            "Gemini transport is missing the structured response schema"
        ) from exc
    forbidden = _find_forbidden_keys(schema)
    if forbidden:
        raise CampaignPreflightError(
            "Gemini transport contains unsupported schema fields: "
            + ", ".join(forbidden)
        )
    if schema_version.get("type") != "STRING" or schema_version.get("enum") != [
        "1.0"
    ]:
        raise CampaignPreflightError(
            "Gemini schema_version must serialize as STRING enum ['1.0']"
        )
    return schema


def _referenced_asset_ids(storyboard: Storyboard) -> list[str]:
    result: list[str] = []
    for scene in storyboard.scenes:
        for layer in [scene.media_plan.base, *scene.media_plan.overlays]:
            if layer.asset_id and layer.asset_id not in result:
                result.append(layer.asset_id)
    return result


def _asset_evidence(
    *,
    brief: CampaignBrief,
    storyboard: Storyboard,
    asset_root: Path,
) -> tuple[list[dict[str, str]], str]:
    managed_root = asset_root.resolve()
    brand_kit = managed_root / "brand-kit.json"
    if not brand_kit.is_file():
        raise CampaignPreflightError(
            f"managed brand kit does not exist: {brand_kit}"
        )
    referenced = _referenced_asset_ids(storyboard)
    undeclared = sorted(set(referenced) - set(brief.available_asset_ids))
    if undeclared:
        raise CampaignPreflightError(
            "storyboard references assets absent from the campaign brief: "
            + ", ".join(undeclared)
        )
    evidence: list[dict[str, str]] = []
    for asset_id in dict.fromkeys([*brief.available_asset_ids, *referenced]):
        try:
            path = resolve_managed_asset(managed_root, asset_id)
        except StoryboardValidationError as exc:
            raise CampaignPreflightError(
                f"invalid managed campaign asset {asset_id!r}: {exc}"
            ) from exc
        evidence.append({"asset_id": asset_id, "sha256": _sha256_file(path)})
    return evidence, _sha256_file(brand_kit)


def _implementation_fingerprints(
    *,
    temporal_implementation_sha256: str,
) -> dict[str, str]:
    from app.services.creative import budget, campaign, pipeline, runway

    modules = {
        "budget": Path(budget.__file__).resolve(),
        "campaign": Path(campaign.__file__).resolve(),
        "campaign_preflight": Path(__file__).resolve(),
        "pipeline": Path(pipeline.__file__).resolve(),
        "runway": Path(runway.__file__).resolve(),
    }
    return {
        **{name: _sha256_file(path) for name, path in modules.items()},
        "temporal_judge": temporal_implementation_sha256,
    }


def _validate_runway_provider(
    *,
    base_url: str,
    api_version: str,
) -> dict[str, str]:
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    official = urlparse(RUNWAY_OFFICIAL_BASE_URL)
    if (
        RunwayAdapter.BASE_URL != RUNWAY_OFFICIAL_BASE_URL
        or RunwayAdapter.API_VERSION != RUNWAY_REQUIRED_API_VERSION
    ):
        raise CampaignPreflightError(
            "Runway adapter provider contract drifted from the audited endpoint"
        )
    if (
        parsed.scheme != "https"
        or parsed.hostname != official.hostname
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or normalized != RUNWAY_OFFICIAL_BASE_URL
    ):
        raise CampaignPreflightError(
            "Runway base URL must be the official HTTPS API endpoint"
        )
    normalized_version = api_version.strip()
    if normalized_version != RUNWAY_REQUIRED_API_VERSION:
        raise CampaignPreflightError("Runway API version is unsupported")
    return {
        "base_url": normalized,
        "api_version": normalized_version,
    }


def _validate_temporal_contract(
    contract: TemporalPreflightContract,
    *,
    gemini_model: str,
) -> dict[str, Any]:
    if contract.model != gemini_model:
        raise CampaignPreflightError(
            "temporal evaluator model must match the configured Gemini model"
        )
    if contract.end_seconds <= contract.start_seconds:
        raise CampaignPreflightError("temporal evaluator scene range is invalid")
    event_types = [item.strip() for item in contract.event_types]
    if any(not item for item in event_types) or len(event_types) != len(
        set(event_types)
    ):
        raise CampaignPreflightError(
            "temporal evaluator event types must be non-empty and unique"
        )
    normalized = contract.model_dump(mode="json")
    normalized["event_types"] = event_types
    return normalized


def _candidate_evidence(plan: CampaignPlan) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in plan.candidates:
        payload = build_runway_payload(candidate.request)
        prompt_chars = len(candidate.request.prompt_text)
        if prompt_chars > 1000:
            raise CampaignPreflightError(
                f"Runway prompt for {candidate.candidate_id!r} exceeds 1000 characters"
            )
        result.append(
            {
                "candidate_id": candidate.candidate_id,
                "operation_id": candidate.operation_id,
                "storyboard_sha256": _sha256_json(
                    candidate.storyboard.model_dump(mode="json")
                ),
                "request_sha256": _sha256_json(
                    candidate.request.model_dump(mode="json")
                ),
                "payload_sha256": _sha256_json(payload),
                "prompt_chars": prompt_chars,
                "estimated_cost_microusd": candidate.estimated_cost_microusd,
            }
        )
    return result


def _ensure_unique_operations(operation_ids: list[str]) -> None:
    if any(not item.strip() for item in operation_ids):
        raise CampaignPreflightError("campaign operation IDs must be non-empty")
    if len(operation_ids) != len(set(operation_ids)):
        raise CampaignPreflightError("campaign operation IDs must be unique")


def build_campaign_preflight(
    *,
    brief: CampaignBrief,
    template_storyboard: Storyboard,
    asset_root: Path,
    operation_prefix: str,
    gemini_model: str,
    planning_mode: Literal["live", "replay"],
    budget_snapshot: BudgetSnapshot,
    planner_maximum_cost_microusd: int,
    temporal_maximum_cost_microusd: int,
    runway_base_url: str,
    runway_api_version: str,
    temporal_contract: TemporalPreflightContract,
    concepts: HypothesisBatch | None = None,
    orchestrator_sha256: str | None = None,
) -> CampaignPreflightReport:
    """Build a deterministic, non-networking and non-reserving campaign gate."""

    if not operation_prefix.strip():
        raise CampaignPreflightError("operation prefix is required")
    if brief.content_language != "en-US":
        raise CampaignPreflightError("campaign preflight requires en-US content")
    if planner_maximum_cost_microusd <= 0:
        raise CampaignPreflightError("planner maximum cost must be positive")
    if temporal_maximum_cost_microusd <= 0:
        raise CampaignPreflightError("temporal maximum cost must be positive")
    if planning_mode == "replay" and concepts is None:
        raise CampaignPreflightError("replay preflight requires exact concepts")
    if planning_mode == "live" and concepts is None:
        stage: Literal["planner_ready", "generation_ready"] = "planner_ready"
    else:
        stage = "generation_ready"
    runway_provider = _validate_runway_provider(
        base_url=runway_base_url,
        api_version=runway_api_version,
    )
    temporal_evaluator = _validate_temporal_contract(
        temporal_contract,
        gemini_model=gemini_model,
    )
    storyboard = validate_storyboard(template_storyboard)
    prompt = build_hypothesis_prompt(brief, include_json_schema=False)
    gemini_transport = build_gemini_concept_transport(
        model=gemini_model,
        prompt=prompt,
    )
    gemini_schema = _validate_gemini_transport(gemini_transport)
    assets, brand_kit_sha256 = _asset_evidence(
        brief=brief,
        storyboard=storyboard,
        asset_root=asset_root,
    )

    if concepts is None:
        prototype = build_runway_request(storyboard)
        prototype_payload = build_runway_payload(prototype)
        runway_cost = estimate_runway_cost_microusd(prototype)
        candidate_count = brief.concept_count
        candidate_records = [
            {
                "candidate_id": "post-planner-deferred",
                "operation_id": "post-planner-deferred",
                "storyboard_sha256": _sha256_json(
                    storyboard.model_dump(mode="json")
                ),
                "request_sha256": _sha256_json(prototype.model_dump(mode="json")),
                "payload_sha256": _sha256_json(prototype_payload),
                "prompt_chars": len(prototype.prompt_text),
                "estimated_cost_microusd": runway_cost,
                "candidate_count": candidate_count,
            }
        ]
        runway_maximum = runway_cost * candidate_count
        operation_ids = [f"{operation_prefix}-{PLANNER_OPERATION_SUFFIX}"]
        concepts_sha256 = None
        campaign_plan_sha256 = None
    else:
        if concepts.product_name != brief.product_name:
            raise CampaignPreflightError("concept batch changed the campaign product")
        if concepts.content_language != brief.content_language:
            raise CampaignPreflightError("concept batch changed the campaign language")
        if len(concepts.concepts) != brief.concept_count:
            raise CampaignPreflightError("concept count does not match the campaign brief")
        plan = build_campaign_plan(
            concepts,
            template_storyboard=storyboard,
            operation_prefix=operation_prefix,
        )
        candidate_records = _candidate_evidence(plan)
        runway_maximum = plan.total_estimated_cost_microusd
        candidate_count = len(plan.candidates)
        operation_ids = [candidate.operation_id for candidate in plan.candidates]
        operation_ids.extend(
            f"{operation_prefix}-{candidate.candidate_id}-temporal"
            for candidate in plan.candidates
        )
        if planning_mode == "live":
            operation_ids.append(f"{operation_prefix}-{PLANNER_OPERATION_SUFFIX}")
        concepts_sha256 = _sha256_json(concepts.model_dump(mode="json"))
        campaign_plan_sha256 = _sha256_json(plan.model_dump(mode="json"))
    _ensure_unique_operations(operation_ids)

    planner_maximum = (
        planner_maximum_cost_microusd if planning_mode == "live" else 0
    )
    temporal_maximum = temporal_maximum_cost_microusd * candidate_count
    campaign_maximum = planner_maximum + runway_maximum + temporal_maximum
    required_remaining = runway_maximum + temporal_maximum
    if stage == "planner_ready":
        required_remaining += planner_maximum
    if budget_snapshot.remaining_microusd < required_remaining:
        raise CampaignPreflightError(
            "iteration budget exceeded during campaign preflight: "
            f"required {required_remaining} micro-USD, remaining "
            f"{budget_snapshot.remaining_microusd} micro-USD"
        )

    semantic_manifest: dict[str, Any] = {
        "stage": stage,
        "planning_mode": planning_mode,
        "operation_prefix": operation_prefix,
        "brief_sha256": _sha256_json(brief.model_dump(mode="json")),
        "storyboard_sha256": _sha256_json(storyboard.model_dump(mode="json")),
        "brand_kit_sha256": brand_kit_sha256,
        "assets": assets,
        "concepts_sha256": concepts_sha256,
        "campaign_plan_sha256": campaign_plan_sha256,
        "candidate_requests": candidate_records,
        "operation_ids": operation_ids,
        "gemini_model": gemini_model,
        "gemini_prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "gemini_transport_sha256": _sha256_json(gemini_transport),
        "gemini_schema_sha256": _sha256_json(gemini_schema),
        "google_genai_version": importlib.metadata.version("google-genai"),
        "runway_provider": runway_provider,
        "temporal_evaluator": temporal_evaluator,
        "implementation_sha256": _implementation_fingerprints(
            temporal_implementation_sha256=temporal_contract.implementation_sha256
        ),
        "orchestrator_sha256": orchestrator_sha256,
        "cost_contract": {
            "planner_maximum_microusd": planner_maximum,
            "runway_maximum_microusd": runway_maximum,
            "temporal_maximum_microusd": temporal_maximum,
            "campaign_maximum_microusd": campaign_maximum,
            "required_remaining_microusd": required_remaining,
        },
    }
    preflight_id = _sha256_json(semantic_manifest)
    return CampaignPreflightReport(
        stage=stage,
        planning_mode=planning_mode,
        preflight_id=preflight_id,
        operation_prefix=operation_prefix,
        semantic_manifest=semantic_manifest,
        checks={
            "gemini_transport": "passed",
            "managed_assets": "passed",
            "runway_prompt_limit": "passed",
            "runway_pricing": "passed",
            "runway_official_endpoint": "passed",
            "runway_api_version": "passed",
            "temporal_evaluator_contract": "passed",
            "operation_identities": "passed",
            "budget": "passed",
            "exact_concepts": "passed" if concepts is not None else "deferred",
        },
        budget={
            **semantic_manifest["cost_contract"],
            "scope_id": budget_snapshot.scope_id,
            "cap_microusd": budget_snapshot.cap_microusd,
            "reserved_microusd": budget_snapshot.reserved_microusd,
            "charged_microusd": budget_snapshot.charged_microusd,
            "remaining_microusd": budget_snapshot.remaining_microusd,
            "check_type": "point_in_time_non_reserving",
        },
    )


def require_matching_preflight(
    *,
    stored_preflight_id: str,
    current_preflight_id: str,
) -> None:
    """Fail closed unless a persisted preflight exactly matches current inputs."""

    if not _HASH_PATTERN.fullmatch(stored_preflight_id or ""):
        raise CampaignPreflightError("campaign preflight is missing or invalid")
    if not _HASH_PATTERN.fullmatch(current_preflight_id or ""):
        raise CampaignPreflightError("current campaign preflight is invalid")
    if not hmac.compare_digest(stored_preflight_id, current_preflight_id):
        raise CampaignPreflightError(
            "campaign preflight is stale for the current semantic inputs"
        )
