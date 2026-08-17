# Video pipeline

The canonical orchestration path is `_run_pipeline()` in `app/services/task.py`. WebUI, API, and CLI eventually construct `VideoParams` and enter this service layer.

## Stages

1. Validate paid/generated-music prerequisites when that mode is enabled.
2. Use the supplied script or ask the configured LLM to generate one.
3. For non-local media, use supplied search terms or ask the LLM to produce them. Optional TwelveLabs reranking can reorder global terms.
4. Use uploaded narration or synthesize speech through the selected TTS provider.
5. Build subtitles from TTS timing (`edge`) or transcribe audio with local `faster-whisper` (`whisper`).
6. Obtain media from Pexels, Pixabay, Coverr, or the managed local-media directory.
7. Assemble source clips to the narration duration, optionally applying transitions and script-order matching.
8. Composite narration, subtitles, and background music; encode final MP4 files.
9. Persist task state and artifacts, then optionally schedule TikTok, Instagram, or YouTube publishing.

The pipeline supports early stop points for script, terms, audio, subtitles, and materials as well as complete video generation.

## Local media behavior

With `video_source="local"`, keyword generation and stock search are skipped. Local files are represented as `MaterialInfo(provider="local", ...)` and must resolve inside `storage/local_videos` by the time they reach the service layer. The WebUI/API upload endpoints place files there; the CLI may copy explicitly supplied external files into that managed directory.

The current renderer accepts both video and image formats. Images are opened with Pillow/MoviePy, converted into fixed-duration clips, resized/cropped, and encoded before normal concatenation. This is a static-image treatment: there is no image-to-video diffusion model, mascot animation, pose control, lip sync, or character-consistency subsystem in the current pipeline.

## Important invariants

- Local-media mode requires valid `video_materials`; it does not fall back to stock footage.
- Custom narration bypasses TTS. Subtitles for custom audio require explicit Whisper mode because no TTS timing object exists.
- Whisper is loaded only when configured; an Edge subtitle failure does not silently download a multi-gigabyte model.
- `match_materials_to_script` forces ordered term acquisition and sequential concatenation.
- Multiple generated variants normally randomize source ordering unless script-order matching is enabled.
- Media, upload, and artifact paths must stay within their managed roots.
- Task outputs are written under `storage/tasks/<task-id>/`, including `script.json`, intermediate media, audio/subtitles, and `final-*.mp4`.

## Related context

- [Architecture](architecture.md)
- [External integrations](integrations.md)
- [Video-quality feedback loop](evaluation.md)
- [Runtime and validation](runtime.md)
