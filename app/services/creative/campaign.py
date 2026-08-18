from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.services.creative.pipeline import build_runway_request
from app.services.creative.runway import (
    RunwayJob,
    RunwayVideoRequest,
    estimate_runway_cost_microusd,
)
from app.services.creative.storyboard import (
    CreativeModel,
    Storyboard,
    StoryboardScene,
    VisualIntent,
    validate_storyboard,
)
from app.services.creative.temporal import temporal_candidate_is_eligible


class CampaignPlanningError(RuntimeError):
    """Raised when a concept batch is ambiguous or unsafe to execute."""


class CampaignBrief(CreativeModel):
    product_name: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    product_facts: list[str] = Field(min_length=1)
    available_asset_ids: list[str] = Field(default_factory=list)
    content_language: str = "en-US"
    concept_count: int = Field(default=3, ge=3, le=5)


class HookBeat(CreativeModel):
    start_seconds: float = Field(ge=0, le=5)
    # Gemini's response-schema dialect rejects exclusiveMinimum. The stricter
    # end > start invariant remains enforced locally by _validate_hook_beats.
    end_seconds: float = Field(ge=0, le=5)
    visible_action: str = Field(min_length=1)
    expected_evidence: list[str] = Field(min_length=1)


class AdvertisingConcept(CreativeModel):
    concept_id: str = Field(min_length=1, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    hypothesis: str = Field(min_length=1)
    audience_problem: str = Field(min_length=1)
    target_emotion: str = Field(min_length=1)
    emotional_arc: str = Field(min_length=1)
    hook_setting: str = Field(min_length=1)
    hook_camera: str = Field(min_length=1)
    hook_voiceover: str = Field(min_length=1)
    hook_beats: list[HookBeat] = Field(min_length=1)
    product_bridge: str = Field(min_length=1)
    quality_criteria: list[str] = Field(min_length=1)


class HypothesisBatch(CreativeModel):
    schema_version: Literal["1.0"] = "1.0"
    product_name: str = Field(min_length=1)
    content_language: str = "en-US"
    concepts: list[AdvertisingConcept] = Field(min_length=3, max_length=5)


class HookBeatResponse(BaseModel):
    """Constraint-light DTO for Gemini's supported JSON Schema subset."""

    start_seconds: float
    end_seconds: float
    visible_action: str
    expected_evidence: list[str]


class AdvertisingConceptResponse(BaseModel):
    concept_id: str = Field(
        description=(
            "Unique lowercase kebab-case identifier using a-z, 0-9, and hyphens "
            "only; for example, tab-overload"
        )
    )
    hypothesis: str
    audience_problem: str
    target_emotion: str
    emotional_arc: str
    hook_setting: str
    hook_camera: str
    hook_voiceover: str
    hook_beats: list[HookBeatResponse]
    product_bridge: str
    quality_criteria: list[str]


class HypothesisBatchResponse(BaseModel):
    schema_version: Literal["1.0"]
    product_name: str
    content_language: str
    concepts: list[AdvertisingConceptResponse]


class CompiledConceptStoryboard(CreativeModel):
    concept: AdvertisingConcept
    storyboard: Storyboard


class CampaignCandidatePlan(CreativeModel):
    candidate_id: str
    concept_id: str
    operation_id: str
    concept: AdvertisingConcept
    storyboard: Storyboard
    request: RunwayVideoRequest
    estimated_cost_microusd: int = Field(gt=0)
    state: Literal["planned"] = "planned"


class CampaignPlan(CreativeModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_prefix: str
    candidates: list[CampaignCandidatePlan] = Field(min_length=3, max_length=5)
    total_estimated_cost_microusd: int = Field(gt=0)


class ScoreDimension(CreativeModel):
    value: float | None = Field(default=None, ge=0, le=1)
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def require_value_or_reason(self) -> "ScoreDimension":
        reason = (self.unavailable_reason or "").strip()
        if self.value is None and not reason:
            raise ValueError("an unavailable score dimension requires a reason")
        if self.value is not None and reason:
            raise ValueError("a measured score dimension cannot be unavailable")
        self.unavailable_reason = reason or None
        return self


class CandidateScorecard(CreativeModel):
    candidate_id: str
    eligible: bool
    dimensions: dict[str, ScoreDimension]


SCORECARD_DIMENSIONS = (
    "hypothesis_match",
    "target_emotion_strength",
    "first_two_seconds_hook_clarity",
    "hook_to_product_bridge_coherence",
    "storyboard_action_alignment",
    "temporal_eligibility",
    "audiovisual_correctness",
    "product_brand_fidelity",
    "cta_clarity",
    "human_acceptance",
)


def build_hypothesis_prompt(
    brief: CampaignBrief,
    *,
    include_json_schema: bool = True,
) -> str:
    """Request a strict, provider-neutral batch of advertising concepts."""

    facts = "\n".join(f"- {fact}" for fact in brief.product_facts)
    assets = "\n".join(f"- {asset_id}" for asset_id in brief.available_asset_ids)
    schema_instruction = ""
    if include_json_schema:
        schema = json.dumps(HypothesisBatch.model_json_schema(), ensure_ascii=False)
        schema_instruction = f"""
The JSON must validate against this schema:
{schema}
"""
    return f"""You are planning hypotheses for a controlled short-form advertising experiment.

Product: {brief.product_name}
Audience: {brief.audience}
Verified product facts:
{facts}

Available managed asset IDs:
{assets or "- none"}

Create exactly {brief.concept_count} meaningfully distinct advertising concepts in
{brief.content_language}. Each concept must test a different audience insight and open
with a different visible action. Fully specify the 0.0 to 5.0 second hook as contiguous,
non-overlapping timed beats. Make the target emotion visually legible without audio in
the first two seconds, then define a coherent bridge into the exact product demonstration.
Do not invent product claims. Do not name a video provider or ask a model to redraw UI.
Use canonical lowercase "tict" in visible or narrated copy.

Set schema_version to exactly "1.0". Every concept_id must be a unique lowercase
kebab-case identifier containing only a-z, 0-9, and hyphens; never use underscores.

Return raw JSON only: no explanation and no Markdown except one optional JSON code fence.
{schema_instruction}
"""


def _parse_batch_json(raw: str) -> HypothesisBatch:
    candidate = raw.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if (
            len(lines) < 3
            or lines[0].strip().lower() not in {"```", "```json"}
            or lines[-1].strip() != "```"
        ):
            raise CampaignPlanningError(
                "campaign response must contain raw JSON with one optional code fence"
            )
        candidate = "\n".join(lines[1:-1]).strip()
    if not candidate.startswith("{") or not candidate.endswith("}"):
        raise CampaignPlanningError(
            "campaign response must contain raw JSON without explanatory prose"
        )
    try:
        payload = json.loads(candidate)
        if not isinstance(payload, dict):
            raise TypeError("root must be an object")
        return HypothesisBatch.model_validate(payload)
    except (TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise CampaignPlanningError(f"invalid campaign response: {exc}") from exc


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _validate_hook_beats(concept: AdvertisingConcept) -> None:
    previous_end = 0.0
    for index, beat in enumerate(concept.hook_beats):
        if beat.end_seconds <= beat.start_seconds:
            raise CampaignPlanningError(
                f"concept {concept.concept_id!r} has a hook beat that does not advance"
            )
        if beat.start_seconds < previous_end:
            raise CampaignPlanningError(
                f"concept {concept.concept_id!r} has overlapping hook beats"
            )
        if beat.start_seconds > previous_end:
            raise CampaignPlanningError(
                f"concept {concept.concept_id!r} has a gap before hook beat {index + 1}"
            )
        previous_end = beat.end_seconds
    if concept.hook_beats[0].start_seconds != 0.0 or previous_end != 5.0:
        raise CampaignPlanningError(
            f"concept {concept.concept_id!r} must cover the complete 0.0-5.0 second hook"
        )


def plan_hypotheses(
    brief: CampaignBrief,
    *,
    response_generator: Callable[[str], str] | None = None,
) -> HypothesisBatch:
    """Generate and fail-closed validate one complete concept batch."""

    if brief.content_language != "en-US":
        raise CampaignPlanningError("the first campaign slice must use en-US")
    if response_generator is None:
        from app.services import llm

        response_generator = llm._generate_response

    batch = _parse_batch_json(response_generator(build_hypothesis_prompt(brief)))
    if batch.product_name != brief.product_name:
        raise CampaignPlanningError("planner changed the product name")
    if batch.content_language != brief.content_language:
        raise CampaignPlanningError("planner changed the content language")
    if len(batch.concepts) != brief.concept_count:
        raise CampaignPlanningError(
            f"planner returned {len(batch.concepts)} concepts; expected exactly "
            f"{brief.concept_count}"
        )

    concept_ids: set[str] = set()
    hypotheses: set[str] = set()
    opening_actions: set[str] = set()
    for concept in batch.concepts:
        _validate_hook_beats(concept)
        if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", concept.concept_id) is None:
            raise CampaignPlanningError(
                "concept IDs must use lowercase kebab-case"
            )
        if concept.concept_id in concept_ids:
            raise CampaignPlanningError("concept IDs must be distinct")
        concept_ids.add(concept.concept_id)
        hypothesis = _normalized(concept.hypothesis)
        if hypothesis in hypotheses:
            raise CampaignPlanningError("concept hypotheses must be distinct")
        hypotheses.add(hypothesis)
        opening_action = _normalized(concept.hook_beats[0].visible_action)
        if opening_action in opening_actions:
            raise CampaignPlanningError("every concept requires a distinct opening action")
        opening_actions.add(opening_action)
    return batch


def _compile_hook_scene(
    template_hook: StoryboardScene,
    concept: AdvertisingConcept,
    *,
    product_name: str,
) -> StoryboardScene:
    timed_actions: list[str] = []
    evidence: list[str] = []
    normalized_product = _normalized(product_name)
    for beat in concept.hook_beats:
        action = beat.visible_action
        beat_evidence = beat.expected_evidence
        if normalized_product and normalized_product in _normalized(action):
            # Product pixels belong to the exact local composition beginning at
            # 5s. If a planner leaks a brand reveal into a generated hook beat,
            # preserve its timing but compile it into a safe physical reaction.
            action = (
                "The subject completes the preceding physical action with a clear "
                f"{concept.target_emotion.lower()} reaction; every device screen "
                "stays hidden"
            )
            beat_evidence = [
                "continuous physical motion",
                f"visible {concept.target_emotion.lower()} reaction",
                "device screens hidden",
            ]
        timed_actions.append(
            f"{beat.start_seconds:g}-{beat.end_seconds:g}s: {action}"
        )
        evidence.extend(beat_evidence)
    evidence = list(dict.fromkeys(evidence))
    visual_intent = VisualIntent(
        setting=concept.hook_setting,
        subject_action=" ".join(timed_actions),
        camera=concept.hook_camera,
        screen_content_policy=template_hook.visual_intent.screen_content_policy,
    )
    return template_hook.model_copy(
        update={
            "visual_intent": visual_intent,
            "voiceover": concept.hook_voiceover,
            "expected_evidence": evidence,
        }
    )


def compile_concept_storyboards(
    batch: HypothesisBatch,
    *,
    template_storyboard: Storyboard,
) -> list[CompiledConceptStoryboard]:
    """Replace hook intent while preserving exact product-demo and CTA scenes."""

    template = validate_storyboard(template_storyboard)
    if template.scenes[0].purpose != "hook":
        raise CampaignPlanningError("the controlled template must begin with a hook")

    compiled: list[CompiledConceptStoryboard] = []
    for concept in batch.concepts:
        hook = _compile_hook_scene(
            template.scenes[0],
            concept,
            product_name=batch.product_name,
        )
        storyboard = template.model_copy(
            update={
                "storyboard_id": f"{template.storyboard_id}-{concept.concept_id}",
                "hypothesis": concept.hypothesis,
                "scenes": [hook, *template.scenes[1:]],
            }
        )
        compiled.append(
            CompiledConceptStoryboard(
                concept=concept,
                storyboard=validate_storyboard(storyboard),
            )
        )
    return compiled


def build_campaign_plan(
    batch: HypothesisBatch,
    *,
    template_storyboard: Storyboard,
    operation_prefix: str,
) -> CampaignPlan:
    """Build reproducible, independently identifiable Runway candidate jobs."""

    if not operation_prefix.strip():
        raise CampaignPlanningError("operation_prefix is required")
    candidates: list[CampaignCandidatePlan] = []
    for item in compile_concept_storyboards(
        batch,
        template_storyboard=template_storyboard,
    ):
        request = build_runway_request(item.storyboard)
        candidates.append(
            CampaignCandidatePlan(
                candidate_id=item.concept.concept_id,
                concept_id=item.concept.concept_id,
                operation_id=f"{operation_prefix}-{item.concept.concept_id}",
                concept=item.concept,
                storyboard=item.storyboard,
                request=request,
                estimated_cost_microusd=estimate_runway_cost_microusd(request),
            )
        )
    return CampaignPlan(
        operation_prefix=operation_prefix,
        candidates=candidates,
        total_estimated_cost_microusd=sum(
            candidate.estimated_cost_microusd for candidate in candidates
        ),
    )


def build_candidate_scorecard(
    *,
    candidate_id: str,
    eligible: bool,
    measured: dict[str, float | None],
) -> CandidateScorecard:
    unknown = set(measured) - set(SCORECARD_DIMENSIONS)
    if unknown:
        raise ValueError(f"unknown scorecard dimensions: {sorted(unknown)}")
    dimensions = {
        name: ScoreDimension(
            value=measured.get(name),
            unavailable_reason=(
                None if measured.get(name) is not None else "not measured in this run"
            ),
        )
        for name in SCORECARD_DIMENSIONS
    }
    return CandidateScorecard(
        candidate_id=candidate_id,
        eligible=eligible,
        dimensions=dimensions,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_sha256(request: RunwayVideoRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _CandidateResumeState:
    kind: Literal["new", "submitted", "terminal"]
    record: dict[str, Any] | None = None
    job: RunwayJob | None = None


def _read_candidate_state(path: Path, *, candidate_id: str) -> dict[str, Any]:
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise CampaignPlanningError(
            f"candidate {candidate_id!r} has unreadable durable state"
        ) from exc
    if not resolved_path.is_relative_to(path.parent.resolve()):
        raise CampaignPlanningError(
            f"candidate {candidate_id!r} durable state is outside managed output"
        )
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignPlanningError(
            f"candidate {candidate_id!r} has unreadable durable state"
        ) from exc
    if not isinstance(payload, dict):
        raise CampaignPlanningError(
            f"candidate {candidate_id!r} durable state must be an object"
        )
    return payload


def _require_matching_state_field(
    record: dict[str, Any],
    *,
    key: str,
    expected: Any,
    candidate_id: str,
) -> None:
    if record.get(key) != expected:
        raise CampaignPlanningError(
            f"candidate {candidate_id!r} has stale or mismatched {key} in durable state"
        )


def _validate_ledger_operation(
    candidate: CampaignCandidatePlan,
    operation: Any,
) -> None:
    if operation.amount_microusd != candidate.estimated_cost_microusd:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} ledger amount does not match its plan"
        )
    if operation.status != "submitted":
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} has ambiguous ledger state "
            f"{operation.status!r}; automatic execution is blocked"
        )
    if not isinstance(operation.provider_job_id, str) or not operation.provider_job_id:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} submitted ledger operation has no "
            "provider job ID"
        )


def _validate_terminal_record(
    candidate: CampaignCandidatePlan,
    record: dict[str, Any],
    *,
    managed_output: Path,
) -> None:
    state = record["state"]
    expected_eligible = state == "eligible"
    if record.get("eligible") is not expected_eligible:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal eligibility is inconsistent"
        )
    if record.get("provider_status") != "SUCCEEDED":
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal provider status is stale"
        )
    screening = record.get("screening")
    if not isinstance(screening, dict):
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal state has no screening"
        )
    if screening.get("candidate_id") != candidate.candidate_id:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} screening identity is mismatched"
        )
    if temporal_candidate_is_eligible(screening) is not expected_eligible:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal screening eligibility is invalid"
        )

    raw_video_path = record.get("video_path")
    if not isinstance(raw_video_path, str) or not raw_video_path.strip():
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal state has no video path"
        )
    candidate_dir = (
        managed_output / "candidates" / candidate.candidate_id
    ).resolve()
    unresolved_video = Path(raw_video_path)
    if not unresolved_video.is_absolute():
        unresolved_video = managed_output / unresolved_video
    try:
        video_path = unresolved_video.resolve(strict=True)
    except OSError as exc:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal video is missing"
        ) from exc
    if not video_path.is_file() or not video_path.is_relative_to(candidate_dir):
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal video is outside managed output"
        )
    recorded_sha256 = record.get("video_sha256")
    if (
        not isinstance(recorded_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", recorded_sha256) is None
        or _sha256(video_path) != recorded_sha256
    ):
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} terminal video hash is mismatched"
        )


def _load_candidate_resume_state(
    candidate: CampaignCandidatePlan,
    *,
    managed_output: Path,
    budget_ledger: Any,
) -> _CandidateResumeState:
    state_path = managed_output / f"{candidate.candidate_id}.state.json"
    operation = budget_ledger.find_operation(candidate.operation_id)
    if not state_path.exists():
        if state_path.is_symlink():
            raise CampaignPlanningError(
                f"candidate {candidate.candidate_id!r} has an invalid durable state link"
            )
        if operation is None:
            return _CandidateResumeState(kind="new")
        _validate_ledger_operation(candidate, operation)
        reconstructed_record = {
            "candidate_id": candidate.candidate_id,
            "concept_id": candidate.concept_id,
            "operation_id": candidate.operation_id,
            "state": "submitted",
            "estimated_cost_microusd": candidate.estimated_cost_microusd,
            "request_sha256": _request_sha256(candidate.request),
            "provider_job_id": operation.provider_job_id,
        }
        return _CandidateResumeState(
            kind="submitted",
            record=reconstructed_record,
            job=RunwayJob(
                provider_job_id=operation.provider_job_id,
                operation_id=candidate.operation_id,
                request=candidate.request,
                estimated_cost_microusd=candidate.estimated_cost_microusd,
            ),
        )

    record = _read_candidate_state(
        state_path,
        candidate_id=candidate.candidate_id,
    )
    _require_matching_state_field(
        record,
        key="candidate_id",
        expected=candidate.candidate_id,
        candidate_id=candidate.candidate_id,
    )
    _require_matching_state_field(
        record,
        key="concept_id",
        expected=candidate.concept_id,
        candidate_id=candidate.candidate_id,
    )
    _require_matching_state_field(
        record,
        key="operation_id",
        expected=candidate.operation_id,
        candidate_id=candidate.candidate_id,
    )
    _require_matching_state_field(
        record,
        key="estimated_cost_microusd",
        expected=candidate.estimated_cost_microusd,
        candidate_id=candidate.candidate_id,
    )
    _require_matching_state_field(
        record,
        key="request_sha256",
        expected=_request_sha256(candidate.request),
        candidate_id=candidate.candidate_id,
    )
    if operation is None:
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} has durable state without a ledger "
            "operation; automatic execution is blocked"
        )
    _validate_ledger_operation(candidate, operation)

    state = record.get("state")
    if state == "submitting":
        recorded_provider_job_id = record.get("provider_job_id")
        if recorded_provider_job_id not in {None, operation.provider_job_id}:
            raise CampaignPlanningError(
                f"candidate {candidate.candidate_id!r} has stale or mismatched "
                "provider_job_id in durable state"
            )
        reconstructed_record = dict(record)
        reconstructed_record.update(
            {
                "state": "submitted",
                "provider_job_id": operation.provider_job_id,
            }
        )
        return _CandidateResumeState(
            kind="submitted",
            record=reconstructed_record,
            job=RunwayJob(
                provider_job_id=operation.provider_job_id,
                operation_id=candidate.operation_id,
                request=candidate.request,
                estimated_cost_microusd=candidate.estimated_cost_microusd,
            ),
        )
    _require_matching_state_field(
        record,
        key="provider_job_id",
        expected=operation.provider_job_id,
        candidate_id=candidate.candidate_id,
    )

    if state == "submitted":
        return _CandidateResumeState(
            kind="submitted",
            record=record,
            job=RunwayJob(
                provider_job_id=operation.provider_job_id,
                operation_id=candidate.operation_id,
                request=candidate.request,
                estimated_cost_microusd=candidate.estimated_cost_microusd,
            ),
        )
    if state in {"eligible", "screened_out"}:
        _validate_terminal_record(
            candidate,
            record,
            managed_output=managed_output,
        )
        return _CandidateResumeState(kind="terminal", record=record)
    raise CampaignPlanningError(
        f"candidate {candidate.candidate_id!r} has ambiguous durable state {state!r}; "
        "automatic execution is blocked"
    )


def _validate_runway_job(
    candidate: CampaignCandidatePlan,
    job: RunwayJob,
    *,
    require_succeeded: bool,
) -> None:
    if (
        job.operation_id != candidate.operation_id
        or job.request != candidate.request
        or job.estimated_cost_microusd != candidate.estimated_cost_microusd
        or not job.provider_job_id.strip()
    ):
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} provider job does not match its plan"
        )
    if require_succeeded and job.status != "SUCCEEDED":
        raise CampaignPlanningError(
            f"candidate {candidate.candidate_id!r} provider job did not succeed"
        )


def execute_candidate_pool(
    plan: CampaignPlan,
    *,
    adapter: Any,
    output_dir: Path,
    screen_hook: Callable[[CampaignCandidatePlan, Path], dict[str, Any]],
) -> dict[str, Any]:
    """Execute a preflighted pool and persist each candidate's durable state."""

    managed_output = output_dir.resolve()
    plan_path = managed_output / "campaign-plan.json"
    if plan_path.exists():
        try:
            stored_plan = CampaignPlan.model_validate_json(
                plan_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise CampaignPlanningError(
                "existing durable campaign plan is unreadable or invalid"
            ) from exc
        if stored_plan != plan:
            raise CampaignPlanningError(
                "existing durable campaign plan does not match the requested plan"
            )
    elif any(
        (managed_output / f"{candidate.candidate_id}.state.json").exists()
        or adapter.budget_ledger.find_operation(candidate.operation_id) is not None
        for candidate in plan.candidates
    ):
        raise CampaignPlanningError(
            "existing candidate state has no matching durable campaign plan"
        )
    resume_states = [
        _load_candidate_resume_state(
            candidate,
            managed_output=managed_output,
            budget_ledger=adapter.budget_ledger,
        )
        for candidate in plan.candidates
    ]
    new_cost_microusd = sum(
        candidate.estimated_cost_microusd
        for candidate, resume_state in zip(
            plan.candidates,
            resume_states,
            strict=True,
        )
        if resume_state.kind == "new"
    )
    # Submitted jobs have already consumed their conservative ledger amount.
    # Check the whole genuinely-new batch before making any provider call.
    if new_cost_microusd:
        adapter.budget_ledger.ensure_available(new_cost_microusd)

    managed_output.mkdir(parents=True, exist_ok=True)
    _write_json(plan_path, plan.model_dump(mode="json"))

    records: list[dict[str, Any]] = []
    for candidate, resume_state in zip(
        plan.candidates,
        resume_states,
        strict=True,
    ):
        if resume_state.kind == "terminal":
            assert resume_state.record is not None
            records.append(resume_state.record)
            continue

        started_at = time.monotonic()
        if resume_state.kind == "submitted":
            assert resume_state.record is not None
            assert resume_state.job is not None
            record = dict(resume_state.record)
            job: RunwayJob | None = resume_state.job
            record.pop("last_error", None)
            record.pop("last_error_type", None)
            record.pop("last_attempt_latency_seconds", None)
        else:
            record = {
                "candidate_id": candidate.candidate_id,
                "concept_id": candidate.concept_id,
                "operation_id": candidate.operation_id,
                "state": "submitting",
                "estimated_cost_microusd": candidate.estimated_cost_microusd,
                "request_sha256": _request_sha256(candidate.request),
            }
            job = None
            _write_json(
                managed_output / f"{candidate.candidate_id}.state.json",
                record,
            )
        try:
            if job is None:
                job = adapter.submit(
                    candidate.request,
                    operation_id=candidate.operation_id,
                )
                _validate_runway_job(
                    candidate,
                    job,
                    require_succeeded=False,
                )
                ledger_operation = adapter.budget_ledger.find_operation(
                    candidate.operation_id
                )
                if ledger_operation is None:
                    raise CampaignPlanningError(
                        f"candidate {candidate.candidate_id!r} submitted without a "
                        "durable ledger operation"
                    )
                _validate_ledger_operation(candidate, ledger_operation)
                if ledger_operation.provider_job_id != job.provider_job_id:
                    raise CampaignPlanningError(
                        f"candidate {candidate.candidate_id!r} provider job differs "
                        "from its durable ledger operation"
                    )
            record.update(
                {
                    "state": "submitted",
                    "provider_job_id": job.provider_job_id,
                }
            )
            _write_json(
                managed_output / f"{candidate.candidate_id}.state.json",
                record,
            )
            completed = adapter.wait(job)
            _validate_runway_job(
                candidate,
                completed,
                require_succeeded=True,
            )
            if completed.provider_job_id != job.provider_job_id:
                raise CampaignPlanningError(
                    f"candidate {candidate.candidate_id!r} completed provider job "
                    "identity changed"
                )
            downloaded = adapter.download_outputs(
                completed,
                managed_output / "candidates" / candidate.candidate_id,
            )
            if not downloaded:
                raise CampaignPlanningError(
                    f"candidate {candidate.candidate_id!r} produced no downloaded video"
                )
            primary_video = downloaded[0]
            candidate_dir = (
                managed_output / "candidates" / candidate.candidate_id
            ).resolve()
            try:
                primary_video = primary_video.resolve(strict=True)
            except OSError as exc:
                raise CampaignPlanningError(
                    f"candidate {candidate.candidate_id!r} downloaded video is missing"
                ) from exc
            if (
                not primary_video.is_file()
                or not primary_video.is_relative_to(candidate_dir)
            ):
                raise CampaignPlanningError(
                    f"candidate {candidate.candidate_id!r} downloaded outside managed output"
                )
            screening = screen_hook(candidate, primary_video)
            screening_record = {
                "candidate_id": candidate.candidate_id,
                **screening,
            }
            eligible = temporal_candidate_is_eligible(screening_record)
            record.update(
                {
                    "state": "eligible" if eligible else "screened_out",
                    "provider_status": completed.status,
                    "video_path": str(primary_video),
                    "video_sha256": _sha256(primary_video),
                    "screening": screening_record,
                    "eligible": eligible,
                    "latency_seconds": round(time.monotonic() - started_at, 3),
                }
            )
        except Exception as exc:
            operation = adapter.budget_ledger.find_operation(candidate.operation_id)
            if (
                operation is not None
                and operation.status == "submitted"
                and isinstance(operation.provider_job_id, str)
                and operation.provider_job_id
            ):
                record.update(
                    {
                        "state": "submitted",
                        "provider_job_id": operation.provider_job_id,
                        "last_error_type": type(exc).__name__,
                        "last_error": str(exc),
                        "last_attempt_latency_seconds": round(
                            time.monotonic() - started_at,
                            3,
                        ),
                    }
                )
            else:
                record.update(
                    {
                        "state": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "latency_seconds": round(time.monotonic() - started_at, 3),
                    }
                )
            records.append(record)
            _write_json(
                managed_output / f"{candidate.candidate_id}.state.json",
                record,
            )
            _write_json(
                managed_output / "candidate-pool.json",
                {"schema_version": "1.0", "candidates": records},
            )
            raise
        records.append(record)
        _write_json(
            managed_output / f"{candidate.candidate_id}.state.json",
            record,
        )
        _write_json(
            managed_output / "candidate-pool.json",
            {"schema_version": "1.0", "candidates": records},
        )

    result = {
        "schema_version": "1.0",
        "operation_prefix": plan.operation_prefix,
        "total_estimated_cost_microusd": plan.total_estimated_cost_microusd,
        "candidates": records,
        "eligible_candidate_ids": [
            record["candidate_id"] for record in records if record.get("eligible")
        ],
        "automatic_selection": None,
    }
    _write_json(managed_output / "candidate-pool.json", result)
    return result
