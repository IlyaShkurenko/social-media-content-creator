from __future__ import annotations

from typing import Any


def eligible_temporal_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only generated hooks with an explicit passing temporal screen."""

    eligible: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id", "")).strip()
        if not candidate_id:
            raise ValueError("every generated hook requires a candidate_id")
        if candidate_id in seen_ids:
            raise ValueError(f"duplicate generated hook candidate_id: {candidate_id}")
        seen_ids.add(candidate_id)
        if candidate.get("temporal_consistency_pass") is True:
            eligible.append(candidate)
    return eligible
