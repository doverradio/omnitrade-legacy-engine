from __future__ import annotations

from decimal import Decimal

from app.services.entry_intelligence.evidence import (
    FALLBACK_STRATEGY_ASSET_ALL_TIMEFRAMES,
    FALLBACK_STRATEGY_ASSET_TIMEFRAME,
    FALLBACK_STRATEGY_ASSET_TIMEFRAME_REGIME,
    FALLBACK_UNAVAILABLE,
    resolve_context_specific_edge_evidence,
)
from app.services.strategy_outcomes.service import StrategyScorecard, StrategyScorecardBucket


def _bucket(
    *,
    horizon_label: str = "15m",
    buy_mean: Decimal | None = None,
    buy_stdev: Decimal | None = None,
    buy_count: int = 0,
) -> StrategyScorecardBucket:
    return StrategyScorecardBucket(
        horizon_label=horizon_label,
        total_evaluated=buy_count,
        buy_evaluations=buy_count,
        buy_correct=0,
        sell_evaluations=0,
        sell_correct=0,
        hold_evaluations=0,
        hold_correct=0,
        overall_correct_pct=None,
        average_raw_return_pct=None,
        average_fee_adjusted_return_pct=None,
        average_mfe_pct=None,
        average_mae_pct=None,
        buy_average_raw_return_pct=buy_mean,
        buy_raw_return_stdev_pct=buy_stdev,
    )


def _scorecard(
    *,
    aggregate: StrategyScorecardBucket,
    per_horizon: list[StrategyScorecardBucket] | None = None,
    regime_conditioned_buckets: dict[str, dict[str, StrategyScorecardBucket]] | None = None,
) -> StrategyScorecard:
    return StrategyScorecard(
        strategy_slug="momentum",
        strategy_identity="momentum@1.0.0",
        per_horizon=per_horizon or [],
        aggregate=aggregate,
        best_regime=None,
        worst_regime=None,
        regime_evidence_count=0,
        regime_min_evidence_required=50,
        regime_conditioned_buckets=regime_conditioned_buckets or {},
    )


def test_scorecard_none_is_unavailable() -> None:
    evidence = resolve_context_specific_edge_evidence(
        scorecard=None,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend="TRENDING",
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.available is False
    assert evidence.fallback_path == FALLBACK_UNAVAILABLE
    assert "scorecard_unavailable" in evidence.missing_input_flags


def test_tier1_regime_conditioned_bucket_used_when_sample_sufficient() -> None:
    scorecard = _scorecard(
        aggregate=_bucket(buy_mean=Decimal("-0.05"), buy_count=200),
        per_horizon=[_bucket(buy_mean=Decimal("0.10"), buy_count=50)],
        regime_conditioned_buckets={
            "15m": {"TRENDING": _bucket(buy_mean=Decimal("0.30"), buy_stdev=Decimal("0.20"), buy_count=25)}
        },
    )
    evidence = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend="TRENDING",
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.available is True
    assert evidence.fallback_path == FALLBACK_STRATEGY_ASSET_TIMEFRAME_REGIME
    assert evidence.mean_raw_return_pct == Decimal("0.30")
    assert evidence.sample_size == 25
    # conservative_gross_edge = mean - z * (stdev / sqrt(n))
    expected_se = Decimal("0.20") / Decimal(25).sqrt()
    assert evidence.standard_error_pct == expected_se
    assert evidence.conservative_gross_edge_pct == Decimal("0.30") - expected_se


def test_tier1_skipped_when_regime_sample_too_small_falls_back_to_tier2() -> None:
    scorecard = _scorecard(
        aggregate=_bucket(buy_mean=Decimal("-0.05"), buy_count=200),
        per_horizon=[_bucket(buy_mean=Decimal("0.10"), buy_stdev=Decimal("0.05"), buy_count=15)],
        regime_conditioned_buckets={
            "15m": {"TRENDING": _bucket(buy_mean=Decimal("0.30"), buy_count=5)}  # below min_horizon_regime_sample_size
        },
    )
    evidence = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend="TRENDING",
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.fallback_path == FALLBACK_STRATEGY_ASSET_TIMEFRAME
    assert evidence.mean_raw_return_pct == Decimal("0.10")
    assert evidence.sample_size == 15


def test_tier2_skipped_when_horizon_sample_too_small_falls_back_to_tier3_aggregate() -> None:
    scorecard = _scorecard(
        aggregate=_bucket(buy_mean=Decimal("-0.05"), buy_stdev=Decimal("0.40"), buy_count=200),
        per_horizon=[_bucket(buy_mean=Decimal("0.10"), buy_count=3)],  # below min_horizon_sample_size
        regime_conditioned_buckets={},
    )
    evidence = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend=None,
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.fallback_path == FALLBACK_STRATEGY_ASSET_ALL_TIMEFRAMES
    assert evidence.mean_raw_return_pct == Decimal("-0.05")
    assert evidence.sample_size == 200
    # This is exactly today's existing historical_gross_return_pct source --
    # the fallback tier must never be more restrictive than current
    # production behavior.


def test_no_regime_trend_skips_tier1_entirely() -> None:
    scorecard = _scorecard(
        aggregate=_bucket(buy_mean=Decimal("-0.05"), buy_count=200),
        per_horizon=[_bucket(buy_mean=Decimal("0.10"), buy_count=15)],
        regime_conditioned_buckets={
            "15m": {"TRENDING": _bucket(buy_mean=Decimal("0.30"), buy_count=25)}
        },
    )
    evidence = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend=None,
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.fallback_path == FALLBACK_STRATEGY_ASSET_TIMEFRAME


def test_all_tiers_unavailable_fails_closed() -> None:
    scorecard = _scorecard(aggregate=_bucket(buy_mean=None, buy_count=0))
    evidence = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend="TRENDING",
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.available is False
    assert evidence.fallback_path == FALLBACK_UNAVAILABLE
    assert "no_action_scoped_evidence_at_any_tier" in evidence.missing_input_flags


def test_missing_variance_flags_and_zero_penalty_never_certainty() -> None:
    # A single aggregate sample: mean exists, stdev is undefined (n=1).
    # Missing variance must not be silently treated as zero-uncertainty
    # confidence -- it must be flagged, even though the penalty itself is 0.
    scorecard = _scorecard(aggregate=_bucket(buy_mean=Decimal("0.50"), buy_stdev=None, buy_count=1))
    evidence = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend=None,
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("1.0"),
    )
    assert evidence.available is True
    assert evidence.uncertainty_penalty_pct == Decimal("0")
    assert evidence.conservative_gross_edge_pct == Decimal("0.50")
    assert "variance_unavailable" in evidence.missing_input_flags


def test_uncertainty_penalty_z_scales_conservatism() -> None:
    scorecard = _scorecard(
        aggregate=_bucket(buy_mean=Decimal("0.20"), buy_stdev=Decimal("0.50"), buy_count=100),
    )
    low_z = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend=None,
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("0.5"),
    )
    high_z = resolve_context_specific_edge_evidence(
        scorecard=scorecard,
        final_action="BUY",
        candle_interval="15m",
        current_regime_trend=None,
        min_horizon_regime_sample_size=20,
        min_horizon_sample_size=10,
        uncertainty_penalty_z=Decimal("2.0"),
    )
    assert high_z.uncertainty_penalty_pct > low_z.uncertainty_penalty_pct
    assert high_z.conservative_gross_edge_pct < low_z.conservative_gross_edge_pct
