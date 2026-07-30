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

from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.controlled_proof_run import ControlledProofRun
from app.models.exchange_connection import ExchangeConnection
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.controlled_proof.service import get_controlled_proof_view, should_propose_controlled_sell
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.orchestration.reconciliation_guard import has_unresolved_reconciliation
from tests.support.real_sqlite_session import real_sqlite_session

# LiveAuditEvidenceRecord is excluded for the same reason as
# test_reconcile_external_trade.py: its CHECK constraint uses Postgres's
# num_nonnulls(), which sqlite has no equivalent for. record_live_audit_evidence
# is stubbed out below instead.
_ALL_TABLES = [
    CapitalCampaign.__table__,
    ExchangeConnection.__table__,
    LiveTradingProfile.__table__,
    LiveCryptoOrder.__table__,
    LiveAccountingRecord.__table__,
    LiveReconciliationEvent.__table__,
    LiveExecutionEvent.__table__,
    CanonicalPreviewPackage.__table__,
    ControlledProofRun.__table__,
    AutonomousExecutionClaim.__table__,
    AuditLog.__table__,
]

_ACTOR = "operator:eric"
_PROVIDER_ORDER_ID = "OQF3Z4-FA5SW-LSIQCE"


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
    """Mutable stand-in for KrakenSpotClient's lookup_order/list_fills, so a
    single provider instance can first report a genuine FILLED order (the
    real BUY confirmation) and then, on a later reconciliation pass, report
    a worse/stale read (OPEN, no fills) -- reproducing the exact production
    regression this test suite guards against, without touching the network."""

    def __init__(self, *, order: _FakeProviderOrder | None, fills: list[_FakeProviderFill]) -> None:
        self.order = order
        self.fills = fills

    async def lookup_order(self, *, credentials, environment, provider_order_id, client_order_id, product_id):
        return self.order

    async def list_fills(self, *, credentials, environment, provider_order_id):
        return self.fills


def _filled_buy_order(*, provider_order_id: str = _PROVIDER_ORDER_ID) -> _FakeProviderOrder:
    return _FakeProviderOrder(
        provider_order_id=provider_order_id, client_order_id=None, product_id=None, side="BUY",
        status="FILLED", submitted_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        acknowledged_at=None,
        raw={"descr": {"pair": "XBTUSD", "type": "buy", "ordertype": "market"}, "status": "closed"},
    )


def _buy_fill(*, provider_order_id: str = _PROVIDER_ORDER_ID) -> _FakeProviderFill:
    return _FakeProviderFill(
        provider_fill_id="fill-1", provider_order_id=provider_order_id, product_id="BTC-USD",
        size=Decimal("0.0001"), price=Decimal("50000.00"),
        fee=_FakeFee(amount=Decimal("0.01"), currency="USD"),
        occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        raw={"ordertxid": provider_order_id, "trade_id": "fill-1"},
    )


async def _seed_connection(session: AsyncSession) -> ExchangeConnection:
    connection = ExchangeConnection(
        exchange_connection_id=uuid.uuid4(), provider="kraken_spot",
        connection_name="kraken_spot-production-primary", environment="production", status="connected",
        credentials_encrypted="unused-in-tests", api_key_masked="****abcd", api_secret_masked="********",
        passphrase_configured=False, credentials_valid=True,
        api_permissions=["funds_query", "closed_order_query", "ledger_query"], account_status="active",
        balances=[{"currency": "USD", "available": "94.99", "reserved": "0", "total": "94.99"}],
        total_equity_usd="94.99", last_verified_at=datetime.now(timezone.utc),
    )
    session.add(connection)
    await session.flush()
    return connection


async def _seed_live_profile(session: AsyncSession) -> LiveTradingProfile:
    profile = LiveTradingProfile(
        id=uuid.uuid4(), paper_account_id=uuid.uuid4(), operating_mode="live", lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True, paper_default_mode=True,
        governance_approved=True, risk_authority_model="risk_engine_final", autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False, automatic_promotion_enabled=False, provenance_metadata={},
    )
    session.add(profile)
    await session.flush()
    return profile


async def _seed_proof_with_pending_buy(
    session: AsyncSession, *, connection: ExchangeConnection, profile: LiveTradingProfile,
) -> tuple[ControlledProofRun, LiveCryptoOrder]:
    """A Controlled Proof whose BUY package/claim/order lineage is fully
    wired but the order has not yet been reconciled -- status ACKNOWLEDGED,
    matching the exact production incident (BUY confirmed filled by Kraken,
    system still shows ACKNOWLEDGED)."""
    campaign_id = uuid.uuid4()
    campaign_version = 1

    package = CanonicalPreviewPackage(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=profile.paper_account_id,
        live_trading_profile_id=profile.id, provider="kraken_spot", environment="production",
        product="BTC-USD", side="BUY", proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state="ACTIVATED",
        generated_at=datetime.now(timezone.utc), idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
    )
    session.add(package)
    await session.flush()

    proof = ControlledProofRun(
        proof_id=uuid.uuid4(), status="PACKAGE_CREATED", provider="kraken_spot", environment="production",
        campaign_id=campaign_id, campaign_version=campaign_version, product_id="BTC-USD",
        max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30), package_id=package.package_id,
    )
    session.add(proof)
    await session.flush()
    package.market_evidence_identity = {
        **(package.market_evidence_identity or {}), "controlled_proof_id": str(proof.proof_id),
    }

    order = LiveCryptoOrder(
        live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=package.crypto_order_preview_id,
        exchange_connection_id=connection.exchange_connection_id, provider="kraken_spot", environment="production",
        product_id="BTC-USD", side="BUY", order_type="MARKET", requested_quote_size=Decimal("5"),
        client_order_id=f"buy-{uuid.uuid4()}", status="ACKNOWLEDGED", provider_order_id=_PROVIDER_ORDER_ID,
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=6), audit_correlation_id=proof.audit_correlation_id,
        safe_provider_response={
            "live_trading_profile_id": str(profile.id),
            "usd_available_before_submit": "100.00",
        },
    )
    session.add(order)
    await session.flush()

    claim = AutonomousExecutionClaim(
        claim_id=uuid.uuid4(), package_id=package.package_id, activation_id=uuid.uuid4(),
        campaign_id=campaign_id, campaign_version=campaign_version, mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
        account_id=profile.paper_account_id, profile_id=profile.id, connection_id=connection.exchange_connection_id,
        provider="kraken_spot", environment="production", product="BTC-USD", side="BUY",
        claim_status="SUBMISSION_PENDING", claimed_at=datetime.now(timezone.utc), claim_owner="test",
        live_order_id=order.live_crypto_order_id,
    )
    session.add(claim)
    await session.flush()
    return proof, order


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider, *, connection: ExchangeConnection) -> None:
    async def _load_exchange_connection(*, db, exchange_connection_id):
        return connection

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr(
        "app.services.live_crypto_orders._load_decrypted_credentials",
        lambda _c: {"api_key": "k", "api_secret": "s", "passphrase": ""},
    )
    monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_a, **_k: provider)

    async def _no_audit_evidence(*_a, **_k):
        return None

    monkeypatch.setattr("app.services.live.accounting_reconciliation.record_live_audit_evidence", _no_audit_evidence)


@pytest.mark.asyncio
async def test_controlled_proof_reports_filled_and_sell_eligible_after_reconciled_buy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirements #2 and #3: once the real BUY order genuinely reconciles
    as FILLED, the Controlled Proof projection reports it as FILLED (not
    stale ACKNOWLEDGED) and the SELL proposal becomes eligible immediately --
    no separate worker cycle or cache-repair step required."""
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        proof, order = await _seed_proof_with_pending_buy(session, connection=connection, profile=profile)

        provider = _FakeProvider(order=_filled_buy_order(), fills=[_buy_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        result = await reconcile_live_order_and_fills(
            db=session, live_crypto_order_id=order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert result["reconciliation_status"] == "FILLED"
        await session.commit()

        view = await get_controlled_proof_view(db=session, proof_id=proof.proof_id)
        assert view["buy_order"] is not None
        assert view["buy_order"]["status"] == "FILLED"
        assert view["position"] is not None
        assert view["reconciliation"] is not None
        assert view["reconciliation"]["unresolved"] is False

        refreshed_proof = await session.get(ControlledProofRun, proof.proof_id)
        assert await should_propose_controlled_sell(db=session, proof=refreshed_proof) is True


@pytest.mark.asyncio
async def test_controlled_proof_sell_eligibility_survives_blocked_regression_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement #1, proven at the Controlled Proof level: a later
    reconciliation pass that would downgrade the already-FILLED BUY (the
    confirmed production regression -- filled -> partially_filled ->
    reconciliation_required) must never flip the order status, the
    Controlled Proof projection, or SELL eligibility away from their correct,
    already-proven values."""
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        proof, order = await _seed_proof_with_pending_buy(session, connection=connection, profile=profile)

        provider = _FakeProvider(order=_filled_buy_order(), fills=[_buy_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        await reconcile_live_order_and_fills(
            db=session, live_crypto_order_id=order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        await session.commit()

        refreshed_proof = await session.get(ControlledProofRun, proof.proof_id)
        assert await should_propose_controlled_sell(db=session, proof=refreshed_proof) is True

        # A later pass observes a worse/transient provider read: OPEN, no
        # fills -- exactly the shape that used to regress a FILLED order.
        provider.order = _FakeProviderOrder(
            provider_order_id=_PROVIDER_ORDER_ID, client_order_id=None, product_id=None, side="BUY",
            status="OPEN", submitted_at=datetime.now(timezone.utc), acknowledged_at=None,
            raw={"descr": {"pair": "XBTUSD", "type": "buy", "ordertype": "market"}, "status": "open"},
        )
        provider.fills = []

        regression_result = await reconcile_live_order_and_fills(
            db=session, live_crypto_order_id=order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert regression_result["reconciliation_status"] == "FILLED"
        await session.commit()

        await session.refresh(order)
        assert order.status == "FILLED"

        view = await get_controlled_proof_view(db=session, proof_id=proof.proof_id)
        assert view["buy_order"]["status"] == "FILLED"
        assert view["reconciliation"]["unresolved"] is False

        refreshed_proof = await session.get(ControlledProofRun, proof.proof_id)
        assert await should_propose_controlled_sell(db=session, proof=refreshed_proof) is True

        latest_reconciliation = await session.scalar(
            select(LiveReconciliationEvent)
            .where(LiveReconciliationEvent.live_crypto_order_id == order.live_crypto_order_id)
            .order_by(LiveReconciliationEvent.sequence_number.desc())
            .limit(1)
        )
        assert latest_reconciliation is not None
        assert latest_reconciliation.reconciliation_status == "filled"


@pytest.mark.asyncio
async def test_viqc_buy_reconciliation_clears_unresolved_gate_and_sell_becomes_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confirmed production incident: a Kraken quote-sized (viqc) market
    BUY's raw "vol" is quote-currency (USD), not base-currency (BTC). Before
    the fix, comparing that raw quantity against base-currency fill sizes
    permanently misclassified the fill as partial, leaving the order's
    latest reconciliation event at "partially_filled" -- which is exactly
    what the worker's unresolved-reconciliation gate (the same
    has_unresolved_reconciliation this test calls directly) checks before
    ever proposing a SELL. This proves the gate now clears end-to-end for a
    genuinely fully filled viqc BUY, so the Controlled Proof SELL becomes
    eligible without any separate cache-repair step."""
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        proof, order = await _seed_proof_with_pending_buy(session, connection=connection, profile=profile)

        viqc_order = _FakeProviderOrder(
            provider_order_id=_PROVIDER_ORDER_ID, client_order_id=None, product_id=None, side="BUY",
            status="FILLED", submitted_at=datetime.now(timezone.utc) - timedelta(minutes=5), acknowledged_at=None,
            raw={
                "descr": {"pair": "XBTUSD", "type": "buy", "ordertype": "market"},
                "oflags": "fciq,viqc", "vol": "5.00000000", "cost": "5.00000000", "status": "closed",
            },
        )
        provider = _FakeProvider(order=viqc_order, fills=[_buy_fill()])
        _patch_provider(monkeypatch, provider, connection=connection)

        result = await reconcile_live_order_and_fills(
            db=session, live_crypto_order_id=order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert result["reconciliation_status"] == "FILLED"
        await session.commit()

        fill_record = await session.scalar(
            select(LiveAccountingRecord).where(
                LiveAccountingRecord.live_crypto_order_id == order.live_crypto_order_id,
                LiveAccountingRecord.record_type.in_(("fill_accounting", "partial_fill_accounting")),
            )
        )
        assert fill_record is not None
        assert fill_record.record_type == "fill_accounting"

        assert await has_unresolved_reconciliation(
            db=session, provider="kraken_spot", environment="production", product="BTC-USD",
        ) is False

        refreshed_proof = await session.get(ControlledProofRun, proof.proof_id)
        assert await should_propose_controlled_sell(db=session, proof=refreshed_proof) is True

        view = await get_controlled_proof_view(db=session, proof_id=proof.proof_id)
        assert view["buy_order"]["status"] == "FILLED"
        assert view["reconciliation"]["unresolved"] is False
