from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.getenv("OMNITRADE_CUSTODY_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="disposable PostgreSQL URL required")]

INSERT = text("""
INSERT INTO autonomous_position_exit_authorities (
 authority_id, authority_state, custody_id, live_trading_profile_id, paper_account_id,
 exchange_connection_id, provider, environment, product, originating_buy_claim_id,
 originating_reconciliation_event_id, provenance_classification, proof_eligible,
 classification, evaluation_at, evaluation_integrity_hash,
 authoritative_quantity_at_issuance, maximum_sell_quantity, policy_evidence,
 risk_evidence, issued_at, expires_at
) VALUES (
 :authority_id,'ARMED',:custody_id,:profile_id,:account_id,:connection_id,
 'kraken_spot','production','BTC-USD',:claim_id,:reconciliation_id,
 'SCHEDULED_PRODUCTION_AUTONOMOUS',true,'PROOF_ELIGIBLE_AUTONOMOUS',
 :now,:hash,0.00008,0.00008,'{}','{}',:now,:expires
)
""")


def _row():
    now = datetime.now(timezone.utc)
    return dict(authority_id=uuid.uuid4(), custody_id=uuid.uuid4(), profile_id=uuid.uuid4(),
                account_id=uuid.uuid4(), connection_id=uuid.uuid4(), claim_id=uuid.uuid4(),
                reconciliation_id=uuid.uuid4(), now=now, expires=now + timedelta(minutes=15), hash=uuid.uuid4().hex)


async def _reset(conn):
    await conn.execute(text("SET session_replication_role=replica"))
    await conn.execute(text("TRUNCATE autonomous_position_exit_authorities"))


async def test_postgresql_reservation_requires_complete_unique_paperwork_binding():
    engine = create_async_engine(URL); row = _row()
    try:
        async with engine.begin() as conn:
            await _reset(conn); await conn.execute(INSERT, row)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(text("UPDATE autonomous_position_exit_authorities SET authority_state='RESERVED', reserved_decision_id=:decision WHERE authority_id=:id"), {"id": row["authority_id"], "decision": uuid.uuid4()})
        decision_id, package_id = uuid.uuid4(), uuid.uuid4()
        async with engine.begin() as conn:
            await conn.execute(text("SET session_replication_role=replica"))
            await conn.execute(text("""UPDATE autonomous_position_exit_authorities
                SET authority_state='RESERVED', reserved_decision_id=:decision, reserved_package_id=:package
                WHERE authority_id=:id"""), {"id": row["authority_id"], "decision": decision_id, "package": package_id})
        second = _row()
        async with engine.begin() as conn:
            await conn.execute(INSERT, second)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(text("""UPDATE autonomous_position_exit_authorities
                    SET authority_state='RESERVED', reserved_decision_id=:decision, reserved_package_id=:package
                    WHERE authority_id=:id"""), {"id": second["authority_id"], "decision": decision_id, "package": uuid.uuid4()})
    finally:
        await engine.dispose()


async def test_postgresql_concurrent_reservation_has_one_winner_and_failure_rolls_back():
    engine = create_async_engine(URL); row = _row()
    try:
        async with engine.begin() as conn:
            await _reset(conn); await conn.execute(INSERT, row)
        async def attempt():
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                result = await conn.execute(text("""UPDATE autonomous_position_exit_authorities
                    SET authority_state='RESERVED', reserved_decision_id=:decision, reserved_package_id=:package
                    WHERE authority_id=:id AND authority_state='ARMED'"""),
                    {"id": row["authority_id"], "decision": uuid.uuid4(), "package": uuid.uuid4()})
                return result.rowcount
        assert sorted(await asyncio.gather(attempt(), attempt())) == [0, 1]

        rollback_row = _row()
        async with engine.begin() as conn:
            await conn.execute(INSERT, rollback_row)
        with pytest.raises(RuntimeError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(text("""UPDATE autonomous_position_exit_authorities
                    SET authority_state='RESERVED', reserved_decision_id=:decision, reserved_package_id=:package
                    WHERE authority_id=:id"""), {"id": rollback_row["authority_id"], "decision": uuid.uuid4(), "package": uuid.uuid4()})
                raise RuntimeError("simulate package/audit transaction failure")
        async with engine.connect() as conn:
            state = await conn.scalar(text("SELECT authority_state FROM autonomous_position_exit_authorities WHERE authority_id=:id"), {"id": rollback_row["authority_id"]})
        assert state == "ARMED"
    finally:
        await engine.dispose()


async def test_postgresql_side_aware_package_constraints_allow_sell_proceeds_above_buy_cap():
    engine = create_async_engine(URL); now = datetime.now(timezone.utc)
    values = dict(
        package_id=uuid.uuid4(), campaign_id=uuid.uuid4(), runtime_campaign_id=uuid.uuid4(),
        account_id=uuid.uuid4(), profile_id=uuid.uuid4(), strategy_id=uuid.uuid4(),
        parameter_id=uuid.uuid4(), decision_id=uuid.uuid4(), risk_id=uuid.uuid4(), preview_id=uuid.uuid4(),
        now=now, expires=now + timedelta(minutes=5), key=uuid.uuid4().hex,
    )
    statement = text("""INSERT INTO canonical_preview_packages (
        package_id,campaign_id,campaign_version,runtime_campaign_id,paper_account_id,live_trading_profile_id,
        provider,environment,product,side,proposed_order_amount,risk_approved_amount,capital_deployment_amount,
        proposed_base_quantity,maximum_authorized_base_quantity,expected_quote_proceeds,
        strategy_id,strategy_version,parameter_set_id,parameter_set_version,decision_record_id,risk_event_id,
        crypto_order_preview_id,preview_expires_at,package_state,generated_at,idempotency_key,input_fingerprint
    ) VALUES (:package_id,:campaign_id,1,:runtime_campaign_id,:account_id,:profile_id,'kraken_spot','production',
        'BTC-USD',:side,:amount,:amount,:capital,:base,:max_base,:proceeds,:strategy_id,'v1',:parameter_id,'v1',
        :decision_id,:risk_id,:preview_id,:expires,'READY',:now,:key,:key)""")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SET session_replication_role=replica"))
            await conn.execute(statement, {**values, "side": "SELL", "amount": Decimal("7.20"),
                                           "capital": Decimal("0"), "base": Decimal("0.00008"),
                                           "max_base": Decimal("0.00008"), "proceeds": Decimal("7.20")})
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(statement, {**values, "package_id": uuid.uuid4(), "preview_id": uuid.uuid4(),
                                               "key": uuid.uuid4().hex, "side": "BUY", "amount": Decimal("5.01"),
                                               "capital": Decimal("5.01"), "base": None, "max_base": None,
                                               "proceeds": None})
    finally:
        await engine.dispose()
