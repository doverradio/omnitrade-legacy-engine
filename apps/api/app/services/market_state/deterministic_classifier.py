"""Canonical deterministic Market State Classifier.

**Research / replay layer only.** See the module docstring in `contracts.py`
and `docs/MARKET_STATE_CLASSIFIER.md`. This module must never be imported
from a live strategy, risk, mandate/campaign, or order-execution path.

`classify_market_state` is a pure function: given the same OHLCV bars and
the same `MarketStateClassifierParams`, it always returns a field-for-field
identical `MarketState`. It performs no I/O, opens no database session,
reads no wall clock, holds no module-level mutable state, and never
inspects any bar other than the ones passed to it in `candles`. This is
what makes it replay-safe and free of look-ahead leakage *by construction*:
there is no code path inside this function through which a bar not present
in its own `candles` argument could influence its output. Callers doing
replay/backtest evaluation are responsible for the (separate, caller-side)
discipline of only ever passing bars that would genuinely have been known
at the simulated evaluation instant -- see
`docs/PIPELINE_ARCHITECTURE.md` §16 for that discipline's general statement
across the platform.

Inputs are OHLCV only, per this module's governing prompt. No order book,
funding-rate, ETF, on-chain, news, macro, or derivatives evidence is
consumed, and no network or database call is ever made from this module.

This is explicitly **not** the platform's production-authoritative regime
classifier -- see `app.services.strategy_outcomes.service.classify_regime_labels`
and `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`, which
names that function canonical for live strategy weighting and this one
canonical for the separate research/replay baseline responsibility.

Tie-breaking / boundary convention: each axis below checks its "more
extreme" states before falling through to the middle/default state, in a
fixed, documented order (direction: up-threshold, then down-threshold, else
ranging; volatility: high-threshold, then low-threshold, else normal;
participation: expansion-threshold, then contraction-threshold, else
normal). A metric exactly equal to a threshold resolves toward the extreme
state that threshold gates, never toward the middle state -- this is
deterministic and covered by dedicated boundary tests.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from .contracts import (
    DirectionState,
    MarketState,
    MarketStateClassifierParams,
    OHLCVBar,
    ParticipationState,
    VolatilityState,
)

CLASSIFIER_VERSION = "market_state_deterministic_v1"

DEFAULT_PARAMS = MarketStateClassifierParams()

_METRIC_QUANTIZE = Decimal("0.000001")
_CONFIDENCE_QUANTIZE = Decimal("0.0001")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")
_TWO = Decimal("2")


class InsufficientMarketDataError(ValueError):
    """Raised when fewer than `params.min_bars` bars are supplied.

    Fails closed rather than silently classifying an under-evidenced
    window, consistent with the project's fail-closed principle
    (`PROJECT_CONSTITUTION.md` Article VIII).
    """


def classify_market_state(
    candles: Sequence[OHLCVBar],
    *,
    params: MarketStateClassifierParams = DEFAULT_PARAMS,
) -> MarketState:
    """Classify direction, volatility, and participation state from a
    chronologically-ordered (oldest first) OHLCV window.

    Raises `InsufficientMarketDataError` if fewer than `params.min_bars`
    bars are supplied, and `ValueError` if any bar has a non-positive
    `close` or a negative `volume` -- both fail-closed rather than
    producing a classification from degenerate input.
    """

    if len(candles) < params.min_bars:
        raise InsufficientMarketDataError(
            f"classify_market_state requires at least {params.min_bars} bars, got {len(candles)}."
        )
    for bar in candles:
        if bar.close <= 0:
            raise ValueError("classify_market_state requires strictly positive close prices.")
        if bar.volume < 0:
            raise ValueError("classify_market_state requires non-negative volume.")

    direction_state, direction_confidence, direction_reason, direction_metrics = _classify_direction(
        candles, params
    )
    volatility_state, volatility_confidence, volatility_reason, volatility_metrics = _classify_volatility(
        candles, params
    )
    participation_state, participation_confidence, participation_reason, participation_metrics = (
        _classify_participation(candles, params)
    )

    confidence = (
        (direction_confidence + volatility_confidence + participation_confidence) / Decimal("3")
    ).quantize(_CONFIDENCE_QUANTIZE, rounding=ROUND_HALF_EVEN)

    metrics: dict[str, Decimal] = {}
    metrics.update(direction_metrics)
    metrics.update(volatility_metrics)
    metrics.update(participation_metrics)

    return MarketState(
        direction_state=direction_state,
        volatility_state=volatility_state,
        participation_state=participation_state,
        confidence=_clamp(confidence),
        reason_codes=(direction_reason, volatility_reason, participation_reason),
        metrics=metrics,
        classifier_version=CLASSIFIER_VERSION,
        evaluated_bar_count=len(candles),
    )


def _clamp(value: Decimal) -> Decimal:
    return max(_ZERO, min(_ONE, value))


def _classify_direction(
    candles: Sequence[OHLCVBar], params: MarketStateClassifierParams
) -> tuple[DirectionState, Decimal, str, dict[str, Decimal]]:
    start_close = candles[0].close
    end_close = candles[-1].close
    net_return_pct = ((end_close - start_close) / start_close * Decimal("100")).quantize(
        _METRIC_QUANTIZE, rounding=ROUND_HALF_EVEN
    )
    threshold = params.direction_trend_threshold_pct

    if net_return_pct >= threshold:
        state = DirectionState.TRENDING_UP
    elif net_return_pct <= -threshold:
        state = DirectionState.TRENDING_DOWN
    else:
        state = DirectionState.RANGING

    if state == DirectionState.RANGING:
        # Confidence in "ranging" grows the closer net_return is to zero.
        distance_ratio = (abs(net_return_pct) / threshold) if threshold != 0 else _ZERO
        confidence = _clamp(_ONE - distance_ratio)
    else:
        # Confidence in a trend grows the further net_return exceeds the
        # threshold, starting at 0.5 exactly at the threshold and saturating
        # at 1.0 once the excess reaches the threshold's own magnitude again.
        excess_ratio = ((abs(net_return_pct) - threshold) / threshold) if threshold != 0 else _ONE
        confidence = _clamp(_HALF + excess_ratio / _TWO)

    reason = (
        f"direction_state={state.value}: net_return_pct={net_return_pct} over "
        f"{len(candles)} bars (trend_threshold_pct={threshold})"
    )
    metrics = {"direction_net_return_pct": net_return_pct}
    return state, confidence, reason, metrics


def _classify_volatility(
    candles: Sequence[OHLCVBar], params: MarketStateClassifierParams
) -> tuple[VolatilityState, Decimal, str, dict[str, Decimal]]:
    returns: list[float] = []
    for idx in range(1, len(candles)):
        prev_close = candles[idx - 1].close
        curr_close = candles[idx].close
        if prev_close != 0:
            returns.append(float((curr_close - prev_close) / prev_close))

    raw_stdev = statistics.pstdev(returns) if len(returns) >= 2 else 0.0
    stdev = Decimal(str(raw_stdev)).quantize(_METRIC_QUANTIZE, rounding=ROUND_HALF_EVEN)

    low_threshold = params.volatility_low_threshold
    high_threshold = params.volatility_high_threshold

    if stdev >= high_threshold:
        state = VolatilityState.HIGH
        span = high_threshold if high_threshold != 0 else _ONE
        confidence = _clamp(_HALF + (stdev - high_threshold) / span / _TWO)
    elif stdev <= low_threshold:
        state = VolatilityState.LOW
        span = low_threshold if low_threshold != 0 else _ONE
        confidence = _clamp(_HALF + (low_threshold - stdev) / span / _TWO)
    else:
        state = VolatilityState.NORMAL
        mid = (low_threshold + high_threshold) / _TWO
        half_band = (high_threshold - low_threshold) / _TWO
        distance_from_mid = abs(stdev - mid)
        span = half_band if half_band != 0 else _ONE
        confidence = _clamp(_ONE - distance_from_mid / span)

    reason = (
        f"volatility_state={state.value}: population_stdev_of_returns={stdev} "
        f"(low_threshold={low_threshold}, high_threshold={high_threshold})"
    )
    metrics = {"volatility_population_stdev": stdev}
    return state, confidence, reason, metrics


def _classify_participation(
    candles: Sequence[OHLCVBar], params: MarketStateClassifierParams
) -> tuple[ParticipationState, Decimal, str, dict[str, Decimal]]:
    volumes = [bar.volume for bar in candles]
    pivot = max(1, len(volumes) // 2)
    earlier = volumes[:pivot]
    recent = volumes[pivot:] or earlier

    earlier_avg = sum(earlier, _ZERO) / Decimal(len(earlier))
    recent_avg = sum(recent, _ZERO) / Decimal(len(recent))

    if earlier_avg == 0 and recent_avg == 0:
        state = ParticipationState.VOLUME_NORMAL
        reason = (
            "participation_state=volume_normal: both earlier and recent average "
            "volume are zero (no participation evidence); defaulting to neutral "
            "state with reduced confidence"
        )
        metrics = {"participation_volume_ratio": _ONE}
        return state, _HALF, reason, metrics

    if earlier_avg == 0:
        # Recent volume is positive against a zero base -- treat as
        # maximally expanding rather than dividing by zero.
        ratio = params.participation_expansion_ratio
    else:
        ratio = (recent_avg / earlier_avg).quantize(_METRIC_QUANTIZE, rounding=ROUND_HALF_EVEN)

    expansion = params.participation_expansion_ratio
    contraction = params.participation_contraction_ratio

    if ratio >= expansion:
        state = ParticipationState.VOLUME_EXPANDING
        confidence = _clamp(_HALF + (ratio - expansion) / expansion / _TWO)
    elif ratio <= contraction:
        state = ParticipationState.VOLUME_CONTRACTING
        span = contraction if contraction != 0 else _ONE
        confidence = _clamp(_HALF + (contraction - ratio) / span / _TWO)
    else:
        state = ParticipationState.VOLUME_NORMAL
        mid = (expansion + contraction) / _TWO
        half_band = (expansion - contraction) / _TWO
        distance_from_mid = abs(ratio - mid)
        span = half_band if half_band != 0 else _ONE
        confidence = _clamp(_ONE - distance_from_mid / span)

    reason = (
        f"participation_state={state.value}: recent_avg_volume/earlier_avg_volume="
        f"{ratio} (expansion_threshold={expansion}, contraction_threshold={contraction})"
    )
    metrics = {"participation_volume_ratio": ratio}
    return state, confidence, reason, metrics
