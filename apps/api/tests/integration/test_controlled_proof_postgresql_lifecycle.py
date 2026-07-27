from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings as real_get_settings
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.controlled_proof_run import ControlledProofRun
from app.models.exchange_connection import ExchangeConnection
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.parameter_set import ParameterSet
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.risk_equity_baseline import RiskEquityBaseline
from app.models.risk_rule_config import RiskRuleConfig
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.services.controlled_proof import service as controlled_proof_service
from app.services.exchange_connections.providers.base import (
    ExchangeBalanceItem,
    ExchangeBalanceSnapshot,
    ExchangeAuthResult,
    ExchangeOrderSubmissionResult,
    ExchangePreviewResult,
    ExchangePriceEvidence,
    ExchangeProviderFee,
    ExchangeProviderFill,
    ExchangeProviderOrder,
    ExchangeProductSnapshot,
)
from app.services.exchange_connections import service as exchange_connection_service
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.orchestration import continuous_pipeline_worker as worker
from app.services.orchestration.autonomous_execution_claims import release_execution_claim_scope_if_order_resolved
from app.services.risk import risk_monitor
from app.services.strategies.identity import build_strategy_identity


TEST_DATABASE_URL = os.getenv("OMNITRADE_LIFECYCLE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="OMNITRADE_LIFECYCLE_TEST_DATABASE_URL is required for the disposable PostgreSQL lifecycle test",
)

_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_STRATEGY_IDENTITY = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")


class _Settings:
    def __init__(self, *, mandate_id: uuid.UUID) -> None:
        self._base = real_get_settings()
        self.automatic_mandate_package_activation_mandate_id = mandate_id
        self.automatic_mandate_package_activation_campaign_id = _CAMPAIGN_ID
        self.automatic_mandate_package_activation_campaign_version = None
        self.automatic_mandate_package_activation_mandate_version_id = None
        self.automatic_mandate_package_activation_enabled = False
        self.live_crypto_order_submission_enabled = True
        self.asset_discovery_mode = "env"
        self.autonomous_cycle_additional_products = ""
        self.parsed_autonomous_cycle_additional_products = []

    def __getattr__(self, name: str):
        return getattr(self._base, name)


class _ExchangeBoundary:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, Decimal | None, Decimal | None, str]] = []
        self.orders: dict[str, ExchangeProviderOrder] = {}
        self.usd_balance = Decimal("25")
        self.btc_balance = Decimal("0")

    async def test_authentication(self, **kwargs) -> ExchangeAuthResult:
        return ExchangeAuthResult(
            reachable=True, authenticated=True, account_status="active", permissions=["trade", "read"],
            heartbeat_at=datetime.now(timezone.utc), trade_permission_present=True,
        )

    async def fetch_product(self, **kwargs) -> ExchangeProductSnapshot:
        return ExchangeProductSnapshot(
            product_id="BTC-USD", available=True, trading_enabled=True,
            min_order_notional=Decimal("1"), min_order_quantity=Decimal("0.00000001"),
            quantity_increment=Decimal("0.00000001"),
        )

    async def fetch_price_evidence(self, **kwargs) -> ExchangePriceEvidence:
        now = datetime.now(timezone.utc)
        return ExchangePriceEvidence(
            evidence_id=uuid.uuid4(), provider="kraken_spot", venue="kraken_spot",
            product_id="BTC-USD", symbol="BTC", quote_currency="USD", base_currency="BTC",
            bid=Decimal("49999"), ask=Decimal("50001"), midpoint=Decimal("50000"),
            last_trade=Decimal("50000"), reference_price=Decimal("50000"), observed_at=now,
            retrieved_at=now, latency_ms=1, freshness_seconds=0, source_endpoint="test-boundary",
            retrieval_method="mocked_external_exchange", confidence=Decimal("1"),
        )

    async def fetch_balances(self, **kwargs) -> ExchangeBalanceSnapshot:
        return ExchangeBalanceSnapshot(
            balances=[
                ExchangeBalanceItem(currency="USD", available=self.usd_balance, reserved=Decimal("0"), total=self.usd_balance),
                ExchangeBalanceItem(currency="BTC", available=self.btc_balance, reserved=Decimal("0"), total=self.btc_balance),
            ],
            total_equity_usd=self.usd_balance + self.btc_balance * Decimal("50000"),
        )

    async def preview_market_order(self, **kwargs) -> ExchangePreviewResult:
        quote_size = kwargs.get("quote_size")
        base_size = kwargs.get("base_size") or (Decimal(str(quote_size)) / Decimal("50000"))
        return ExchangePreviewResult(
            preview_id=f"preview-{kwargs['side'].lower()}-{uuid.uuid4()}", success=True,
            failure_reason=None, warning_messages=[], estimated_average_price=Decimal("50000"),
            estimated_total_value=Decimal(str(base_size)) * Decimal("50000") + Decimal("0.01"),
            estimated_base_size=Decimal(str(base_size)),
            estimated_quote_size=Decimal(str(base_size)) * Decimal("50000"),
            estimated_fee=Decimal("0.01"), estimated_fee_currency="USD",
            estimated_slippage=Decimal("0"), estimated_commission_total=Decimal("0.01"),
            best_bid=Decimal("49999"), best_ask=Decimal("50001"),
            exchange_response_summary={"external_boundary_mock": True},
        )

    async def submit_order(self, *, request, **kwargs) -> ExchangeOrderSubmissionResult:
        provider_order_id = f"provider-{request.side.lower()}-{len(self.submissions) + 1}"
        self.submissions.append((request.side, request.quote_size, request.base_size, request.client_order_id))
        order = ExchangeProviderOrder(
            provider_order_id=provider_order_id, client_order_id=request.client_order_id,
            product_id=request.product_id, side=request.side, status="OPEN",
            submitted_at=datetime.now(timezone.utc), acknowledged_at=datetime.now(timezone.utc),
            raw={"external_boundary_mock": True},
        )
        self.orders[provider_order_id] = order
        return ExchangeOrderSubmissionResult(
            classification="success", order=order, rejection=None, ambiguous=None,
            raw_response={"external_boundary_mock": True}, safe_headers={},
        )

    async def get_historical_order(self, *, order_id=None, provider_order_id=None, **kwargs):
        resolved = str(order_id or provider_order_id)
        order = self.orders[resolved]
        return {
            "order": {
                "order_id": resolved, "client_order_id": order.client_order_id,
                "product_id": order.product_id, "status": "FILLED",
                "completion_time": datetime.now(timezone.utc).isoformat(),
            }
        }, {}

    async def list_historical_fills(self, *, order_id=None, provider_order_id=None, **kwargs):
        resolved = str(order_id or provider_order_id)
        order = self.orders[resolved]
        price = Decimal("50000") if order.side == "BUY" else Decimal("50100")
        size = Decimal("0.0001")
        if order.side == "BUY":
            self.usd_balance = Decimal("19.999")
            self.btc_balance = size
        else:
            self.usd_balance = Decimal("25.008")
            self.btc_balance = Decimal("0")
        return {
            "fills": [{
                "trade_id": f"fill-{resolved}", "order_id": resolved,
                "price": str(price), "size": str(size), "commission": "0.001",
                "commission_currency": "USD", "created_at": datetime.now(timezone.utc).isoformat(),
            }]
        }, {}


async def _truncate_database(session) -> None:
    await session.execute(text("""
        DO $$ DECLARE item record;
        BEGIN
          FOR item IN SELECT tablename FROM pg_tables
                      WHERE schemaname='public' AND tablename <> 'alembic_version'
          LOOP EXECUTE format('TRUNCATE TABLE %I CASCADE', item.tablename); END LOOP;
        END $$;
    """))
    await session.commit()


async def _seed_scope(session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    now = datetime.now(timezone.utc)
    account = PaperAccount(
        owner_user_id=uuid.uuid4(), name="controlled-proof-lifecycle", asset_class="crypto",
        starting_balance=Decimal("25"), current_cash_balance=Decimal("25"), is_active=True,
    )
    session.add(account)
    await session.flush()
    session.add_all([
        RiskKillSwitch(scope="global", paper_account_id=None, engaged=False, rearm_required=False, changed_by="test"),
        RiskKillSwitch(scope="account", paper_account_id=account.id, engaged=False, rearm_required=False, changed_by="test"),
        RiskRuleConfig(
            paper_account_id=account.id, max_position_size_pct=Decimal("1"),
            max_daily_loss_pct=Decimal("1"), max_drawdown_pct=Decimal("1"),
            default_stop_loss_pct=Decimal("0.03"), cooldown_after_losses=3,
            cooldown_duration_hours=24,
        ),
        RiskEquityBaseline(
            paper_account_id=account.id, session_date=now.date(),
            start_of_day_equity=Decimal("25"), start_of_day_source="test",
            start_of_day_recorded_at=now, high_water_mark_equity=Decimal("25"),
            high_water_mark_source="test", high_water_mark_recorded_at=now,
            last_equity=Decimal("25"), last_cash_balance=Decimal("25"),
            last_position_value=Decimal("0"), last_price_timestamp=None,
            valuation_source="test", valuation_state="ready",
        ),
    ])
    profile = LiveTradingProfile(
        paper_account_id=account.id, operating_mode="live", lifecycle_state="enabled",
        approval_state="approved", live_opt_in=True, human_approval_recorded=True,
        paper_default_mode=True, governance_approved=True, risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False, autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={"test": "postgresql-lifecycle", "provider": "kraken_spot", "environment": "production"},
    )
    connection = ExchangeConnection(
        provider="kraken_spot", connection_name="disposable-lifecycle", environment="production",
        status="connected", credentials_encrypted="test-only", api_key_masked="test",
        api_secret_masked="test", credentials_valid=True, api_permissions=["trade", "read"],
        account_status="active", balances=[{"currency": "USD", "available": "25"}, {"currency": "BTC", "available": "0"}],
        total_equity_usd="25", last_successful_sync_at=now, last_heartbeat_at=now,
        last_verified_at=now, last_readiness_verdict="READY_FOR_DRY_RUN", last_readiness_report=[],
    )
    session.add_all([profile, connection])
    await session.flush()
    definition = CapitalCampaignDefinition(
        campaign_id=_CAMPAIGN_ID, version=1, name="controlled-proof-lifecycle",
        owner_identity="operator:test", status="READY", capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"), base_currency="USD",
        allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD"], campaign_modes=[], maximum_open_positions=1,
        maximum_position_size=Decimal("5"), minimum_position_size=Decimal("1"),
        maximum_total_exposure=Decimal("5"), profitability_policy_id="test-profit",
        profitability_policy_version="1", risk_policy_id="default", risk_policy_version="1",
        compounding_policy={"policy_type": "FIXED_CAPITAL"},
    )
    runtime = CapitalCampaign(
        uuid=_CAMPAIGN_ID, owner="operator:test", name="controlled-proof-lifecycle", status="READY",
        campaign_type="definition_pinned_runtime", definition_campaign_id=_CAMPAIGN_ID,
        definition_version=1, paper_account_id=account.id, starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
    )
    session.add(definition)
    await session.flush()
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
        mandate_id=mandate.mandate_id, version_number=1, version_hash="lifecycle-v1",
        base_currency="USD", authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("10"),
        max_daily_realized_loss_usd=Decimal("5"), max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=3, position_limit=1, price_evidence_max_age_seconds=60,
        max_slippage_bps=Decimal("50"), max_fee_bps=Decimal("100"), allowed_products=["BTC-USD"],
        allowed_order_sides=["BUY", "SELL"], allowed_strategy_versions=[_STRATEGY_IDENTITY],
        entry_policy={"minimum_base_quantity": "0.00000001"}, exit_policy={}, cooldown_policy={},
        operating_schedule={}, approval_policy="MANDATE_ALLOWED", reconciliation_policy={},
        kill_switch_policy={}, owner_acknowledgements={"test": True},
        authorization_evidence_summary={"test": True}, is_authorized=True, is_active=True,
    )
    session.add(version)
    await session.flush()
    session.add(AutonomousCapitalMandateAuthorization(
        mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id,
        authorization_state="AUTHORIZED", approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        authorized_by_actor_id="operator:test", authorization_method="controlled-proof-lifecycle-test",
        owner_acknowledgements={"test": True}, authorization_evidence={"test": True},
        deterministic_explanation={"test": True}, idempotency_key="controlled-proof-lifecycle-authorization",
    ))
    strategy = Strategy(
        name="MA Crossover", slug="ma_crossover", description="lifecycle test strategy",
        module_version="1.0.0", is_active=True,
    )
    session.add(strategy)
    await session.flush()
    session.add(ParameterSet(strategy_id=strategy.id, label="lifecycle-v1", params={}, created_by="test"))
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
    return mandate.mandate_id, account.id, profile.id


@pytest.mark.asyncio
async def test_complete_controlled_proof_buy_sell_lifecycle_uses_canonical_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    boundary = _ExchangeBoundary()
    try:
        async with sessions() as session:
            await _truncate_database(session)
            mandate_id, account_id, profile_id = await _seed_scope(session)
            settings = _Settings(mandate_id=mandate_id)

            monkeypatch.setattr(worker, "get_settings", lambda: settings)
            monkeypatch.setattr("app.services.asset_commissioning.service.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.orchestration.automatic_package_executor.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.orchestration.autonomous_execution_claims.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.crypto_order_previews.service.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.live_crypto_orders.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.crypto_order_previews.service.get_decrypted_credentials_for_connection", lambda _connection: {"test": "only"})
            monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _connection: {"test": "only"})
            monkeypatch.setattr("app.services.crypto_order_previews.service.get_exchange_provider", lambda *_args, **_kwargs: boundary)
            monkeypatch.setattr("app.services.live_crypto_orders.get_exchange_provider", lambda *_args, **_kwargs: boundary)
            monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_args, **_kwargs: boundary)
            monkeypatch.setattr(exchange_connection_service, "get_exchange_provider", lambda *_args, **_kwargs: boundary)
            monkeypatch.setattr(exchange_connection_service, "_decrypt_credentials", lambda _connection: {"api_key": "test", "api_secret": "test"})

            # No ambient autonomous signal/cycle decision is seeded. Prove
            # that a fresh Controlled Proof risk DENY is terminal and
            # truthful through the real Risk service before exercising the
            # ALLOW lifecycle.
            assert await session.scalar(select(func.count()).select_from(Signal)) == 0
            denied, _denied_replay = await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="postgres-lifecycle-denied",
                expires_in_minutes=30, actor="operator:test",
            )
            await session.commit()
            await risk_monitor.enable_kill_switch(
                db=session, scope="global", account_id=None, reason="lifecycle-deny-evidence",
                confirm=True, actor="operator:test",
            )
            await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=denied.proof_id)
            denied_view = await controlled_proof_service.get_controlled_proof_view(
                db=session, proof_id=denied.proof_id,
            )
            assert denied_view["status"] == "BLOCKED"
            assert denied_view["terminal_verdict"] == "BLOCKED"
            assert "controlled_proof_risk_denied" in denied_view["blocked_reason"]
            await risk_monitor.disable_kill_switch(
                db=session, scope="global", account_id=None, reason="continue-allow-lifecycle",
                confirm=True, actor="operator:test",
            )

            proof, _replay = await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="postgres-lifecycle-proof",
                expires_in_minutes=30, actor="operator:test",
            )
            assert proof.status == "REQUESTED"
            proof_id = proof.proof_id
            await session.commit()

            await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof_id)
            proof = await session.get(ControlledProofRun, proof_id)
            assert proof is not None
            assert proof.package_id is not None
            buy_package = await session.get(CanonicalPreviewPackage, proof.package_id)
            assert buy_package is not None and buy_package.side == "BUY"
            buy_claim = await session.scalar(select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == buy_package.package_id))
            assert buy_claim is not None
            buy_order = await session.get(LiveCryptoOrder, buy_claim.live_order_id)
            assert buy_order is not None and buy_order.provider_order_id is not None

            first_buy_reconciliation = await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=buy_order.live_crypto_order_id,
                operator_identity="system:controlled_proof_test",
            )
            await session.commit()
            assert first_buy_reconciliation["accounting_completion_status"] == "unresolved"
            assert await compute_signed_owned_quantity(
                db=session, live_trading_profile_id=profile_id, symbol="BTC-USD",
            ) == Decimal("0.0001")

            # The first fill projection deliberately leaves unresolved
            # balance evidence. Supervision must not propose SELL until the
            # real connection refresh and idempotent reconciliation replay
            # resolve it.
            await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof_id)
            proof = await session.get(ControlledProofRun, proof_id)
            assert proof is not None and proof.sell_package_id is None
            connection = await session.scalar(select(ExchangeConnection).where(ExchangeConnection.provider == "kraken_spot"))
            assert connection is not None
            await exchange_connection_service.refresh_exchange_balances(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                actor="system:controlled_proof_test",
            )
            second_buy_reconciliation = await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=buy_order.live_crypto_order_id,
                operator_identity="system:controlled_proof_test",
            )
            await session.commit()
            assert second_buy_reconciliation["accounting_completion_status"] == "complete"
            await release_execution_claim_scope_if_order_resolved(
                db=session, live_crypto_order_id=buy_order.live_crypto_order_id,
                order_status=buy_order.status,
            )
            await session.commit()

            await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof_id)
            proof = await session.get(ControlledProofRun, proof_id)
            assert proof is not None and proof.sell_package_id is not None
            sell_package = await session.get(CanonicalPreviewPackage, proof.sell_package_id)
            assert sell_package is not None and sell_package.side == "SELL"
            sell_claim = await session.scalar(select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.package_id == sell_package.package_id))
            assert sell_claim is not None
            sell_order = await session.get(LiveCryptoOrder, sell_claim.live_order_id)
            assert sell_order is not None and sell_order.provider_order_id is not None

            first_sell_reconciliation = await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=sell_order.live_crypto_order_id,
                operator_identity="system:controlled_proof_test",
            )
            await session.commit()
            assert first_sell_reconciliation["accounting_completion_status"] == "unresolved"
            assert await compute_signed_owned_quantity(
                db=session, live_trading_profile_id=profile_id, symbol="BTC-USD",
            ) == Decimal("0")

            await exchange_connection_service.refresh_exchange_balances(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                actor="system:controlled_proof_test",
            )
            second_sell_reconciliation = await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=sell_order.live_crypto_order_id,
                operator_identity="system:controlled_proof_test",
            )
            await session.commit()
            assert second_sell_reconciliation["accounting_completion_status"] == "complete"
            assert Decimal(second_sell_reconciliation["net_quote_capital_effect"]) == Decimal("5.009")
            await release_execution_claim_scope_if_order_resolved(
                db=session, live_crypto_order_id=sell_order.live_crypto_order_id,
                order_status=sell_order.status,
            )
            await session.commit()

            view = await controlled_proof_service.get_controlled_proof_view(db=session, proof_id=proof_id)
            assert view["status"] in {"RECONCILED", "EXITED", "PROFIT_CONFIRMED"}
            assert view["net_pnl_usd"] == Decimal("0.008")
            assert [item[0] for item in boundary.submissions] == ["BUY", "SELL"]

            await worker._attempt_operator_controlled_proof_entry(db=session, proof_id=proof_id)
            assert [item[0] for item in boundary.submissions] == ["BUY", "SELL"]
            assert await session.scalar(select(func.count()).select_from(LiveAccountingRecord)) > 0
            assert await session.scalar(select(func.count()).select_from(LiveReconciliationEvent)) > 0

            second, _second_replay = await controlled_proof_service.create_controlled_proof(
                db=session, product_id="BTC-USD", idempotency_key="postgres-lifecycle-proof-2",
                expires_in_minutes=30, actor="operator:test",
            )
            assert second.proof_id != proof_id and second.status == "REQUESTED"
    finally:
        await engine.dispose()
