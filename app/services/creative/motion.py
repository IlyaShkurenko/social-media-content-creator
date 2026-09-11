"""Opt-in isolated React rendering. No product, scene or provider literals here."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


REPO = Path(__file__).resolve().parents[3]
WORKER = REPO / "motion"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def image_reference(path: Path) -> tuple[str, bytes] | None:
    """Attach every catalog image; rasterize vector/other image formats locally."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if not mime.startswith("image/"):
        return None  # Audio/video remain catalog metadata, not fake image inputs.
    if mime == "image/svg+xml":
        import resvg_py
        svg = path.read_text()
        if re.search(r"<!DOCTYPE|<!ENTITY|@import", svg, re.I):
            raise ValueError(f"SVG reference must be self-contained: {path.name}")
        root = ET.fromstring(svg)
        refs = [v for element in root.iter() for k, v in element.attrib.items()
                if k.rsplit("}", 1)[-1] == "href"]
        refs += re.findall(r"url\(\s*['\"]?([^)'\"]+)", svg, re.I)
        if any(not value.strip().startswith(("#", "data:")) for value in refs):
            raise ValueError(f"SVG reference must be self-contained: {path.name}")
        return "image/png", bytes(resvg_py.svg_to_bytes(svg_string=svg))
    if mime in {"image/png", "image/jpeg", "image/webp"}:
        return mime, path.read_bytes()
    import io
    from PIL import Image
    with Image.open(path) as picture:
        if getattr(picture, "n_frames", 1) > 1:
            raise ValueError(f"Animated image needs explicit frame handling: {path.name}")
        output = io.BytesIO()
        picture.convert("RGBA").save(output, format="PNG")
        return "image/png", output.getvalue()


def managed_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or Path(name).is_absolute():
        raise ValueError("asset/source path must be relative")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("missing or escaping managed file")
    return path


def validate_project(path: Path) -> dict:
    value = json.loads(path.read_text())
    if value.get("schema_version") != "1.0":
        raise ValueError("unsupported motion project version")
    for field in ("language", "hypothesis", "composition_id"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"missing {field}")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", value["composition_id"]):
        raise ValueError("invalid composition ID")
    for field in ("width", "height", "fps"):
        v = value.get(field)
        if type(v) is not int or v <= 0:
            raise ValueError(f"invalid {field}")
    if not (1 <= value["fps"] <= 60 and 64 <= value["width"] <= 2160
            and 64 <= value["height"] <= 3840):
        raise ValueError("geometry exceeds local worker limits")
    duration = value.get("duration_seconds", 0)
    if not isinstance(duration, (float, int)) or not math.isfinite(duration) or not 0 < duration <= 180:
        raise ValueError("duration must be finite and within 180 seconds")
    if abs(duration * value["fps"] - round(duration * value["fps"])) > 1e-6:
        raise ValueError("duration must resolve to whole frames")
    if value.get("user_constraints", {}).get("duration_seconds") != duration:
        raise ValueError("requested and declared durations disagree")
    end, ids = 0.0, set()
    for beat in value.get("beats", []):
        if (not beat.get("intent") or not beat.get("id") or beat["id"] in ids
                or not math.isclose(beat["start"], end, abs_tol=1e-6)
                or not math.isfinite(beat["end"]) or beat["end"] <= end):
            raise ValueError("beats must have unique IDs and contiguous finite timing")
        end = beat["end"]
        ids.add(beat["id"])
    if not math.isclose(end, duration, abs_tol=1e-6):
        raise ValueError("beats must cover the requested duration")
    source = managed_file(path.parent, value["entrypoint"])
    if source.suffix != ".tsx":
        raise ValueError("entrypoint must be TSX")
    assets = value.get("assets", {})
    for asset_id, filename in assets.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", asset_id):
            raise ValueError("invalid asset ID")
        managed_file(path.parent, filename)
    if not set(value.get("required_assets", [])).issubset(assets):
        raise ValueError("required asset unavailable")
    audio = value.get("audio", {})
    if audio.get("mode") not in {"silent", "music", "voiceover", "music_and_voiceover"}:
        raise ValueError("explicit audio mode required")
    audio_ids = audio.get("asset_ids", [])
    if (not set(audio_ids).issubset(assets)
            or (audio["mode"] != "silent" and not audio_ids)
            or (audio["mode"] == "silent" and audio_ids)):
        raise ValueError("audio intent and staged assets disagree")
    return value


def sandbox_profile(stage: Path, port: int, debug_port: int | None = None) -> str:
    """Deny-by-default OS boundary; only one renderer loopback port is reachable."""
    def quoted(path):
        return json.dumps(str(Path(path).resolve()))
    roots = ["/System", "/usr", "/bin", "/sbin", "/Library", "/opt/homebrew",
             "/private/etc", "/private/var/db", "/dev",
             "/Applications/Google Chrome.app", WORKER, stage]
    reads = " ".join(f"(subpath {quoted(p)})" for p in roots)
    debug_port = debug_port or port
    return f'''(version 1)
(deny default)
(allow process* sysctl-read mach* ipc* system-socket iokit* user-preference-read)
(allow signal (target same-sandbox))
(allow file-read* (require-all (require-not (subpath "/Users"))
 (require-not (subpath "/private/var/folders")) (require-not (subpath "/private/tmp"))))
(allow file-read* {reads})
(allow file-read-metadata)
(allow file-write* (subpath {quoted(stage)}) (literal "/dev/null") (literal "/dev/urandom"))
(allow network-bind (local ip "*:{port}"))
(allow network-inbound (local ip "localhost:{port}"))
(allow network-outbound (remote ip "localhost:{port}"))
(allow network-bind (local ip "localhost:{debug_port}"))
(allow network-inbound (local ip "localhost:{debug_port}"))
(allow network-outbound (remote ip "localhost:{debug_port}"))
'''


def worker_environment(stage: Path) -> dict:
    return {"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(stage),
            "TMPDIR": str(stage / "tmp"), "LANG": "en_US.UTF-8",
            "NODE_ENV": "production"}


def inspect_video(video: Path, project: dict) -> dict:
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)
    ]))
    streams = probe["streams"]
    picture = next(s for s in streams if s["codec_type"] == "video")
    duration = float(probe["format"]["duration"])
    expected_audio = project["audio"]["mode"] != "silent"
    checks = {"duration": abs(duration - project["duration_seconds"]) <= 1 / project["fps"] + .01,
              "geometry": (picture["width"], picture["height"]) == (project["width"], project["height"]),
              "audio_stream": any(s["codec_type"] == "audio" for s in streams) == expected_audio}
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video),
                              "-f", "null", "-"], capture_output=True, timeout=180)
    checks["full_decode"] = decoded.returncode == 0 and not decoded.stderr
    if expected_audio:
        levels = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-af",
                                 "volumedetect", "-vn", "-f", "null", "-"],
                                capture_output=True, text=True, timeout=180)
        match = re.search(r"mean_volume: ([-\w.]+) dB", levels.stderr)
        checks["audible_signal"] = bool(match and float(match[1]) > -60)
    return {"checks": checks, "technical_pass": all(checks.values()),
            "duration_seconds": duration, "video_sha256": sha256(video),
            "creative_quality": None, "unavailable_reason": "requires request-specific assessment"}


def render_project(path: Path, output: Path, *, timeout: int = 900) -> dict:
    project = validate_project(path)
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
        raise RuntimeError("isolated worker currently requires macOS sandbox-exec")
    if not (WORKER / "node_modules/@remotion/renderer").is_dir():
        raise RuntimeError("install project-local motion dependencies with npm ci")
    output.mkdir(parents=True, exist_ok=False)
    # Only declared source/assets are copied. Never copy the parent or config.
    snapshot = output / "project"
    snapshot.mkdir()
    staged = {**project, "entrypoint": "Scene.tsx", "assets": {}}
    shutil.copyfile(managed_file(path.parent, project["entrypoint"]), snapshot / "Scene.tsx")
    for aid, filename in project["assets"].items():
        source = managed_file(path.parent, filename)
        relative = f"public/{aid}{source.suffix.lower()}"
        (snapshot / "public").mkdir(exist_ok=True)
        shutil.copyfile(source, snapshot / relative)
        staged["assets"][aid] = relative
    write_json(snapshot / "project.json", staged)
    write_json(output / "provenance.json", {
        "source_sha256": sha256(snapshot / "Scene.tsx"),
        "contract_sha256": sha256(snapshot / "project.json"),
        "lock_sha256": sha256(WORKER / "package-lock.json"),
        "worker_sha256": sha256(WORKER / "render.mjs"),
        "executor_sha256": sha256(Path(__file__)),
        "assets": {a: sha256(snapshot / p) for a, p in staged["assets"].items()},
        "isolation": "macos-seatbelt-loopback-only"})
    try:
        with tempfile.TemporaryDirectory(prefix="motion-") as temporary:
            stage = Path(temporary).resolve()
            shutil.copytree(snapshot, stage / "project")
            (stage / "tmp").mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                debug_port = sock.getsockname()[1]
            policy = stage / "worker.sb"
            policy.write_text(sandbox_profile(stage, port, debug_port))
            command = ["/usr/bin/sandbox-exec", "-f", str(policy),
                       "/opt/homebrew/bin/node", str(WORKER / "render.mjs"),
                       str(stage), str(port), str(debug_port)]
            with (stage / "render.log").open("w") as log:
                process = subprocess.Popen(command, cwd=stage, env=worker_environment(stage),
                                           stdout=log, stderr=log, start_new_session=True)
                try:
                    result = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise RuntimeError("render timeout") from None
            shutil.copyfile(stage / "render.log", output / "render.log")
            if result:
                shutil.copyfile(policy, output / "worker.sb")
                raise RuntimeError(f"render worker exited {result}; inspect retained render.log")
            # Normalize the muxed end time; AAC priming/padding can otherwise
            # extend a frame-exact composition's container beyond the brief.
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(stage / "video.mp4"),
                "-t", str(project["duration_seconds"]), "-c:v", "copy", "-c:a", "aac",
                "-b:a", "192k", "-movflags", "+faststart", str(output / "video.mp4")],
                check=True, capture_output=True, timeout=180)
        metrics = inspect_video(output / "video.mp4", project)
        write_json(output / "metrics.json", metrics)
        if not metrics["technical_pass"]:
            raise RuntimeError("render failed technical checks")
        return metrics
    except Exception as exc:
        write_json(output / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise


def verify_render(output: Path) -> dict:
    """Offline replay verifies the exact saved project, dependencies and video."""
    provenance = json.loads((output / "provenance.json").read_text())
    metrics = json.loads((output / "metrics.json").read_text())
    project_path = output / "project/project.json"
    project = validate_project(project_path)
    checks = {
        "contract": sha256(project_path) == provenance["contract_sha256"],
        "source": sha256(project_path.parent / "Scene.tsx") == provenance["source_sha256"],
        "dependencies": sha256(WORKER / "package-lock.json") == provenance["lock_sha256"],
        "video": sha256(output / "video.mp4") == metrics["video_sha256"],
        "assets": all(sha256(project_path.parent / name) == provenance["assets"][aid]
                      for aid, name in project["assets"].items()),
    }
    if not all(checks.values()):
        raise ValueError("saved rendering evidence hash mismatch")
    return {"status": "verified", "network_calls": 0, "hash_checks": checks,
            "technical_pass": metrics["technical_pass"], "creative_acceptance": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_render(args.project.resolve())))
    else:
        if args.output is None:
            parser.error("output is required for a render")
        print(json.dumps(render_project(args.project.resolve(), args.output.resolve())))


if __name__ == "__main__":
    main()
