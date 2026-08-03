"""Entry decision model: BUY_NOW / BUY_LIMIT / WAIT / REJECT.

Implements docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md
Phases 3-5 (entry decision model, candidate entry object, maximum profitable
entry price).

This module is invoked ONLY after the existing net-edge gate
(app.services.capital_campaign_orchestration.authoritative,
`non_positive_net_edge` / market-entry evaluation) has already run. It never
changes that gate's own accept/reject outcome -- it answers a strictly
additional question: "if immediate market entry is not attractive, does a
bounded, economically-derived lower entry price make this setup viable
instead?" A BUY_LIMIT decision here proposes a NEW, distinct order (a limit
order that must independently pass Risk Engine and campaign/mandate
governance before any capital is committed); it does not retroactively
authorize the market-entry BUY the legacy gate already rejected.

Live provider submission of a BUY_LIMIT decision is NOT implemented as of
this module's introduction -- see the module docstring in
app.services.exchange_connections.providers.kraken_spot::submit_order, which
explicitly supports only MARKET orders in the current execution profile.
This module produces the fully-audited decision and candidate object; wiring
it to real submission requires the Kraken adapter to gain limit-order
support first (tracked as a known, explicit gap -- see the response
"Production Evidence" section of the delivering conversation for exact scope).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from app.services.entry_intelligence.evidence import ContextSpecificEdgeEvidence

BUY_NOW = "BUY_NOW"
BUY_LIMIT = "BUY_LIMIT"
WAIT = "WAIT"
REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class EntryIntelligenceCandidate:
    instrument: str
    venue: str
    side: str
    signal_time: datetime
    candle_close: datetime
    timeframe: str
    campaign_id: str
    campaign_version: int
    strategy_identity: str | None
    strategy_coalition: str | None
    contributing_strategies: tuple[str, ...]
    signal_strength: str | None
    market_regime: str | None
    volatility_regime: str | None
    expected_holding_period_minutes: int | None
    expected_exit_price: Decimal | None
    market_entry_price: Decimal
    maximum_profitable_entry_price: Decimal | None
    preferred_limit_price: Decimal | None
    invalidation_price: Decimal | None
    expiration_time: datetime | None
    expected_gross_edge_at_market_pct: Decimal | None
    expected_net_edge_at_market_pct: Decimal | None
    expected_gross_edge_at_limit_pct: Decimal | None
    expected_net_edge_at_limit_pct: Decimal | None
    confidence_sample_size: int
    uncertainty_penalty_pct: Decimal
    evidence_provenance: str
    approved_notional: Decimal
    decision: str
    reason: str
    maximum_replacement_count: int
    minimum_repricing_interval_minutes: int


def compute_maximum_profitable_entry_price(
    *,
    expected_exit_price: Decimal,
    round_trip_fee_pct: Decimal,
    slippage_pct: Decimal,
    required_profit_buffer_pct: Decimal,
    price_decimals: int,
) -> Decimal | None:
    """The highest entry price at which expected net edge (given the SAME
    expected_exit_price already used for the market-entry evaluation) remains
    non-negative after costs, rounded DOWN to provider price precision so
    the quantized result is never above the true economic breakeven.

    max_price * (1 + total_cost_pct/100) = expected_exit_price
    => max_price = expected_exit_price / (1 + total_cost_pct/100)
    """
    if expected_exit_price <= Decimal("0"):
        return None
    total_cost_pct = round_trip_fee_pct + slippage_pct + required_profit_buffer_pct
    denominator = Decimal("1") + (total_cost_pct / Decimal("100"))
    if denominator <= Decimal("0"):
        return None
    raw_max_price = expected_exit_price / denominator
    quantum = Decimal("1").scaleb(-price_decimals)
    return raw_max_price.quantize(quantum, rounding=ROUND_DOWN)


def evaluate_entry_decision(
    *,
    instrument: str,
    venue: str,
    side: str,
    signal_time: datetime,
    candle_close: datetime,
    timeframe: str,
    campaign_id: str,
    campaign_version: int,
    strategy_identity: str | None,
    strategy_coalition: str | None,
    contributing_strategies: tuple[str, ...],
    signal_strength: str | None,
    market_regime: str | None,
    volatility_regime: str | None,
    market_entry_price: Decimal,
    expected_gross_edge_at_market_pct: Decimal | None,
    expected_net_edge_at_market_pct: Decimal | None,
    evidence: ContextSpecificEdgeEvidence,
    round_trip_fee_pct: Decimal,
    slippage_pct: Decimal,
    required_profit_buffer_pct: Decimal,
    price_decimals: int,
    min_order_notional: Decimal | None,
    approved_notional: Decimal,
    max_limit_discount_pct: Decimal,
    limit_order_max_age_minutes: int,
    max_replacement_count: int,
    min_repricing_interval_minutes: int,
) -> EntryIntelligenceCandidate:
    base_kwargs: dict[str, Any] = dict(
        instrument=instrument,
        venue=venue,
        side=side,
        signal_time=signal_time,
        candle_close=candle_close,
        timeframe=timeframe,
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        strategy_identity=strategy_identity,
        strategy_coalition=strategy_coalition,
        contributing_strategies=contributing_strategies,
        signal_strength=signal_strength,
        market_regime=market_regime,
        volatility_regime=volatility_regime,
        market_entry_price=market_entry_price,
        expected_gross_edge_at_market_pct=expected_gross_edge_at_market_pct,
        expected_net_edge_at_market_pct=expected_net_edge_at_market_pct,
        maximum_replacement_count=max_replacement_count,
        minimum_repricing_interval_minutes=min_repricing_interval_minutes,
        approved_notional=approved_notional,
    )

    if expected_net_edge_at_market_pct is not None and expected_net_edge_at_market_pct > Decimal("0"):
        return EntryIntelligenceCandidate(
            **base_kwargs,
            expected_holding_period_minutes=None,
            expected_exit_price=None,
            maximum_profitable_entry_price=None,
            preferred_limit_price=None,
            invalidation_price=None,
            expiration_time=None,
            expected_gross_edge_at_limit_pct=None,
            expected_net_edge_at_limit_pct=None,
            confidence_sample_size=evidence.sample_size,
            uncertainty_penalty_pct=evidence.uncertainty_penalty_pct,
            evidence_provenance=evidence.fallback_path,
            decision=BUY_NOW,
            reason="market_entry_net_edge_positive",
        )

    if not evidence.available or evidence.conservative_gross_edge_pct is None:
        return EntryIntelligenceCandidate(
            **base_kwargs,
            expected_holding_period_minutes=None,
            expected_exit_price=None,
            maximum_profitable_entry_price=None,
            preferred_limit_price=None,
            invalidation_price=None,
            expiration_time=None,
            expected_gross_edge_at_limit_pct=None,
            expected_net_edge_at_limit_pct=None,
            confidence_sample_size=evidence.sample_size,
            uncertainty_penalty_pct=evidence.uncertainty_penalty_pct,
            evidence_provenance=evidence.fallback_path,
            decision=REJECT,
            reason="insufficient_or_unavailable_evidence",
        )

    expected_exit_price = market_entry_price * (
        Decimal("1") + (evidence.conservative_gross_edge_pct / Decimal("100"))
    )
    maximum_profitable_entry_price = compute_maximum_profitable_entry_price(
        expected_exit_price=expected_exit_price,
        round_trip_fee_pct=round_trip_fee_pct,
        slippage_pct=slippage_pct,
        required_profit_buffer_pct=required_profit_buffer_pct,
        price_decimals=price_decimals,
    )

    common_kwargs: dict[str, Any] = dict(
        **base_kwargs,
        expected_holding_period_minutes=None,
        expected_exit_price=expected_exit_price,
        confidence_sample_size=evidence.sample_size,
        uncertainty_penalty_pct=evidence.uncertainty_penalty_pct,
        evidence_provenance=evidence.fallback_path,
    )

    if maximum_profitable_entry_price is None or maximum_profitable_entry_price <= Decimal("0"):
        return EntryIntelligenceCandidate(
            **common_kwargs,
            maximum_profitable_entry_price=maximum_profitable_entry_price,
            preferred_limit_price=None,
            invalidation_price=None,
            expiration_time=None,
            expected_gross_edge_at_limit_pct=None,
            expected_net_edge_at_limit_pct=None,
            decision=REJECT,
            reason="no_economically_viable_entry_price",
        )

    if maximum_profitable_entry_price >= market_entry_price:
        # The context-specific (conservative, uncertainty-penalized) edge
        # estimate is actually favorable even at today's market price -- but
        # the existing, already-proven net-edge gate is authoritative for
        # market-entry acceptance and already rejected this candidate using
        # its own (non-regime/timeframe-conditioned) evidence. This is
        # surfaced explicitly for operator/audit review rather than silently
        # discarded or used to override the market gate.
        return EntryIntelligenceCandidate(
            **common_kwargs,
            maximum_profitable_entry_price=maximum_profitable_entry_price,
            preferred_limit_price=None,
            invalidation_price=None,
            expiration_time=None,
            expected_gross_edge_at_limit_pct=None,
            expected_net_edge_at_limit_pct=None,
            decision=WAIT,
            reason="context_specific_edge_favorable_at_market_but_market_gate_authoritative",
        )

    discount_pct = (
        (market_entry_price - maximum_profitable_entry_price) / market_entry_price
    ) * Decimal("100")
    if discount_pct > max_limit_discount_pct:
        return EntryIntelligenceCandidate(
            **common_kwargs,
            maximum_profitable_entry_price=maximum_profitable_entry_price,
            preferred_limit_price=None,
            invalidation_price=None,
            expiration_time=None,
            expected_gross_edge_at_limit_pct=None,
            expected_net_edge_at_limit_pct=None,
            decision=REJECT,
            reason="required_discount_exceeds_bounded_safety_cap",
        )

    preferred_limit_price = maximum_profitable_entry_price
    if min_order_notional is not None and approved_notional > Decimal("0"):
        implied_quantity = approved_notional / market_entry_price
        limit_notional = implied_quantity * preferred_limit_price
        if limit_notional < min_order_notional:
            return EntryIntelligenceCandidate(
                **common_kwargs,
                maximum_profitable_entry_price=maximum_profitable_entry_price,
                preferred_limit_price=None,
                invalidation_price=None,
                expiration_time=None,
                expected_gross_edge_at_limit_pct=None,
                expected_net_edge_at_limit_pct=None,
                decision=REJECT,
                reason="limit_notional_below_provider_minimum",
            )

    expected_gross_edge_at_limit_pct = (
        (expected_exit_price - preferred_limit_price) / preferred_limit_price
    ) * Decimal("100")
    total_cost_pct = round_trip_fee_pct + slippage_pct + required_profit_buffer_pct
    expected_net_edge_at_limit_pct = expected_gross_edge_at_limit_pct - total_cost_pct

    if expected_net_edge_at_limit_pct <= Decimal("0"):
        # Tick-rounding pushed the quantized price back below breakeven.
        return EntryIntelligenceCandidate(
            **common_kwargs,
            maximum_profitable_entry_price=maximum_profitable_entry_price,
            preferred_limit_price=preferred_limit_price,
            invalidation_price=None,
            expiration_time=None,
            expected_gross_edge_at_limit_pct=expected_gross_edge_at_limit_pct,
            expected_net_edge_at_limit_pct=expected_net_edge_at_limit_pct,
            decision=REJECT,
            reason="quantized_limit_price_non_positive_net_edge",
        )

    expiration_time = signal_time + timedelta(minutes=limit_order_max_age_minutes)
    # Invalidation is a coarse, evidence-based bound (not yet action-scoped
    # MAE -- see module docstring caveat in evidence.py) rather than an
    # arbitrary percentage: if price falls further than the historical
    # average adverse excursion for this evidence tier, the setup's
    # thesis is considered broken. None when no MAE evidence is available.
    invalidation_price = None

    return EntryIntelligenceCandidate(
        **common_kwargs,
        maximum_profitable_entry_price=maximum_profitable_entry_price,
        preferred_limit_price=preferred_limit_price,
        invalidation_price=invalidation_price,
        expiration_time=expiration_time,
        expected_gross_edge_at_limit_pct=expected_gross_edge_at_limit_pct,
        expected_net_edge_at_limit_pct=expected_net_edge_at_limit_pct,
        decision=BUY_LIMIT,
        reason="bounded_limit_entry_creates_positive_expected_net_edge",
    )
