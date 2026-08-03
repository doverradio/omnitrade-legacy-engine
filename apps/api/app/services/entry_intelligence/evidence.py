"""Context-specific expected-edge evidence resolution.

This module implements the evidence hierarchy required by
docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md Phase 2. It is
deliberately a pure, side-effect-free reader over an already-fetched
StrategyScorecard (app.services.strategy_outcomes.service) -- it performs no
I/O and makes no economic accept/reject decision itself (see
app.services.entry_intelligence.decision for that).

It is consumed ONLY by the new entry-intelligence decision layer, which only
ever runs AFTER the existing net-edge gate
(app.services.capital_campaign_orchestration.authoritative) has already
evaluated and rejected a market-entry BUY. It does not feed, and must never
feed, that existing gate's own accept/reject boundary -- doing so would
retroactively change a live, already-proven-correct gate's outcome, which is
explicitly out of scope for this feature (see the module docstring rationale
in authoritative.py's _NET_EDGE_* constants for why that boundary has already
been the subject of multiple prior, carefully-scoped corrections).

Evidence hierarchy (narrowest/most-specific first), each tier gated by a
minimum action-scoped sample size so a thin sample is never silently trusted:

    1. exact strategy + asset + timeframe (matching the campaign's own
       candle interval) + regime_trend  -- StrategyScorecard.regime_conditioned_buckets
    2. exact strategy + asset + timeframe (matching candle interval), any
       regime -- StrategyScorecard.per_horizon
    3. exact strategy + asset, all timeframes blended -- StrategyScorecard.aggregate
       (this is the SAME figure already used today as historical_gross_return_pct;
       reusing it here as the final fallback guarantees this hierarchy can never
       be less permissive than today's production behavior)
    4. fail closed ("unavailable") if no tier has a usable action-scoped mean

Missing or thin evidence never becomes a negative edge value: an
unavailable/insufficient tier is skipped in favor of the next broader one,
and if every tier is unusable the result is explicitly `available=False` with
`missing_input_flags` naming why, for the decision layer to fail closed on.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

FALLBACK_STRATEGY_ASSET_TIMEFRAME_REGIME = "strategy_asset_timeframe_regime"
FALLBACK_STRATEGY_ASSET_TIMEFRAME = "strategy_asset_timeframe"
FALLBACK_STRATEGY_ASSET_ALL_TIMEFRAMES = "strategy_asset_all_timeframes"
FALLBACK_UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ContextSpecificEdgeEvidence:
    available: bool
    fallback_path: str
    source_strategy_slug: str | None
    source_horizon_label: str | None
    source_regime: str | None
    mean_raw_return_pct: Decimal | None
    sample_size: int
    stdev_pct: Decimal | None
    standard_error_pct: Decimal | None
    uncertainty_penalty_pct: Decimal
    conservative_gross_edge_pct: Decimal | None
    confidence_lower_bound_pct: Decimal | None
    confidence_upper_bound_pct: Decimal | None
    missing_input_flags: tuple[str, ...]


def _action_fields(bucket: Any, final_action: str) -> tuple[Decimal | None, Decimal | None, int]:
    """Returns (mean_raw_return_pct, stdev_pct, sample_size) for the given
    action from a StrategyScorecardBucket-shaped object. Unknown/HOLD-only
    actions this hierarchy doesn't support return (None, None, 0)."""
    if final_action == "BUY":
        return bucket.buy_average_raw_return_pct, bucket.buy_raw_return_stdev_pct, bucket.buy_evaluations
    if final_action == "SELL":
        return bucket.sell_average_raw_return_pct, bucket.sell_raw_return_stdev_pct, bucket.sell_evaluations
    if final_action == "HOLD":
        return bucket.hold_average_raw_return_pct, bucket.hold_raw_return_stdev_pct, bucket.hold_evaluations
    return None, None, 0


def _unavailable(*, flags: tuple[str, ...]) -> ContextSpecificEdgeEvidence:
    return ContextSpecificEdgeEvidence(
        available=False,
        fallback_path=FALLBACK_UNAVAILABLE,
        source_strategy_slug=None,
        source_horizon_label=None,
        source_regime=None,
        mean_raw_return_pct=None,
        sample_size=0,
        stdev_pct=None,
        standard_error_pct=None,
        uncertainty_penalty_pct=Decimal("0"),
        conservative_gross_edge_pct=None,
        confidence_lower_bound_pct=None,
        confidence_upper_bound_pct=None,
        missing_input_flags=flags,
    )


def _with_uncertainty(
    *,
    fallback_path: str,
    strategy_slug: str | None,
    horizon_label: str | None,
    regime: str | None,
    mean: Decimal,
    stdev: Decimal | None,
    sample_size: int,
    uncertainty_penalty_z: Decimal,
    missing_input_flags: tuple[str, ...],
) -> ContextSpecificEdgeEvidence:
    standard_error: Decimal | None = None
    if stdev is not None and sample_size > 0:
        standard_error = stdev / Decimal(sample_size).sqrt()
    penalty = (uncertainty_penalty_z * standard_error) if standard_error is not None else Decimal("0")
    flags = missing_input_flags
    if standard_error is None:
        flags = flags + ("variance_unavailable",)
    return ContextSpecificEdgeEvidence(
        available=True,
        fallback_path=fallback_path,
        source_strategy_slug=strategy_slug,
        source_horizon_label=horizon_label,
        source_regime=regime,
        mean_raw_return_pct=mean,
        sample_size=sample_size,
        stdev_pct=stdev,
        standard_error_pct=standard_error,
        uncertainty_penalty_pct=penalty,
        conservative_gross_edge_pct=mean - penalty,
        confidence_lower_bound_pct=mean - penalty,
        confidence_upper_bound_pct=mean + penalty,
        missing_input_flags=flags,
    )


def resolve_context_specific_edge_evidence(
    *,
    scorecard: Any | None,
    final_action: str,
    candle_interval: str,
    current_regime_trend: str | None,
    min_horizon_regime_sample_size: int,
    min_horizon_sample_size: int,
    uncertainty_penalty_z: Decimal,
) -> ContextSpecificEdgeEvidence:
    if scorecard is None:
        return _unavailable(flags=("scorecard_unavailable",))

    # Tier 1: exact strategy + asset + timeframe + regime.
    if current_regime_trend is not None:
        regime_bucket = (scorecard.regime_conditioned_buckets or {}).get(candle_interval, {}).get(
            current_regime_trend
        )
        if regime_bucket is not None:
            mean, stdev, count = _action_fields(regime_bucket, final_action)
            if mean is not None and count >= min_horizon_regime_sample_size:
                return _with_uncertainty(
                    fallback_path=FALLBACK_STRATEGY_ASSET_TIMEFRAME_REGIME,
                    strategy_slug=scorecard.strategy_slug,
                    horizon_label=candle_interval,
                    regime=current_regime_trend,
                    mean=mean,
                    stdev=stdev,
                    sample_size=count,
                    uncertainty_penalty_z=uncertainty_penalty_z,
                    missing_input_flags=(),
                )

    # Tier 2: exact strategy + asset + timeframe, any regime.
    horizon_bucket = next(
        (bucket for bucket in scorecard.per_horizon if bucket.horizon_label == candle_interval),
        None,
    )
    if horizon_bucket is not None:
        mean, stdev, count = _action_fields(horizon_bucket, final_action)
        if mean is not None and count >= min_horizon_sample_size:
            return _with_uncertainty(
                fallback_path=FALLBACK_STRATEGY_ASSET_TIMEFRAME,
                strategy_slug=scorecard.strategy_slug,
                horizon_label=candle_interval,
                regime=None,
                mean=mean,
                stdev=stdev,
                sample_size=count,
                uncertainty_penalty_z=uncertainty_penalty_z,
                missing_input_flags=(),
            )

    # Tier 3: exact strategy + asset, all timeframes blended (today's
    # existing historical_gross_return_pct source -- guarantees this
    # hierarchy is never more restrictive than current production behavior).
    mean, stdev, count = _action_fields(scorecard.aggregate, final_action)
    if mean is not None and count > 0:
        return _with_uncertainty(
            fallback_path=FALLBACK_STRATEGY_ASSET_ALL_TIMEFRAMES,
            strategy_slug=scorecard.strategy_slug,
            horizon_label="aggregate",
            regime=None,
            mean=mean,
            stdev=stdev,
            sample_size=count,
            uncertainty_penalty_z=uncertainty_penalty_z,
            missing_input_flags=(),
        )

    return _unavailable(flags=("no_action_scoped_evidence_at_any_tier",))
