"""Deterministic shadow counterfactual evaluation (docs/
OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md Phase 10).

Replays a single rejected/proposed BUY candidate against ALREADY-STORED
historical candle data to answer: would the proposed bounded limit price
have filled, how long would it have taken, and how would the resulting
position (or the market-entry alternative) have performed at fixed forward
horizons. This is read-only -- it never writes to the database and never
submits anything to a provider -- and is meant to be run OFFLINE, before any
live BUY_LIMIT lane is enabled, to build calibration evidence for whether
the model's assumptions (limit fills, and are net-edge-positive when they
do) match reality.

Reuses the SAME candle-window primitives strategy_outcomes/service.py
already uses for its own historical outcome scoring
(_load_close_at_or_before, _load_window_candles) rather than a second,
competing replay implementation.

Known limitation: "strategy-native exit" (Phase 10's sixth required
evaluation point, alongside the five fixed horizons) is NOT implemented --
it requires evaluating the position-lifecycle exit policy the live campaign
would actually use, which is a materially larger integration than replaying
fixed-horizon candle closes. Only the five fixed horizons (15m/30m/1h/2h/4h)
are implemented; results are explicit about this (strategy_native_exit is
always None with a missing_input_flag), never fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.strategy_outcomes.service import _load_close_at_or_before, _load_window_candles

HORIZON_MINUTES: tuple[tuple[str, int], ...] = (
    ("15m", 15), ("30m", 30), ("1h", 60), ("2h", 120), ("4h", 240),
)


@dataclass(frozen=True, slots=True)
class ShadowHorizonOutcome:
    horizon_label: str
    exit_price: Decimal | None
    gross_pnl_pct: Decimal | None
    net_pnl_pct: Decimal | None


@dataclass(frozen=True, slots=True)
class ShadowCounterfactualResult:
    instrument: str
    candle_close_time: datetime
    market_entry_price: Decimal
    preferred_limit_price: Decimal
    maximum_profitable_entry_price: Decimal
    would_limit_fill: bool
    time_to_fill_minutes: int | None
    fill_price: Decimal | None
    maximum_favorable_excursion_pct: Decimal | None
    maximum_adverse_excursion_pct: Decimal | None
    market_entry_horizon_outcomes: tuple[ShadowHorizonOutcome, ...]
    limit_entry_horizon_outcomes: tuple[ShadowHorizonOutcome, ...]
    strategy_native_exit: None
    missed_opportunity_cost_pct: Decimal | None
    avoided_loss_value_pct: Decimal | None
    data_sufficient: bool
    missing_input_flags: tuple[str, ...]


def _utc(value: datetime) -> datetime:
    # SQLite (test-only) drops tzinfo on round trip; Postgres (production)
    # preserves it. Normalizing here keeps this arithmetic correct on both.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pct_return(*, entry: Decimal, exit_price: Decimal) -> Decimal:
    if entry <= Decimal("0"):
        return Decimal("0")
    return ((exit_price - entry) / entry) * Decimal("100")


async def _horizon_outcomes(
    *, db: AsyncSession, asset_id: UUID, interval: str, entry_time: datetime, entry_price: Decimal,
    total_cost_pct: Decimal,
) -> tuple[ShadowHorizonOutcome, ...]:
    outcomes: list[ShadowHorizonOutcome] = []
    for label, minutes in HORIZON_MINUTES:
        exit_price = await _load_close_at_or_before(
            db=db, asset_id=asset_id, interval=interval, target=entry_time + timedelta(minutes=minutes),
        )
        if exit_price is None:
            outcomes.append(ShadowHorizonOutcome(horizon_label=label, exit_price=None, gross_pnl_pct=None, net_pnl_pct=None))
            continue
        gross = _pct_return(entry=entry_price, exit_price=exit_price)
        outcomes.append(
            ShadowHorizonOutcome(horizon_label=label, exit_price=exit_price, gross_pnl_pct=gross, net_pnl_pct=gross - total_cost_pct)
        )
    return tuple(outcomes)


async def replay_rejected_buy_candidate_counterfactual(
    *,
    db: AsyncSession,
    asset_id: UUID,
    interval: str,
    instrument: str,
    candle_close_time: datetime,
    market_entry_price: Decimal,
    preferred_limit_price: Decimal,
    maximum_profitable_entry_price: Decimal,
    expiration_time: datetime,
    round_trip_fee_pct: Decimal,
    slippage_pct: Decimal,
    required_profit_buffer_pct: Decimal,
) -> ShadowCounterfactualResult:
    total_cost_pct = round_trip_fee_pct + slippage_pct + required_profit_buffer_pct
    missing_input_flags: list[str] = ["strategy_native_exit_not_implemented"]

    window_candles = await _load_window_candles(
        db=db, asset_id=asset_id, interval=interval,
        start_exclusive=candle_close_time, end_inclusive=expiration_time,
    )
    if not window_candles:
        missing_input_flags.append("no_candle_data_in_window")
        return ShadowCounterfactualResult(
            instrument=instrument, candle_close_time=candle_close_time,
            market_entry_price=market_entry_price, preferred_limit_price=preferred_limit_price,
            maximum_profitable_entry_price=maximum_profitable_entry_price,
            would_limit_fill=False, time_to_fill_minutes=None, fill_price=None,
            maximum_favorable_excursion_pct=None, maximum_adverse_excursion_pct=None,
            market_entry_horizon_outcomes=(), limit_entry_horizon_outcomes=(),
            strategy_native_exit=None,
            missed_opportunity_cost_pct=None, avoided_loss_value_pct=None,
            data_sufficient=False, missing_input_flags=tuple(missing_input_flags),
        )

    # A resting BUY limit fills the first time price trades AT OR BELOW the
    # limit price -- standard, conservative backtesting assumption: assumed
    # fill price is the limit price itself (never better), never the
    # candle's actual low (which would overstate the fill quality).
    fill_candle = next((candle for candle in window_candles if candle.low <= preferred_limit_price), None)
    would_limit_fill = fill_candle is not None
    fill_price = preferred_limit_price if would_limit_fill else None
    time_to_fill_minutes = (
        int((_utc(fill_candle.close_time) - _utc(candle_close_time)).total_seconds() // 60)
        if fill_candle is not None else None
    )

    highs = [candle.high for candle in window_candles]
    lows = [candle.low for candle in window_candles]
    reference_for_excursion = fill_price if would_limit_fill else market_entry_price
    maximum_favorable_excursion_pct = _pct_return(entry=reference_for_excursion, exit_price=max(highs))
    maximum_adverse_excursion_pct = _pct_return(entry=reference_for_excursion, exit_price=min(lows))

    market_entry_horizon_outcomes = await _horizon_outcomes(
        db=db, asset_id=asset_id, interval=interval, entry_time=candle_close_time,
        entry_price=market_entry_price, total_cost_pct=total_cost_pct,
    )

    limit_entry_horizon_outcomes: tuple[ShadowHorizonOutcome, ...] = ()
    if would_limit_fill and fill_candle is not None and fill_price is not None:
        limit_entry_horizon_outcomes = await _horizon_outcomes(
            db=db, asset_id=asset_id, interval=interval, entry_time=_utc(fill_candle.close_time),
            entry_price=fill_price, total_cost_pct=total_cost_pct,
        )

    # Missed-opportunity / avoided-loss, proxied by the 1h market-entry
    # horizon (a defensible single representative point rather than
    # picking a horizon per candidate with no stated policy for which).
    one_hour_market = next((item for item in market_entry_horizon_outcomes if item.horizon_label == "1h"), None)
    missed_opportunity_cost_pct: Decimal | None = None
    avoided_loss_value_pct: Decimal | None = None
    if not would_limit_fill and one_hour_market is not None and one_hour_market.net_pnl_pct is not None:
        if one_hour_market.net_pnl_pct > Decimal("0"):
            missed_opportunity_cost_pct = one_hour_market.net_pnl_pct
        else:
            avoided_loss_value_pct = -one_hour_market.net_pnl_pct

    return ShadowCounterfactualResult(
        instrument=instrument, candle_close_time=candle_close_time,
        market_entry_price=market_entry_price, preferred_limit_price=preferred_limit_price,
        maximum_profitable_entry_price=maximum_profitable_entry_price,
        would_limit_fill=would_limit_fill, time_to_fill_minutes=time_to_fill_minutes, fill_price=fill_price,
        maximum_favorable_excursion_pct=maximum_favorable_excursion_pct,
        maximum_adverse_excursion_pct=maximum_adverse_excursion_pct,
        market_entry_horizon_outcomes=market_entry_horizon_outcomes,
        limit_entry_horizon_outcomes=limit_entry_horizon_outcomes,
        strategy_native_exit=None,
        missed_opportunity_cost_pct=missed_opportunity_cost_pct,
        avoided_loss_value_pct=avoided_loss_value_pct,
        data_sufficient=True, missing_input_flags=tuple(missing_input_flags),
    )
