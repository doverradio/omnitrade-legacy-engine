from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

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
 :authority_id, :state, :custody_id, :profile_id, :account_id, :connection_id,
 'kraken_spot','production','BTC-USD',:claim_id,:reconciliation_id,
 'SCHEDULED_PRODUCTION_AUTONOMOUS',true,'PROOF_ELIGIBLE_AUTONOMOUS',
 :now,:hash,0.00008,0.00008,'{}','{}',:now,:expires
)
""")


def _row(*, custody_id=None, profile_id=None, state="ARMED"):
    now = datetime.now(timezone.utc)
    return dict(
        authority_id=uuid.uuid4(), state=state, custody_id=custody_id or uuid.uuid4(),
        profile_id=profile_id or uuid.uuid4(), account_id=uuid.uuid4(), connection_id=uuid.uuid4(),
        claim_id=uuid.uuid4(), reconciliation_id=uuid.uuid4(), now=now,
        expires=now + timedelta(minutes=15), hash=uuid.uuid4().hex,
    )


async def _reset(connection):
    await connection.execute(text("SET session_replication_role=replica"))
    await connection.execute(text("TRUNCATE autonomous_position_exit_authorities"))


async def test_postgresql_authority_constraints_and_terminal_reuse():
    engine = create_async_engine(URL); custody_id=uuid.uuid4(); profile_id=uuid.uuid4()
    first=_row(custody_id=custody_id, profile_id=profile_id); second=_row(custody_id=custody_id, profile_id=profile_id)
    try:
        async with engine.begin() as conn:
            await _reset(conn); await conn.execute(INSERT, first)
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica")); await conn.execute(INSERT, second)
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE autonomous_position_exit_authorities SET authority_state='CONSUMED', consumed_at=now() WHERE authority_id=:id"), {"id": first["authority_id"]})
            await conn.execute(text("SET session_replication_role=replica")); await conn.execute(INSERT, second)
        with pytest.raises(IntegrityError):
            bad={**_row(), "state":"ARMED"}
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(text("""INSERT INTO autonomous_position_exit_authorities (
                    authority_id,authority_state,custody_id,live_trading_profile_id,paper_account_id,
                    exchange_connection_id,provider,environment,product,originating_buy_claim_id,
                    originating_reconciliation_event_id,provenance_classification,proof_eligible,classification,
                    evaluation_at,evaluation_integrity_hash,authoritative_quantity_at_issuance,maximum_sell_quantity,
                    side,exposure_effect,buy_forbidden,increased_exposure_forbidden,policy_evidence,risk_evidence,issued_at,expires_at
                ) VALUES (:authority_id,:state,:custody_id,:profile_id,:account_id,:connection_id,'kraken_spot','production','BTC-USD',
                :claim_id,:reconciliation_id,'SCHEDULED_PRODUCTION_AUTONOMOUS',true,'PROOF_ELIGIBLE_AUTONOMOUS',:now,:hash,
                0.00008,0.00009,'BUY','INCREASE','false','false','{}','{}',:now,:expires)"""), bad)
    finally: await engine.dispose()


async def test_postgresql_concurrent_authority_issuance_has_one_winner():
    engine=create_async_engine(URL); custody_id=uuid.uuid4(); profile_id=uuid.uuid4()
    rows=[_row(custody_id=custody_id, profile_id=profile_id), _row(custody_id=custody_id, profile_id=profile_id)]
    async with engine.begin() as conn: await _reset(conn)
    async def attempt(row):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica")); await conn.execute(INSERT,row)
            return "committed"
        except IntegrityError: return "conflict"
    try:
        assert sorted(await asyncio.gather(*(attempt(row) for row in rows))) == ["committed","conflict"]
    finally: await engine.dispose()
