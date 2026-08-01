from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.getenv("OMNITRADE_CUSTODY_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not URL, reason="disposable PostgreSQL URL required")]

CUSTODY = text("""INSERT INTO autonomous_position_custodies (
 custody_id,custody_state,originating_autonomous_cycle_id,originating_campaign_cycle_id,campaign_id,campaign_version,
 runtime_campaign_id,mandate_id,mandate_version_id,decision_record_id,buy_package_id,buy_activation_id,buy_claim_id,
 buy_live_order_id,buy_reconciliation_event_id,paper_account_id,live_trading_profile_id,exchange_connection_id,
 provider,environment,product,original_acquired_quantity,observed_remaining_quantity,quantity_authority,
 autonomous_origin,provenance_classification,proof_eligible,disqualification_reason,disqualified_at,
 continuing_exit_authority_state,audit_metadata,created_at,updated_at,exit_reconciliation_event_id,
 exit_reconciled_at,realized_gross_sell_proceeds,realized_sell_fees,realized_net_sell_proceeds,
 allocated_buy_cost_basis,allocated_buy_fees,realized_net_profit,realized_return,realized_sold_quantity,
 residual_dust_quantity,autonomous_proof_sell_verified,terminal_at
) VALUES (
 :custody,:state,:cycle1,:cycle2,:campaign,1,:runtime,:mandate,:mandate_version,:decision,:buy_package,
 :buy_activation,:buy_claim,:buy_order,:buy_recon,:account,:profile,:connection,'kraken_spot','production','BTC-USD',
 0.00008,:remaining,'live_accounting_records',true,'AUTONOMOUS_PRODUCTION',:proof,:disq,:disqualified_at,
 :authority_state,'{}'::jsonb,:now,:now,:exit_recon,:exit_at,:gross,:sell_fees,:net,:buy_cost,:buy_fees,
 :profit,:realized_return,:sold,:residual,:verified,:terminal_at)""")


def _custody(**changes):
    now = datetime.now(timezone.utc)
    values = {name: uuid.uuid4() for name in (
        "custody", "cycle1", "cycle2", "campaign", "runtime", "mandate", "mandate_version", "decision",
        "buy_package", "buy_activation", "buy_claim", "buy_order", "buy_recon", "account", "profile", "connection",
    )}
    values.update({"state": "EXIT_PENDING", "remaining": Decimal("0.00008"), "proof": True, "disq": None,
                   "disqualified_at": None, "authority_state": "RESERVED", "now": now, "exit_recon": None,
                   "exit_at": None, "gross": None, "sell_fees": None, "net": None, "buy_cost": None,
                   "buy_fees": None, "profit": None, "realized_return": None, "sold": None, "residual": None,
                   "verified": False, "terminal_at": None})
    values.update(changes)
    return values


async def _clean(conn):
    await conn.execute(text("SET session_replication_role=replica"))
    await conn.execute(text("DELETE FROM live_accounting_records WHERE idempotency_key LIKE 'p7-test:%'"))
    await conn.execute(text("DELETE FROM autonomous_position_custodies WHERE provenance_classification='AUTONOMOUS_PRODUCTION'"))


async def test_postgresql_concurrent_terminal_closure_has_one_winner_and_replay_is_stable():
    engine = create_async_engine(URL); values = _custody()
    try:
        async with engine.begin() as conn:
            await _clean(conn); await conn.execute(CUSTODY, values)
        exit_recon = uuid.uuid4()

        async def close_once():
            async with engine.begin() as conn:
                return await conn.scalar(text("""UPDATE autonomous_position_custodies SET
                    custody_state='CLOSED',observed_remaining_quantity=0,continuing_exit_authority_state='CONSUMED',
                    exit_reconciliation_event_id=:recon,exit_reconciled_at=now(),realized_gross_sell_proceeds=5.60,
                    realized_sell_fees=0.10,realized_net_sell_proceeds=5.50,allocated_buy_cost_basis=5.00,
                    allocated_buy_fees=0.05,realized_net_profit=0.45,realized_return=0.089108910891,
                    realized_sold_quantity=0.00008,residual_dust_quantity=0,autonomous_proof_sell_verified=true,
                    terminal_at=now() WHERE custody_id=:custody AND custody_state='EXIT_PENDING'
                    RETURNING custody_id"""), {"custody": values["custody"], "recon": exit_recon})

        winners = [row for row in await asyncio.gather(close_once(), close_once()) if row is not None]
        assert winners == [values["custody"]]
        async with engine.connect() as conn:
            row = (await conn.execute(text("""SELECT custody_state,observed_remaining_quantity,
                autonomous_proof_sell_verified,realized_net_profit FROM autonomous_position_custodies
                WHERE custody_id=:custody"""), values)).one()
        assert row == ("CLOSED", Decimal("0"), True, Decimal("0.45"))
    finally:
        await engine.dispose()


async def test_postgresql_proof_and_economics_constraints_fail_closed_and_rollback():
    engine = create_async_engine(URL)
    try:
        async with engine.begin() as conn: await _clean(conn)
        invalid = (
            _custody(verified=True),
            _custody(state="CLOSED", remaining=Decimal("0"), authority_state="CONSUMED", exit_recon=uuid.uuid4(),
                     exit_at=datetime.now(timezone.utc), gross=Decimal("5.6"), sell_fees=Decimal("0.1"), net=Decimal("5.5"),
                     buy_cost=Decimal("5"), buy_fees=Decimal("0.05"), profit=Decimal("0"), realized_return=Decimal("0"),
                     sold=Decimal("0.00008"), residual=Decimal("0"), verified=True, terminal_at=datetime.now(timezone.utc)),
            _custody(state="CLOSED", remaining=Decimal("0.00001"), authority_state="CONSUMED", exit_recon=uuid.uuid4(),
                     exit_at=datetime.now(timezone.utc), gross=Decimal("5.6"), sell_fees=Decimal("0.1"), net=Decimal("5.4"),
                     buy_cost=Decimal("5"), buy_fees=Decimal("0.05"), profit=Decimal("0.45"), realized_return=Decimal("0.09"),
                     sold=Decimal("0.00007"), residual=Decimal("0.00001"), verified=True, terminal_at=datetime.now(timezone.utc)),
        )
        for values in invalid:
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await conn.execute(text("SET session_replication_role=replica")); await conn.execute(CUSTODY, values)
        values = _custody()
        async with engine.begin() as conn:
            await conn.execute(text("SET session_replication_role=replica")); await conn.execute(CUSTODY, values)
        with pytest.raises(RuntimeError, match="audit failure"):
            async with engine.begin() as conn:
                await conn.execute(text("UPDATE autonomous_position_custodies SET observed_remaining_quantity=0.00003 WHERE custody_id=:custody"), values)
                raise RuntimeError("simulated audit failure")
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT observed_remaining_quantity FROM autonomous_position_custodies WHERE custody_id=:custody"), values) == Decimal("0.00008")
    finally:
        await engine.dispose()


async def test_postgresql_provider_fill_identity_is_unique_across_retries():
    engine = create_async_engine(URL); now = datetime.now(timezone.utc)
    values = {"profile": uuid.uuid4(), "order": uuid.uuid4(), "recon": uuid.uuid4(), "source": uuid.uuid4(), "now": now}
    statement = text("""INSERT INTO live_accounting_records (
        id,idempotency_key,live_trading_profile_id,live_crypto_order_id,reconciliation_event_id,
        source_execution_event_id,source_execution_event_type,record_type,provider_order_id,provider_fill_id,
        symbol,side,filled_quantity,fill_price,gross_notional,fee_amount,fee_currency,net_cash_impact,
        provenance,provider_fill_timestamp,recorded_at
    ) VALUES (gen_random_uuid(),:key,:profile,:order,:recon,:source,'execution_intent_created',
        'fill_accounting','KRAKEN-SELL','FILL-1','BTC-USD','sell',0.00004,70000,2.8,0.05,'USD',2.8,
        '{}'::jsonb,:now,:now)""")
    try:
        async with engine.begin() as conn:
            await _clean(conn); await conn.execute(text("SET session_replication_role=replica"))
            await conn.execute(statement, {**values, "key": "p7-test:first"})
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(text("SET session_replication_role=replica"))
                await conn.execute(statement, {**values, "key": "p7-test:replay"})
    finally:
        await engine.dispose()
