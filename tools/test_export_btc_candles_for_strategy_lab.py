"""Tests for the pure CSV-building / data-quality-report logic in
tools/export_btc_candles_for_strategy_lab.py. Run:
    python3 -m pytest tools/test_export_btc_candles_for_strategy_lab.py

Only the DB-independent logic is exercised here -- the actual SELECT
against the `candles` table requires a reachable Postgres instance and the
apps/api SQLAlchemy/asyncpg environment, and is documented (not run) in
this repository. See the module docstring for the exact command."""
from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import export_btc_candles_for_strategy_lab as exporter  # noqa: E402

from strategy_lab.candles import load_candles_csv  # noqa: E402


@dataclass
class _FakeCandle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: str


class _Args:
    symbol = "BTC-USD"
    exchange = "kraken_spot"
    interval = "1h"
    output = None


def _candle(hour: int, close_delta_hours: int = 1, source: str = "kraken_rest") -> _FakeCandle:
    open_time = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour)
    return _FakeCandle(
        open_time=open_time,
        close_time=open_time + timedelta(hours=close_delta_hours),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        source=source,
    )


def _args(tmp_path, **overrides):
    class Args(_Args):
        pass

    args = Args()
    args.output = str(tmp_path / "out.csv")
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_exports_clean_sequence_with_no_gaps_or_duplicates(tmp_path):
    candles = [_candle(h) for h in range(250)]
    rows = [(c, "BTC-USD", "kraken_spot") for c in candles]
    args = _args(tmp_path)

    report = exporter._build_report_and_write_csv(rows, args)

    assert report.candle_count == 250
    assert report.gap_count == 0
    assert report.duplicate_timestamps == 0
    assert report.excluded_not_yet_closed == 0
    assert report.insufficiency_notes == []

    loaded = load_candles_csv(args.output)
    assert len(loaded) == 250


def test_excludes_not_yet_closed_candles(tmp_path):
    now = datetime.now(timezone.utc)
    candles = [_candle(h) for h in range(200)]
    # Force the last candle's close_time far into the future.
    candles[-1].close_time = now + timedelta(days=1)
    rows = [(c, "BTC-USD", "kraken_spot") for c in candles]
    args = _args(tmp_path)

    report = exporter._build_report_and_write_csv(rows, args)

    assert report.excluded_not_yet_closed == 1
    assert report.candle_count == 199


def test_detects_duplicate_open_times(tmp_path):
    candles = [_candle(h) for h in range(200)]
    candles.append(_candle(5))  # duplicate open_time
    rows = [(c, "BTC-USD", "kraken_spot") for c in candles]
    args = _args(tmp_path)

    report = exporter._build_report_and_write_csv(rows, args)

    assert report.duplicate_timestamps == 1
    assert report.candle_count == 200


def test_detects_gaps_in_the_sequence(tmp_path):
    candles = [_candle(h) for h in range(200)]
    del candles[100]  # remove one candle -> a 2-hour gap where 1 was expected
    rows = [(c, "BTC-USD", "kraken_spot") for c in candles]
    args = _args(tmp_path)

    report = exporter._build_report_and_write_csv(rows, args)

    assert report.gap_count == 1
    assert "expected 1:00:00" in report.gaps[0] or "expected 1 " in report.gaps[0] or "expected" in report.gaps[0]


def test_notes_insufficiency_when_fewer_than_200_candles(tmp_path):
    candles = [_candle(h) for h in range(10)]
    rows = [(c, "BTC-USD", "kraken_spot") for c in candles]
    args = _args(tmp_path)

    report = exporter._build_report_and_write_csv(rows, args)

    assert report.candle_count == 10
    assert any("too few" in note for note in report.insufficiency_notes)


def test_empty_result_set_is_reported_as_insufficient(tmp_path):
    args = _args(tmp_path)

    report = exporter._build_report_and_write_csv([], args)

    assert report.candle_count == 0
    assert report.insufficiency_notes

    with open(args.output) as handle:
        rows = list(csv.reader(handle))
    assert rows == [["timestamp", "open", "high", "low", "close", "volume"]]


def test_distinct_sources_are_reported():
    candles = [_candle(h, source="kraken_rest") for h in range(5)]
    candles += [_candle(h, source="kraken_ws") for h in range(5, 10)]
    rows = [(c, "BTC-USD", "kraken_spot") for c in candles]
    args = _Args()
    args.output = "/tmp/unused_export_test_output.csv"

    report = exporter._build_report_and_write_csv(rows, args)

    assert report.distinct_sources == ["kraken_rest", "kraken_ws"]
    os.remove(args.output)
