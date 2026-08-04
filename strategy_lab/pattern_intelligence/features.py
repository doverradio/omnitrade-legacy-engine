from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
import hashlib
from statistics import median
from typing import Callable, Sequence

from strategy_lab.candles import Candle

from .models import AnalysisConfig, FEATURE_VERSION, FeatureSet

ZERO = Decimal("0")
ONE = Decimal("1")
_FEATURE_CACHE: "OrderedDict[tuple[str, int, int, str, tuple[str, ...]], FeatureSet]" = OrderedDict()
_CACHE_SIZE = 16


def dataset_hash(candles: Sequence[Candle]) -> str:
    digest = hashlib.sha256()
    for candle in candles:
        digest.update(
            f"{candle.timestamp.isoformat()}|{candle.open}|{candle.high}|{candle.low}|{candle.close}|{candle.volume}\n".encode()
        )
    return digest.hexdigest()


def extract_features(
    candles: Sequence[Candle],
    selected_start: int,
    selected_end: int,
    config: AnalysisConfig,
) -> tuple[str, FeatureSet]:
    identity = dataset_hash(candles)
    key = (identity, selected_start, selected_end, FEATURE_VERSION, config.cache_values())
    cached = _FEATURE_CACHE.get(key)
    if cached is not None:
        _FEATURE_CACHE.move_to_end(key)
        return identity, cached

    closes = [item.close for item in candles]
    highs = [item.high for item in candles]
    lows = [item.low for item in candles]
    volumes = [item.volume for item in candles]
    returns = _period_returns(closes, 1)
    multi_returns = _period_returns(closes, config.structure_window)
    rolling_high = _rolling(highs, config.structure_window, max)
    rolling_low = _rolling(lows, config.structure_window, min)
    rolling_range = _combine(rolling_high, rolling_low, lambda high, low: high - low)
    normalized_range = _combine(rolling_range, closes, _safe_ratio)
    slopes = _rolling_slope(closes, config.structure_window)
    slope_acceleration = _difference(slopes)
    distance_high = _combine(closes, rolling_high, lambda close, high: _safe_ratio(close - high, high))
    distance_low = _combine(closes, rolling_low, lambda close, low: _safe_ratio(close - low, low))

    return_volatility = _rolling_std(returns, config.structure_window)
    true_ranges = _true_ranges(candles)
    atr = _rolling(true_ranges, config.structure_window, _mean)
    volatility_percentile = _rolling_percentile(return_volatility, config.baseline_window)
    range_ratio = _baseline_ratio(normalized_range, config.baseline_window, config.structure_window)

    short_momentum = _period_returns(closes, config.short_momentum_window)
    medium_momentum = _period_returns(closes, config.medium_momentum_window)
    momentum_acceleration = _difference(short_momentum)
    momentum_deceleration = _difference(short_momentum, negate=True)
    recovery = _combine(closes, rolling_low, lambda close, low: _safe_ratio(close - low, low))
    exhaustion = _combine(short_momentum, slope_acceleration, lambda momentum, acceleration: abs(momentum) * max(ZERO, -momentum * acceleration))

    volume_mean = _rolling(volumes, config.structure_window, _mean)
    volume_median = _rolling(volumes, config.structure_window, lambda values: Decimal(median(values)))
    volume_percentile = _rolling_percentile([value for value in volumes], config.baseline_window)
    volume_ratio = _combine(volumes, volume_mean, _safe_ratio)
    price_volume_agreement = _combine(returns, volume_ratio, lambda candle_return, ratio: candle_return * (ratio - ONE))
    volume_divergence = _combine(returns, volume_ratio, lambda candle_return, ratio: -candle_return * (ratio - ONE))

    features = FeatureSet(values={
        "candle_return": tuple(returns),
        "multi_candle_return": tuple(multi_returns),
        "rolling_high": tuple(rolling_high),
        "rolling_low": tuple(rolling_low),
        "rolling_range": tuple(rolling_range),
        "normalized_range": tuple(normalized_range),
        "slope": tuple(slopes),
        "slope_acceleration": tuple(slope_acceleration),
        "distance_from_recent_high": tuple(distance_high),
        "distance_from_recent_low": tuple(distance_low),
        "return_volatility": tuple(return_volatility),
        "true_range": tuple(true_ranges),
        "atr": tuple(atr),
        "volatility_percentile": tuple(volatility_percentile),
        "range_ratio": tuple(range_ratio),
        "range_contraction": tuple(_map(range_ratio, lambda value: ONE - value)),
        "range_expansion": tuple(_map(range_ratio, lambda value: value - ONE)),
        "short_momentum": tuple(short_momentum),
        "medium_momentum": tuple(medium_momentum),
        "momentum_acceleration": tuple(momentum_acceleration),
        "momentum_deceleration": tuple(momentum_deceleration),
        "recovery": tuple(recovery),
        "exhaustion_proxy": tuple(exhaustion),
        "volume_mean": tuple(volume_mean),
        "volume_median": tuple(volume_median),
        "volume_percentile": tuple(volume_percentile),
        "volume_ratio": tuple(volume_ratio),
        "volume_expansion": tuple(_map(volume_ratio, lambda value: value - ONE)),
        "volume_contraction": tuple(_map(volume_ratio, lambda value: ONE - value)),
        "price_volume_agreement": tuple(price_volume_agreement),
        "price_volume_divergence": tuple(volume_divergence),
    })
    _FEATURE_CACHE[key] = features
    _FEATURE_CACHE.move_to_end(key)
    while len(_FEATURE_CACHE) > _CACHE_SIZE:
        _FEATURE_CACHE.popitem(last=False)
    return identity, features


def clear_feature_cache() -> None:
    _FEATURE_CACHE.clear()


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return ZERO if denominator == ZERO else numerator / denominator


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def _period_returns(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    return [None if index < period else _safe_ratio(value - values[index - period], values[index - period]) for index, value in enumerate(values)]


def _rolling(values: Sequence[Decimal | None], window: int, operation: Callable[[Sequence[Decimal]], Decimal]) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    for index in range(len(values)):
        sample = values[max(0, index - window + 1):index + 1]
        result.append(None if len(sample) < window or any(value is None for value in sample) else operation([value for value in sample if value is not None]))
    return result


def _rolling_std(values: Sequence[Decimal | None], window: int) -> list[Decimal | None]:
    def standard_deviation(sample: Sequence[Decimal]) -> Decimal:
        average = _mean(sample)
        return (_mean([(value - average) ** 2 for value in sample])).sqrt()
    return _rolling(values, window, standard_deviation)


def _rolling_slope(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    x_values = [Decimal(index) for index in range(window)]
    x_mean = _mean(x_values)
    denominator = sum(((value - x_mean) ** 2 for value in x_values), ZERO)
    return _rolling(values, window, lambda sample: sum(((x_values[index] - x_mean) * (value - _mean(sample)) for index, value in enumerate(sample)), ZERO) / denominator)


def _rolling_percentile(values: Sequence[Decimal | None], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    for index, current in enumerate(values):
        sample = [value for value in values[max(0, index - window + 1):index + 1] if value is not None]
        result.append(None if current is None or len(sample) < window else Decimal(sum(value <= current for value in sample)) / Decimal(len(sample)))
    return result


def _baseline_ratio(values: Sequence[Decimal | None], window: int, exclusion_window: int) -> list[Decimal | None]:
    baseline: list[Decimal | None] = []
    for index in range(len(values)):
        sample = values[max(0, index - exclusion_window - window + 1):index - exclusion_window + 1]
        baseline.append(None if len(sample) < window or any(value is None for value in sample) else _mean([value for value in sample if value is not None]))
    return _combine(values, baseline, _safe_ratio)


def _difference(values: Sequence[Decimal | None], negate: bool = False) -> list[Decimal | None]:
    result: list[Decimal | None] = [None]
    for previous, current in zip(values, values[1:]):
        difference = None if previous is None or current is None else current - previous
        result.append(-difference if difference is not None and negate else difference)
    return result


def _combine(
    left: Sequence[Decimal | None],
    right: Sequence[Decimal | None],
    operation: Callable[[Decimal, Decimal], Decimal],
) -> list[Decimal | None]:
    return [None if first is None or second is None else operation(first, second) for first, second in zip(left, right)]


def _map(values: Sequence[Decimal | None], operation: Callable[[Decimal], Decimal]) -> list[Decimal | None]:
    return [None if value is None else operation(value) for value in values]


def _true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    result: list[Decimal] = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1].close if index else candle.open
        result.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
    return result