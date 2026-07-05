from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


BPS_DENOMINATOR = Decimal("10000")


@dataclass(frozen=True, slots=True)
class FillSimulationResult:
    side: str
    quantity: Decimal
    reference_price: Decimal
    executed_price: Decimal
    gross_value: Decimal
    fee_paid: Decimal
    slippage_cost: Decimal
    cash_delta: Decimal


def simulate_buy_fill(
    *,
    cash_available: Decimal | str | int,
    reference_price: Decimal | str | int,
    fee_bps: Decimal | str | int = Decimal("0"),
    slippage_bps: Decimal | str | int = Decimal("0"),
) -> FillSimulationResult:
    available = Decimal(str(cash_available))
    price = Decimal(str(reference_price))
    fee_rate = Decimal(str(fee_bps)) / BPS_DENOMINATOR
    slippage_rate = Decimal(str(slippage_bps)) / BPS_DENOMINATOR

    executed_price = price * (Decimal("1") + slippage_rate)
    total_cost_per_unit = executed_price * (Decimal("1") + fee_rate)
    quantity = Decimal("0") if total_cost_per_unit == 0 else available / total_cost_per_unit
    gross_value = quantity * executed_price
    fee_paid = gross_value * fee_rate
    slippage_cost = quantity * (executed_price - price)
    cash_delta = -(gross_value + fee_paid)

    return FillSimulationResult(
        side="buy",
        quantity=quantity,
        reference_price=price,
        executed_price=executed_price,
        gross_value=gross_value,
        fee_paid=fee_paid,
        slippage_cost=slippage_cost,
        cash_delta=cash_delta,
    )


def simulate_sell_fill(
    *,
    quantity: Decimal | str | int,
    reference_price: Decimal | str | int,
    fee_bps: Decimal | str | int = Decimal("0"),
    slippage_bps: Decimal | str | int = Decimal("0"),
) -> FillSimulationResult:
    filled_quantity = Decimal(str(quantity))
    price = Decimal(str(reference_price))
    fee_rate = Decimal(str(fee_bps)) / BPS_DENOMINATOR
    slippage_rate = Decimal(str(slippage_bps)) / BPS_DENOMINATOR

    executed_price = price * (Decimal("1") - slippage_rate)
    gross_value = filled_quantity * executed_price
    fee_paid = gross_value * fee_rate
    slippage_cost = filled_quantity * (price - executed_price)
    cash_delta = gross_value - fee_paid

    return FillSimulationResult(
        side="sell",
        quantity=filled_quantity,
        reference_price=price,
        executed_price=executed_price,
        gross_value=gross_value,
        fee_paid=fee_paid,
        slippage_cost=slippage_cost,
        cash_delta=cash_delta,
    )