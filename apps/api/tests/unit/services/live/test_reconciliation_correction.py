from __future__ import annotations

import logging
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
from app.services.controlled_proof.service import should_propose_controlled_sell
from app.services.live.accounting_reconciliation import ensure_execution_source
from app.services.live.reconciliation_correction import correct_stale_viqc_reconciliation
from app.services.orchestration.reconciliation_guard import has_unresolved_reconciliation
from tests.support.real_sqlite_session import real_sqlite_session

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
_LIVE_ORDER_ID = uuid.UUID("ce6f3199-0827-4dbf-8e0e-73584b7564ba")


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


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


@dataclass(frozen=True, slots=True)
class _StaleFixture:
    proof: ControlledProofRun
    order: LiveCryptoOrder
    filled_order_event: LiveReconciliationEvent
    stale_partial_event: LiveReconciliationEvent
    accounting: LiveAccountingRecord


async def _seed_stale_viqc_buy(
    session: AsyncSession, *, connection: ExchangeConnection, profile: LiveTradingProfile,
    live_crypto_order_id: uuid.UUID | None = None, provider_order_id: str = _PROVIDER_ORDER_ID,
    side: str = "BUY", provider: str = "kraken_spot", sell_package_id: uuid.UUID | None = None,
    ledger_quantity: Decimal = Decimal("0.00007799"),
) -> _StaleFixture:
    """Reproduces -- entirely locally, never against real production data --
    the exact confirmed historical shape: a Kraken viqc BUY whose order-level
    status correctly reached FILLED, but whose fill was misclassified
    "partial_fill_accounting" / "partially_filled" by the (now-fixed) bug in
    reconcile_live_order_and_fills, and that fill-level event is the latest
    (highest sequence_number) reconciliation event for the order."""
    campaign_id = uuid.uuid4()
    campaign_version = 1

    package = CanonicalPreviewPackage(
        package_id=uuid.uuid4(), campaign_id=campaign_id, campaign_version=campaign_version,
        runtime_campaign_id=uuid.uuid4(), paper_account_id=profile.paper_account_id,
        live_trading_profile_id=profile.id, provider=provider, environment="production",
        product="BTC-USD", side="BUY", proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
        strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
        decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state="ACTIVATED",
        generated_at=datetime.now(timezone.utc), idempotency_key=f"idem-{uuid.uuid4()}", input_fingerprint="fp",
    )
    session.add(package)
    await session.flush()

    proof = ControlledProofRun(
        proof_id=uuid.uuid4(), status="PACKAGE_CREATED", provider=provider, environment="production",
        campaign_id=campaign_id, campaign_version=campaign_version, product_id="BTC-USD",
        max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30), package_id=package.package_id,
        sell_package_id=sell_package_id,
    )
    session.add(proof)
    await session.flush()
    package.market_evidence_identity = {
        **(package.market_evidence_identity or {}), "controlled_proof_id": str(proof.proof_id),
    }

    order = LiveCryptoOrder(
        live_crypto_order_id=live_crypto_order_id or uuid.uuid4(), crypto_order_preview_id=package.crypto_order_preview_id,
        exchange_connection_id=connection.exchange_connection_id, provider=provider, environment="production",
        product_id="BTC-USD", side=side, order_type="MARKET", requested_quote_size=Decimal("5"),
        client_order_id=f"buy-{uuid.uuid4()}", status="FILLED", provider_order_id=provider_order_id,
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=15),
        filled_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        audit_correlation_id=proof.audit_correlation_id,
        safe_provider_response={"live_trading_profile_id": str(profile.id)},
    )
    session.add(order)
    await session.flush()

    claim = AutonomousExecutionClaim(
        claim_id=uuid.uuid4(), package_id=package.package_id, activation_id=uuid.uuid4(),
        campaign_id=campaign_id, campaign_version=campaign_version, mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
        account_id=profile.paper_account_id, profile_id=profile.id, connection_id=connection.exchange_connection_id,
        provider=provider, environment="production", product="BTC-USD", side=side,
        claim_status="SUBMISSION_PENDING", claimed_at=datetime.now(timezone.utc), claim_owner="test",
        live_order_id=order.live_crypto_order_id,
    )
    session.add(claim)
    await session.flush()

    source_event = await ensure_execution_source(db=session, live_order=order, profile=profile)

    filled_order_event = LiveReconciliationEvent(
        idempotency_key=f"lco-reconcile:{order.live_crypto_order_id}:status:filled:FILLED",
        event_hash=f"hash-order-{order.live_crypto_order_id}",
        live_trading_profile_id=profile.id, live_crypto_order_id=order.live_crypto_order_id,
        capital_campaign_id=None, source_execution_event_id=source_event.id,
        source_execution_event_type="execution_intent_created", sequence_number=1, event_type="order_reconciled",
        reconciliation_status="filled", provider_name=provider, provider_order_id=provider_order_id,
        provider_fill_id=None, event_payload={}, provenance={"requested_by": "system:reconciliation_scheduler"},
        immutable_contract_version="v1", provider_recorded_at=None,
        recorded_at=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    session.add(filled_order_event)
    await session.flush()

    stale_partial_event = LiveReconciliationEvent(
        idempotency_key=f"lco-reconcile:{order.live_crypto_order_id}:fill:fill-1",
        event_hash=f"hash-fill-{order.live_crypto_order_id}",
        live_trading_profile_id=profile.id, live_crypto_order_id=order.live_crypto_order_id,
        capital_campaign_id=None, source_execution_event_id=source_event.id,
        source_execution_event_type="execution_intent_created", sequence_number=2, event_type="fill_reconciled",
        reconciliation_status="partially_filled", provider_name=provider, provider_order_id=provider_order_id,
        provider_fill_id="fill-1", event_payload={}, provenance={"requested_by": "system:reconciliation_scheduler"},
        immutable_contract_version="v1", provider_recorded_at=None,
        recorded_at=datetime.now(timezone.utc) - timedelta(minutes=9),
    )
    session.add(stale_partial_event)
    await session.flush()

    accounting = LiveAccountingRecord(
        idempotency_key=f"lco-reconcile:{order.live_crypto_order_id}:fill:fill-1:fill",
        live_trading_profile_id=profile.id, live_crypto_order_id=order.live_crypto_order_id,
        capital_campaign_id=None, reconciliation_event_id=stale_partial_event.id, source_execution_event_id=source_event.id,
        source_execution_event_type="execution_intent_created", record_type="partial_fill_accounting",
        provider_order_id=provider_order_id, provider_fill_id="fill-1", symbol="BTC-USD", side="buy",
        filled_quantity=ledger_quantity, fill_price=Decimal("64110.78"), gross_notional=Decimal("5.00"),
        fee_amount=Decimal("0.01"), fee_currency="USD", net_cash_impact=Decimal("-5.01"), provenance={},
        provider_fill_timestamp=None, recorded_at=datetime.now(timezone.utc) - timedelta(minutes=9),
    )
    session.add(accounting)
    await session.flush()

    return _StaleFixture(
        proof=proof, order=order, filled_order_event=filled_order_event,
        stale_partial_event=stale_partial_event, accounting=accounting,
    )


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


class _FakeProvider:
    def __init__(self, *, order: _FakeProviderOrder | None) -> None:
        self.order = order

    async def lookup_order(self, **_kwargs):
        return self.order


def _viqc_filled_order(*, provider_order_id: str = _PROVIDER_ORDER_ID, status: str = "FILLED") -> _FakeProviderOrder:
    return _FakeProviderOrder(
        provider_order_id=provider_order_id, client_order_id=None, product_id=None, side="BUY", status=status,
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=10), acknowledged_at=None,
        raw={
            "descr": {"pair": "XBTUSD", "type": "buy", "ordertype": "market"},
            "oflags": "fciq,viqc", "vol": "5.00000000", "cost": "5.00000000",
            "status": "closed" if status == "FILLED" else "open",
        },
    )


def _patch_provider(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider, *, connection: ExchangeConnection) -> None:
    async def _load_exchange_connection(*, db, exchange_connection_id):
        return connection

    monkeypatch.setattr("app.services.live_crypto_orders._load_exchange_connection", _load_exchange_connection)
    monkeypatch.setattr(
        "app.services.live_crypto_orders._load_decrypted_credentials",
        lambda _c: {"api_key": "k", "api_secret": "s", "passphrase": ""},
    )
    monkeypatch.setattr("app.services.live.reconciliation_correction.get_exchange_provider", lambda *_a, **_k: provider)


# --- happy path -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_guards_pass_and_correction_appends_exactly_one_terminal_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile, live_crypto_order_id=_LIVE_ORDER_ID)
        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order()), connection=connection)

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )

        assert outcome.eligible is True
        assert outcome.applied is True
        assert outcome.already_applied is False
        assert outcome.blocked_reason is None
        assert outcome.provider_order_id == _PROVIDER_ORDER_ID
        assert outcome.prior_effective_status == "partially_filled"
        assert outcome.provider_confirmed_status == "FILLED"
        assert outcome.reconciliation_event_id is not None

        events = (await session.scalars(
            select(LiveReconciliationEvent).where(
                LiveReconciliationEvent.live_crypto_order_id == fixture.order.live_crypto_order_id,
            )
        )).all()
        assert len(events) == 3  # the original 2 plus exactly one new correction event

        correction_event = await session.get(LiveReconciliationEvent, outcome.reconciliation_event_id)
        assert correction_event is not None
        assert correction_event.reconciliation_status == "filled"
        assert correction_event.sequence_number == 3
        assert correction_event.provenance["reason"] == "stale_viqc_classification_corrected"
        assert correction_event.provenance["original_reconciliation_event_id"] == str(fixture.stale_partial_event.id)
        assert correction_event.provenance["provider_order_id"] == _PROVIDER_ORDER_ID
        assert correction_event.provenance["live_crypto_order_id"] == str(fixture.order.live_crypto_order_id)
        assert correction_event.provenance["prior_effective_status"] == "partially_filled"
        assert correction_event.provenance["provider_confirmed_status"] == "FILLED"
        assert correction_event.provenance["operator_identity"] == _ACTOR
        assert "corrected_at" in correction_event.provenance

        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.entity_id == fixture.order.live_crypto_order_id,
                AuditLog.action == "live_crypto_order.stale_viqc_reconciliation_corrected",
            )
        )
        assert audit is not None
        assert audit.actor == _ACTOR


@pytest.mark.asyncio
async def test_historical_rows_remain_unchanged_after_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order()), connection=connection)

        before_filled = {
            "reconciliation_status": fixture.filled_order_event.reconciliation_status,
            "sequence_number": fixture.filled_order_event.sequence_number,
            "event_hash": fixture.filled_order_event.event_hash,
        }
        before_stale = {
            "reconciliation_status": fixture.stale_partial_event.reconciliation_status,
            "sequence_number": fixture.stale_partial_event.sequence_number,
            "event_hash": fixture.stale_partial_event.event_hash,
        }
        before_accounting = {
            "record_type": fixture.accounting.record_type,
            "filled_quantity": fixture.accounting.filled_quantity,
        }

        await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )

        refreshed_filled = await session.get(LiveReconciliationEvent, fixture.filled_order_event.id)
        refreshed_stale = await session.get(LiveReconciliationEvent, fixture.stale_partial_event.id)
        refreshed_accounting = await session.get(LiveAccountingRecord, fixture.accounting.id)

        assert refreshed_filled.reconciliation_status == before_filled["reconciliation_status"]
        assert refreshed_filled.sequence_number == before_filled["sequence_number"]
        assert refreshed_filled.event_hash == before_filled["event_hash"]
        assert refreshed_stale.reconciliation_status == before_stale["reconciliation_status"]
        assert refreshed_stale.sequence_number == before_stale["sequence_number"]
        assert refreshed_stale.event_hash == before_stale["event_hash"]
        assert refreshed_accounting.record_type == before_accounting["record_type"]
        assert refreshed_accounting.filled_quantity == before_accounting["filled_quantity"]


@pytest.mark.asyncio
async def test_duplicate_correction_calls_are_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order()), connection=connection)

        first = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        # A second call must not even need to reach the provider again to
        # detect the prior correction -- swap in a provider that would fail
        # every guard, proving the idempotency short-circuit happens first.
        _patch_provider(monkeypatch, _FakeProvider(order=None), connection=connection)
        second = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )

        assert first.applied is True
        assert first.already_applied is False
        assert second.applied is True
        assert second.already_applied is True
        assert second.reconciliation_event_id == first.reconciliation_event_id
        assert second.idempotency_key == first.idempotency_key

        events = (await session.scalars(
            select(LiveReconciliationEvent).where(
                LiveReconciliationEvent.live_crypto_order_id == fixture.order.live_crypto_order_id,
            )
        )).all()
        assert len(events) == 3  # still exactly one correction event, never two


@pytest.mark.asyncio
async def test_dry_run_reports_eligible_without_mutating(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order()), connection=connection)

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR, dry_run=True,
        )

        assert outcome.eligible is True
        assert outcome.applied is False
        assert outcome.already_applied is False
        assert outcome.provider_confirmed_status == "FILLED"
        assert outcome.reconciliation_event_id is None

        events = (await session.scalars(
            select(LiveReconciliationEvent).where(
                LiveReconciliationEvent.live_crypto_order_id == fixture.order.live_crypto_order_id,
            )
        )).all()
        assert len(events) == 2  # nothing appended

        audit = await session.scalar(
            select(AuditLog).where(AuditLog.entity_id == fixture.order.live_crypto_order_id)
        )
        assert audit is None


# --- fail-closed guards ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_fails_closed_when_order_not_found() -> None:
    async with _real_session() as session:
        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=uuid.uuid4(), operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "live_order_not_found"


@pytest.mark.asyncio
async def test_fails_closed_when_not_a_buy_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile, side="SELL")

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "not_a_buy_order"


@pytest.mark.asyncio
async def test_fails_closed_when_not_kraken_provider() -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile, provider="coinbase_advanced")

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "not_kraken_provider"


@pytest.mark.asyncio
async def test_fails_closed_when_latest_state_is_not_stale_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a later, legitimate pass already resolved the order to "filled"
    (or anything other than the stale partial shape), there is nothing to
    correct -- must fail closed rather than append a redundant event."""
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)

        already_resolved = LiveReconciliationEvent(
            idempotency_key=f"lco-reconcile:{fixture.order.live_crypto_order_id}:already-resolved",
            event_hash=f"hash-resolved-{fixture.order.live_crypto_order_id}",
            live_trading_profile_id=profile.id, live_crypto_order_id=fixture.order.live_crypto_order_id,
            capital_campaign_id=None, source_execution_event_id=fixture.filled_order_event.source_execution_event_id,
            source_execution_event_type="execution_intent_created", sequence_number=3, event_type="order_reconciled",
            reconciliation_status="filled", provider_name="kraken_spot", provider_order_id=_PROVIDER_ORDER_ID,
            provider_fill_id=None, event_payload={}, provenance={"requested_by": "system"},
            immutable_contract_version="v1", provider_recorded_at=None, recorded_at=datetime.now(timezone.utc),
        )
        session.add(already_resolved)
        await session.flush()

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "latest_reconciliation_not_stale_partial"
        assert outcome.prior_effective_status == "filled"


@pytest.mark.asyncio
async def test_fails_closed_when_no_positive_ledger_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(
            session, connection=connection, profile=profile, ledger_quantity=Decimal("0"),
        )

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "no_positive_ledger_quantity"


@pytest.mark.asyncio
async def test_fails_closed_when_sell_already_proposed(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(
            session, connection=connection, profile=profile, sell_package_id=uuid.uuid4(),
        )

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "sell_already_proposed"


@pytest.mark.asyncio
async def test_fails_closed_when_provider_order_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        _patch_provider(monkeypatch, _FakeProvider(order=None), connection=connection)

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "provider_order_not_found_or_mismatched"


@pytest.mark.asyncio
async def test_fails_closed_when_provider_reports_not_terminal_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order(status="OPEN")), connection=connection)

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "provider_order_not_terminal_filled"
        assert outcome.provider_confirmed_status == "OPEN"


@pytest.mark.asyncio
async def test_fails_closed_when_not_viqc_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        base_sized_order = _FakeProviderOrder(
            provider_order_id=_PROVIDER_ORDER_ID, client_order_id=None, product_id=None, side="BUY", status="FILLED",
            submitted_at=datetime.now(timezone.utc), acknowledged_at=None,
            raw={"descr": {"pair": "XBTUSD", "type": "buy", "ordertype": "market"}, "oflags": "fciq", "vol": "0.0001", "status": "closed"},
        )
        _patch_provider(monkeypatch, _FakeProvider(order=base_sized_order), connection=connection)

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.eligible is False
        assert outcome.blocked_reason == "not_viqc_shape"


# --- downstream effects: SELL becomes proposable ---------------------------------------

@pytest.mark.asyncio
async def test_has_unresolved_reconciliation_clears_and_sell_remains_eligible_after_correction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)

        assert await has_unresolved_reconciliation(
            db=session, provider="kraken_spot", environment="production", product="BTC-USD",
        ) is True

        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order()), connection=connection)
        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.applied is True

        assert await has_unresolved_reconciliation(
            db=session, provider="kraken_spot", environment="production", product="BTC-USD",
        ) is False

        refreshed_proof = await session.get(ControlledProofRun, fixture.proof.proof_id)
        assert await should_propose_controlled_sell(db=session, proof=refreshed_proof) is True


class _ReachedRuntimeCampaignLookup(Exception):
    """Sentinel proving _attempt_operator_controlled_proof_entry reached
    past both the open-live-order and unresolved-reconciliation gates."""


@pytest.mark.asyncio
async def test_continuous_worker_proceeds_past_reconciliation_gate_after_correction(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Proves the unmodified worker code -- not a reimplementation of its
    gate logic -- actually proceeds once the correction is applied. The
    worker's own _attempt_operator_controlled_proof_entry checks
    should_propose_controlled_sell, then _has_open_live_order, then
    _has_unresolved_reconciliation (continuous_pipeline_worker.py ~1999-2032)
    before ever calling _load_runtime_campaign. Raising from a monkeypatched
    _load_runtime_campaign proves execution reached exactly that point --
    i.e. passed the reconciliation gate -- without needing the full
    downstream runtime-campaign/paper-account/risk-engine fixture this test
    is not exercising."""
    import app.services.orchestration.continuous_pipeline_worker as worker

    async with _real_session() as session:
        connection = await _seed_connection(session)
        profile = await _seed_live_profile(session)
        fixture = await _seed_stale_viqc_buy(session, connection=connection, profile=profile)
        _patch_provider(monkeypatch, _FakeProvider(order=_viqc_filled_order()), connection=connection)

        outcome = await correct_stale_viqc_reconciliation(
            db=session, live_crypto_order_id=fixture.order.live_crypto_order_id, operator_identity=_ACTOR,
        )
        assert outcome.applied is True

        async def _reached(**_kwargs):
            raise _ReachedRuntimeCampaignLookup()

        monkeypatch.setattr(worker, "_load_runtime_campaign", _reached)

        # claim_controlled_proof_by_id's own expires_at <= now comparison
        # fails under sqlite (DateTime(timezone=True) round-trips tz-naive
        # here, a pre-existing sqlite-test-environment limitation unrelated
        # to this fix). Bypass just that locking mechanic -- not what this
        # test is about -- by returning the already-active proof directly.
        async def _claim_stub(*, db, proof_id, cycle_id=None):
            return await db.get(ControlledProofRun, proof_id)

        monkeypatch.setattr(worker, "claim_controlled_proof_by_id", _claim_stub)

        # _attempt_operator_controlled_proof_entry catches and logs
        # exceptions internally rather than propagating them (it always
        # commits/records rather than crashing the worker loop), so the
        # sentinel surfaces in its error log record, not as a raised
        # exception here.
        with caplog.at_level(logging.INFO):
            await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=fixture.proof.proof_id)

        messages = [record.message for record in caplog.records]
        assert any("controlled_proof_sell_eligible" in message for message in messages)
        assert any("_ReachedRuntimeCampaignLookup" in message for message in messages)
        assert not any("unresolved_reconciliation_exists" in message for message in messages)
        assert not any("open_live_order_exists" in message for message in messages)
