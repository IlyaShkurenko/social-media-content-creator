# Goal: tict advertising video quality

Build an autonomous, reproducible production loop that turns a tict advertising hypothesis into up to five distinct short-form videos, measures the rendered results against their storyboards, and keeps only demonstrable improvements.

## Primary metric

Maximize `timeline_alignment_f1`: micro-averaged F1 over `(scene_id, expected_visual_tag)` pairs compared with the visual observations for the same scene. The fixed evaluator-v0 fixture is human-labelled; the target architecture replaces it with a versioned vision judge without changing the metric contract.

The first trustworthy automated baseline will set the numeric target. Until then, improvement is continuous: a candidate must beat the latest comparable baseline.

## Secondary metrics

- `visual_judge_win_rate`
- `brand_asset_fidelity`
- `subtitle_text_token_f1`
- `brand_text_exact_match`
- `brand_pronunciation_pass`
- `screen_policy_compliance`
- `voiceover_wer`
- `word_timing_mae_ms`
- `shot_boundary_mae_ms`
- `generation_latency_seconds`
- `estimated_cost_usd`
- `cost_per_accepted_video_usd`

Unavailable metrics must be emitted as `null` with a reason, never silently omitted or invented.

## Required constraints

- Final video probes and decodes successfully: 100%.
- Expected audio stream is present: 100%.
- Aspect ratio and duration satisfy the scenario contract: 100%.
- No corrupt frames or sustained black segments.
- Voiceover WER is at most `0.05` once ASR evaluation is enabled.
- Subtitle safe-area compliance is 100% once frame-level subtitle detection is enabled.
- Brand assets preserve the approved logo/mascot identity when the scenario requires them.
- Repository and evaluator tests pass.
- Paid generation is disabled unless explicitly authorized, and any authorized live run stays inside its recorded budget.

## Current authorized iteration budget

- Scope ID: `mixed-media-iteration-001`.
- Shared paid-call ceiling: **$10.00 USD** (`10_000_000` micro-USD) for the whole iteration, not per generation.
- The durable ledger is `feedback-loop/video-quality/.state/mixed-media-iteration-001.sqlite3`.
- Every paid request must reserve its fail-closed estimated cost before submission. An accepted provider job remains charged even when the creative is rejected.
- Dry runs, local rendering, FFmpeg evaluation, and stock-provider searches do not consume this paid-call budget. Any paid planner, TTS, vision judge, or generated-media call introduced later must use this same scope.
- No automatic top-up, implicit resubmission, or budget reset is allowed.

## Comparison policy

Use the same scenario, input artifact contract, evaluator version, and observation mode for direct comparisons. Keep a candidate automatically only if the primary metric improves and all required constraints are verified. If some required checks are still pending, the result is `provisional_requires_review` even when the primary score improves.

Evaluator changes are research changes, not ordinary product experiments: bump the evaluator version and establish a fresh baseline.
