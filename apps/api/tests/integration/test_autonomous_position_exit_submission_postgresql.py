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

INSERT = text("""INSERT INTO live_crypto_orders (
 live_crypto_order_id,crypto_order_preview_id,exchange_connection_id,provider,environment,product_id,
 side,order_type,requested_quote_size,client_order_id,status,risk_event_id,decision_record_id,
 provider_order_id,submitted_at,safe_provider_response,audit_correlation_id,execution_claim_id,claim_version,
 custody_id,evaluation_integrity_hash,exit_authority_id,exit_authority_version,activation_id,
 originating_buy_claim_id,originating_reconciliation_event_id,exposure_effect,requested_base_quantity,
 normalized_base_quantity,maximum_authorized_base_quantity,expected_quote_proceeds,capital_deployment_amount,
 proof_eligible,disqualification_reason,construction_expires_at,provider_submission_connected,created_at,updated_at
) VALUES (:order,:preview,:connection,'kraken_spot','production','BTC-USD',:side,'market',7.20,:key,
 :status,:risk,:decision,:provider_order_id,:submitted_at,jsonb_build_object('provider_call_made', CAST(:called AS boolean)),:audit,
 :claim,1,:custody,'hash',:authority,1,:activation,:buy_claim,:reconciliation,:effect,0.00008,:normalized,
 0.00008,7.20,:capital,:proof,:disqualification_reason,:expires,:connected,:now,:now)""")


def _values(**changes):
    now = datetime.now(timezone.utc)
    values = {name: uuid.uuid4() for name in (
        "order", "preview", "connection", "risk", "decision", "audit", "claim", "custody",
        "authority", "activation", "buy_claim", "reconciliation",
    )}
    values.update({"key": uuid.uuid4().hex, "side": "SELL", "effect": "REDUCE_ONLY",
                   "normalized": Decimal("0.00008"), "capital": Decimal("0"), "proof": False,
                   "disqualification_reason": "permanent_nonqualifying_lineage",
                   "status": "PENDING_CONFIRMATION", "provider_order_id": None, "submitted_at": None,
                   "called": False, "connected": False, "now": now,
                   "expires": now + timedelta(minutes=2)})
    values.update(changes)
    return values


async def _clean(conn):
    await conn.execute(text("SET session_replication_role=replica"))
    await conn.execute(text("DELETE FROM live_crypto_orders WHERE execution_claim_id IS NOT NULL"))


async def test_postgresql_concurrent_submission_transition_has_one_provider_winner():
    engine = create_async_engine(URL); values = _values(); provider_calls = 0
    try:
        async with engine.begin() as conn:
            await _clean(conn); await conn.execute(INSERT, values)

        async def attempt():
            nonlocal provider_calls
            async with engine.begin() as conn:
                row = await conn.scalar(text("""UPDATE live_crypto_orders SET
                    status='SUBMISSION_PENDING', submitted_at=now(), provider_submission_connected=true
                    WHERE live_crypto_order_id=:order AND status='PENDING_CONFIRMATION'
                    AND submitted_at IS NULL AND provider_submission_connected=false
                    RETURNING live_crypto_order_id"""), values)
            if row is not None:
                provider_calls += 1
            return row

        winners = [row for row in await asyncio.gather(attempt(), attempt()) if row is not None]
        assert winners == [values["order"]]
        assert provider_calls == 1
        async with engine.connect() as conn:
            state = (await conn.execute(text("""SELECT status,submitted_at IS NOT NULL,
                provider_submission_connected,provider_order_id FROM live_crypto_orders
                WHERE live_crypto_order_id=:order"""), values)).one()
        assert state == ("SUBMISSION_PENDING", True, True, None)
    finally:
        await engine.dispose()


async def test_postgresql_lifecycle_constraint_accepts_truth_and_rejects_invalid_evidence():
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as conn: await _clean(conn)
        accepted = (
            _values(),
            _values(status="SUBMISSION_PENDING", submitted_at=datetime.now(timezone.utc), connected=True, called=False),
            _values(status="RECONCILIATION_REQUIRED", submitted_at=datetime.now(timezone.utc), connected=True, called=True),
            _values(status="REJECTED", submitted_at=datetime.now(timezone.utc), connected=True, called=True),
            _values(status="ACKNOWLEDGED", submitted_at=datetime.now(timezone.utc), connected=True,
                    called=True, provider_order_id=f"KRAKEN-{uuid.uuid4().hex}"),
        )
        async with engine.begin() as conn:
            await conn.execute(text("SET session_replication_role=replica"))
            for values in accepted: await conn.execute(INSERT, values)
        invalid = (
            _values(status="ACKNOWLEDGED", submitted_at=datetime.now(timezone.utc), connected=True, called=True),
            _values(status="PENDING_CONFIRMATION", submitted_at=datetime.now(timezone.utc)),
            _values(status="SUBMISSION_PENDING", submitted_at=None, connected=True),
            _values(status="SUBMISSION_PENDING", submitted_at=datetime.now(timezone.utc), connected=False),
            _values(status="REJECTED", submitted_at=datetime.now(timezone.utc), connected=True,
                    called=True, provider_order_id=f"KRAKEN-{uuid.uuid4().hex}"),
            _values(proof=True),
            _values(side="BUY"), _values(effect="INCREASE"),
            _values(normalized=Decimal("0.00009")), _values(capital=Decimal("0.01")),
        )
        for values in invalid:
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(text("SET session_replication_role=replica"))
                    await conn.execute(INSERT, values)
    finally:
        await engine.dispose()


async def test_postgresql_pre_provider_state_and_audit_failure_roll_back_together():
    engine = create_async_engine(URL); values = _values()
    try:
        async with engine.begin() as conn:
            await _clean(conn); await conn.execute(INSERT, values)
        with pytest.raises(RuntimeError, match="audit failure"):
            async with engine.begin() as conn:
                await conn.execute(text("""UPDATE live_crypto_orders SET
                    status='SUBMISSION_PENDING', submitted_at=now(), provider_submission_connected=true
                    WHERE live_crypto_order_id=:order"""), values)
                raise RuntimeError("simulated audit failure")
        async with engine.connect() as conn:
            state = (await conn.execute(text("""SELECT status,submitted_at,
                provider_submission_connected FROM live_crypto_orders
                WHERE live_crypto_order_id=:order"""), values)).one()
        assert state == ("PENDING_CONFIRMATION", None, False)
    finally:
        await engine.dispose()
