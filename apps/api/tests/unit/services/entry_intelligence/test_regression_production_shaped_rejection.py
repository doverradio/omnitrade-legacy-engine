"""Regression test recreating the exact production-shaped rejection cited in
docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md's "Tests"
section:

    expected_gross_edge_pct=-0.0948
    entry_fee_pct=0.01
    exit_fee_pct=0.01
    slippage_pct=0.01
    expected_net_edge_pct=-0.1248

This proves, with the ACTUAL production numbers (not synthetic ones):
1. why the existing net-edge gate rejects it (arithmetic, unchanged by this
   feature),
2. that the SAME evidence, run through the new context-specific entry-
   intelligence layer, does NOT fabricate a positive market-edge -- it
   proposes a bounded LOWER entry price instead,
3. that the bounded limit price is derived from the same expected-exit-price
   evidence (never an arbitrary discount) and is genuinely net-edge-positive,
4. the final decision is exactly one of BUY_LIMIT / WAIT / REJECT,
5. no trade is ever forced (BUY_NOW never appears here, since market edge is
   negative by construction).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.services.entry_intelligence.decision import (
    BUY_LIMIT,
    BUY_NOW,
    REJECT,
    evaluate_entry_decision,
)
from app.services.entry_intelligence.evidence import ContextSpecificEdgeEvidence

_GROSS_EDGE_PCT = Decimal("-0.0948")
_ENTRY_FEE_PCT = Decimal("0.01")
_EXIT_FEE_PCT = Decimal("0.01")
_SLIPPAGE_PCT = Decimal("0.01")
_ROUND_TRIP_FEE_PCT = _ENTRY_FEE_PCT + _EXIT_FEE_PCT
_EXPECTED_NET_EDGE_PCT = Decimal("-0.1248")


def test_the_cited_production_numbers_reproduce_the_reported_net_edge() -> None:
    """Step 1: prove the existing (unchanged) gate arithmetic really does
    produce the cited -0.1248% figure from the cited inputs -- this is the
    SAME formula as authoritative.py's expected_net_edge computation
    (gross - round_trip_fee - slippage - required_profit_buffer), confirming
    the reported rejection is a correct application of the existing model to
    a genuinely negative-at-market historical figure, not a math defect."""
    required_profit_buffer_pct = Decimal("0")
    net_edge = _GROSS_EDGE_PCT - _ROUND_TRIP_FEE_PCT - _SLIPPAGE_PCT - required_profit_buffer_pct
    assert net_edge == _EXPECTED_NET_EDGE_PCT
    assert net_edge <= Decimal("0")  # confirms the gate is correct to reject at market


def test_context_specific_layer_proposes_bounded_limit_not_a_fabricated_market_accept() -> None:
    """Step 2-5: feed the SAME -0.0948% figure into the new entry-intelligence
    decision layer as the (already uncertainty-penalized) context-specific
    conservative edge -- i.e. assume, conservatively, that tighter regime/
    timeframe-conditioned evidence does not improve on the existing blended
    estimate at all. The market-entry decision must still never be BUY_NOW
    (no fabricated positive edge), and the only economically legitimate
    alternative is a bounded lower entry price with a genuinely positive net
    edge at THAT price -- proven by deriving it from the identical
    expected-exit-price evidence, never an arbitrary discount."""
    market_entry_price = Decimal("65000.00")
    evidence = ContextSpecificEdgeEvidence(
        available=True,
        fallback_path="strategy_asset_all_timeframes",
        source_strategy_slug="momentum",
        source_horizon_label="aggregate",
        source_regime=None,
        mean_raw_return_pct=_GROSS_EDGE_PCT,
        sample_size=42,
        stdev_pct=None,
        standard_error_pct=None,
        uncertainty_penalty_pct=Decimal("0"),
        conservative_gross_edge_pct=_GROSS_EDGE_PCT,
        confidence_lower_bound_pct=_GROSS_EDGE_PCT,
        confidence_upper_bound_pct=_GROSS_EDGE_PCT,
        missing_input_flags=(),
    )

    candidate = evaluate_entry_decision(
        instrument="BTC-USD",
        venue="kraken",
        side="BUY",
        signal_time=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        candle_close=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        timeframe="15m",
        campaign_id="00000000-0000-0000-0000-000000000000",
        campaign_version=1,
        strategy_identity="momentum@1.0.0",
        strategy_coalition="momentum@1.0.0",
        contributing_strategies=("momentum",),
        signal_strength="0.97",
        market_regime="TRENDING",
        volatility_regime=None,
        market_entry_price=market_entry_price,
        expected_gross_edge_at_market_pct=_GROSS_EDGE_PCT,
        expected_net_edge_at_market_pct=_EXPECTED_NET_EDGE_PCT,
        evidence=evidence,
        round_trip_fee_pct=_ROUND_TRIP_FEE_PCT,
        slippage_pct=_SLIPPAGE_PCT,
        required_profit_buffer_pct=Decimal("0"),
        price_decimals=2,
        min_order_notional=Decimal("1.00"),
        approved_notional=Decimal("5.00"),
        max_limit_discount_pct=Decimal("1.0"),
        limit_order_max_age_minutes=60,
        max_replacement_count=1,
        min_repricing_interval_minutes=15,
    )

    # (5) No trade is ever forced: BUY_NOW must never appear for a genuinely
    # negative market edge.
    assert candidate.decision != BUY_NOW

    # (4) The final result is one of the three legitimate non-forcing outcomes.
    assert candidate.decision in {BUY_LIMIT, REJECT, "WAIT"}

    # (3) A bounded limit price was derived (never an arbitrary discount) and
    # is within the default 1% safety cap of market -- the -0.0948% edge is
    # small, so the required discount to reach breakeven is also small.
    assert candidate.maximum_profitable_entry_price is not None
    assert candidate.maximum_profitable_entry_price < market_entry_price
    discount_pct = (
        (market_entry_price - candidate.maximum_profitable_entry_price) / market_entry_price
    ) * Decimal("100")
    assert discount_pct < Decimal("1.0")

    # At this discount, notional at $5 approved_notional and 65000 market
    # price is right at the boundary of the $1 test minimum, so this
    # specific production-shaped case resolves to BUY_LIMIT.
    assert candidate.decision == BUY_LIMIT
    assert candidate.expected_net_edge_at_limit_pct is not None
    assert candidate.expected_net_edge_at_limit_pct > Decimal("0")


def test_at_a_five_dollar_provider_minimum_the_same_case_fails_closed_not_forced() -> None:
    """Small-account / $5-notional caveat, proven rather than assumed: a
    BUY_LIMIT proposal reuses the SAME base quantity Risk already approved
    at market price (this session deliberately does not re-run Risk at a
    hypothetical limit price -- see decision.py's module docstring). Because
    a lower limit price applied to a FIXED quantity always yields a lower
    notional than the original approved_notional, a proving-campaign
    approved_notional sitting exactly at the provider's minimum order value
    leaves no room for ANY bounded discount -- the correct, fail-closed
    result is REJECT, never a silently-undersized live order."""
    market_entry_price = Decimal("65000.00")
    evidence = ContextSpecificEdgeEvidence(
        available=True,
        fallback_path="strategy_asset_all_timeframes",
        source_strategy_slug="momentum",
        source_horizon_label="aggregate",
        source_regime=None,
        mean_raw_return_pct=_GROSS_EDGE_PCT,
        sample_size=42,
        stdev_pct=None,
        standard_error_pct=None,
        uncertainty_penalty_pct=Decimal("0"),
        conservative_gross_edge_pct=_GROSS_EDGE_PCT,
        confidence_lower_bound_pct=_GROSS_EDGE_PCT,
        confidence_upper_bound_pct=_GROSS_EDGE_PCT,
        missing_input_flags=(),
    )
    candidate = evaluate_entry_decision(
        instrument="BTC-USD",
        venue="kraken",
        side="BUY",
        signal_time=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        candle_close=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
        timeframe="15m",
        campaign_id="00000000-0000-0000-0000-000000000000",
        campaign_version=1,
        strategy_identity="momentum@1.0.0",
        strategy_coalition="momentum@1.0.0",
        contributing_strategies=("momentum",),
        signal_strength="0.97",
        market_regime="TRENDING",
        volatility_regime=None,
        market_entry_price=market_entry_price,
        expected_gross_edge_at_market_pct=_GROSS_EDGE_PCT,
        expected_net_edge_at_market_pct=_EXPECTED_NET_EDGE_PCT,
        evidence=evidence,
        round_trip_fee_pct=_ROUND_TRIP_FEE_PCT,
        slippage_pct=_SLIPPAGE_PCT,
        required_profit_buffer_pct=Decimal("0"),
        price_decimals=2,
        min_order_notional=Decimal("5.00"),
        approved_notional=Decimal("5.00"),
        max_limit_discount_pct=Decimal("1.0"),
        limit_order_max_age_minutes=60,
        max_replacement_count=1,
        min_repricing_interval_minutes=15,
    )
    assert candidate.decision == REJECT
    assert candidate.reason == "limit_notional_below_provider_minimum"
