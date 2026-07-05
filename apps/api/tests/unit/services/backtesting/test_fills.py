from __future__ import annotations

from decimal import Decimal

from app.services.backtesting.fills import simulate_buy_fill, simulate_sell_fill


def test_simulate_buy_fill() -> None:
    fill = simulate_buy_fill(cash_available=Decimal("100"), reference_price=Decimal("10"))

    assert fill.side == "buy"
    assert fill.quantity == Decimal("10")
    assert fill.cash_delta == Decimal("-100")


def test_simulate_sell_fill() -> None:
    fill = simulate_sell_fill(quantity=Decimal("10"), reference_price=Decimal("12"))

    assert fill.side == "sell"
    assert fill.gross_value == Decimal("120")
    assert fill.cash_delta == Decimal("120")


def test_simulate_fill_fees() -> None:
    fill = simulate_buy_fill(
        cash_available=Decimal("100"), reference_price=Decimal("10"), fee_bps=Decimal("100")
    )

    assert fill.fee_paid > Decimal("0")
    assert fill.quantity < Decimal("10")


def test_simulate_fill_slippage() -> None:
    buy_fill = simulate_buy_fill(
        cash_available=Decimal("100"), reference_price=Decimal("10"), slippage_bps=Decimal("100")
    )
    sell_fill = simulate_sell_fill(
        quantity=Decimal("10"), reference_price=Decimal("10"), slippage_bps=Decimal("100")
    )

    assert buy_fill.executed_price == Decimal("10.10")
    assert sell_fill.executed_price == Decimal("9.90")


def test_simulate_fill_fractional_quantities() -> None:
    fill = simulate_buy_fill(cash_available=Decimal("25"), reference_price=Decimal("65000"))

    assert fill.quantity == Decimal("25") / Decimal("65000")