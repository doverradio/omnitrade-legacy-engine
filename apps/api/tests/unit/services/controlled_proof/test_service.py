from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import AsyncIterator

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
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
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
from tests.support.real_sqlite_session import real_sqlite_session

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_ALL_TABLES = [
    Asset.__table__, AuditLog.__table__, AutonomousCapitalMandate.__table__,
    AutonomousCapitalMandateVersion.__table__, AutonomousCapitalMandateAuthorization.__table__,
    AutonomousCapitalMandateEvaluation.__table__, Candle.__table__,
    CanonicalPreviewPackage.__table__,
    CapitalCampaign.__table__, CapitalCampaignDefinition.__table__, ControlledProofRun.__table__,
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
async def test_should_propose_controlled_sell_becomes_true_once_buy_is_filled_and_reconciled(
    monkeypatch: pytest.MonkeyPatch,
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

        # Before any fill exists, the BUY has not been reconciled yet.
        assert await controlled_proof_service.should_propose_controlled_sell(db=session, proof=proof) is False

        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == _CAMPAIGN_ID))
        profile = await session.scalar(select(LiveTradingProfile).where(LiveTradingProfile.paper_account_id == runtime.paper_account_id))
        # Exactly the shape reconcile_live_order_and_fills persists for a
        # genuinely filled BUY -- this test does not re-implement or bypass
        # that logic, it proves the downstream consumer reacts correctly
        # once that authoritative record exists.
        session.add(LiveAccountingRecord(
            idempotency_key="fill-buy-1", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="kraken-order-1",
            symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
            gross_notional=Decimal("5"), fee_amount=Decimal("0.005"), fee_currency="USD",
            net_cash_impact=Decimal("-5.005"), provenance={}, recorded_at=datetime.now(timezone.utc),
        ))
        await session.flush()

        assert await controlled_proof_service.should_propose_controlled_sell(db=session, proof=proof) is True


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
            decision_record_id=decision_id, filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        ))
        session.add(LiveCryptoOrder(
            live_crypto_order_id=sell_order_id, crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
            provider="kraken_spot", environment="production", product_id="BTC-USD", side="SELL", order_type="MARKET",
            requested_quote_size=Decimal("5"), client_order_id="c-sell", status="FILLED",
            filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
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
        decision_record_id=decision_id, filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
    ))
    session.add(LiveCryptoOrder(
        live_crypto_order_id=sell_order_id, crypto_order_preview_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(),
        provider="kraken_spot", environment="production", product_id="BTC-USD", side="SELL", order_type="MARKET",
        requested_quote_size=Decimal("5"), client_order_id=f"{idempotency_prefix}-sell", status="FILLED",
        filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
    ))
    now = datetime.now(timezone.utc)
    session.add(LiveAccountingRecord(
        idempotency_key=f"{idempotency_prefix}-fill-buy", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
        live_crypto_order_id=buy_order_id, reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="p-buy",
        symbol="BTC-USD", side="buy", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50000"),
        gross_notional=Decimal("5"), fee_amount=Decimal("0.02"), fee_currency="USD",
        net_cash_impact=Decimal("-5.02"), provenance={}, recorded_at=now,
    ))
    session.add(LiveAccountingRecord(
        idempotency_key=f"{idempotency_prefix}-fill-sell", live_trading_profile_id=profile.id, capital_campaign_id=runtime.id,
        live_crypto_order_id=sell_order_id, reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created", record_type="fill_accounting", provider_order_id="p-sell",
        symbol="BTC-USD", side="sell", filled_quantity=Decimal("0.0001"), fill_price=Decimal("50010"),
        gross_notional=Decimal("5.001"), fee_amount=Decimal("0.02"), fee_currency="USD",
        net_cash_impact=sell_net_cash_impact, provenance={}, recorded_at=now + timedelta(minutes=1),
    ))
    await session.flush()
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

            outcome = await controlled_proof_service.evaluate_controlled_proof_risk(
                db=session, proof_id=uuid.uuid4(), campaign_id=_CAMPAIGN_ID, campaign_version=1,
                paper_account_id=paper_account_id, product_id="BTC-USD", side="BUY",
                notional_usd=Decimal("5"), actor="system:test",
            )

        assert outcome.verdict == "ALLOW"
        assert outcome.risk_event_id is not None
        persisted = await session.get(RiskEvent, outcome.risk_event_id)
        assert persisted is not None
        assert persisted.action_taken == "approved"


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
