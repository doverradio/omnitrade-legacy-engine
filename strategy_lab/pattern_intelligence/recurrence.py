from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from statistics import median

from .models import AnalysisConfig, FeatureSet, Finding, FindingCategory, RecurrenceOutcome
from .partitions import partition_bounds
from strategy_lab.candles import Candle

ZERO = Decimal("0")


def attach_recurrence(findings: list[Finding], candles: tuple[Candle, ...], features: FeatureSet, config: AnalysisConfig) -> list[Finding]:
    return [_with_recurrence(item, candles, features, config) for item in findings]


def _with_recurrence(finding: Finding, candles: tuple[Candle, ...], features: FeatureSet, config: AnalysisConfig) -> Finding:
    matches = comparable_prior_indices(finding.end_index, features, config)
    outcomes: list[RecurrenceOutcome] = []
    for partition, (partition_start, partition_end) in partition_bounds(len(candles)).items():
        partition_matches = [index for index in matches if partition_start <= index <= partition_end]
        for horizon in config.forward_horizons:
            eligible = [index for index in partition_matches if index + horizon <= partition_end]
            outcomes.append(_outcome(partition, horizon, eligible, candles, config))
    sufficient = len(matches) >= config.minimum_recurrences
    category = FindingCategory.STATISTICAL_EVIDENCE if sufficient else FindingCategory.INSUFFICIENT_EVIDENCE
    return replace(finding, recurrence=tuple(outcomes), sufficient_evidence=sufficient, category=category)


def comparable_prior_indices(end_index: int, features: FeatureSet, config: AnalysisConfig) -> list[int]:
    names = ("normalized_range", "slope", "short_momentum", "volume_ratio")
    target = [features.at(name, end_index) for name in names]
    if any(value is None for value in target):
        return []
    matches: list[int] = []
    start = max(config.baseline_window, config.medium_momentum_window)
    for index in range(start, end_index):
        candidate = [features.at(name, index) for name in names]
        if any(value is None for value in candidate):
            continue
        distances = [abs(candidate_value - target_value) / max(abs(target_value), Decimal("0.000001"))
                     for candidate_value, target_value in zip(candidate, target) if candidate_value is not None and target_value is not None]
        if all(distance <= config.recurrence_tolerance_pct for distance in distances):
            matches.append(index)
    return matches


def _outcome(partition: str, horizon: int, indices: list[int], candles: tuple[Candle, ...], config: AnalysisConfig) -> RecurrenceOutcome:
    returns = [candles[index + horizon].close / candles[index].close - 1 for index in indices]
    cost = (config.fee_pct + config.slippage_pct) * Decimal("2")
    mfes: list[Decimal] = []
    maes: list[Decimal] = []
    target_first = 0
    for index in indices:
        entry = candles[index].close
        future = candles[index + 1:index + horizon + 1]
        mfes.append(max(item.high / entry - 1 for item in future))
        maes.append(min(item.low / entry - 1 for item in future))
        target_index = next((offset for offset, item in enumerate(future) if item.high >= entry * (1 + config.target_pct)), None)
        stop_index = next((offset for offset, item in enumerate(future) if item.low <= entry * (1 - config.stop_pct)), None)
        if target_index is not None and (stop_index is None or target_index < stop_index):
            target_first += 1
    count = len(returns)
    average = sum(returns, ZERO) / Decimal(count) if count else None
    interval = None
    if count >= 2 and average is not None:
        variance = sum(((item - average) ** 2 for item in returns), ZERO) / Decimal(count - 1)
        margin = Decimal("1.96") * (variance / Decimal(count)).sqrt()
        interval = (average - margin, average + margin)
    return RecurrenceOutcome(
        partition=partition, forward_horizon=horizon, occurrence_count=count,
        average_forward_return=average,
        median_forward_return=Decimal(median(returns)) if returns else None,
        positive_return_frequency=Decimal(sum(item > 0 for item in returns)) / Decimal(count) if count else None,
        net_positive_frequency=Decimal(sum(item - cost > 0 for item in returns)) / Decimal(count) if count else None,
        maximum_favorable_excursion=sum(mfes, ZERO) / Decimal(count) if count else None,
        maximum_adverse_excursion=sum(maes, ZERO) / Decimal(count) if count else None,
        target_before_stop_frequency=Decimal(target_first) / Decimal(count) if count else None,
        confidence_interval_95=interval,
        sufficient_evidence=count >= config.minimum_recurrences,
    )