from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.venue_commissioning_run import VenueCommissioningRun
from app.services.live import venue_commissioning as vc


TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"


async def _db_available() -> bool:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def _delete_runs(session: AsyncSession, run_ids: list[uuid.UUID]) -> None:
    if not run_ids:
        return
    await session.execute(
        text("DELETE FROM venue_commissioning_runs WHERE commissioning_run_id = ANY(:ids)"),
        {"ids": run_ids},
    )
    await session.commit()


@pytest.mark.asyncio
async def test_concurrent_activations_cannot_create_two_active_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await _db_available():
        pytest.skip("PostgreSQL unavailable for concurrency integration test")

    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    created_ids: list[uuid.UUID] = []

    async def _ready(**_kwargs):
        return vc.ReadinessResult(would_activate_safely=True, exact_blocker=None, checks=[], existing_active_run="NONE")

    monkeypatch.setattr(vc, "evaluate_readiness", _ready)

    config = vc.CommissioningConfig(
        provider="kraken_spot",
        product_id="BTC-USD",
        environment="production",
        amount=Decimal("5.00"),
        hold_minutes=30,
    )

    async def _activate(actor: str):
        async with session_factory() as session:
            run = await vc.activate_run(db=session, actor=actor, config=config, confirm=True)
            created_ids.append(run.commissioning_run_id)
            return run.commissioning_run_id

    results = await asyncio.gather(_activate("operator:A"), _activate("operator:B"), return_exceptions=True)

    async with session_factory() as verify_session:
        active_count = await verify_session.scalar(
            select(func.count())
            .select_from(VenueCommissioningRun)
            .where(VenueCommissioningRun.status.in_(sorted(vc._ACTIVE_STATES)))
            .where(VenueCommissioningRun.provider == "kraken_spot")
            .where(VenueCommissioningRun.environment == "production")
            .where(VenueCommissioningRun.product_id == "BTC-USD")
        )
        assert int(active_count or 0) == 1

    # One caller may observe a unique-index race; this is fail-closed behavior.
    assert all(not isinstance(item, Exception) or isinstance(item, IntegrityError) for item in results)

    async with session_factory() as cleanup_session:
        await _delete_runs(cleanup_session, list(set(created_ids)))
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_starts_submit_only_one_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await _db_available():
        pytest.skip("PostgreSQL unavailable for concurrency integration test")

    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    submit_calls = {"count": 0}

    async def _submit(**_kwargs):
        submit_calls["count"] += 1
        await asyncio.sleep(0.05)
        return "SUCCESS", None, "provider-buy-1", {}

    async def _reconcile(**_kwargs):
        return "OPEN", None, []

    monkeypatch.setattr(vc, "_submit_order", _submit)
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    async with session_factory() as seed_session:
        run = VenueCommissioningRun(
            status="ACTIVE",
            hold_minutes=30,
            buy_requested_quote_usd=Decimal("5.00"),
            activated_by="operator:seed",
            activated_at=datetime.now(timezone.utc),
            state_payload={"seed": True},
        )
        seed_session.add(run)
        await seed_session.commit()
        run_id = run.commissioning_run_id

    async def _start(actor: str):
        async with session_factory() as session:
            return await vc.start_run(db=session, actor=actor, run_id=run_id, confirm=True)

    await asyncio.gather(_start("operator:A"), _start("operator:B"))

    async with session_factory() as verify_session:
        persisted = await verify_session.scalar(
            select(VenueCommissioningRun).where(VenueCommissioningRun.commissioning_run_id == run_id).limit(1)
        )
        assert persisted is not None
        assert persisted.buy_client_order_id is not None

    assert submit_calls["count"] == 1

    async with session_factory() as cleanup_session:
        await _delete_runs(cleanup_session, [run_id])
    await engine.dispose()


@pytest.mark.asyncio
async def test_start_and_revoke_race_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await _db_available():
        pytest.skip("PostgreSQL unavailable for concurrency integration test")

    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _submit(**_kwargs):
        await asyncio.sleep(0.05)
        return "SUCCESS", None, "provider-buy-2", {}

    async def _reconcile(**_kwargs):
        return "OPEN", None, []

    monkeypatch.setattr(vc, "_submit_order", _submit)
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    async with session_factory() as seed_session:
        run = VenueCommissioningRun(
            status="ACTIVE",
            hold_minutes=30,
            buy_requested_quote_usd=Decimal("5.00"),
            activated_by="operator:seed",
            activated_at=datetime.now(timezone.utc),
            state_payload={"seed": True},
        )
        seed_session.add(run)
        await seed_session.commit()
        run_id = run.commissioning_run_id

    async def _start():
        async with session_factory() as session:
            return await vc.start_run(db=session, actor="operator:start", run_id=run_id, confirm=True)

    async def _revoke():
        async with session_factory() as session:
            return await vc.revoke_run(db=session, actor="operator:revoke", run_id=run_id, confirm=True)

    await asyncio.gather(_start(), _revoke())

    async with session_factory() as verify_session:
        persisted = await verify_session.scalar(
            select(VenueCommissioningRun).where(VenueCommissioningRun.commissioning_run_id == run_id).limit(1)
        )
        assert persisted is not None
        assert persisted.status in {"BUY_RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "REVOKED"}

    async with session_factory() as cleanup_session:
        await _delete_runs(cleanup_session, [run_id])
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_workers_resume_same_due_run_only_one_submits_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    if not await _db_available():
        pytest.skip("PostgreSQL unavailable for concurrency integration test")

    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    submit_calls = {"count": 0}

    async def _submit(**_kwargs):
        side = _kwargs.get("side")
        if side == "SELL":
            submit_calls["count"] += 1
        await asyncio.sleep(0.05)
        return "SUCCESS", None, "provider-sell-1", {}

    async def _reconcile(**_kwargs):
        return "OPEN", None, []

    monkeypatch.setattr(vc, "_submit_order", _submit)
    monkeypatch.setattr(vc, "_reconcile_order", _reconcile)

    async with session_factory() as seed_session:
        now = datetime.now(timezone.utc)
        run = VenueCommissioningRun(
            status="SELL_DUE",
            hold_minutes=30,
            buy_requested_quote_usd=Decimal("5.00"),
            buy_filled_base_btc=Decimal("0.00005"),
            buy_filled_quote_usd=Decimal("5.00"),
            buy_submitted_at=now - timedelta(minutes=31),
            buy_client_order_id="kff-buy-seeded",
            buy_idempotency_key="kff-buy-seeded",
            hold_started_at=now - timedelta(minutes=31),
            hold_due_at=now - timedelta(minutes=1),
            activated_by="operator:seed",
            activated_at=now - timedelta(minutes=31),
            started_by="operator:seed",
            started_at=now - timedelta(minutes=31),
            state_payload={"seed": True},
        )
        seed_session.add(run)
        await seed_session.commit()
        run_id = run.commissioning_run_id

    async def _resume(actor: str):
        async with session_factory() as session:
            return await vc.resume_runs(db=session, actor=actor, limit=1)

    processed = await asyncio.gather(_resume("worker:A"), _resume("worker:B"))

    async with session_factory() as verify_session:
        persisted = await verify_session.scalar(
            select(VenueCommissioningRun).where(VenueCommissioningRun.commissioning_run_id == run_id).limit(1)
        )
        assert persisted is not None
        assert persisted.sell_client_order_id is not None

    assert submit_calls["count"] == 1
    assert sorted(processed) == [0, 1]

    async with session_factory() as cleanup_session:
        await _delete_runs(cleanup_session, [run_id])
    await engine.dispose()