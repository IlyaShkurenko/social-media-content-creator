# 022 — Action-led direction

## Current result — diagnostic video retained, user review pending

[Final 20-second MP4](artifacts/video.mp4), SHA256
`8af34b1fdd26e3183a6a1e14e239aadf4e63a627c35be07146589514cde29e08`.
New Runway footage, Astra-authored React, exact tict UI/brand assets and local
procedural instrumental music were rendered through the existing isolated
Remotion worker. No new universal scene template or production-code change was
introduced. This is a reviewed-plan execution experiment, not a creative keep.

Both full generated takes contain confirmed defects. The final diagnostic edit
uses only take 02 source ranges [0,4) and [6,10), each at 0.8x, separated by the
product insert. The known 5.3–5.5s disappearance is excluded, not declared fixed.
The plan's book-closing action is absent; the emotional problem is less explicit
than intended. The UI insert remains an editorial cut, not tracked screen
replacement. A new architecture is not being claimed for this continuation.

The first assembled render passed decode/audio checks but had unintended cream
background at 4–5s and 14–15s. The coding-agent hint and generated code both
misused `endAt` with slowed video: the installed Remotion implementation bounds
the internal sequence duration without stretching that bound by playback rate.
Removing the two `endAt` props and retaining outer 150-frame sequences fixes the
local timing bug. Original model response, source snapshot and failed MP4 remain
at `artifacts/film-01/attempt-01/render/`; corrected immutable source snapshot is
at `artifacts/film-01/trim-corrected-render/project/`. No extra author call or
material generation was used for this correction.

Checks: 20s, 720×1280, video/audio present, full decode, audible signal, no
sustained black segments. A diagnostic 10-FPS uniform-frame scan changed from
20/200 unintended uniform samples to 0/200 after the trim correction. This
exposes a gap in the existing black-only check; it is not a creative-quality
metric. Native frames at scene boundaries, the product zoom and the end card
were inspected. Exact asset hashes are preserved in render provenance.

Gemini agentic review completed with linked processing steps and recognized all
four picture beats. It incorrectly alleged English dialogue about a train to
Paris. Independent decoded-PCM verification found correlation 0.99959 against
the original instrumental in the alleged first-five-second window and 0.99961
over the full track after correcting a 1882-sample codec offset. The generated
video has no audio stream; the only rendered audio asset is the hash-matching
procedural oscillator WAV. The audio allegation is recorded as a false positive;
the raw judge report is unchanged. This does not convert its other subjective
checks into ground truth or confer automatic acceptance.

Current validation: 64 targeted tests passed, 1 skipped; evaluator `make verify`
passed all 104 tests and lint. No dependencies were installed outside `.venv`.
The continuation cost is **$3.616129**: two Runway takes $2.40, two temporal
checks $0.070029, one Astra source call $1.073425, final judge $0.072675. Including
earlier planning, experiment 022 costs **$5.030567**. Shared-scope spending is
**$13.554059 of $20**, leaving **$6.445941**, with no reserved funds. These are
local conservative usage estimates, not invoices.

Decision: retain this candidate and failure evidence for user review. Do not
claim it beats experiment 020/021 or promote it as a production-quality winner.
The next creative choice should follow the user's reaction to this exact MP4;
stronger performance direction and a more continuous product transition remain
open, not automatically solved by the new author model.

## Frozen hypothesis and scope

Baseline: experiment 021, final MP4 SHA256
`73546ad4f879e8644565aa07193db64cacac7456eb60d9b90dbe801c4afad303`.
The user rejected its creative quality: text appearing followed by a flat app
capture felt unfinished. The author integration remains functional; this is not
evidence that the film improved. Experiment 020 was acceptable but too simple.

Hypothesis: allowing the director to acquire action-led live-action footage and
design a motivated connection to the exact product UI will convey the supplied
travel-planning hypothesis more convincingly than a typography-led composition.
This is a creative-production experiment under the existing opt-in architecture,
not a comparison under the incompatible fixed-airport evaluator.

Change: use the full current brand catalog and new material acquisition instead
of experiment 021's frozen-material comparison. Keep Astra authoring, Remotion
rendering, exact-asset policy and request-specific diagnostic judging. No fixed
scene count, mandatory phone, or universal travel template is added to the code.

Expected: visible human action and a coherent relationship between that action,
the product demonstration and the advertising hypothesis; unchanged technical
constraints. Subjective improvement requires review of the retained final MP4,
not inferred scores or a successful render.

## Budget and checkpoints

On 2026-09-11 the user explicitly raised the existing total scope from $10 to
$20, not by $20. Previous charges are preserved. The ledger migration retains a
local database backup and verifies every operation row is unchanged.

First checkpoint: real Astra planning only, with all brand references and the
available material tools. Show the director plan and estimated costs to the
user before executing generated-scene requests. No video exists at this planning
checkpoint; it must not be described as an evaluated video experiment.

Second checkpoint, after plan review: acquire declared materials, retain source
clips, author the composition, render the final MP4, and run technical and
request-specific diagnostic checks. Inspect evidence behind judge allegations;
preserve errors and the exact final video. Do not claim a creative keep before
artifact-bound review.

## Planning checkpoint — subsequently approved by the user

Both real Astra calls completed with all 18 catalog images attached. The first
plan proposed a photograph-gathering action and a six-second static reading-board
UI. Its 1135-character Runway prompt also failed the local 1000-character limit.
No Runway call was made. The original plan, validation error and provider usage
are preserved. A single explicit refinement shortened the prompt and addressed
the coding agent's editorial concern; that concern is not a measured score or
a new user acceptance label.

Proposed concept: **The View Is Waiting**.

| Time | Picture |
| --- | --- |
| 0–5s | Two travelers at a Dubai waterfront café. One is ready to leave; the other asks for a moment while juggling a guidebook, notes and a phone. |
| 5–10s | An explicit editorial cut to a composed handset with the exact tict screenshot. A brief camera-like push makes the destination and categories readable. |
| 10–15s | Return to the second half of the same human take: close the guidebook, look up, exchange a smile, turn toward the waterfront. |
| 15–20s | Exact logo and mascot with a readable “Create your trip.” invitation. |

Music only; no narration requested by this plan. One new ten-second Runway take
supplies both human passages, reducing cross-shot cast drift. The UI insert is
deliberately separate, not a tracked replacement in the generated phone. Exact
midpoint performance remains a generation risk, not a guaranteed result. The
static UI and five-second held ending still need product-owner review; planning
is not proof that the resulting film will be compelling.

Proposed new footage: **$1.20** at the standard Gen-4.5 API rate of $0.12/second,
verified against https://docs.dev.runwayml.com/guides/pricing/ on 2026-09-11.
This excludes authoring, judging, retries and any applicable provider taxes.
The two completed planning calls cost a conservative **$1.414438** combined.
The shared ledger now records **$9.937930** used and **$10.062070** remaining;
there are no reserved funds. These are local usage estimates, not invoices or
provider account balances. The older balance quoted within the model's response
is not authoritative; `metrics.json` captures the post-planning snapshot.

Baseline offline replay passed. Validation: 765 repository tests passed, 12
skipped, 4183 subtests passed; coverage 76%; 104 evaluator tests passed; lint and
diff checks passed. One old default-cap test was updated from $10 to the shared
authorized constant; historical $10 fixture ledgers were not rewritten.

Checkpoint status at that time: **awaiting user review before scene generation**. No new
MP4 yet, no creative improvement claim, no campaign publication. The next run
must reuse the reviewed retained plan rather than pay to silently replan it.

## Production continuation

The user approved the proposed concept ("go ahead"). Production reuses the exact
reviewed plan hash; no further paid director call is made. A six-frame local
OffthreadVideo smoke render passed before source authoring. It is a technical
fixture using the previous MP4, not a candidate or a source in the new ad.

Take 01 is a newly generated ten-second Runway video, provider job
`07e0e28c-4a19-4d94-8413-ece93846b3bb`, SHA256
`dce8cb362cc4749d9732d332b38330a969f8fb428e172abf2e8449c7f751e553`.
It cost $1.20. All 100 temporal samples and 20 timestamped strips are retained.
Gemini's temporal review cost $0.035130 and reported two high-severity phone
disappearances. Independent inspection did not confirm the literal disappearance
claims: hands and pages obscure/reveal the object. However, it confirmed a
different defect around 2.2–2.8 seconds: the rigid phone becomes a bending black
book-like surface. The characters also smile before the intended midpoint cut.
The raw judge response remains unchanged; `artifacts/source-review-01.json` records
the separate findings. Take 01 is rejected, not silently cropped into a keeper.

One explicit additional take tests a bounded choreography hypothesis: a phone
held still in one hand, stationary book and notes, and no page turning reduce
object-identity errors. The concept, duration and cast-continuity approach remain
the same; book closure is deliberately omitted and must not be reported as
matching the original storyboard. The extra estimated cost is $1.20 within the
same $20 scope. Its frozen request and hypothesis are retained before submission
in `artifacts/source-materials-02/hypothesis.json`. No transport retry or previous
provider job is resubmitted. No final creative acceptance has occurred.
## Source take 02: bounded editorial salvage

The second take also failed full-source screening: native frames confirm the phone disappears at 5.3–5.5 seconds. Its complete MP4 and Gemini report are retained; it is **not** a clean source pass. No third generation is requested.

Before source authoring, freeze this salvage hypothesis: exclude source seconds 4–6, play 0–4 and 6–10 at 0.8x around the existing five-second product insert. Preserve the four output beat boundaries. The explicit editorial time ellipsis may remove the defective transition while retaining the same cast, setting and emotional release. It does not repair the source or prove creative improvement. The actual book stays open and the waiting gesture differs from the approved plan; these deviations must remain visible in the record. Final acceptance remains pending.
