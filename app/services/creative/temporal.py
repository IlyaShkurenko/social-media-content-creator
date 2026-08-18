from __future__ import annotations

import copy
from typing import Any


def _confirmed_false_positive(event: dict[str, Any]) -> bool:
    confirmation = event.get("confirmation")
    if not isinstance(confirmation, dict):
        return False
    return (
        confirmation.get("outcome") == "false_positive"
        and bool(str(confirmation.get("reviewer", "")).strip())
        and bool(str(confirmation.get("reviewed_at", "")).strip())
        and bool(str(confirmation.get("reason", "")).strip())
        and isinstance(confirmation.get("evidence_frames"), list)
        and bool(confirmation["evidence_frames"])
    )


def _complete_artifact_review(candidate: dict[str, Any]) -> bool:
    review = candidate.get("artifact_review")
    if not isinstance(review, dict):
        return False
    return (
        review.get("outcome") == "pass"
        and bool(str(review.get("reviewer", "")).strip())
        and bool(str(review.get("reviewed_at", "")).strip())
        and bool(str(review.get("reason", "")).strip())
        and review.get("reviewed_frame_count")
        == candidate.get("sampled_frame_count")
        and isinstance(review.get("evidence_frames"), list)
        and bool(review["evidence_frames"])
    )


def _screening_unavailable(event: dict[str, Any]) -> bool:
    return (
        event.get("event_id") == "screening-unavailable"
        or event.get("event_type") == "screening_unavailable"
    )


def temporal_candidate_is_eligible(candidate: dict[str, Any]) -> bool:
    """Apply model screening plus artifact-bound event confirmation."""

    events = candidate.get("temporal_events")
    if events is None:
        return candidate.get("temporal_consistency_pass") is True
    if not isinstance(events, list):
        return False

    high_events = [
        event
        for event in events
        if isinstance(event, dict) and event.get("severity") == "high"
    ]
    if not high_events:
        return candidate.get("temporal_consistency_pass") is True
    unavailable = [event for event in high_events if _screening_unavailable(event)]
    reported_defects = [
        event for event in high_events if not _screening_unavailable(event)
    ]
    if unavailable and not _complete_artifact_review(candidate):
        return False
    return all(_confirmed_false_positive(event) for event in reported_defects)


def apply_candidate_temporal_review(
    candidate: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Apply a separate artifact-bound review without changing provider evidence."""

    reviewed = copy.deepcopy(candidate)
    candidate_id = str(reviewed.get("candidate_id", "")).strip()
    if not candidate_id or review.get("candidate_id") != candidate_id:
        raise ValueError("temporal review candidate_id must match the candidate")

    artifact_review = review.get("artifact_review")
    if artifact_review is not None:
        if not isinstance(artifact_review, dict):
            raise ValueError("artifact_review must be an object")
        reviewed["artifact_review"] = copy.deepcopy(artifact_review)

    confirmations = review.get("event_confirmations", [])
    if not isinstance(confirmations, list):
        raise ValueError("event_confirmations must be a list")
    events = reviewed.get("temporal_events")
    if not isinstance(events, list):
        raise ValueError("candidate temporal_events must be a list")
    seen_indices: set[int] = set()
    for item in confirmations:
        if not isinstance(item, dict):
            raise ValueError("every event confirmation must be an object")
        event_index = item.get("event_index")
        if (
            not isinstance(event_index, int)
            or isinstance(event_index, bool)
            or event_index < 0
            or event_index >= len(events)
        ):
            raise ValueError("event confirmation index is outside candidate events")
        if event_index in seen_indices:
            raise ValueError("an event may be confirmed only once per review")
        seen_indices.add(event_index)
        confirmation = {key: value for key, value in item.items() if key != "event_index"}
        events[event_index]["confirmation"] = confirmation

    reviewed["temporal_consistency_pass"] = temporal_candidate_is_eligible(reviewed)
    return reviewed


def eligible_temporal_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return hooks that pass screening or have reviewed false positives only."""

    eligible: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError("every generated hook requires a candidate_id")
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate generated hook candidate_id: {candidate_id}")
        seen_ids.add(candidate_id)
        if temporal_candidate_is_eligible(candidate):
            eligible.append(candidate)
    return eligible
