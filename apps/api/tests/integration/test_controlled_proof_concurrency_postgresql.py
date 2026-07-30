from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings as real_get_settings
from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.controlled_proof_run import ControlledProofRun
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.strategy import Strategy
from app.services.controlled_proof import service as controlled_proof_service
from app.services.strategies.identity import build_strategy_identity

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"

_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")


async def _db_available() -> bool:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


class _Settings:
    """Only what get_asset_readiness (via asset_commissioning) needs to
    report this scope fully ready -- create_controlled_proof itself never
    touches the exchange boundary, so none of the provider/credential mocks
    the full BUY/SELL lifecycle test requires are needed here."""

    def __init__(self, *, mandate_id: uuid.UUID) -> None:
        self._base = real_get_settings()
        self.automatic_mandate_package_activation_mandate_id = mandate_id
        self.automatic_mandate_package_activation_campaign_id = _CAMPAIGN_ID
        self.automatic_mandate_package_activation_campaign_version = None
        self.automatic_mandate_package_activation_mandate_version_id = None
        self.asset_discovery_mode = "env"
        self.autonomous_cycle_additional_products = ""
        self.parsed_autonomous_cycle_additional_products = []

    def __getattr__(self, name: str):
        return getattr(self._base, name)


async def _truncate_database(session: AsyncSession) -> None:
    await session.execute(text("""
        DO $$ DECLARE item record;
        BEGIN
          FOR item IN SELECT tablename FROM pg_tables
                      WHERE schemaname='public' AND tablename <> 'alembic_version'
          LOOP EXECUTE format('TRUNCATE TABLE %I CASCADE', item.tablename); END LOOP;
        END $$;
    """))
    await session.commit()


async def _seed_ready_scope(session: AsyncSession) -> uuid.UUID:
    """Campaign + mandate + asset/candles + connection + profile + account --
    exactly what create_controlled_proof's own scope/readiness checks require.
    Mirrors test_controlled_proof_postgresql_lifecycle.py's _seed_scope,
    trimmed to only what proof creation itself (not execution) touches."""
    now = datetime.now(timezone.utc)
    account = PaperAccount(
        owner_user_id=uuid.uuid4(), name="controlled-proof-concurrency", asset_class="crypto",
        starting_balance=Decimal("25"), current_cash_balance=Decimal("25"), is_active=True,
    )
    session.add(account)
    await session.flush()
    profile = LiveTradingProfile(
        paper_account_id=account.id, operating_mode="live", lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True,
        paper_default_mode=True, governance_approved=True, risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False, autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={"test": "controlled-proof-concurrency", "provider": "kraken_spot", "environment": "production"},
    )
    connection = ExchangeConnection(
        provider="kraken_spot", connection_name="disposable-concurrency", environment="production",
        status="connected", credentials_encrypted="test-only", api_key_masked="test",
        api_secret_masked="test", credentials_valid=True, api_permissions=["trade", "read"],
        account_status="active", balances=[{"currency": "USD", "available": "25"}],
        total_equity_usd="25", last_successful_sync_at=now, last_heartbeat_at=now,
        last_verified_at=now, last_readiness_verdict="READY_FOR_DRY_RUN", last_readiness_report=[],
    )
    session.add_all([profile, connection])
    await session.flush()
    definition = CapitalCampaignDefinition(
        campaign_id=_CAMPAIGN_ID, version=1, name="controlled-proof-concurrency",
        owner_identity="operator:test", status="READY", capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"), base_currency="USD",
        allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD"], campaign_modes=[], maximum_open_positions=1,
        maximum_position_size=Decimal("5"), minimum_position_size=Decimal("1"),
        maximum_total_exposure=Decimal("5"), profitability_policy_id="test-profit",
        profitability_policy_version="1", risk_policy_id="default", risk_policy_version="1",
        compounding_policy={"policy_type": "FIXED_CAPITAL"},
    )
    session.add(definition)
    await session.flush()
    runtime = CapitalCampaign(
        uuid=_CAMPAIGN_ID, owner="operator:test", name="controlled-proof-concurrency", status="READY",
        campaign_type="definition_pinned_runtime", definition_campaign_id=_CAMPAIGN_ID,
        definition_version=1, paper_account_id=account.id, starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
    )
    session.add(runtime)
    await session.flush()
    mandate = AutonomousCapitalMandate(
        owner_actor_id="operator:test", status="ACTIVE", autonomy_level="LEVEL_2",
        provider="kraken_spot", exchange_environment="production",
        exchange_connection_id=connection.exchange_connection_id,
        live_trading_profile_id=profile.id, paper_account_id=account.id,
        capital_campaign_id=runtime.id,
    )
    session.add(mandate)
    await session.flush()
    version = AutonomousCapitalMandateVersion(
        mandate_id=mandate.mandate_id, version_number=1, version_hash="concurrency-v1",
        base_currency="USD", authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("10"),
        max_daily_realized_loss_usd=Decimal("5"), max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=3, position_limit=1, price_evidence_max_age_seconds=60,
        max_slippage_bps=Decimal("50"), max_fee_bps=Decimal("100"), allowed_products=["BTC-USD"],
        allowed_order_sides=["BUY", "SELL"], allowed_strategy_versions=[_STRATEGY_IDENTITY],
        entry_policy={}, exit_policy={}, cooldown_policy={}, operating_schedule={},
        approval_policy="MANDATE_ALLOWED", reconciliation_policy={}, kill_switch_policy={},
        owner_acknowledgements={"test": True}, authorization_evidence_summary={"test": True},
        is_authorized=True, is_active=True,
    )
    session.add(version)
    await session.flush()
    session.add(AutonomousCapitalMandateAuthorization(
        mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id,
        authorization_state="AUTHORIZED", approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        authorized_by_actor_id="operator:test", authorization_method="controlled-proof-concurrency-test",
        owner_acknowledgements={"test": True}, authorization_evidence={"test": True},
        deterministic_explanation={"test": True}, idempotency_key="controlled-proof-concurrency-authorization",
    ))
    strategy = Strategy(
        name="MA Crossover", slug="ma_crossover", description="concurrency test strategy",
        module_version="1.0.0", is_active=True,
    )
    session.add(strategy)
    asset = Asset(
        symbol="BTC", asset_class="crypto", exchange="kraken_spot", base_currency="USD",
        is_active=True, min_order_notional=Decimal("1"), qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
    )
    session.add(asset)
    await session.flush()
    for index in range(60, 0, -1):
        opened = now - timedelta(minutes=15 * index)
        session.add(Candle(
            asset_id=asset.id, interval="15m", open_time=opened, close_time=opened + timedelta(minutes=15),
            open=Decimal("50000"), high=Decimal("50100"), low=Decimal("49900"),
            close=Decimal("50000"), volume=Decimal("1"), source="kraken_spot",
        ))
    await session.commit()
    return mandate.mandate_id


@pytest.mark.asyncio
async def test_two_simultaneous_creation_requests_against_a_stale_active_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """True concurrency (asyncio.gather, two independent AsyncSession
    instances/connections against the same real PostgreSQL database state --
    not two sequential calls) racing create_controlled_proof while a genuinely
    expired, safe-to-recover Controlled Proof is active. Proves: the stale
    proof is recovered exactly once; at most one replacement proof is
    created; the loser deterministically raises the active-proof rejection
    (the two calls use distinct idempotency keys, so an idempotent replay of
    each other is not possible here -- InvalidRequestError is the only
    deterministic outcome for the loser); no two rows are ever left in an
    active Controlled Proof state; exactly one stale-recovery audit
    transition is written."""
    if not await _db_available():
        pytest.skip("PostgreSQL unavailable for concurrency integration test")

    engine = create_async_engine(TEST_DATABASE_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as seed_session:
            await _truncate_database(seed_session)
            mandate_id = await _seed_ready_scope(seed_session)
            settings = _Settings(mandate_id=mandate_id)
            monkeypatch.setattr("app.services.asset_commissioning.service.get_settings", lambda: settings)

            stale, _ = await controlled_proof_service.create_controlled_proof(
                db=seed_session, product_id="BTC-USD", idempotency_key="concurrency-stale",
                expires_in_minutes=30, actor="operator:seed",
            )
            stale.status = "PACKAGE_CREATED"
            stale.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            await seed_session.commit()
            stale_proof_id = stale.proof_id

        async def _create(idempotency_key: str, actor: str):
            async with session_factory() as session:
                return await controlled_proof_service.create_controlled_proof(
                    db=session, product_id="BTC-USD", idempotency_key=idempotency_key,
                    expires_in_minutes=30, actor=actor,
                )

        results = await asyncio.gather(
            _create("concurrency-new-a", "operator:a"),
            _create("concurrency-new-b", "operator:b"),
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, Exception)]
        failures = [r for r in results if isinstance(r, Exception)]

        # Exactly one request wins and creates a genuinely new proof; the
        # other deterministically fails closed rather than also succeeding.
        assert len(successes) == 1
        assert len(failures) == 1
        # The loser's exact path depends on scheduling (it may see the
        # now-EXPIRED stale row and lose the unique-index race on its own
        # insert, falling to the generic already-active fallback with
        # details={}; or it may unblock after the winner's new proof is
        # already committed and see THAT as the active row instead, with
        # active_proof_id populated) -- both are the same deterministic
        # outcome class the task asks for: an active-proof rejection, never
        # a second successful creation.
        assert isinstance(failures[0], InvalidRequestError)
        assert failures[0].message == "Another controlled proof is already active"

        new_proof, replaced = successes[0]
        assert replaced is None
        assert new_proof.proof_id != stale_proof_id
        assert new_proof.status == "REQUESTED"

        async with session_factory() as verify_session:
            refreshed_stale = await verify_session.get(ControlledProofRun, stale_proof_id)
            assert refreshed_stale.status == "EXPIRED"
            assert refreshed_stale.terminal_verdict == "FAILED"
            assert refreshed_stale.failure_reason == "expired_before_execution_completion"

            active_count = await verify_session.scalar(
                select(func.count()).select_from(ControlledProofRun).where(
                    ControlledProofRun.status.in_(controlled_proof_service._ACTIVE_STATES)
                )
            )
            assert int(active_count or 0) == 1

            recovery_audits = (await verify_session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == stale_proof_id,
                    AuditLog.action == "controlled_proof_run.stale_recovery_expired",
                )
            )).scalars().all()
            assert len(recovery_audits) == 1
    finally:
        await engine.dispose()
