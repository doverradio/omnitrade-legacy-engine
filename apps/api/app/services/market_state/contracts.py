"""Canonical output contracts for the deterministic Market State Classifier.

**Research / replay layer only.** See `docs/MARKET_STATE_CLASSIFIER.md` and
`docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`. This module
must never be imported by any live production decision path (strategy
evaluation, risk evaluation, campaign/mandate orchestration, or order
execution). The platform's production-authoritative regime classifier
remains `app.services.strategy_outcomes.service.classify_regime_labels`, per
ADR-0018 -- this module is the separate, explicitly-labeled research-only
baseline that ADR anticipates, intended as the fixed comparison point future
HMM, Bayesian, or neural-network regime research must be evaluated against.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class DirectionState(str, Enum):
    """Trend direction over the evaluated window."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"


class VolatilityState(str, Enum):
    """Realized-return dispersion over the evaluated window."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class ParticipationState(str, Enum):
    """Volume-participation trend over the evaluated window."""

    VOLUME_CONTRACTING = "volume_contracting"
    VOLUME_NORMAL = "volume_normal"
    VOLUME_EXPANDING = "volume_expanding"


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """One OHLCV bar. Chronological ordering (oldest first) is the caller's
    responsibility -- this module never sorts, reorders, or otherwise
    inspects timestamp ordering of its input, since silently doing so would
    itself be a hidden assumption the project's explainability principles
    disallow (`PROJECT_CONSTITUTION.md` Article I).

    `high`/`low` are accepted for OHLCV-input-contract completeness and for
    forward compatibility (e.g. a future true-range-based volatility
    measure), but are not consumed by the v1 classifier math in
    `deterministic_classifier.py`, which uses only `close` and `volume`.
    This is stated explicitly rather than left to be discovered by reading
    the implementation.
    """

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class MarketStateClassifierParams:
    """Versioned, explicit threshold parameter set.

    Every threshold below is an initial hypothesis, not a validated
    optimum -- per `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md`
    §7.2 ("No Arbitrary Threshold Is Permanent"). Future walk-forward
    research may supersede these defaults, but any such change must
    construct a new, explicitly versioned `MarketStateClassifierParams`
    (and, if the semantics of a state change, a new `classifier_version` in
    `deterministic_classifier.py`) -- never a silent in-place edit to
    `DEFAULT_PARAMS` that would make old and new results indistinguishable.
    """

    min_bars: int = 20
    direction_trend_threshold_pct: Decimal = Decimal("0.60")
    volatility_low_threshold: Decimal = Decimal("0.0015")
    volatility_high_threshold: Decimal = Decimal("0.0040")
    participation_expansion_ratio: Decimal = Decimal("1.20")
    participation_contraction_ratio: Decimal = Decimal("0.80")

    def __post_init__(self) -> None:
        if self.min_bars < 2:
            raise ValueError("MarketStateClassifierParams.min_bars must be >= 2.")
        if self.direction_trend_threshold_pct < 0:
            raise ValueError("MarketStateClassifierParams.direction_trend_threshold_pct must be >= 0.")
        if self.volatility_low_threshold < 0:
            raise ValueError("MarketStateClassifierParams.volatility_low_threshold must be >= 0.")
        if self.volatility_high_threshold < self.volatility_low_threshold:
            raise ValueError(
                "MarketStateClassifierParams.volatility_high_threshold must be >= volatility_low_threshold."
            )
        if self.participation_contraction_ratio <= 0:
            raise ValueError("MarketStateClassifierParams.participation_contraction_ratio must be > 0.")
        if self.participation_expansion_ratio <= self.participation_contraction_ratio:
            raise ValueError(
                "MarketStateClassifierParams.participation_expansion_ratio must be "
                "> participation_contraction_ratio."
            )


@dataclass(frozen=True, slots=True)
class MarketState:
    """The single canonical Market State output object.

    Immutable, fully explainable, and computed only from the OHLCV bars
    actually supplied to `deterministic_classifier.classify_market_state` --
    see that function's docstring for the no-lookahead guarantee this
    object's fields depend on.
    """

    direction_state: DirectionState
    volatility_state: VolatilityState
    participation_state: ParticipationState
    confidence: Decimal
    reason_codes: tuple[str, ...]
    metrics: Mapping[str, Decimal]
    classifier_version: str
    evaluated_bar_count: int

    def __init__(
        self,
        *,
        direction_state: DirectionState,
        volatility_state: VolatilityState,
        participation_state: ParticipationState,
        confidence: Decimal,
        reason_codes: tuple[str, ...],
        metrics: Mapping[str, Decimal],
        classifier_version: str,
        evaluated_bar_count: int,
    ) -> None:
        object.__setattr__(self, "direction_state", direction_state)
        object.__setattr__(self, "volatility_state", volatility_state)
        object.__setattr__(self, "participation_state", participation_state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "reason_codes", tuple(reason_codes))
        object.__setattr__(self, "metrics", MappingProxyType(dict(metrics)))
        object.__setattr__(self, "classifier_version", classifier_version)
        object.__setattr__(self, "evaluated_bar_count", evaluated_bar_count)
