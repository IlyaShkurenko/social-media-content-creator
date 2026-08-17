# Video-quality evaluation

## What evaluator v0 measures

The evaluator consumes a fixed scenario plus a rendered MP4. It:

1. probes streams, duration, dimensions, and frame rate with FFprobe;
2. decodes the full video with FFmpeg to catch corrupt media;
3. detects sustained black segments;
4. extracts one evidence frame from the midpoint of every declared storyboard scene;
5. calculates timeline-alignment precision, recall, and F1 from frozen per-scene visual tags;
6. compares subtitle tokens with the stored script when both artifacts exist;
7. records unavailable metrics explicitly.

The initial `human_fixture` tags were assigned from the four extracted scene frames of the controlled Coverr smoke video. This establishes a reproducible evaluator and exposes the weak stock-footage alignment, but it is not automated visual understanding.

## Interpretation

`timeline_alignment_f1` keys every tag by scene ID. A concept appearing in the wrong scene is therefore a false positive in one scene and a false negative in the intended scene. The metric intentionally rewards both relevance and timing.

Hard checks currently enforced are decode success, audio presence, aspect ratio, duration, and the absence of sustained black segments. ASR WER, word timing, shot-boundary timing, subtitle safe-area detection, brand fidelity, latency, and cost remain pending in evaluator v0. A candidate cannot be automatically accepted while a required constraint is pending.

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
make evaluate EXPERIMENT=experiments/001-baseline
make experiment SLUG=ordered-materials HYPOTHESIS="Ordered semantic retrieval improves scene alignment"
```

`make baseline` and `make experiment` always allocate the next numeric experiment directory. Re-evaluation may refresh generated metrics in an existing directory but does not create a new experiment identity.

## TICT brand fixture

`evals/assets/brand/brand-kit.json` records Figma provenance, exported logo/mascot assets, product screens, and confirmed variables. The typography source currently fails through the Figma connector because the connected user lacks the required access; no font name has been guessed.
