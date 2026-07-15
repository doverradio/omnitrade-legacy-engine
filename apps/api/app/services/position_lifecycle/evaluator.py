from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app.services.position_lifecycle.contracts import PositionLifecycleEvaluation, PositionLifecyclePolicy, PositionSnapshot
from app.services.profitability.engine import (
    RECOMMENDATION_HOLD_FOR_PROFIT,
    RECOMMENDATION_MAX_HOLD_EXIT,
    RECOMMENDATION_NO_POSITION,
    RECOMMENDATION_SELL_NOW,
    RECOMMENDATION_STOP_LOSS_EXIT,
    ProfitabilityInput,
    evaluate_exit_profitability,
)

STATE_OPEN = "OPEN"
STATE_HOLDING_FOR_PROFIT = "HOLDING_FOR_PROFIT"
STATE_PROFITABLE_EXIT_AVAILABLE = "PROFITABLE_EXIT_AVAILABLE"
STATE_STOP_LOSS_RECOMMENDED = "STOP_LOSS_RECOMMENDED"
STATE_MAX_HOLD_EXIT_RECOMMENDED = "MAX_HOLD_EXIT_RECOMMENDED"
STATE_STALE_MARKET_DATA = "STALE_MARKET_DATA"
STATE_DUST = "DUST"
STATE_CLOSED = "CLOSED"


def evaluate_position_lifecycle(*, snapshot: PositionSnapshot, policy: PositionLifecyclePolicy, now) -> PositionLifecycleEvaluation:
    if snapshot.fail_closed_reason is not None:
        return PositionLifecycleEvaluation(
            lifecycle_state=STATE_STALE_MARKET_DATA,
            recommendation=RECOMMENDATION_HOLD_FOR_PROFIT,
            reason=f"Fail-closed lifecycle evaluation: {snapshot.fail_closed_reason}.",
            current_market_value=None,
            expected_net_realized_pnl_if_sold_now=None,
            break_even_price=None,
            minimum_profitable_exit_price=None,
            market_data_stale=True,
            stale_indicator=True,
            dust_indicator=False,
            closed_indicator=False,
        )

    if snapshot.position_size <= Decimal("0"):
        return PositionLifecycleEvaluation(
            lifecycle_state=STATE_CLOSED,
            recommendation=RECOMMENDATION_NO_POSITION,
            reason="No open quantity remains for this position.",
            current_market_value=Decimal("0"),
            expected_net_realized_pnl_if_sold_now=Decimal("0"),
            break_even_price=None,
            minimum_profitable_exit_price=None,
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=True,
        )

    if snapshot.current_price is None or snapshot.market_data_timestamp is None or snapshot.market_data_age_minutes is None:
        return PositionLifecycleEvaluation(
            lifecycle_state=STATE_OPEN,
            recommendation=RECOMMENDATION_HOLD_FOR_PROFIT,
            reason="Position is open but no market evidence is available yet for profitability evaluation.",
            current_market_value=None,
            expected_net_realized_pnl_if_sold_now=None,
            break_even_price=None,
            minimum_profitable_exit_price=None,
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=False,
            closed_indicator=False,
        )

    if snapshot.market_data_age_minutes > policy.stale_price_threshold_minutes:
        return PositionLifecycleEvaluation(
            lifecycle_state=STATE_STALE_MARKET_DATA,
            recommendation=RECOMMENDATION_HOLD_FOR_PROFIT,
            reason="Market evidence is stale; refresh current price before acting on advisory output.",
            current_market_value=snapshot.position_size * snapshot.current_price,
            expected_net_realized_pnl_if_sold_now=None,
            break_even_price=None,
            minimum_profitable_exit_price=None,
            market_data_stale=True,
            stale_indicator=True,
            dust_indicator=False,
            closed_indicator=False,
        )

    current_market_value = snapshot.position_size * snapshot.current_price
    if snapshot.position_size <= policy.minimum_position_size or current_market_value <= policy.dust_threshold:
        return PositionLifecycleEvaluation(
            lifecycle_state=STATE_DUST,
            recommendation=RECOMMENDATION_HOLD_FOR_PROFIT,
            reason="Position is below dust thresholds and is treated as non-actionable.",
            current_market_value=current_market_value,
            expected_net_realized_pnl_if_sold_now=None,
            break_even_price=None,
            minimum_profitable_exit_price=None,
            market_data_stale=False,
            stale_indicator=False,
            dust_indicator=True,
            closed_indicator=False,
        )

    stop_loss_price = policy.stop_loss_price
    if stop_loss_price is None and policy.stop_loss_percent is not None and policy.stop_loss_percent > Decimal("0"):
        stop_loss_price = snapshot.entry_price * (Decimal("1") - policy.stop_loss_percent)

    max_hold_until = None
    if policy.max_hold_minutes is not None and snapshot.opened_at is not None:
        max_hold_until = snapshot.opened_at + timedelta(minutes=policy.max_hold_minutes)

    profitability = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=snapshot.position_size,
            entry_price=snapshot.entry_price,
            current_price=snapshot.current_price,
            accumulated_entry_and_carry_costs=snapshot.accumulated_entry_and_carry_costs,
            estimated_exit_fee_rate=policy.estimated_exit_fee_rate,
            estimated_slippage_rate=policy.estimated_slippage_rate,
            minimum_net_profit_to_exit=policy.minimum_net_profit_to_exit,
            stop_loss_price=stop_loss_price,
            now=now,
            max_hold_until=max_hold_until,
        )
    )

    lifecycle_state = STATE_HOLDING_FOR_PROFIT
    if profitability.recommendation == RECOMMENDATION_SELL_NOW:
        lifecycle_state = STATE_PROFITABLE_EXIT_AVAILABLE
    elif profitability.recommendation == RECOMMENDATION_STOP_LOSS_EXIT:
        lifecycle_state = STATE_STOP_LOSS_RECOMMENDED
    elif profitability.recommendation == RECOMMENDATION_MAX_HOLD_EXIT:
        lifecycle_state = STATE_MAX_HOLD_EXIT_RECOMMENDED

    return PositionLifecycleEvaluation(
        lifecycle_state=lifecycle_state,
        recommendation=profitability.recommendation,
        reason=profitability.reason,
        current_market_value=profitability.current_market_value,
        expected_net_realized_pnl_if_sold_now=profitability.expected_net_realized_pnl_if_sold_now,
        break_even_price=profitability.break_even_price,
        minimum_profitable_exit_price=profitability.minimum_profitable_exit_price,
        market_data_stale=False,
        stale_indicator=False,
        dust_indicator=False,
        closed_indicator=False,
    )
