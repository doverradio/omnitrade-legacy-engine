from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from decimal import Decimal
import hashlib
import json
from time import perf_counter
from typing import Sequence

from strategy_lab.candles import Candle

from .annotations import annotations_for
from .features import extract_features
from .models import AnalysisConfig, AnalysisContext, AnalysisResult, DataQualityIssue, DetectorInput, ENGINE_VERSION, FEATURE_VERSION, json_value
from .recurrence import attach_recurrence
from .registry import DETECTORS, detector_versions


def analyze(candles: Sequence[Candle], context: AnalysisContext, config: AnalysisConfig | None = None) -> AnalysisResult:
    started = perf_counter()
    config = config or AnalysisConfig()
    normalized = tuple(candles)
    if not normalized:
        raise ValueError("Pattern Intelligence requires at least one candle")
    quality = validate_candles(normalized, context.interval)
    end = len(normalized) - 1 if context.selected_end_index is None else context.selected_end_index
    start = context.selected_start_index
    if start < 0 or end >= len(normalized) or start > end:
        raise ValueError("selected candle range is outside the dataset")
    dataset_identity, features = extract_features(normalized, start, end, config)
    detector_input = DetectorInput(normalized, start, end, features, context, config)
    findings = [finding for _, _, detector in DETECTORS for finding in detector(detector_input)]
    if len(normalized[start:end + 1]) < max(config.structure_window, config.short_momentum_window + 1):
        from .models import FindingCategory, FindingGroup
        from .detectors.common import finding
        findings.append(finding(detector_input, "insufficient_history_v1", "Insufficient History", FindingGroup.PRICE_STRUCTURE, start, end,
            {"available_candles": end - start + 1}, {"minimum_candles": max(config.structure_window, config.short_momentum_window + 1)},
            ("available candles are below the minimum feature lookback",), FindingCategory.INSUFFICIENT_EVIDENCE, False))
    findings = attach_recurrence(findings, normalized, features, config)
    findings = [replace(item, finding_id=f"finding_{index:04d}") for index, item in enumerate(findings, start=1)]
    annotations = annotations_for(findings)
    payload = {
        "engine_version": ENGINE_VERSION, "dataset_hash": dataset_identity, "range": [start, end],
        "partition": context.partition, "configuration": json_value(config), "findings": json_value(findings),
    }
    content_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return AnalysisResult(
        analysis_id=f"analysis_{content_hash[:16]}", engine_version=ENGINE_VERSION, feature_version=FEATURE_VERSION,
        dataset_id=context.dataset_id, dataset_hash=dataset_identity, selected_range=(start, end), partition=context.partition,
        configuration=config, data_quality=quality, findings=tuple(findings), annotations=annotations,
        detector_versions=detector_versions(), content_hash=content_hash,
        elapsed_ms=Decimal(str(round((perf_counter() - started) * 1000, 3))),
    )


def validate_candles(candles: tuple[Candle, ...], interval: str | None) -> tuple[DataQualityIssue, ...]:
    issues: list[DataQualityIssue] = []
    interval_seconds = _interval_seconds(interval)
    for index, candle in enumerate(candles):
        if candle.timestamp.tzinfo is None or candle.timestamp.utcoffset() is None:
            issues.append(DataQualityIssue("invalid_timestamp", "timestamp must include a timezone", index, candle.timestamp))
        if candle.volume < 0:
            issues.append(DataQualityIssue("invalid_volume", "volume must be non-negative", index, candle.timestamp))
        if candle.high < max(candle.open, candle.close) or candle.low > min(candle.open, candle.close):
            issues.append(DataQualityIssue("impossible_ohlc", "open/close must be inside high/low", index, candle.timestamp))
        if index:
            difference = int((candle.timestamp - candles[index - 1].timestamp).total_seconds())
            if difference == 0:
                issues.append(DataQualityIssue("duplicate", "duplicate candle timestamp", index, candle.timestamp))
            elif difference < 0:
                issues.append(DataQualityIssue("invalid_timestamp", "timestamps must be strictly increasing", index, candle.timestamp))
            elif interval_seconds and difference > interval_seconds:
                issues.append(DataQualityIssue("gap", f"expected {interval_seconds}s interval; observed {difference}s", index, candle.timestamp))
    return tuple(issues)


def _interval_seconds(interval: str | None) -> int | None:
    if not interval or len(interval) < 2 or not interval[:-1].isdigit():
        return None
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(interval[-1].lower())
    return int(interval[:-1]) * multiplier if multiplier else None