import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.audit_log import AuditLog
from app.models.autonomous_proof_sell_attempt import AutonomousProofSellAttempt
from app.services.orchestration import autonomous_proof_sell_worker as worker


URL = os.getenv("AUTONOMOUS_PROOF_SELL_POSTGRES_TEST_URL")
SA_URL = None if URL is None else URL.replace("postgresql://", "postgresql+asyncpg://", 1)
pytestmark = pytest.mark.skipif(not URL, reason="disposable PostgreSQL URL not configured")


@pytest.mark.asyncio
async def test_migration_constraints_and_one_attempt_campaign_latch():
    conn = await asyncpg.connect(URL)
    try:
        names = set(await conn.fetchval("""
            SELECT array_agg(conname) FROM pg_constraint
            WHERE conrelid = 'autonomous_proof_sell_attempts'::regclass
        """))
        assert {"uq_apsa_custody", "uq_apsa_campaign_attempt", "ck_apsa_stage",
                "ck_apsa_terminal_hard_stop", "ck_apsa_retry_count"} <= names
        await conn.execute("TRUNCATE autonomous_proof_sell_attempts")
        await conn.execute("SET session_replication_role = replica")
        campaign, runtime = uuid4(), uuid4()
        await conn.execute("""
            INSERT INTO autonomous_proof_sell_attempts
              (custody_id,campaign_id,campaign_version,runtime_campaign_id,stage,hard_stopped)
            VALUES ($1,$2,1,$3,'SELECTED',false)
        """, uuid4(), campaign, runtime)
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute("""
                INSERT INTO autonomous_proof_sell_attempts
                  (custody_id,campaign_id,campaign_version,runtime_campaign_id,stage,hard_stopped)
                VALUES ($1,$2,1,$3,'SELECTED',false)
            """, uuid4(), campaign, runtime)
        assert await conn.fetchval("SELECT count(*) FROM autonomous_proof_sell_attempts") == 1
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("""
                INSERT INTO autonomous_proof_sell_attempts
                  (custody_id,campaign_id,campaign_version,runtime_campaign_id,stage,hard_stopped)
                VALUES ($1,$2,1,$3,'TERMINAL',false)
            """, uuid4(), uuid4(), uuid4())
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_concurrent_campaign_selection_has_one_winner():
    setup = await asyncpg.connect(URL)
    await setup.execute("TRUNCATE autonomous_proof_sell_attempts")
    await setup.close()
    campaign, runtime = uuid4(), uuid4()
    first, second = await asyncpg.connect(URL), await asyncpg.connect(URL)
    try:
        await first.execute("SET session_replication_role = replica")
        await second.execute("SET session_replication_role = replica")
        transaction = first.transaction(); await transaction.start()
        await first.execute("""
            INSERT INTO autonomous_proof_sell_attempts
              (custody_id,campaign_id,campaign_version,runtime_campaign_id,stage,hard_stopped)
            VALUES ($1,$2,1,$3,'SELECTED',false)
        """, uuid4(), campaign, runtime)
        contender = asyncio.create_task(second.execute("""
            INSERT INTO autonomous_proof_sell_attempts
              (custody_id,campaign_id,campaign_version,runtime_campaign_id,stage,hard_stopped)
            VALUES ($1,$2,1,$3,'SELECTED',false)
        """, uuid4(), campaign, runtime))
        await asyncio.sleep(0.05)
        assert not contender.done()
        await transaction.commit()
        with pytest.raises(asyncpg.UniqueViolationError):
            await contender
        assert await first.fetchval("SELECT count(*) FROM autonomous_proof_sell_attempts") == 1
    finally:
        await first.close(); await second.close()


@pytest.mark.asyncio
async def test_stage_transition_rolls_back_as_one_database_unit():
    conn = await asyncpg.connect(URL)
    try:
        await conn.execute("TRUNCATE autonomous_proof_sell_attempts")
        await conn.execute("SET session_replication_role = replica")
        attempt_id = await conn.fetchval("""
            INSERT INTO autonomous_proof_sell_attempts
              (custody_id,campaign_id,campaign_version,runtime_campaign_id,stage,hard_stopped)
            VALUES ($1,$2,1,$3,'SELECTED',false) RETURNING attempt_id
        """, uuid4(), uuid4(), uuid4())
        transaction = conn.transaction(); await transaction.start()
        await conn.execute("""
            UPDATE autonomous_proof_sell_attempts SET stage='EVALUATED'
            WHERE attempt_id=$1
        """, attempt_id)
        await transaction.rollback()
        assert await conn.fetchval(
            "SELECT stage FROM autonomous_proof_sell_attempts WHERE attempt_id=$1", attempt_id,
        ) == "SELECTED"
    finally:
        await conn.close()


async def _seed_real_selection_scope(engine):
    campaign, runtime = uuid4(), uuid4()
    custodies = [uuid4(), uuid4()]
    async with engine.begin() as conn:
        await conn.execute(text("SET session_replication_role=replica"))
        await conn.execute(text("TRUNCATE autonomous_proof_sell_attempts, autonomous_position_custodies CASCADE"))
        await conn.execute(text("""INSERT INTO capital_campaign_definitions (
          campaign_id,version,name,owner_identity,status,capital_budget,remaining_unallocated_capital,
          base_currency,allowed_asset_classes,allowed_venues,allowed_instruments,campaign_modes,
          maximum_open_positions,maximum_position_size,minimum_position_size,maximum_total_exposure,
          profitability_policy_id,profitability_policy_version,risk_policy_id,risk_policy_version
        ) VALUES (:campaign,1,'proof-sell-pg','test','READY',25,25,'USD','["crypto"]','["kraken_spot"]',
          '["BTC-USD"]','[]',2,10,1,20,'test','1','test','1')
        ON CONFLICT (campaign_id,version) DO NOTHING"""), {"campaign": campaign})
        await conn.execute(text("""INSERT INTO capital_campaigns (
          uuid,owner,name,status,campaign_type,definition_campaign_id,definition_version,
          starting_capital,current_equity
        ) VALUES (:runtime,'test','proof-sell-pg','READY','definition_pinned_runtime',NULL,NULL,25,25)"""),
                           {"runtime": runtime})
        for index, custody in enumerate(custodies):
            values = {name: uuid4() for name in (
                "cycle1", "cycle2", "mandate", "mandate_version", "decision", "package",
                "activation", "claim", "order", "reconciliation", "account", "profile", "connection",
            )}
            values.update(custody=custody, campaign=campaign, runtime=runtime, index=index)
            await conn.execute(text("""INSERT INTO autonomous_position_custodies (
              custody_id,custody_state,originating_autonomous_cycle_id,originating_campaign_cycle_id,
              campaign_id,campaign_version,runtime_campaign_id,mandate_id,mandate_version_id,
              decision_record_id,buy_package_id,buy_activation_id,buy_claim_id,buy_live_order_id,
              buy_reconciliation_event_id,paper_account_id,live_trading_profile_id,exchange_connection_id,
              provider,environment,product,original_acquired_quantity,observed_remaining_quantity,
              autonomous_origin,provenance_classification,proof_eligible,continuing_exit_authority_state,
              audit_metadata,created_at,updated_at
            ) VALUES (:custody,'ACTIVE',:cycle1,:cycle2,:campaign,1,:runtime,:mandate,:mandate_version,
              :decision,:package,:activation,:claim,:order,:reconciliation,:account,:profile,:connection,
              'kraken_spot','production','BTC-USD',0.00008,0.00008,true,
              'SCHEDULED_PRODUCTION_AUTONOMOUS',true,'UNARMED',
              jsonb_build_object('latest_exit_evaluation',jsonb_build_object(
                'disposition','EXIT_RECOMMENDED','price_fresh',true)),
              now() + (:index * interval '1 second'),now())"""), values)
        await conn.execute(text("SET session_replication_role=origin"))
    return campaign, runtime, custodies


class _Settings:
    autonomous_proof_sell_worker_enabled = True
    autonomous_position_exit_submission_enabled = False

    def __init__(self, campaign, runtime):
        self.autonomous_proof_sell_campaign_id = campaign
        self.autonomous_proof_sell_campaign_version = 1
        self.autonomous_proof_sell_runtime_campaign_id = runtime


@pytest.mark.asyncio
async def test_real_asyncsession_coordinator_selection_has_one_winner_and_no_replacement(monkeypatch):
    engine = create_async_engine(SA_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        campaign, runtime, custodies = await _seed_real_selection_scope(engine)
        monkeypatch.setattr(worker, "get_settings", lambda: _Settings(campaign, runtime))
        real_locked_attempt = worker._locked_attempt
        arrived = 0
        release = asyncio.Event()

        async def synchronized_lookup(db, scope):
            nonlocal arrived
            value = await real_locked_attempt(db, scope)
            arrived += 1
            if arrived == 2:
                release.set()
            await release.wait()
            return value

        monkeypatch.setattr(worker, "_locked_attempt", synchronized_lookup)

        async def select_once():
            async with sessions() as session:
                result = await worker.advance_one_autonomous_proof_sell_stage(
                    db=session, cadence_seconds=30,
                )
                await session.commit()
                return result

        results = await asyncio.gather(select_once(), select_once())
        assert {result.action for result in results} <= {"selected", "duplicate_safe", "no_eligible_custody"}
        async with sessions() as verify:
            attempts = list((await verify.scalars(select(AutonomousProofSellAttempt))).all())
            assert len(attempts) == 1
            winner = attempts[0]
            assert winner.custody_id == custodies[0]
            attempt_id = winner.attempt_id

        monkeypatch.setattr(worker, "_locked_attempt", real_locked_attempt)

        # Replay advances only the winning custody's persisted evidence; it
        # cannot use the second eligible row as a replacement.
        async with sessions() as replay:
            result = await worker.advance_one_autonomous_proof_sell_stage(
                db=replay, cadence_seconds=30,
            )
            await replay.commit()
            assert result.attempt_id == attempt_id and result.stage == "EVALUATED"
            assert await replay.scalar(select(func.count()).select_from(AutonomousProofSellAttempt)) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_flushed_stage_savepoint_rolls_back_artifact_and_persists_retry(monkeypatch):
    engine = create_async_engine(SA_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        campaign, runtime, _ = await _seed_real_selection_scope(engine)
        monkeypatch.setattr(worker, "get_settings", lambda: _Settings(campaign, runtime))
        async with sessions() as session:
            await worker.advance_one_autonomous_proof_sell_stage(db=session, cadence_seconds=30)
            await session.commit()
            attempt = await session.scalar(select(AutonomousProofSellAttempt))
            attempt.stage = "AUTHORIZED"
            await session.commit()
            attempt_id = attempt.attempt_id

        async def dirty_failure(**_kwargs):
            # This valid row proves a successful flush is also undone, then an
            # invalid row puts PostgreSQL into a failed savepoint state.
            _kwargs["db"].add(AuditLog(actor="test", action="must_rollback", entity_type="proof_sell"))
            await _kwargs["db"].flush()
            _kwargs["db"].add(AuditLog(actor=None, action="invalid", entity_type="proof_sell"))
            await _kwargs["db"].flush()

        monkeypatch.setattr(worker, "construct_exit_paperwork", dirty_failure)
        async with sessions() as session:
            result = await worker.advance_one_autonomous_proof_sell_stage(db=session, cadence_seconds=30)
            await session.commit()
            assert result.action == "stage_failed" and result.stage == "AUTHORIZED"

        async with sessions() as verify:
            attempt = await verify.get(AutonomousProofSellAttempt, attempt_id)
            assert attempt.stage == "AUTHORIZED" and attempt.retry_count == 1
            assert attempt.blocker == "IntegrityError:stage_failed"
            assert await verify.scalar(select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "must_rollback")) == 0
    finally:
        await engine.dispose()
