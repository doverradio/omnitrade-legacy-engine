"""Deterministic walk-forward Market State evaluation harness.

**Research / replay layer only.** See `docs/MARKET_STATE_WALK_FORWARD_EVALUATION.md`.
This module must never be imported from a live strategy, risk,
mandate/campaign, or order-execution path.

`run_walk_forward_evaluation` answers, over a supplied chronological OHLCV
history: how frequently each market state occurs, how persistent each state
is, how states transition into one another, what forward return
distributions follow each state (compared against an unconditional
baseline), and -- by exposing both single-axis and joint-axis summaries
side by side -- whether combining direction, volatility, and participation
carries more information than direction alone. It does not itself compute
or assert an answer to that last question; it exposes the data needed to
answer it honestly, per this module's explicit instruction not to claim
predictive value from a positive average return alone.

**No future leakage, by construction.** At each evaluation point T (a bar
index), the classification window passed to the canonical
`deterministic_classifier.classify_market_state` contains only bars at or
before T -- there is no code path in this function through which a bar
after T could reach the classifier call. Forward returns are computed
*after* the state has already been classified and frozen into a
`WalkForwardStateObservation`, strictly as a separate, later step, exactly
mirroring the walk-forward process this module's governing prompt
specifies. See `docs/MARKET_STATE_WALK_FORWARD_EVALUATION.md` for the
paired-timeline test pattern that verifies this.

This module calls the existing canonical `classify_market_state` for every
evaluation point. It does not reimplement, approximate, or duplicate any
part of the classifier's own direction/volatility/participation math.
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from .contracts import MarketStateClassifierParams
from .deterministic_classifier import CLASSIFIER_VERSION, classify_market_state
from .deterministic_classifier import DEFAULT_PARAMS as CLASSIFIER_DEFAULT_PARAMS
from .walk_forward_contracts import (
    EvidenceStatus,
    MarketStateForwardReturnSummary,
    MarketStateFrequency,
    MarketStatePersistence,
    MarketStateTransition,
    StateDimension,
    WalkForwardBar,
    WalkForwardEvaluationParams,
    WalkForwardEvaluationResult,
    WalkForwardStateObservation,
)

EVALUATOR_VERSION = "market_state_walk_forward_v1"

DEFAULT_WALK_FORWARD_PARAMS = WalkForwardEvaluationParams()

_RETURN_QUANTIZE = Decimal("0.000001")
_PERCENT_QUANTIZE = Decimal("0.0001")
_PROBABILITY_QUANTIZE = Decimal("0.0001")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def run_walk_forward_evaluation(
    bars: Sequence[WalkForwardBar],
    *,
    classifier_params: MarketStateClassifierParams = CLASSIFIER_DEFAULT_PARAMS,
    walk_forward_params: WalkForwardEvaluationParams = DEFAULT_WALK_FORWARD_PARAMS,
) -> WalkForwardEvaluationResult:
    """Run a deterministic walk-forward Market State evaluation.

    Raises `ValueError` if `bars` is empty, if `bars` is not strictly
    chronologically ordered (`open_time` strictly increasing), or if
    `walk_forward_params.classification_window_bars` is smaller than
    `classifier_params.min_bars` (every window would otherwise fail inside
    the classifier itself, less clearly than failing here upfront).

    If `bars` is non-empty but shorter than the configured classification
    window, returns a result with zero observations and empty summaries --
    "not enough data for even one evaluation point" is a legitimate research
    outcome, not an error.
    """

    if len(bars) == 0:
        raise ValueError("run_walk_forward_evaluation requires at least one bar.")
    if walk_forward_params.classification_window_bars < classifier_params.min_bars:
        raise ValueError(
            "walk_forward_params.classification_window_bars "
            f"({walk_forward_params.classification_window_bars}) must be >= "
            f"classifier_params.min_bars ({classifier_params.min_bars})."
        )
    _validate_chronological_order(bars)

    observations = _build_observations(bars, classifier_params, walk_forward_params)
    frequencies = _compute_frequencies(observations, walk_forward_params.minimum_sample_count)
    transitions, persistence = _compute_transitions_and_persistence(
        observations, walk_forward_params.minimum_sample_count
    )
    forward_return_summaries = _compute_forward_return_summaries(
        observations, walk_forward_params.forward_horizons_bars, walk_forward_params.minimum_sample_count
    )

    return WalkForwardEvaluationResult(
        observations=observations,
        frequencies=frequencies,
        transitions=transitions,
        persistence=persistence,
        forward_return_summaries=forward_return_summaries,
        classifier_version=CLASSIFIER_VERSION,
        evaluator_version=EVALUATOR_VERSION,
        classifier_params=classifier_params,
        walk_forward_params=walk_forward_params,
        total_bars_supplied=len(bars),
    )


def _validate_chronological_order(bars: Sequence[WalkForwardBar]) -> None:
    for idx in range(1, len(bars)):
        if bars[idx].open_time <= bars[idx - 1].open_time:
            raise ValueError(
                "run_walk_forward_evaluation requires strictly increasing open_time; "
                f"bar at index {idx} ({bars[idx].open_time}) does not follow bar at index "
                f"{idx - 1} ({bars[idx - 1].open_time})."
            )


def _build_observations(
    bars: Sequence[WalkForwardBar],
    classifier_params: MarketStateClassifierParams,
    walk_forward_params: WalkForwardEvaluationParams,
) -> tuple[WalkForwardStateObservation, ...]:
    window = walk_forward_params.classification_window_bars
    step = walk_forward_params.evaluation_step_size_bars
    horizons = walk_forward_params.forward_horizons_bars

    if len(bars) < window:
        return ()

    observations: list[WalkForwardStateObservation] = []
    for evaluation_index in range(window - 1, len(bars), step):
        # CORE RULE: this slice contains only bars at or before
        # evaluation_index -- there is no way for a later bar to reach the
        # classifier call below.
        classification_window = tuple(
            bar.to_ohlcv_bar() for bar in bars[evaluation_index - window + 1 : evaluation_index + 1]
        )
        state = classify_market_state(classification_window, params=classifier_params)

        # The state above is now frozen. Only after this point do we look
        # at any bar beyond evaluation_index, and only to score the
        # already-recorded state -- never to influence it.
        forward_returns = _compute_forward_returns(bars, evaluation_index, horizons)

        observations.append(
            WalkForwardStateObservation(
                evaluation_index=evaluation_index,
                evaluation_time=bars[evaluation_index].open_time,
                direction_state=state.direction_state,
                volatility_state=state.volatility_state,
                participation_state=state.participation_state,
                confidence=state.confidence,
                reason_codes=state.reason_codes,
                forward_returns_by_horizon=forward_returns,
            )
        )

    return tuple(observations)


def _compute_forward_returns(
    bars: Sequence[WalkForwardBar],
    evaluation_index: int,
    horizons: tuple[int, ...],
) -> dict[int, Decimal | None]:
    evaluation_close = bars[evaluation_index].close
    results: dict[int, Decimal | None] = {}
    for horizon in horizons:
        future_index = evaluation_index + horizon
        if future_index >= len(bars):
            # Do not fabricate a result: the future data for this horizon
            # does not exist in the supplied bars.
            results[horizon] = None
            continue
        future_close = bars[future_index].close
        ret = ((future_close - evaluation_close) / evaluation_close * _HUNDRED).quantize(
            _RETURN_QUANTIZE, rounding=ROUND_HALF_EVEN
        )
        results[horizon] = ret
    return results


def _evidence_status(count: int, minimum_sample_count: int) -> EvidenceStatus:
    return EvidenceStatus.SUFFICIENT if count >= minimum_sample_count else EvidenceStatus.INSUFFICIENT_EVIDENCE


def _compute_frequencies(
    observations: tuple[WalkForwardStateObservation, ...],
    minimum_sample_count: int,
) -> tuple[MarketStateFrequency, ...]:
    total = len(observations)
    if total == 0:
        return ()

    counters: dict[StateDimension, Counter[str]] = {
        StateDimension.DIRECTION: Counter(),
        StateDimension.VOLATILITY: Counter(),
        StateDimension.PARTICIPATION: Counter(),
        StateDimension.JOINT: Counter(),
    }
    for obs in observations:
        counters[StateDimension.DIRECTION][obs.direction_state.value] += 1
        counters[StateDimension.VOLATILITY][obs.volatility_state.value] += 1
        counters[StateDimension.PARTICIPATION][obs.participation_state.value] += 1
        counters[StateDimension.JOINT][obs.joint_state_value] += 1

    results: list[MarketStateFrequency] = []
    for dimension in (StateDimension.DIRECTION, StateDimension.VOLATILITY, StateDimension.PARTICIPATION, StateDimension.JOINT):
        for state_value, count in sorted(counters[dimension].items()):
            percentage = (Decimal(count) / Decimal(total) * _HUNDRED).quantize(
                _PERCENT_QUANTIZE, rounding=ROUND_HALF_EVEN
            )
            results.append(
                MarketStateFrequency(
                    state_dimension=dimension,
                    state_value=state_value,
                    observation_count=count,
                    observation_percentage=percentage,
                    evidence_status=_evidence_status(count, minimum_sample_count),
                )
            )
    return tuple(results)


def _compute_transitions_and_persistence(
    observations: tuple[WalkForwardStateObservation, ...],
    minimum_sample_count: int,
) -> tuple[tuple[MarketStateTransition, ...], tuple[MarketStatePersistence, ...]]:
    if len(observations) < 2:
        return (), ()

    def _series(dimension: StateDimension) -> list[str]:
        if dimension == StateDimension.DIRECTION:
            return [obs.direction_state.value for obs in observations]
        if dimension == StateDimension.VOLATILITY:
            return [obs.volatility_state.value for obs in observations]
        if dimension == StateDimension.PARTICIPATION:
            return [obs.participation_state.value for obs in observations]
        return [obs.joint_state_value for obs in observations]

    transitions: list[MarketStateTransition] = []
    persistence: list[MarketStatePersistence] = []

    for dimension in (StateDimension.DIRECTION, StateDimension.VOLATILITY, StateDimension.PARTICIPATION, StateDimension.JOINT):
        series = _series(dimension)
        pair_counts: dict[tuple[str, str], int] = {}
        outgoing_totals: dict[str, int] = {}
        # Transitions are counted between consecutive *evaluated
        # observations* (this series), never between raw candles.
        for idx in range(len(series) - 1):
            frm, to = series[idx], series[idx + 1]
            pair_counts[(frm, to)] = pair_counts.get((frm, to), 0) + 1
            outgoing_totals[frm] = outgoing_totals.get(frm, 0) + 1

        for (frm, to), count in sorted(pair_counts.items()):
            total_from = outgoing_totals[frm]
            probability = (Decimal(count) / Decimal(total_from)).quantize(
                _PROBABILITY_QUANTIZE, rounding=ROUND_HALF_EVEN
            )
            transitions.append(
                MarketStateTransition(
                    state_dimension=dimension,
                    from_state=frm,
                    to_state=to,
                    transition_count=count,
                    total_outgoing_transitions_from_state=total_from,
                    conditional_probability=probability,
                    evidence_status=_evidence_status(total_from, minimum_sample_count),
                )
            )

        for state_value, total_from in sorted(outgoing_totals.items()):
            self_count = pair_counts.get((state_value, state_value), 0)
            persistence_probability = (
                (Decimal(self_count) / Decimal(total_from)).quantize(_PROBABILITY_QUANTIZE, rounding=ROUND_HALF_EVEN)
                if total_from > 0
                else None
            )
            persistence.append(
                MarketStatePersistence(
                    state_dimension=dimension,
                    state_value=state_value,
                    self_transition_count=self_count,
                    total_outgoing_transitions=total_from,
                    persistence_probability=persistence_probability,
                    evidence_status=_evidence_status(total_from, minimum_sample_count),
                )
            )

    return tuple(transitions), tuple(persistence)


def _compute_forward_return_summaries(
    observations: tuple[WalkForwardStateObservation, ...],
    horizons: tuple[int, ...],
    minimum_sample_count: int,
) -> tuple[MarketStateForwardReturnSummary, ...]:
    """Deliberate design choice: a baseline row is always emitted for every
    configured horizon, even when `observations` is empty (sample_count=0,
    marked INSUFFICIENT_EVIDENCE) -- per "do not suppress the data," an
    empty/insufficient dataset is itself a fact worth reporting explicitly,
    not a reason to silently omit the row. State-conditioned rows, by
    contrast, naturally only exist for states that actually occurred."""

    results: list[MarketStateForwardReturnSummary] = []

    for horizon in horizons:
        # Baseline (unconditional): every observation with an available
        # return for this horizon, regardless of state.
        baseline_returns = [
            r for r in (obs.forward_returns_by_horizon.get(horizon) for obs in observations) if r is not None
        ]
        results.append(
            _summarize_returns(StateDimension.BASELINE, "unconditional", horizon, baseline_returns, minimum_sample_count)
        )

        grouped: dict[tuple[StateDimension, str], list[Decimal]] = {}
        for obs in observations:
            ret = obs.forward_returns_by_horizon.get(horizon)
            if ret is None:
                continue
            grouped.setdefault((StateDimension.DIRECTION, obs.direction_state.value), []).append(ret)
            grouped.setdefault((StateDimension.VOLATILITY, obs.volatility_state.value), []).append(ret)
            grouped.setdefault((StateDimension.PARTICIPATION, obs.participation_state.value), []).append(ret)
            grouped.setdefault((StateDimension.JOINT, obs.joint_state_value), []).append(ret)

        for (dimension, state_value), returns in sorted(grouped.items(), key=lambda item: (item[0][0].value, item[0][1])):
            results.append(_summarize_returns(dimension, state_value, horizon, returns, minimum_sample_count))

    return tuple(results)


def _summarize_returns(
    dimension: StateDimension,
    state_value: str,
    horizon: int,
    returns: list[Decimal],
    minimum_sample_count: int,
) -> MarketStateForwardReturnSummary:
    sample_count = len(returns)
    if sample_count == 0:
        mean_return = median_return = positive_pct = min_return = max_return = None
    else:
        mean_return = (sum(returns, _ZERO) / Decimal(sample_count)).quantize(
            _RETURN_QUANTIZE, rounding=ROUND_HALF_EVEN
        )
        median_return = statistics.median(returns).quantize(_RETURN_QUANTIZE, rounding=ROUND_HALF_EVEN)
        positive_count = sum(1 for r in returns if r > 0)
        positive_pct = (Decimal(positive_count) / Decimal(sample_count) * _HUNDRED).quantize(
            _PERCENT_QUANTIZE, rounding=ROUND_HALF_EVEN
        )
        min_return = min(returns)
        max_return = max(returns)

    return MarketStateForwardReturnSummary(
        state_dimension=dimension,
        state_value=state_value,
        horizon_bars=horizon,
        sample_count=sample_count,
        mean_return=mean_return,
        median_return=median_return,
        positive_return_percentage=positive_pct,
        minimum_return=min_return,
        maximum_return=max_return,
        evidence_status=_evidence_status(sample_count, minimum_sample_count),
    )
