from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


RECOMMENDATION_SELL_NOW = "SELL_NOW"
RECOMMENDATION_HOLD_FOR_PROFIT = "HOLD_FOR_PROFIT"
RECOMMENDATION_STOP_LOSS_EXIT = "STOP_LOSS_EXIT"
RECOMMENDATION_MAX_HOLD_EXIT = "MAX_HOLD_EXIT"
RECOMMENDATION_NO_POSITION = "NO_POSITION"


@dataclass(frozen=True)
class ProfitabilityInput:
    # Position context.
    position_size: Decimal
    entry_price: Decimal
    current_price: Decimal

    # Paid costs already realized and attributable to this position (entry + carry).
    accumulated_entry_and_carry_costs: Decimal

    # Exit-cost policy assumptions for "sell now" estimation.
    estimated_exit_fee_rate: Decimal = Decimal("0")
    estimated_slippage_rate: Decimal = Decimal("0")

    # Policy thresholds.
    minimum_net_profit_to_exit: Decimal = Decimal("0")
    stop_loss_price: Decimal | None = None
    now: datetime | None = None
    max_hold_until: datetime | None = None


@dataclass(frozen=True)
class ProfitabilitySnapshot:
    entry_price: Decimal
    current_price: Decimal
    current_market_value: Decimal
    gross_pnl: Decimal

    paid_costs: Decimal
    estimated_exit_fee: Decimal
    estimated_slippage: Decimal

    break_even_price: Decimal | None
    minimum_profitable_exit_price: Decimal | None

    expected_net_realized_pnl_if_sold_now: Decimal

    recommendation: str
    reason: str


@dataclass(frozen=True)
class RealizedExitInput:
    position_size: Decimal
    entry_price: Decimal
    exit_price: Decimal
    paid_costs: Decimal
    realized_exit_fee: Decimal


@dataclass(frozen=True)
class RealizedExitSnapshot:
    gross_pnl: Decimal
    total_fees_and_costs: Decimal
    net_realized_pnl: Decimal


def _d(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _validate_common(*, position_size: Decimal, entry_price: Decimal, paid_costs: Decimal) -> None:
    if position_size < Decimal("0"):
        raise ValueError("position_size must be >= 0")
    if entry_price < Decimal("0"):
        raise ValueError("entry_price must be >= 0")
    if paid_costs < Decimal("0"):
        raise ValueError("paid costs must be >= 0")


def _validate_expected_exit(input_value: ProfitabilityInput) -> None:
    _validate_common(
        position_size=input_value.position_size,
        entry_price=input_value.entry_price,
        paid_costs=input_value.accumulated_entry_and_carry_costs,
    )
    if input_value.current_price < Decimal("0"):
        raise ValueError("current_price must be >= 0")
    if input_value.estimated_exit_fee_rate < Decimal("0"):
        raise ValueError("estimated_exit_fee_rate must be >= 0")
    if input_value.estimated_slippage_rate < Decimal("0"):
        raise ValueError("estimated_slippage_rate must be >= 0")
    if input_value.minimum_net_profit_to_exit < Decimal("0"):
        raise ValueError("minimum_net_profit_to_exit must be >= 0")
    if input_value.stop_loss_price is not None and input_value.stop_loss_price < Decimal("0"):
        raise ValueError("stop_loss_price must be >= 0")

    combined_rate = input_value.estimated_exit_fee_rate + input_value.estimated_slippage_rate
    if combined_rate >= Decimal("1"):
        raise ValueError("combined estimated exit costs must be < 1")


def evaluate_exit_profitability(input_value: ProfitabilityInput) -> ProfitabilitySnapshot:
    _validate_expected_exit(input_value)

    size = _d(input_value.position_size)
    entry_price = _d(input_value.entry_price)
    current_price = _d(input_value.current_price)
    paid_costs = _d(input_value.accumulated_entry_and_carry_costs)
    exit_fee_rate = _d(input_value.estimated_exit_fee_rate)
    slippage_rate = _d(input_value.estimated_slippage_rate)
    minimum_net_profit = _d(input_value.minimum_net_profit_to_exit)

    if size == Decimal("0"):
        return ProfitabilitySnapshot(
            entry_price=entry_price,
            current_price=current_price,
            current_market_value=Decimal("0"),
            gross_pnl=Decimal("0"),
            paid_costs=paid_costs,
            estimated_exit_fee=Decimal("0"),
            estimated_slippage=Decimal("0"),
            break_even_price=None,
            minimum_profitable_exit_price=None,
            expected_net_realized_pnl_if_sold_now=Decimal("0") - paid_costs,
            recommendation=RECOMMENDATION_NO_POSITION,
            reason="No open position to evaluate.",
        )

    entry_cost_basis = size * entry_price
    current_market_value = size * current_price

    gross_pnl = current_market_value - entry_cost_basis
    estimated_exit_fee = current_market_value * exit_fee_rate
    estimated_slippage = current_market_value * slippage_rate

    expected_net = current_market_value - entry_cost_basis - paid_costs - estimated_exit_fee - estimated_slippage

    combined_rate = exit_fee_rate + slippage_rate
    net_capture = Decimal("1") - combined_rate
    break_even_price = (entry_cost_basis + paid_costs) / (size * net_capture)
    minimum_profitable_exit_price = (entry_cost_basis + paid_costs + minimum_net_profit) / (size * net_capture)

    recommendation = RECOMMENDATION_HOLD_FOR_PROFIT
    reason = "Expected net result is below the configured profitability threshold after paid costs, fees, and slippage assumptions."

    # Policy priority: NO_POSITION > MAX_HOLD_EXIT > STOP_LOSS_EXIT > SELL_NOW > HOLD_FOR_PROFIT.
    if input_value.max_hold_until is not None and input_value.now is not None and input_value.now >= input_value.max_hold_until:
        recommendation = RECOMMENDATION_MAX_HOLD_EXIT
        reason = "Maximum hold horizon reached; exit is recommended by policy regardless of expected net result."
    elif input_value.stop_loss_price is not None and current_price <= input_value.stop_loss_price:
        recommendation = RECOMMENDATION_STOP_LOSS_EXIT
        reason = "Current price is at or below stop-loss threshold; exit is recommended to cap downside risk."
    elif expected_net >= minimum_net_profit:
        recommendation = RECOMMENDATION_SELL_NOW
        reason = "Expected net result after paid costs, fees, and slippage assumptions meets the profitability threshold."

    return ProfitabilitySnapshot(
        entry_price=entry_price,
        current_price=current_price,
        current_market_value=current_market_value,
        gross_pnl=gross_pnl,
        paid_costs=paid_costs,
        estimated_exit_fee=estimated_exit_fee,
        estimated_slippage=estimated_slippage,
        break_even_price=break_even_price,
        minimum_profitable_exit_price=minimum_profitable_exit_price,
        expected_net_realized_pnl_if_sold_now=expected_net,
        recommendation=recommendation,
        reason=reason,
    )


def evaluate_realized_exit(input_value: RealizedExitInput) -> RealizedExitSnapshot:
    _validate_common(
        position_size=input_value.position_size,
        entry_price=input_value.entry_price,
        paid_costs=input_value.paid_costs,
    )

    size = _d(input_value.position_size)
    exit_price = _d(input_value.exit_price)
    paid_costs = _d(input_value.paid_costs)
    realized_exit_fee = _d(input_value.realized_exit_fee)
    entry_price = _d(input_value.entry_price)

    if exit_price < Decimal("0"):
        raise ValueError("exit_price must be >= 0")
    if realized_exit_fee < Decimal("0"):
        raise ValueError("realized_exit_fee must be >= 0")

    entry_cost_basis = size * entry_price
    realized_exit_value = size * exit_price

    gross_pnl = realized_exit_value - entry_cost_basis
    total_fees_and_costs = paid_costs + realized_exit_fee
    net_realized_pnl = gross_pnl - total_fees_and_costs

    return RealizedExitSnapshot(
        gross_pnl=gross_pnl,
        total_fees_and_costs=total_fees_and_costs,
        net_realized_pnl=net_realized_pnl,
    )
