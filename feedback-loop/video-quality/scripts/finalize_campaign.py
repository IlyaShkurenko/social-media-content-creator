from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from run_campaign import (
    DEFAULT_ASSET_ROOT,
    DEFAULT_BUDGET_DATABASE,
    ITERATION_CAP_MICROUSD,
    ITERATION_SCOPE_ID,
    _resolve_file,
    _resolve_output,
    _write_json,
)

from app.services.creative.campaign import (
    CampaignPlan,
    build_candidate_scorecard,
)
from app.services.creative.budget import IterationBudgetLedger
from app.services.creative.narration import generate_scene_narration
from app.services.creative.renderer import render_mixed_media_video
from app.services.creative.temporal import (
    apply_candidate_temporal_review,
    temporal_candidate_is_eligible,
)
from evals.candidate_judge import (
    CandidateJudgeResponse,
    EVALUATOR_VERSION as CANDIDATE_EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION as CANDIDATE_EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_MODE as CANDIDATE_OBSERVATION_MODE,
    _candidate_contract,
    _validate_response as validate_candidate_response,
    map_dimension_statuses,
    sha256_json as candidate_sha256_json,
)
from evals.invariant_judge import (
    EVALUATOR_VERSION as INVARIANT_EVALUATOR_VERSION,
    EVIDENCE_SCHEMA_VERSION as INVARIANT_EVIDENCE_SCHEMA_VERSION,
    OBSERVATION_MODE as INVARIANT_OBSERVATION_MODE,
    InvariantJudgeResponse,
    _validate_response as validate_invariant_response,
    build_shared_invariant_contract,
    map_invariant_statuses,
    sha256_json as invariant_sha256_json,
)


SCORECARD_EVALUATOR_VERSION = "1.0.0"
SCORECARD_SCHEMA_VERSION = "1.0"
INVARIANT_SCORECARD_DIMENSIONS = (
    "audiovisual_correctness",
    "product_brand_fidelity",
    "cta_clarity",
)


def _validate_evidence_paths(
    output_dir: Path,
    review: dict[str, Any],
) -> None:
    evidence: list[str] = []
    artifact_review = review.get("artifact_review")
    if isinstance(artifact_review, dict):
        evidence.extend(artifact_review.get("evidence_frames", []))
    for confirmation in review.get("event_confirmations", []):
        if isinstance(confirmation, dict):
            evidence.extend(confirmation.get("evidence_frames", []))
    for value in evidence:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("review evidence paths must be non-empty strings")
        path = (output_dir / value).resolve()
        if output_dir not in path.parents or not path.is_file():
            raise ValueError(f"review evidence is missing or unmanaged: {value}")


def _resolve_budget_database(value: str) -> Path:
    loop_root = Path(__file__).parents[1].resolve()
    raw = Path(value)
    candidate = raw if raw.is_absolute() else loop_root / raw
    resolved = candidate.resolve()
    if loop_root not in resolved.parents:
        raise ValueError(
            f"budget database must stay inside {loop_root}: {resolved}"
        )
    if not resolved.is_file():
        raise ValueError(f"budget database does not exist: {resolved}")
    return resolved


def _apply_reviews_in_memory(
    *,
    output_dir: Path,
    records: list[dict[str, Any]],
    reviews_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate and apply every review without mutating campaign artifacts."""

    reviewed_records = copy.deepcopy(records)
    reviewed_candidate_ids: list[str] = []
    seen_ids: set[str] = set()
    for record in reviewed_records:
        if not isinstance(record, dict):
            raise ValueError("every candidate record must be an object")
        candidate_id = str(record.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in seen_ids:
            raise ValueError("candidate record IDs must be non-empty and unique")
        seen_ids.add(candidate_id)
        review = reviews_by_id.get(candidate_id)
        if review is None:
            continue
        screening = record.get("screening")
        if not isinstance(screening, dict):
            raise ValueError(
                f"candidate screening must be an object: {candidate_id}"
            )
        screening_input = copy.deepcopy(screening)
        artifact_review = review.get("artifact_review")
        if isinstance(artifact_review, dict):
            extracted_frames = sorted(
                (
                    output_dir
                    / "temporal"
                    / f"{candidate_id}-strips"
                    / "frames"
                ).glob("frame-*.jpg")
            )
            reviewed_count = artifact_review.get("reviewed_frame_count")
            if not extracted_frames or reviewed_count != len(extracted_frames):
                raise ValueError(
                    "artifact review count does not match retained frames: "
                    f"{candidate_id}"
                )
            screening_input["sampled_frame_count"] = len(extracted_frames)
        reviewed_screening = apply_candidate_temporal_review(
            screening_input,
            review,
        )
        eligible = temporal_candidate_is_eligible(reviewed_screening)
        record.update(
            {
                "screening": reviewed_screening,
                "eligible": eligible,
                "state": (
                    "eligible_after_review" if eligible else "screened_out"
                ),
            }
        )
        reviewed_candidate_ids.append(candidate_id)

    unknown = set(reviews_by_id) - seen_ids
    if unknown:
        raise ValueError(
            f"campaign review contains unknown candidates: {sorted(unknown)}"
        )
    return reviewed_records, reviewed_candidate_ids


def _existing_rendered_by_id(
    summary: dict[str, Any],
    *,
    known_ids: set[str],
) -> dict[str, dict[str, Any]]:
    rendered = summary.get("rendered_candidates", [])
    if not isinstance(rendered, list):
        raise ValueError("campaign summary rendered_candidates must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in rendered:
        if not isinstance(item, dict):
            raise ValueError("every rendered candidate must be an object")
        candidate_id = str(item.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in result:
            raise ValueError(
                "rendered candidate IDs must be non-empty and unique"
            )
        if candidate_id not in known_ids:
            raise ValueError(
                f"campaign summary contains an unknown candidate: {candidate_id}"
            )
        result[candidate_id] = copy.deepcopy(item)
    return result


def _render_eligible(
    *,
    output_dir: Path,
    plan: CampaignPlan,
    records: list[dict[str, Any]],
    existing_rendered: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    plan_by_id = {candidate.candidate_id: candidate for candidate in plan.candidates}
    rendered: list[dict[str, Any]] = []
    for record in records:
        if not record.get("eligible"):
            continue
        candidate_id = record["candidate_id"]
        existing = existing_rendered.get(candidate_id)
        if existing is not None:
            existing_video = Path(existing.get("video_path", "")).resolve()
            existing_subtitle = Path(existing.get("subtitle_path", "")).resolve()
            if (
                output_dir in existing_video.parents
                and output_dir in existing_subtitle.parents
                and existing_video.is_file()
                and existing_subtitle.is_file()
            ):
                rendered.append(existing)
                continue
        candidate = plan_by_id[candidate_id]
        hook_video = Path(record["video_path"]).resolve()
        if output_dir not in hook_video.parents or not hook_video.is_file():
            raise ValueError(f"candidate video is missing or unmanaged: {candidate_id}")
        narration = generate_scene_narration(
            candidate.storyboard,
            output_dir=output_dir / "narration" / candidate_id,
            interface_locale="en-US",
        )
        render = render_mixed_media_video(
            candidate.storyboard,
            hook_video_path=hook_video,
            asset_root=DEFAULT_ASSET_ROOT,
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
    return rendered


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_evidence_documents(
    values: list[str],
    *,
    label: str,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for value in values:
        path = _resolve_file(value, label=label)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{label} must contain one JSON object: {path}")
        try:
            source_path = str(path.relative_to(Path(__file__).parents[1]))
        except ValueError as exc:
            raise ValueError(
                f"{label} must stay inside {Path(__file__).parents[1]}: {path}"
            ) from exc
        documents.append(
            {
                "source_path": source_path,
                "source_sha256": _sha256(path),
                "payload": payload,
            }
        )
    return documents


def _load_invariant_scenario(value: str) -> dict[str, Any]:
    path = _resolve_file(value, label="invariant scenario")
    loop_root = Path(__file__).parents[1]
    try:
        source_path = str(path.relative_to(loop_root))
    except ValueError as exc:
        raise ValueError(
            f"invariant scenario must stay inside {loop_root}: {path}"
        ) from exc
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invariant scenario must contain one JSON object")
    return {
        "source_path": source_path,
        "source_sha256": _sha256(path),
        "payload": payload,
    }


def _plan_candidate_bindings(plan: CampaignPlan) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for candidate in plan.candidates:
        candidate_id = candidate.candidate_id.strip()
        concept_id = candidate.concept_id.strip()
        if not candidate_id or not concept_id or concept_id in bindings:
            raise ValueError("campaign plan concept IDs must be non-empty and unique")
        concept = candidate.concept.model_dump(mode="json")
        storyboard = candidate.storyboard.model_dump(mode="json")
        contract = _candidate_contract(concept, storyboard)
        bindings[concept_id] = {
            "candidate_id": candidate_id,
            "concept_id": concept_id,
            "contract": contract,
            "concept_sha256": candidate_sha256_json(concept),
            "storyboard_sha256": candidate_sha256_json(storyboard),
            "contract_sha256": candidate_sha256_json(contract),
        }
    return bindings


def _validated_render_hashes(
    *,
    output_dir: Path,
    rendered: list[dict[str, Any]],
) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in rendered:
        candidate_id = str(item.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in hashes:
            raise ValueError("rendered candidate IDs must be non-empty and unique")
        video_path = Path(str(item.get("video_path", ""))).resolve()
        if output_dir not in video_path.parents or not video_path.is_file():
            raise ValueError(
                f"rendered final video is missing or unmanaged: {candidate_id}"
            )
        hashes[candidate_id] = _sha256(video_path)
    return hashes


def _validate_evidence_header(
    payload: dict[str, Any],
    *,
    label: str,
    schema_version: int,
    evaluator_version: str,
    observation_mode: str,
) -> None:
    if payload.get("schema_version") != schema_version:
        raise ValueError(f"{label} has an unsupported schema version")
    if payload.get("evaluator_version") != evaluator_version:
        raise ValueError(f"{label} has an unsupported evaluator version")
    if payload.get("observation_mode") != observation_mode:
        raise ValueError(f"{label} has an unsupported observation mode")
    if payload.get("status") != "complete":
        raise ValueError(f"{label} must have complete status")


def _candidate_evidence_by_id(
    documents: list[dict[str, Any]],
    *,
    known_ids: set[str],
    render_hashes: dict[str, str],
    plan_bindings: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for document in documents:
        payload = document["payload"]
        _validate_evidence_header(
            payload,
            label="candidate evidence",
            schema_version=CANDIDATE_EVIDENCE_SCHEMA_VERSION,
            evaluator_version=CANDIDATE_EVALUATOR_VERSION,
            observation_mode=CANDIDATE_OBSERVATION_MODE,
        )
        concept_id = str(payload.get("concept_id", "")).strip()
        binding = plan_bindings.get(concept_id)
        candidate_id = binding["candidate_id"] if binding is not None else ""
        if candidate_id not in known_ids:
            raise ValueError(
                f"candidate evidence belongs to an unknown candidate: {concept_id!r}"
            )
        if candidate_id in result:
            raise ValueError(f"duplicate candidate evidence: {candidate_id}")
        for field in (
            "concept_sha256",
            "storyboard_sha256",
            "contract_sha256",
        ):
            if payload.get(field) != binding[field]:
                raise ValueError(
                    f"candidate evidence has a mismatched plan {field}: {candidate_id}"
                )
        expected_hash = render_hashes.get(candidate_id)
        if expected_hash is None:
            raise ValueError(
                f"candidate evidence has no retained final MP4: {candidate_id}"
            )
        if payload.get("video_sha256") != expected_hash:
            raise ValueError(
                f"candidate evidence final MP4 hash does not match: {candidate_id}"
            )
        response = CandidateJudgeResponse.model_validate(payload.get("response"))
        video_duration_ms = payload.get("video_duration_ms")
        if (
            not isinstance(video_duration_ms, int)
            or isinstance(video_duration_ms, bool)
            or video_duration_ms <= 0
        ):
            raise ValueError(
                f"candidate evidence has an invalid video duration: {candidate_id}"
            )
        validate_candidate_response(
            response,
            video_duration_seconds=video_duration_ms / 1000,
            contract=binding["contract"],
        )
        scores = map_dimension_statuses(response)
        if payload.get("dimension_scores") != scores:
            raise ValueError(
                f"candidate evidence contains non-deterministic scores: {candidate_id}"
            )
        result[candidate_id] = {
            "scores": scores,
            "provenance": {
                "source_path": document["source_path"],
                "source_sha256": document["source_sha256"],
                "schema_version": payload["schema_version"],
                "evaluator_version": payload["evaluator_version"],
                "observation_mode": payload["observation_mode"],
                "concept_sha256": payload["concept_sha256"],
                "storyboard_sha256": payload["storyboard_sha256"],
                "contract_sha256": payload.get("contract_sha256"),
                "prompt_sha256": payload.get("prompt_sha256"),
                "response_schema_sha256": payload.get(
                    "response_schema_sha256"
                ),
                "video_sha256": payload["video_sha256"],
                "provider_response_id": payload.get("provider", {}).get(
                    "response_id"
                ),
            },
        }
    return result


def _candidate_label_for_pass(
    item: dict[str, Any],
    *,
    candidate_sha256: str,
) -> str:
    order = item.get("order")
    if not isinstance(order, dict) or set(order) != {"A", "B"}:
        raise ValueError("invariant evidence pass has an invalid A/B order")
    labels = [label for label in ("A", "B") if order[label] == candidate_sha256]
    if len(labels) != 1:
        raise ValueError(
            "invariant evidence pass must map the candidate to exactly one label"
        )
    return labels[0]


def _invariant_evidence_by_id(
    documents: list[dict[str, Any]],
    *,
    render_hashes: dict[str, str],
    scenario_document: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if documents and scenario_document is None:
        raise ValueError("invariant evidence requires an explicit invariant scenario")
    scenario = scenario_document["payload"] if scenario_document is not None else None
    contract = (
        build_shared_invariant_contract(scenario) if scenario is not None else None
    )
    scenario_sha256 = (
        scenario_document["source_sha256"] if scenario_document is not None else None
    )
    shared_contract_sha256 = (
        invariant_sha256_json(contract) if contract is not None else None
    )
    scene_end_seconds = (
        max(float(scene["end_seconds"]) for scene in contract["downstream_scenes"])
        if contract is not None
        else None
    )
    candidate_by_hash = {
        digest: candidate_id for candidate_id, digest in render_hashes.items()
    }
    if len(candidate_by_hash) != len(render_hashes):
        raise ValueError("rendered final MP4 hashes must be unique per candidate")

    result: dict[str, dict[str, Any]] = {}
    for document in documents:
        payload = document["payload"]
        _validate_evidence_header(
            payload,
            label="invariant evidence",
            schema_version=INVARIANT_EVIDENCE_SCHEMA_VERSION,
            evaluator_version=INVARIANT_EVALUATOR_VERSION,
            observation_mode=INVARIANT_OBSERVATION_MODE,
        )
        if payload.get("scenario_sha256") != scenario_sha256:
            raise ValueError("invariant evidence has a mismatched scenario_sha256")
        if payload.get("shared_contract_sha256") != shared_contract_sha256:
            raise ValueError(
                "invariant evidence has a mismatched shared_contract_sha256"
            )
        if payload.get("scenario_id") != scenario.get("id"):
            raise ValueError("invariant evidence has a mismatched scenario ID")
        candidate_sha256 = str(payload.get("candidate_sha256", ""))
        candidate_id = candidate_by_hash.get(candidate_sha256)
        if candidate_id is None:
            raise ValueError(
                "invariant evidence candidate SHA does not match a retained final MP4"
            )
        if candidate_id in result:
            raise ValueError(f"duplicate invariant evidence: {candidate_id}")
        baseline_sha256 = str(payload.get("baseline_sha256", ""))
        if not baseline_sha256 or baseline_sha256 == candidate_sha256:
            raise ValueError(
                "campaign invariant evidence requires distinct baseline and candidate artifacts"
            )
        passes = payload.get("passes")
        if not isinstance(passes, list) or len(passes) != 2:
            raise ValueError("invariant evidence requires exactly two reversed passes")

        expected_orders = {
            (("A", baseline_sha256), ("B", candidate_sha256)),
            (("A", candidate_sha256), ("B", baseline_sha256)),
        }
        actual_orders: set[tuple[tuple[str, str], ...]] = set()
        pass_values: list[dict[str, float | None]] = []
        for item in passes:
            if not isinstance(item, dict):
                raise ValueError("invariant evidence pass must be an object")
            label = _candidate_label_for_pass(
                item,
                candidate_sha256=candidate_sha256,
            )
            order = item["order"]
            actual_orders.add(
                tuple(
                    sorted(
                        (str(key), str(value)) for key, value in order.items()
                    )
                )
            )
            response = InvariantJudgeResponse.model_validate(item.get("response"))
            validate_invariant_response(
                response,
                contract=contract,
                video_duration_seconds_by_label={
                    "A": scene_end_seconds,
                    "B": scene_end_seconds,
                },
            )
            mapped = map_invariant_statuses(response)
            if item.get("criterion_scores") != mapped:
                raise ValueError(
                    f"invariant evidence contains non-deterministic scores: {candidate_id}"
                )
            score_key = "video_a" if label == "A" else "video_b"
            pass_values.append(
                {
                    name: mapped[score_key][name]
                    for name in INVARIANT_SCORECARD_DIMENSIONS
                }
            )
        if actual_orders != expected_orders:
            raise ValueError("invariant evidence passes are not reversed A/B orders")

        scores: dict[str, float | None] = {}
        for name in INVARIANT_SCORECARD_DIMENSIONS:
            values = [item[name] for item in pass_values]
            scores[name] = (
                sum(float(value) for value in values) / len(values)
                if all(value is not None for value in values)
                else None
            )
        result[candidate_id] = {
            "scores": scores,
            "provenance": {
                "source_path": document["source_path"],
                "source_sha256": document["source_sha256"],
                "schema_version": payload["schema_version"],
                "evaluator_version": payload["evaluator_version"],
                "observation_mode": payload["observation_mode"],
                "scenario_id": payload["scenario_id"],
                "scenario_sha256": payload["scenario_sha256"],
                "shared_contract_sha256": payload.get(
                    "shared_contract_sha256"
                ),
                "scenario_source_path": scenario_document["source_path"],
                "prompt_sha256": payload.get("prompt_sha256"),
                "response_schema_sha256": payload.get(
                    "response_schema_sha256"
                ),
                "baseline_sha256": baseline_sha256,
                "candidate_sha256": candidate_sha256,
                "pass_values": pass_values,
                "provider_response_ids": [
                    item.get("provider", {}).get("response_id") for item in passes
                ],
            },
        }
    return result


def _temporal_provenance(record: dict[str, Any]) -> dict[str, Any]:
    screening = record.get("screening")
    return {
        "source": "candidate_pool_record",
        "eligible": record.get("eligible"),
        "temporal_status": (
            screening.get("temporal_status") if isinstance(screening, dict) else None
        ),
        "temporal_evidence": (
            screening.get("temporal_evidence")
            if isinstance(screening, dict)
            else None
        ),
    }


def _build_scorecards(
    records: list[dict[str, Any]],
    *,
    plan: CampaignPlan | None = None,
    output_dir: Path | None = None,
    rendered: list[dict[str, Any]] | None = None,
    candidate_evidence: list[dict[str, Any]] | None = None,
    invariant_evidence: list[dict[str, Any]] | None = None,
    invariant_scenario: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build scorecards only from artifact-bound, versioned evidence."""

    candidate_ids = [
        str(record.get("candidate_id", "")).strip() for record in records
    ]
    if any(not candidate_id for candidate_id in candidate_ids) or len(
        set(candidate_ids)
    ) != len(candidate_ids):
        raise ValueError("candidate record IDs must be non-empty and unique")
    evidence_supplied = bool(candidate_evidence or invariant_evidence)
    if evidence_supplied and output_dir is None:
        raise ValueError("scorecard evidence requires the managed campaign output")
    if candidate_evidence and plan is None:
        raise ValueError("candidate evidence requires the exact campaign plan")
    plan_bindings = _plan_candidate_bindings(plan) if plan is not None else {}
    planned_ids = {item["candidate_id"] for item in plan_bindings.values()}
    if candidate_evidence and planned_ids != set(candidate_ids):
        raise ValueError("candidate records do not match the exact campaign plan")
    render_hashes = (
        _validated_render_hashes(
            output_dir=output_dir.resolve(),
            rendered=rendered or [],
        )
        if output_dir is not None
        else {}
    )
    semantic_by_id = _candidate_evidence_by_id(
        candidate_evidence or [],
        known_ids=set(candidate_ids),
        render_hashes=render_hashes,
        plan_bindings=plan_bindings,
    )
    invariant_by_id = _invariant_evidence_by_id(
        invariant_evidence or [],
        render_hashes=render_hashes,
        scenario_document=invariant_scenario,
    )

    scorecards: list[dict[str, Any]] = []
    for record in records:
        candidate_id = str(record["candidate_id"])
        eligible = record.get("eligible")
        measured: dict[str, float | None] = {}
        if isinstance(eligible, bool):
            measured["temporal_eligibility"] = 1.0 if eligible else 0.0
        semantic = semantic_by_id.get(candidate_id)
        if semantic is not None:
            measured.update(semantic["scores"])
        invariant = invariant_by_id.get(candidate_id)
        if invariant is not None:
            measured.update(invariant["scores"])

        scorecard = build_candidate_scorecard(
            candidate_id=candidate_id,
            eligible=bool(eligible),
            measured=measured,
        ).model_dump(mode="json")
        scorecard.update(
            {
                "schema_version": SCORECARD_SCHEMA_VERSION,
                "scorecard_evaluator_version": SCORECARD_EVALUATOR_VERSION,
                "evidence": {
                    "temporal": _temporal_provenance(record),
                    "candidate_semantic": (
                        semantic["provenance"] if semantic is not None else None
                    ),
                    "shared_invariant": (
                        invariant["provenance"] if invariant is not None else None
                    ),
                },
            }
        )
        scorecards.append(scorecard)
    return scorecards


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply artifact-bound campaign reviews and render eligible videos"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--candidate-evidence", action="append", default=[])
    parser.add_argument("--invariant-evidence", action="append", default=[])
    parser.add_argument("--invariant-scenario")
    parser.add_argument(
        "--budget-database",
        default=str(DEFAULT_BUDGET_DATABASE),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = _resolve_output(args.output)
    review_path = _resolve_file(args.review, label="campaign review")
    review_payload = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(review_payload, dict):
        raise ValueError("campaign review must contain one JSON object")
    if review_payload.get("schema_version") != "1.0":
        raise ValueError("campaign review schema_version must be 1.0")
    reviews = review_payload.get("candidate_reviews")
    if not isinstance(reviews, list):
        raise ValueError("candidate_reviews must be a list")
    reviews_by_id: dict[str, dict[str, Any]] = {}
    for review in reviews:
        if not isinstance(review, dict):
            raise ValueError("every candidate review must be an object")
        candidate_id = str(review.get("candidate_id", "")).strip()
        if not candidate_id or candidate_id in reviews_by_id:
            raise ValueError("candidate review IDs must be non-empty and unique")
        _validate_evidence_paths(output_dir, review)
        reviews_by_id[candidate_id] = review

    plan = CampaignPlan.model_validate_json(
        (output_dir / "campaign-plan.json").read_text(encoding="utf-8")
    )
    pool_path = output_dir / "candidate-pool.json"
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    if not isinstance(pool, dict):
        raise ValueError("candidate pool must contain one JSON object")
    records = pool.get("candidates")
    if not isinstance(records, list):
        raise ValueError("candidate pool does not contain candidate records")
    reviewed_records, reviewed_candidate_ids = _apply_reviews_in_memory(
        output_dir=output_dir,
        records=records,
        reviews_by_id=reviews_by_id,
    )
    known_ids = {
        str(record["candidate_id"]).strip() for record in reviewed_records
    }
    plan_bindings = _plan_candidate_bindings(plan)
    planned_ids = {binding["candidate_id"] for binding in plan_bindings.values()}
    if planned_ids != known_ids:
        raise ValueError("candidate pool does not match the exact campaign plan")
    eligible_ids = [
        str(record["candidate_id"])
        for record in reviewed_records
        if record.get("eligible")
    ]

    summary_path = output_dir / "campaign-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError("campaign summary must contain one JSON object")
    existing_rendered = _existing_rendered_by_id(
        summary,
        known_ids=known_ids,
    )
    candidate_evidence = _load_evidence_documents(
        args.candidate_evidence,
        label="candidate evidence",
    )
    invariant_evidence = _load_evidence_documents(
        args.invariant_evidence,
        label="invariant evidence",
    )
    invariant_scenario = (
        _load_invariant_scenario(args.invariant_scenario)
        if args.invariant_scenario
        else None
    )
    projected_rendered = [
        existing_rendered[candidate_id]
        for candidate_id in eligible_ids
        if candidate_id in existing_rendered
    ]
    if candidate_evidence or invariant_evidence:
        _build_scorecards(
            reviewed_records,
            plan=plan,
            output_dir=output_dir,
            rendered=projected_rendered,
            candidate_evidence=candidate_evidence,
            invariant_evidence=invariant_evidence,
            invariant_scenario=invariant_scenario,
        )

    budget_database = _resolve_budget_database(args.budget_database)
    snapshot = IterationBudgetLedger(
        budget_database,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    ).snapshot()

    rendered = _render_eligible(
        output_dir=output_dir,
        plan=plan,
        records=reviewed_records,
        existing_rendered=existing_rendered,
    )
    scorecards = _build_scorecards(
        reviewed_records,
        plan=plan,
        output_dir=output_dir,
        rendered=rendered,
        candidate_evidence=candidate_evidence,
        invariant_evidence=invariant_evidence,
        invariant_scenario=invariant_scenario,
    )

    updated_pool = copy.deepcopy(pool)
    updated_pool["candidates"] = reviewed_records
    updated_pool["eligible_candidate_ids"] = eligible_ids
    updated_pool["automatic_selection"] = None
    updated_summary = copy.deepcopy(summary)
    updated_summary.update(
        {
            "eligible_candidate_ids": eligible_ids,
            "rendered_candidates": rendered,
            "automatic_selection": None,
            "review_record": str(output_dir / "campaign-review-applied.json"),
            "scorecards": scorecards,
            "budget": {
                "cap_microusd": snapshot.cap_microusd,
                "charged_microusd": snapshot.charged_microusd,
                "remaining_microusd": snapshot.remaining_microusd,
            },
        }
    )

    for candidate_id in reviewed_candidate_ids:
        record = next(
            item
            for item in reviewed_records
            if item["candidate_id"] == candidate_id
        )
        _write_json(output_dir / f"{candidate_id}.state.json", record)
    _write_json(pool_path, updated_pool)
    _write_json(output_dir / "campaign-review-applied.json", review_payload)
    _write_json(
        output_dir / "candidate-scorecards.json",
        {
            "schema_version": SCORECARD_SCHEMA_VERSION,
            "scorecard_evaluator_version": SCORECARD_EVALUATOR_VERSION,
            "scorecards": scorecards,
        },
    )
    _write_json(summary_path, updated_summary)
    print(json.dumps(updated_summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
