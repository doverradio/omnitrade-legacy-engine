from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.data.binance_client import NormalizedCandle
from app.services.data.candle_writer import _deduplicate_candles_by_open_time, upsert_candles


def _candle(*, minute: int, close: str, source: str = "kraken_spot") -> NormalizedCandle:
    open_time = datetime(2026, 7, 13, 5, minute, tzinfo=timezone.utc)
    return NormalizedCandle(
        open_time=open_time,
        close_time=open_time.replace(minute=open_time.minute + 15),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=Decimal("10"),
        source=source,
    )


def test_deduplicate_last_occurrence_wins_deterministically() -> None:
    candles = [
        _candle(minute=0, close="100.1", source="binance_us"),
        _candle(minute=15, close="101.1", source="binance_us"),
        _candle(minute=0, close="100.9", source="kraken_spot"),
    ]

    deduplicated = _deduplicate_candles_by_open_time(candles)

    assert len(deduplicated) == 2
    assert deduplicated[0].open_time == candles[0].open_time
    assert deduplicated[0].close == Decimal("100.9")
    assert deduplicated[0].source == "kraken_spot"
    assert deduplicated[1].open_time == candles[1].open_time


def test_deduplicate_no_duplicates_keeps_all_rows() -> None:
    candles = [_candle(minute=0, close="100.1"), _candle(minute=15, close="101.1")]

    deduplicated = _deduplicate_candles_by_open_time(candles)

    assert deduplicated == candles


def test_deduplicate_empty_batch_returns_empty_list() -> None:
    assert _deduplicate_candles_by_open_time([]) == []


@pytest.mark.asyncio
async def test_upsert_candles_submits_unique_conflict_keys_only(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeExcluded:
        close_time = "close_time"
        open = "open"
        high = "high"
        low = "low"
        close = "close"
        volume = "volume"
        source = "source"

    class _FakeInsertStatement:
        def __init__(self):
            self.values_payload = []
            self.excluded = _FakeExcluded()

        def values(self, values):
            self.values_payload = values
            return self

        def on_conflict_do_update(self, **kwargs):
            self.conflict_kwargs = kwargs
            return self

        def returning(self, *_args, **_kwargs):
            return self

    class _FakeResult:
        def __init__(self, n: int):
            self._n = n

        def fetchall(self):
            return [object() for _ in range(self._n)]

    class _FakeSession:
        def __init__(self):
            self.executed_statement = None

        async def execute(self, statement):
            self.executed_statement = statement
            return _FakeResult(len(statement.values_payload))

    monkeypatch.setattr("app.services.data.candle_writer.insert", lambda _model: _FakeInsertStatement())

    asset_id = uuid4()
    candles = [
        _candle(minute=0, close="100.1", source="kraken_spot"),
        _candle(minute=15, close="101.1", source="kraken_spot"),
        _candle(minute=0, close="100.9", source="kraken_spot"),
    ]

    session = _FakeSession()
    written = await upsert_candles(session, asset_id, "15m", candles)

    values = session.executed_statement.values_payload
    assert len(values) == 2
    assert {value["open_time"] for value in values} == {
        datetime(2026, 7, 13, 5, 0, tzinfo=timezone.utc),
        datetime(2026, 7, 13, 5, 15, tzinfo=timezone.utc),
    }
    # Last occurrence wins for duplicate open_time.
    winning_row = [value for value in values if value["open_time"].minute == 0][0]
    assert winning_row["close"] == Decimal("100.9")
    assert written == 2
