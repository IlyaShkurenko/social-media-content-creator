import json
from types import SimpleNamespace
import wave

import pytest

from app.services.creative.budget import IterationBudgetLedger
from app.services.creative.motion_agent import GeminiAuthor, validate_plan
from app.services.creative.motion_tools import synth_music


def test_original_music_has_exact_duration_and_signal(tmp_path):
    file = synth_music({"id": "music", "bpm": 110, "notes": [60, 64, 67]},
                       tmp_path, {"duration_seconds": 2})
    with wave.open(str(file)) as audio:
        assert audio.getnframes() == 88200
        assert any(audio.readframes(88200))
    assert json.loads((tmp_path / "music.provenance.json").read_text())["duration_seconds"] == 2


@pytest.mark.parametrize("notes", [[], [1, 100], "C4 E4"])
def test_invalid_music_plan_prevents_tool_execution(notes):
    plan = {"hypothesis": "x", "beats": [{"id": "x", "start": 0, "end": 2, "intent": "x"}],
            "materials": [{"id": "m", "tool": "music", "bpm": 110, "notes": notes}],
            "audio": {"mode": "music", "asset_ids": ["m"]}}
    with pytest.raises(ValueError, match="numeric MIDI"):
        validate_plan(plan, {"duration_seconds": 2}, {}, {"music": object()})


@pytest.mark.parametrize("stage,text", [("plan", '{"hypothesis":"x"}'),
                                        ("author", "```tsx\nexport default () => <div/>;\n```")])
def test_paid_author_records_usage_and_never_repeats(tmp_path, monkeypatch, stage, text):
    ledger = IterationBudgetLedger(tmp_path / "ledger.db", scope_id="test", cap_microusd=1_000_000)
    author = GeminiAuthor("not-a-real-key", ledger, "test", {})
    request = {}

    def post(url, **kwargs):
        request.update(kwargs["json"])
        return SimpleNamespace(status_code=200, json=lambda: {
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "thoughtsTokenCount": 20},
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}]})
    monkeypatch.setattr("app.services.creative.motion_agent.httpx.post", post)
    response = author(stage, {"request": "fixture"}, tmp_path)
    assert response.get("source", response.get("hypothesis"))
    assert request["generationConfig"]["responseMimeType"] == ("application/json" if stage == "plan" else "text/plain")
    assert ledger.snapshot().charged_microusd == 675
    assert "not-a-real-key" not in (tmp_path / f"{stage}.provider.json").read_text()
    with pytest.raises(RuntimeError, match="already submitted"):
        author(stage, {}, tmp_path)


def test_ambiguous_author_failure_is_charged_once(tmp_path, monkeypatch):
    ledger = IterationBudgetLedger(tmp_path / "ledger.db", scope_id="test", cap_microusd=1_000_000)
    author = GeminiAuthor("fake", ledger, "test", {})

    def post(*args, **kwargs):
        raise TimeoutError("fixture timeout")
    monkeypatch.setattr("app.services.creative.motion_agent.httpx.post", post)
    with pytest.raises(TimeoutError):
        author("author", {}, tmp_path)
    assert ledger.snapshot().charged_microusd == 250_000
    with pytest.raises(RuntimeError, match="already submitted"):
        author("author", {}, tmp_path)
