from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExternalAPIError(Exception):
    message: str
    endpoint: str
    status_code: int | None = None
    response_body: str | None = None

    def __str__(self) -> str:
        return (
            f"{self.message} endpoint={self.endpoint} "
            f"status_code={self.status_code} response_body={self.response_body}"
        )


class AsyncHTTPClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        max_retries: int = 3,
        base_delay: float = 0.5,
        jitter_factor: float = 1.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.jitter_factor = jitter_factor
        self._sleeper = sleeper
        self._random_fn = random_fn

    async def __aenter__(self) -> AsyncHTTPClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        retries = 0
        while True:
            try:
                response = await self._client.request(method, url, **kwargs)
            except httpx.RequestError as exc:
                raise ExternalAPIError(
                    message="External API request failed before receiving a response",
                    endpoint=str(exc.request.url) if exc.request else url,
                    status_code=None,
                    response_body=str(exc),
                ) from exc

            if self._is_retryable_status(response.status_code):
                if retries >= self.max_retries:
                    raise ExternalAPIError(
                        message="External API request failed after retries",
                        endpoint=str(response.request.url),
                        status_code=response.status_code,
                        response_body=response.text,
                    )

                retries += 1
                delay = self._compute_backoff_delay(retries)
                logger.warning(
                    "Retrying external API request: endpoint=%s status_code=%s attempt=%s/%s delay=%.3fs",
                    response.request.url,
                    response.status_code,
                    retries,
                    self.max_retries,
                    delay,
                )
                await self._sleeper(delay)
                continue

            if response.status_code >= 400:
                raise ExternalAPIError(
                    message="External API request failed",
                    endpoint=str(response.request.url),
                    status_code=response.status_code,
                    response_body=response.text,
                )

            return response

    def _compute_backoff_delay(self, attempt: int) -> float:
        exponential = self.base_delay * (2 ** (attempt - 1))
        jitter = self._random_fn() * self.base_delay * self.jitter_factor
        return exponential + jitter

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or 500 <= status_code <= 599