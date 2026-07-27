from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.live_accounting_record import LiveAccountingRecord
from app.services.live.position_quantity import compute_signed_owned_quantity, owned_position_exists
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [LiveAccountingRecord.__table__]
_PROFILE_ID = uuid.uuid4()
_SYMBOL = "BTC-USD"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


def _row(
    *, side: str, record_type: str, filled_quantity: Decimal, provider_order_id: str,
    provider_fill_id: str, live_trading_profile_id: uuid.UUID = _PROFILE_ID, symbol: str = _SYMBOL,
) -> LiveAccountingRecord:
    return LiveAccountingRecord(
        idempotency_key=f"{provider_order_id}:{provider_fill_id}:{record_type}",
        live_trading_profile_id=live_trading_profile_id,
        live_crypto_order_id=None,
        capital_campaign_id=None,
        reconciliation_event_id=uuid.uuid4(),
        source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created",
        record_type=record_type,
        provider_order_id=provider_order_id,
        provider_fill_id=provider_fill_id,
        symbol=symbol,
        side=side,
        filled_quantity=filled_quantity,
        fill_price=Decimal("64900.00"),
        gross_notional=filled_quantity * Decimal("64900.00"),
        fee_amount=Decimal("0.05"),
        fee_currency="USD",
        net_cash_impact=Decimal("0"),
        provenance={},
        recorded_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )


async def _seed_fill_and_fee(
    session: AsyncSession, *, side: str, quantity: Decimal, provider_order_id: str,
) -> None:
    """Mirrors exactly what record_live_fill_reconciliation actually writes
    for one real fill: a fill_accounting row AND a fee_attribution row,
    same side, same filled_quantity."""
    session.add(_row(
        side=side, record_type="fill_accounting", filled_quantity=quantity,
        provider_order_id=provider_order_id, provider_fill_id=f"{provider_order_id}-fill",
    ))
    session.add(_row(
        side=side, record_type="fee_attribution", filled_quantity=quantity,
        provider_order_id=provider_order_id, provider_fill_id=f"{provider_order_id}-fill",
    ))
    await session.flush()


@pytest.mark.asyncio
async def test_buy_fill_and_its_fee_attribution_counts_once_not_twice() -> None:
    async with _real_session() as session:
        await _seed_fill_and_fee(session, side="buy", quantity=Decimal("0.00007817"), provider_order_id="BUY-1")

        quantity = await compute_signed_owned_quantity(db=session, live_trading_profile_id=_PROFILE_ID, symbol=_SYMBOL)
        assert quantity == Decimal("0.00007817")  # not 0.00015634 (double-counted)


@pytest.mark.asyncio
async def test_sell_fill_and_its_fee_attribution_counts_once_not_twice() -> None:
    async with _real_session() as session:
        await _seed_fill_and_fee(session, side="buy", quantity=Decimal("0.0002"), provider_order_id="BUY-1")
        await _seed_fill_and_fee(session, side="sell", quantity=Decimal("0.00007817"), provider_order_id="SELL-1")

        quantity = await compute_signed_owned_quantity(db=session, live_trading_profile_id=_PROFILE_ID, symbol=_SYMBOL)
        assert quantity == Decimal("0.0002") - Decimal("0.00007817")  # not double-subtracted


@pytest.mark.asyncio
async def test_matching_buy_and_sell_produce_exactly_zero_owned_quantity() -> None:
    async with _real_session() as session:
        await _seed_fill_and_fee(session, side="buy", quantity=Decimal("0.00007817"), provider_order_id="BUY-1")
        await _seed_fill_and_fee(session, side="sell", quantity=Decimal("0.00007817"), provider_order_id="SELL-1")

        quantity = await compute_signed_owned_quantity(db=session, live_trading_profile_id=_PROFILE_ID, symbol=_SYMBOL)
        assert quantity == Decimal("0")
        assert await owned_position_exists(db=session, live_trading_profile_id=_PROFILE_ID, symbol=_SYMBOL) is False


@pytest.mark.asyncio
async def test_unequal_buy_and_sell_quantities_remain_correctly_open() -> None:
    async with _real_session() as session:
        await _seed_fill_and_fee(session, side="buy", quantity=Decimal("0.0003"), provider_order_id="BUY-1")
        await _seed_fill_and_fee(session, side="sell", quantity=Decimal("0.0001"), provider_order_id="SELL-1")

        quantity = await compute_signed_owned_quantity(db=session, live_trading_profile_id=_PROFILE_ID, symbol=_SYMBOL)
        assert quantity == Decimal("0.0002")
        assert await owned_position_exists(db=session, live_trading_profile_id=_PROFILE_ID, symbol=_SYMBOL) is True


@pytest.mark.asyncio
async def test_fee_attribution_rows_remain_present_after_quantity_computation() -> None:
    """The fix excludes fee_attribution rows from the QUANTITY sum -- it must
    never delete or alter them. They still exist and still carry the fee
    amount other code (capital_ledger) uses for cash/fee accounting."""
    async with _real_session() as session:
        await _seed_fill_and_fee(session, side="buy", quantity=Decimal("0.00007817"), provider_order_id="BUY-1")

        from sqlalchemy import select

        fee_rows = (await session.execute(
            select(LiveAccountingRecord).where(LiveAccountingRecord.record_type == "fee_attribution")
        )).scalars().all()
        assert len(fee_rows) == 1
        assert fee_rows[0].fee_amount == Decimal("0.05")
        assert fee_rows[0].filled_quantity == Decimal("0.00007817")  # untouched, not deleted or zeroed
