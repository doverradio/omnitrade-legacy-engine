from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.asset_commissioning_run import AssetCommissioningRun
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.live_trading_profile import LiveTradingProfile
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.asset_commissioning import service as commissioning_service
from app.services.capital_campaign_domain import get_campaign_definition
from app.services.strategies.identity import build_strategy_identity
from tests.support.real_sqlite_session import real_sqlite_session

_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
_ALL_TABLES = [
    Asset.__table__, AssetCommissioningRun.__table__, AuditLog.__table__,
    AutonomousCapitalMandate.__table__, AutonomousCapitalMandateVersion.__table__,
    AutonomousCapitalMandateAuthorization.__table__, Candle.__table__,
    CapitalCampaign.__table__, CapitalCampaignDefinition.__table__, LiveTradingProfile.__table__,
    StrategyRosterRun.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


def _settings(*, mandate_id: uuid.UUID | None, campaign_id: uuid.UUID | None, discovery_mode: str = "env") -> SimpleNamespace:
    return SimpleNamespace(
        automatic_mandate_package_activation_mandate_id=mandate_id,
        automatic_mandate_package_activation_campaign_id=campaign_id,
        asset_discovery_mode=discovery_mode,
        autonomous_cycle_additional_products="",
        parsed_autonomous_cycle_additional_products=[],
    )


async def _seed_campaign(session: AsyncSession, *, campaign_id: uuid.UUID, allowed_instruments: list[str]) -> uuid.UUID:
    paper_account_id = uuid.uuid4()
    session.add(
        CapitalCampaignDefinition(
            campaign_id=campaign_id, version=1, name="test", owner_identity="operator:test", status="READY",
            capital_budget=Decimal("25"), remaining_unallocated_capital=Decimal("25"), base_currency="USD",
            allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"], allowed_instruments=allowed_instruments,
            campaign_modes=[], maximum_open_positions=1, maximum_position_size=Decimal("5"),
            minimum_position_size=Decimal("1"), maximum_total_exposure=Decimal("5"),
            profitability_policy_id="p", profitability_policy_version="1", risk_policy_id="r", risk_policy_version="1",
            compounding_policy={"policy_type": "FIXED_CAPITAL"},
        )
    )
    session.add(
        CapitalCampaign(
            uuid=campaign_id, owner="operator:test", name="test", status="READY", campaign_type="definition_pinned_runtime",
            definition_campaign_id=campaign_id, definition_version=1, paper_account_id=paper_account_id,
            starting_capital=Decimal("25"), current_equity=Decimal("25"),
        )
    )
    session.add(LiveTradingProfile(
        id=uuid.uuid4(), paper_account_id=paper_account_id, operating_mode="live", lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True, paper_default_mode=True,
        governance_approved=True, risk_authority_model="risk_engine_final", autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False, automatic_promotion_enabled=False, provenance_metadata={},
    ))
    await session.flush()
    return paper_account_id


async def _seed_active_mandate_with_version(
    session: AsyncSession, *, mandate_id: uuid.UUID, allowed_products: tuple[str, ...] = ("BTC-USD",),
) -> AutonomousCapitalMandateVersion:
    mandate = AutonomousCapitalMandate(
        mandate_id=mandate_id, owner_actor_id="operator:owner", status="ACTIVE", autonomy_level="LEVEL_2",
        provider="kraken_spot", exchange_environment="production", exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), capital_campaign_id=None,
    )
    session.add(mandate)
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
    return version


async def _seed_asset_with_candles(session: AsyncSession, *, symbol: str, candle_count: int, stale: bool = False) -> Asset:
    asset = Asset(symbol=symbol, asset_class="crypto", exchange="kraken_spot", base_currency="USD", is_active=True)
    session.add(asset)
    await session.flush()
    now = datetime.now(timezone.utc) - (timedelta(hours=6) if stale else timedelta(minutes=1))
    for i in range(candle_count):
        open_time = now - timedelta(minutes=15 * (candle_count - i))
        session.add(Candle(
            asset_id=asset.id, interval="15m", open_time=open_time, close_time=open_time + timedelta(minutes=15),
            open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("1"), source="kraken_spot",
        ))
    await session.flush()
    return asset


class _FakeProduct:
    def __init__(self) -> None:
        self.available = True
        self.trading_enabled = True
        self.min_order_notional = Decimal("1")
        self.quantity_increment = Decimal("0.01")


class _FakeProviderClient:
    async def fetch_product(self, *, credentials, environment, product_id):
        return _FakeProduct()


class _FailingProviderClient:
    async def fetch_product(self, *, credentials, environment, product_id):
        raise AssertionError("should not be called after an earlier stage already failed")


def _fake_candle(open_time: datetime):
    from app.services.data.binance_client import NormalizedCandle
    return NormalizedCandle(
        open_time=open_time, close_time=open_time + timedelta(minutes=15),
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100"),
        volume=Decimal("1"), source="kraken_spot",
    )


async def _fake_transition_canonical_campaign_status(*, db: AsyncSession, request):
    """Models transition_canonical_campaign_status's real, observable DRAFT ->
    READY side effect -- including the runtime repin, which the real function
    now performs atomically as part of this same mutation, never earlier --
    without exercising its own deep multi-table readiness gate (exchange
    connection status/balances/instrument candle history for every allowed
    instrument, open-order/position/reconciliation counts, etc.) -- that gate
    has its own extensive test suite in test_canonical_campaign_binding.py.
    These tests isolate and prove only _stage_campaign_authorized's own
    logic: what it creates, how it resumes, and that it never duplicates."""
    definition = await db.scalar(
        select(CapitalCampaignDefinition).where(
            CapitalCampaignDefinition.campaign_id == request.campaign_id,
            CapitalCampaignDefinition.version == request.campaign_version,
        )
    )
    runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.id == request.runtime_campaign_id))
    before = {"definition_status": definition.status, "runtime_status": runtime.status, "runtime_definition_version": runtime.definition_version}
    definition.status = "READY"
    runtime.status = "READY"
    runtime.definition_version = request.campaign_version
    await db.flush()
    after = {"definition_status": definition.status, "runtime_status": runtime.status, "runtime_definition_version": runtime.definition_version}
    return SimpleNamespace(changed=True, idempotent=False, audit_created=True, before=before, after=after, readiness=SimpleNamespace(ready=True, blockers=[]))


class _FakeMarketDataClient:
    def __init__(self, http_client) -> None:
        pass

    async def fetch_klines(self, *, symbol, interval, start_time, end_time):
        now = datetime.now(timezone.utc)
        return [_fake_candle(now - timedelta(minutes=15 * i)) for i in range(60, 0, -1)]


# --- preview: must never mutate -----------------------------------------------------

@pytest.mark.asyncio
async def test_preview_never_mutates_and_reports_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commissioning_service, "KrakenProviderClient", _FakeProviderClient)
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id))

        asset_count_before = len((await session.execute(select(Asset))).scalars().all())
        mandate_version_count_before = len((await session.execute(select(AutonomousCapitalMandateVersion))).scalars().all())
        campaign_version_count_before = len((await session.execute(select(CapitalCampaignDefinition))).scalars().all())

        result = await commissioning_service.preview_asset_commissioning(
            db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
        )

        asset_count_after = len((await session.execute(select(Asset))).scalars().all())
        mandate_version_count_after = len((await session.execute(select(AutonomousCapitalMandateVersion))).scalars().all())
        campaign_version_count_after = len((await session.execute(select(CapitalCampaignDefinition))).scalars().all())

    assert asset_count_before == asset_count_after
    assert mandate_version_count_before == mandate_version_count_after
    assert campaign_version_count_before == campaign_version_count_after
    assert result["provider_supported"] is True
    assert result["asset_registered"] is False
    assert result["campaign_mutation_required"] is True
    assert any("SOL-USD" in c and "allowed_instruments" in c for c in result["expected_changes"])
    assert result["mandate_successor_required"] is True
    assert Decimal(result["preserved_risk_constraints"]["authorized_capital_usd"]) == Decimal("25")


@pytest.mark.asyncio
async def test_preview_unsupported_provider_is_a_blocker_not_a_crash() -> None:
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        result = await commissioning_service.preview_asset_commissioning(
            db=session, provider="binance", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
        )
    assert result["provider_supported"] is False
    assert "unsupported_provider:binance" in result["blockers"]


# --- commission: idempotent, resumable, fail-closed, never drops products ----------

@pytest.mark.asyncio
async def test_commission_without_activate_stops_before_mandate_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commissioning_service, "KrakenProviderClient", _FakeProviderClient)
    monkeypatch.setattr(commissioning_service, "KrakenMarketDataClient", _FakeMarketDataClient)
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id))

        run = await commissioning_service.commission_asset(
            db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
            activate=False, idempotency_key="test-key-1", actor="operator:test",
        )

        assert run.status == "IN_PROGRESS"
        assert run.stages["CAMPAIGN_AUTHORIZED"]["status"] == "COMPLETED"
        assert "MANDATE_SUCCESSOR_CREATED" not in run.stages
        assert "MANDATE_AUTHORIZED_AND_PROMOTED" not in run.stages

        mandate_versions = (await session.execute(select(AutonomousCapitalMandateVersion))).scalars().all()
        assert len(mandate_versions) == 1, "activate=False must never create a mandate version"


@pytest.mark.asyncio
async def test_commission_with_activate_creates_successor_preserves_limits_and_promotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commissioning_service, "KrakenProviderClient", _FakeProviderClient)
    monkeypatch.setattr(commissioning_service, "KrakenMarketDataClient", _FakeMarketDataClient)
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        await _seed_asset_with_candles(session, symbol="BTC", candle_count=0)
        original = await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id, discovery_mode="campaign_db"))

        run = await commissioning_service.commission_asset(
            db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
            activate=True, idempotency_key="test-key-2", actor="operator:test",
        )

        assert run.status == "COMPLETED"
        assert run.mandate_version_id is not None
        assert run.mandate_version_id != original.mandate_version_id

        new_version = await session.get(AutonomousCapitalMandateVersion, run.mandate_version_id)
        assert set(new_version.allowed_products) == {"BTC-USD", "SOL-USD"}
        assert new_version.authorized_capital_usd == original.authorized_capital_usd
        assert new_version.max_order_notional_usd == original.max_order_notional_usd
        assert new_version.max_daily_realized_loss_usd == original.max_daily_realized_loss_usd
        assert new_version.max_campaign_drawdown_usd == original.max_campaign_drawdown_usd
        assert new_version.is_active is True
        assert new_version.is_authorized is True

        await session.refresh(original)
        assert original.is_active is False, "old version must be demoted, never left ambiguously governing"


@pytest.mark.asyncio
async def test_commission_is_idempotent_on_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commissioning_service, "KrakenProviderClient", _FakeProviderClient)
    monkeypatch.setattr(commissioning_service, "KrakenMarketDataClient", _FakeMarketDataClient)
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        await _seed_asset_with_candles(session, symbol="BTC", candle_count=0)
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id, discovery_mode="campaign_db"))

        first = await commissioning_service.commission_asset(
            db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
            activate=True, idempotency_key="test-key-3", actor="operator:test",
        )
        second = await commissioning_service.commission_asset(
            db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
            activate=True, idempotency_key="test-key-3", actor="operator:test",
        )

        assert first.commissioning_id == second.commissioning_id
        mandate_versions = (await session.execute(
            select(AutonomousCapitalMandateVersion).where(AutonomousCapitalMandateVersion.mandate_id == mandate_id)
        )).scalars().all()
        assert len(mandate_versions) == 2, "replay must not create a duplicate successor version"


@pytest.mark.asyncio
async def test_commission_fails_closed_on_provider_rejection_and_creates_no_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RejectingProviderClient:
        async def fetch_product(self, *, credentials, environment, product_id):
            product = _FakeProduct()
            product.trading_enabled = False
            return product

    monkeypatch.setattr(commissioning_service, "KrakenProviderClient", _RejectingProviderClient)
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id))

        with pytest.raises(InvalidRequestError):
            await commissioning_service.commission_asset(
                db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
                activate=True, idempotency_key="test-key-4", actor="operator:test",
            )

        run = (await session.execute(select(AssetCommissioningRun))).scalars().first()
        assert run.status == "FAILED"
        assert (await session.execute(select(Asset))).scalars().first() is None


# --- readiness: must require actual observed runtime processing --------------------

@pytest.mark.asyncio
async def test_readiness_not_ready_without_observed_strategy_roster_run(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "SOL-USD"))
        await _seed_asset_with_candles(session, symbol="SOL", candle_count=60)
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id))
        result = await commissioning_service.get_asset_readiness(db=session, product_id="SOL-USD", campaign_id=campaign_id)

    assert result["asset_registered"] is True
    assert result["campaign_authorized"] is True
    assert result["mandate_authorized"] is True
    assert result["strategy_evaluation_observed"] is False
    assert result["overall_status"] == "NOT_READY"
    assert "no_strategy_roster_run_observed_for_this_asset_yet" in result["blockers"]


@pytest.mark.asyncio
async def test_readiness_ready_only_after_observed_roster_run(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        await _seed_asset_with_candles(session, symbol="BTC", candle_count=0)
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD", "SOL-USD"))
        asset = await _seed_asset_with_candles(session, symbol="SOL", candle_count=60)
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id, discovery_mode="campaign_db"))

        session.add(StrategyRosterRun(
            idempotency_key="rr-1", asset_id=asset.id, provider="kraken_spot", product_id="SOL-USD", interval="15m",
            candle_open_time=datetime.now(timezone.utc), candle_close_time=datetime.now(timezone.utc),
            trigger="kraken_roster_15m_candle_close", started_at=datetime.now(timezone.utc),
        ))
        await session.flush()

        result = await commissioning_service.get_asset_readiness(db=session, product_id="SOL-USD", campaign_id=campaign_id)

    assert result["strategy_evaluation_observed"] is True
    assert result["runtime_selected"] is True
    assert result["overall_status"] == "READY"
    assert result["blockers"] == []


# --- Correction 1: governed campaign successor onboarding ---------------------------

@pytest.mark.asyncio
async def test_campaign_successor_adds_only_the_requested_product_and_preserves_everything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _fake_transition_canonical_campaign_status)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        before = await get_campaign_definition(db=session, campaign_id=campaign_id)

        result = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-1",
        )

        assert result.status == "COMPLETED"
        assert result.evidence["mutation_required"] is True
        assert result.evidence["successor_campaign_version"] == before.version + 1

        after = await get_campaign_definition(db=session, campaign_id=campaign_id)
        assert set(after.allowed_instruments) == {"BTC-USD", "SOL-USD"}
        assert after.status == "READY"
        assert after.version == before.version + 1
        # Every other field preserved exactly.
        assert after.capital_budget == before.capital_budget
        assert after.base_currency == before.base_currency
        assert after.allowed_venues == before.allowed_venues
        assert after.maximum_open_positions == before.maximum_open_positions
        assert after.maximum_position_size == before.maximum_position_size
        assert after.minimum_position_size == before.minimum_position_size
        assert after.maximum_total_exposure == before.maximum_total_exposure
        assert after.profitability_policy_id == before.profitability_policy_id
        assert after.risk_policy_id == before.risk_policy_id
        assert after.aggression_mode == before.aggression_mode

        # The prior governing version is untouched, never mutated in place.
        prior = await session.scalar(
            select(CapitalCampaignDefinition).where(
                CapitalCampaignDefinition.campaign_id == campaign_id, CapitalCampaignDefinition.version == before.version,
            )
        )
        assert prior.allowed_instruments == ["BTC-USD"]
        assert prior.status == "READY"


@pytest.mark.asyncio
async def test_campaign_successor_already_present_is_a_no_op() -> None:
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD", "SOL-USD"])
        result = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-2",
        )
    assert result.status == "COMPLETED"
    assert result.evidence["mutation_required"] is False


@pytest.mark.asyncio
async def test_repeated_campaign_commissioning_creates_no_duplicate_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _fake_transition_canonical_campaign_status)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])

        first = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-3",
        )
        second = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-3",
        )

        assert first.status == "COMPLETED"
        assert second.status == "COMPLETED"
        assert first.evidence["successor_campaign_version"] == second.evidence["successor_campaign_version"]

        versions = (await session.execute(
            select(CapitalCampaignDefinition).where(CapitalCampaignDefinition.campaign_id == campaign_id)
        )).scalars().all()
        assert len(versions) == 2, "exactly one successor must exist, not a duplicate per replay"


@pytest.mark.asyncio
async def test_campaign_stage_resumes_safely_after_partial_failure_between_create_and_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a crash between create_campaign_draft succeeding and
    transition_canonical_campaign_status running: the retry must reuse the
    already-created DRAFT successor and only run the transition, never create
    a second successor version."""
    monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _fake_transition_canonical_campaign_status)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])

        # Manually create the successor via the same reused service, exactly
        # as the stage function would, then leave it at DRAFT to simulate the
        # crash point (never calling the transition).
        before = await get_campaign_definition(db=session, campaign_id=campaign_id)
        created = await commissioning_service.create_campaign_draft(
            db=session,
            request=commissioning_service.CapitalCampaignDraftCreateRequest(
                campaign_id=campaign_id, name=before.name, description=before.description,
                owner_identity=before.owner_identity, status="DRAFT", capital_budget=before.capital_budget,
                remaining_unallocated_capital=before.remaining_unallocated_capital, base_currency=before.base_currency,
                allowed_asset_classes=list(before.allowed_asset_classes), allowed_venues=list(before.allowed_venues),
                allowed_instruments=list(before.allowed_instruments) + ["SOL-USD"], campaign_modes=list(before.campaign_modes),
                maximum_open_positions=before.maximum_open_positions, maximum_position_size=before.maximum_position_size,
                minimum_position_size=before.minimum_position_size, maximum_total_exposure=before.maximum_total_exposure,
                profitability_policy_id=before.profitability_policy_id, profitability_policy_version=before.profitability_policy_version,
                risk_policy_id=before.risk_policy_id, risk_policy_version=before.risk_policy_version,
                compounding_policy=before.compounding_policy, profit_distribution_policy=before.profit_distribution_policy,
                aggression_mode=before.aggression_mode, accounting_state=before.accounting_state,
                metadata_evidence=dict(before.metadata_evidence), non_live_only=True,
            ),
        )
        assert created.version == before.version + 1
        assert created.status == "DRAFT"

        # Now resume via the real stage function.
        result = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-4",
        )

        assert result.status == "COMPLETED"
        assert result.evidence["successor_campaign_version"] == created.version

        versions = (await session.execute(
            select(CapitalCampaignDefinition).where(CapitalCampaignDefinition.campaign_id == campaign_id)
        )).scalars().all()
        assert len(versions) == 2, "resume must not create a second successor on top of the partial one"
        after = await get_campaign_definition(db=session, campaign_id=campaign_id)
        assert after.status == "READY"
        governing = await commissioning_service.get_governing_campaign_definition(db=session, campaign_id=campaign_id)
        assert governing.version == created.version, "the resumed successor is what governance now resolves to"


# --- Issue 2: campaign successor transition production safety ----------------------

@pytest.mark.asyncio
async def test_prior_governing_campaign_remains_governing_until_atomic_transition_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves requirements (1), (2), (4), and (5). create_campaign_draft no
    longer repins a currently-governing (READY) runtime onto an unvalidated
    DRAFT successor -- that repin now happens only inside
    transition_canonical_campaign_status's own atomic, row-locked mutation,
    and only on success. The observing fake transition below captures state
    at exactly the mid-flight point (successor created, transition not yet
    run) to prove the runtime pin genuinely never moved: it's still READY,
    still pointing at the ORIGINAL version, and the governing lookup
    (which resolves strictly through that pin) still resolves to the
    original version with its original allowed_instruments -- not nothing,
    not the unvalidated successor."""
    campaign_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def _observing_fake_transition(*, db: AsyncSession, request):
        runtime = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.id == request.runtime_campaign_id))
        captured["runtime_status_mid_flight"] = runtime.status
        captured["runtime_definition_version_mid_flight"] = runtime.definition_version
        governing_mid_flight = await commissioning_service.get_governing_campaign_definition(db=db, campaign_id=campaign_id)
        captured["governing_version_mid_flight"] = governing_mid_flight.version if governing_mid_flight else None
        captured["governing_allowed_instruments_mid_flight"] = (
            list(governing_mid_flight.allowed_instruments) if governing_mid_flight else None
        )
        return await _fake_transition_canonical_campaign_status(db=db, request=request)

    monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _observing_fake_transition)
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        before = await get_campaign_definition(db=session, campaign_id=campaign_id)

        result = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-midflight",
        )

        assert result.status == "COMPLETED"
        # Requirement (2): the runtime pin never moved off the original,
        # still-governing version while the (by-then-created) successor was
        # awaiting its transition.
        assert captured["runtime_status_mid_flight"] == "READY"
        assert captured["runtime_definition_version_mid_flight"] == before.version
        # Requirement (1): the original READY version is still exactly what
        # governance resolves to -- not nothing, not the DRAFT successor.
        assert captured["governing_version_mid_flight"] == before.version
        assert captured["governing_allowed_instruments_mid_flight"] == ["BTC-USD"]

        # Requirement (4): only once the transition actually succeeds does
        # the pin move -- to the successor, and only the successor.
        after_governing = await commissioning_service.get_governing_campaign_definition(db=session, campaign_id=campaign_id)
        assert after_governing.version == before.version + 1
        assert set(after_governing.allowed_instruments) == {"BTC-USD", "SOL-USD"}


@pytest.mark.asyncio
async def test_failed_successor_transition_leaves_original_governing_version_and_pin_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves requirement (3): a transition_canonical_campaign_status failure
    (its own real readiness gate blocking it, e.g. no connected exchange
    connection) leaves BOTH the original definition row AND the runtime pin
    completely untouched -- because create_campaign_draft never moved the
    pin in the first place, there is nothing to revert. The campaign remains
    fully, continuously governed by its original version throughout a failed
    attempt -- not ungoverned, not governed by the failed successor. The
    DRAFT successor is left behind purely so a retry (requirement 6) can
    resume and complete it, but it is never treated as authorized."""
    async def _failing_transition(*, db: AsyncSession, request):
        raise PermissionError("canonical status transition prerequisites failed: provider_connection_exists")

    monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _failing_transition)
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        before = await get_campaign_definition(db=session, campaign_id=campaign_id)

        result = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-failtransition",
        )

        assert result.status == "FAILED"
        assert "campaign_transition_failed" in result.error

        original = await session.scalar(
            select(CapitalCampaignDefinition).where(
                CapitalCampaignDefinition.campaign_id == campaign_id, CapitalCampaignDefinition.version == before.version,
            )
        )
        assert original.status == "READY"
        assert original.allowed_instruments == ["BTC-USD"], "the original definition row itself is never mutated"

        runtime = await session.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id))
        assert runtime.status == "READY"
        assert runtime.definition_version == before.version, "the runtime pin never moved -- nothing to revert"

        # The campaign is continuously governed by its original version --
        # never ungoverned, never governed by the failed successor.
        governing = await commissioning_service.get_governing_campaign_definition(db=session, campaign_id=campaign_id)
        assert governing is not None
        assert governing.version == before.version
        assert governing.allowed_instruments == ["BTC-USD"]

        successor = await session.scalar(
            select(CapitalCampaignDefinition).where(
                CapitalCampaignDefinition.campaign_id == campaign_id, CapitalCampaignDefinition.version == before.version + 1,
            )
        )
        assert successor is not None and successor.status == "DRAFT", "left behind so a retry can resume and complete it"

        # Requirement (6): a retry resumes the SAME successor and, once the
        # transition succeeds, moves governance to it -- no duplicate version.
        monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _fake_transition_canonical_campaign_status)
        retry_result = await commissioning_service._stage_campaign_authorized(
            db=session, campaign_id=campaign_id, product_id="SOL-USD", actor="operator:test", idempotency_key="commission-sol-failtransition",
        )
        assert retry_result.status == "COMPLETED"
        assert retry_result.evidence["successor_campaign_version"] == successor.version
        versions = (await session.execute(
            select(CapitalCampaignDefinition).where(CapitalCampaignDefinition.campaign_id == campaign_id)
        )).scalars().all()
        assert len(versions) == 2, "the retry must resume the existing successor, never create a second one"
        governing_after_retry = await commissioning_service.get_governing_campaign_definition(db=session, campaign_id=campaign_id)
        assert governing_after_retry.version == successor.version


@pytest.mark.asyncio
async def test_concurrent_calls_cannot_create_two_successors_for_the_same_operation() -> None:
    """Proves requirement (6). The real race is two concurrent commissioning
    attempts both calling create_campaign_draft's repository.next_version()
    before either has committed its insert, so both compute the identical
    next version number -- not reproducible with a single shared SQLite
    connection, so modeled directly at that boundary: the second writer
    attempts to insert the exact (campaign_id, version) the first already
    committed. The existing uq_ccd_campaign_version unique constraint (reused,
    not invented here) is what actually prevents two divergent successor
    versions from ever coexisting -- the second writer must fail outright."""
    campaign_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        before = await get_campaign_definition(db=session, campaign_id=campaign_id)

        request_kwargs = dict(
            campaign_id=campaign_id, name=before.name, description=before.description, owner_identity=before.owner_identity,
            status="DRAFT", capital_budget=before.capital_budget, remaining_unallocated_capital=before.remaining_unallocated_capital,
            base_currency=before.base_currency, allowed_asset_classes=list(before.allowed_asset_classes),
            allowed_venues=list(before.allowed_venues), campaign_modes=list(before.campaign_modes),
            maximum_open_positions=before.maximum_open_positions, maximum_position_size=before.maximum_position_size,
            minimum_position_size=before.minimum_position_size, maximum_total_exposure=before.maximum_total_exposure,
            profitability_policy_id=before.profitability_policy_id, profitability_policy_version=before.profitability_policy_version,
            risk_policy_id=before.risk_policy_id, risk_policy_version=before.risk_policy_version,
            compounding_policy=before.compounding_policy, profit_distribution_policy=before.profit_distribution_policy,
            aggression_mode=before.aggression_mode, accounting_state=before.accounting_state,
            metadata_evidence=dict(before.metadata_evidence), non_live_only=True,
        )

        winner = await commissioning_service.create_campaign_draft(
            db=session,
            request=commissioning_service.CapitalCampaignDraftCreateRequest(
                allowed_instruments=list(before.allowed_instruments) + ["SOL-USD"], **request_kwargs,
            ),
        )
        assert winner.version == before.version + 1

        loser_definition = CapitalCampaignDefinition(
            campaign_id=campaign_id, version=winner.version, name=before.name, owner_identity=before.owner_identity,
            status="DRAFT", capital_budget=before.capital_budget, remaining_unallocated_capital=before.remaining_unallocated_capital,
            base_currency=before.base_currency, allowed_asset_classes=list(before.allowed_asset_classes),
            allowed_venues=list(before.allowed_venues), allowed_instruments=list(before.allowed_instruments) + ["ETH-USD"],
            campaign_modes=[], maximum_open_positions=before.maximum_open_positions, maximum_position_size=before.maximum_position_size,
            minimum_position_size=before.minimum_position_size, maximum_total_exposure=before.maximum_total_exposure,
            profitability_policy_id=before.profitability_policy_id, profitability_policy_version="1",
            risk_policy_id=before.risk_policy_id, risk_policy_version="1", compounding_policy={"policy_type": "FIXED_CAPITAL"},
        )
        session.add(loser_definition)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()

        versions = (await session.execute(
            select(CapitalCampaignDefinition).where(
                CapitalCampaignDefinition.campaign_id == campaign_id, CapitalCampaignDefinition.version == winner.version,
            )
        )).scalars().all()
        assert len(versions) == 1, "only the winning writer's row exists -- the loser never got committed"
        assert versions[0].allowed_instruments == ["BTC-USD", "SOL-USD"]


# --- No trades, orders, claims, fills, or positions are ever created ---------------

@pytest.mark.asyncio
async def test_commissioning_touches_no_execution_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test schema (_ALL_TABLES) intentionally never includes
    CanonicalPreviewPackage, AutonomousExecutionClaim, LiveCryptoOrder, or
    DecisionRecord -- if commission_asset's own code (as opposed to the
    separately-tested transition_canonical_campaign_status, mocked here per
    the tests above) touched any of them, this test would fail with 'no such
    table' rather than silently passing, which is a stronger guarantee than
    asserting empty tables would be."""
    monkeypatch.setattr(commissioning_service, "transition_canonical_campaign_status", _fake_transition_canonical_campaign_status)
    monkeypatch.setattr(commissioning_service, "KrakenProviderClient", _FakeProviderClient)
    monkeypatch.setattr(commissioning_service, "KrakenMarketDataClient", _FakeMarketDataClient)
    campaign_id = uuid.uuid4()
    mandate_id = uuid.uuid4()
    async with _real_session() as session:
        await _seed_campaign(session, campaign_id=campaign_id, allowed_instruments=["BTC-USD"])
        await _seed_asset_with_candles(session, symbol="BTC", candle_count=0)
        await _seed_active_mandate_with_version(session, mandate_id=mandate_id, allowed_products=("BTC-USD",))
        monkeypatch.setattr(commissioning_service, "get_settings", lambda: _settings(mandate_id=mandate_id, campaign_id=campaign_id, discovery_mode="campaign_db"))

        run = await commissioning_service.commission_asset(
            db=session, provider="kraken", product_id="SOL-USD", campaign_id=campaign_id, environment="production",
            activate=True, idempotency_key="commission-sol-5", actor="operator:test",
        )

    assert run.status == "COMPLETED"
