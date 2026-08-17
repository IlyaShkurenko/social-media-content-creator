# Video-quality evaluation

## What evaluator 0.4 measures

The evaluator consumes a fixed scenario plus a rendered MP4. It:

1. probes streams, duration, dimensions, and frame rate with FFprobe;
2. decodes the full video with FFmpeg to catch corrupt media;
3. detects sustained black segments;
4. extracts one evidence frame from the midpoint of every declared storyboard scene;
5. calculates timeline-alignment precision, recall, and F1 from frozen per-scene visual tags;
6. compares subtitle tokens and exact canonical `tict` spelling with the stored script;
7. evaluates every scene's declared screen policy from structured screen evidence;
8. records generation latency and estimated cost when provider evidence exists;
9. records unavailable metrics explicitly.

The initial `human_fixture` tags were assigned from extracted evidence frames. They establish reproducible observations, including the distinction between generic non-product UI and approved tict UI, but they are not automated visual understanding.

## Interpretation

`timeline_alignment_f1` keys every tag by scene ID. A concept appearing in the wrong scene is therefore a false positive in one scene and a false negative in the intended scene. The metric intentionally rewards both relevance and timing.

Hard checks currently enforced are decode success, audio presence, aspect ratio, duration, the absence of sustained black segments, evidence-frame extraction, and subtitle safe-area compliance when renderer evidence is available. Exact `tict` spelling and scene screen-policy compliance are scored. Timestamped ASR, rendered-audio pronunciation, word timing, automatic shot boundaries, automated screen understanding, and brand-asset fidelity remain pending. A candidate cannot be automatically accepted while a required constraint is pending.

Screen policy is intent-aware:

- `approved_product_ui` requires the approved tict capture and identity evidence;
- `non_product_context` permits generic or hidden UI and forbids claiming it is tict;
- `screen_hidden` requires no readable device display;
- `unconstrained` does not require a screen class.

## Target evaluator architecture

```text
MP4 + storyboard
  -> FFmpeg scene boundaries and evidence frames
  -> audio extraction
  -> timestamped ASR
  -> versioned vision judge for objects/actions/text/brand identity
  -> timeline and transcript comparison
  -> deterministic metrics.json + evidence
```

The vision judge must return structured observations rather than a single aesthetic score. Expected fields include scene objects, actions, visible product UI, logo/mascot fidelity, on-screen text, confidence, and supporting timestamps. Pairwise aesthetic preference can be a secondary metric, never the sole primary score.

## Commands

Run from `feedback-loop/video-quality`:

```bash
make verify
make baseline
make evaluate EXPERIMENT=experiments/008-baseline
make experiment SLUG=ordered-materials HYPOTHESIS="Ordered semantic retrieval improves scene alignment"
```

`make baseline` and `make experiment` always allocate the next numeric experiment directory after validating every input path. Successful records track only `README.md`, `metrics.json`, and a content-addressed `inputs.json`; failed evaluation also retains `evaluator.stderr.log`. Video snapshots and extracted frames stay under ignored `artifacts/`. `make evaluate` verifies the referenced hashes, reconstructs sanitized inline inputs under ignored `.state/replay/`, and refreshes metrics without creating a new experiment identity. Historical experiment layouts remain untouched.

## tict brand fixture

`evals/assets/brand/brand-kit.json` records Figma provenance, exported logo/mascot assets, product screens, and confirmed variables. The typography source currently fails through the Figma connector because the connected user lacks the required access; no font name has been guessed.
