from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.profitability.engine import (
    ProfitabilityInput,
    RECOMMENDATION_HOLD_FOR_PROFIT,
    RECOMMENDATION_MAX_HOLD_EXIT,
    RECOMMENDATION_NO_POSITION,
    RECOMMENDATION_SELL_NOW,
    RECOMMENDATION_STOP_LOSS_EXIT,
    RealizedExitInput,
    evaluate_exit_profitability,
    evaluate_realized_exit,
)


def test_no_position_recommendation() -> None:
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("0"),
            entry_price=Decimal("100"),
            current_price=Decimal("110"),
            accumulated_entry_and_carry_costs=Decimal("0.25"),
        )
    )

    assert result.recommendation == RECOMMENDATION_NO_POSITION
    assert result.break_even_price is None
    assert result.minimum_profitable_exit_price is None


def test_fee_and_slippage_are_included_in_expected_net() -> None:
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("2"),
            entry_price=Decimal("100"),
            current_price=Decimal("120"),
            accumulated_entry_and_carry_costs=Decimal("1.50"),
            estimated_exit_fee_rate=Decimal("0.001"),
            estimated_slippage_rate=Decimal("0.002"),
            minimum_net_profit_to_exit=Decimal("0"),
        )
    )

    assert result.current_market_value == Decimal("240")
    assert result.gross_pnl == Decimal("40")
    assert result.estimated_exit_fee == Decimal("0.240")
    assert result.estimated_slippage == Decimal("0.480")
    assert result.expected_net_realized_pnl_if_sold_now == Decimal("37.780")
    assert result.recommendation == RECOMMENDATION_SELL_NOW


def test_break_even_and_minimum_profitable_exit_price() -> None:
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("1"),
            entry_price=Decimal("100"),
            current_price=Decimal("101"),
            accumulated_entry_and_carry_costs=Decimal("1"),
            estimated_exit_fee_rate=Decimal("0.001"),
            estimated_slippage_rate=Decimal("0.001"),
            minimum_net_profit_to_exit=Decimal("2"),
        )
    )

    assert result.break_even_price == Decimal("101.2024048096192384769539078")
    assert result.minimum_profitable_exit_price == Decimal("103.2064128256513026052104208")
    assert result.recommendation == RECOMMENDATION_HOLD_FOR_PROFIT


def test_stop_loss_exit_takes_priority_over_sell_now() -> None:
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("1"),
            entry_price=Decimal("100"),
            current_price=Decimal("94"),
            accumulated_entry_and_carry_costs=Decimal("0.2"),
            estimated_exit_fee_rate=Decimal("0.001"),
            estimated_slippage_rate=Decimal("0.001"),
            minimum_net_profit_to_exit=Decimal("0"),
            stop_loss_price=Decimal("95"),
        )
    )

    assert result.recommendation == RECOMMENDATION_STOP_LOSS_EXIT


def test_max_hold_exit_takes_priority_over_stop_loss() -> None:
    now = datetime.now(timezone.utc)
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("1"),
            entry_price=Decimal("100"),
            current_price=Decimal("94"),
            accumulated_entry_and_carry_costs=Decimal("0.2"),
            estimated_exit_fee_rate=Decimal("0.001"),
            estimated_slippage_rate=Decimal("0.001"),
            minimum_net_profit_to_exit=Decimal("0"),
            stop_loss_price=Decimal("95"),
            now=now,
            max_hold_until=now - timedelta(minutes=1),
        )
    )

    assert result.recommendation == RECOMMENDATION_MAX_HOLD_EXIT


def test_tiny_dust_position_holds_when_net_is_not_profitable() -> None:
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("0.00000091"),
            entry_price=Decimal("65000"),
            current_price=Decimal("65200"),
            accumulated_entry_and_carry_costs=Decimal("0.01"),
            estimated_exit_fee_rate=Decimal("0.003"),
            estimated_slippage_rate=Decimal("0.003"),
            minimum_net_profit_to_exit=Decimal("0"),
        )
    )

    assert result.expected_net_realized_pnl_if_sold_now < Decimal("0")
    assert result.recommendation == RECOMMENDATION_HOLD_FOR_PROFIT


def test_large_position_recommends_sell_now_when_net_positive() -> None:
    result = evaluate_exit_profitability(
        ProfitabilityInput(
            position_size=Decimal("125"),
            entry_price=Decimal("42.50"),
            current_price=Decimal("48.10"),
            accumulated_entry_and_carry_costs=Decimal("21.25"),
            estimated_exit_fee_rate=Decimal("0.0008"),
            estimated_slippage_rate=Decimal("0.0012"),
            minimum_net_profit_to_exit=Decimal("150"),
        )
    )

    assert result.expected_net_realized_pnl_if_sold_now > Decimal("150")
    assert result.recommendation == RECOMMENDATION_SELL_NOW


def test_first_flight_realized_exit_matches_ledger_semantics() -> None:
    # Golden First Flight settlement semantics use persisted quote totals.
    size = Decimal("0.00007717")
    buy_quote = Decimal("4.99")
    sell_quote = Decimal("4.99")
    buy_price = buy_quote / size
    sell_price = sell_quote / size

    result = evaluate_realized_exit(
        RealizedExitInput(
            position_size=size,
            entry_price=buy_price,
            exit_price=sell_price,
            paid_costs=Decimal("0.04"),
            realized_exit_fee=Decimal("0.03"),
        )
    )

    assert result.gross_pnl == Decimal("0")
    assert result.total_fees_and_costs == Decimal("0.07")
    assert result.net_realized_pnl == Decimal("-0.07")


@pytest.mark.parametrize(
    "position_size,entry_price,current_price,paid_costs,exit_fee_rate,slippage_rate",
    [
        (Decimal("-0.001"), Decimal("1"), Decimal("1"), Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("1"), Decimal("-1"), Decimal("1"), Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("1"), Decimal("1"), Decimal("-1"), Decimal("0"), Decimal("0"), Decimal("0")),
        (Decimal("1"), Decimal("1"), Decimal("1"), Decimal("-0.01"), Decimal("0"), Decimal("0")),
        (Decimal("1"), Decimal("1"), Decimal("1"), Decimal("0"), Decimal("0.7"), Decimal("0.3")),
    ],
)
def test_invalid_inputs_fail_closed(
    position_size: Decimal,
    entry_price: Decimal,
    current_price: Decimal,
    paid_costs: Decimal,
    exit_fee_rate: Decimal,
    slippage_rate: Decimal,
) -> None:
    with pytest.raises(ValueError):
        evaluate_exit_profitability(
            ProfitabilityInput(
                position_size=position_size,
                entry_price=entry_price,
                current_price=current_price,
                accumulated_entry_and_carry_costs=paid_costs,
                estimated_exit_fee_rate=exit_fee_rate,
                estimated_slippage_rate=slippage_rate,
            )
        )
