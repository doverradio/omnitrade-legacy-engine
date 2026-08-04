from __future__ import annotations

from decimal import Decimal

from strategy_lab.costs import CostModel


def test_effective_buy_price_is_worse_than_raw():
    model = CostModel(fee_pct=Decimal("0.001"), slippage_pct=Decimal("0.01"))
    assert model.effective_buy_price(Decimal("100")) == Decimal("101.00")


def test_effective_sell_price_is_worse_than_raw():
    model = CostModel(fee_pct=Decimal("0.001"), slippage_pct=Decimal("0.01"))
    assert model.effective_sell_price(Decimal("100")) == Decimal("99.00")


def test_zero_slippage_is_a_no_op():
    model = CostModel(fee_pct=Decimal("0.001"), slippage_pct=Decimal("0"))
    assert model.effective_buy_price(Decimal("100")) == Decimal("100")
    assert model.effective_sell_price(Decimal("100")) == Decimal("100")
