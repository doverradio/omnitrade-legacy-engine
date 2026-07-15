from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.services.position_lifecycle.contracts import PositionLifecyclePolicy, PositionSnapshot
from app.services.position_lifecycle.evaluator import STATE_CLOSED, evaluate_position_lifecycle
from app.services.profitability.engine import RECOMMENDATION_NO_POSITION


def _policy() -> PositionLifecyclePolicy:
    return PositionLifecyclePolicy(
        policy_id="pl-policy-crypto-venue-neutral-v1",
        policy_version="1.0.0",
        asset_class="crypto",
        venue_scope="venue-neutral",
        instrument_scope=None,
        evaluation_cadence="5m",
        effective_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        expires_at=None,
        minimum_net_profit_to_exit=Decimal("2"),
        estimated_exit_fee_rate=Decimal("0.004"),
        estimated_slippage_rate=Decimal("0.001"),
        stale_price_threshold_minutes=15,
        minimum_position_size=Decimal("0.00001"),
        stop_loss_percent=Decimal("0.02"),
        stop_loss_price=None,
        max_hold_minutes=1440,
        dust_threshold=Decimal("5"),
    )


def _first_flight_closed_snapshot() -> PositionSnapshot:
    # First Flight run id: 515bc297-af4e-4364-8ac4-88572e1fe54e
    # BUY 0.00007717 @ quote 4.99 fee 0.04
    # SELL 0.00007717 @ quote 4.99 fee 0.03
    return PositionSnapshot(
        position_id="515bc297-af4e-4364-8ac4-88572e1fe54e",
        live_trading_profile_id=uuid4(),
        account_id=uuid4(),
        capital_campaign_id=1,
        symbol="BTC-USD",
        asset_class="crypto",
        position_size=Decimal("0"),
        entry_price=Decimal("64636.51678035506025657639005"),
        accumulated_entry_and_carry_costs=Decimal("0.04"),
        opened_at=datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc),
        last_fill_at=datetime(2026, 7, 10, 9, 5, tzinfo=timezone.utc),
        provider_order_ids=("BUY_ORDER", "SELL_ORDER"),
        provider_fill_ids=("BUY_FILL", "SELL_FILL"),
        accounting_record_count=2,
        fail_closed_reason=None,
        current_price=Decimal("64636.51678035506025657639005"),
        market_data_timestamp=datetime(2026, 7, 10, 9, 6, tzinfo=timezone.utc),
        market_data_age_minutes=1,
        market_data_interval="15m",
        market_data_source="kraken_spot",
        market_data_candle_id=123,
    )


def test_first_flight_completed_position_evaluates_closed_deterministically() -> None:
    now = datetime(2026, 7, 10, 9, 10, tzinfo=timezone.utc)
    snapshot = _first_flight_closed_snapshot()

    first = evaluate_position_lifecycle(snapshot=snapshot, policy=_policy(), now=now)
    second = evaluate_position_lifecycle(snapshot=snapshot, policy=_policy(), now=now)

    assert first.lifecycle_state == STATE_CLOSED
    assert first.recommendation == RECOMMENDATION_NO_POSITION
    assert first.closed_indicator is True
    assert first.expected_net_realized_pnl_if_sold_now == Decimal("0")

    assert second.lifecycle_state == STATE_CLOSED
    assert second.recommendation == RECOMMENDATION_NO_POSITION
