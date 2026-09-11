"""ADPIPE-3.3: runtime authorship and immutable request boundaries."""
import json

import pytest

from app.services.creative.motion_agent import run_agent
from app.services.creative.motion import sha256


def brief(duration):
    return {"request": "Make a restrained editorial animation.", "hypothesis": "Clarity helps.",
            "duration_seconds": duration, "width": 720, "height": 1280, "fps": 30,
            "language": "en-US", "required_assets": [], "audio_mode": "silent"}


@pytest.mark.parametrize("duration", [10, 60])
def test_runtime_source_not_template(tmp_path, duration):
    seen = []

    def author(stage, payload, directory):
        seen.append(stage)
        if stage == "plan":
            return {"hypothesis": "Clarity helps.", "beats": [
                {"id": "one", "start": 0, "end": duration, "intent": "Editorial reveal"}],
                "materials": [], "audio": {"mode": "silent", "asset_ids": []}}
        return {"source": f"export default function Film() {{ return <div>{duration}</div>; }}",
                "change_hypothesis": "Implement the requested editorial reveal."}

    def renderer(project, output):
        output.mkdir()
        (output / "video.mp4").write_bytes(b"test-only-not-a-video")
        return {"technical_pass": True}

    report = run_agent(brief(duration), {}, tmp_path / "run", author=author,
                       tools={}, renderer=renderer)
    assert seen == ["plan", "author"]
    assert report["status"] == "pending_review"
    assert str(duration) in (tmp_path / "run/attempt-01/Scene.tsx").read_text()
    contract = json.loads((tmp_path / "run/attempt-01/project.json").read_text())
    assert contract["duration_seconds"] == duration


def test_unsupported_tool_never_silently_substitutes(tmp_path):
    def author(stage, payload, directory):
        return {"hypothesis": "Clarity helps", "beats": [
            {"id": "one", "start": 0, "end": 10, "intent": "Editorial reveal"}],
            "audio": {"mode": "silent", "asset_ids": []},
            "materials": [{"id": "x", "tool": "unknown"}]}
    with pytest.raises(ValueError, match="unsupported"):
        run_agent(brief(10), {}, tmp_path / "run", author=author, tools={})
    assert (tmp_path / "run/failure.json").exists()


def test_invalid_duration_rejected_before_author(tmp_path):
    def forbidden(*args):
        pytest.fail("invalid request reached paid author")
    invalid = brief(10)
    invalid["duration_seconds"] = float("nan")
    with pytest.raises(ValueError, match="duration"):
        run_agent(invalid, {}, tmp_path / "invalid", author=forbidden, tools={})


def test_unbound_feedback_rejected_before_author(tmp_path):
    def forbidden(*args):
        pytest.fail("unbound feedback reached author")
    with pytest.raises(ValueError, match="existing rendered"):
        run_agent(brief(10), {}, tmp_path / "invalid", author=forbidden,
                  tools={}, feedback={"confirmed": True})


def test_confirmed_revision_preserves_plan_and_uses_new_source(tmp_path):
    calls = []

    def author(stage, payload, directory):
        calls.append(stage)
        if stage == "plan":
            return {"hypothesis": "Clarity helps", "beats": [{"id": "one", "start": 0,
                "end": 10, "intent": "Readable type"}], "materials": [],
                "audio": {"mode": "silent", "asset_ids": []}}
        return {"source": f"export default () => <div>{len(calls)}</div>;", "change_hypothesis": "Larger type"}

    def renderer(project, output):
        output.mkdir()
        (output / "video.mp4").write_bytes(b"fixture-only")
        return {"technical_pass": True}

    original = tmp_path / "original"
    first = run_agent(brief(10), {}, original, author=author, tools={}, renderer=renderer)
    feedback = {"confirmed": True, "video_sha256": sha256(original / first["video"]),
                "evidence": "Human inspected the retained frame: text is too small.",
                "hypothesis": "Larger text will improve legibility."}
    revision = tmp_path / "revision"
    run_agent(brief(10), {}, revision, author=author, tools={}, renderer=renderer,
              revision_of=original, feedback=feedback)
    assert calls == ["plan", "author", "repair"]
    assert (original / "plan.json").read_text() == (revision / "plan.json").read_text()
    assert (original / "attempt-01/Scene.tsx").read_text() != (revision / "attempt-01/Scene.tsx").read_text()


def test_resume_does_not_repeat_ambiguous_author_call(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "brief.json").write_text(json.dumps(brief(10)))
    (run / "plan.submitted.json").write_text('{}')
    with pytest.raises(ValueError, match="ambiguous"):
        run_agent(brief(10), {}, run, author=lambda *args: pytest.fail("resubmitted"), tools={}, resume=True)
