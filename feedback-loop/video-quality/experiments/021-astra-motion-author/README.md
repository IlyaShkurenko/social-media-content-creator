# 021 — Astra motion author

## Frozen plan

Baseline: commit 4037fc9, experiment 020 final revised 20-second render.
This is an explicitly requested provider integration and authorship comparison,
not an optimization of the incompatible airport evaluator. Baseline offline
hash replay runs before implementation; the user-owned mascot preview is untouched.

Problem: the runtime author is pinned to Gemini; the requested quality-first
director/source-author role cannot select Astra independently of video judging.

Hypothesis: Astra can create a coherent, technically valid alternative construction
from the same brief and exact available materials; subjective superiority requires
inspection of both MP4s and is not inferred from model reputation or rendering.

Change: add a selectable Astra author through the existing injected interface;
keep Gemini judging, renderer, material bytes and user brief unchanged. Astra
chooses a fresh plan and source without seeing the baseline source. No new Runway
or stock material requests. Retain every failed attempt and final MP4.

Expected: provider-selection/budget/response-contract tests pass; a real 20-second
MP4 passes the unchanged technical checks; request-specific diagnostics and human
comparison remain separate from technical acceptance. No invented win-rate score.

Budget: remaining shared $2.631993, no reset or new durable reservations. Author
calls and optional one Gemini review must fit remaining funds. The historical
brief contains an outdated budget hint; the trusted ledger remains authoritative.

Disposition: planned; integration acceptance and creative acceptance are separate.

## Implementation and initial evidence

Baseline hash replay passed before production edits. New ADPIPE-3.4 tests were
RED on the absent author module, then passed after implementation. Astra/Gemini
selection is independent of the judge/legacy provider; resume binds author identity.
The comparison mode verifies the immutable rendered snapshot, reuses materials
and provenance, disables acquisition and withholds the previous source.

The first real Astra plan request counted 13,505 input tokens and was rejected
with HTTP 400. No plan or MP4 was created. The first error handler unfortunately
retained only HTTP status, losing the provider's detailed reason; tests now cover
sanitized error code/parameter/message retention and explicit rejection accounting.
The initial conservative $0.988013 ledger entry is not an invoice and remains
unreconciled; it must not be represented as successful generation spend.

A separate public-text compatibility probe succeeded with 19 input / 8 output
tokens ($0.000638 conservative charge). A synthetic white-square image probe
returned incomplete with zero reported tokens; it contained no private assets.
These probes establish basic API connectivity, not a completed advertising run.
The external approval reviewer rejected the private-material retry. An explicit
async confirmation was requested; no indirect/private-data retry was performed.

Initial full repository validation: 758 passed, 12 skipped, 4183 subtests; coverage
76%; lint passed. Additional error-retention tests were added after that run.
Creative acceptance and the real comparison remain pending; no quality gain claimed.

## Completed live comparison

The user explicitly approved the retry. `astra-02` retained the actual error:
JSON-mode validation requires the word JSON in an input message; placing it only
in the top-level instructions field was insufficient. The adapter now adds that
transport instruction to input and a regression assertion verifies it. Both
rejected attempts remain retained. `artifacts/budget-reconciliation.json` records
the correction of the initial erroneous worst-case estimate: the identical
request was explicitly rejected before generation. The cap was not changed.

`astra-03` produced a new six-beat plan and a complete React source; its first
source attempt compiled and rendered successfully without human composition edits.
It uses the identical 20-second brief and all seven hash-matching available assets,
but chooses only logo, mascot, a real trip-overview crop and existing music for
the visible/audible construction. The prior source was not provided. No new Runway,
Pexels or speech call occurred. Music-only is an explicit creative choice under
the unchanged `audio_mode=auto`, not a missing-audio failure.

- [Astra candidate MP4](artifacts/video.mp4)
- [Gemini baseline MP4](../020-free-montage-pilot/artifacts/video.mp4)
- Editable source/contract: `artifacts/astra-03/attempt-01/`
- Plan and all provider evidence: `artifacts/astra-03/`
- Diagnostic resolution: `artifacts/review-resolution.json`

The candidate is 720×1280, 30 FPS, exactly 20.000 seconds. Duration, geometry,
audio presence/audible signal and full decode pass unchanged technical checks;
offline hash replay passes. MP4 SHA256:
`73546ad4f879e8644565aa07193db64cacac7456eb60d9b90dbe801c4afad303`.

Gemini completed its agentic diagnostic review. It reported one medium issue:
the opening headline allegedly stays stationary instead of moving/shrinking.
Decoded frames at 2.75, 3.2, 3.8 and 4.05 seconds visibly disprove that claim.
The original judge evidence remains intact, with a separate agent inspection
record; no source change or fabricated acceptance score followed the false alarm.

Known limitation: the plan requested music ducking around the pause, but the
existing trusted audio wrapper plays the continuous asset and has no volume
envelope. The author-selection integration does not add this unrelated renderer
capability. The input app capture also contains inconsistent demo dates. The new
film is restrained, typography-led and music-only; it is not automatically better
advertising than the Gemini film and remains pending the user's viewing preference.

Validation: 759 repository tests and 4183 subtests passed, 12 skipped; coverage 76%;
41 targeted motion tests passed (one opt-in sandbox test skipped); 104 evaluator
tests passed; lint and diff checks passed. Conservative experiment cost including
the public probe, Astra planning/source and Gemini judging: **$1.155485**.
Remaining shared balance: **$1.476508**. Rejected requests did not generate content;
their erroneous initial estimate was reconciled with preserved evidence.

Disposition: retain the user-requested provider integration based on executable
contracts and successful real end-to-end rendering. Creative superiority and
production acceptance remain unmeasured/pending; this is not a score-based keep
or replacement of the old evaluator baseline.

Reproduce locally without another provider call:

```bash
make motion-verify OUTPUT=feedback-loop/video-quality/experiments/021-astra-motion-author/artifacts/astra-03/attempt-01/render
```

## Subsequent product-owner review

The user rejected the retained candidate's creative quality: it mainly displays
text followed by an unframed static app capture and feels unfinished. This label
applies to MP4 `73546ad4f879e8644565aa07193db64cacac7456eb60d9b90dbe801c4afad303`;
it does not change the historical technical measurements. The requested author
integration is retained, but this film is not a creative improvement or an accepted
production reference. Experiment 022 tests action-led acquisition and direction
instead of another author swap or the same frozen pool of still materials.
