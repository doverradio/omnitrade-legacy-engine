from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import TextClause

from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.risk.equity_evidence import _count_reconciliation_uncertainty

# Production uses Postgres-only JSONB/UUID column types and the
# now()/gen_random_uuid() server defaults. These compiler overrides let the
# REAL app models (and the REAL _count_reconciliation_uncertainty query --
# including its GROUP BY/subquery/join) run against a REAL, in-memory
# SQLAlchemy AsyncSession (sqlite+aiosqlite), instead of asserting against a
# hand-rolled fake that could hide a query-shape defect.


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw) -> str:
    return "JSON"


@compiles(PG_UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw) -> str:
    return "CHAR(36)"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _record) -> None:
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: str(uuid.uuid4()))

    tables = [
        LiveTradingProfile.__table__,
        LiveReconciliationEvent.__table__,
        LiveCryptoOrder.__table__,
    ]
    # Postgres accepts `DEFAULT gen_random_uuid()` / `DEFAULT now()` bare and
    # Postgres-only casts like `DEFAULT '{}'::jsonb`; SQLite's CREATE TABLE
    # grammar needs a parenthesized expression for a function-call default
    # and has no `::type` cast syntax. Rewrite just for this in-memory
    # schema so the real model definitions stay untouched.
    for table in tables:
        for column in table.columns:
            default = column.server_default
            if isinstance(default, DefaultClause) and isinstance(default.arg, TextClause):
                raw = default.arg.text.strip().split("::", 1)[0]
                if raw.endswith("()") and not raw.startswith("("):
                    raw = f"({raw})"
                column.server_default = DefaultClause(text(raw))

    try:
        async with engine.begin() as conn:
            await conn.run_sync(LiveTradingProfile.metadata.create_all, tables=tables)

        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _seed_profile(session: AsyncSession, *, paper_account_id: uuid.UUID) -> LiveTradingProfile:
    profile = LiveTradingProfile(
        id=uuid.uuid4(),
        paper_account_id=paper_account_id,
        provenance_metadata={},
    )
    session.add(profile)
    await session.flush()
    return profile


def _event(
    *,
    profile_id: uuid.UUID,
    sequence_number: int,
    reconciliation_status: str,
    live_crypto_order_id: uuid.UUID | None,
    event_type: str = "order_reconciled",
) -> LiveReconciliationEvent:
    now = datetime.now(timezone.utc)
    return LiveReconciliationEvent(
        id=uuid.uuid4(),
        idempotency_key=f"key-{uuid.uuid4()}",
        event_hash=f"hash-{uuid.uuid4()}",
        live_trading_profile_id=profile_id,
        live_crypto_order_id=live_crypto_order_id,
        capital_campaign_id=None,
        source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created",
        sequence_number=sequence_number,
        event_type=event_type,
        reconciliation_status=reconciliation_status,
        provider_name="kraken_spot",
        provider_order_id="OAXUZJ-7WRL5-NPFWYA",
        provider_fill_id=None,
        event_payload={"status": reconciliation_status},
        provenance={"reason": "test"},
        immutable_contract_version="v1",
        provider_recorded_at=None,
        recorded_at=now,
    )


@pytest.mark.asyncio
async def test_multiple_historical_unresolved_events_for_one_order_count_once() -> None:
    """Regression for the reported incident: order c142df4e had 3 append-only
    events (partially_filled, partially_filled, reconciliation_required), all
    individually unresolved-status, but they describe ONE order's evolving
    state, not three distinct problems. Only the latest (highest
    sequence_number) event per order should count.
    """
    async with _real_session() as session:
        account_id = uuid.uuid4()
        profile = await _seed_profile(session, paper_account_id=account_id)
        order_id = uuid.uuid4()

        session.add_all(
            [
                _event(profile_id=profile.id, sequence_number=1, reconciliation_status="partially_filled", live_crypto_order_id=order_id, event_type="order_reconciled"),
                _event(profile_id=profile.id, sequence_number=2, reconciliation_status="partially_filled", live_crypto_order_id=order_id, event_type="fill_reconciled"),
                _event(profile_id=profile.id, sequence_number=3, reconciliation_status="reconciliation_required", live_crypto_order_id=order_id, event_type="order_reconciled"),
            ]
        )
        await session.flush()

        unresolved_count, _unknown_count = await _count_reconciliation_uncertainty(
            db=session, paper_account_id=account_id
        )
        assert unresolved_count == 1


@pytest.mark.asyncio
async def test_superseded_unresolved_event_does_not_count_once_order_resolves() -> None:
    """An order that WAS unresolved but has since reached a resolved terminal
    status at a later sequence number must not be counted -- the append-only
    history is immutable, but "unresolved" evaluation is about current state.
    """
    async with _real_session() as session:
        account_id = uuid.uuid4()
        profile = await _seed_profile(session, paper_account_id=account_id)
        order_id = uuid.uuid4()

        session.add_all(
            [
                _event(profile_id=profile.id, sequence_number=1, reconciliation_status="reconciliation_required", live_crypto_order_id=order_id),
                _event(profile_id=profile.id, sequence_number=2, reconciliation_status="filled", live_crypto_order_id=order_id, event_type="fill_reconciled"),
            ]
        )
        await session.flush()

        unresolved_count, _unknown_count = await _count_reconciliation_uncertainty(
            db=session, paper_account_id=account_id
        )
        assert unresolved_count == 0


@pytest.mark.asyncio
async def test_two_distinct_unresolved_orders_count_as_two() -> None:
    """Two genuinely different orders, each currently unresolved, must both
    be counted -- the fix must not collapse distinct problems into one.
    """
    async with _real_session() as session:
        account_id = uuid.uuid4()
        profile = await _seed_profile(session, paper_account_id=account_id)
        order_a = uuid.uuid4()
        order_b = uuid.uuid4()

        session.add_all(
            [
                _event(profile_id=profile.id, sequence_number=1, reconciliation_status="reconciliation_required", live_crypto_order_id=order_a),
                _event(profile_id=profile.id, sequence_number=2, reconciliation_status="open", live_crypto_order_id=order_b),
            ]
        )
        await session.flush()

        unresolved_count, _unknown_count = await _count_reconciliation_uncertainty(
            db=session, paper_account_id=account_id
        )
        assert unresolved_count == 2


@pytest.mark.asyncio
async def test_wrong_account_id_returns_zero_without_examining_events() -> None:
    """Regression for the root cause behind the reported inconsistency:
    querying by an account_id that has no matching LiveTradingProfile row
    (e.g. AutonomousCapitalMandate.paper_account_id diverging from the
    profile's own paper_account_id) must return (0, 0) -- this is existing,
    correct fail-open-on-no-profile behavior, not something this fix changes.
    Included here so the account-identity pitfall is pinned by a test.
    """
    async with _real_session() as session:
        real_account_id = uuid.uuid4()
        unrelated_account_id = uuid.uuid4()
        profile = await _seed_profile(session, paper_account_id=real_account_id)
        session.add(
            _event(profile_id=profile.id, sequence_number=1, reconciliation_status="reconciliation_required", live_crypto_order_id=uuid.uuid4())
        )
        await session.flush()

        unresolved_count, unknown_count = await _count_reconciliation_uncertainty(
            db=session, paper_account_id=unrelated_account_id
        )
        assert (unresolved_count, unknown_count) == (0, 0)

        correct_count, _ = await _count_reconciliation_uncertainty(db=session, paper_account_id=real_account_id)
        assert correct_count == 1


@pytest.mark.asyncio
async def test_resolved_only_history_counts_zero() -> None:
    async with _real_session() as session:
        account_id = uuid.uuid4()
        profile = await _seed_profile(session, paper_account_id=account_id)
        order_id = uuid.uuid4()

        session.add(
            _event(profile_id=profile.id, sequence_number=1, reconciliation_status="filled", live_crypto_order_id=order_id, event_type="fill_reconciled")
        )
        await session.flush()

        unresolved_count, _unknown_count = await _count_reconciliation_uncertainty(
            db=session, paper_account_id=account_id
        )
        assert unresolved_count == 0
