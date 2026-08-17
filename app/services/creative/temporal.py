from __future__ import annotations

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
    return all(_confirmed_false_positive(event) for event in high_events)


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
