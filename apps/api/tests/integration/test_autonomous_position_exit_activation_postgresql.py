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

ACTIVATION = text("""INSERT INTO canonical_proving_activations (
 activation_id,package_id,authority_source,dry_run_live_crypto_order_id,campaign_id,campaign_version,
 paper_account_id,live_trading_profile_id,provider,environment,product,side,max_order_amount,
 max_deployed_capital,maximum_authorized_base_quantity,no_leverage,activated_at,expires_at,activation_state
) VALUES (:activation,:package,'CONTINUING_EXIT',NULL,:campaign,1,:account,:profile,'kraken_spot',
 'production','BTC-USD','SELL',7.20,0,0.00008,true,:now,:expires,'ACTIVE')""")

CLAIM = text("""INSERT INTO autonomous_execution_claims (
 claim_id,package_id,activation_id,campaign_id,campaign_version,mandate_id,mandate_version_id,
 account_id,profile_id,connection_id,provider,environment,product,side,claim_version,idempotency_key,
 custody_id,evaluation_integrity_hash,exit_authority_id,exit_authority_version,originating_buy_claim_id,
 originating_reconciliation_event_id,exposure_effect,claimed_base_quantity,maximum_authorized_base_quantity,
 expected_quote_proceeds,capital_deployment_amount,preview_id,risk_event_id,audit_correlation_id,
 proof_eligible,disqualification_reason,expires_at,authority_evidence,claim_status,claimed_at,claim_owner,
 recover_after,attempt_count
) VALUES (:claim,:package,:activation,:campaign,1,:mandate,:mandate_version,:account,:profile,:connection,
 'kraken_spot','production','BTC-USD',:side,1,:key,:custody,'hash',:authority,1,:buy_claim,
 :reconciliation,:effect,:quantity,0.00008,7.20,:capital,:preview,:risk,:audit,true,NULL,:expires,
 '{}','CLAIMED',:now,'test',:expires,1)""")


def _values():
    now = datetime.now(timezone.utc)
    values = {name: uuid.uuid4() for name in (
        "activation", "package", "campaign", "account", "profile", "claim", "mandate",
        "mandate_version", "connection", "custody", "authority", "buy_claim", "reconciliation",
        "preview", "risk", "audit",
    )}
    return {**values, "now": now, "expires": now + timedelta(minutes=2), "key": uuid.uuid4().hex,
            "side": "SELL", "effect": "REDUCE_ONLY", "quantity": Decimal("0.00008"), "capital": Decimal("0")}


async def _clean(conn):
    await conn.execute(text("SET session_replication_role=replica"))
    await conn.execute(text("DELETE FROM autonomous_execution_claims WHERE custody_id IS NOT NULL"))
    await conn.execute(text("DELETE FROM canonical_proving_activations WHERE authority_source='CONTINUING_EXIT'"))


async def test_postgresql_concurrent_atomic_activation_claim_has_one_winner_and_replay_identity():
    engine = create_async_engine(URL); base = _values()
    try:
        async with engine.begin() as conn: await _clean(conn)

        async def attempt(suffix: str):
            values = {**base, "activation": uuid.uuid4(), "claim": uuid.uuid4(), "key": f"{base['key']}-{suffix}"}
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("SET session_replication_role=replica"))
                    await conn.execute(ACTIVATION, values)
                    await conn.execute(CLAIM, values)
                return values["activation"], values["claim"]
            except IntegrityError:
                return None

        outcomes = await asyncio.gather(attempt("a"), attempt("b"))
        winners = [item for item in outcomes if item is not None]
        assert len(winners) == 1
        async with engine.connect() as conn:
            activation_count = await conn.scalar(text("SELECT count(*) FROM canonical_proving_activations WHERE package_id=:package AND authority_source='CONTINUING_EXIT'"), base)
            claim_rows = (await conn.execute(text("SELECT activation_id,claim_id FROM autonomous_execution_claims WHERE exit_authority_id=:authority"), base)).all()
        assert activation_count == 1 and claim_rows == [winners[0]]
        # Durable replay resolves the exact same pair; no second insert is needed.
        assert claim_rows[0] == winners[0]
    finally:
        await engine.dispose()


async def test_postgresql_claim_or_audit_failure_rolls_back_activation_and_checks_reduction_shape():
    engine = create_async_engine(URL); values = _values()
    try:
        async with engine.begin() as conn: await _clean(conn)
        with pytest.raises(RuntimeError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(ACTIVATION, values)
                raise RuntimeError("simulated claim or audit failure")
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM canonical_proving_activations WHERE activation_id=:activation"), values) == 0

        for changes in (
            {"side": "BUY"}, {"effect": "INCREASE"},
            {"quantity": Decimal("0.00009")}, {"capital": Decimal("0.01")},
        ):
            candidate = {**values, **changes, "activation": uuid.uuid4(), "claim": uuid.uuid4(),
                         "package": uuid.uuid4(), "custody": uuid.uuid4(), "authority": uuid.uuid4(),
                         "key": uuid.uuid4().hex}
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(text("SET session_replication_role=replica"))
                    await conn.execute(ACTIVATION, candidate)
                    await conn.execute(CLAIM, candidate)
    finally:
        await engine.dispose()
