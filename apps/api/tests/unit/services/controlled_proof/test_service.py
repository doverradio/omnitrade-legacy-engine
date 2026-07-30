from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.controlled_proof_run import ControlledProofRun
from app.models.decision_record import DecisionRecord
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.controlled_proof import service as controlled_proof_service
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session, real_sqlite_session_factory

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_ALL_TABLES = [
    Asset.__table__, AuditLog.__table__, AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__, AutonomousCapitalMandateAuthorization.__table__,
    AutonomousCapitalMandateEvaluation.__table__, AutonomousExecutionClaim.__table__, Candle.__table__,
    CanonicalPreviewPackage.__table__,
    CapitalCampaign.__table__, CapitalCampaignDefinition.__table__, ControlledProofExitRecovery.__table__,
    ControlledProofRun.__table__,
    DecisionRecord.__table__, LiveAccountingRecord.__table__, LiveCryptoOrder.__table__,
    LiveReconciliationEvent.__table__, LiveTradingProfile.__table__, StrategyRosterRun.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _seed_campaign(
    session: AsyncSession, *, allowed_instruments: list[str] = ("BTC-USD",), version: int = 1,
) -> uuid.UUID:
    paper_account_id = uuid.uuid4()
    session.add(CapitalCampaignDefinition(
        campaign_id=_CAMPAIGN_ID, version=version, name="test", owner_identity="operator:test", status="READY",
        capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
        allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=list(allowed_instruments),
        campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
        minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
        profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
        compounding_policy={"policy_type": "FIXED_CAPITAL"},
    ))
    session.add(CapitalCampaign(
        uuid=_CAMPAIGN_ID, owner="operator:test", name="test", status="READY", campaign_type="definition_pinned_runtime",
        definition_campaign_id=_CAMPAIGN_ID, definition_version=version, paper_account_id=paper_account_id,
        starting_capital=Decimal("25"), current_equity=Decimal("25"),
    ))
    session.add(LiveTradingProfile(
        id=uuid.uuid4(), paper_account_id=paper_account_id, operating_mode="live", lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True, paper_default_mode=True,
        governance_approved=True, risk_authority_model="risk_engine_final", autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False, automatic_promotion_enabled=False, provenance_metadata={},
    ))
    await session.flush()
    return paper_account_id


async def _seed_asset_with_fresh_candles(session: AsyncSession, *, symbol: str = "BTC") -> Asset:
    asset = Asset(symbol=symbol, asset_class="crypto", exchange="kraken_spot", base_currency="USD", is_active=True)
    session.add(asset)
    await session.flush()
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    for i in range(60, 0, -1):
        open_time = now - timedelta(minutes=15 * i)
        session.add(Candle(
            asset_id=asset.id, interval="15m", open_time=open_time, close_time=open_time + timedelta(minutes=15),
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("1"), source="kraken_spot",
        ))
    await session.flush()
    return asset


async def _seed_active_mandate(
    session: AsyncSession, *, mandate_id: uuid.UUID, allowed_products: tuple[str, ...] = ("BTC-USD",),
) -> None:
    session.add(AutonomousCapitalMandate(
        mandate_id=mandate_id, owner_actor_id="operator:owner", status="ACTIVE", autonomy_level="LEVEL_2",
        provider="kraken_spot", exchange_environment="production", exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), capital_campaign_id=None,
    ))
    version = AutonomousCapitalMandateVersion(
        mandate_version_id=uuid.uuid4(), mandate_id=mandate_id, version_number=1, version_hash="h1",
        base_currency="USD", authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("5"),
        max_daily_realized_loss_usd=Decimal("1"), max_campaign_drawdown_usd=Decimal("1"),
        max_consecutive_losses=2, position_limit=1, price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("20"), max_fee_bps=Decimal("50"), allowed_products=list(allowed_products),
        allowed_order_sides=["BUY", "SELL"], allowed_strategy_versions=[_STRATEGY_IDENTITY],
        entry_policy={}, exit_policy={}, cooldown_policy={}, operating_schedule={}, approval_policy="MANDATE_ALLOWED",
        reconciliation_policy={}, kill_switch_policy={}, owner_acknowledgements={"a": True},
        authorization_evidence_summary={"b": True}, is_authorized=True, is_active=True,
    )
    session.add(version)
    session.add(AutonomousCapitalMandateAuthorization(
        mandate_id=mandate_id, mandate_version_id=version.mandate_version_id, authorization_state="AUTHORIZED",
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE", authorized_by_actor_id="operator:owner",
        authorization_method="test", owner_acknowledgements={"a": True}, authorization_evidence={"b": True},
        deterministic_explanation={"c": True}, idempotency_key=f"auth-{mandate_id}",
    ))
    await session.flush()


def _fully_ready_settings(*, mandate_id: uuid.UUID, additional_products: str = ""):
    from types import SimpleNamespace
    return SimpleNamespace(
        automatic_mandate_package_activation_mandate_id=mandate_id,
        automatic_mandate_package_activation_campaign_id=_CAMPAIGN_ID,
        asset_discovery_mode="env",
        autonomous_cycle_additional_products=additional_products,
        parsed_autonomous_cycle_additional_products=[p.strip() for p in additional_products.split(",") if p.strip()],
    )


async def _seed_fully_ready_scope(session: AsyncSession, monkeypatch: pytest.MonkeyPatch, *, version: int = 1) -> uuid.UUID:
    """BTC-USD is always runtime_selected (canonical product); everything
    else must be established explicitly. Returns the mandate_id."""
    mandate_id = uuid.uuid4()
    await _seed_campaign(session, allowed_instruments=["BTC-USD"], version=version)
    await _seed_asset_with_fresh_candles(session, symbol="BTC")
    await _seed_active_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
    monkeypatch.setattr(
        "app.services.asset_commissioning.service.get_settings",
        lambda: _fully_ready_settings(mandate_id=mandate_id),
    )
    return mandate_id


# --- creation: scope, readiness, idempotency, exclusion -----------------------------

@pytest.mark.asyncio
async def test_create_requires_full_readiness_and_returns_server_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-1", expires_in_minutes=30, actor="operator:alice",
        )
        assert proof.provider == "kraken_spot"
        assert proof.environment == "production"
        assert proof.campaign_id == _CAMPAIGN_ID
        assert proof.campaign_version == 1
        assert proof.max_notional_usd == Decimal("5")
        assert proof.status == "REQUESTED"

        audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == proof.proof_id, AuditLog.action == "controlled_proof_run.requested")
        )).scalars().all()
        assert len(audits) == 1


# --- campaign version: resolved dynamically from the governing definition -----------

@pytest.mark.asyncio
async def test_create_resolves_campaign_version_dynamically_from_governing_definition(monkeypatch: pytest.MonkeyPatch) -> None:
    """No hardcoded version pin: whatever CapitalCampaignDefinition version
    the runtime is currently pinned to as READY-and-governing is what a new
    proof is scoped to -- proven here with a governing version that is NOT 1,
    which the old ALLOWED_CAMPAIGN_VERSION=1 constant would have rejected."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch, version=7)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-v7", expires_in_minutes=30, actor="operator:alice",
        )
        assert proof.campaign_id == _CAMPAIGN_ID
        assert proof.campaign_version == 7

        audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == proof.proof_id, AuditLog.action == "controlled_proof_run.requested")
        )).scalars().all()
        assert audits[0].after_state["campaign_version"] == 7


@pytest.mark.asyncio
async def test_create_fails_closed_when_no_campaign_is_currently_governing() -> None:
    """No CapitalCampaignDefinition/CapitalCampaign seeded at all -- the
    dynamic resolver must fail closed with a clear error, not raise an
    unrelated AttributeError from treating None as a governing definition."""
    async with _real_session() as session:
        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-no-campaign",
                expires_in_minutes=30, actor="operator:alice",
            )
        assert "governing" in str(excinfo.value.message).lower()
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_create_resolves_the_version_governing_at_request_time_not_a_stale_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two sequential requests, with a real campaign-version promotion in
    between (a new CapitalCampaignDefinition row + runtime repin, exactly
    what transition_canonical_campaign_status performs) -- proves resolution
    happens fresh on every call, not once and cached, and that an earlier
    proof's already-persisted campaign_version is never retroactively
    changed by a later promotion."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch, version=1)
        first, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-before-promotion",
            expires_in_minutes=30, actor="operator:alice",
        )
        first_proof_id = first.proof_id
        assert first.campaign_version == 1
        await controlled_proof_service.cancel_controlled_proof(
            db=session, proof_id=first_proof_id, actor="operator:alice", reason="testing",
        )
        # SQLite (unlike the real Postgres uq_controlled_proof_runs_single_active
        # partial index it stands in for) enforces "(1)" as a table-wide
        # unique value regardless of status -- this test isolates campaign-
        # version resolution, not single-active-proof exclusivity (already
        # covered by test_second_concurrent_request_is_excluded_while_one_is_active),
        # so the terminal row is removed here the same way that existing test
        # suite's own SQLite-limitation convention already does elsewhere.
        first_row = await session.get(ControlledProofRun, first_proof_id)
        await session.delete(first_row)
        await session.flush()

        # Simulate a real governed promotion: a new governing definition row,
        # runtime repinned to it -- exactly the two effects
        # transition_canonical_campaign_status produces.
        session.add(CapitalCampaignDefinition(
            campaign_id=_CAMPAIGN_ID, version=2, name="test", owner_identity="operator:test", status="READY",
            capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
            allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=["BTC-USD"],
            campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
            minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
            profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
            compounding_policy={"policy_type": "FIXED_CAPITAL"},
        ))
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        runtime.definition_version = 2
        await session.flush()

        second, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-after-promotion",
            expires_in_minutes=30, actor="operator:alice",
        )

        assert second.campaign_version == 2
        assert first.campaign_version == 1  # never mutated by the later promotion


@pytest.mark.asyncio
async def test_create_is_idempotent_on_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        first, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-dup", expires_in_minutes=30, actor="operator:alice",
        )
        second, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-dup", expires_in_minutes=30, actor="operator:alice",
        )
        assert first.proof_id == second.proof_id
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_second_concurrent_request_is_excluded_while_one_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-a", expires_in_minutes=30, actor="operator:alice",
        )
        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-b", expires_in_minutes=30, actor="operator:bob",
            )
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_expired_active_proof_no_longer_permanently_blocks_new_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: create_controlled_proof's own _reap_expired call must
    durably commit an expired active proof's EXPIRED transition immediately
    -- not merely flush it and leave it dependent on this request's own
    eventual success -- otherwise a later, unrelated failure in the same
    request rolls back the whole transaction via get_db()'s exception path,
    silently undoing the reap every time, so the same stale proof keeps
    blocking every future submission with "Another controlled proof is
    already active" forever."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-stale", expires_in_minutes=30, actor="operator:alice",
        )
        proof.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

        # Exactly what create_controlled_proof itself now does at its own
        # top: reap, then commit immediately, before the "already active"
        # check (the exact check that raises "Another controlled proof is
        # already active") ever runs.
        await controlled_proof_service._reap_expired(db=session)
        await session.commit()

        refreshed_stale = (
            await session.execute(select(ControlledProofRun).where(ControlledProofRun.proof_id == proof.proof_id))
        ).scalar_one()
        assert refreshed_stale.status == "EXPIRED"

        # The exact query create_controlled_proof's "already active" guard
        # runs -- must now find nothing, proving a fresh submission would no
        # longer be blocked.
        existing_active = await session.scalar(
            select(ControlledProofRun).where(ControlledProofRun.status.in_(controlled_proof_service._ACTIVE_STATES))
        )
        assert existing_active is None


@pytest.mark.asyncio
async def test_create_rejects_unauthorized_product_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="ETH-USD", idempotency_key="proof-eth", expires_in_minutes=30, actor="operator:alice",
            )
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_create_rejects_when_mandate_does_not_authorize_product(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        mandate_id = uuid.uuid4()
        await _seed_campaign(session, allowed_instruments=["BTC-USD"])
        await _seed_asset_with_fresh_candles(session, symbol="BTC")
        # Mandate exists and is ACTIVE, but its authorized version does not
        # include BTC-USD -- campaign_authorized True, mandate_authorized False.
        await _seed_active_mandate(session, mandate_id=mandate_id, allowed_products=("SOL-USD",))
        monkeypatch.setattr(
            "app.services.asset_commissioning.service.get_settings",
            lambda: _fully_ready_settings(mandate_id=mandate_id),
        )
        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-mandate", expires_in_minutes=30, actor="operator:alice",
            )
        assert "mandate_authorized" in str(excinfo.value.details.get("unmet_readiness"))


@pytest.mark.asyncio
async def test_create_rejects_stale_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        mandate_id = uuid.uuid4()
        await _seed_campaign(session, allowed_instruments=["BTC-USD"])
        asset = Asset(symbol="BTC", asset_class="crypto", exchange="kraken_spot", base_currency="USD", is_active=True)
        session.add(asset)
        await session.flush()
        stale_time = datetime.now(timezone.utc) - timedelta(hours=6)
        for i in range(60, 0, -1):
            open_time = stale_time - timedelta(minutes=15 * i)
            session.add(Candle(
                asset_id=asset.id, interval="15m", open_time=open_time, close_time=open_time + timedelta(minutes=15),
                open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
                volume=Decimal("1"), source="kraken_spot",
            ))
        await session.flush()
        await _seed_active_mandate(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(
            "app.services.asset_commissioning.service.get_settings",
            lambda: _fully_ready_settings(mandate_id=mandate_id),
        )
        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-stale", expires_in_minutes=30, actor="operator:alice",
            )
        assert "market_data_current" in str(excinfo.value.details.get("unmet_readiness"))


@pytest.mark.asyncio
async def test_create_rejects_when_open_position_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        session.add(LiveAccountingRecord(
            idempotency_key="fill-1", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="o1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("50"), fee_amount=Decimal("0.05"), fee_currency="USD",
            net_cash_impact=Decimal("-50.05"), provenance={}, recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()
        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-openpos", expires_in_minutes=30, actor="operator:alice",
            )


# --- BUY-to-position progression: the handoff automatic reconciliation feeds --------

@pytest.mark.asyncio
async def test_waiting_for_profitable_exit_remains_pending_for_periodic_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-periodic-sell-supervision",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.status = "WAITING_FOR_PROFITABLE_EXIT"
        await session.flush()

        assert await controlled_proof_service.find_pending_controlled_proof_id(db=session) == proof.proof_id

@pytest.mark.asyncio
async def test_should_propose_controlled_sell_becomes_true_once_buy_is_filled_and_reconciled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """should_propose_controlled_sell is the exact mechanism automatic
    reconciliation (reconciliation_scheduler.poll_unresolved_live_orders ->
    LiveCryptoOrderService.reconcile -> reconcile_live_order_and_fills)
    feeds: once that produces a real LiveAccountingRecord fill row and the
    resulting position snapshot shows a nonzero position, the Controlled
    Proof lifecycle must recognize the BUY as filled and become eligible to
    propose a SELL -- with no manual reconciliation call required."""
    async with _real_session() as session:
        mandate_id = await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-sell-eligible", expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        await session.flush()
        exact_buy_accounting: list[LiveAccountingRecord] = []

        async def _buy_lineage(**_kwargs):
            return SimpleNamespace(package_id=proof.package_id), SimpleNamespace(), SimpleNamespace(live_crypto_order_id=uuid.uuid4())

        async def _buy_accounting(**_kwargs):
            return exact_buy_accounting

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _buy_lineage)
        monkeypatch.setattr(controlled_proof_service, "_order_accounting", _buy_accounting)

        # Before any fill exists, the BUY has not been reconciled yet.
        with caplog.at_level(logging.INFO, logger=controlled_proof_service.logger.name):
            assert await controlled_proof_service.should_propose_controlled_sell(db=session, proof=proof) is False
        false_messages = [record.getMessage() for record in caplog.records]
        assert any(
            "controlled_proof_sell_evaluation" in message
            and "buy_fill_accounting_exists=false" in message
            and "position_nonzero=false" in message
            and "eligible=false" in message
            for message in false_messages
        )
        assert any(
            "controlled_proof_sell_ineligible" in message
            and "reason=buy_fill_accounting_exists" in message
            for message in false_messages
        )

        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        # Exactly the shape reconcile_live_order_and_fills persists for a
        # genuinely filled BUY -- this test does not re-implement or bypass
        # that logic, it proves the downstream consumer reacts correctly
        # once that authoritative record exists.
        fill = LiveAccountingRecord(
            idempotency_key="fill-buy-1", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="kraken-order-1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.005"), fee_currency="USD",
            net_cash_impact=Decimal("-5.005"), provenance={}, recorded_at=datetime.now(timezone.utc),
        )
        session.add(fill)
        await session.flush()
        exact_buy_accounting.append(fill)

        caplog.clear()
        with caplog.at_level(logging.INFO, logger=controlled_proof_service.logger.name):
            assert await controlled_proof_service.should_propose_controlled_sell(db=session, proof=proof) is True
        true_messages = [record.getMessage() for record in caplog.records]
        assert any(
            "controlled_proof_sell_evaluation" in message
            and "buy_package_linked=true" in message
            and "sell_package_unlinked=true" in message
            and "buy_claim_linked=true" in message
            and "buy_order_linked=true" in message
            and "buy_fill_accounting_exists=true" in message
            and "position_nonzero=true" in message
            and "eligible=true" in message
            for message in true_messages
        )
        assert not any("controlled_proof_sell_ineligible" in message for message in true_messages)


# --- resolve_controlled_proof_owned_quantity: canonical SELL sizing -----------------
#
# Regression coverage for the production defect where should_propose_
# controlled_sell (above) correctly reported eligible=true from canonical
# lineage while SELL preview creation still failed with
# canonical_owned_sell_quantity_missing, because the quantity resolver read
# the proof's buy_live_crypto_order_id/sell_live_crypto_order_id cache
# columns instead of the same canonical lineage.

def _fill(*, side: str, quantity: Decimal, record_type: str = "fill_accounting") -> SimpleNamespace:
    return SimpleNamespace(side=side, filled_quantity=quantity, record_type=record_type)


@pytest.mark.asyncio
async def test_resolve_owned_quantity_uses_remaining_canonical_position_after_partial_exit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement 1: a controlled proof with reconciled BUY fill accounting
    and a nonzero proof-owned position resolves exactly the remaining
    canonical quantity (BUY fills minus any already-reconciled SELL fills),
    via the same canonical lineage should_propose_controlled_sell trusts."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-owned-qty-remaining",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        proof.sell_package_id = uuid.uuid4()
        await session.flush()

        buy_order = SimpleNamespace(live_crypto_order_id=uuid.uuid4())
        sell_order = SimpleNamespace(live_crypto_order_id=uuid.uuid4())

        async def _lineage(*, db, proof, package_id, side):
            if side == "BUY":
                return SimpleNamespace(package_id=proof.package_id), SimpleNamespace(), buy_order
            return SimpleNamespace(package_id=proof.sell_package_id), SimpleNamespace(), sell_order

        async def _accounting(*, db, order):
            return [_fill(side="buy", quantity=Decimal("0.0002"))] if order is buy_order \
                else [_fill(side="sell", quantity=Decimal("0.00005"))]

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _lineage)
        monkeypatch.setattr(controlled_proof_service, "_order_accounting", _accounting)

        with caplog.at_level(logging.INFO, logger=controlled_proof_service.logger.name):
            quantity = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)

        assert quantity == Decimal("0.00015")
        assert any(
            "controlled_proof_owned_quantity_resolved" in record.getMessage()
            and "bought_quantity=0.0002" in record.getMessage()
            and "sold_quantity=0.00005" in record.getMessage()
            and "net_quantity=0.00015" in record.getMessage()
            for record in caplog.records
        )


@pytest.mark.asyncio
async def test_resolve_owned_quantity_ignores_unrelated_wallet_or_position_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 2: the SELL quantity must never pick up unrelated fill
    accounting for the same symbol -- proven against the real, unmocked
    _order_accounting query (only lineage resolution is faked), not just a
    mock asserting isolation."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-owned-qty-isolated",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        await session.flush()

        own_order_id = uuid.uuid4()
        own_order = SimpleNamespace(
            live_crypto_order_id=own_order_id, provider_order_id="kraken-own-buy",
            product_id="BTC-USD", side="BUY",
        )

        async def _lineage(*, db, proof, package_id, side):
            assert side == "BUY"
            return SimpleNamespace(package_id=proof.package_id), SimpleNamespace(), own_order

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _lineage)
        # _order_accounting is left real/unmocked here on purpose.

        def _accounting_row(*, live_crypto_order_id, provider_order_id, quantity):
            return LiveAccountingRecord(
                idempotency_key=f"{provider_order_id}-fill", live_trading_profile_id=uuid.uuid4(),
                live_crypto_order_id=live_crypto_order_id, capital_campaign_id=None,
                reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
                source_execution_event_type="execution_intent_created", record_type="fill_accounting",
                provider_order_id=provider_order_id, symbol="BTC-USD", side="buy",
                filled_quantity=quantity, fill_price=Decimal("50000"),
                gross_notional=quantity * Decimal("50000"), fee_amount=Decimal("0.01"),
                fee_currency="USD", net_cash_impact=Decimal("0"), provenance={},
                recorded_at=datetime.now(timezone.utc),
            )

        session.add(_accounting_row(
            live_crypto_order_id=own_order_id, provider_order_id="kraken-own-buy", quantity=Decimal("0.0002"),
        ))
        # Unrelated order/wallet activity for the same symbol -- a much
        # larger quantity that must never leak into this proof's own SELL
        # sizing.
        session.add(_accounting_row(
            live_crypto_order_id=uuid.uuid4(), provider_order_id="kraken-unrelated-buy", quantity=Decimal("5"),
        ))
        await session.flush()

        quantity = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)
        assert quantity == Decimal("0.0002")


@pytest.mark.asyncio
async def test_resolve_owned_quantity_fails_closed_when_buy_lineage_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Requirement 3: no resolvable BUY lineage (ABSENT/PACKAGE_ONLY/CLAIM_
    ONLY -- _proof_leg_lineage resolves `order` to None in every one of
    those states) fails closed to 0, the exact input the caller's existing
    <= 0 guard turns into canonical_owned_sell_quantity_missing -- never
    fabricates a nonzero quantity."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-owned-qty-missing-lineage",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        await session.flush()

        async def _absent_lineage(**_kwargs):
            return None, None, None

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _absent_lineage)

        with caplog.at_level(logging.INFO, logger=controlled_proof_service.logger.name):
            quantity = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)

        assert quantity == Decimal("0")
        assert any(
            "controlled_proof_owned_quantity_unresolved" in record.getMessage()
            and "reason=buy_lineage_unresolved" in record.getMessage()
            for record in caplog.records
        )


@pytest.mark.asyncio
async def test_resolve_owned_quantity_fails_closed_on_ambiguous_or_conflicting_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 4: an ambiguous/conflicting BUY leg -- multiple execution
    claims, a scope-mismatched ("foreign") order, or any other INCONSISTENT
    state -- resolves `order` to None inside _proof_leg_lineage itself
    (resolve_controlled_proof_leg_execution_lineage's own state machine,
    covered by its own test suite and unchanged by this fix). What this
    test proves is that resolve_controlled_proof_owned_quantity treats that
    None identically to "no provable quantity", the same as the missing-
    lineage case, rather than e.g. falling back to a package-only guess."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-owned-qty-ambiguous",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        await session.flush()

        async def _inconsistent_lineage(**_kwargs):
            # Mirrors resolve_controlled_proof_leg_execution_lineage's own
            # INCONSISTENT return shape (package resolved, order is not).
            return SimpleNamespace(package_id=proof.package_id), None, None

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _inconsistent_lineage)

        quantity = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)
        assert quantity == Decimal("0")


@pytest.mark.asyncio
async def test_resolve_owned_quantity_is_zero_when_position_already_fully_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5: BUY and SELL fills that exactly net to zero (position
    already fully closed) resolve to Decimal("0"), not a negative or stale
    value -- the caller's existing <= 0 guard still fails closed on it."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-owned-qty-fully-closed",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        proof.sell_package_id = uuid.uuid4()
        await session.flush()

        buy_order = SimpleNamespace(live_crypto_order_id=uuid.uuid4())
        sell_order = SimpleNamespace(live_crypto_order_id=uuid.uuid4())

        async def _lineage(*, db, proof, package_id, side):
            return (
                (SimpleNamespace(package_id=proof.package_id), SimpleNamespace(), buy_order) if side == "BUY"
                else (SimpleNamespace(package_id=proof.sell_package_id), SimpleNamespace(), sell_order)
            )

        async def _accounting(*, db, order):
            return [_fill(side="buy", quantity=Decimal("0.0002"))] if order is buy_order \
                else [_fill(side="sell", quantity=Decimal("0.0002"))]

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _lineage)
        monkeypatch.setattr(controlled_proof_service, "_order_accounting", _accounting)

        quantity = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)
        assert quantity == Decimal("0")


@pytest.mark.asyncio
async def test_resolve_owned_quantity_is_a_pure_repeatable_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 6: repeated execution is idempotent -- calling the
    resolver twice against unchanged state returns the same result and
    performs no writes (no db.add/flush/commit anywhere in the function)."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-owned-qty-idempotent",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.package_id = uuid.uuid4()
        await session.flush()

        buy_order = SimpleNamespace(live_crypto_order_id=uuid.uuid4())

        async def _lineage(**_kwargs):
            return SimpleNamespace(package_id=proof.package_id), SimpleNamespace(), buy_order

        async def _accounting(**_kwargs):
            return [_fill(side="buy", quantity=Decimal("0.0002"))]

        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _lineage)
        monkeypatch.setattr(controlled_proof_service, "_order_accounting", _accounting)

        first = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)
        second = await controlled_proof_service.resolve_controlled_proof_owned_quantity(db=session, proof=proof)

        assert first == second == Decimal("0.0002")
        assert len(session.new) == 0 and len(session.dirty) == 0


# --- replace-active: operator-safe supersede of a stalled active proof ---------------

@pytest.mark.asyncio
async def test_replace_active_safely_cancels_and_replaces_when_no_live_capital_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-old", expires_in_minutes=30, actor="operator:alice",
        )
        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-new", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )
        assert replaced is not None
        assert replaced.proof_id == old.proof_id
        assert new.proof_id != old.proof_id
        assert new.status == "REQUESTED"

        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 2
        active = [r for r in rows if r.status in controlled_proof_service._ACTIVE_STATES]
        assert [r.proof_id for r in active] == [new.proof_id]


@pytest.mark.asyncio
async def test_replace_active_old_proof_remains_stored_as_cancelled_with_audit_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-old2", expires_in_minutes=30, actor="operator:alice",
        )
        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-new2", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )

        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status == "CANCELLED"
        assert refreshed_old.cancelled_by == "operator:bob"
        assert refreshed_old.cancelled_at is not None

        cancel_audit = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == old.proof_id, AuditLog.action == "controlled_proof_run.cancelled")
        )).scalar_one()
        assert cancel_audit.after_state["reason"] == "replaced_by_operator_request"

        link_audit = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == new.proof_id, AuditLog.action == "controlled_proof_run.replaced")
        )).scalar_one()
        assert link_audit.before_state["replaced_proof_id"] == str(old.proof_id)
        assert link_audit.after_state["new_proof_id"] == str(new.proof_id)


@pytest.mark.asyncio
async def test_replace_active_refused_when_live_buy_order_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-buy", expires_in_minutes=30, actor="operator:alice",
        )
        old.buy_live_crypto_order_id = uuid.uuid4()
        await session.flush()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-buy-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert excinfo.value.details.get("blocker") == "live_buy_order_exists"

        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status not in ("CANCELLED",)
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_refused_when_live_sell_order_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-sell", expires_in_minutes=30, actor="operator:alice",
        )
        old.sell_live_crypto_order_id = uuid.uuid4()
        await session.flush()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-sell-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert excinfo.value.details.get("blocker") == "live_sell_order_exists"
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_refused_when_open_position_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-pos", expires_in_minutes=30, actor="operator:alice",
        )
        old.position_id = "pos-live-1"
        await session.flush()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-pos-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert excinfo.value.details.get("blocker") == "open_position_exists"
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


# --- replace-active: live-capital detection must not depend on a column that is only
# --- ever backfilled as a side effect of get_controlled_proof_view being polled -------

@pytest.mark.asyncio
async def test_replace_active_refused_when_unresolved_buy_order_exists_without_backfilled_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: buy_live_crypto_order_id is only ever written by
    get_controlled_proof_view's own opportunistic backfill -- a fully
    unattended run has no reason to call it, so a genuinely submitted,
    unresolved BUY order must still be detected even when that column was
    never populated. A false negative here would let create_controlled_proof
    cancel a proof that may control real funds."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-unresolved-buy", expires_in_minutes=30, actor="operator:alice",
        )
        session.add(LiveCryptoOrder(
            live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider=old.provider, environment=old.environment, product_id=old.product_id, side="BUY",
            order_type="market", requested_quote_size=Decimal("5"), client_order_id=str(uuid.uuid4()),
            status="SUBMISSION_PENDING", submitted_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        ))
        await session.flush()
        assert old.buy_live_crypto_order_id is None  # never backfilled -- the exact production gap

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-unresolved-buy-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert excinfo.value.details.get("blocker") == "live_buy_order_exists"
        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status not in ("CANCELLED",)
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_refused_when_unresolved_sell_order_exists_without_backfilled_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-unresolved-sell", expires_in_minutes=30, actor="operator:alice",
        )
        session.add(LiveCryptoOrder(
            live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider=old.provider, environment=old.environment, product_id=old.product_id, side="SELL",
            order_type="market", requested_quote_size=Decimal("5"), client_order_id=str(uuid.uuid4()),
            status="RECONCILIATION_REQUIRED", submitted_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        ))
        await session.flush()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-unresolved-sell-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert excinfo.value.details.get("blocker") == "live_sell_order_exists"


@pytest.mark.asyncio
async def test_replace_active_refused_when_open_position_exists_without_backfilled_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: position_id is only ever written by get_controlled_proof_
    view's own backfill. A real, filled BUY that left an open position (the
    exact production scenario: automatic reconciliation records the fill,
    but nothing ever polled this proof's status afterward) must still be
    detected as live capital, not treated as safe to replace."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-orphan-position", expires_in_minutes=30, actor="operator:alice",
        )
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        session.add(LiveAccountingRecord(
            idempotency_key="fill-orphan-1", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="kraken-orphan-1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.005"), fee_currency="USD",
            net_cash_impact=Decimal("-5.005"), provenance={}, recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()
        assert old.position_id is None  # never backfilled -- the exact production gap

        # Exercised directly rather than through create_controlled_proof:
        # a genuinely open position for this product ALSO trips that
        # function's own, separate, pre-existing "open production position"
        # guard first (same protective outcome via a different, already-
        # tested path) -- this isolates _live_capital_blocker's own new
        # live-derivation fallback specifically.
        blocker = await controlled_proof_service._live_capital_blocker(db=session, proof=old)
        assert blocker == "open_position_exists"


@pytest.mark.asyncio
async def test_create_rejects_open_position_uncategorized_by_campaign_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the production incident: a real BUY fill's accounting
    record can be uncategorized (capital_campaign_id=None, see
    _resolve_campaign_for_live_order's "uncategorized" outcome) or
    attributed to a campaign row other than whichever one is currently
    governing. A campaign_id-scoped position check would silently miss it.
    The initial "no open position" pre-check must still catch it."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        session.add(LiveAccountingRecord(
            idempotency_key="fill-precheck-uncategorized", live_trading_profile_id=profile.id, capital_campaign_id=None,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="o-precheck-1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("50"), fee_amount=Decimal("0.05"), fee_currency="USD",
            net_cash_impact=Decimal("-50.05"), provenance={}, recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()
        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-precheck-uncategorized", expires_in_minutes=30,
                actor="operator:alice",
            )


@pytest.mark.asyncio
async def test_replace_active_refused_when_owned_position_exists_uncategorized_by_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the production incident: RUN_CONTROLLED_PROOF with
    replace_active=true reported replacement_performed=true and a new proof
    proceeded all the way to autonomous_execution_claimed, then failed
    pre-provider with autonomous_execution_failed_pre_provider /
    owned_position_exists. prepare_autonomous_claimed_buy's
    owned_position_exists check (autonomous_order_preparation.py) is scoped
    only to live_trading_profile_id + exact symbol -- it never filters by
    capital_campaign_id, because a real fill's accounting record can be
    uncategorized (capital_campaign_id=None, see
    _resolve_campaign_for_live_order's "uncategorized" outcome) or
    attributed to a different campaign row than whichever one is currently
    governing. Before this fix, the replacement gate filtered by
    capital_campaign_id == runtime.id and so silently missed exactly this
    position, concluding "safe to replace" while execution independently,
    correctly, concluded "owned position exists" for the same real funds.
    Replacement must be rejected before any new proof is created."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-uncategorized", expires_in_minutes=30, actor="operator:alice",
        )
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        # The exact production gap: a real, filled BUY whose accounting
        # record is not attributed to the currently governing campaign row
        # (here: capital_campaign_id=None), and whose proof view was never
        # polled afterward, so old.position_id was never backfilled either.
        session.add(LiveAccountingRecord(
            idempotency_key="fill-uncategorized-1", live_trading_profile_id=profile.id, capital_campaign_id=None,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="kraken-uncat-1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.005"), fee_currency="USD",
            net_cash_impact=Decimal("-5.005"), provenance={}, recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()
        assert old.position_id is None  # never backfilled -- matches the production gap

        # Isolates _live_capital_blocker's own fix: even asked about this
        # specific active proof directly, it must independently recognize
        # the uncategorized fill as live capital.
        blocker = await controlled_proof_service._live_capital_blocker(db=session, proof=old)
        assert blocker == "open_position_exists"

        # The full replace_active call must also refuse -- via
        # create_controlled_proof's own pre-check firing first now that it
        # shares the same profile+symbol scope, which is an even earlier
        # and equally correct rejection point than _live_capital_blocker.
        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-uncategorized-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert "open production position already exists" in str(excinfo.value)
        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status not in ("CANCELLED",)
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_refused_when_owned_position_belongs_to_different_campaign_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same production gap as the "uncategorized" (capital_campaign_id=None)
    case above, but with the accounting record attributed to a real,
    different CapitalCampaign.id -- e.g. a prior or unrelated campaign row
    -- rather than left NULL. The live_trading_profile_id + exact-symbol
    scope must still catch it regardless of which campaign row (if any) the
    record happens to be tagged with -- a real owned position belongs to the
    live account, not to whichever internal campaign row created it."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-foreign-campaign", expires_in_minutes=30, actor="operator:alice",
        )
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))

        decoy_campaign = CapitalCampaign(
            uuid=uuid.uuid4(), owner="operator:decoy", name="decoy", status="ARCHIVED",
            campaign_type="definition_pinned_runtime", definition_campaign_id=None, definition_version=None,
            paper_account_id=uuid.uuid4(), starting_capital=Decimal("25"), current_equity=Decimal("25"),
        )
        session.add(decoy_campaign)
        await session.flush()
        assert decoy_campaign.id != runtime.id

        # Same live_trading_profile_id and exact BTC-USD symbol as the
        # governing campaign's real profile, but capital_campaign_id points
        # at an entirely different (decoy) campaign row.
        session.add(LiveAccountingRecord(
            idempotency_key="fill-foreign-campaign-1", live_trading_profile_id=profile.id, capital_campaign_id=decoy_campaign.id,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="kraken-foreign-1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.005"), fee_currency="USD",
            net_cash_impact=Decimal("-5.005"), provenance={}, recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()
        assert old.position_id is None  # never backfilled -- matches the production gap

        blocker = await controlled_proof_service._live_capital_blocker(db=session, proof=old)
        assert blocker == "open_position_exists"

        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-foreign-campaign-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status not in ("CANCELLED",)
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_refused_when_live_trading_profile_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed regression: if the live trading profile for the
    campaign's paper account cannot be resolved at all,
    _live_capital_blocker must not treat that as "safe to replace" -- an
    inability to prove no live capital exists is not proof that none
    exists. Must return "ownership_scope_unresolved" and refuse
    replacement, not silently fall through to None (the prior behavior)."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-no-profile", expires_in_minutes=30, actor="operator:alice",
        )
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        await session.delete(profile)
        await session.flush()

        blocker = await controlled_proof_service._live_capital_blocker(db=session, proof=old)
        assert blocker == "ownership_scope_unresolved"

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-no-profile-new", expires_in_minutes=30,
                actor="operator:bob", replace_active=True,
            )
        assert excinfo.value.details.get("blocker") == "ownership_scope_unresolved"
        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status not in ("CANCELLED",)
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_allowed_when_matching_buy_and_sell_fee_pairs_net_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the fee_attribution double-counting defect:
    record_live_fill_reconciliation always writes a fee_attribution row
    alongside every fill_accounting row, with the SAME filled_quantity and
    side. _owned_position_exists (now the shared
    app.services.live.position_quantity.owned_position_exists) must scope to
    quantity-bearing record types only -- a BUY fully offset by a SELL of
    the exact same size, each with its real paired fee row, must net to
    exactly zero and no longer block replacement."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-fee-pairs", expires_in_minutes=30, actor="operator:alice",
        )
        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))

        def _fill_and_fee(*, side: str, provider_order_id: str, recorded_at):
            common = dict(
                live_trading_profile_id=profile.id, live_crypto_order_id=None, capital_campaign_id=None,
                reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
                source_execution_event_type="execution_intent_created", provider_order_id=provider_order_id,
                provider_fill_id=f"{provider_order_id}-fill", symbol="BTC-USD", side=side,
                filled_quantity=Decimal("0.00007817"), fill_price=Decimal("64900.00"),
                gross_notional=Decimal("5.08"), fee_amount=Decimal("0.05"), fee_currency="USD",
                provenance={}, recorded_at=recorded_at,
            )
            session.add(LiveAccountingRecord(idempotency_key=f"{provider_order_id}:fill", record_type="fill_accounting", net_cash_impact=Decimal("-5.08"), **common))
            session.add(LiveAccountingRecord(idempotency_key=f"{provider_order_id}:fee", record_type="fee_attribution", net_cash_impact=Decimal("0.05"), **common))

        now = datetime.now(timezone.utc)
        _fill_and_fee(side="buy", provider_order_id="BUY-1", recorded_at=now - timedelta(days=9))
        _fill_and_fee(side="sell", provider_order_id="SELL-1", recorded_at=now)
        await session.flush()

        blocker = await controlled_proof_service._live_capital_blocker(db=session, proof=old)
        assert blocker is None

        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-fee-pairs-new", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )
        assert replaced is not None and replaced.proof_id == old.proof_id
        assert new.proof_id != old.proof_id


@pytest.mark.asyncio
async def test_replace_active_allowed_when_a_terminal_order_leaves_no_open_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CANCELLED/REJECTED/EXPIRED order (submitted, but never resulted in
    a fill or open position) must not falsely block replacement -- only a
    genuinely unresolved order or a real nonzero position should."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-terminal-order", expires_in_minutes=30, actor="operator:alice",
        )
        session.add(LiveCryptoOrder(
            live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider=old.provider, environment=old.environment, product_id=old.product_id, side="BUY",
            order_type="market", requested_quote_size=Decimal("5"), client_order_id=str(uuid.uuid4()),
            status="CANCELLED", submitted_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        ))
        await session.flush()

        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-terminal-order-new", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )
        assert replaced is not None and replaced.proof_id == old.proof_id
        assert new.proof_id != old.proof_id


@pytest.mark.asyncio
async def test_replace_active_false_retains_unchanged_fail_closed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default (replace_active=False) path must be byte-for-byte
    unchanged: same message, same details shape, old proof left untouched --
    proving the new feature is strictly additive, opt-in behavior."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-default", expires_in_minutes=30, actor="operator:alice",
        )
        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="proof-default-2", expires_in_minutes=30,
                actor="operator:bob",
            )
        assert excinfo.value.message == "Another controlled proof is already active"
        assert excinfo.value.details == {"active_proof_id": str(old.proof_id)}

        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status == old.status
        assert refreshed_old.cancelled_at is None
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_replace_active_idempotent_replay_returns_same_replacement_without_double_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-idem-old", expires_in_minutes=30, actor="operator:alice",
        )
        first, first_replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-idem-new", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )
        second, second_replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-idem-new", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )

        assert second.proof_id == first.proof_id
        assert first_replaced is not None and first_replaced.proof_id == old.proof_id
        # Replay never re-derives a replacement: create_controlled_proof's
        # idempotent-replay branch always returns (existing, None).
        assert second_replaced is None

        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 2
        cancelled = [r for r in rows if r.status == "CANCELLED"]
        assert [r.proof_id for r in cancelled] == [old.proof_id]
        cancel_audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == old.proof_id, AuditLog.action == "controlled_proof_run.cancelled")
        )).scalars().all()
        assert len(cancel_audits) == 1


@pytest.mark.asyncio
async def test_sequential_replace_active_requests_never_leave_more_than_one_active_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy for the two-simultaneous-requests race requirement: the real
    race-safety mechanism (SELECT ... FOR UPDATE row lock plus Postgres's
    uq_controlled_proof_runs_single_active partial unique index) is not
    exercisable under sqlite's single shared connection -- see this file's
    other documented SQLite-limitation notes -- so this instead proves the
    invariant those mechanisms exist to protect: no matter how many
    replace_active requests land, at most one active proof ever exists at
    any observable point."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof_a, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="race-a", expires_in_minutes=30, actor="operator:alice",
        )
        proof_b, replaced_1 = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="race-b", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )
        proof_c, replaced_2 = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="race-c", expires_in_minutes=30,
            actor="operator:carol", replace_active=True,
        )
        assert replaced_1.proof_id == proof_a.proof_id
        assert replaced_2.proof_id == proof_b.proof_id

        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 3
        active = [r for r in rows if r.status in controlled_proof_service._ACTIVE_STATES]
        assert [r.proof_id for r in active] == [proof_c.proof_id]


@pytest.mark.asyncio
async def test_expired_proofs_remain_automatically_reaped_alongside_replace_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 9: replace_active supplements expiration reaping, it does
    not replace it -- an expired active proof is reaped (and thus never
    needs replacing at all) whether or not replace_active is set."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        stale, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-reap", expires_in_minutes=30, actor="operator:alice",
        )
        stale.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-reap-new", expires_in_minutes=30,
            actor="operator:bob", replace_active=True,
        )
        # Already reaped to EXPIRED before the active-proof check ever ran --
        # so there was nothing left to replace.
        assert replaced is None
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "EXPIRED"
        assert new.status == "REQUESTED"


# --- cancellation --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_requested_proof_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-cancel", expires_in_minutes=30, actor="operator:alice",
        )
        cancelled = await controlled_proof_service.cancel_controlled_proof(
            db=session, proof_id=proof.proof_id, actor="operator:alice", reason="testing",
        )
        assert cancelled.status == "CANCELLED"
        assert cancelled.cancelled_by == "operator:alice"
        audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == proof.proof_id, AuditLog.action == "controlled_proof_run.cancelled")
        )).scalars().all()
        assert len(audits) == 1


@pytest.mark.asyncio
async def test_cancel_after_entry_proposed_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-cancel2", expires_in_minutes=30, actor="operator:alice",
        )
        proof = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
        )
        await controlled_proof_service.link_controlled_proof_entry(
            db=session, proof=proof, decision_record_id=uuid.uuid4(),
            mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
        )
        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.cancel_controlled_proof(
                db=session, proof_id=proof.proof_id, actor="operator:alice", reason="too late",
            )


@pytest.mark.asyncio
async def test_cancel_unknown_proof_raises_not_found() -> None:
    async with _real_session() as session:
        with pytest.raises(NotFoundError):
            await controlled_proof_service.cancel_controlled_proof(
                db=session, proof_id=uuid.uuid4(), actor="operator:alice", reason=None,
            )


# --- worker claim: exactly-once, restart-safe, idempotent linkage -------------------

@pytest.mark.asyncio
async def test_claim_transitions_requested_to_claimed_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-claim", expires_in_minutes=30, actor="operator:alice",
        )
        cycle_id = uuid.uuid4()
        claimed = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=cycle_id,
        )
        assert claimed is not None
        assert claimed.proof_id == proof.proof_id
        assert claimed.status == "CLAIMED"
        assert claimed.claimed_by_cycle_id == cycle_id

        claim_audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == proof.proof_id, AuditLog.action == "controlled_proof_run.claimed")
        )).scalars().all()
        assert len(claim_audits) == 1


@pytest.mark.asyncio
async def test_repeated_claim_calls_simulating_worker_restart_do_not_reclaim_or_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker restart re-runs the same per-cycle claim lookup. It must
    keep finding the SAME already-CLAIMED proof, never create a second one,
    and never emit a second 'claimed' audit entry."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-restart", expires_in_minutes=30, actor="operator:alice",
        )
        first = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
        )
        second = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
        )
        assert first.proof_id == second.proof_id == proof.proof_id
        assert second.claimed_by_cycle_id == first.claimed_by_cycle_id, "second call must not reassign the claim"

        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1
        claim_audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == proof.proof_id, AuditLog.action == "controlled_proof_run.claimed")
        )).scalars().all()
        assert len(claim_audits) == 1


@pytest.mark.asyncio
async def test_link_entry_and_package_are_idempotent_no_duplicate_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-link", expires_in_minutes=30, actor="operator:alice",
        )
        proof = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
        )
        decision_id = uuid.uuid4()
        package_id = uuid.uuid4()
        await controlled_proof_service.link_controlled_proof_entry(
            db=session, proof=proof, decision_record_id=decision_id,
            mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
        )
        await controlled_proof_service.link_controlled_proof_package(db=session, proof=proof, package_id=package_id)
        assert proof.status == "PACKAGE_CREATED"

        # Simulate a replayed cycle attempting to link a DIFFERENT decision/package.
        other_decision = uuid.uuid4()
        other_package = uuid.uuid4()
        await controlled_proof_service.link_controlled_proof_entry(
            db=session, proof=proof, decision_record_id=other_decision,
            mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
        )
        await controlled_proof_service.link_controlled_proof_package(db=session, proof=proof, package_id=other_package)

        assert proof.decision_record_id == decision_id, "must not overwrite the one controlled entry"
        assert proof.package_id == package_id, "must not overwrite the linked package"

        entry_audits = (await session.execute(
            select(AuditLog).where(AuditLog.entity_id == proof.proof_id, AuditLog.action == "controlled_proof_run.entry_linked")
        )).scalars().all()
        assert len(entry_audits) == 1


@pytest.mark.asyncio
async def test_expired_proof_is_not_claimable_and_transitions_to_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-expire", expires_in_minutes=30, actor="operator:alice",
        )
        proof.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.flush()

        claimed = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
        )
        assert claimed is None

        refreshed = await session.get(ControlledProofRun, proof.proof_id)
        assert refreshed.status == "EXPIRED"


# --- status view: downstream linkage, reconciliation, net P&L derivation ------------


def test_authoritative_fee_total_counts_paired_fill_and_fee_rows_once() -> None:
    buy_order_id, sell_order_id = uuid.uuid4(), uuid.uuid4()
    rows = [
        SimpleNamespace(live_crypto_order_id=buy_order_id, provider_fill_id="buy-fill", reconciliation_event_id=uuid.uuid4(), record_type="fill_accounting", fee_amount=Decimal("0.04")),
        SimpleNamespace(live_crypto_order_id=buy_order_id, provider_fill_id="buy-fill", reconciliation_event_id=uuid.uuid4(), record_type="fee_attribution", fee_amount=Decimal("0.04")),
        SimpleNamespace(live_crypto_order_id=sell_order_id, provider_fill_id="sell-fill", reconciliation_event_id=uuid.uuid4(), record_type="fill_accounting", fee_amount=Decimal("0.03997")),
        SimpleNamespace(live_crypto_order_id=sell_order_id, provider_fill_id="sell-fill", reconciliation_event_id=uuid.uuid4(), record_type="fee_attribution", fee_amount=Decimal("0.03997")),
    ]
    assert controlled_proof_service._authoritative_fee_total(rows) == Decimal("0.07997")

@pytest.mark.asyncio
async def test_status_view_never_borrows_cached_or_campaign_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proof carrying the exact shape of the production contamination
    incident must regress to its last provable state, never present cached
    order/position/P&L fields whose package -> claim lineage is absent."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-no-foreign-lineage",
            expires_in_minutes=30, actor="operator:alice",
        )
        proof.status = "RECONCILED"
        proof.decision_record_id = uuid.uuid4()
        proof.package_id = uuid.uuid4()
        proof.buy_live_crypto_order_id = uuid.uuid4()
        proof.sell_live_crypto_order_id = uuid.uuid4()
        proof.position_id = "foreign-position"
        proof.net_pnl_usd = Decimal("-0.055")
        proof.terminal_verdict = "LIFECYCLE_PROVEN_LOSS"
        await session.flush()

        view = await controlled_proof_service.get_controlled_proof_view(
            db=session, proof_id=proof.proof_id,
        )

        assert view["buy_order"] is None
        assert view["sell_order"] is None
        assert view["position"] is None
        assert view["reconciliation"] is None
        assert view["fees_usd"] is None
        assert view["net_pnl_usd"] is None
        assert view["terminal_verdict"] is None
        assert view["status"] == "ENTRY_PROPOSED"


@pytest.mark.asyncio
async def test_status_view_derives_reconciled_without_fabricating_profit(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-pnl", expires_in_minutes=30, actor="operator:alice",
        )
        proof = await controlled_proof_service.claim_next_controlled_proof_for_scope(
            db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
            environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
        )
        decision_id = uuid.uuid4()
        session.add(DecisionRecord(
            decision_id=decision_id, idempotency_key=f"dr-{decision_id}", source_lineage={}, field_provenance={},
            version="1", timestamp=datetime.now(timezone.utc), asset={"symbol": "BTC-USD"}, timeframe="15m",
            market_regime={}, indicators={}, generated_signals=[], supporting_strategies=[], opposing_strategies=[],
            risk_adjustments=[], trade_accepted=True,
        ))
        await session.flush()
        await controlled_proof_service.link_controlled_proof_entry(
            db=session, proof=proof, decision_record_id=decision_id,
            mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
        )
        package_id = uuid.uuid4()
        await controlled_proof_service.link_controlled_proof_package(db=session, proof=proof, package_id=package_id)

        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        buy_order_id = uuid.uuid4()
        sell_order_id = uuid.uuid4()
        session.add(LiveCryptoOrder(
            live_crypto_order_id=buy_order_id, crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY", order_type="MARKET",
            requested_quote_size=Decimal("5"), client_order_id="c-buy", status="FILLED",
            decision_record_id=decision_id, provider_order_id="p-buy",
            filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        ))
        session.add(LiveCryptoOrder(
            live_crypto_order_id=sell_order_id, crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider="kraken_spot", environment="production", product_id="BTC-USD", side="SELL", order_type="MARKET",
            requested_quote_size=Decimal("5"), client_order_id="c-sell", status="FILLED",
            provider_order_id="p-sell", filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        ))
        now = datetime.now(timezone.utc)
        session.add(LiveAccountingRecord(
            idempotency_key="fill-buy", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
            live_crypto_order_id=buy_order_id, reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="p-buy",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.02"), fee_currency="USD",
            net_cash_impact=Decimal("-5.02"), provenance={}, recorded_at=now,
        ))
        session.add(LiveAccountingRecord(
            idempotency_key="fill-sell", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
            live_crypto_order_id=sell_order_id, reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="p-sell",
            symbol="BTC-USD", side="sell", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50010"),
            gross_notional=Decimal("5.001"), fee_amount=Decimal("0.02"), fee_currency="USD",
            net_cash_impact=Decimal("4.961"), provenance={}, recorded_at=now + timedelta(minutes=1),
        ))
        await session.flush()

        proof.sell_package_id = uuid.uuid4()
        async def _exact_lineage(*, side, **_kwargs):
            order = await _kwargs["db"].get(LiveCryptoOrder, buy_order_id if side == "BUY" else sell_order_id)
            package = SimpleNamespace(package_id=proof.package_id if side == "BUY" else proof.sell_package_id, package_state="COMPLETED")
            return package, SimpleNamespace(live_order_id=order.live_crypto_order_id), order
        async def _resolved(**_kwargs):
            return SimpleNamespace(reconciliation_status="filled")
        monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _exact_lineage)
        monkeypatch.setattr(controlled_proof_service, "_latest_order_reconciliation", _resolved)

        view = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=proof.proof_id)

        assert view["decision"]["decision_record_id"] == str(decision_id)
        assert view["package"] is None or view["package"]["package_id"] == str(package_id)
        assert view["buy_order"]["live_crypto_order_id"] == str(buy_order_id)
        assert view["sell_order"]["live_crypto_order_id"] == str(sell_order_id)
        # Net cash impact here is -5.02 + 4.961 = -0.059: a real, small net
        # LOSS after fees on both legs. The view must never report
        # PROFIT_CONFIRMED for a non-positive net P&L.
        assert view["net_pnl_usd"] == Decimal("-0.059")
        assert view["status"] in {"RECONCILED", "EXITED"}
        assert view["status"] != "PROFIT_CONFIRMED"
        # Requirement 9: actual fills and fees determine the terminal
        # verdict -- a real net loss must be reported as
        # LIFECYCLE_PROVEN_LOSS, never LIFECYCLE_PROVEN_PROFIT.
        assert view["terminal_verdict"] == "LIFECYCLE_PROVEN_LOSS"


async def _seed_reconciled_round_trip(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, *, sell_net_cash_impact: Decimal, idempotency_prefix: str,
) -> uuid.UUID:
    """Shared setup for terminal-verdict tests: a claimed proof with a real,
    linked BUY entry/package and a real, filled BUY+SELL round trip whose net
    P&L is controlled entirely by sell_net_cash_impact."""
    await _seed_fully_ready_scope(session, monkeypatch)
    proof, _ = await controlled_proof_service.create_controlled_proof(
        db=session, product_id="BTC-USD", idempotency_key=f"{idempotency_prefix}-req", expires_in_minutes=30, actor="operator:alice",
    )
    proof = await controlled_proof_service.claim_next_controlled_proof_for_scope(
        db=session, campaign_id=_CAMPAIGN_ID, campaign_version=1, provider="kraken_spot",
        environment="production", product_id="BTC-USD", cycle_id=uuid.uuid4(),
    )
    decision_id = uuid.uuid4()
    session.add(DecisionRecord(
        decision_id=decision_id, idempotency_key=f"dr-{decision_id}", source_lineage={}, field_provenance={},
        version="1", timestamp=datetime.now(timezone.utc), asset={"symbol": "BTC-USD"}, timeframe="15m",
        market_regime={}, indicators={}, generated_signals=[], supporting_strategies=[], opposing_strategies=[],
        risk_adjustments=[], trade_accepted=True,
    ))
    await session.flush()
    await controlled_proof_service.link_controlled_proof_entry(
        db=session, proof=proof, decision_record_id=decision_id,
        mandate_id=None, mandate_version_id=None, mandate_evaluation_id=None,
    )
    package_id = uuid.uuid4()
    await controlled_proof_service.link_controlled_proof_package(db=session, proof=proof, package_id=package_id)

    runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
    profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
    buy_order_id = uuid.uuid4()
    sell_order_id = uuid.uuid4()
    session.add(LiveCryptoOrder(
        live_crypto_order_id=buy_order_id, crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="BUY", order_type="MARKET",
        requested_quote_size=Decimal("5"), client_order_id=f"{idempotency_prefix}-buy", status="FILLED",
        decision_record_id=decision_id, provider_order_id=f"{idempotency_prefix}-p-buy",
        filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
    ))
    session.add(LiveCryptoOrder(
        live_crypto_order_id=sell_order_id, crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="SELL", order_type="MARKET",
        requested_quote_size=Decimal("5"), client_order_id=f"{idempotency_prefix}-sell", status="FILLED",
        provider_order_id=f"{idempotency_prefix}-p-sell",
        filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
    ))
    now = datetime.now(timezone.utc)
    session.add(LiveAccountingRecord(
        idempotency_key=f"{idempotency_prefix}-fill-buy", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
        live_crypto_order_id=buy_order_id, reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id=f"{idempotency_prefix}-p-buy",
        symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
        gross_notional=Decimal("5"), fee_amount=Decimal("0.02"), fee_currency="USD",
        net_cash_impact=Decimal("-5.02"), provenance={}, recorded_at=now,
    ))
    session.add(LiveAccountingRecord(
        idempotency_key=f"{idempotency_prefix}-fill-sell", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
        live_crypto_order_id=sell_order_id, reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id=f"{idempotency_prefix}-p-sell",
        symbol="BTC-USD", side="sell", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50010"),
        gross_notional=Decimal("5.001"), fee_amount=Decimal("0.02"), fee_currency="USD",
        net_cash_impact=sell_net_cash_impact, provenance={}, recorded_at=now + timedelta(minutes=1),
    ))
    await session.flush()
    proof.sell_package_id = uuid.uuid4()
    async def _exact_lineage(*, side, **_kwargs):
        order = await _kwargs["db"].get(LiveCryptoOrder, buy_order_id if side == "BUY" else sell_order_id)
        package = SimpleNamespace(package_id=proof.package_id if side == "BUY" else proof.sell_package_id, package_state="COMPLETED")
        return package, SimpleNamespace(live_order_id=order.live_crypto_order_id), order
    async def _resolved(**_kwargs):
        return SimpleNamespace(reconciliation_status="filled")
    monkeypatch.setattr(controlled_proof_service, "_proof_leg_lineage", _exact_lineage)
    monkeypatch.setattr(controlled_proof_service, "_latest_order_reconciliation", _resolved)
    return proof.proof_id


@pytest.mark.asyncio
async def test_status_view_computes_profit_verdict_from_real_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 9 (profit branch): a real net-positive P&L across the
    actual BUY and SELL fills is reported as LIFECYCLE_PROVEN_PROFIT."""
    async with _real_session() as session:
        proof_id = await _seed_reconciled_round_trip(
            session, monkeypatch, sell_net_cash_impact=Decimal("5.50"), idempotency_prefix="proof-profit",
        )
        view = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=proof_id)
        # -5.02 + 5.50 = 0.48: a real net PROFIT after fees on both legs.
        assert view["net_pnl_usd"] == Decimal("0.48")
        assert view["terminal_verdict"] == "LIFECYCLE_PROVEN_PROFIT"


@pytest.mark.asyncio
async def test_status_view_computes_flat_verdict_from_real_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 9 (flat branch): an exact real net-zero P&L is reported
    as LIFECYCLE_PROVEN_FLAT -- never PROFIT, never LOSS."""
    async with _real_session() as session:
        proof_id = await _seed_reconciled_round_trip(
            session, monkeypatch, sell_net_cash_impact=Decimal("5.02"), idempotency_prefix="proof-flat",
        )
        view = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=proof_id)
        assert view["net_pnl_usd"] == Decimal("0")
        assert view["terminal_verdict"] == "LIFECYCLE_PROVEN_FLAT"


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved_side", ["BUY", "SELL"])
async def test_post_sell_finalization_waits_for_both_latest_reconciliations_then_persists(
    monkeypatch: pytest.MonkeyPatch,
    unresolved_side: str,
) -> None:
    async with real_sqlite_session_factory(_ALL_TABLES) as session_factory:
        async with session_factory() as session:
            proof_id = await _seed_reconciled_round_trip(
                session, monkeypatch, sell_net_cash_impact=Decimal("4.98"),
                idempotency_prefix="proof-post-sell",
            )

            async def _unresolved(**kwargs):
                status = "reconciliation_required" if kwargs["order"].side == unresolved_side else "filled"
                return SimpleNamespace(reconciliation_status=status)

            monkeypatch.setattr(controlled_proof_service, "_latest_order_reconciliation", _unresolved)
            incomplete = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=proof_id)
            assert incomplete["status"] == "EXITED"
            assert incomplete["reconciliation"]["unresolved"] is True
            assert incomplete["net_pnl_usd"] is None
            assert incomplete["terminal_verdict"] is None

        async with session_factory() as finalization_session:
            async def _resolved(**_kwargs):
                return SimpleNamespace(reconciliation_status="filled")

            monkeypatch.setattr(controlled_proof_service, "_latest_order_reconciliation", _resolved)
            completed = await controlled_proof_service.get_controlled_proof_view(
                db=finalization_session, proof_id=proof_id,
            )
            assert completed["status"] == "RECONCILED"
            assert completed["reconciliation"]["unresolved"] is False
            assert completed["net_pnl_usd"] == Decimal("-0.04")
            assert completed["terminal_verdict"] == "LIFECYCLE_PROVEN_LOSS"

        async with session_factory() as replay_session:
            replay = await controlled_proof_service.get_controlled_proof_view(
                db=replay_session, proof_id=proof_id,
            )
            assert replay["net_pnl_usd"] == Decimal("-0.04")
            assert replay["terminal_verdict"] == "LIFECYCLE_PROVEN_LOSS"


@pytest.mark.asyncio
async def test_status_view_reports_blocked_verdict_when_proof_expires_before_any_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proof that expires while real gates (mandate/risk/evidence) never
    let a controlled BUY get proposed is a genuine BLOCKED outcome, not a
    silent no-op -- distinct from a lifecycle that ran and lost money."""
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        proof, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="proof-blocked", expires_in_minutes=30, actor="operator:alice",
        )
        proof.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.flush()

        view = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=proof.proof_id)

        assert view["status"] == "EXPIRED"
        assert view["terminal_verdict"] == "BLOCKED"


# --- fresh Controlled Proof risk evaluation -------------------------------------------

async def _seed_paper_account(session: AsyncSession, *, paper_account_id: uuid.UUID) -> None:
    session.add(PaperAccount(
        id=paper_account_id, owner_user_id=uuid.uuid4(), name="test", asset_class="crypto",
        starting_balance=Decimal("25"), current_cash_balance=Decimal("25"), is_active=True,
    ))
    await session.flush()


def _stub_risk_context():
    from app.services.risk.risk_context import ExecutionRiskContext

    now = datetime.now(timezone.utc)
    return ExecutionRiskContext(
        account_equity=Decimal("25"), start_of_day_equity=Decimal("25"), current_equity=Decimal("25"),
        max_position_size_pct=Decimal("1"), max_daily_loss_pct=Decimal("0.5"), high_water_mark_equity=Decimal("25"),
        max_drawdown_pct=Decimal("0.5"), consecutive_losses_on_pair=0, cooldown_after_losses=3,
        last_loss_at=None, cooldown_duration_minutes=Decimal("0"), evaluation_time=now,
        data_is_stale=False, data_has_gaps=False, global_kill_switch_engaged_state=False,
        global_kill_switch_rearm_required=False, account_kill_switch_engaged_state=False,
        account_kill_switch_rearm_required=False, global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True, risk_policy_source="test", runtime_cooldown_state="inactive",
        runtime_no_trade_zone_state="inactive", start_of_day_equity_source="test", high_water_mark_equity_source="test",
    )


@pytest.mark.asyncio
async def test_evaluate_controlled_proof_risk_allows_and_persists_real_risk_event() -> None:
    """A genuine ALLOW verdict from the real Risk Engine triad proceeds and
    persists a real RiskEvent through the existing canonical persistence
    path -- not a fabricated approval."""

    async with real_sqlite_session([*_ALL_TABLES, PaperAccount.__table__, RiskEvent.__table__]) as session:
        paper_account_id = uuid.uuid4()
        await _seed_paper_account(session, paper_account_id=paper_account_id)
        await _seed_asset_with_fresh_candles(session, symbol="BTC")

        real_context = _stub_risk_context()

        async def _resolve(*, db, paper_account, asset):
            return real_context

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(controlled_proof_service, "resolve_execution_risk_context", _resolve)

            proof_id = uuid.uuid4()
            outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
                db=session, proof_id=proof_id, campaign_id=_CAMPAIGN_ID, campaign_version=1,
                paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
                notional_usd=Decimal("5"), actor="system:test",
            )

        assert outcome.verdict == "ALLOW"
        assert outcome.risk_event_id is not None
        persisted = await session.get(RiskEvent, outcome.risk_event_id)
        assert persisted is not None
        assert persisted.action_taken == "approved"
        evidence = persisted.detail["evidence_context"]
        assert evidence["purpose"] == "controlled_proof"
        assert evidence["proof_id"] == str(proof_id)
        assert evidence["product_id"] == "BTC-USD"
        assert evidence["side"] == "BUY"
        assert evidence["requested_notional_usd"] == "5"
        assert Decimal(evidence["reference_price"]) == Decimal("100")
        assert evidence["data_quality"]["data_is_stale"] is False
        assert evidence["data_quality"]["data_has_gaps"] is False
        assert evidence["risk_policy"]["source"] == "test"


@pytest.mark.asyncio
async def test_evaluate_controlled_proof_risk_denies_never_fabricates_allow() -> None:
    """A genuine REJECT verdict blocks with the real reason -- it must never
    be silently treated as ALLOW."""
    from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationResult

    async with real_sqlite_session([*_ALL_TABLES, PaperAccount.__table__, RiskEvent.__table__]) as session:
        paper_account_id = uuid.uuid4()
        await _seed_paper_account(session, paper_account_id=paper_account_id)
        await _seed_asset_with_fresh_candles(session, symbol="BTC")

        real_context = _stub_risk_context()

        async def _resolve(*, db, paper_account, asset):
            return real_context

        def _denied(*, request, reference_price=None, context=None):
            return RiskEvaluationResult(
                action=RiskDecisionAction.REJECT, reason_code="max_drawdown_breached",
                approved_quantity=Decimal("0"), steps=[],
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(controlled_proof_service, "resolve_execution_risk_context", _resolve)
            mp.setattr(controlled_proof_service, "evaluate_signal_risk", _denied)

            outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
                db=session, proof_id=uuid.uuid4(), campaign_id=_CAMPAIGN_ID, campaign_version=1,
                paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
                notional_usd=Decimal("5"), actor="system:test",
            )

        assert outcome.verdict == "DENY"
        assert outcome.approved_notional_usd is None
        assert outcome.reason_code == "max_drawdown_breached"


@pytest.mark.asyncio
async def test_evaluate_controlled_proof_risk_resize_never_reports_full_notional() -> None:
    """A genuine RESIZE verdict reports only the risk-approved amount --
    never the full requested $5 -- so callers can never silently proceed
    at the full size."""
    from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationResult

    async with real_sqlite_session([*_ALL_TABLES, PaperAccount.__table__, RiskEvent.__table__]) as session:
        paper_account_id = uuid.uuid4()
        await _seed_paper_account(session, paper_account_id=paper_account_id)
        await _seed_asset_with_fresh_candles(session, symbol="BTC")

        real_context = _stub_risk_context()

        async def _resolve(*, db, paper_account, asset):
            return real_context

        def _resized(*, request, reference_price=None, context=None):
            return RiskEvaluationResult(
                action=RiskDecisionAction.RESIZE, reason_code="position_resized_by_risk_engine",
                approved_quantity=Decimal("0.01"), steps=[],
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(controlled_proof_service, "resolve_execution_risk_context", _resolve)
            mp.setattr(controlled_proof_service, "evaluate_signal_risk", _resized)

            outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
                db=session, proof_id=uuid.uuid4(), campaign_id=_CAMPAIGN_ID, campaign_version=1,
                paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
                notional_usd=Decimal("5"), actor="system:test",
            )

        assert outcome.verdict == "RESIZE"
        assert outcome.approved_notional_usd is not None
        assert outcome.approved_notional_usd != Decimal("5")
        assert outcome.approved_notional_usd == Decimal("0.01") * Decimal("100")


@pytest.mark.asyncio
async def test_evaluate_controlled_proof_risk_blank_verdict_fails_closed() -> None:
    """An unrecognized action -- something outside the closed
    APPROVE/RESIZE/REJECT enum this code maps -- must fail closed as
    UNAVAILABLE, never be silently treated as ALLOW. Modeled as a distinct
    str+Enum member (rather than a bare string) because the shared
    persistence path always reads `.value` off the action before this
    code's own verdict mapping ever runs -- so a genuinely value-less
    action can never reach this function's defensive branch in practice;
    what CAN reach it is a real enum-shaped action this elif chain simply
    doesn't recognize, e.g. a future RiskDecisionAction member added
    upstream before this mapping is updated for it."""
    from enum import Enum

    from app.services.risk.risk_engine import RiskEvaluationResult

    class _UnrecognizedAction(str, Enum):
        HOLD_FOR_REVIEW = "hold_for_review"

    async with real_sqlite_session([*_ALL_TABLES, PaperAccount.__table__, RiskEvent.__table__]) as session:
        paper_account_id = uuid.uuid4()
        await _seed_paper_account(session, paper_account_id=paper_account_id)
        await _seed_asset_with_fresh_candles(session, symbol="BTC")

        real_context = _stub_risk_context()

        async def _resolve(*, db, paper_account, asset):
            return real_context

        def _blank(*, request, reference_price=None, context=None):
            return RiskEvaluationResult(
                action=_UnrecognizedAction.HOLD_FOR_REVIEW, reason_code=None, approved_quantity=Decimal("0"), steps=[],
            )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(controlled_proof_service, "resolve_execution_risk_context", _resolve)
            mp.setattr(controlled_proof_service, "evaluate_signal_risk", _blank)

            outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
                db=session, proof_id=uuid.uuid4(), campaign_id=_CAMPAIGN_ID, campaign_version=1,
                paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
                notional_usd=Decimal("5"), actor="system:test",
            )

        assert outcome.verdict == "UNAVAILABLE"
        assert outcome.approved_notional_usd is None


@pytest.mark.asyncio
async def test_evaluate_controlled_proof_risk_fails_closed_when_asset_unregistered() -> None:
    """A missing/unavailable dependency (here: no registered Asset for the
    product) must fail closed as UNAVAILABLE, never silently proceed as if
    risk had approved."""
    async with real_sqlite_session([*_ALL_TABLES, PaperAccount.__table__, RiskEvent.__table__]) as session:
        paper_account_id = uuid.uuid4()
        await _seed_paper_account(session, paper_account_id=paper_account_id)
        # Deliberately no Asset seeded for BTC-USD.

        outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
            db=session, proof_id=uuid.uuid4(), campaign_id=_CAMPAIGN_ID, campaign_version=1,
            paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
            notional_usd=Decimal("5"), actor="system:test",
        )

        assert outcome.verdict == "UNAVAILABLE"
        assert outcome.risk_event_id is None
        assert outcome.approved_notional_usd is None


@pytest.mark.asyncio
async def test_evaluate_controlled_proof_risk_fails_closed_when_risk_context_raises() -> None:
    """An exception raised deep inside risk-context resolution (e.g. a
    downstream dependency outage) must be caught and mapped to a fail-closed
    UNAVAILABLE verdict, never propagate as a fabricated ALLOW."""
    async with real_sqlite_session([*_ALL_TABLES, PaperAccount.__table__, RiskEvent.__table__]) as session:
        paper_account_id = uuid.uuid4()
        await _seed_paper_account(session, paper_account_id=paper_account_id)
        await _seed_asset_with_fresh_candles(session, symbol="BTC")

        async def _raise(*, db, paper_account, asset):
            raise RuntimeError("simulated risk-context dependency outage")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(controlled_proof_service, "resolve_execution_risk_context", _raise)

            outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
                db=session, proof_id=uuid.uuid4(), campaign_id=_CAMPAIGN_ID, campaign_version=1,
                paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
                notional_usd=Decimal("5"), actor="system:test",
            )

        assert outcome.verdict == "UNAVAILABLE"
        assert outcome.risk_event_id is None


# --- governed stale controlled proof recovery ----------------------------------------

async def _seed_stale_active_proof(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, *, idempotency_key: str,
) -> ControlledProofRun:
    """A genuinely active (PACKAGE_CREATED) proof, past its own expires_at,
    with no live-capital or lineage evidence of any kind -- the exact
    production shape (buy_order=null, position=null, sell_order=null,
    terminal_verdict=null) that must now be safe to automatically recover."""
    await _seed_fully_ready_scope(session, monkeypatch)
    proof, _ = await controlled_proof_service.create_controlled_proof(
        db=session, product_id="BTC-USD", idempotency_key=idempotency_key, expires_in_minutes=30, actor="operator:alice",
    )
    proof.status = "PACKAGE_CREATED"
    proof.package_id = uuid.uuid4()
    proof.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()
    return proof


@pytest.mark.asyncio
async def test_expired_safe_proof_is_automatically_recovered_and_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-safe")

        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="stale-safe-new", expires_in_minutes=30, actor="operator:bob",
        )

        # Auto-recovery is expiry, not an operator replacement -- replaced_proof
        # stays reserved for the explicit replace_active=True cancellation path.
        assert replaced is None
        assert new.proof_id != stale.proof_id
        assert new.status == "REQUESTED"

        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "EXPIRED"
        assert refreshed_stale.terminal_verdict == "FAILED"
        assert refreshed_stale.failure_reason == "expired_before_execution_completion"

        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        active = [r for r in rows if r.status in controlled_proof_service._ACTIVE_STATES]
        assert [r.proof_id for r in active] == [new.proof_id]


@pytest.mark.asyncio
async def test_non_expired_active_proof_still_blocks_creation_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        old, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="fresh-active", expires_in_minutes=30, actor="operator:alice",
        )

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="fresh-active-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        assert excinfo.value.message == "Another controlled proof is already active"
        assert excinfo.value.details == {"active_proof_id": str(old.proof_id)}
        refreshed_old = await session.get(ControlledProofRun, old.proof_id)
        assert refreshed_old.status == old.status
        assert refreshed_old.cancelled_at is None
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_expired_proof_with_buy_order_blocks_and_requires_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-buy")
        stale.buy_live_crypto_order_id = uuid.uuid4()
        await session.commit()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-buy-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        assert "exit recovery or reconciliation is required" in excinfo.value.message
        assert excinfo.value.details.get("blocker") == "live_buy_order_exists"
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "PACKAGE_CREATED"
        assert refreshed_stale.terminal_verdict is None
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_expired_proof_with_open_position_blocks_and_requires_exit_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-position")
        stale.position_id = "pos-live-stale-1"
        await session.commit()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-position-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        assert excinfo.value.details.get("blocker") == "open_position_exists"
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "PACKAGE_CREATED"
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_expired_proof_with_unresolved_execution_claim_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-claim")
        session.add(AutonomousExecutionClaim(
            claim_id=uuid.uuid4(), package_id=stale.package_id, activation_id=uuid.uuid4(),
            campaign_id=stale.campaign_id, campaign_version=stale.campaign_version,
            mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), account_id=uuid.uuid4(),
            profile_id=uuid.uuid4(), connection_id=uuid.uuid4(),
            provider=stale.provider, environment=stale.environment, product=stale.product_id,
            side="BUY", claim_status="SUBMISSION_PENDING",
            claimed_at=datetime.now(timezone.utc), claim_owner="system:test",
        ))
        await session.commit()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-claim-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        assert excinfo.value.details.get("blocker") == "unresolved_execution_claim_exists"
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "PACKAGE_CREATED"
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_unrelated_production_execution_claim_for_same_market_does_not_block_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an unresolved execution claim for the identical
    provider/environment/product (e.g. an ordinary autonomous production
    cycle trading the same BTC-USD/kraken_spot/production scope) but a
    DIFFERENT package_id -- never linked to this proof at all -- must not
    falsely block this proof's stale recovery. Only a claim referencing this
    proof's own package_id/sell_package_id counts."""
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-unrelated-claim")
        unrelated_package_id = uuid.uuid4()
        assert unrelated_package_id != stale.package_id
        session.add(AutonomousExecutionClaim(
            claim_id=uuid.uuid4(), package_id=unrelated_package_id, activation_id=uuid.uuid4(),
            campaign_id=stale.campaign_id, campaign_version=stale.campaign_version,
            mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), account_id=uuid.uuid4(),
            profile_id=uuid.uuid4(), connection_id=uuid.uuid4(),
            provider=stale.provider, environment=stale.environment, product=stale.product_id,
            side="BUY", claim_status="SUBMISSION_PENDING",
            claimed_at=datetime.now(timezone.utc), claim_owner="system:test",
        ))
        await session.commit()

        new, replaced = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="stale-unrelated-claim-new", expires_in_minutes=30,
            actor="operator:bob",
        )

        assert replaced is None
        assert new.proof_id != stale.proof_id
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "EXPIRED"
        assert refreshed_stale.terminal_verdict == "FAILED"


@pytest.mark.asyncio
async def test_expired_proof_with_reconciliation_required_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-recon")
        # FILLED is a terminal LiveCryptoOrder status (passes _live_capital_
        # blocker's own order check) -- but its reconciliation event is still
        # in an unresolved state, exactly the gap _stale_recovery_blocker adds
        # on top of _live_capital_blocker.
        order = LiveCryptoOrder(
            live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider=stale.provider, environment=stale.environment, product_id=stale.product_id, side="BUY",
            order_type="market", requested_quote_size=Decimal("5"), client_order_id=str(uuid.uuid4()),
            status="FILLED", submitted_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        )
        session.add(order)
        await session.flush()
        session.add(LiveReconciliationEvent(
            idempotency_key=f"{order.live_crypto_order_id}:recon-1", event_hash="h1",
            live_trading_profile_id=uuid.uuid4(), live_crypto_order_id=order.live_crypto_order_id,
            source_execution_event_id=uuid.uuid4(), source_execution_event_type="execution_intent_created",
            sequence_number=1, event_type="fill_reconciled", reconciliation_status="reconciliation_required",
            provider_name=stale.provider, event_payload={}, provenance={}, immutable_contract_version="1",
            recorded_at=datetime.now(timezone.utc),
        ))
        await session.commit()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-recon-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        assert excinfo.value.details.get("blocker") == "unresolved_reconciliation_exists"
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "PACKAGE_CREATED"
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_expired_proof_with_active_exit_recovery_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-exit-recovery")
        session.add(ControlledProofExitRecovery(
            proof_id=stale.proof_id, status="AUTHORIZED", idempotency_key="stale-exit-recovery-authz",
            authorized_by="operator:alice", authorized_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ))
        await session.commit()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-exit-recovery-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        assert excinfo.value.details.get("blocker") == "exit_recovery_active"
        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "PACKAGE_CREATED"
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_stale_recovery_writes_immutable_audit_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-audit")

        new, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="stale-audit-new", expires_in_minutes=30,
            actor="operator:bob",
        )

        audits = (await session.execute(
            select(AuditLog).where(
                AuditLog.entity_id == stale.proof_id,
                AuditLog.action == "controlled_proof_run.stale_recovery_expired",
            )
        )).scalars().all()
        assert len(audits) == 1
        audit = audits[0]
        assert audit.actor == "operator:bob"
        assert audit.entity_type == "controlled_proof_run"
        assert audit.before_state["status"] == "PACKAGE_CREATED"
        assert audit.after_state["status"] == "EXPIRED"
        assert audit.after_state["terminal_verdict"] == "FAILED"
        assert audit.after_state["failure_reason"] == "expired_before_execution_completion"
        # Replacement request correlation: ties this recovery to exactly the
        # new-proof creation request that triggered it.
        assert audit.after_state["replacement_idempotency_key"] == "stale-audit-new"
        assert new.idempotency_key == "stale-audit-new"


@pytest.mark.asyncio
async def test_old_proof_remains_queryable_as_expired_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 6: never delete proof records."""
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-queryable")

        await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="stale-queryable-new", expires_in_minutes=30,
            actor="operator:bob",
        )

        view = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=stale.proof_id)
        assert view["status"] == "EXPIRED"
        assert view["terminal_verdict"] == "FAILED"
        assert view["failure_reason"] == "expired_before_execution_completion"


@pytest.mark.asyncio
async def test_new_proof_creation_fails_if_recovery_itself_is_unsafe_and_new_proof_never_persists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New proof is created only after safe recovery -- when recovery is
    blocked, no new proof row is ever inserted at all."""
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-unsafe-no-new")
        stale.buy_live_crypto_order_id = uuid.uuid4()
        await session.commit()

        with pytest.raises(InvalidRequestError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-unsafe-no-new-new", expires_in_minutes=30,
                actor="operator:bob",
            )

        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        assert [r.idempotency_key for r in rows] == ["stale-unsafe-no-new"]


@pytest.mark.asyncio
async def test_recovery_commit_survives_a_later_unrelated_failure_in_the_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovered old proof's EXPIRED transition must be durable
    independent of whatever happens next in the same create_controlled_proof
    call -- mirrors the exact regression this call site was already once
    fixed for with the old unconditional _reap_expired()+commit(). Fails
    synchronously, before any further I/O, at the exact point the new
    ControlledProofRun is registered with the session -- simulating an
    unrelated downstream error without disturbing the connection's own
    async/greenlet state (unlike interrupting an in-flight flush)."""
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-durable")

        real_add = session.add

        def _add_and_fail_on_new_proof(obj, *args, **kwargs):
            if isinstance(obj, ControlledProofRun) and obj.status == "REQUESTED":
                raise RuntimeError("simulated unrelated failure after recovery committed")
            return real_add(obj, *args, **kwargs)

        monkeypatch.setattr(session, "add", _add_and_fail_on_new_proof)
        with pytest.raises(RuntimeError):
            await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="stale-durable-new", expires_in_minutes=30,
                actor="operator:bob",
            )
        monkeypatch.undo()

        refreshed_stale = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed_stale.status == "EXPIRED"
        assert refreshed_stale.terminal_verdict == "FAILED"
        assert refreshed_stale.failure_reason == "expired_before_execution_completion"


@pytest.mark.asyncio
async def test_sequential_creation_attempts_never_leave_two_active_proofs_after_stale_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proxy for the two-simultaneous-requests race requirement (real
    concurrent-transaction testing is not exercisable under sqlite's single
    shared connection, per this file's other documented sqlite-limitation
    notes): proves the invariant SELECT ... FOR UPDATE plus
    uq_controlled_proof_runs_single_active exist to protect -- at most one
    active proof ever exists, no matter how many creation attempts land
    against the same stale, safe, expired row."""
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-race")

        first, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="stale-race-a", expires_in_minutes=30, actor="operator:alice",
        )
        second, _ = await controlled_proof_service.create_controlled_proof(
            db=session, product_id="BTC-USD", idempotency_key="stale-race-a", expires_in_minutes=30, actor="operator:bob",
        )

        assert first.proof_id == second.proof_id  # idempotent replay, not a duplicate
        rows = (await session.execute(select(ControlledProofRun))).scalars().all()
        active = [r for r in rows if r.status in controlled_proof_service._ACTIVE_STATES]
        assert [r.proof_id for r in active] == [first.proof_id]
        assert len([r for r in rows if r.status == "EXPIRED"]) == 1


@pytest.mark.asyncio
async def test_recover_stale_controlled_proof_explicit_endpoint_recovers_safe_stale_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The standalone, explicit service operation the operator API endpoint
    calls -- same governed check, invocable without also creating a new
    proof."""
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-explicit")

        outcome = await controlled_proof_service.recover_stale_controlled_proof(db=session, actor="operator:alice")

        assert outcome.recovered is True
        assert outcome.proof_id == stale.proof_id
        refreshed = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed.status == "EXPIRED"
        assert refreshed.terminal_verdict == "FAILED"


@pytest.mark.asyncio
async def test_recover_stale_controlled_proof_explicit_endpoint_fails_closed_when_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        stale = await _seed_stale_active_proof(session, monkeypatch, idempotency_key="stale-explicit-unsafe")
        stale.position_id = "pos-live-explicit-1"
        await session.commit()

        with pytest.raises(InvalidRequestError) as excinfo:
            await controlled_proof_service.recover_stale_controlled_proof(db=session, actor="operator:alice")

        assert "exit recovery or" in excinfo.value.message
        refreshed = await session.get(ControlledProofRun, stale.proof_id)
        assert refreshed.status == "PACKAGE_CREATED"


@pytest.mark.asyncio
async def test_recover_stale_controlled_proof_explicit_endpoint_raises_when_nothing_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _real_session() as session:
        await _seed_fully_ready_scope(session, monkeypatch)
        with pytest.raises(NotFoundError):
            await controlled_proof_service.recover_stale_controlled_proof(db=session, actor="operator:alice")


# --- no direct provider submission ---------------------------------------------------

def test_service_module_never_references_provider_submission() -> None:
    """Static proof, not behavioral: the controlled-proof service must never
    import or call anything that submits an order to a provider. Mirrors the
    existing test_no_execution_side_effect_imports/test_no_provider_order_calls
    pattern already used for capital_campaign_domain."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[4] / "app" / "services" / "controlled_proof"
    source = "\n".join((root / name).read_text() for name in ["service.py", "__init__.py"]).lower()
    for forbidden in ("live_service.submit", "create_order", "submit_order", "kraken_spot.py", "exchange_connections.providers"):
        assert forbidden not in source, f"forbidden reference found: {forbidden}"


def test_routes_module_never_references_provider_submission() -> None:
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[4] / "app" / "api" / "routes" / "controlled_proofs.py"
    source = path.read_text().lower()
    for forbidden in ("live_service.submit", "create_order", "submit_order", "kraken", "provider_client"):
        assert forbidden not in source, f"forbidden reference found: {forbidden}"
