from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.creative.budget import IterationBudgetLedger
from app.services.creative.runway import (
    RunwayAdapter,
    RunwayPricingError,
    RunwayProviderError,
    RunwayVideoRequest,
)


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status_code: int = 200,
        content: bytes = b"video",
        json_error: Exception | None = None,
    ) -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.content = content
        self.text = "provider response"
        self.json_error = json_error

    def json(self) -> dict[str, Any]:
        if self.json_error is not None:
            raise self.json_error
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 65536):
        del chunk_size
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback


class FakeSession:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.gets: list[dict[str, Any]] = []
        self.status_payloads: list[dict[str, Any]] = []
        self.downloads: dict[str, bytes] = {}
        self.post_response = FakeResponse({"id": "job-123"})
        self.post_error: Exception | None = None

    def post(self, url: str, **kwargs):
        self.posts.append({"url": url, **kwargs})
        if self.post_error is not None:
            raise self.post_error
        return self.post_response

    def get(self, url: str, **kwargs):
        self.gets.append({"url": url, **kwargs})
        if url.startswith("https://download.example/"):
            return FakeResponse(content=self.downloads[url])
        if self.status_payloads:
            return FakeResponse(self.status_payloads.pop(0))
        raise AssertionError(f"unexpected GET {url} {kwargs}")


def make_adapter(tmp_path: Path, session: FakeSession) -> RunwayAdapter:
    budget = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="iteration-001",
        cap_microusd=10_000_000,
    )
    return RunwayAdapter(
        api_key="test-secret",
        budget_ledger=budget,
        session=session,
        sleep=lambda _: None,
        random_uniform=lambda _a, _b: 0,
    )


def request() -> RunwayVideoRequest:
    return RunwayVideoRequest(
        prompt_text="A traveller looks overwhelmed in an airport, handheld push-in.",
        model="gen4.5",
        mode="text_to_video",
        ratio="720:1280",
        duration_seconds=5,
    )


def test_runway_1_1_gen45_five_second_cost_is_sixty_cents(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path, FakeSession())
    assert adapter.estimate_cost_microusd(request()) == 600_000


def test_unknown_model_pricing_fails_closed(tmp_path: Path) -> None:
    adapter = make_adapter(tmp_path, FakeSession())
    unknown = request().model_copy(update={"model": "unknown-model"})
    with pytest.raises(RunwayPricingError, match="unknown-model"):
        adapter.estimate_cost_microusd(unknown)


def test_runway_1_5_submit_records_job_without_persisting_secret(tmp_path: Path) -> None:
    session = FakeSession()
    adapter = make_adapter(tmp_path, session)
    job = adapter.submit(request(), operation_id="hook-1")
    assert job.provider_job_id == "job-123"
    assert job.estimated_cost_microusd == 600_000
    assert len(session.posts) == 1
    assert session.posts[0]["json"]["model"] == "gen4.5"
    assert session.posts[0]["json"]["duration"] == 5
    assert session.posts[0]["headers"]["X-Runway-Version"] == "2024-11-06"
    assert session.posts[0]["headers"]["Authorization"] == "Bearer test-secret"
    assert session.posts[0]["allow_redirects"] is False
    assert "test-secret" not in str(job.to_record())
    assert adapter.budget_ledger.snapshot().charged_microusd == 600_000


def test_submit_failure_before_job_releases_reservation(tmp_path: Path) -> None:
    session = FakeSession()
    session.post_response = FakeResponse(status_code=401)
    adapter = make_adapter(tmp_path, session)
    with pytest.raises(RunwayProviderError):
        adapter.submit(request(), operation_id="hook-1")
    assert adapter.budget_ledger.snapshot().remaining_microusd == 10_000_000


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.dev.runwayml.com",
        "https://api.dev.runwayml.com.evil.example",
        "https://user:secret@api.dev.runwayml.com",
        "https://api.dev.runwayml.com/v1",
        "https://api.dev.runwayml.com?redirect=evil",
        "https://api.dev.runwayml.com:8443",
    ],
)
def test_runway_bearer_key_is_only_sent_to_official_https_origin(
    tmp_path: Path,
    base_url: str,
) -> None:
    budget = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="iteration-001",
        cap_microusd=10_000_000,
    )

    with pytest.raises(ValueError, match="official HTTPS API origin"):
        RunwayAdapter(
            api_key="test-secret",
            budget_ledger=budget,
            session=FakeSession(),
            base_url=base_url,
        )


def test_runway_api_version_fails_closed_before_sending_key(tmp_path: Path) -> None:
    budget = IterationBudgetLedger(
        tmp_path / "budget.sqlite3",
        scope_id="iteration-001",
        cap_microusd=10_000_000,
    )

    with pytest.raises(ValueError, match="unsupported Runway API version"):
        RunwayAdapter(
            api_key="test-secret",
            budget_ledger=budget,
            session=FakeSession(),
            api_version="2099-01-01",
        )


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status_code=200),
        FakeResponse(
            status_code=200,
            json_error=ValueError("malformed JSON"),
        ),
        FakeResponse(status_code=503),
        FakeResponse(status_code=429),
    ],
)
def test_ambiguous_submit_retains_budget_and_blocks_duplicate_retry(
    tmp_path: Path,
    response: FakeResponse,
) -> None:
    session = FakeSession()
    session.post_response = response
    adapter = make_adapter(tmp_path, session)

    with pytest.raises(RunwayProviderError, match="retained"):
        adapter.submit(request(), operation_id="hook-ambiguous")

    snapshot = adapter.budget_ledger.snapshot()
    assert snapshot.reserved_microusd == 600_000
    assert snapshot.remaining_microusd == 9_400_000
    with pytest.raises(RunwayProviderError, match="resubmission is blocked"):
        adapter.submit(request(), operation_id="hook-ambiguous")
    assert len(session.posts) == 1


def test_network_submit_failure_retains_budget_and_blocks_duplicate_retry(
    tmp_path: Path,
) -> None:
    session = FakeSession()
    session.post_error = TimeoutError("unknown provider outcome")
    adapter = make_adapter(tmp_path, session)

    with pytest.raises(RunwayProviderError, match="outcome is unknown"):
        adapter.submit(request(), operation_id="hook-timeout")

    assert adapter.budget_ledger.snapshot().reserved_microusd == 600_000
    with pytest.raises(RunwayProviderError, match="resubmission is blocked"):
        adapter.submit(request(), operation_id="hook-timeout")
    assert len(session.posts) == 1


def test_submitted_operation_is_not_resubmitted(tmp_path: Path) -> None:
    session = FakeSession()
    adapter = make_adapter(tmp_path, session)
    adapter.submit(request(), operation_id="hook-1")

    with pytest.raises(RunwayProviderError, match="already submitted"):
        adapter.submit(request(), operation_id="hook-1")

    assert len(session.posts) == 1


def test_runway_1_4_polling_does_not_resubmit_generation(tmp_path: Path) -> None:
    session = FakeSession()
    session.status_payloads = [
        {"id": "job-123", "status": "PENDING"},
        {
            "id": "job-123",
            "status": "SUCCEEDED",
            "output": ["https://download.example/output.mp4?token=signed"],
        },
    ]
    adapter = make_adapter(tmp_path, session)
    job = adapter.submit(request(), operation_id="hook-1")
    completed = adapter.wait(job, timeout_seconds=30, poll_interval_seconds=5)
    assert completed.status == "SUCCEEDED"
    assert len(session.posts) == 1
    assert all(call["allow_redirects"] is False for call in session.gets)


def test_runway_1_6_output_is_downloaded_without_signed_url_in_record(
    tmp_path: Path,
) -> None:
    output_url = "https://download.example/output.mp4?token=signed"
    session = FakeSession()
    session.status_payloads = [
        {
            "id": "job-123",
            "status": "SUCCEEDED",
            "output": [output_url],
        }
    ]
    session.downloads[output_url] = b"mp4-bytes"
    adapter = make_adapter(tmp_path, session)
    job = adapter.submit(request(), operation_id="hook-1")
    completed = adapter.wait(job, timeout_seconds=30, poll_interval_seconds=5)
    downloaded = adapter.download_outputs(completed, tmp_path / "managed")
    assert downloaded[0].read_bytes() == b"mp4-bytes"
    record = completed.to_record(downloaded_paths=downloaded)
    assert "token=signed" not in str(record)
    assert str(downloaded[0]) in str(record)
