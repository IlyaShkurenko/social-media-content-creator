from __future__ import annotations

import pytest

from app.services.creative.temporal import (
    apply_candidate_temporal_review,
    temporal_candidate_is_eligible,
)


def unavailable_candidate() -> dict:
    return {
        "candidate_id": "browser-tab-chaos",
        "temporal_consistency_pass": False,
        "sampled_frame_count": 50,
        "temporal_events": [
            {
                "event_id": "screening-unavailable",
                "severity": "high",
            }
        ],
    }


def complete_review() -> dict:
    return {
        "candidate_id": "browser-tab-chaos",
        "artifact_review": {
            "outcome": "pass",
            "reviewer": "artifact-reviewer",
            "reviewed_at": "2026-08-18T12:42:15Z",
            "reason": "All timestamped frames show continuous motion.",
            "reviewed_frame_count": 50,
            "evidence_frames": ["review/browser-10fps.jpg"],
        },
        "event_confirmations": [],
    }


def test_complete_artifact_review_clears_provider_unavailability() -> None:
    reviewed = apply_candidate_temporal_review(
        unavailable_candidate(),
        complete_review(),
    )

    assert temporal_candidate_is_eligible(reviewed) is True
    assert reviewed["temporal_consistency_pass"] is True


def test_incomplete_artifact_review_remains_ineligible() -> None:
    review = complete_review()
    review["artifact_review"]["reviewed_frame_count"] = 49

    reviewed = apply_candidate_temporal_review(unavailable_candidate(), review)

    assert temporal_candidate_is_eligible(reviewed) is False


def test_false_positive_confirmation_clears_only_its_event() -> None:
    candidate = {
        "candidate_id": "map-pins",
        "temporal_consistency_pass": False,
        "temporal_events": [
            {"event_type": "object_disappearance", "severity": "high"},
            {"event_type": "geometry_deformation", "severity": "high"},
        ],
    }
    review = {
        "candidate_id": "map-pins",
        "event_confirmations": [
            {
                "event_index": 0,
                "outcome": "false_positive",
                "reviewer": "artifact-reviewer",
                "reviewed_at": "2026-08-18T12:42:15Z",
                "reason": "The object exits the crop during a continuous push-in.",
                "evidence_frames": ["F41", "F42", "F43"],
            }
        ],
    }

    reviewed = apply_candidate_temporal_review(candidate, review)

    assert temporal_candidate_is_eligible(reviewed) is False


def test_review_rejects_out_of_range_event_index() -> None:
    review = complete_review()
    review["event_confirmations"] = [{"event_index": 1}]

    with pytest.raises(ValueError, match="outside candidate events"):
        apply_candidate_temporal_review(unavailable_candidate(), review)
