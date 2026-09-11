# 020 — Agent-authored free montage pilot

## Frozen plan (before implementation)

Baseline revision: e71fcd6. Prior experiment: 019 (diagnostic evaluator baseline).
This is an architecture pilot under RFC-0011, not a claim of a better old score.
The legacy experiment-start command assumes the fixed storyboard and a completely
clean tree; the user-owned untracked mascot-rig-preview.mp4 is preserved untouched.
This record establishes a separate construction baseline instead of mislabelling
that video with an incompatible pairwise metric.

Problem: the executor fixes the montage into three slots, limiting rhythm and
semantic transitions even when the creative concept changes.

Hypothesis: an agent-authored composition can communicate saved-travel-idea chaos
turning into a coherent trip, using multiple visual beats, purposeful silence,
kinetic typography and exact app captures, without a generated person/phone shot.

Change: add an opt-in Remotion executor and one editable tict pilot. Keep the
legacy production path and judges unchanged. Use brand cream/yellow/dark, English
copy, approved raw screenshots, freshly sourced Dubai stock and original audio.
No voiceover is intentional for this first music-led concept, not a failed TTS job.

Expected evidence: real MP4 with audible soundtrack, correct declared duration,
editable source and exact inputs; varied beat count; independent request-specific
diagnostics. Do not convert successful rendering into a creative score of 1.0.
Budget: no new paid generation; at most one existing budgeted agentic judge call.

## User correction before production implementation

The user rejected the narrow example-composition scope. The revised, authorized
change is an actual runtime authoring agent and generic React executor. The
creative construction must be chosen by that agent from the brief, not hardcoded
as the previously proposed travel/chaos/app/finale sequence. Keep this original
plan for history; do not pretend it predicted the revised work. Success is an
end-to-end agent-authored render plus tests of different requests and bounded
revision, not a subjective score improvement. No product-code change existed at
the time of correction; initial RED was a missing motion module on test collection.

Disposition: pending implementation and user review.

## Delivered architecture and live results

Implemented the runtime authoring architecture, not a hardcoded replacement ad.
Gemini receives a brief/catalog, selects a construction and tools, writes TSX,
receives compilation errors, and can revise a rendered artifact using confirmed
feedback. Remotion owns visual execution; trusted Python owns assets, exact duration,
audio, budget, isolation and provenance. Existing WebUI/template paths are unchanged.

| Retained result | Construction and evidence |
| --- | --- |
| [Revised 20-second ad](artifacts/video.mp4) | Stock-photo/typographic opening, two exact app captures, voice/music and brand close. Runtime-generated React; factual/case correction completed through an actual agent revision. |
| [10-second graphical teaser](artifacts/teaser.mp4) | Music-only kinetic type and mascot; no app UI, phone or live-action. Different request, same executor, first source attempt rendered successfully. |
| [20-second pre-revision version](artifacts/agent-02/attempt-03/render-replay-01/video.mp4) | Retained to inspect the feedback-driven change, not recommended for publication. |

Editable projects: `artifacts/revision-01/attempt-01/` and
`artifacts/teaser-01/attempt-01/`. Each includes `Scene.tsx`, `project.json`, exact
media, prompt and public model response. Full diagnostic observations are in each
run's `review/metrics.json`. No new Runway video job was requested by these plans;
they used a new Pexels photo, Edge voice (with word times), procedural music and
newly generated React source. No old woman/phone footage was substituted.

## What failed and what was learned

- Worker smoke tests 01–09 exposed OS policy needs: dynamic-loader reads, staged
  log writes, headless Chromium IPC/IOKit, a separate fixed DevTools port and
  same-sandbox process termination. Test 10 passed. User browser profiles were
  never made readable; a separate project-local headless browser is installed.
- `agent-01` produced note-name text instead of MIDI numbers and unresolved audio
  mode `auto`. It stopped during material resolution. The integration now validates
  the full plan before material work and supports one bounded plan repair.
- `agent-02/attempt-01` returned malformed JSON-wrapped TSX despite a STOP finish.
  The raw public response and cost are retained. Source transport now uses plain
  TSX, not JSON escaping. The run resumed with verified existing materials.
- Attempt 02 was truncated at MAX_TOKENS. The compiler reported the precise EOF
  error; the agent repaired it in attempt 03 without human source edits.
- The first completed render had 20.000 seconds of video but 20.053333 seconds of
  muxed AAC audio. The strict duration test stayed unchanged; a final FFmpeg mux
  step now trims/re-encodes audio to the requested end time. The zero-provider
  replay produced a technically valid 20.000-second MP4.
- Inspection of actual frames confirmed uppercase `TICT` and unsupplied platform,
  URL and capability claims. The existing Gemini judge missed those issues.
  `artifacts/confirmed-feedback.json` binds the observations to the exact video
  hash. The agent removed the unsupported footer, corrected brand case and replaced
  unsupported badges with descriptions of visible categories. A source diff and
  decoded frames confirm the change; this does not establish overall creative superiority.

The current sidecar judge returned complete, verified agentic navigation for all
three final MP4s. Its claims remain diagnostic: it inconsistently noticed narration
ending early and placeholder copy. The teaser's title differs from the agent's own
planned title while preserving the user concept; this is not an automatic veto.
Original app captures contain placeholder/inconsistent demo content, and the first
ad's narration ends before its visual close. Neither output is declared production
quality. The next improvement should follow the user's actual viewing feedback,
not blindly optimize this judge's wording.

## Validation, cost and disposition

Technical duration/geometry/audio/full-decode checks pass for all three ad outputs.
Sandbox integration fixtures verified denied outside reads/writes and external
network access plus secret-environment removal. Offline replay verifies exact MP4,
contract, source, assets and dependency-lock hashes; it makes no provider calls.
Repository validation: 743 passed, 12 skipped; coverage passes at 76%; lint passes.
The targeted sandbox integration has 11 passing tests, and all 104 evaluator tests
pass. Paid estimate: **$0.844731**, including failed author calls and three
judges. Shared remaining balance: **$2.631993**. These are conservative estimates,
not invoices. No durable reservation was added.

```bash
make motion-test
make motion-verify OUTPUT=feedback-loop/video-quality/experiments/020-free-montage-pilot/artifacts/revision-01/attempt-01/render
make motion-verify OUTPUT=feedback-loop/video-quality/experiments/020-free-montage-pilot/artifacts/teaser-01/attempt-01/render
```

Disposition: deliver the explicitly requested opt-in architecture and evidence;
keep creative acceptance pending user review. This is not a `keep` based on the
old fixed-storyboard score, and does not replace the old evaluator's baseline.
Live entrypoints and limitations are documented in `motion/README.md`.
