"""Output and configuration contracts for the deterministic walk-forward
Market State evaluation harness.

**Research / replay layer only.** See `docs/MARKET_STATE_WALK_FORWARD_EVALUATION.md`
and `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`. This
module must never be imported by any live production decision path.

Deliberately distinct from `contracts.py` (the canonical classifier's own
output contracts): `OHLCVBar` has no timestamp, so this module defines its
own `WalkForwardBar` (an OHLCV bar plus an explicit `open_time`) rather than
modifying the canonical classifier's contracts. `WalkForwardBar.to_ohlcv_bar()`
converts to the classifier's own `OHLCVBar` for the actual classification
call -- the canonical classifier module is untouched by this addition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .contracts import (
    DirectionState,
    MarketStateClassifierParams,
    OHLCVBar,
    ParticipationState,
    VolatilityState,
)


class StateDimension(str, Enum):
    """Which axis (or their combination) a summary row describes."""

    DIRECTION = "direction"
    VOLATILITY = "volatility"
    PARTICIPATION = "participation"
    JOINT = "joint"
    BASELINE = "baseline"
    """Unconditional (no state filter) -- used only by
    `MarketStateForwardReturnSummary` rows, to expose the comparison point
    required by the "Baseline Comparison" requirement."""


class EvidenceStatus(str, Enum):
    """Whether a summary row's sample count meets the caller-configured
    `WalkForwardEvaluationParams.minimum_sample_count`. Rows below the
    threshold are still returned in full -- this flag marks them as
    unreliable, it does not suppress them."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class WalkForwardBar:
    """One OHLCV bar with an explicit evaluation timestamp.

    Chronological ordering (strictly increasing `open_time`) is enforced by
    `walk_forward_evaluator.run_walk_forward_evaluation` -- unlike
    `contracts.OHLCVBar`, which trusts caller-supplied ordering without
    checking it, this module validates ordering explicitly because the
    walk-forward evaluator's entire no-future-leakage guarantee depends on
    it: "at or before T" is meaningless if the bars aren't actually in time
    order.
    """

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def to_ohlcv_bar(self) -> OHLCVBar:
        """Convert to the canonical classifier's own bar type. `open_time`
        is intentionally dropped here -- the classifier itself never
        consumes a timestamp (see `contracts.OHLCVBar`'s own docstring)."""

        return OHLCVBar(open=self.open, high=self.high, low=self.low, close=self.close, volume=self.volume)


@dataclass(frozen=True, slots=True)
class WalkForwardEvaluationParams:
    """Versioned, explicit walk-forward configuration.

    Like `contracts.MarketStateClassifierParams`, every default here is a
    starting point, not a validated conclusion -- any change must be a new,
    explicitly constructed `WalkForwardEvaluationParams`, never a silent
    in-place edit.
    """

    classification_window_bars: int = 20
    evaluation_step_size_bars: int = 1
    forward_horizons_bars: tuple[int, ...] = (1, 4, 16)
    minimum_sample_count: int = 30

    def __post_init__(self) -> None:
        if self.classification_window_bars < 2:
            raise ValueError("WalkForwardEvaluationParams.classification_window_bars must be >= 2.")
        if self.evaluation_step_size_bars < 1:
            raise ValueError("WalkForwardEvaluationParams.evaluation_step_size_bars must be >= 1.")
        if not self.forward_horizons_bars:
            raise ValueError("WalkForwardEvaluationParams.forward_horizons_bars must not be empty.")
        if any(horizon < 1 for horizon in self.forward_horizons_bars):
            raise ValueError("WalkForwardEvaluationParams.forward_horizons_bars entries must all be >= 1.")
        if len(set(self.forward_horizons_bars)) != len(self.forward_horizons_bars):
            raise ValueError("WalkForwardEvaluationParams.forward_horizons_bars must not contain duplicates.")
        if self.minimum_sample_count < 1:
            raise ValueError("WalkForwardEvaluationParams.minimum_sample_count must be >= 1.")


@dataclass(frozen=True, slots=True)
class WalkForwardStateObservation:
    """One walk-forward evaluation point: the classified state at a single
    bar, plus the (possibly partially unavailable) forward returns measured
    after that state was frozen.

    `evaluation_index` is the index, within the caller's original `bars`
    sequence, of the last bar included in the classification window -- the
    "current" bar at evaluation time. `forward_returns_by_horizon` always
    contains one entry per configured horizon; a value of `None` means that
    horizon's future bar was not available in the supplied data (never
    fabricated), not that the horizon wasn't requested.
    """

    evaluation_index: int
    evaluation_time: datetime
    direction_state: DirectionState
    volatility_state: VolatilityState
    participation_state: ParticipationState
    confidence: Decimal
    reason_codes: tuple[str, ...]
    forward_returns_by_horizon: Mapping[int, Decimal | None]

    def __init__(
        self,
        *,
        evaluation_index: int,
        evaluation_time: datetime,
        direction_state: DirectionState,
        volatility_state: VolatilityState,
        participation_state: ParticipationState,
        confidence: Decimal,
        reason_codes: tuple[str, ...],
        forward_returns_by_horizon: Mapping[int, Decimal | None],
    ) -> None:
        object.__setattr__(self, "evaluation_index", evaluation_index)
        object.__setattr__(self, "evaluation_time", evaluation_time)
        object.__setattr__(self, "direction_state", direction_state)
        object.__setattr__(self, "volatility_state", volatility_state)
        object.__setattr__(self, "participation_state", participation_state)
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "reason_codes", tuple(reason_codes))
        object.__setattr__(self, "forward_returns_by_horizon", MappingProxyType(dict(forward_returns_by_horizon)))

    @property
    def joint_state_value(self) -> str:
        """Stable, explicit joint-state key: `direction|volatility|participation`."""

        return f"{self.direction_state.value}|{self.volatility_state.value}|{self.participation_state.value}"


@dataclass(frozen=True, slots=True)
class MarketStateFrequency:
    """How often one state value occurred, within one dimension, across all
    walk-forward observations."""

    state_dimension: StateDimension
    state_value: str
    observation_count: int
    observation_percentage: Decimal
    evidence_status: EvidenceStatus


@dataclass(frozen=True, slots=True)
class MarketStateTransition:
    """One `from_state -> to_state` edge, within one dimension, counted
    across consecutive walk-forward observations (never raw candles).
    `conditional_probability` is always accompanied by its own denominator
    (`total_outgoing_transitions_from_state`), never reported alone."""

    state_dimension: StateDimension
    from_state: str
    to_state: str
    transition_count: int
    total_outgoing_transitions_from_state: int
    conditional_probability: Decimal
    evidence_status: EvidenceStatus


@dataclass(frozen=True, slots=True)
class MarketStatePersistence:
    """How often a state, once observed, was still the state at the next
    walk-forward evaluation point. `persistence_probability` is `None` only
    when a state never appeared as an origin of any transition (impossible
    given how this is computed, but represented explicitly rather than
    assumed)."""

    state_dimension: StateDimension
    state_value: str
    self_transition_count: int
    total_outgoing_transitions: int
    persistence_probability: Decimal | None
    evidence_status: EvidenceStatus


@dataclass(frozen=True, slots=True)
class MarketStateForwardReturnSummary:
    """Forward close-to-close percentage return distribution for one
    horizon, either conditioned on a state value (`state_dimension` one of
    direction/volatility/participation/joint) or unconditional
    (`state_dimension=BASELINE`, `state_value="unconditional"`) -- present
    so a caller can honestly compare a conditioned summary against the
    baseline for the same horizon, per the "Baseline Comparison"
    requirement. All fields are `None` when `sample_count == 0`; never a
    fabricated zero."""

    state_dimension: StateDimension
    state_value: str
    horizon_bars: int
    sample_count: int
    mean_return: Decimal | None
    median_return: Decimal | None
    positive_return_percentage: Decimal | None
    minimum_return: Decimal | None
    maximum_return: Decimal | None
    evidence_status: EvidenceStatus


@dataclass(frozen=True, slots=True)
class WalkForwardEvaluationResult:
    """The complete output of one walk-forward evaluation run."""

    observations: tuple[WalkForwardStateObservation, ...]
    frequencies: tuple[MarketStateFrequency, ...]
    transitions: tuple[MarketStateTransition, ...]
    persistence: tuple[MarketStatePersistence, ...]
    forward_return_summaries: tuple[MarketStateForwardReturnSummary, ...]
    classifier_version: str
    evaluator_version: str
    classifier_params: MarketStateClassifierParams
    walk_forward_params: WalkForwardEvaluationParams
    total_bars_supplied: int
