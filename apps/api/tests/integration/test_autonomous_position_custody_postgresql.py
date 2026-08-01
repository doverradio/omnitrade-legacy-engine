from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.orchestration.autonomous_position_exit_evaluation import discover_due_custodies


TEST_DATABASE_URL = os.getenv("OMNITRADE_CUSTODY_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="OMNITRADE_CUSTODY_TEST_DATABASE_URL is required for disposable PostgreSQL custody tests",
    ),
]

_INSERT_CUSTODY = text("""
    INSERT INTO autonomous_position_custodies (
        custody_id, custody_state, originating_autonomous_cycle_id,
        originating_campaign_cycle_id, campaign_id, campaign_version,
        runtime_campaign_id, mandate_id, mandate_version_id, decision_record_id,
        buy_package_id, buy_activation_id, buy_claim_id, buy_live_order_id,
        buy_reconciliation_event_id, paper_account_id, live_trading_profile_id,
        exchange_connection_id, provider, environment, product,
        original_acquired_quantity, observed_remaining_quantity,
        provenance_classification
    ) VALUES (
        :custody_id, :custody_state, :origin_cycle_id, :campaign_cycle_id,
        :campaign_id, 1, :runtime_campaign_id, :mandate_id, :mandate_version_id,
        :decision_id, :package_id, :activation_id, :claim_id, :order_id,
        :reconciliation_id, :account_id, :profile_id, :connection_id,
        'kraken_spot', 'production', :product, 0.00008, 0.00008,
        'SCHEDULED_PRODUCTION_AUTONOMOUS'
    )
""")


def _custody(*, profile_id: uuid.UUID, product: str = "BTC-USD", state: str = "ACTIVE") -> dict:
    return {
        "custody_id": uuid.uuid4(), "custody_state": state,
        "origin_cycle_id": uuid.uuid4(), "campaign_cycle_id": uuid.uuid4(),
        "campaign_id": uuid.uuid4(), "runtime_campaign_id": uuid.uuid4(),
        "mandate_id": uuid.uuid4(), "mandate_version_id": uuid.uuid4(),
        "decision_id": uuid.uuid4(), "package_id": uuid.uuid4(),
        "activation_id": uuid.uuid4(), "claim_id": uuid.uuid4(),
        "order_id": uuid.uuid4(), "reconciliation_id": uuid.uuid4(),
        "account_id": uuid.uuid4(), "profile_id": profile_id,
        "connection_id": uuid.uuid4(), "product": product,
    }


async def _reset(connection) -> None:
    await connection.execute(text("SET session_replication_role = replica"))
    await connection.execute(text("TRUNCATE autonomous_position_exit_authorities, autonomous_position_custodies, autonomous_execution_claims"))
    await connection.execute(text("SET session_replication_role = origin"))


async def test_postgresql_schema_contains_expected_constraints_and_index() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "20260731_0058"
            constraints = set((await connection.execute(text("""
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_schema='public' AND table_name='autonomous_position_custodies'
            """))).scalars())
            assert {
                "ck_apc_state", "ck_apc_autonomous_origin",
                "ck_apc_original_quantity_positive", "ck_apc_remaining_quantity_nonnegative",
                "ck_apc_continuing_authority", "ck_apc_proof_disqualification",
                "uq_apc_buy_claim", "uq_apc_buy_package", "uq_apc_buy_order",
                "fk_apc_campaign_definition",
            } <= constraints
            foreign_keys = await connection.scalar(text("""
                SELECT count(*) FROM information_schema.table_constraints
                WHERE table_schema='public' AND table_name='autonomous_position_custodies'
                  AND constraint_type='FOREIGN KEY'
            """))
            assert foreign_keys == 19
            index_definition = await connection.scalar(text("""
                SELECT indexdef FROM pg_indexes
                WHERE schemaname='public' AND tablename='autonomous_position_custodies'
                  AND indexname='uq_apc_nonterminal_position_scope'
            """))
            assert "UNIQUE INDEX" in index_definition
            assert "live_trading_profile_id, product" in index_definition
            assert "HANDOFF_PENDING" in index_definition and "BLOCKED" in index_definition
    finally:
        await engine.dispose()


async def test_postgresql_nonterminal_exclusivity_terminal_reuse_and_replay_identity() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    profile_id = uuid.uuid4()
    first = _custody(profile_id=profile_id)
    second = _custody(profile_id=profile_id)
    try:
        async with engine.begin() as connection:
            await _reset(connection)
            await connection.execute(text("SET session_replication_role = replica"))
            await connection.execute(_INSERT_CUSTODY, first)
        async with engine.begin() as connection:
            await connection.execute(text("SET session_replication_role = replica"))
            with pytest.raises(IntegrityError):
                await connection.execute(_INSERT_CUSTODY, second)
        async with engine.begin() as connection:
            await connection.execute(text("SET session_replication_role = replica"))
            await connection.execute(text("""
                UPDATE autonomous_position_custodies
                SET custody_state='CLOSED', observed_remaining_quantity=0, terminal_at=now()
                WHERE custody_id=:custody_id
            """), {"custody_id": first["custody_id"]})
            await connection.execute(_INSERT_CUSTODY, second)
            replayed = await connection.scalar(text("""
                SELECT custody_id FROM autonomous_position_custodies WHERE buy_claim_id=:claim_id
            """), {"claim_id": second["claim_id"]})
            assert replayed == second["custody_id"]
    finally:
        await engine.dispose()


async def test_postgresql_concurrent_nonterminal_acquisition_has_one_winner() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    profile_id = uuid.uuid4()
    rows = [_custody(profile_id=profile_id), _custody(profile_id=profile_id)]
    async with engine.begin() as connection:
        await _reset(connection)

    async def attempt(row: dict) -> str:
        try:
            async with engine.begin() as connection:
                await connection.execute(text("SET session_replication_role = replica"))
                await connection.execute(_INSERT_CUSTODY, row)
            return "committed"
        except IntegrityError:
            return "conflict"

    try:
        assert sorted(await asyncio.gather(*(attempt(row) for row in rows))) == ["committed", "conflict"]
        async with engine.connect() as connection:
            count = await connection.scalar(text("""
                SELECT count(*) FROM autonomous_position_custodies
                WHERE live_trading_profile_id=:profile_id AND product='BTC-USD'
            """), {"profile_id": profile_id})
            assert count == 1
    finally:
        await engine.dispose()


async def test_postgresql_uniqueness_and_audit_failures_roll_back_complete_handoff() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    profile_id = uuid.uuid4()
    incumbent = _custody(profile_id=profile_id)
    conflicting = _custody(profile_id=profile_id)
    audit_failure = _custody(profile_id=uuid.uuid4())
    claim_ids = [conflicting["claim_id"], audit_failure["claim_id"]]
    claim_insert = text("""
        INSERT INTO autonomous_execution_claims (
            claim_id, package_id, activation_id, campaign_id, campaign_version,
            mandate_id, mandate_version_id, account_id, profile_id, connection_id,
            provider, environment, product, side, claim_status, claimed_at,
            claim_owner, attempt_count
        ) VALUES (
            :claim_id, :package_id, :activation_id, :campaign_id, 1,
            :mandate_id, :mandate_version_id, :account_id, :profile_id,
            :connection_id, 'kraken_spot', 'production', 'BTC-USD', 'BUY',
            'RECONCILIATION_REQUIRED', now(), 'test:postgresql', 1
        )
    """)
    try:
        async with engine.begin() as connection:
            await _reset(connection)
            await connection.execute(text("SET session_replication_role = replica"))
            await connection.execute(_INSERT_CUSTODY, incumbent)
            for row in (conflicting, audit_failure):
                await connection.execute(claim_insert, row)

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(text("SET session_replication_role = replica"))
                await connection.execute(text("""
                    UPDATE autonomous_execution_claims SET claim_status='BUY_RECONCILED'
                    WHERE claim_id=:claim_id
                """), {"claim_id": conflicting["claim_id"]})
                await connection.execute(_INSERT_CUSTODY, conflicting)

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(text("SET session_replication_role = replica"))
                await connection.execute(_INSERT_CUSTODY, audit_failure)
                await connection.execute(text("""
                    INSERT INTO audit_log (actor, action, entity_type)
                    VALUES (NULL, 'autonomous_position_custody.established', 'autonomous_position_custody')
                """))

        async with engine.connect() as connection:
            statuses = dict((await connection.execute(text("""
                SELECT claim_id, claim_status FROM autonomous_execution_claims
                WHERE claim_id = ANY(:claim_ids)
            """), {"claim_ids": claim_ids})).all())
            assert statuses == {claim_id: "RECONCILIATION_REQUIRED" for claim_id in claim_ids}
            failed_rows = await connection.scalar(text("""
                SELECT count(*) FROM autonomous_position_custodies
                WHERE custody_id IN (:conflicting_id, :audit_failure_id)
            """), {
                "conflicting_id": conflicting["custody_id"],
                "audit_failure_id": audit_failure["custody_id"],
            })
            assert failed_rows == 0
    finally:
        await engine.dispose()


async def test_postgresql_exit_evaluation_claims_skip_locked_and_recover_after_rollback() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    rows = [_custody(profile_id=uuid.uuid4()), _custody(profile_id=uuid.uuid4())]
    now = datetime.now(timezone.utc)
    try:
        async with engine.begin() as connection:
            await _reset(connection)
            await connection.execute(text("SET session_replication_role = replica"))
            for row in rows:
                await connection.execute(_INSERT_CUSTODY, row)

        async with AsyncSession(engine, expire_on_commit=False) as first_session:
            first = await discover_due_custodies(db=first_session, now=now, limit=1)
            async with AsyncSession(engine, expire_on_commit=False) as second_session:
                second = await discover_due_custodies(db=second_session, now=now, limit=1)
                assert len(first) == len(second) == 1
                assert first[0].custody_id != second[0].custody_id
                await second_session.rollback()
            await first_session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as restarted_session:
            rediscovered = await discover_due_custodies(db=restarted_session, now=now, limit=2)
            assert {item.custody_id for item in rediscovered} == {row["custody_id"] for row in rows}
            await restarted_session.rollback()
    finally:
        await engine.dispose()
