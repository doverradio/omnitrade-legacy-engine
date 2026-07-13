from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.services.data.http_client import AsyncHTTPClient
from app.services.data.kraken_client import KrakenSpotClient


@pytest.mark.asyncio
async def test_fetch_klines_uses_public_ohlc_endpoint_without_auth_headers() -> None:
    requests_seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        payload = {
            "error": [],
            "result": {
                "XXBTZUSD": [
                    [1704067200, "42000.10", "42200.00", "41850.50", "42100.00", "0", "120.25", 10],
                ],
                "last": 1704067200,
            },
        }
        return httpx.Response(status_code=200, request=request, json=payload)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as raw_client:
        http_client = AsyncHTTPClient(raw_client)
        client = KrakenSpotClient(http_client)
        candles = await client.fetch_klines(
            symbol="BTC-USD",
            interval="15m",
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
        )

    assert len(candles) == 1
    assert candles[0].source == "kraken_spot"
    assert candles[0].open == Decimal("42000.10")
    assert candles[0].close == Decimal("42100.00")

    assert len(requests_seen) == 1
    request = requests_seen[0]
    assert request.url.path == "/0/public/OHLC"
    assert request.url.params["pair"] == "XBTUSD"
    assert request.url.params["interval"] == "15"
    assert "API-Key" not in request.headers
    assert "API-Sign" not in request.headers


@pytest.mark.asyncio
async def test_fetch_klines_excludes_current_incomplete_candle_using_end_time() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "error": [],
            "result": {
                "XXBTZUSD": [
                    [1704067200, "42000", "42100", "41900", "42050", "0", "10", 1],  # close 00:15
                    [1704068100, "42050", "42200", "42000", "42190", "0", "11", 1],  # close 00:30
                    [1704069000, "42190", "42300", "42150", "42240", "0", "12", 1],  # close 00:45 (filtered)
                ],
                "last": 1704067200,
            },
        }
        return httpx.Response(status_code=200, request=request, json=payload)

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as raw_client:
        http_client = AsyncHTTPClient(raw_client)
        client = KrakenSpotClient(http_client)
        candles = await client.fetch_klines(
            symbol="BTC-USD",
            interval="15m",
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 0, 35, tzinfo=timezone.utc),
        )

    assert len(candles) == 2
    assert candles[-1].close_time == datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc)
