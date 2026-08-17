# Goal: tict advertising video quality

Build an autonomous, reproducible production loop that turns a tict advertising hypothesis into up to five distinct short-form videos, measures the rendered results against their storyboards, and keeps only demonstrable improvements.

## Acceptance objective

For reviewed product experiments, the controlling outcome is explicit product-owner acceptance of the retained final MP4 after every enforced technical and product constraint passes. This binary human label is the calibration target for future automation; it is not fabricated by the evaluator.

Evaluator `0.6` still reports `visual_judge_win_rate`: the mean candidate credit from two blind Gemini pairwise passes with reversed A/B order. Candidate win is `1.0`, tie is `0.5`, and baseline win is `0.0`. It is a diagnostic model-preference signal, not a percentage of video quality and not the final acceptance authority.

`timeline_alignment_f1` remains a required non-regression metric. It is micro-averaged F1 over `(scene_id, expected_visual_tag)` pairs calculated deterministically from the judge's closed-vocabulary scene observations. Human preference cannot override an alignment, technical, temporal, screen-policy, or brand failure.

## Secondary metrics

- `timeline_alignment_f1`
- `visual_judge_win_rate`
- `brand_asset_fidelity`
- `subtitle_text_token_f1`
- `brand_text_exact_match`
- `brand_pronunciation_pass`
- `screen_policy_compliance`
- `temporal_consistency_pass`
- `temporal_high_severity_event_count`
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
- Every generated hook is sampled at 10 FPS. Reported high-severity events block automatic selection until confirmed; confirmed defects veto and confirmed false positives may be cleared with artifact-bound review evidence.
- Every evaluated experiment retains a hash-matching `artifacts/video.mp4`.
- Voiceover WER is at most `0.05` once ASR evaluation is enabled.
- Subtitle safe-area compliance is 100% once frame-level subtitle detection is enabled.
- Brand assets preserve the approved logo/mascot identity when the scenario requires them.
- Repository and evaluator tests pass.
- Paid generation is disabled unless explicitly authorized, and any authorized live run stays inside its recorded budget.

## Current authorized iteration budget

- Scope ID: `mixed-media-iteration-001`.
- Shared paid-call ceiling: **$10.00 USD** (`10_000_000` micro-USD) for the whole iteration, not per generation.
- The durable ledger is `feedback-loop/video-quality/.state/mixed-media-iteration-001.sqlite3`.
- Every paid request must pass a fail-closed budget check before submission. The check creates no durable reservation; completed calls record their usage-based charge and ambiguous outcomes record a worst-case charge.
- Dry runs, local rendering, FFmpeg evaluation, and stock-provider searches do not consume this paid-call budget. Any paid planner, TTS, vision judge, or generated-media call introduced later must use this same scope.
- No automatic top-up, implicit resubmission, or budget reset is allowed.

## Comparison policy

Use the same scenario, input artifact contract, evaluator version, and observation mode for direct comparisons. Automated model-preference evidence may rank candidates, but a product candidate is kept only when all enforced constraints pass and either the configured automatic acceptance contract is satisfied or the product owner explicitly accepts the exact retained artifact. Pending subjective checks may be resolved by that review; enforced failures may not.

Evaluator measurement changes are research changes, not ordinary product experiments: bump the evaluator version and establish a fresh baseline. Acceptance-policy changes preserve the original automated metrics and require an auditable review record.
