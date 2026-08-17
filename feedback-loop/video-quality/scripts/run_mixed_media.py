from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


LOOP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.creative.budget import IterationBudgetLedger  # noqa: E402
from app.services.creative.narration import generate_scene_narration  # noqa: E402
from app.services.creative.pipeline import prepare_creative_run  # noqa: E402
from app.services.creative.renderer import render_mixed_media_video  # noqa: E402
from app.services.creative.runway import (  # noqa: E402
    RunwayAdapter,
    RunwayVideoRequest,
)
from app.services.creative.storyboard import validate_storyboard  # noqa: E402


ITERATION_SCOPE_ID = "mixed-media-iteration-001"
ITERATION_CAP_MICROUSD = 10_000_000
DEFAULT_STORYBOARD = (
    LOOP_ROOT / "evals" / "dataset" / "mixed-media-first-slice-001.json"
)
DEFAULT_ASSET_ROOT = LOOP_ROOT / "evals" / "assets" / "brand"
DEFAULT_OUTPUT = LOOP_ROOT / ".state" / "prepared" / "mixed-media-first-slice-001"
DEFAULT_BUDGET_DATABASE = (
    LOOP_ROOT / ".state" / "mixed-media-iteration-001.sqlite3"
)


def _resolve(value: str | Path, *, relative_to: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else relative_to / path).resolve()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare or explicitly execute the first mixed-media ad slice"
    )
    parser.add_argument("--storyboard", default=str(DEFAULT_STORYBOARD))
    parser.add_argument("--asset-root", default=str(DEFAULT_ASSET_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--hook-video")
    parser.add_argument("--narrate", action="store_true")
    parser.add_argument("--narration-audio")
    parser.add_argument("--interface-locale", default="en-US")
    parser.add_argument("--execute-runway", action="store_true")
    parser.add_argument("--confirm-paid", default="")
    parser.add_argument(
        "--operation-id",
        default="mixed-media-first-slice-001-runway-hook-v1",
    )
    args = parser.parse_args()
    if args.execute_runway and args.confirm_paid != "YES":
        parser.error("paid Runway execution requires --confirm-paid YES")
    if args.execute_runway and args.hook_video:
        parser.error("choose either --execute-runway or --hook-video, not both")
    if args.narrate and not (args.execute_runway or args.hook_video):
        parser.error("--narrate requires a Runway or stock hook video")
    if args.narrate and args.narration_audio:
        parser.error("choose either --narrate or --narration-audio, not both")
    if args.narration_audio and not (args.execute_runway or args.hook_video):
        parser.error("--narration-audio requires a Runway or stock hook video")
    return args


def _configured_runway() -> tuple[str, str, str]:
    from app.config import config

    api_key = (
        str(config.app.get("runway_api_key", "") or "").strip()
        or os.getenv("RUNWAYML_API_SECRET", "").strip()
    )
    if not api_key:
        raise RuntimeError(
            "Runway is not configured: set app.runway_api_key in config.toml, "
            "use the WebUI Material APIs panel, or export RUNWAYML_API_SECRET"
        )
    base_url = str(
        config.app.get("runway_base_url", RunwayAdapter.BASE_URL)
        or RunwayAdapter.BASE_URL
    ).strip()
    api_version = str(
        config.app.get("runway_api_version", RunwayAdapter.API_VERSION)
        or RunwayAdapter.API_VERSION
    ).strip()
    return api_key, base_url, api_version


def main() -> int:
    args = _parse_args()
    storyboard_path = _resolve(args.storyboard, relative_to=LOOP_ROOT)
    asset_root = _resolve(args.asset_root, relative_to=LOOP_ROOT)
    output_dir = _resolve(args.output, relative_to=LOOP_ROOT)
    if not storyboard_path.is_file():
        raise FileNotFoundError(f"storyboard does not exist: {storyboard_path}")

    storyboard = validate_storyboard(
        json.loads(storyboard_path.read_text(encoding="utf-8"))
    )
    prepared = prepare_creative_run(
        storyboard,
        asset_root=asset_root,
        output_dir=output_dir,
    )
    summary: dict = {
        "storyboard_id": storyboard.storyboard_id,
        "output_dir": str(prepared.output_dir),
        "paid_submission": False,
        "iteration_budget_usd": 10.0,
        "actual_paid_cost_usd": 0.0,
        "generation_latency_seconds": None,
        "estimated_runway_cost_usd": round(
            prepared.estimated_runway_cost_microusd / 1_000_000,
            6,
        ),
        "rendered_video": None,
        "script": " ".join(
            scene.voiceover.strip()
            for scene in storyboard.scenes
            if scene.voiceover.strip()
        ),
    }

    hook_video: Path | None = None
    variant_id: str | None = None
    if args.execute_runway:
        api_key, base_url, api_version = _configured_runway()
        budget = IterationBudgetLedger(
            DEFAULT_BUDGET_DATABASE,
            scope_id=ITERATION_SCOPE_ID,
            cap_microusd=ITERATION_CAP_MICROUSD,
        )
        adapter = RunwayAdapter(
            api_key=api_key,
            budget_ledger=budget,
            base_url=base_url,
            api_version=api_version,
        )
        runway_request = RunwayVideoRequest.model_validate_json(
            prepared.runway_request_path.read_text(encoding="utf-8")
        )
        started_at = time.monotonic()
        job = adapter.submit(runway_request, operation_id=args.operation_id)
        completed = adapter.wait(job)
        downloaded = adapter.download_outputs(
            completed,
            prepared.output_dir / "runway" / "outputs",
        )
        elapsed_seconds = time.monotonic() - started_at
        job_record = completed.to_record(downloaded_paths=downloaded)
        job_record["generation_latency_seconds"] = round(elapsed_seconds, 3)
        _write_json(prepared.output_dir / "runway" / "job.json", job_record)
        snapshot = budget.snapshot()
        summary.update(
            {
                "paid_submission": True,
                "runway_job_id": completed.provider_job_id,
                "charged_microusd": snapshot.charged_microusd,
                "actual_paid_cost_usd": round(
                    completed.estimated_cost_microusd / 1_000_000,
                    6,
                ),
                "remaining_microusd": snapshot.remaining_microusd,
                "generation_latency_seconds": round(elapsed_seconds, 3),
            }
        )
        hook_video = downloaded[0]
        variant_id = "runway-candidate"
    elif args.hook_video:
        hook_video = _resolve(args.hook_video, relative_to=REPO_ROOT)
        variant_id = "stock-baseline"

    if hook_video is not None and variant_id is not None:
        narration_audio = None
        if args.narration_audio:
            narration_audio = _resolve(args.narration_audio, relative_to=REPO_ROOT)
            if not narration_audio.is_file():
                raise FileNotFoundError(
                    f"narration audio does not exist: {narration_audio}"
                )
            summary["narration_voice"] = "prebuilt-en-US"
        elif args.narrate:
            narration = generate_scene_narration(
                storyboard,
                output_dir=prepared.output_dir / "narration",
                interface_locale=args.interface_locale,
            )
            narration_audio = narration.audio_path
            summary["narration_voice"] = narration.plan.settings.voice_name
        rendered = render_mixed_media_video(
            storyboard,
            hook_video_path=hook_video,
            asset_root=asset_root,
            output_dir=prepared.output_dir / "renders" / variant_id,
            narration_audio_path=narration_audio,
        )
        summary["rendered_video"] = str(rendered.video_path)
        summary["subtitle_path"] = str(rendered.subtitle_path)
        summary["subtitle_layout_path"] = str(rendered.subtitle_layout_path)
        summary["subtitle_safe_area_pass"] = all(
            layout.safe_area_pass for layout in rendered.subtitle_layouts
        )
        summary["variant_id"] = variant_id

    _write_json(prepared.output_dir / "run-summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
