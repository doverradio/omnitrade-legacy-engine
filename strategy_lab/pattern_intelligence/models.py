from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from strategy_lab.candles import Candle

ENGINE_VERSION = "1.0.0"
FEATURE_VERSION = "1.0.0"


class FindingCategory(str, Enum):
    OBSERVATION = "OBSERVATION"
    STATISTICAL_EVIDENCE = "STATISTICAL_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONTRADICTION = "CONTRADICTION"


class FindingGroup(str, Enum):
    PRICE_STRUCTURE = "Price Structure"
    VOLATILITY = "Volatility"
    MOMENTUM = "Momentum"
    VOLUME = "Volume"
    BREAKOUTS = "Breakouts"
    STRATEGY_BEHAVIOR = "Strategy Behavior"


@dataclass(frozen=True)
class AnalysisConfig:
    structure_window: int = 6
    baseline_window: int = 12
    short_momentum_window: int = 3
    medium_momentum_window: int = 8
    recurrence_tolerance_pct: Decimal = Decimal("0.20")
    contraction_ratio: Decimal = Decimal("0.75")
    expansion_ratio: Decimal = Decimal("1.25")
    flat_range_pct: Decimal = Decimal("0.01")
    support_tolerance_pct: Decimal = Decimal("0.003")
    breakout_buffer_pct: Decimal = Decimal("0.001")
    failed_breakout_window: int = 4
    minimum_recurrences: int = 5
    forward_horizons: tuple[int, ...] = (1, 2, 4, 8, 16)
    target_pct: Decimal = Decimal("0.01")
    stop_pct: Decimal = Decimal("0.01")
    narrow_limit_miss_pct: Decimal = Decimal("0.002")
    missed_entry_max_distance_pct: Decimal = Decimal("0.01")
    missed_entry_follow_through_pct: Decimal = Decimal("0.02")
    missed_entry_horizon: int = 8
    fee_pct: Decimal = Decimal("0.002")
    slippage_pct: Decimal = Decimal("0.0005")

    def cache_values(self) -> tuple[str, ...]:
        return tuple(f"{key}={value}" for key, value in sorted(asdict(self).items()))


@dataclass(frozen=True)
class AnalysisContext:
    dataset_id: str
    asset: str | None = None
    exchange: str | None = None
    interval: str | None = None
    strategy_version: str | None = None
    selected_start_index: int = 0
    selected_end_index: int | None = None
    partition: str = "entire_dataset"
    replay_events: Sequence[Any] = ()
    trades: Sequence[Any] = ()
    selected_trade: Any | None = None
    equity_curve: Sequence[Decimal] = ()
    buy_hold_return_pct: Decimal | None = None
    strategy_return_pct: Decimal | None = None


@dataclass(frozen=True)
class DataQualityIssue:
    issue_type: str
    message: str
    index: int | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class RecurrenceOutcome:
    partition: str
    forward_horizon: int
    occurrence_count: int
    average_forward_return: Decimal | None
    median_forward_return: Decimal | None
    positive_return_frequency: Decimal | None
    net_positive_frequency: Decimal | None
    maximum_favorable_excursion: Decimal | None
    maximum_adverse_excursion: Decimal | None
    target_before_stop_frequency: Decimal | None
    confidence_interval_95: tuple[Decimal, Decimal] | None
    sufficient_evidence: bool


@dataclass(frozen=True)
class Finding:
    finding_id: str
    detector_id: str
    detector_version: str
    category: FindingCategory
    group: FindingGroup
    pattern_name: str
    start_index: int
    end_index: int
    start_time: datetime
    end_time: datetime
    measurements: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    evidence: tuple[str, ...]
    conditions: tuple[str, ...]
    sufficient_evidence: bool
    recurrence: tuple[RecurrenceOutcome, ...] = ()


@dataclass(frozen=True)
class ChartAnnotation:
    annotation_id: str
    pattern_id: str
    start_time: datetime
    end_time: datetime
    label: str
    chart_region: str
    details_ref: str


@dataclass(frozen=True)
class AnalysisResult:
    analysis_id: str
    engine_version: str
    feature_version: str
    dataset_id: str
    dataset_hash: str
    selected_range: tuple[int, int]
    partition: str
    configuration: AnalysisConfig
    data_quality: tuple[DataQualityIssue, ...]
    findings: tuple[Finding, ...]
    annotations: tuple[ChartAnnotation, ...]
    detector_versions: Mapping[str, str]
    content_hash: str
    elapsed_ms: Decimal


@dataclass(frozen=True)
class DetectorInput:
    candles: Sequence[Candle]
    selected_start: int
    selected_end: int
    features: "FeatureSet"
    context: AnalysisContext
    config: AnalysisConfig


@dataclass(frozen=True)
class FeatureSet:
    values: Mapping[str, tuple[Decimal | None, ...]] = field(default_factory=dict)

    def at(self, name: str, index: int) -> Decimal | None:
        return self.values[name][index]


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value