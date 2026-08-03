from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.candle import Candle
from app.services.entry_intelligence.shadow_validation import replay_rejected_buy_candidate_counterfactual
from tests.support.real_sqlite_session import real_sqlite_session


def _candle(*, asset_id, open_time, close_time, open_, high, low, close):
    return Candle(
        asset_id=asset_id, interval="15m", open_time=open_time, close_time=close_time,
        open=Decimal(str(open_)), high=Decimal(str(high)), low=Decimal(str(low)), close=Decimal(str(close)),
        volume=Decimal("1"), source="test",
    )


@pytest.mark.asyncio
async def test_limit_fills_and_horizon_outcomes_computed() -> None:
    asset_id = uuid4()
    candle_close_time = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    async with real_sqlite_session([Candle.__table__]) as db:
        # 15m candles from 12:00 to 16:15. Price dips to 99.90 at 12:30
        # (below preferred_limit_price 99.92) then recovers.
        rows = [
            (100.00, 100.10, 99.95, 100.00),  # 12:00-12:15
            (100.00, 100.05, 99.90, 99.95),   # 12:15-12:30 -- fills here
            (99.95, 100.50, 99.90, 100.40),   # 12:30-12:45
            (100.40, 101.00, 100.30, 100.90), # 12:45-13:00
        ]
        for i, (o, h, l, c) in enumerate(rows):
            db.add(_candle(
                asset_id=asset_id,
                open_time=candle_close_time + timedelta(minutes=15 * i),
                close_time=candle_close_time + timedelta(minutes=15 * (i + 1)),
                open_=o, high=h, low=l, close=c,
            ))
        # extend out to 4h with flat candles so every horizon has data
        last_close = candle_close_time + timedelta(minutes=15 * len(rows))
        for i in range(len(rows), 16):
            db.add(_candle(
                asset_id=asset_id,
                open_time=candle_close_time + timedelta(minutes=15 * i),
                close_time=candle_close_time + timedelta(minutes=15 * (i + 1)),
                open_=101.00, high=101.20, low=100.90, close=101.10,
            ))
        await db.commit()

        result = await replay_rejected_buy_candidate_counterfactual(
            db=db, asset_id=asset_id, interval="15m", instrument="BTC-USD",
            candle_close_time=candle_close_time,
            market_entry_price=Decimal("100.00"),
            preferred_limit_price=Decimal("99.92"),
            maximum_profitable_entry_price=Decimal("99.92"),
            expiration_time=candle_close_time + timedelta(hours=4),
            round_trip_fee_pct=Decimal("0.02"), slippage_pct=Decimal("0.01"),
            required_profit_buffer_pct=Decimal("0"),
        )

    assert result.data_sufficient is True
    assert result.would_limit_fill is True
    assert result.time_to_fill_minutes == 30
    assert result.fill_price == Decimal("99.92")
    assert result.maximum_favorable_excursion_pct is not None
    assert result.maximum_adverse_excursion_pct is not None
    assert len(result.market_entry_horizon_outcomes) == 5
    assert len(result.limit_entry_horizon_outcomes) == 5
    one_hour_limit = next(item for item in result.limit_entry_horizon_outcomes if item.horizon_label == "1h")
    assert one_hour_limit.net_pnl_pct is not None
    assert result.strategy_native_exit is None
    assert "strategy_native_exit_not_implemented" in result.missing_input_flags
    # filled -> no missed-opportunity/avoided-loss framing applies
    assert result.missed_opportunity_cost_pct is None
    assert result.avoided_loss_value_pct is None


@pytest.mark.asyncio
async def test_limit_never_fills_produces_missed_opportunity_or_avoided_loss() -> None:
    asset_id = uuid4()
    candle_close_time = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    async with real_sqlite_session([Candle.__table__]) as db:
        # Price only ever rises -- never touches the 99.92 limit price.
        for i in range(16):
            db.add(_candle(
                asset_id=asset_id,
                open_time=candle_close_time + timedelta(minutes=15 * i),
                close_time=candle_close_time + timedelta(minutes=15 * (i + 1)),
                open_=100.00, high=101.00, low=100.05, close=100.80,
            ))
        await db.commit()

        result = await replay_rejected_buy_candidate_counterfactual(
            db=db, asset_id=asset_id, interval="15m", instrument="BTC-USD",
            candle_close_time=candle_close_time,
            market_entry_price=Decimal("100.00"),
            preferred_limit_price=Decimal("99.92"),
            maximum_profitable_entry_price=Decimal("99.92"),
            expiration_time=candle_close_time + timedelta(hours=4),
            round_trip_fee_pct=Decimal("0.02"), slippage_pct=Decimal("0.01"),
            required_profit_buffer_pct=Decimal("0"),
        )

    assert result.would_limit_fill is False
    assert result.fill_price is None
    assert result.time_to_fill_minutes is None
    assert result.limit_entry_horizon_outcomes == ()
    # Market kept rising -- missed opportunity, not avoided loss.
    assert result.missed_opportunity_cost_pct is not None
    assert result.missed_opportunity_cost_pct > Decimal("0")
    assert result.avoided_loss_value_pct is None


@pytest.mark.asyncio
async def test_limit_never_fills_and_market_would_have_lost_is_avoided_loss() -> None:
    asset_id = uuid4()
    candle_close_time = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    async with real_sqlite_session([Candle.__table__]) as db:
        # Price falls but never as low as the limit price (limit at 99.92,
        # price bottoms at 99.95) -- market entry would have lost money,
        # but the (unfilled) limit avoided that loss entirely.
        for i in range(16):
            db.add(_candle(
                asset_id=asset_id,
                open_time=candle_close_time + timedelta(minutes=15 * i),
                close_time=candle_close_time + timedelta(minutes=15 * (i + 1)),
                open_=100.00, high=100.05, low=99.95, close=99.97,
            ))
        await db.commit()

        result = await replay_rejected_buy_candidate_counterfactual(
            db=db, asset_id=asset_id, interval="15m", instrument="BTC-USD",
            candle_close_time=candle_close_time,
            market_entry_price=Decimal("100.00"),
            preferred_limit_price=Decimal("99.92"),
            maximum_profitable_entry_price=Decimal("99.92"),
            expiration_time=candle_close_time + timedelta(hours=4),
            round_trip_fee_pct=Decimal("0.02"), slippage_pct=Decimal("0.01"),
            required_profit_buffer_pct=Decimal("0"),
        )

    assert result.would_limit_fill is False
    assert result.avoided_loss_value_pct is not None
    assert result.avoided_loss_value_pct > Decimal("0")
    assert result.missed_opportunity_cost_pct is None


@pytest.mark.asyncio
async def test_no_candle_data_fails_closed_not_a_fabricated_result() -> None:
    asset_id = uuid4()
    candle_close_time = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    async with real_sqlite_session([Candle.__table__]) as db:
        result = await replay_rejected_buy_candidate_counterfactual(
            db=db, asset_id=asset_id, interval="15m", instrument="BTC-USD",
            candle_close_time=candle_close_time,
            market_entry_price=Decimal("100.00"),
            preferred_limit_price=Decimal("99.92"),
            maximum_profitable_entry_price=Decimal("99.92"),
            expiration_time=candle_close_time + timedelta(hours=4),
            round_trip_fee_pct=Decimal("0.02"), slippage_pct=Decimal("0.01"),
            required_profit_buffer_pct=Decimal("0"),
        )

    assert result.data_sufficient is False
    assert "no_candle_data_in_window" in result.missing_input_flags
    assert result.would_limit_fill is False
    assert result.market_entry_horizon_outcomes == ()
