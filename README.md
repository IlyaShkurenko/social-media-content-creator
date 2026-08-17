# Social Media Content Creator

An AI-assisted production system for creating, evaluating, and iterating social media content, with an initial focus on short-form video and advertising creatives.

The project is intended to turn a product brief into multiple publishable content variants without requiring a separate scriptwriter, editor, voice actor, and media researcher for every iteration.

## Project status

The repository already contains a working short-video assembly pipeline. It can generate scripts, find or accept media, synthesize voiceover, create subtitles, mix music, and render final videos.

The broader advertising-production workflow is under active development. Structured storyboards, generated video providers, mascot animation, exact product-screen composition, automated visual evaluation, and campaign-performance feedback are planned capabilities rather than completed features.

## Current capabilities

- Generate video scripts and stock-footage search terms with an LLM.
- Use local media or retrieve footage from Pexels, Pixabay, and Coverr.
- Generate voiceover through supported speech providers or use uploaded audio.
- Create and style subtitles.
- Add background music and control narration and music volume.
- Assemble portrait `9:16` and landscape `16:9` videos with MoviePy and FFmpeg.
- Produce multiple video variants in one run.
- Operate through a WebUI, FastAPI service, or CLI.
- Publish completed content through the existing social-platform integrations.
- Evaluate rendered MP4 files through a versioned experimental feedback loop.

## Target workflow

```text
Product brief and brand assets
        ↓
Advertising hypotheses
        ↓
Up to five distinct storyboards
        ↓
Stock, uploaded, product, and generated media
        ↓
Voiceover, music, subtitles, and timed composition
        ↓
Rendered social media videos
        ↓
Technical, visual, and timeline evaluation
        ↓
Experiment history and the next controlled iteration
```

The intended production system will support:

- generating several meaningfully different creative hypotheses for one product;
- describing every scene, action, asset, line, and timestamp in a structured storyboard;
- combining travel or lifestyle footage with exact application screenshots;
- animating approved mascot and brand assets through image-to-video providers;
- keeping generated footage, voiceover, subtitles, and product actions synchronized;
- measuring improvements without changing several unrelated variables in one experiment;
- using real campaign metrics such as retention, click-through rate, and conversion when they become available.

## How the current pipeline works

1. A user provides a subject, requirements, or a complete script.
2. The selected LLM generates or refines the script and extracts media-search terms.
3. The pipeline resolves local assets or downloads matching stock footage.
4. A speech provider generates narration unless uploaded audio or no voiceover is selected.
5. Subtitle timing is generated from the narration or transcription path.
6. Clips are normalized, ordered, composited, and mixed with narration and music.
7. FFmpeg renders the final video into the task artifact directory.

The current pipeline is primarily an orchestration and rendering system. It does not yet contain a general-purpose generative video model. Generated-video providers will be connected through explicit adapters so that models and costs can be compared experimentally.

## Local setup

Python 3.11 or later is required. Keep project dependencies inside a local virtual environment.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config.example.toml config.toml
```

API keys and provider settings belong in the untracked `config.toml`. They can also be configured from the WebUI. Never commit credentials.

### Start the WebUI

```bash
.venv/bin/python -m streamlit run webui/Main.py
```

### Start the API

```bash
.venv/bin/python main.py
```

API documentation is available at `http://127.0.0.1:8080/docs` after the server starts.

### Use the CLI

```bash
.venv/bin/python cli.py --help
```

## Configuration

The main provider groups are:

- LLM providers for scripts, hypotheses, keywords, and metadata;
- media providers for stock footage and local uploads;
- speech providers for voiceover;
- music providers or local background tracks;
- publishing providers for supported social platforms.

Copy `config.example.toml` to `config.toml`, then configure only the providers needed for a particular workflow. Paid generation must be explicitly enabled and evaluated with a recorded budget.

## Quality and experimentation

The video-quality feedback loop lives under `feedback-loop/video-quality/`. It stores a fixed goal, evaluator contracts, scenarios, sequential experiment records, and reproducible commands.

```bash
cd feedback-loop/video-quality
make verify
make baseline
```

The current evaluator verifies technical properties such as decoding, duration, aspect ratio, audio presence, black segments, subtitle agreement, and fixed-fixture timeline alignment. Automated ASR timing, vision-based action checks, brand fidelity, visual quality, cost, and latency are being introduced incrementally.

Product experiments should change one coherent variable at a time. A higher primary score is not automatically accepted while required constraints remain unverified.

## Tests

Run the selected behavior specifications:

```bash
make test-bdd
```

Run the complete test suite:

```bash
.venv/bin/python -m pytest -q test
```

Run lint:

```bash
.venv/bin/python -m ruff check app cli.py main.py webui test
```

## Repository structure

- `app/` — application models, controllers, and service layer.
- `webui/` — Streamlit interface.
- `main.py` — FastAPI entrypoint.
- `cli.py` — command-line entrypoint.
- `feedback-loop/video-quality/` — evaluator, goals, scenarios, and experiment history.
- `docs/` — architecture, integration, runtime, specification, and testing context.
- `test/` — unit, controller, integration, and BDD tests.
- `resource/` — fonts, music, and other bundled rendering resources.
- `storage/` — generated tasks and local artifacts.

## Development direction

The next major milestone is a specification and controlled benchmark for a mixed-media advertising pipeline. It will compare generated-video providers and editing strategies before any one provider or effect is treated as the production default.

The first experiments will focus on:

1. mascot identity preservation during image-to-video animation;
2. travel and lifestyle scene quality;
3. transitions from generated footage into exact product screens;
4. voiceover and scene timing alignment;
5. deterministic brand and subtitle composition;
6. cost, latency, and repeatability across providers.

## License

This repository is distributed under the MIT License. See `LICENSE` for details.
