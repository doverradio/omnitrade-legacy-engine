from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.services.controlled_proof.exposure import compute_controlled_proof_open_exposure_usd
from app.services.live.risk_accounting_snapshot import RiskAccountingUnavailableError
from tests.support.real_sqlite_session import real_sqlite_session

_ALL_TABLES = [ControlledProofRun.__table__, LiveAccountingRecord.__table__]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


def _proof(*, buy_order_id: uuid.UUID | None, sell_order_id: uuid.UUID | None) -> ControlledProofRun:
    return ControlledProofRun(
        proof_id=uuid.uuid4(),
        status="RECONCILED",
        provider="kraken_spot",
        environment="production",
        campaign_id=uuid.uuid4(),
        campaign_version=1,
        product_id="BTC-USD",
        max_notional_usd=Decimal("5"),
        idempotency_key=f"proof-{uuid.uuid4()}",
        requested_by="operator:owner",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        buy_live_crypto_order_id=buy_order_id,
        sell_live_crypto_order_id=sell_order_id,
    )


def _fill(
    *, live_trading_profile_id: uuid.UUID, live_crypto_order_id: uuid.UUID, side: str,
    filled_quantity: Decimal, fill_price: Decimal, gross_notional: Decimal, fee_amount: Decimal,
    recorded_at: datetime,
) -> LiveAccountingRecord:
    return LiveAccountingRecord(
        idempotency_key=f"fill-{uuid.uuid4()}",
        live_trading_profile_id=live_trading_profile_id,
        live_crypto_order_id=live_crypto_order_id,
        capital_campaign_id=None,
        reconciliation_event_id=uuid.uuid4(),
        source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created",
        record_type="fill_accounting",
        provider_order_id=f"kraken-{live_crypto_order_id}",
        symbol="BTC-USD",
        side=side,
        filled_quantity=filled_quantity,
        fill_price=fill_price,
        gross_notional=gross_notional,
        fee_amount=fee_amount,
        fee_currency="USD",
        net_cash_impact=Decimal("0"),
        provenance={},
        recorded_at=recorded_at,
    )


@pytest.mark.asyncio
async def test_no_controlled_proof_history_reports_zero_exposure() -> None:
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        assert await compute_controlled_proof_open_exposure_usd(db=session, live_trading_profile_id=profile_id) == Decimal("0")


@pytest.mark.asyncio
async def test_open_buy_with_no_sell_reports_nonzero_exposure() -> None:
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        buy_order_id = uuid.uuid4()
        session.add(_proof(buy_order_id=buy_order_id, sell_order_id=None))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=buy_order_id, side="buy",
            filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.04"),
            recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()

        exposure = await compute_controlled_proof_open_exposure_usd(db=session, live_trading_profile_id=profile_id)

        assert exposure == Decimal("0.0001") * Decimal("50000")


@pytest.mark.asyncio
async def test_fully_sold_and_reconciled_proof_returns_to_zero_exposure_same_day() -> None:
    """The exact fix under test: once a proof's SELL fully reconciles, its
    exposure must return to zero -- even on the SAME UTC day as the BUY --
    unlike the ordinary daily_deployed_usd metric this replaces."""
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        buy_order_id = uuid.uuid4()
        sell_order_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        session.add(_proof(buy_order_id=buy_order_id, sell_order_id=sell_order_id))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=buy_order_id, side="buy",
            filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.04"), recorded_at=now,
        ))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=sell_order_id, side="sell",
            filled_quantity=Decimal("0.0001"), fill_price=Decimal("50500"),
            gross_notional=Decimal("5.05"), fee_amount=Decimal("0.04"), recorded_at=now + timedelta(seconds=1),
        ))
        await session.flush()

        exposure = await compute_controlled_proof_open_exposure_usd(db=session, live_trading_profile_id=profile_id)

        assert exposure == Decimal("0")


@pytest.mark.asyncio
async def test_a_second_same_day_proof_is_unaffected_by_the_first_fully_closed_proof() -> None:
    """Two Controlled Proofs, same UTC day, same profile: the first fully
    closes, and a second proof's own open BUY is measured independently --
    proving accounting does not leak/accumulate across proofs."""
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        first_buy, first_sell = uuid.uuid4(), uuid.uuid4()
        session.add(_proof(buy_order_id=first_buy, sell_order_id=first_sell))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=first_buy, side="buy",
            filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.04"), recorded_at=now,
        ))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=first_sell, side="sell",
            filled_quantity=Decimal("0.0001"), fill_price=Decimal("50500"),
            gross_notional=Decimal("5.05"), fee_amount=Decimal("0.04"), recorded_at=now + timedelta(seconds=1),
        ))

        second_buy = uuid.uuid4()
        session.add(_proof(buy_order_id=second_buy, sell_order_id=None))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=second_buy, side="buy",
            filled_quantity=Decimal("0.0002"), fill_price=Decimal("50000"),
            gross_notional=Decimal("10"), fee_amount=Decimal("0.08"), recorded_at=now + timedelta(seconds=2),
        ))
        await session.flush()

        exposure = await compute_controlled_proof_open_exposure_usd(db=session, live_trading_profile_id=profile_id)

        assert exposure == Decimal("0.0002") * Decimal("50000")


@pytest.mark.asyncio
async def test_inconsistent_sell_evidence_fails_closed_not_silently_zero() -> None:
    """A SELL fill exceeding the tracked BUY quantity is inconsistent
    evidence -- this must fail closed (raise), never silently report zero
    exposure, which would wrongly let a new BUY through."""
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        buy_order_id = uuid.uuid4()
        sell_order_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        session.add(_proof(buy_order_id=buy_order_id, sell_order_id=sell_order_id))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=buy_order_id, side="buy",
            filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.04"), recorded_at=now,
        ))
        session.add(_fill(
            live_trading_profile_id=profile_id, live_crypto_order_id=sell_order_id, side="sell",
            filled_quantity=Decimal("0.0005"),  # more than was ever bought
            fill_price=Decimal("50500"), gross_notional=Decimal("25"), fee_amount=Decimal("0.04"),
            recorded_at=now,
        ))
        await session.flush()

        with pytest.raises(RiskAccountingUnavailableError):
            await compute_controlled_proof_open_exposure_usd(db=session, live_trading_profile_id=profile_id)
