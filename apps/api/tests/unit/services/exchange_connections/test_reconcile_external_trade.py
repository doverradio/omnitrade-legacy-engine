from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.schemas.exchange_connections import ReconcileExternalTradeRequest
from app.services.exchange_connections import service as exchange_connections_service
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from tests.support.real_sqlite_session import real_sqlite_session

# LiveAuditEvidenceRecord is deliberately excluded: its
# ck_live_audit_evidence_records_requires_linkage CHECK constraint uses
# Postgres's num_nonnulls(), which sqlite has no equivalent for and
# real_sqlite_session does not register as a UDF. record_live_audit_evidence
# (the only caller) is stubbed out below instead -- this test verifies
# accounting/order outcomes, not that helper's own persistence, which already
# has direct coverage elsewhere (test_live_accounting_reconciliation*.py).
_ALL_TABLES = [
    ExchangeConnection.__table__,
    LiveTradingProfile.__table__,
    LiveCryptoOrder.__table__,
    LiveAccountingRecord.__table__,
    LiveReconciliationEvent.__table__,
    LiveExecutionEvent.__table__,
    AutonomousExecutionClaim.__table__,
    CapitalCampaign.__table__,
    AuditLog.__table__,
]

_ACTOR = "operator:eric"
_PROVIDER_ORDER_ID = "OAXUZJ-SELL1-EXTERN"


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


@dataclass(frozen=True, slots=True)
class _FakeProviderOrder:
    provider_order_id: str | None
    client_order_id: str | None
    product_id: str | None
    side: str | None
    status: str | None
    submitted_at: datetime | None
    acknowledged_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _FakeFee:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class _FakeProviderFill:
    provider_fill_id: str | None
    provider_order_id: str | None
    product_id: str | None
    size: Decimal
    price: Decimal
    fee: _FakeFee | None
    occurred_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


class _FakeProvider:
    """Stands in for the real KrakenSpotClient's provider-facing surface --
    lookup_order/list_fills only, matching what reconcile_external_trade and
    reconcile_live_order_and_fills actually call. Never touches the network."""

    def __init__(self, *, order: _FakeProviderOrder | None, fills: list[_FakeProviderFill]) -> None:
        self._order = order
        self._fills = fills
        self.list_fills_calls = 0

    async def lookup_order(self, *, credentials, environment, provider_order_id, client_order_id, product_id):
        return self._order

    async def list_fills(self, *, credentials, environment, provider_order_id):
        self.list_fills_calls += 1
        return self._fills


def _filled_sell_order(*, provider_order_id: str = _PROVIDER_ORDER_ID, submitted_at: datetime | None = None) -> _FakeProviderOrder:
    return _FakeProviderOrder(
        provider_order_id=provider_order_id,
        client_order_id=None,
        product_id=None,
        side="SELL",
        status="FILLED",
        submitted_at=submitted_at or (datetime.now(timezone.utc) - timedelta(minutes=5)),
        acknowledged_at=None,
        raw={"descr": {"pair": "XBTUSD", "type": "sell", "ordertype": "market", "order": "sell 0.00007817 XBTUSD @ market"}, "status": "closed"},
    )


def _sell_fill(*, provider_order_id: str = _PROVIDER_ORDER_ID) -> _FakeProviderFill:
    return _FakeProviderFill(
        provider_fill_id="103999999",
        provider_order_id=provider_order_id,
        product_id="BTC-USD",
        size=Decimal("0.00007817"),
        price=Decimal("64900.00"),
        fee=_FakeFee(amount=Decimal("0.05"), currency="USD"),
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        raw={"ordertxid": provider_order_id, "trade_id": "103999999"},
    )


async def _seed_connection(session: AsyncSession, *, status: str = "connected", environment: str = "production", credentials_valid: bool = True) -> ExchangeConnection:
    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot",
        connection_name="kraken_spot-production-primary",
        environment=environment,
        status=status,
        credentials_encrypted="unused-in-tests",
        api_key_masked="****abcd",
        api_secret_masked="********",
        passphrase_configured=False,
        credentials_valid=credentials_valid,
        api_permissions=["funds_query", "closed_order_query", "ledger_query"],
        account_status="active",
        balances=[{"currency": "BTC", "available": "0", "reserved": "0", "total": "0"}],
        total_equity_usd="18.68",
    )
    session.add(connection)
    await session.flush()
    return connection


async def _seed_live_profile(session: AsyncSession, *, operating_mode: str = "live") -> LiveTradingProfile:
    profile = LiveTradingProfile(
        id=uuid.uuid4(), paper_account_id=uuid.uuid4(), operating_mode=operating_mode, lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True, paper_default_mode=True,
        governance_approved=True, risk_authority_model="risk_engine_final", autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False, automatic_promotion_enabled=False, provenance_metadata={},
    )
    session.add(profile)
    await session.flush()
    return profile


async def _seed_existing_buy(session: AsyncSession, *, profile: LiveTradingProfile) -> LiveAccountingRecord:
    """The exact production BUY this SELL must offset: 0.00007817 BTC-USD,
    uncategorized (capital_campaign_id=None), matching the incident.

    Seeds both the fill_accounting row AND its paired fee_attribution row --
    record_live_fill_reconciliation always writes both for every real fill
    (same filled_quantity, same side, different record_type), and
    _owned_position_exists' own query is deliberately not record_type-scoped
    (see its docstring), so a real BUY genuinely contributes its quantity
    twice. Seeding only one row here would understate the BUY relative to a
    SELL imported through the real reconciliation path below, which always
    produces both rows -- a fixture bug, not a real imbalance."""
    reconciliation_event_id = uuid.uuid4()
    source_execution_event_id = uuid.uuid4()
    recorded_at = datetime.now(timezone.utc) - timedelta(days=9)
    common = dict(
        live_trading_profile_id=profile.id,
        live_crypto_order_id=None,
        capital_campaign_id=None,
        reconciliation_event_id=reconciliation_event_id,
        source_execution_event_id=source_execution_event_id,
        source_execution_event_type="execution_intent_created",
        provider_order_id="OAXUZJ-7WRL5-NPFWYA",
        provider_fill_id="103901842",
        symbol="BTC-USD",
        side="buy",
        filled_quantity=Decimal("0.00007817"),
        fill_price=Decimal("63959.60000"),
        gross_notional=Decimal("4.9997219320000"),
        fee_amount=Decimal("0.04000"),
        fee_currency="USD",
        provenance={},
        recorded_at=recorded_at,
    )
    fill_record = LiveAccountingRecord(
        idempotency_key="fill-existing-buy-1:fill", record_type="fill_accounting",
        net_cash_impact=Decimal("-5.0397219320000"), **common,
    )
    fee_record = LiveAccountingRecord(
        idempotency_key="fill-existing-buy-1:fee", record_type="fee_attribution",
        net_cash_impact=Decimal("0.04000"), **common,
    )
    session.add(fill_record)
    session.add(fee_record)
    await session.flush()
    return fill_record


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider, *, connection: ExchangeConnection) -> None:
    monkeypatch.setattr(exchange_connections_service, "get_exchange_provider", lambda *_a, **_k: provider)
    monkeypatch.setattr(exchange_connections_service, "_decrypt_credentials", lambda _c: {"api_key": "k", "api_secret": "s", "passphrase": ""})

    async def _load_exchange_connection(*, db, exchange_connection_id):
        return connection

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _c: {"api_key": "k", "api_secret": "s", "passphrase": ""})
    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_a, **_k: provider)

    async def _no_audit_evidence(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.live.accounting_reconciliation.record_live_audit_evidence", _no_audit_evidence)


# --- happy path: import + reconcile the external SELL --------------------------------

@pytest.mark.asyncio
async def test_reconcile_external_trade_imports_filled_sell_and_zeroes_owned_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        await _seed_existing_buy(session, profile=profile)

        provider = _FakeProvider(order=_filled_sell_order(), fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        response = await exchange_connections_service.reconcile_external_trade(
            db=session, exchange_connection_id=connection.exchange_connection_id,
            payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
        )

        assert response.provider_order_id == _PROVIDER_ORDER_ID
        assert response.product_id == "BTC-USD"
        assert response.side == "SELL"
        assert response.live_trading_profile_id == profile.id
        assert response.live_crypto_order.status == "FILLED"
        assert response.live_crypto_order.side == "SELL"
        assert response.reconciliation_status == "FILLED"
        # External provenance is not canonical submission lineage: the
        # OmniTrade pre-submit balance snapshot truthfully never existed.
        # Authoritative provider order/fill/fee evidence therefore completes
        # economic reconciliation without fabricating Risk, Decision,
        # Mandate, Campaign, or Controlled Proof authority.
        assert response.accounting_completion_status == "complete"
        assert response.provider_fill_observed is True

        # Explicit external-trade provenance -- never implies Risk Engine,
        # mandate, or Controlled Proof authorized this trade.
        stored = await session.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.provider_order_id == _PROVIDER_ORDER_ID).limit(1)
        )
        assert stored is not None
        assert stored.risk_event_id is None
        assert stored.decision_record_id is None
        assert stored.validation_run_id is None
        assert stored.safe_provider_response.get("authority_classification") == "EXTERNALLY_EXECUTED_MANUAL_TRADE"
        assert stored.safe_provider_response.get("capital_campaign_id") is None
        assert stored.safe_provider_response["reconciliation"]["balance_mismatch_state"] == "not_applicable_external_provenance"
        assert stored.safe_provider_response["reconciliation"]["accounting_completion_status"] == "complete"

        latest_reconciliation = await session.scalar(
            select(LiveReconciliationEvent)
            .where(LiveReconciliationEvent.live_crypto_order_id == stored.live_crypto_order_id)
            .order_by(LiveReconciliationEvent.sequence_number.desc())
            .limit(1)
        )
        assert latest_reconciliation is not None
        assert latest_reconciliation.reconciliation_status == "filled"
        assert latest_reconciliation.provenance["reason"] == "external_provenance_evidence_resolved"

        # Canonical replay is convergent: the historical unresolved production
        # shape can be retried by the scheduler without duplicating accounting
        # or terminal evidence.
        accounting_count = len((await session.scalars(
            select(LiveAccountingRecord).where(
                LiveAccountingRecord.live_crypto_order_id == stored.live_crypto_order_id,
            )
        )).all())
        resolution_count = len((await session.scalars(
            select(LiveReconciliationEvent).where(
                LiveReconciliationEvent.live_crypto_order_id == stored.live_crypto_order_id,
                LiveReconciliationEvent.idempotency_key.endswith(":balance-resolved:external-provenance"),
            )
        )).all())

        replay = await reconcile_live_order_and_fills(
            db=session,
            live_crypto_order_id=stored.live_crypto_order_id,
            operator_identity="system:reconciliation_scheduler",
        )
        assert replay["accounting_completion_status"] == "complete"
        assert len((await session.scalars(select(LiveAccountingRecord).where(
            LiveAccountingRecord.live_crypto_order_id == stored.live_crypto_order_id,
        ))).all()) == accounting_count
        assert len((await session.scalars(select(LiveReconciliationEvent).where(
            LiveReconciliationEvent.live_crypto_order_id == stored.live_crypto_order_id,
            LiveReconciliationEvent.idempotency_key.endswith(":balance-resolved:external-provenance"),
        ))).all()) == resolution_count == 1

        # BUY 0.00007817 minus imported SELL 0.00007817 == 0 owned quantity --
        # verified by calling the actual production function
        # (app.services.live.position_quantity), not a test-local
        # reimplementation of its query. This is the same function
        # controlled_proof._owned_position_exists and
        # prepare_autonomous_claimed_buy's owned_position_exists check both
        # call; it correctly excludes fee_attribution rows (which
        # record_live_fill_reconciliation always writes alongside every
        # fill_accounting row, with the same filled_quantity/side) from the
        # quantity sum.
        from app.services.live.position_quantity import compute_signed_owned_quantity, owned_position_exists

        net_quantity = await compute_signed_owned_quantity(
            db=session, live_trading_profile_id=profile.id, symbol="BTC-USD",
        )
        assert net_quantity == Decimal("0")
        assert await owned_position_exists(db=session, live_trading_profile_id=profile.id, symbol="BTC-USD") is False

        # A single, comprehensive audit event recorded the import.
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "external_trade_reconciled",
                AuditLog.entity_id == stored.live_crypto_order_id,
            )
        )
        assert audit is not None
        assert audit.actor == _ACTOR
        assert audit.after_state["provider_order_id"] == _PROVIDER_ORDER_ID
        assert audit.after_state["product_id"] == "BTC-USD"
        assert audit.after_state["side"] == "SELL"
        assert audit.after_state["resulting_status"] == "FILLED"


# --- fail-closed rejections -------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_duplicate_provider_order_id(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        await _seed_existing_buy(session, profile=profile)
        provider = _FakeProvider(order=_filled_sell_order(), fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        first = await exchange_connections_service.reconcile_external_trade(
            db=session, exchange_connection_id=connection.exchange_connection_id,
            payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
        )
        assert first.live_crypto_order.status == "FILLED"

        with pytest.raises(ConflictError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )

        rows = (await session.execute(
            select(LiveCryptoOrder).where(LiveCryptoOrder.provider_order_id == _PROVIDER_ORDER_ID)
        )).scalars().all()
        assert len(rows) == 1  # replay never creates a second row


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_unknown_provider_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        await _seed_live_profile(session)
        provider = _FakeProvider(order=None, fills=[])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id="UNKNOWN-TXID"), actor=_ACTOR,
            )

        rows = (await session.execute(select(LiveCryptoOrder))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_non_filled_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        await _seed_live_profile(session)
        open_order = _FakeProviderOrder(
            provider_order_id=_PROVIDER_ORDER_ID, client_order_id=None, product_id=None, side="SELL",
            status="OPEN", submitted_at=datetime.now(timezone.utc), acknowledged_at=None,
            raw={"descr": {"pair": "XBTUSD", "type": "sell", "ordertype": "market"}},
        )
        provider = _FakeProvider(order=open_order, fills=[])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )

        rows = (await session.execute(select(LiveCryptoOrder))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_when_fills_are_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        await _seed_live_profile(session)
        provider = _FakeProvider(order=_filled_sell_order(), fills=[])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )

        rows = (await session.execute(select(LiveCryptoOrder))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_unnormalizable_product(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        await _seed_live_profile(session)
        exotic_order = _FakeProviderOrder(
            provider_order_id=_PROVIDER_ORDER_ID, client_order_id=None, product_id=None, side="SELL",
            status="FILLED", submitted_at=datetime.now(timezone.utc), acknowledged_at=None,
            raw={"descr": {"pair": "DOGEUSD", "type": "sell", "ordertype": "market"}},
        )
        provider = _FakeProvider(order=exotic_order, fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )

        rows = (await session.execute(select(LiveCryptoOrder))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_when_more_than_one_live_profile_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        await _seed_live_profile(session)
        await _seed_live_profile(session)  # second live-mode profile -> ambiguous
        provider = _FakeProvider(order=_filled_sell_order(), fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )

        rows = (await session.execute(select(LiveCryptoOrder))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_when_no_live_profile_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        # No LiveTradingProfile seeded at all.
        provider = _FakeProvider(order=_filled_sell_order(), fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_non_production_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session, environment="sandbox")
        await _seed_live_profile(session)
        provider = _FakeProvider(order=_filled_sell_order(), fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )


@pytest.mark.asyncio
async def test_reconcile_external_trade_rejects_disconnected_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session, status="disconnected")
        await _seed_live_profile(session)
        provider = _FakeProvider(order=_filled_sell_order(), fills=[_sell_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        with pytest.raises(InvalidRequestError):
            await exchange_connections_service.reconcile_external_trade(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                payload=ReconcileExternalTradeRequest(provider_order_id=_PROVIDER_ORDER_ID), actor=_ACTOR,
            )
