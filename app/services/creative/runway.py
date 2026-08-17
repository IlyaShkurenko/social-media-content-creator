from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from random import uniform
from typing import Any, Callable, Literal
from urllib.parse import urlparse

import requests
from pydantic import Field

from app.services.creative.budget import IterationBudgetLedger
from app.services.creative.storyboard import CreativeModel


class RunwayPricingError(ValueError):
    """Raised when a request has no explicitly configured price."""


class RunwayProviderError(RuntimeError):
    """A sanitized provider or transport failure."""


class RunwayVideoRequest(CreativeModel):
    prompt_text: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1)
    mode: Literal["text_to_video"] = "text_to_video"
    ratio: str = "720:1280"
    duration_seconds: int = Field(gt=0)


@dataclass(frozen=True)
class RunwayJob:
    provider_job_id: str
    operation_id: str
    request: RunwayVideoRequest
    estimated_cost_microusd: int
    status: str = "PENDING"
    output_urls: tuple[str, ...] = ()
    failure_code: str | None = None

    def to_record(
        self,
        *,
        downloaded_paths: list[Path] | tuple[Path, ...] = (),
    ) -> dict[str, Any]:
        """Return durable evidence without secrets or expiring signed URLs."""

        return {
            "provider": "runway",
            "provider_job_id": self.provider_job_id,
            "operation_id": self.operation_id,
            "model": self.request.model,
            "mode": self.request.mode,
            "ratio": self.request.ratio,
            "duration_seconds": self.request.duration_seconds,
            "estimated_cost_microusd": self.estimated_cost_microusd,
            "status": self.status,
            "failure_code": self.failure_code,
            "output_count": len(self.output_urls),
            "downloaded_paths": [str(path) for path in downloaded_paths],
        }


def estimate_runway_cost_microusd(request: RunwayVideoRequest) -> int:
    credits_per_second = RunwayAdapter.CREDITS_PER_SECOND.get(request.model)
    if credits_per_second is None:
        raise RunwayPricingError(
            f"no fail-closed pricing is configured for Runway model {request.model!r}"
        )
    return (
        request.duration_seconds
        * credits_per_second
        * RunwayAdapter.CREDIT_PRICE_MICROUSD
    )


class RunwayAdapter:
    BASE_URL = "https://api.dev.runwayml.com"
    API_VERSION = "2024-11-06"
    CREDIT_PRICE_MICROUSD = 10_000
    CREDITS_PER_SECOND = {
        "gen4.5": 12,
    }

    def __init__(
        self,
        *,
        api_key: str,
        budget_ledger: IterationBudgetLedger,
        session: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = uniform,
        base_url: str = BASE_URL,
        api_version: str = API_VERSION,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Runway API key is required")
        self._api_key = api_key
        self.budget_ledger = budget_ledger
        self.session = session or requests.Session()
        self.sleep = sleep
        self.random_uniform = random_uniform
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-Runway-Version": self.api_version,
            "Content-Type": "application/json",
        }

    def estimate_cost_microusd(self, request: RunwayVideoRequest) -> int:
        return estimate_runway_cost_microusd(request)

    def submit(
        self,
        request: RunwayVideoRequest,
        *,
        operation_id: str,
    ) -> RunwayJob:
        if request.mode != "text_to_video":
            raise RunwayProviderError(
                f"unsupported Runway generation mode {request.mode!r}"
            )

        estimated_cost = self.estimate_cost_microusd(request)
        self.budget_ledger.reserve(
            operation_id,
            estimated_cost,
            f"Runway {request.model} {request.duration_seconds}s {request.mode}",
        )
        payload = {
            "model": request.model,
            "promptText": request.prompt_text,
            "ratio": request.ratio,
            "duration": request.duration_seconds,
        }

        try:
            response = self.session.post(
                f"{self.base_url}/v1/text_to_video",
                headers=self._headers,
                json=payload,
                timeout=30,
            )
        except Exception as exc:
            raise RunwayProviderError(
                "Runway submission outcome is unknown; the reservation was retained "
                f"under operation {operation_id!r} and was not retried"
            ) from exc

        try:
            response.raise_for_status()
            response_payload = response.json()
            provider_job_id = response_payload.get("id")
            if not isinstance(provider_job_id, str) or not provider_job_id.strip():
                raise ValueError("provider response did not contain a task id")
        except Exception as exc:
            self.budget_ledger.release(
                operation_id,
                reason="provider rejected request before returning a task id",
            )
            raise RunwayProviderError(
                f"Runway rejected the generation request (HTTP {response.status_code})"
            ) from exc

        self.budget_ledger.mark_submitted(
            operation_id,
            provider_job_id=provider_job_id,
        )
        return RunwayJob(
            provider_job_id=provider_job_id,
            operation_id=operation_id,
            request=request,
            estimated_cost_microusd=estimated_cost,
        )

    def wait(
        self,
        job: RunwayJob,
        *,
        timeout_seconds: float = 600,
        poll_interval_seconds: float = 5,
    ) -> RunwayJob:
        if timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("timeout and poll interval must be positive")
        deadline = time.monotonic() + timeout_seconds
        current = job

        while time.monotonic() < deadline:
            try:
                response = self.session.get(
                    f"{self.base_url}/v1/tasks/{job.provider_job_id}",
                    headers=self._headers,
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                raise RunwayProviderError(
                    f"failed to poll Runway task {job.provider_job_id!r}; task was not resubmitted"
                ) from exc

            status = str(payload.get("status", "UNKNOWN")).upper()
            output = payload.get("output") or []
            output_urls = tuple(url for url in output if isinstance(url, str))
            current = replace(
                current,
                status=status,
                output_urls=output_urls,
                failure_code=(
                    str(payload.get("failureCode"))
                    if payload.get("failureCode") is not None
                    else None
                ),
            )
            if status == "SUCCEEDED":
                return current
            if status in {"FAILED", "CANCELED"}:
                raise RunwayProviderError(
                    f"Runway task {job.provider_job_id!r} ended with status {status}"
                )

            delay = poll_interval_seconds + self.random_uniform(0, 1)
            self.sleep(delay)

        raise RunwayProviderError(
            f"Runway task {job.provider_job_id!r} did not finish before timeout; "
            "task was not resubmitted"
        )

    def download_outputs(self, job: RunwayJob, output_dir: Path) -> list[Path]:
        if job.status != "SUCCEEDED":
            raise RunwayProviderError("only successful Runway jobs can be downloaded")
        if not job.output_urls:
            raise RunwayProviderError("successful Runway job has no output URLs")

        managed_dir = output_dir.resolve()
        managed_dir.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        for index, output_url in enumerate(job.output_urls):
            parsed = urlparse(output_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise RunwayProviderError("Runway output URL must use HTTPS")
            target = managed_dir / f"{job.provider_job_id}-{index}.mp4"
            temporary = target.with_suffix(".mp4.part")
            try:
                with self.session.get(
                    output_url,
                    stream=True,
                    timeout=120,
                ) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                handle.write(chunk)
                temporary.replace(target)
            except Exception as exc:
                if temporary.exists():
                    temporary.unlink()
                raise RunwayProviderError(
                    f"failed to download output {index} for task {job.provider_job_id!r}"
                ) from exc
            downloaded.append(target)
        return downloaded
