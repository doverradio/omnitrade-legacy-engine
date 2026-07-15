from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.services.position_lifecycle.contracts import PositionLifecyclePolicy, PositionSnapshot
from app.services.position_lifecycle.evaluator import (
    STATE_CLOSED,
    STATE_DUST,
    STATE_HOLDING_FOR_PROFIT,
    STATE_MAX_HOLD_EXIT_RECOMMENDED,
    STATE_OPEN,
    STATE_PROFITABLE_EXIT_AVAILABLE,
    STATE_STALE_MARKET_DATA,
    STATE_STOP_LOSS_RECOMMENDED,
    evaluate_position_lifecycle,
)
from app.services.profitability.engine import RECOMMENDATION_HOLD_FOR_PROFIT, RECOMMENDATION_NO_POSITION, RECOMMENDATION_SELL_NOW, RECOMMENDATION_STOP_LOSS_EXIT


def _policy() -> PositionLifecyclePolicy:
    return PositionLifecyclePolicy(
        policy_id="pl-policy-crypto-venue-neutral-v1",
        policy_version="1.0.0",
        asset_class="crypto",
        venue_scope="venue-neutral",
        instrument_scope=None,
        evaluation_cadence="5m",
        effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
        minimum_net_profit_to_exit=Decimal("2"),
        estimated_exit_fee_rate=Decimal("0.001"),
        estimated_slippage_rate=Decimal("0.001"),
        stale_price_threshold_minutes=15,
        minimum_position_size=Decimal("0.00001"),
        stop_loss_percent=Decimal("0.02"),
        stop_loss_price=None,
        max_hold_minutes=24 * 60,
        dust_threshold=Decimal("5"),
    )


def _snapshot(**overrides) -> PositionSnapshot:
    base = PositionSnapshot(
        position_id="position-1",
        live_trading_profile_id=uuid4(),
        account_id=uuid4(),
        capital_campaign_id=1,
        symbol="BTC-USD",
        asset_class="crypto",
        position_size=Decimal("1"),
        entry_price=Decimal("100"),
        accumulated_entry_and_carry_costs=Decimal("0.25"),
        opened_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        last_fill_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        provider_order_ids=("ord-1",),
        provider_fill_ids=("fill-1",),
        accounting_record_count=1,
        fail_closed_reason=None,
        current_price=Decimal("103"),
        market_data_timestamp=datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc),
        market_data_age_minutes=3,
        market_data_interval="15m",
        market_data_source="kraken_spot",
        market_data_candle_id=1,
    )
    return PositionSnapshot(**{**base.__dict__, **overrides})


def test_closed_state_when_size_zero() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(position_size=Decimal("0")), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_CLOSED
    assert result.recommendation == RECOMMENDATION_NO_POSITION
    assert result.closed_indicator is True


def test_open_state_when_price_missing() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(current_price=None), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_OPEN


def test_open_state_when_market_timestamp_missing() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(market_data_timestamp=None), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_OPEN


def test_open_state_when_market_age_missing() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(market_data_age_minutes=None), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_OPEN


def test_stale_market_data_state_when_age_exceeds_policy() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(market_data_age_minutes=20), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_STALE_MARKET_DATA
    assert result.market_data_stale is True
    assert result.stale_indicator is True


def test_dust_state_when_quantity_is_tiny() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(position_size=Decimal("0.000001")), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_DUST
    assert result.dust_indicator is True


def test_dust_state_when_notional_is_tiny() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(
        snapshot=_snapshot(position_size=Decimal("0.1"), current_price=Decimal("20")),
        policy=_policy(),
        now=now,
    )
    assert result.lifecycle_state == STATE_DUST


def test_profitable_exit_available_when_net_above_threshold() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(current_price=Decimal("110")), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_PROFITABLE_EXIT_AVAILABLE
    assert result.recommendation == RECOMMENDATION_SELL_NOW


def test_holding_for_profit_when_net_below_threshold() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(current_price=Decimal("101")), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_HOLDING_FOR_PROFIT
    assert result.recommendation == RECOMMENDATION_HOLD_FOR_PROFIT


def test_stop_loss_state_when_price_below_stop_loss() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(current_price=Decimal("97")), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_STOP_LOSS_RECOMMENDED
    assert result.recommendation == RECOMMENDATION_STOP_LOSS_EXIT


def test_max_hold_state_when_opened_too_long_ago() -> None:
    now = datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(opened_at=datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)), policy=_policy(), now=now)
    assert result.lifecycle_state == STATE_MAX_HOLD_EXIT_RECOMMENDED


def test_holding_when_max_hold_configured_but_opened_at_missing() -> None:
    now = datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(
        snapshot=_snapshot(opened_at=None, current_price=Decimal("101")),
        policy=_policy(),
        now=now,
    )
    assert result.lifecycle_state == STATE_HOLDING_FOR_PROFIT


def test_break_even_price_available_on_evaluated_position() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(current_price=Decimal("103")), policy=_policy(), now=now)
    assert result.break_even_price is not None


def test_expected_net_present_on_evaluated_position() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(snapshot=_snapshot(current_price=Decimal("103")), policy=_policy(), now=now)
    assert result.expected_net_realized_pnl_if_sold_now is not None


def test_fail_closed_reason_returns_stale_market_data_guard_state() -> None:
    now = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)
    result = evaluate_position_lifecycle(
        snapshot=_snapshot(fail_closed_reason="net_short_not_supported"),
        policy=_policy(),
        now=now,
    )
    assert result.lifecycle_state == STATE_STALE_MARKET_DATA
    assert result.stale_indicator is True
