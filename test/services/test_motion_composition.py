"""STORY-3 / ADPIPE-3 executable contract, independent of subjective taste."""

import json
import os
import subprocess
import sys

import pytest

from app.services.creative.motion import (
    WORKER, sandbox_profile, sha256, validate_project, verify_render, worker_environment,
)


def project(tmp_path, duration=10, count=2):
    (tmp_path / "index.tsx").write_text("export {}")
    (tmp_path / "photo.png").write_bytes(b"fixture")
    value = {
        "schema_version": "1.0", "entrypoint": "index.tsx",
        "composition_id": "Pilot", "language": "en-US",
        "hypothesis": "Less planning friction creates anticipation.",
        "user_constraints": {"duration_seconds": duration},
        "width": 720, "height": 1280, "fps": 30,
        "duration_seconds": duration,
        "beats": [{"id": f"beat-{i}", "start": i * duration / count,
                   "end": (i + 1) * duration / count, "intent": "Visible intent"}
                  for i in range(count)],
        "assets": {"photo": "photo.png"}, "required_assets": ["photo"],
        "audio": {"mode": "silent", "asset_ids": []},
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize("duration,count", [(10, 2), (22, 6), (60, 9)])
def test_story_3_1_variable_construction(tmp_path, duration, count):
    path, _ = project(tmp_path, duration, count)
    result = validate_project(path)
    assert result["duration_seconds"] == duration
    assert len(result["beats"]) == count


@pytest.mark.parametrize("mutation", ["gap", "duration", "missing", "escape", "audio"])
def test_story_3_invalid_project_fails_before_render(tmp_path, mutation):
    path, value = project(tmp_path)
    if mutation == "gap":
        value["beats"][1]["start"] += 1
    elif mutation == "duration":
        value["user_constraints"]["duration_seconds"] = 60
    elif mutation == "missing":
        value["required_assets"] = ["unavailable"]
    elif mutation == "escape":
        value["assets"]["photo"] = "../config.toml"
    else:
        value["audio"]["mode"] = "music"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        validate_project(path)


def test_adpipe_3_symlink_escape(tmp_path):
    path, _ = project(tmp_path)
    (tmp_path / "photo.png").unlink()
    (tmp_path / "photo.png").symlink_to(tmp_path.parent / "outside.png")
    with pytest.raises(ValueError):
        validate_project(path)


@pytest.mark.skipif(sys.platform != "darwin" or not os.getenv("MOTION_ISOLATION_TEST"),
                    reason="explicit local macOS sandbox integration test")
def test_adpipe_3_real_isolation(tmp_path, monkeypatch):
    stage = tmp_path / "worker"
    stage.mkdir()
    (stage / "tmp").mkdir()
    sentinel = tmp_path / "private-sentinel.txt"
    sentinel.write_text("test fixture only, not a real credential")
    policy = stage / "test.sb"
    policy.write_text(sandbox_profile(stage, 49001, 49002))
    monkeypatch.setenv("MOTION_TEST_SECRET", "test-secret-not-a-real-key")
    result = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(policy),
        "/opt/homebrew/bin/node", str(WORKER / "isolation-check.mjs"), str(sentinel),
        str(tmp_path / "forbidden.txt")], env=worker_environment(stage),
        cwd=stage, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr + result.stdout
    assert all(json.loads(result.stdout).values())


def test_adpipe_3_replay_detects_artifact_mutation(tmp_path):
    snapshot = tmp_path / "project"
    snapshot.mkdir()
    path, value = project(snapshot)
    (snapshot / "index.tsx").rename(snapshot / "Scene.tsx")
    value["entrypoint"] = "Scene.tsx"
    path.write_text(json.dumps(value))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fixture-only")
    (tmp_path / "metrics.json").write_text(json.dumps({"technical_pass": True, "video_sha256": sha256(video)}))
    (tmp_path / "provenance.json").write_text(json.dumps({
        "contract_sha256": sha256(path), "source_sha256": sha256(snapshot / "Scene.tsx"),
        "lock_sha256": sha256(WORKER / "package-lock.json"),
        "assets": {"photo": sha256(snapshot / "photo.png")}}))
    assert verify_render(tmp_path)["status"] == "verified"
    video.write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_render(tmp_path)
