"""Unit tests for the canonical deterministic Market State Classifier.

Research/replay layer only -- see
`app/services/market_state/deterministic_classifier.py` and
`docs/MARKET_STATE_CLASSIFIER.md`. These tests exercise
`classify_market_state` as a pure function only; no database, no network,
no async fixtures.
"""

from __future__ import annotations

import dataclasses
import statistics
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from app.services.market_state import (
    CLASSIFIER_VERSION,
    DirectionState,
    InsufficientMarketDataError,
    MarketStateClassifierParams,
    OHLCVBar,
    ParticipationState,
    VolatilityState,
    classify_market_state,
)


def _bar(close: str, volume: str = "1000", *, open_: str | None = None) -> OHLCVBar:
    close_dec = Decimal(close)
    return OHLCVBar(
        open=Decimal(open_) if open_ is not None else close_dec,
        high=close_dec + Decimal("0.01"),
        low=close_dec - Decimal("0.01"),
        close=close_dec,
        volume=Decimal(volume),
    )


def _bars(closes: list[str], volumes: list[str] | None = None) -> list[OHLCVBar]:
    if volumes is None:
        volumes = ["1000"] * len(closes)
    assert len(closes) == len(volumes)
    return [_bar(c, v) for c, v in zip(closes, volumes)]


def _flat_closes(value: str, count: int) -> list[str]:
    return [value] * count


# ---------------------------------------------------------------------------
# Direction: trending market
# ---------------------------------------------------------------------------


def test_strong_uptrend_classifies_as_trending_up() -> None:
    closes = [str(100 + i) for i in range(25)]  # 100 -> 124, +24%
    state = classify_market_state(_bars(closes))

    assert state.direction_state == DirectionState.TRENDING_UP
    assert state.confidence > Decimal("0.5")
    assert "direction_state=trending_up" in state.reason_codes[0]
    assert state.metrics["direction_net_return_pct"] == Decimal("24.000000")


def test_strong_downtrend_classifies_as_trending_down() -> None:
    closes = [str(124 - i) for i in range(25)]  # 124 -> 100, ~ -19.35%
    state = classify_market_state(_bars(closes))

    assert state.direction_state == DirectionState.TRENDING_DOWN
    assert state.confidence > Decimal("0.5")
    assert "direction_state=trending_down" in state.reason_codes[0]
    assert state.metrics["direction_net_return_pct"] < Decimal("0")


def test_sideways_market_classifies_as_ranging() -> None:
    # Oscillates narrowly (0.2%) and ends almost exactly where it started.
    closes = ["100.0" if i % 2 == 0 else "100.2" for i in range(20)]
    state = classify_market_state(_bars(closes))

    assert state.direction_state == DirectionState.RANGING
    assert "direction_state=ranging" in state.reason_codes[0]
    assert abs(state.metrics["direction_net_return_pct"]) < Decimal("0.60")


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


def test_choppy_market_classifies_as_high_volatility() -> None:
    # Alternating +-large swings every bar.
    closes = ["100" if i % 2 == 0 else "110" for i in range(24)]
    state = classify_market_state(_bars(closes))

    assert state.volatility_state == VolatilityState.HIGH
    assert "volatility_state=high" in state.reason_codes[1]
    assert state.metrics["volatility_population_stdev"] >= Decimal("0.0040")


def test_near_flat_market_classifies_as_low_volatility() -> None:
    closes = ["100.000" if i % 2 == 0 else "100.001" for i in range(24)]
    state = classify_market_state(_bars(closes))

    assert state.volatility_state == VolatilityState.LOW
    assert "volatility_state=low" in state.reason_codes[1]
    assert state.metrics["volatility_population_stdev"] <= Decimal("0.0015")


def test_moderate_noise_classifies_as_normal_volatility() -> None:
    # Small, consistent per-bar moves that land strictly between the
    # low and high default thresholds (0.0015 - 0.0040).
    closes = []
    price = Decimal("100")
    for i in range(24):
        price += Decimal("0.25") if i % 2 == 0 else Decimal("-0.15")
        closes.append(str(price))
    state = classify_market_state(_bars(closes))

    assert state.volatility_state == VolatilityState.NORMAL
    assert "volatility_state=normal" in state.reason_codes[1]
    stdev = state.metrics["volatility_population_stdev"]
    assert Decimal("0.0015") < stdev < Decimal("0.0040")


# ---------------------------------------------------------------------------
# Participation (volume)
# ---------------------------------------------------------------------------


def test_expanding_volume_classifies_as_volume_expanding() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["1000"] * 10 + ["2000"] * 10  # ratio 2.0
    state = classify_market_state(_bars(closes, volumes))

    assert state.participation_state == ParticipationState.VOLUME_EXPANDING
    assert "participation_state=volume_expanding" in state.reason_codes[2]
    assert state.metrics["participation_volume_ratio"] == Decimal("2.000000")


def test_contracting_volume_classifies_as_volume_contracting() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["2000"] * 10 + ["800"] * 10  # ratio 0.4
    state = classify_market_state(_bars(closes, volumes))

    assert state.participation_state == ParticipationState.VOLUME_CONTRACTING
    assert "participation_state=volume_contracting" in state.reason_codes[2]
    assert state.metrics["participation_volume_ratio"] == Decimal("0.400000")


def test_stable_volume_classifies_as_volume_normal() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["1000"] * 20  # ratio 1.0
    state = classify_market_state(_bars(closes, volumes))

    assert state.participation_state == ParticipationState.VOLUME_NORMAL
    assert state.metrics["participation_volume_ratio"] == Decimal("1.000000")
    # Flat closes (ranging, zero volatility) + ratio exactly on the midpoint
    # of the normal participation band all independently maximize their own
    # sub-confidence, so the blended confidence is exactly 1.0.
    assert state.confidence == Decimal("1.0000")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_insufficient_bars_raises_insufficient_market_data_error() -> None:
    closes = _flat_closes("100", 5)
    with pytest.raises(InsufficientMarketDataError):
        classify_market_state(_bars(closes))


def test_exactly_min_bars_does_not_raise() -> None:
    params = MarketStateClassifierParams(min_bars=5)
    closes = _flat_closes("100", 5)
    state = classify_market_state(_bars(closes), params=params)
    assert state.evaluated_bar_count == 5


def test_non_positive_close_raises_value_error() -> None:
    closes = _flat_closes("100", 19) + ["0"]
    with pytest.raises(ValueError, match="strictly positive close"):
        classify_market_state(_bars(closes))


def test_negative_close_raises_value_error() -> None:
    closes = _flat_closes("100", 19) + ["-5"]
    with pytest.raises(ValueError, match="strictly positive close"):
        classify_market_state(_bars(closes))


def test_negative_volume_raises_value_error() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["1000"] * 19 + ["-1"]
    with pytest.raises(ValueError, match="non-negative volume"):
        classify_market_state(_bars(closes, volumes))


def test_zero_volume_throughout_defaults_to_participation_normal() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["0"] * 20
    state = classify_market_state(_bars(closes, volumes))

    assert state.participation_state == ParticipationState.VOLUME_NORMAL
    assert "zero" in state.reason_codes[2]
    assert state.metrics["participation_volume_ratio"] == Decimal("1")


def test_zero_direction_threshold_does_not_raise_and_stays_bounded() -> None:
    params = MarketStateClassifierParams(direction_trend_threshold_pct=Decimal("0"))
    closes = [str(100 + Decimal("0.01") * i) for i in range(20)]
    state = classify_market_state(_bars(closes), params=params)

    assert state.direction_state == DirectionState.TRENDING_UP
    assert Decimal("0") <= state.confidence <= Decimal("1")


def test_single_flat_bar_repeated_has_zero_return_and_zero_volatility() -> None:
    closes = _flat_closes("100", 20)
    state = classify_market_state(_bars(closes))

    assert state.direction_state == DirectionState.RANGING
    assert state.volatility_state == VolatilityState.LOW
    assert state.metrics["direction_net_return_pct"] == Decimal("0.000000")
    assert state.metrics["volatility_population_stdev"] == Decimal("0.000000")


# ---------------------------------------------------------------------------
# Boundary conditions (exact threshold values)
# ---------------------------------------------------------------------------


def test_net_return_exactly_at_up_threshold_classifies_trending_up_with_half_confidence() -> None:
    params = MarketStateClassifierParams()
    closes = _flat_closes("100", 19) + ["100.60"]  # exactly +0.60%
    state = classify_market_state(_bars(closes), params=params)

    assert state.direction_state == DirectionState.TRENDING_UP
    assert state.metrics["direction_net_return_pct"] == params.direction_trend_threshold_pct


def test_net_return_exactly_at_down_threshold_classifies_trending_down() -> None:
    params = MarketStateClassifierParams()
    closes = _flat_closes("100", 19) + ["99.40"]  # exactly -0.60%
    state = classify_market_state(_bars(closes), params=params)

    assert state.direction_state == DirectionState.TRENDING_DOWN
    assert state.metrics["direction_net_return_pct"] == -params.direction_trend_threshold_pct


def test_net_return_just_inside_threshold_classifies_ranging() -> None:
    closes = _flat_closes("100", 19) + ["100.59"]  # just under +0.60%
    state = classify_market_state(_bars(closes))
    assert state.direction_state == DirectionState.RANGING


def test_volatility_stdev_exactly_at_high_threshold_classifies_high() -> None:
    # Build a two-return series, compute its exact pstdev, then set the
    # high threshold to that exact value so the boundary is hit precisely.
    closes = ["100", "101", "100"]
    returns = [
        (Decimal(closes[1]) - Decimal(closes[0])) / Decimal(closes[0]),
        (Decimal(closes[2]) - Decimal(closes[1])) / Decimal(closes[1]),
    ]
    exact_stdev = Decimal(str(statistics.pstdev(float(r) for r in returns))).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_EVEN
    )
    params = MarketStateClassifierParams(
        min_bars=3,
        volatility_low_threshold=Decimal("0.000001"),
        volatility_high_threshold=exact_stdev,
    )
    state = classify_market_state(_bars(closes), params=params)

    assert state.metrics["volatility_population_stdev"] == exact_stdev
    assert state.volatility_state == VolatilityState.HIGH


def test_volatility_stdev_exactly_at_low_threshold_classifies_low() -> None:
    closes = ["100", "101", "100"]
    returns = [
        (Decimal(closes[1]) - Decimal(closes[0])) / Decimal(closes[0]),
        (Decimal(closes[2]) - Decimal(closes[1])) / Decimal(closes[1]),
    ]
    exact_stdev = Decimal(str(statistics.pstdev(float(r) for r in returns))).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_EVEN
    )
    params = MarketStateClassifierParams(
        min_bars=3,
        volatility_low_threshold=exact_stdev,
        volatility_high_threshold=exact_stdev + Decimal("0.01"),
    )
    state = classify_market_state(_bars(closes), params=params)

    assert state.metrics["volatility_population_stdev"] == exact_stdev
    assert state.volatility_state == VolatilityState.LOW


def test_participation_ratio_exactly_at_expansion_threshold_classifies_expanding() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["1000"] * 10 + ["1200"] * 10  # ratio exactly 1.20
    state = classify_market_state(_bars(closes, volumes))

    assert state.metrics["participation_volume_ratio"] == Decimal("1.200000")
    assert state.participation_state == ParticipationState.VOLUME_EXPANDING


def test_participation_ratio_exactly_at_contraction_threshold_classifies_contracting() -> None:
    closes = _flat_closes("100", 20)
    volumes = ["1000"] * 10 + ["800"] * 10  # ratio exactly 0.80
    state = classify_market_state(_bars(closes, volumes))

    assert state.metrics["participation_volume_ratio"] == Decimal("0.800000")
    assert state.participation_state == ParticipationState.VOLUME_CONTRACTING


# ---------------------------------------------------------------------------
# Params validation (fail-closed configuration)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_bars": 1},
        {"min_bars": 0},
        {"direction_trend_threshold_pct": Decimal("-1")},
        {"volatility_low_threshold": Decimal("-0.01")},
        {"volatility_low_threshold": Decimal("0.01"), "volatility_high_threshold": Decimal("0.001")},
        {"participation_contraction_ratio": Decimal("0")},
        {"participation_contraction_ratio": Decimal("-1")},
        {"participation_expansion_ratio": Decimal("0.5"), "participation_contraction_ratio": Decimal("0.8")},
        {"participation_expansion_ratio": Decimal("0.8"), "participation_contraction_ratio": Decimal("0.8")},
    ],
)
def test_invalid_params_raise_value_error(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MarketStateClassifierParams(**kwargs)  # type: ignore[arg-type]


def test_default_params_are_valid_and_reused() -> None:
    from app.services.market_state import DEFAULT_PARAMS

    assert DEFAULT_PARAMS.min_bars == 20
    closes = [str(100 + i) for i in range(20)]
    state = classify_market_state(_bars(closes))
    assert state.classifier_version == CLASSIFIER_VERSION


# ---------------------------------------------------------------------------
# Replay determinism
# ---------------------------------------------------------------------------


def test_identical_input_produces_identical_output() -> None:
    closes = [str(100 + Decimal("0.37") * i) for i in range(30)]
    volumes = [str(1000 + 13 * i) for i in range(30)]

    result_a = classify_market_state(_bars(list(closes), list(volumes)))
    result_b = classify_market_state(_bars(list(closes), list(volumes)))

    assert result_a == result_b
    assert result_a.reason_codes == result_b.reason_codes
    assert dict(result_a.metrics) == dict(result_b.metrics)
    assert result_a.confidence == result_b.confidence


def test_repeated_calls_do_not_leak_state_between_invocations() -> None:
    uptrend = classify_market_state(_bars([str(100 + i) for i in range(25)]))
    downtrend = classify_market_state(_bars([str(124 - i) for i in range(25)]))
    uptrend_again = classify_market_state(_bars([str(100 + i) for i in range(25)]))

    assert uptrend == uptrend_again
    assert uptrend.direction_state != downtrend.direction_state


# ---------------------------------------------------------------------------
# No-lookahead verification
# ---------------------------------------------------------------------------


def test_classification_of_a_window_is_unaffected_by_bars_not_passed_to_it() -> None:
    """The defining no-lookahead guarantee: classifying a window depends
    only on the bars in that window. Simulate two divergent futures
    following an identical historical window, and confirm that evaluating
    only the historical slice (as any correct replay caller must) produces
    an identical result regardless of which future eventually happens --
    because the function is never given the future bars at all.
    """

    history = [str(100 + i) for i in range(20)]
    future_bull = [str(120 + 5 * i) for i in range(1, 6)]
    future_bear = [str(120 - 15 * i) for i in range(1, 6)]  # stays positive: 105..45

    full_bull_timeline = _bars(history + future_bull)
    full_bear_timeline = _bars(history + future_bear)

    state_from_bull_timeline = classify_market_state(full_bull_timeline[:20])
    state_from_bear_timeline = classify_market_state(full_bear_timeline[:20])

    assert state_from_bull_timeline == state_from_bear_timeline


def test_classification_does_change_when_future_bars_are_actually_included() -> None:
    """Contrast case for the test above: this function has no magical
    insensitivity to more data -- it is transparent about using exactly
    what it is given. If a caller mistakenly includes future bars in the
    window, the result *will* change; the no-lookahead guarantee is a
    caller-discipline contract (only pass what was knowable), not a
    property that holds regardless of what is passed in.
    """

    history = _bars([str(100 + i) for i in range(20)])
    poisoned_future = _bars([str(500 + 50 * i) for i in range(1, 6)])  # extreme spike

    state_history_only = classify_market_state(history)
    state_with_future_leak = classify_market_state(history + poisoned_future)

    assert state_history_only != state_with_future_leak


# ---------------------------------------------------------------------------
# Purity / immutability
# ---------------------------------------------------------------------------


def test_market_state_is_frozen() -> None:
    state = classify_market_state(_bars([str(100 + i) for i in range(20)]))
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.confidence = Decimal("0.99")  # type: ignore[misc]


def test_ohlcv_bar_is_frozen() -> None:
    bar = _bar("100")
    with pytest.raises(dataclasses.FrozenInstanceError):
        bar.close = Decimal("200")  # type: ignore[misc]


def test_params_is_frozen() -> None:
    params = MarketStateClassifierParams()
    with pytest.raises(dataclasses.FrozenInstanceError):
        params.min_bars = 99  # type: ignore[misc]


def test_reason_codes_are_always_three_non_empty_strings() -> None:
    state = classify_market_state(_bars([str(100 + i) for i in range(20)]))
    assert len(state.reason_codes) == 3
    for reason in state.reason_codes:
        assert isinstance(reason, str)
        assert reason.strip() != ""


def test_confidence_is_always_within_unit_interval_across_varied_inputs() -> None:
    scenarios = [
        [str(100 + i) for i in range(25)],
        [str(124 - i) for i in range(25)],
        ["100.0" if i % 2 == 0 else "100.2" for i in range(20)],
        ["100" if i % 2 == 0 else "110" for i in range(24)],
    ]
    for closes in scenarios:
        state = classify_market_state(_bars(closes))
        assert Decimal("0") <= state.confidence <= Decimal("1")


def test_evaluated_bar_count_matches_input_length() -> None:
    for n in (20, 21, 50, 100):
        closes = [str(100 + i) for i in range(n)]
        state = classify_market_state(_bars(closes))
        assert state.evaluated_bar_count == n
