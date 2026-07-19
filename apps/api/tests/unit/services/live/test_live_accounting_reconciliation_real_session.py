from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.services.live.accounting_reconciliation import (
    record_live_fill_reconciliation,
    record_live_order_reconciliation,
)
from app.services.live.contracts import (
    LiveFillReconciliationRequest,
    LiveOrderReconciliationRequest,
)

# Production uses Postgres-only JSONB/UUID column types and the
# now()/gen_random_uuid() server defaults. These compiler overrides and SQLite
# user-defined functions let the REAL app models run against a REAL, in-memory
# SQLAlchemy AsyncSession (sqlite+aiosqlite) so this test exercises genuine
# transaction/autobegin semantics instead of a hand-rolled fake session.


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    # StaticPool keeps a single underlying connection alive for the whole engine --
    # required for sqlite ":memory:" so the DDL and the test session share one
    # database instead of each pool checkout getting its own blank in-memory db.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))

    async with engine.begin() as conn:
        await conn.run_sync(
            LiveExecutionEvent.metadata.create_all,
            tables=[
                LiveExecutionEvent.__table__,
                LiveReconciliationEvent.__table__,
                LiveAccountingRecord.__table__,
            ],
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _seed_execution_source(session: AsyncSession, *, profile_id: uuid.UUID) -> LiveExecutionEvent:
    now = datetime.now(timezone.utc)
    source = LiveExecutionEvent(
        id=uuid.uuid4(),
        idempotency_key=f"src-{uuid.uuid4()}",
        event_hash=f"hash-{uuid.uuid4()}",
        live_trading_profile_id=profile_id,
        sequence_number=1,
        event_type="execution_intent_created",
        provider_name="kraken_spot",
        risk_decision_id=uuid.uuid4(),
        approval_event_id=uuid.uuid4(),
        audit_correlation_id="audit-1",
        operating_mode="live",
        paper_default_mode=True,
        risk_authority_model="risk_engine_final",
        event_payload={"a": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )
    session.add(source)
    # This is the production-equivalent autobegin trigger: reconcile_live_order_and_fills
    # always loads/creates the execution source (a real SELECT/INSERT+flush) before it
    # ever reaches record_live_order_reconciliation, so by production time the session's
    # transaction is already autobegun via SQLAlchemy -- exactly reproduced here.
    await session.flush()
    return source


@pytest.mark.asyncio
async def test_record_live_order_reconciliation_joins_real_autobegun_transaction() -> None:
    """Production-faithful regression for the exact reported incident.

    Uses a REAL AsyncSession (sqlite+aiosqlite), not AsyncMock/a hand-rolled fake.
    A preceding INSERT+flush autobegins a genuine SQLAlchemy transaction, mirroring
    what always happens earlier in reconcile_live_order_and_fills. If
    record_live_order_reconciliation (or the join_or_begin_transaction helper it
    uses) ever called db.begin() unconditionally instead of joining, this raises
    the exact `InvalidRequestError: A transaction is already begun on this Session.`
    reported from production.
    """
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        source = await _seed_execution_source(session, profile_id=profile_id)

        assert session.in_transaction() is True  # autobegin already active, like production

        request = LiveOrderReconciliationRequest(
            live_trading_profile_id=profile_id,
            source_execution_event_id=source.id,
            provider_name="kraken_spot",
            provider_order_id=None,
            client_order_id="client-order-xyz",
            reconciliation_status="reconciliation_required",
            live_crypto_order_id=uuid.uuid4(),
            capital_campaign_id=None,
            provider_recorded_at=None,
            requested_by="operator:human",
            provenance_metadata={"reason": "provider_order_not_found"},
            idempotency_key=f"lco-reconcile:{uuid.uuid4()}:missing",
        )

        with patch.object(session, "begin", wraps=session.begin) as begin_spy:
            result = await record_live_order_reconciliation(db=session, request=request)
            begin_spy.assert_not_called()  # must join, never open a second transaction

        assert result.status == "recorded"
        assert session.in_transaction() is True

        await session.commit()

        persisted = await session.scalar(
            select(LiveReconciliationEvent).where(
                LiveReconciliationEvent.idempotency_key == request.idempotency_key
            )
        )
        assert persisted is not None


@pytest.mark.asyncio
async def test_record_live_order_and_fill_reconciliation_sequential_calls_stay_atomic() -> None:
    """Extends the core regression to the full order + fill sequence, matching
    reconcile_live_order_and_fills's real call shape, on one continuously-open
    real transaction end to end.
    """
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        live_crypto_order_id = uuid.uuid4()
        source = await _seed_execution_source(session, profile_id=profile_id)

        order_result = await record_live_order_reconciliation(
            db=session,
            request=LiveOrderReconciliationRequest(
                live_trading_profile_id=profile_id,
                source_execution_event_id=source.id,
                provider_name="kraken_spot",
                provider_order_id="OAXUZJ-7WRL5-NPFWYA",
                client_order_id="client-order-xyz",
                reconciliation_status="filled",
                live_crypto_order_id=live_crypto_order_id,
                capital_campaign_id=None,
                provider_recorded_at=None,
                requested_by="operator:human",
                provenance_metadata={"provider_status": "closed"},
                idempotency_key=f"lco-reconcile:{live_crypto_order_id}:status",
            ),
        )
        assert order_result.status == "recorded"
        assert session.in_transaction() is True

        fill_result = await record_live_fill_reconciliation(
            db=session,
            request=LiveFillReconciliationRequest(
                live_trading_profile_id=profile_id,
                source_execution_event_id=source.id,
                provider_name="kraken_spot",
                provider_order_id="OAXUZJ-7WRL5-NPFWYA",
                provider_fill_id="fill-1",
                client_order_id="client-order-xyz",
                symbol="BTC-USD",
                side="buy",
                fill_quantity="0.00002",
                cumulative_filled_quantity="0.00002",
                order_quantity="0.00002",
                fill_price="90000.00",
                fee_amount="0.004",
                fee_currency="USD",
                live_crypto_order_id=live_crypto_order_id,
                capital_campaign_id=None,
                provider_fill_timestamp=datetime.now(timezone.utc),
                provider_recorded_at=datetime.now(timezone.utc),
                requested_by="operator:human",
                provenance_metadata={"fill_index": 0},
                idempotency_key=f"lco-reconcile:{live_crypto_order_id}:fill-1",
            ),
        )
        assert fill_result.status == "recorded"
        assert session.in_transaction() is True

        await session.commit()


@pytest.mark.asyncio
async def test_unconditional_begin_reproduces_the_production_defect() -> None:
    """Proves this regression test has real teeth.

    A naive persistence helper shape -- calling db.begin() unconditionally,
    which is what record_live_order_reconciliation looked like before the
    join-or-begin fix -- reproduces the EXACT production error the moment the
    session has already autobegun a transaction from a preceding operation.
    The fixed record_live_order_reconciliation (tested above, in the identical
    autobegun state) does not raise this.
    """
    async with _real_session() as session:
        profile_id = uuid.uuid4()
        await _seed_execution_source(session, profile_id=profile_id)
        assert session.in_transaction() is True

        with pytest.raises(InvalidRequestError, match="A transaction is already begun"):
            async with session.begin():
                pass
