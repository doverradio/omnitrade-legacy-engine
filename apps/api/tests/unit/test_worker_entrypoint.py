from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.data.binance_client import BinanceClientError, NormalizedCandle
from app.services.data.ingestion_status import (
    get_last_successful_ingestion_at,
    reset_last_successful_ingestion_at,
)
from app.services.data.worker_entrypoint import get_active_crypto_assets, run_ingestion_cycle


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


class _FakeDBSession:
    def __init__(self, assets):
        self.assets = assets
        self.commits = 0
        self.last_statement = None

    async def execute(self, statement):
        self.last_statement = statement
        return _ExecuteResult(self.assets)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_worker_fetches_active_crypto_assets_only() -> None:
    assets = [
        SimpleNamespace(asset_class="crypto", is_active=True),
        SimpleNamespace(asset_class="crypto", is_active=True),
    ]
    db_session = _FakeDBSession(assets)

    result = await get_active_crypto_assets(db_session)

    assert len(result) == 2
    assert db_session.last_statement is not None
    query_text = str(db_session.last_statement)
    assert "assets.is_active IS true" in query_text
    assert "assets.asset_class" in query_text


@pytest.mark.asyncio
async def test_worker_writes_fetched_candles(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_last_successful_ingestion_at()

    asset = SimpleNamespace(id=uuid4(), symbol="BTCUSDT", exchange="binance_us")
    db_session = _FakeDBSession([asset])

    candles = [
        NormalizedCandle(
            open_time=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 5, 0, 1, tzinfo=timezone.utc),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("10"),
        )
    ]

    class _FakeClient:
        async def fetch_klines(self, **_: object):
            return candles

    writes = {"count": 0}

    async def _fake_upsert(db, asset_id, interval, incoming_candles):
        assert db is db_session
        assert asset_id == asset.id
        assert interval == "1m"
        assert incoming_candles == candles
        writes["count"] += 1
        return len(incoming_candles)

    monkeypatch.setattr("app.services.data.worker_entrypoint.upsert_candles", _fake_upsert)

    result = await run_ingestion_cycle(
        db_session,
        _FakeClient(),
        now_fn=lambda: datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc),
    )

    assert writes["count"] == 1
    assert db_session.commits == 1
    assert result.rows_written == 1


@pytest.mark.asyncio
async def test_worker_continues_when_one_asset_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_last_successful_ingestion_at()

    failing = SimpleNamespace(id=uuid4(), symbol="ETHUSDT", exchange="binance_us")
    healthy = SimpleNamespace(id=uuid4(), symbol="BTCUSDT", exchange="binance_us")
    db_session = _FakeDBSession([failing, healthy])

    candles = [
        NormalizedCandle(
            open_time=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2026, 7, 5, 0, 1, tzinfo=timezone.utc),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("10"),
        )
    ]

    class _FakeClient:
        async def fetch_klines(self, *, symbol: str, **_: object):
            if symbol == "ETHUSDT":
                raise BinanceClientError(
                    message="boom",
                    symbol=symbol,
                    interval="1m",
                    start_time=datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc),
                )
            return candles

    upserted_symbols = []

    async def _fake_upsert(db, asset_id, interval, incoming_candles):
        assert db is db_session
        assert interval == "1m"
        assert incoming_candles == candles
        if asset_id == healthy.id:
            upserted_symbols.append("BTCUSDT")
        return len(incoming_candles)

    monkeypatch.setattr("app.services.data.worker_entrypoint.upsert_candles", _fake_upsert)

    result = await run_ingestion_cycle(
        db_session,
        _FakeClient(),
        now_fn=lambda: datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc),
    )

    assert result.failed_assets == 1
    assert result.successful_assets == 1
    assert upserted_symbols == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_ingestion_status_updates_after_successful_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_last_successful_ingestion_at()

    asset = SimpleNamespace(id=uuid4(), symbol="BTCUSDT", exchange="binance_us")
    db_session = _FakeDBSession([asset])

    class _FakeClient:
        async def fetch_klines(self, **_: object):
            return []

    async def _fake_upsert(_db, _asset_id, _interval, _incoming_candles):
        return 0

    monkeypatch.setattr("app.services.data.worker_entrypoint.upsert_candles", _fake_upsert)

    cycle_time = datetime(2026, 7, 5, 2, 0, tzinfo=timezone.utc)
    await run_ingestion_cycle(db_session, _FakeClient(), now_fn=lambda: cycle_time)

    assert get_last_successful_ingestion_at() == cycle_time
