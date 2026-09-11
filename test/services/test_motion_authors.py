"""ADPIPE-3.4: author routing, Responses compatibility and accountable failures."""
import json

import httpx
import pytest

from app.services.creative import motion_agent
from app.services.creative.budget import BudgetExceededError, IterationBudgetLedger
from app.services.creative.motion_authors import AstraAuthor, create_author


def budget(tmp_path, cap=10_000_000):
    return IterationBudgetLedger(tmp_path / "budget.sqlite3", scope_id="test", cap_microusd=cap)


def answer(text="export default () => <div>Hello</div>;", status="completed"):
    return {"id": "resp-test", "model": "gpt-6-astra", "status": status,
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "output_tokens_details": {"reasoning_tokens": 150}},
            "output": [{"type": "reasoning", "encrypted_content": "never-save-this"},
                       {"type": "message", "content": [{"type": "output_text", "text": text}]}]}


def transport(monkeypatch, response=None):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        return httpx.Response(200, json=response or answer())
    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_astra_payload_and_charge(tmp_path, monkeypatch):
    calls = transport(monkeypatch)
    image = tmp_path / "logo.png"
    image.write_bytes(b"fixture-image")
    ledger = budget(tmp_path)
    author = AstraAuthor("secret", ledger, "run", {"logo": {"path": str(image)}})
    result = author("author", {"instructions": "Return TSX", "brief": {}}, tmp_path)
    body = calls[-1][1]["json"]
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"]["effort"] == "high"
    assert body["store"] is False
    assert "temperature" not in body and "top_p" not in body
    assert any(p["type"] == "input_image" for p in body["input"][0]["content"])
    assert result["source"].startswith("export default")
    assert ledger.snapshot().charged_microusd == 11_250  # reasoning included; conservative input ceiling
    assert ledger.snapshot().reserved_microusd == 0
    evidence = (tmp_path / "author.provider.json").read_text()
    assert "secret" not in evidence and "never-save-this" not in evidence
    assert len(calls) == 2


def test_plan_json_transport(tmp_path, monkeypatch):
    calls = transport(monkeypatch, answer('{"hypothesis":"Clear"}'))
    result = AstraAuthor("key", budget(tmp_path), "run", {})("plan", {}, tmp_path)
    assert result == {"hypothesis": "Clear"}
    assert calls[-1][1]["json"]["text"]["format"]["type"] == "json_object"
    inputs = calls[-1][1]["json"]["input"]
    assert any("json" in p.get("text", "").lower() for m in inputs for p in m["content"])


def test_budget_stops_before_generation(tmp_path, monkeypatch):
    calls = transport(monkeypatch)
    with pytest.raises(BudgetExceededError):
        AstraAuthor("key", budget(tmp_path, 20_000), "run", {})("author", {}, tmp_path)
    assert all(url.endswith("input_tokens") for url, _ in calls)
    assert not (tmp_path / "author.submitted.json").exists()


@pytest.mark.parametrize("status", ["incomplete", "failed"])
def test_incomplete_is_saved_not_success(tmp_path, monkeypatch, status):
    transport(monkeypatch, answer(status=status))
    with pytest.raises(RuntimeError, match=status):
        AstraAuthor("key", budget(tmp_path), "run", {})("author", {}, tmp_path)
    assert json.loads((tmp_path / "author.provider.json").read_text())["status"] == status


def test_timeout_is_accounted_and_never_retried(tmp_path, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        if url.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        raise httpx.ReadTimeout("private transport details")
    monkeypatch.setattr(httpx, "post", post)
    ledger = budget(tmp_path)
    author = AstraAuthor("key", ledger, "run", {})
    with pytest.raises(RuntimeError, match="ambiguous"):
        author("author", {}, tmp_path)
    assert ledger.snapshot().charged_microusd > 0
    with pytest.raises(RuntimeError, match="already submitted"):
        author("author", {}, tmp_path)
    assert len(calls) == 2


def test_routing_does_not_change_judge_or_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = {"openai_api_key": "key", "gemini_api_key": "gem", "llm_provider": "moonshot"}
    original = dict(config)
    assert isinstance(create_author(config, budget(tmp_path), "run", {}), AstraAuthor)
    assert isinstance(create_author(config, budget(tmp_path), "run", {}, provider="gemini"),
                      motion_agent.GeminiAuthor)
    assert config == original


@pytest.mark.parametrize("kwargs", [{"provider": "unknown"}, {"model": "gpt-imaginary"},
                                    {"reasoning": "none"}])
def test_invalid_settings_fail_locally(tmp_path, kwargs):
    with pytest.raises(ValueError):
        create_author({"openai_api_key": "key"}, budget(tmp_path), "run", {}, **kwargs)


def test_missing_key_no_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenAI"):
        create_author({"gemini_api_key": "gem"}, budget(tmp_path), "run", {})


def test_resume_cannot_switch_authors(tmp_path):
    from test.services.test_motion_agent import brief
    run = tmp_path / "run"
    run.mkdir()
    (run / "brief.json").write_text(json.dumps(brief(10)))
    (run / "author.json").write_text(json.dumps({"provider": "gemini"}))
    author = AstraAuthor("key", budget(tmp_path), "run", {})
    with pytest.raises(ValueError, match="author identity"):
        motion_agent.run_agent(brief(10), {}, run, author=author, tools={}, resume=True)


def test_reuse_verified_materials_replans_without_baseline_source(tmp_path):
    from app.services.creative.motion import WORKER, sha256
    from test.services.test_motion_agent import brief
    old = tmp_path / "old"
    snapshot = old / "render/project"
    snapshot.mkdir(parents=True)
    image = snapshot / "logo.png"
    image.write_bytes(b"approved-fixture")
    (snapshot / "Scene.tsx").write_text("old source must not reach author")
    video = old / "render/video.mp4"
    video.write_bytes(b"fixture-not-video")
    contract = {**brief(10), "user_constraints": brief(10), "schema_version": "1.0",
                "composition_id": "Test", "entrypoint": "Scene.tsx", "assets": {"logo": "logo.png"},
                "audio": {"mode": "silent", "asset_ids": []},
                "beats": [{"id": "old", "start": 0, "end": 10, "intent": "Old concept"}]}
    path = snapshot / "project.json"
    path.write_text(json.dumps(contract))
    (old / "render/metrics.json").write_text(json.dumps({"technical_pass": True, "video_sha256": sha256(video)}))
    (old / "render/provenance.json").write_text(json.dumps({"contract_sha256": sha256(path),
        "source_sha256": sha256(snapshot / "Scene.tsx"), "assets": {"logo": sha256(image)},
        "lock_sha256": sha256(WORKER / "package-lock.json")}))
    (old / "result.json").write_text(json.dumps({"video": "render/video.mp4"}))
    def author(stage, payload, directory):
        assert "old source" not in json.dumps(payload)
        if stage == "plan":
            assert payload["tools"] == []
            return {"hypothesis": "New concept", "materials": [],
                    "audio": {"mode": "silent", "asset_ids": []},
                    "beats": [{"id": "new", "start": 0, "end": 10, "intent": "New concept"}]}
        assert payload["previous_source"] is None
        return {"source": "export default () => <div>New</div>;", "change_hypothesis": "New concept"}
    def renderer(project, output):
        output.mkdir()
        (output / "video.mp4").write_bytes(b"fixture")
        return {"technical_pass": True}
    run = tmp_path / "new"
    motion_agent.run_agent(brief(10), {}, run, author=author, tools={"forbidden": None},
                           renderer=renderer, materials_from=old)
    assert (run / "materials/logo.png").read_bytes() == image.read_bytes()
    assert json.loads((run / "plan.json").read_text())["beats"][0]["id"] == "new"
    image.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        motion_agent.run_agent(brief(10), {}, tmp_path / "bad", author=author, tools={}, materials_from=old)


def test_refusal_is_not_source(tmp_path, monkeypatch):
    data = answer()
    data["output"] = [{"type": "message", "content": [{"type": "refusal", "refusal": "Declined"}]}]
    transport(monkeypatch, data)
    with pytest.raises(RuntimeError, match="refused=True"):
        AstraAuthor("key", budget(tmp_path), "run", {})("author", {}, tmp_path)


def test_count_failure_is_not_charged(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(401))
    ledger = budget(tmp_path)
    with pytest.raises(RuntimeError, match="token-count HTTP 401"):
        AstraAuthor("key", ledger, "run", {})("author", {}, tmp_path)
    assert ledger.snapshot().charged_microusd == 0


def test_request_rejection_retains_reason_without_charge_or_key(tmp_path, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        if url.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        return httpx.Response(400, json={"error": {"message": "Invalid parameter sk-private",
                              "code": "invalid_request_error", "param": "example"}})
    monkeypatch.setattr(httpx, "post", post)
    ledger = budget(tmp_path)
    author = AstraAuthor("sk-private", ledger, "run", {})
    with pytest.raises(RuntimeError, match="Invalid parameter"):
        author("author", {}, tmp_path)
    evidence = (tmp_path / "author.provider.json").read_text()
    assert "sk-private" not in evidence
    assert json.loads(evidence)["generation_rejected"] is True
    assert json.loads(evidence)["error_param"] == "example"
    assert ledger.snapshot().charged_microusd == 0
    with pytest.raises(RuntimeError, match="already submitted"):
        author("author", {}, tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize("provider", ["astra", "gemini"])
def test_all_references_including_ninth_and_large_file(tmp_path, monkeypatch, provider):
    import base64
    catalog = {}
    for index in range(10):
        path = tmp_path / f"image-{index}.png"
        path.write_bytes(b"fixture" if index != 9 else b"x" * 5_000_001)
        catalog[f"image_{index}"] = {"path": str(path)}
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        if url.endswith("input_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        if provider == "astra":
            return httpx.Response(200, json=answer())
        return httpx.Response(200, json={"usageMetadata": {"promptTokenCount": 100,
            "candidatesTokenCount": 200}, "candidates": [{"finishReason": "STOP",
            "content": {"parts": [{"text": "export default () => <div />;"}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    author = create_author({"openai_api_key": "key", "gemini_api_key": "gem"},
                            budget(tmp_path), "run", catalog, provider=provider)
    author("author", {}, tmp_path)
    body = calls[-1]
    if provider == "astra":
        images = [p["image_url"].split(",", 1)[1] for p in body["input"][0]["content"]
                  if p["type"] == "input_image"]
    else:
        images = [p["inlineData"]["data"] for p in body["contents"][0]["parts"] if "inlineData" in p]
    assert len(images) == 10
    assert len(base64.b64decode(images[-1])) == 5_000_001
    assert len(json.loads((tmp_path / "author.references.json").read_text())) == 10


def test_svg_preview_preserves_original(tmp_path):
    from app.services.creative.motion import image_reference
    path = tmp_path / "art.svg"
    original = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><rect width="20" height="10" fill="yellow"/></svg>'
    path.write_text(original)
    mime, data = image_reference(path)
    assert mime == "image/png" and data.startswith(b"\x89PNG")
    assert path.read_text() == original


def test_svg_external_reference_is_explicit_error(tmp_path):
    from app.services.creative.motion import image_reference
    path = tmp_path / "art.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///private/secret.png"/></svg>')
    with pytest.raises(ValueError, match="self-contained"):
        image_reference(path)


def test_brand_catalog_covers_all_current_images():
    from app.services.creative.motion import REPO, managed_file
    root = REPO / "feedback-loop/video-quality/evals/assets/brand"
    catalog = json.loads((root / "motion-catalog.json").read_text())
    catalog_paths = {managed_file(root, entry["path"]) for entry in catalog.values()}
    media = {p.resolve() for p in root.rglob("*") if p.is_file()
             and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}}
    assert catalog_paths == media
