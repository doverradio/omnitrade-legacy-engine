from __future__ import annotations

import logging

import httpx
import pytest

from app.services.data.http_client import AsyncHTTPClient, ExternalAPIError


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    statuses = [429, 200]
    call_count = 0
    sleep_calls: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        status_code = statuses[call_count]
        call_count += 1
        return httpx.Response(status_code=status_code, request=request, text=f"status-{status_code}")

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    transport = httpx.MockTransport(handler)

    with caplog.at_level(logging.WARNING):
        async with httpx.AsyncClient(transport=transport) as raw_client:
            client = AsyncHTTPClient(
                raw_client,
                max_retries=3,
                base_delay=0.1,
                random_fn=lambda: 0.5,
                sleeper=fake_sleep,
            )
            response = await client.request("GET", "https://example.com/test")

    assert response.status_code == 200
    assert call_count == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(0.15)
    assert "endpoint=https://example.com/test" in caplog.text
    assert "status_code=429" in caplog.text


@pytest.mark.asyncio
async def test_raises_external_api_error_after_retry_exhaustion() -> None:
    call_count = 0
    sleep_calls: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=500, request=request, text="upstream-down")

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = AsyncHTTPClient(
            raw_client,
            max_retries=2,
            base_delay=0.1,
            random_fn=lambda: 0.0,
            sleeper=fake_sleep,
        )

        with pytest.raises(ExternalAPIError) as exc_info:
            await client.request("GET", "https://example.com/fail")

    exc = exc_info.value
    assert call_count == 3
    assert sleep_calls == [0.1, 0.2]
    assert exc.endpoint == "https://example.com/fail"
    assert exc.status_code == 500
    assert exc.response_body == "upstream-down"