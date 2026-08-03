from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.entry_intelligence.decision import (
    BUY_LIMIT,
    BUY_NOW,
    REJECT,
    WAIT,
    compute_maximum_profitable_entry_price,
    evaluate_entry_decision,
)
from app.services.entry_intelligence.evidence import ContextSpecificEdgeEvidence


def _evidence(
    *,
    available: bool = True,
    conservative_gross_edge_pct: Decimal | None,
    sample_size: int = 30,
    fallback_path: str = "strategy_asset_timeframe",
) -> ContextSpecificEdgeEvidence:
    return ContextSpecificEdgeEvidence(
        available=available,
        fallback_path=fallback_path,
        source_strategy_slug="momentum",
        source_horizon_label="15m",
        source_regime="TRENDING",
        mean_raw_return_pct=conservative_gross_edge_pct,
        sample_size=sample_size,
        stdev_pct=Decimal("0.10"),
        standard_error_pct=Decimal("0.02"),
        uncertainty_penalty_pct=Decimal("0.02"),
        conservative_gross_edge_pct=conservative_gross_edge_pct,
        confidence_lower_bound_pct=None,
        confidence_upper_bound_pct=None,
        missing_input_flags=(),
    )


def _base_kwargs(**overrides):
    kwargs = dict(
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
        contributing_strategies=("momentum", "breakout"),
        signal_strength="0.97",
        market_regime="TRENDING",
        volatility_regime=None,
        market_entry_price=Decimal("100.00"),
        expected_gross_edge_at_market_pct=Decimal("-0.0948"),
        expected_net_edge_at_market_pct=Decimal("-0.1248"),
        round_trip_fee_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.01"),
        required_profit_buffer_pct=Decimal("0"),
        price_decimals=2,
        min_order_notional=Decimal("1.00"),
        approved_notional=Decimal("5.00"),
        max_limit_discount_pct=Decimal("1.0"),
        limit_order_max_age_minutes=60,
        max_replacement_count=1,
        min_repricing_interval_minutes=15,
    )
    kwargs.update(overrides)
    return kwargs


def test_compute_maximum_profitable_entry_price_basic_math_and_round_down() -> None:
    # exit=101, total cost=0.05% => max_price = 101 / 1.0005 = 100.9495...
    # rounded DOWN to 2 decimals.
    result = compute_maximum_profitable_entry_price(
        expected_exit_price=Decimal("101"),
        round_trip_fee_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.02"),
        required_profit_buffer_pct=Decimal("0.01"),
        price_decimals=2,
    )
    assert result == Decimal("100.94")


def test_compute_maximum_profitable_entry_price_none_for_non_positive_exit() -> None:
    assert compute_maximum_profitable_entry_price(
        expected_exit_price=Decimal("0"),
        round_trip_fee_pct=Decimal("0.02"),
        slippage_pct=Decimal("0.01"),
        required_profit_buffer_pct=Decimal("0"),
        price_decimals=2,
    ) is None


def test_buy_now_when_market_net_edge_positive() -> None:
    candidate = evaluate_entry_decision(
        **_base_kwargs(expected_net_edge_at_market_pct=Decimal("0.05")),
        evidence=_evidence(conservative_gross_edge_pct=Decimal("0.20")),
    )
    assert candidate.decision == BUY_NOW
    assert candidate.reason == "market_entry_net_edge_positive"
    assert candidate.preferred_limit_price is None


def test_reject_when_evidence_unavailable() -> None:
    candidate = evaluate_entry_decision(
        **_base_kwargs(),
        evidence=_evidence(available=False, conservative_gross_edge_pct=None),
    )
    assert candidate.decision == REJECT
    assert candidate.reason == "insufficient_or_unavailable_evidence"


def test_buy_limit_happy_path_bounded_lower_entry_creates_positive_edge() -> None:
    # market entry price 100, conservative gross edge -0.50% (the SAME
    # context-specific evidence the legacy gate already rejected at market)
    # => expected_exit_price = 99.50. Costs = 0.02+0.01+0=0.03%.
    # max_price = 99.50 / 1.0003 = 99.4701... rounded down -> 99.47, a 0.53%
    # discount, inside the 1.0% bounded safety cap -- and, evaluated AT that
    # lower entry price (not at market), net edge is genuinely positive.
    candidate = evaluate_entry_decision(
        **_base_kwargs(),
        evidence=_evidence(conservative_gross_edge_pct=Decimal("-0.5")),
    )
    assert candidate.decision == BUY_LIMIT
    assert candidate.reason == "bounded_limit_entry_creates_positive_expected_net_edge"
    assert candidate.maximum_profitable_entry_price == Decimal("99.47")
    assert candidate.preferred_limit_price == Decimal("99.47")
    assert candidate.expected_net_edge_at_limit_pct is not None
    assert candidate.expected_net_edge_at_limit_pct > Decimal("0")
    assert candidate.expiration_time == candidate.signal_time + timedelta(minutes=60)
    assert candidate.maximum_replacement_count == 1


def test_wait_when_context_edge_favorable_at_or_above_market_price() -> None:
    # A conservative edge so large that even the maximum profitable entry
    # price sits AT or ABOVE today's market price -- i.e. context-specific
    # evidence suggests market entry itself is fine, but the legacy net-edge
    # gate (different, broader evidence) already rejected it. Must not
    # silently override that gate -- WAIT, not BUY_NOW or BUY_LIMIT.
    candidate = evaluate_entry_decision(
        **_base_kwargs(),
        evidence=_evidence(conservative_gross_edge_pct=Decimal("5.0")),
    )
    assert candidate.decision == WAIT
    assert candidate.reason == "context_specific_edge_favorable_at_market_but_market_gate_authoritative"
    assert candidate.preferred_limit_price is None


def test_reject_when_required_discount_exceeds_bounded_safety_cap() -> None:
    # A steeply negative conservative edge requires an entry price far below
    # market to break even -- further than the configured safety bound, so
    # this must REJECT rather than propose an order relying on an
    # extrapolation-risk price move.
    candidate = evaluate_entry_decision(
        **_base_kwargs(max_limit_discount_pct=Decimal("0.1")),
        evidence=_evidence(conservative_gross_edge_pct=Decimal("-0.5")),
    )
    assert candidate.decision == REJECT
    assert candidate.reason == "required_discount_exceeds_bounded_safety_cap"


def test_reject_when_limit_notional_below_provider_minimum() -> None:
    candidate = evaluate_entry_decision(
        **_base_kwargs(min_order_notional=Decimal("100.00")),
        evidence=_evidence(conservative_gross_edge_pct=Decimal("-0.5")),
    )
    assert candidate.decision == REJECT
    assert candidate.reason == "limit_notional_below_provider_minimum"


def test_reject_when_no_economically_viable_entry_price() -> None:
    # Conservative edge so negative the implied expected_exit_price is <= 0.
    candidate = evaluate_entry_decision(
        **_base_kwargs(),
        evidence=_evidence(conservative_gross_edge_pct=Decimal("-150")),
    )
    assert candidate.decision == REJECT
    assert candidate.reason == "no_economically_viable_entry_price"


def test_no_trade_is_ever_forced_decision_always_one_of_four_outcomes() -> None:
    for edge in (Decimal("-150"), Decimal("-0.5"), Decimal("0.30"), Decimal("5.0")):
        candidate = evaluate_entry_decision(
            **_base_kwargs(),
            evidence=_evidence(conservative_gross_edge_pct=edge),
        )
        assert candidate.decision in {BUY_NOW, BUY_LIMIT, WAIT, REJECT}
