from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from strategy_lab.capital import BALANCED, FULL_COMPOUNDING, CapitalPolicy, apply_capital_policy
from strategy_lab.strategy import ExitReason, TradeRecord

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _trade(index: int, net_return_pct) -> TradeRecord:
    net_return_pct = Decimal(str(net_return_pct))
    return TradeRecord(
        entry_candle_index=index,
        entry_timestamp=_EPOCH + timedelta(hours=index),
        entry_order_price=Decimal("100"),
        raw_fill_price=Decimal("100"),
        effective_entry_price=Decimal("100"),
        entry_fee=Decimal("0"),
        entry_slippage_cost=Decimal("0"),
        exit_candle_index=index + 1,
        exit_timestamp=_EPOCH + timedelta(hours=index + 1),
        raw_exit_price=Decimal("100") * (Decimal("1") + net_return_pct),
        effective_exit_price=Decimal("100") * (Decimal("1") + net_return_pct),
        exit_fee=Decimal("0"),
        exit_slippage_cost=Decimal("0"),
        exit_reason=ExitReason.DECLINING_CLOSES,
        quantity=Decimal("1"),
        equity_before=Decimal("100"),
        equity_after=Decimal("100") * (Decimal("1") + net_return_pct),
        gross_return_pct=net_return_pct,
        net_return_pct=net_return_pct,
        highest_price_during_trade=Decimal("100"),
        lowest_price_during_trade=Decimal("100"),
        holding_candles=1,
    )


def test_policy_rejects_allocation_not_summing_to_100():
    with pytest.raises(ValueError):
        CapitalPolicy(
            name="bad",
            trade_deployment_pct=Decimal("50"),
            profit_compound_pct=Decimal("50"),
            profit_withdrawal_pct=Decimal("20"),
            profit_tax_reserve_pct=Decimal("20"),
        )


def test_policy_rejects_deployment_out_of_range():
    with pytest.raises(ValueError):
        CapitalPolicy(
            name="bad",
            trade_deployment_pct=Decimal("0"),
            profit_compound_pct=Decimal("100"),
            profit_withdrawal_pct=Decimal("0"),
            profit_tax_reserve_pct=Decimal("0"),
        )
    with pytest.raises(ValueError):
        CapitalPolicy(
            name="bad",
            trade_deployment_pct=Decimal("101"),
            profit_compound_pct=Decimal("100"),
            profit_withdrawal_pct=Decimal("0"),
            profit_tax_reserve_pct=Decimal("0"),
        )


def test_full_compounding_matches_simple_compounding():
    trades = [_trade(0, "0.10"), _trade(1, "-0.05"), _trade(2, "0.10")]
    result = apply_capital_policy(trades, initial_capital=Decimal("100"), policy=FULL_COMPOUNDING)

    expected = Decimal("100") * Decimal("1.10") * Decimal("0.95") * Decimal("1.10")
    assert result.trading_capital_final == expected
    assert result.total_economic_value_final == expected
    assert result.cumulative_withdrawn_final == Decimal("0")
    assert result.cumulative_tax_reserve_final == Decimal("0")
    # Full compounding IS the raw strategy view, so they must match exactly.
    assert result.raw_strategy_net_return_pct == ((expected / Decimal("100")) - 1) * Decimal("100")


def test_balanced_policy_splits_profit_three_ways_and_absorbs_losses_fully():
    trades = [_trade(0, "0.20"), _trade(1, "-0.20")]
    result = apply_capital_policy(trades, initial_capital=Decimal("100"), policy=BALANCED)

    first = result.records[0]
    # deployed 25% of 100 = 25; profit = 25 * 0.20 = 5
    assert first.deployed_notional == Decimal("25")
    assert first.realized_pnl == Decimal("5")
    assert first.compounded_amount == Decimal("3")   # 60% of 5
    assert first.withdrawn_amount == Decimal("1")     # 20% of 5
    assert first.tax_reserved_amount == Decimal("1")  # 20% of 5
    assert first.trading_capital_after == Decimal("103")  # only compounded part returns

    second = result.records[1]
    # deployed 25% of 103 = 25.75; loss = 25.75 * -0.20 = -5.15, fully absorbed
    assert second.deployed_notional == Decimal("25.75")
    assert second.realized_pnl == Decimal("-5.15")
    assert second.withdrawn_amount == Decimal("0")
    assert second.tax_reserved_amount == Decimal("0")
    assert second.trading_capital_after == Decimal("103") + Decimal("-5.15")

    assert result.cumulative_withdrawn_final == Decimal("1")
    assert result.cumulative_tax_reserve_final == Decimal("1")
    assert result.total_economic_value_final == (
        result.trading_capital_final + result.cumulative_withdrawn_final + result.cumulative_tax_reserve_final
    )


def test_total_economic_value_change_always_equals_realized_pnl():
    trades = [_trade(i, pct) for i, pct in enumerate(["0.05", "-0.03", "0.08", "-0.10", "0.02"])]
    result = apply_capital_policy(trades, initial_capital=Decimal("100"), policy=BALANCED)

    previous_value = result.initial_capital
    for record in result.records:
        assert record.total_economic_value_after - previous_value == record.realized_pnl
        previous_value = record.total_economic_value_after


def test_raw_strategy_return_is_policy_independent():
    trades = [_trade(0, "0.10"), _trade(1, "-0.05"), _trade(2, "0.10")]

    full = apply_capital_policy(trades, initial_capital=Decimal("100"), policy=FULL_COMPOUNDING)
    balanced = apply_capital_policy(trades, initial_capital=Decimal("100"), policy=BALANCED)

    assert full.raw_strategy_net_return_pct == balanced.raw_strategy_net_return_pct


def test_next_trade_deployed_notional_projects_from_final_trading_capital():
    trades = [_trade(0, "0.10")]
    result = apply_capital_policy(trades, initial_capital=Decimal("100"), policy=BALANCED)

    expected = result.trading_capital_final * Decimal("25") / Decimal("100")
    assert result.next_trade_deployed_notional == expected


def test_empty_trades_leaves_capital_unchanged():
    result = apply_capital_policy([], initial_capital=Decimal("100"), policy=BALANCED)

    assert result.trading_capital_final == Decimal("100")
    assert result.total_economic_value_final == Decimal("100")
    assert result.records == []
