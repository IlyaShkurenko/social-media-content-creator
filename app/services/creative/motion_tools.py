"""Trusted material tools. Generated React never receives these clients or keys."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
import wave

import httpx
import numpy as np

from app.services.creative.motion import sha256, write_json
from app.services.creative.runway import RunwayAdapter, RunwayVideoRequest


def synth_music(request: dict, directory: Path, brief: dict) -> Path:
    """Small original per-request sketch score, not a generative music model."""
    bpm = float(request.get("bpm", 108))
    notes = request.get("notes", [60, 64, 67, 71])
    if not 50 <= bpm <= 180 or not 2 <= len(notes) <= 16 or any(not 36 <= n <= 90 for n in notes):
        raise ValueError("music tempo or MIDI notes outside supported range")
    rate, duration = 44100, float(brief["duration_seconds"])
    sound = np.zeros(round(duration * rate), dtype=np.float64)
    beat = 60 / bpm
    rng = np.random.default_rng(1234)
    for i, start in enumerate(np.arange(0, duration, beat / 2)):
        length = min(round(.7 * rate), len(sound) - round(start * rate))
        t = np.arange(length) / rate
        note = notes[i % len(notes)]
        f = 440 * 2 ** ((note - 69) / 12)
        # Soft mallet with harmonics, sparse bass, kick and brushed texture.
        attack = np.minimum(t / .008, 1)
        hit = .14 * (np.sin(2 * np.pi * f * t) + .3 * np.sin(4 * np.pi * f * t)) * np.exp(-t * 7) * attack
        if i % 8 == 0:
            hit += .14 * np.sin(2 * np.pi * f / 4 * t) * np.exp(-t * 4) * attack
        if i % 4 == 0:
            hit += .14 * np.sin(2 * np.pi * (55 * t + 1.7 * (1 - np.exp(-t * 35)))) * np.exp(-t * 18)
        if i % 2:
            hit += .02 * rng.normal(size=length) * np.exp(-t * 70) * attack
        begin = round(start * rate)
        sound[begin:begin + length] += hit
    fade = np.minimum(np.arange(len(sound)) / (rate * .06), 1)
    fade *= np.minimum(np.arange(len(sound))[::-1] / (rate * 1.2), 1)
    sound *= fade
    sound *= .72 / max(.72, float(np.max(np.abs(sound))))
    target = directory / f"{request['id']}.wav"
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((sound * 32767).astype("<i2").tobytes())
    write_json(directory / f"{request['id']}.provenance.json", {
        "source": "original_procedural_score", "request": request,
        "duration_seconds": duration, "sha256": sha256(target)})
    return target


def build_tools(config: dict, ledger, prefix: str) -> dict:
    def pexels(request, directory, brief):
        keys = config.get("pexels_api_keys", [])
        key = keys[0] if isinstance(keys, list) else keys
        if not key:
            raise ValueError("Pexels key missing")
        with httpx.Client(timeout=90, follow_redirects=False) as client:
            response = client.get("https://api.pexels.com/v1/search",
                headers={"Authorization": key}, params={"query": request["query"],
                "per_page": 1, "orientation": "portrait"})
            if response.status_code != 200:
                raise RuntimeError(f"Pexels search HTTP {response.status_code}")
            photos = response.json().get("photos", [])
            if not photos:
                raise ValueError("Pexels returned no matching photos")
            photo = photos[0]
            url = photo["src"]["large2x"]
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "images.pexels.com":
                raise ValueError("unexpected Pexels download host")
            media = client.get(url)
            if media.status_code != 200 or len(media.content) > 25_000_000:
                raise ValueError("Pexels download failed or too large")
        target = directory / f"{request['id']}.jpg"
        target.write_bytes(media.content)
        write_json(directory / f"{request['id']}.provenance.json", {
            "provider": "pexels", "query": request["query"], "photo_id": photo["id"],
            "source_page": photo["url"], "photographer": photo["photographer"],
            "sha256": sha256(target), "license_review": "https://www.pexels.com/license/"})
        return target

    def runway(request, directory, brief):
        adapter = RunwayAdapter(api_key=config["runway_api_key"], budget_ledger=ledger)
        duration = request.get("duration_seconds", 5)
        if duration not in (5, 10):
            raise ValueError("Runway tool supports 5 or 10 second source clips")
        ratio = "720:1280" if brief["height"] > brief["width"] else "1280:720"
        spec = RunwayVideoRequest(prompt_text=request["prompt_text"], model="gen4.5",
                                 duration_seconds=duration, ratio=ratio)
        job = adapter.submit(spec, operation_id=f"{prefix}-material-{request['id']}")
        record = directory / f"{request['id']}.provenance.json"
        write_json(record, job.to_record())
        job = adapter.wait(job)
        write_json(record, job.to_record())
        paths = adapter.download_outputs(job, directory / request["id"])
        write_json(record, {**job.to_record(downloaded_paths=paths), "sha256": sha256(paths[0])})
        return paths[0]

    def voice(request, directory, brief):
        import edge_tts
        voice_name = request.get("voice", "en-US-JennyNeural")
        if not voice_name.startswith(brief["language"][:2] + "-"):
            raise ValueError("voice language conflicts with brief")
        target = directory / f"{request['id']}.mp3"
        spoken = request["text"]
        for name, pronunciation in brief.get("pronunciations", {}).items():
            spoken = re.sub(r"\b" + re.escape(name) + r"\b", pronunciation, spoken, flags=re.IGNORECASE)
        boundaries = []

        async def synthesize():
            with target.open("wb") as audio:
                async for chunk in edge_tts.Communicate(spoken, voice_name, boundary="WordBoundary").stream():
                    if chunk["type"] == "audio":
                        audio.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        boundaries.append({"text": chunk["text"], "start_seconds": chunk["offset"] / 10_000_000,
                                           "duration_seconds": chunk["duration"] / 10_000_000})
        asyncio.run(synthesize())
        duration = float(json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_format",
            "-of", "json", str(target)]))["format"]["duration"])
        write_json(directory / f"{request['id']}.provenance.json", {
            "provider": "edge-tts", "request": request, "spoken_text": spoken,
            "word_boundaries": boundaries, "duration_seconds": duration, "sha256": sha256(target)})
        if duration > brief["duration_seconds"]:
            raise ValueError("narration exceeds requested film duration; revise the material request")
        return target

    tools = {"music": synth_music, "voice": voice}
    if config.get("pexels_api_keys"):
        tools["pexels"] = pexels
    if config.get("runway_api_key"):
        tools["runway"] = runway
    return tools
