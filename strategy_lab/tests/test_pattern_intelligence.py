from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from strategy_lab.candles import Candle
from strategy_lab.pattern_intelligence import AnalysisConfig, AnalysisContext, analyze
from strategy_lab.pattern_intelligence.models import json_value


def candles(
    closes: list[str],
    highs: list[str] | None = None,
    lows: list[str] | None = None,
    volumes: list[str] | None = None,
) -> tuple[Candle, ...]:
    result = []
    for index, close_text in enumerate(closes):
        close = Decimal(close_text)
        high = Decimal(highs[index]) if highs else close + Decimal("1")
        low = Decimal(lows[index]) if lows else close - Decimal("1")
        result.append(Candle(
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=15 * index),
            open=close, high=high, low=low, close=close,
            volume=Decimal(volumes[index]) if volumes else Decimal("100"),
        ))
    return tuple(result)


def names(result) -> set[str]:
    return {item.pattern_name for item in result.findings}


def context(data: tuple[Candle, ...], start: int = 12, end: int | None = None, **kwargs) -> AnalysisContext:
    return AnalysisContext(dataset_id="handcrafted", interval="15m", selected_start_index=start,
                           selected_end_index=len(data) - 1 if end is None else end, **kwargs)


@pytest.mark.parametrize(("highs", "lows", "expected"), [
    ([str(101 + index) for index in range(24)], [str(99 + index) for index in range(24)], {"Higher Highs", "Higher Lows"}),
    ([str(130 - index) for index in range(24)], [str(128 - index) for index in range(24)], {"Lower Highs", "Lower Lows"}),
])
def test_price_sequences(highs: list[str], lows: list[str], expected: set[str]) -> None:
    data = candles([str((Decimal(high) + Decimal(low)) / 2) for high, low in zip(highs, lows)], highs, lows)
    assert expected <= names(analyze(data, context(data)))


def test_flat_range_and_repeated_levels() -> None:
    data = candles(["100"] * 30, ["100.2"] * 30, ["99.8"] * 30)
    result = analyze(data, context(data))
    assert {"Flat Price Range", "Repeated Support", "Repeated Resistance"} <= names(result)


def test_volatility_contraction_and_expansion() -> None:
    contraction = candles(["100"] * 24 + ["100"] * 12,
        ["105"] * 24 + ["100.2"] * 12, ["95"] * 24 + ["99.8"] * 12)
    expansion = candles(["100"] * 24 + ["100"] * 12,
        ["100.2"] * 24 + ["110"] * 12, ["99.8"] * 24 + ["90"] * 12)
    assert "Volatility Contraction" in names(analyze(contraction, context(contraction, 24)))
    assert "Volatility Expansion" in names(analyze(expansion, context(expansion, 24)))


def test_bullish_and_failed_breakout() -> None:
    closes = ["100"] * 20 + ["103", "99", "100", "100"]
    data = candles(closes, ["101"] * 20 + ["104", "101", "101", "101"], ["99"] * 24)
    result = analyze(data, context(data, 18))
    assert {"Bullish Breakout", "Failed Breakout"} <= names(result)


def test_volume_expansion_and_momentum_acceleration() -> None:
    closes = [str(100 + index // 4) for index in range(24)] + ["107", "109", "112", "116"]
    volumes = ["100"] * 27 + ["500"]
    data = candles(closes, volumes=volumes)
    result = analyze(data, context(data, 20))
    assert "Volume Expansion" in names(result)
    assert "Momentum Acceleration" in names(result)


def test_strategy_late_entry_early_exit_and_missed_limit() -> None:
    data = candles([str(100 + index) for index in range(32)])
    trade = {
        "entry_candle_index": 18, "exit_candle_index": 20, "raw_fill_price": "118", "raw_exit_price": "120",
        "highest_price_during_trade": "121", "lowest_price_during_trade": "117", "exit_reason": "declining_closes",
    }
    events = [
        {"candle_index": 21, "kind": "cancelled_order", "price": "119.8"},
        {"candle_index": 22, "kind": "buy_limit", "price": "121"},
        {"candle_index": 23, "kind": "buy_limit", "price": "122"},
        {"candle_index": 24, "kind": "buy_limit", "price": "123"},
    ]
    result = analyze(data, context(data, 12, replay_events=events, trades=[trade], selected_trade=trade))
    assert {"Late Entry", "Early Exit", "Narrowly Missed BUY Limit", "Missed Entry"} <= names(result)


def test_capital_drawdown_and_recovery() -> None:
    data = candles(["100"] * 30)
    curve = [Decimal("100")] * 15 + [Decimal("95"), Decimal("98"), Decimal("100")] + [Decimal("101")] * 12
    result = analyze(data, context(data, 12, equity_curve=curve))
    assert {"Capital Falls Below Starting Value", "Capital Recovers Above Starting Value"} <= names(result)


def test_recurrence_statistics_and_partition_isolation() -> None:
    repeated = ["100", "101", "100", "101"] * 30
    data = candles(repeated)
    config = AnalysisConfig(recurrence_tolerance_pct=Decimal("2"), minimum_recurrences=2)
    result = analyze(data, context(data, 100, 110), config)
    recurrence = result.findings[0].recurrence
    assert {item.partition for item in recurrence} == {"training", "validation", "final_test", "entire_dataset"}
    assert {item.forward_horizon for item in recurrence} == {1, 2, 4, 8, 16}
    validation_end = (len(data) * 2) // 3 - 1
    horizon_16 = next(item for item in recurrence if item.partition == "validation" and item.forward_horizon == 16)
    eligible = [index for index in range(len(data) // 3, validation_end + 1) if index + 16 <= validation_end]
    assert horizon_16.occurrence_count <= len(eligible)


def test_insufficient_evidence_and_deterministic_output() -> None:
    data = candles(["100", "101", "102", "103"])
    first = analyze(data, context(data, 0))
    second = analyze(data, context(data, 0))
    assert first.content_hash == second.content_hash
    assert "Insufficient History" in names(first)
    assert all(item.category.value == "INSUFFICIENT_EVIDENCE" for item in first.findings)


def test_future_candles_do_not_change_detected_conditions() -> None:
    original = candles([str(100 + index) for index in range(30)])
    altered = original[:21] + candles(["50"] * 9)[:9]
    altered = tuple(replace(item, timestamp=original[index].timestamp) for index, item in enumerate(altered))
    first = analyze(original, context(original, 12, 20))
    second = analyze(altered, context(altered, 12, 20))
    first_conditions = [(item.detector_id, item.measurements, item.conditions) for item in first.findings]
    second_conditions = [(item.detector_id, item.measurements, item.conditions) for item in second.findings]
    assert first_conditions == second_conditions


def test_annotation_serialization_and_quality_gaps() -> None:
    data = list(candles(["100"] * 24))
    data[15] = replace(data[15], timestamp=data[14].timestamp + timedelta(minutes=30))
    result = analyze(tuple(data), context(tuple(data)))
    serialized = json_value(result)
    assert serialized["annotations"][0]["details_ref"].startswith("finding_")
    assert any(item["issue_type"] == "gap" for item in serialized["data_quality"])


def test_invalid_ohlc_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside"):
        Candle(datetime.now(timezone.utc), Decimal("20"), Decimal("12"), Decimal("9"), Decimal("11"), Decimal("1"))