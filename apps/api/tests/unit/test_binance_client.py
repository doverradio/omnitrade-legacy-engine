from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.services.data.binance_client import BinanceClientError, BinanceUSClient
from app.services.data.http_client import AsyncHTTPClient


@pytest.mark.asyncio
async def test_fetch_klines_single_page_success() -> None:
    requests_seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        payload = [
            [
                1704067200000,
                "42000.10",
                "42200.00",
                "41850.50",
                "42100.00",
                "120.25",
                1704067259999,
                "0",
                0,
                "0",
                "0",
                "0",
            ]
        ]
        return httpx.Response(status_code=200, request=request, json=payload)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as raw_client:
        http_client = AsyncHTTPClient(raw_client)
        client = BinanceUSClient(http_client, base_url="https://api.binance.us")
        candles = await client.fetch_klines(
            symbol="BTCUSDT",
            interval="1m",
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=None,
        )

    assert len(candles) == 1
    assert candles[0].source == "binance_us"
    assert candles[0].open == Decimal("42000.10")
    assert candles[0].close == Decimal("42100.00")
    assert len(requests_seen) == 1
    assert requests_seen[0].url.path == "/api/v3/klines"
    assert requests_seen[0].url.params["symbol"] == "BTCUSDT"
    assert requests_seen[0].url.params["interval"] == "1m"


@pytest.mark.asyncio
async def test_fetch_klines_paginates_until_last_page() -> None:
    requests_seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)

        if len(requests_seen) == 1:
            payload = [
                [
                    1704067200000,
                    "42000.10",
                    "42200.00",
                    "41850.50",
                    "42100.00",
                    "120.25",
                    1704067259999,
                    "0",
                    0,
                    "0",
                    "0",
                    "0",
                ],
                [
                    1704067260000,
                    "42100.00",
                    "42300.00",
                    "42000.00",
                    "42250.00",
                    "118.00",
                    1704067319999,
                    "0",
                    0,
                    "0",
                    "0",
                    "0",
                ],
            ]
            return httpx.Response(status_code=200, request=request, json=payload)

        payload = [
            [
                1704067320000,
                "42250.00",
                "42400.00",
                "42110.00",
                "42340.00",
                "111.11",
                1704067379999,
                "0",
                0,
                "0",
                "0",
                "0",
            ]
        ]
        return httpx.Response(status_code=200, request=request, json=payload)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as raw_client:
        http_client = AsyncHTTPClient(raw_client)
        client = BinanceUSClient(http_client, base_url="https://api.binance.us", page_limit=2)
        candles = await client.fetch_klines(
            symbol="BTCUSDT",
            interval="1m",
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=None,
        )

    assert len(candles) == 3
    assert len(requests_seen) == 2
    assert requests_seen[0].url.params["startTime"] == "1704067200000"
    assert requests_seen[1].url.params["startTime"] == "1704067320000"


@pytest.mark.asyncio
async def test_fetch_klines_raises_typed_error_when_retries_exhausted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, request=request, text="upstream-down")

    async def fake_sleep(_: float) -> None:
        return None

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as raw_client:
        http_client = AsyncHTTPClient(
            raw_client,
            max_retries=1,
            base_delay=0.01,
            random_fn=lambda: 0.0,
            sleeper=fake_sleep,
        )
        client = BinanceUSClient(http_client, base_url="https://api.binance.us")

        with pytest.raises(BinanceClientError) as exc_info:
            await client.fetch_klines(
                symbol="BTCUSDT",
                interval="1m",
                start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                end_time=None,
            )

    error = exc_info.value
    assert error.symbol == "BTCUSDT"
    assert error.interval == "1m"
    assert "Failed to fetch Binance.US klines" in str(error)
    assert "status_code=500" in caplog.text
