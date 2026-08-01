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

ORDER = text("""INSERT INTO live_crypto_orders (
 live_crypto_order_id,crypto_order_preview_id,exchange_connection_id,provider,environment,product_id,
 side,order_type,requested_quote_size,client_order_id,status,risk_event_id,decision_record_id,
 provider_order_id,submitted_at,safe_provider_response,audit_correlation_id,execution_claim_id,claim_version,
 custody_id,evaluation_integrity_hash,exit_authority_id,exit_authority_version,activation_id,
 originating_buy_claim_id,originating_reconciliation_event_id,exposure_effect,requested_base_quantity,
 normalized_base_quantity,maximum_authorized_base_quantity,expected_quote_proceeds,capital_deployment_amount,
 proof_eligible,construction_expires_at,provider_submission_connected,created_at,updated_at
) VALUES (:order,:preview,:connection,'kraken_spot','production','BTC-USD',:side,'market',7.20,:key,
 'PENDING_CONFIRMATION',:risk,:decision,NULL,NULL,jsonb_build_object('provider_call_made', false),:audit,:claim,1,:custody,
 'hash',:authority,1,:activation,:buy_claim,:reconciliation,:effect,0.00008,:normalized,0.00008,
 7.20,:capital,true,:expires,false,:now,:now)""")


def _values():
    now = datetime.now(timezone.utc)
    values = {name: uuid.uuid4() for name in (
        "order", "preview", "connection", "risk", "decision", "audit", "claim", "custody",
        "authority", "activation", "buy_claim", "reconciliation",
    )}
    return {**values, "key": uuid.uuid4().hex, "side": "SELL", "effect": "REDUCE_ONLY",
            "normalized": Decimal("0.00008"), "capital": Decimal("0"),
            "now": now, "expires": now + timedelta(minutes=2)}


async def _clean(conn):
    await conn.execute(text("SET session_replication_role=replica"))
    await conn.execute(text("DELETE FROM live_crypto_orders WHERE execution_claim_id IS NOT NULL"))


async def test_postgresql_concurrent_construction_has_one_winner_and_exact_replay_identity():
    engine = create_async_engine(URL); base = _values()
    try:
        async with engine.begin() as conn: await _clean(conn)
        async def attempt(suffix):
            candidate = {**base, "order": uuid.uuid4(), "key": f"{base['key']}-{suffix}"}
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("SET session_replication_role=replica"))
                    await conn.execute(ORDER, candidate)
                return candidate["order"]
            except IntegrityError:
                return None
        winners = [value for value in await asyncio.gather(attempt("a"), attempt("b")) if value is not None]
        assert len(winners) == 1
        async with engine.connect() as conn:
            rows = (await conn.execute(text("SELECT live_crypto_order_id,provider_order_id,submitted_at,provider_submission_connected FROM live_crypto_orders WHERE execution_claim_id=:claim"), base)).all()
        assert rows == [(winners[0], None, None, False)]
    finally:
        await engine.dispose()


async def test_postgresql_order_or_audit_failure_rolls_back_and_invalid_shapes_fail():
    engine = create_async_engine(URL); values = _values()
    try:
        async with engine.begin() as conn: await _clean(conn)
        with pytest.raises(RuntimeError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(ORDER, values)
                raise RuntimeError("simulated binding or audit failure")
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM live_crypto_orders WHERE live_crypto_order_id=:order"), values) == 0
        for changes in (
            {"side": "BUY"}, {"effect": "INCREASE"}, {"normalized": Decimal("0.00009")},
            {"capital": Decimal("0.01")},
        ):
            candidate = {**values, **changes, "order": uuid.uuid4(), "preview": uuid.uuid4(),
                         "claim": uuid.uuid4(), "custody": uuid.uuid4(), "authority": uuid.uuid4(),
                         "key": uuid.uuid4().hex}
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(text("SET session_replication_role=replica"))
                    await conn.execute(ORDER, candidate)
    finally:
        await engine.dispose()
