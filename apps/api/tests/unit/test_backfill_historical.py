from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.data.binance_client import BinanceClientError, NormalizedCandle
from scripts.backfill_historical import BackfillArgs, backfill_symbol, parse_iso_datetime


class _FakeSession:
    def __init__(self) -> None:
        self.asset = SimpleNamespace(id=uuid4(), symbol="BTCUSDT", exchange="binance_us")
        self.writes: list[list[NormalizedCandle]] = []
        self.commit_calls = 0


@pytest.mark.asyncio
async def test_backfill_symbol_reports_partial_failure_and_keeps_prior_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession()

    candles_page_one = [
        NormalizedCandle(
            open_time=datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
            close_time=datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("1000"),
        )
    ]

    class _FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def fetch_klines(self, **_: object) -> list[NormalizedCandle]:
            self.calls += 1
            if self.calls == 1:
                return candles_page_one
            raise BinanceClientError(
                message="boom",
                symbol="BTCUSDT",
                interval="1d",
                start_time=datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc),
                end_time=datetime(2025, 1, 3, 0, 0, tzinfo=timezone.utc),
            )

    async def _fake_get_asset_by_symbol(db_session: object, symbol: str):
        assert db_session is session
        assert symbol == "BTCUSDT"
        return session.asset

    async def _fake_upsert_candles(db_session: object, asset_id, interval: str, candles: list[NormalizedCandle]) -> int:
        assert db_session is session
        assert asset_id == session.asset.id
        assert interval == "1d"
        session.writes.append(candles)
        return len(candles)

    async def _fake_commit() -> None:
        session.commit_calls += 1

    session.commit = _fake_commit  # type: ignore[attr-defined]

    monkeypatch.setattr("scripts.backfill_historical.get_asset_by_symbol", _fake_get_asset_by_symbol)
    monkeypatch.setattr("scripts.backfill_historical.upsert_candles", _fake_upsert_candles)

    args = BackfillArgs(
        symbol="BTCUSDT",
        interval="1d",
        start_date=datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2025, 1, 5, 0, 0, tzinfo=timezone.utc),
    )

    report = await backfill_symbol(session, _FakeClient(), args)

    assert report.failure_message is not None
    assert report.rows_written == 1
    assert report.succeeded_start == candles_page_one[0].open_time
    assert report.succeeded_end == candles_page_one[0].open_time
    assert len(session.writes) == 1
    assert session.commit_calls == 1


def test_parse_iso_datetime_handles_z_suffix() -> None:
    parsed = parse_iso_datetime("2025-07-05T00:00:00Z")

    assert parsed.tzinfo is not None
    assert parsed == datetime(2025, 7, 5, 0, 0, tzinfo=timezone.utc)
