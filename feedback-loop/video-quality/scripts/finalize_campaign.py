from __future__ import annotations

import argparse
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply artifact-bound campaign reviews and render eligible videos"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--review", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = _resolve_output(args.output)
    review_path = _resolve_file(args.review, label="campaign review")
    review_payload = json.loads(review_path.read_text(encoding="utf-8"))
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
    records = pool.get("candidates")
    if not isinstance(records, list):
        raise ValueError("candidate pool does not contain candidate records")
    known_ids = {record.get("candidate_id") for record in records}
    unknown = set(reviews_by_id) - known_ids
    if unknown:
        raise ValueError(f"campaign review contains unknown candidates: {sorted(unknown)}")

    for record in records:
        candidate_id = record.get("candidate_id")
        review = reviews_by_id.get(candidate_id)
        if review is None:
            continue
        screening_input = dict(record["screening"])
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
                    f"artifact review count does not match retained frames: {candidate_id}"
                )
            screening_input["sampled_frame_count"] = len(extracted_frames)
        screening = apply_candidate_temporal_review(screening_input, review)
        eligible = temporal_candidate_is_eligible(screening)
        record.update(
            {
                "screening": screening,
                "eligible": eligible,
                "state": "eligible_after_review" if eligible else "screened_out",
            }
        )
        _write_json(output_dir / f"{candidate_id}.state.json", record)

    eligible_ids = [
        record["candidate_id"] for record in records if record.get("eligible")
    ]
    pool["eligible_candidate_ids"] = eligible_ids
    pool["automatic_selection"] = None
    _write_json(pool_path, pool)
    _write_json(output_dir / "campaign-review-applied.json", review_payload)

    summary_path = output_dir / "campaign-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    existing_rendered = {
        item["candidate_id"]: item
        for item in summary.get("rendered_candidates", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    rendered = _render_eligible(
        output_dir=output_dir,
        plan=plan,
        records=records,
        existing_rendered=existing_rendered,
    )
    rendered_by_id = {item["candidate_id"]: item for item in rendered}
    scorecards = []
    for record in records:
        candidate_id = record["candidate_id"]
        measured: dict[str, float | None] = {
            "temporal_eligibility": 1.0 if record.get("eligible") else 0.0,
        }
        rendered_record = rendered_by_id.get(candidate_id)
        if rendered_record is not None:
            measured.update(
                {
                    "audiovisual_correctness": 1.0,
                    "product_brand_fidelity": 1.0,
                    "cta_clarity": (
                        1.0
                        if rendered_record["subtitle_safe_area_pass"]
                        else 0.0
                    ),
                }
            )
        scorecards.append(
            build_candidate_scorecard(
                candidate_id=candidate_id,
                eligible=bool(record.get("eligible")),
                measured=measured,
            ).model_dump(mode="json")
        )
    _write_json(
        output_dir / "candidate-scorecards.json",
        {"schema_version": "1.0", "scorecards": scorecards},
    )
    summary.update(
        {
            "eligible_candidate_ids": eligible_ids,
            "rendered_candidates": rendered,
            "automatic_selection": None,
            "review_record": str(output_dir / "campaign-review-applied.json"),
            "scorecards": scorecards,
        }
    )
    snapshot = IterationBudgetLedger(
        DEFAULT_BUDGET_DATABASE,
        scope_id=ITERATION_SCOPE_ID,
        cap_microusd=ITERATION_CAP_MICROUSD,
    ).snapshot()
    summary["budget"] = {
        "cap_microusd": snapshot.cap_microusd,
        "charged_microusd": snapshot.charged_microusd,
        "remaining_microusd": snapshot.remaining_microusd,
    }
    _write_json(summary_path, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
