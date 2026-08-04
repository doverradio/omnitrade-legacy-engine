from __future__ import annotations

import csv
from decimal import Decimal

import pytest

from strategy_lab.candles import load_candles_csv
from strategy_lab.tests.helpers import candle


def test_candle_rejects_high_below_low():
    with pytest.raises(ValueError):
        candle(0, o=10, h=9, l=11, c=10)


def test_candle_rejects_close_outside_range():
    with pytest.raises(ValueError):
        candle(0, o=10, h=12, l=9, c=13)


def test_load_candles_csv_round_trip(tmp_path):
    path = tmp_path / "candles.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerow(["2024-01-01T00:00:00Z", "100", "105", "95", "102", "10"])
        writer.writerow(["2024-01-01T01:00:00Z", "102", "108", "101", "107", "12"])

    loaded = load_candles_csv(path)
    assert len(loaded) == 2
    assert loaded[0].close == Decimal("102")
    assert loaded[1].open == Decimal("102")
    assert loaded[0].timestamp < loaded[1].timestamp


def test_load_candles_csv_rejects_out_of_order_rows(tmp_path):
    path = tmp_path / "candles.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        writer.writerow(["2024-01-01T01:00:00Z", "100", "105", "95", "102", "10"])
        writer.writerow(["2024-01-01T00:00:00Z", "102", "108", "101", "107", "12"])

    with pytest.raises(ValueError):
        load_candles_csv(path)


def test_load_candles_csv_rejects_missing_columns(tmp_path):
    path = tmp_path / "candles.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close"])  # missing volume
        writer.writerow(["2024-01-01T00:00:00Z", "100", "105", "95", "102"])

    with pytest.raises(ValueError):
        load_candles_csv(path)
