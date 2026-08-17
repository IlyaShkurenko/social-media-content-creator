# External integrations

MoneyPrinterTurbo is an orchestration layer. Most generative intelligence is supplied by configurable external providers; final composition is local.

## Text generation

There is no single bundled LLM. `app/models/llm_provider.py` is the canonical registry, while `app/services/llm.py` adapts provider calls. The default configuration selects Moonshot/Kimi, but a usable API key and resolved model are still required. Supported paths include Moonshot, OpenAI-compatible APIs, Gemini, DeepSeek, Qwen, Azure OpenAI, VolcEngine, Grok, MiniMax, Xiaomi MiMo, Cloudflare AI Gateway, ModelScope, Ollama, OneAPI, LiteLLM, Groq, and other configured gateways.

The LLM generates text assets: the script, stock-media search terms, and optional social metadata. It does not render frames or animate images.

## Media discovery and understanding

- Pexels, Pixabay, and Coverr provide downloadable stock video.
- Local files bypass external discovery.
- Material search results are cached and filtered/prioritized for the requested aspect ratio.
- TwelveLabs is optional and requires the `twelvelabs` dependency extra. Term reranking is wired into the task pipeline. The module also exposes an `analyze_clip()` video-understanding helper, but the current production pipeline does not call it, so generated videos receive no automatic semantic QA.

## Speech and subtitles

Speech implementations include Edge/Azure TTS, SiliconFlow, Gemini TTS, Xiaomi MiMo, MiniMax, ElevenLabs, and a separately hosted Chatterbox-compatible service. Uploaded narration and a no-voice mode are also supported.

Subtitle mode `edge` derives timing from the TTS result. Subtitle mode `whisper` uses the local `faster-whisper` package and downloads/loads the configured Whisper model when needed.

## Music and publishing

Background music can come from bundled songs, an uploaded file, or supported generated-music providers such as Sonilo and ElevenLabs video-to-music. Upload-Post is the current publishing integration for TikTok, Instagram, and YouTube.

## Local rendering

MoviePy, Pillow, and FFmpeg handle clip preprocessing, concatenation, transitions, text/subtitle composition, audio mixing, and encoding. Hardware H.264 encoders are optional; unsupported selections fall back to `libx264`.

## Configuration ownership

Provider selection, keys, endpoints, and model overrides belong in untracked `config.toml`, initially copied from `config.example.toml`. Do not hard-code credentials or assume the README's featured provider is the only supported provider.

## Related context

- [Architecture](architecture.md)
- [Video pipeline](video-pipeline.md)
- [Runtime and validation](runtime.md)
