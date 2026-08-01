from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.autonomous_proof_sell_attempt import AutonomousProofSellAttempt
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.candle import Candle
from app.models.controlled_proof_run import ControlledProofRun
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.risk_equity_baseline import RiskEquityBaseline
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.risk_rule_config import RiskRuleConfig
from app.models.strategy_aggregate_decision import StrategyAggregateDecision
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle
from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle
from app.services.exchange_connections.providers.base import (
    ExchangeAuthResult, ExchangeBalanceItem, ExchangeBalanceSnapshot, ExchangeOrderSubmissionResult,
    ExchangePreviewResult, ExchangePriceEvidence, ExchangeProductSnapshot, ExchangeProviderOrder,
)
from app.services.exchange_connections import service as exchange_connection_service
from app.config import get_settings as real_get_settings
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.orchestration.automatic_package_executor import (
    AutomaticPackageExecutionRequest, execute_automatic_ready_package_through_activation,
)
from app.services.orchestration.autonomous_execution_claims import (
    advance_claimed_execution, claim_activated_package, release_execution_claim_scope_if_order_resolved,
)
from app.services.orchestration import continuous_pipeline_worker as worker
from app.services.orchestration import autonomous_proof_sell_worker as proof_sell_worker
from app.services.orchestration.autonomous_position_exit_evaluation import evaluate_due_custodies
from tests.support.canonical_autonomous_roster_postgresql import build_canonical_buy_roster


URL = os.getenv("AUTONOMOUS_ROSTER_POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="disposable PostgreSQL URL required")


class _ExternalBoundary:
    def __init__(self, *, ambiguous_sell_once=False):
        self.submissions = 0
        self.sell_submissions = 0
        self.sell_recoveries = 0
        self.sell_recovery_pending = False
        self.ambiguous_sell_once = ambiguous_sell_once
        self.usd = Decimal("25")
        self.btc = Decimal("0")
        self.order = None

    async def test_authentication(self, **_kwargs):
        return ExchangeAuthResult(
            reachable=True, authenticated=True, account_status="active", permissions=["trade", "read"],
            heartbeat_at=datetime.now(timezone.utc), trade_permission_present=True,
        )

    async def fetch_product(self, **_kwargs):
        return ExchangeProductSnapshot(
            product_id="BTC-USD", available=True, trading_enabled=True,
            min_order_notional=Decimal("1"), min_order_quantity=Decimal("0.00000001"),
            quantity_increment=Decimal("0.00000001"),
        )

    async def fetch_balances(self, **_kwargs):
        return ExchangeBalanceSnapshot(balances=[
            ExchangeBalanceItem(currency="USD", available=self.usd, reserved=Decimal("0"), total=self.usd),
            ExchangeBalanceItem(currency="BTC", available=self.btc, reserved=Decimal("0"), total=self.btc),
        ], total_equity_usd=self.usd + self.btc * Decimal("50000"))

    async def fetch_price_evidence(self, **_kwargs):
        now = datetime.now(timezone.utc)
        return ExchangePriceEvidence(
            evidence_id=uuid.uuid4(), provider="kraken_spot", venue="kraken_spot",
            product_id="BTC-USD", symbol="BTC", quote_currency="USD", base_currency="BTC",
            bid=Decimal("49999"), ask=Decimal("50001"), midpoint=Decimal("50000"),
            last_trade=Decimal("50000"), reference_price=Decimal("50000"), observed_at=now,
            retrieved_at=now, latency_ms=1, freshness_seconds=0, source_endpoint="test-boundary",
            retrieval_method="fixed-external-boundary", confidence=Decimal("1"),
        )

    async def preview_market_order(self, **kwargs):
        side = str(kwargs.get("side") or "BUY").upper()
        price = Decimal("80000") if side == "SELL" else Decimal("50000")
        quantity = Decimal(str(kwargs.get("base_size") or "0"))
        quote = Decimal(str(kwargs.get("quote_size") or "0"))
        if quantity <= 0:
            quantity = quote / price
        if quote <= 0:
            quote = quantity * price
        return ExchangePreviewResult(
            preview_id="canonical-autonomous-buy-preview", success=True, failure_reason=None,
            warning_messages=[], estimated_average_price=price, estimated_total_value=quote,
            estimated_base_size=quantity, estimated_quote_size=quote, estimated_fee=Decimal("0.01"),
            estimated_fee_currency="USD", estimated_slippage=Decimal("0"),
            estimated_commission_total=Decimal("0.01"), best_bid=price - 1,
            best_ask=price + 1, exchange_response_summary={"fixed_external_boundary": True},
        )

    async def submit_order(self, **_kwargs):
        self.submissions += 1
        request = _kwargs["request"]
        if request.side == "SELL":
            self.sell_submissions += 1
            self.order = ExchangeProviderOrder(
                provider_order_id="canonical-sell-provider-order-1", client_order_id=request.client_order_id,
                product_id="BTC-USD", side="SELL", status="OPEN",
                submitted_at=datetime(2026, 8, 1, 20, 10, tzinfo=timezone.utc),
                acknowledged_at=datetime(2026, 8, 1, 20, 10, 1, tzinfo=timezone.utc), raw={"fixed": True},
            )
            self.usd = Decimal("27.997")
            self.btc = Decimal("0")
            if self.ambiguous_sell_once:
                self.ambiguous_sell_once = False
                self.sell_recovery_pending = True
                raise TimeoutError("fixed post-acceptance ambiguity")
            return ExchangeOrderSubmissionResult(
                classification="success", order=self.order, rejection=None, ambiguous=None,
                raw_response={"fixed": True}, safe_headers={},
            )
        self.order = ExchangeProviderOrder(
            provider_order_id="canonical-buy-provider-order-1", client_order_id=request.client_order_id,
            product_id="BTC-USD", side="BUY", status="OPEN",
            submitted_at=datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc),
            acknowledged_at=datetime(2026, 8, 1, 20, 0, 1, tzinfo=timezone.utc), raw={"fixed": True},
        )
        return ExchangeOrderSubmissionResult(
            classification="success", order=self.order, rejection=None, ambiguous=None,
            raw_response={"fixed": True}, safe_headers={},
        )

    async def get_historical_order(self, **_kwargs):
        if self.order.side == "SELL":
            return {"order": {
                "order_id": "canonical-sell-provider-order-1", "client_order_id": self.order.client_order_id,
                "product_id": "BTC-USD", "side": "SELL", "status": "FILLED",
                "completion_time": "2026-08-01T20:10:02+00:00",
            }}, {}
        return {"order": {
            "order_id": "canonical-buy-provider-order-1", "client_order_id": self.order.client_order_id,
            "product_id": "BTC-USD", "side": "BUY", "status": "FILLED",
            "completion_time": "2026-08-01T20:00:02+00:00",
        }}, {}

    async def list_historical_fills(self, **_kwargs):
        if self.order.side == "SELL":
            self.usd = Decimal("27.997")
            self.btc = Decimal("0")
            return {"fills": [{
                "trade_id": "canonical-sell-fill-1", "order_id": "canonical-sell-provider-order-1",
                "price": "80000", "size": "0.0001", "commission": "0.002",
                "commission_currency": "USD", "created_at": "2026-08-01T20:10:02+00:00",
            }]}, {}
        self.usd = Decimal("19.999")
        self.btc = Decimal("0.0001")
        return {"fills": [{
            "trade_id": "canonical-buy-fill-1", "order_id": "canonical-buy-provider-order-1",
            "price": "50000", "size": "0.0001", "commission": "0.001",
            "commission_currency": "USD", "created_at": "2026-08-01T20:00:02+00:00",
        }]}, {}

    async def lookup_order(self, **_kwargs):
        if self.order.side == "SELL" and self.sell_recovery_pending:
            self.sell_recoveries += 1
            self.sell_recovery_pending = False
        return ExchangeProviderOrder(
            provider_order_id=self.order.provider_order_id,
            client_order_id=self.order.client_order_id,
            product_id=self.order.product_id,
            side=self.order.side,
            status="FILLED",
            submitted_at=self.order.submitted_at,
            acknowledged_at=self.order.acknowledged_at,
            raw={"fixed": True},
        )


class _ExecutionSettings:
    def __init__(self, fixture, captured):
        self._base = real_get_settings()
        self.automatic_mandate_package_activation_enabled = True
        self.automatic_mandate_package_activation_campaign_id = fixture.campaign_id
        self.automatic_mandate_package_activation_campaign_version = 1
        self.automatic_mandate_package_activation_mandate_id = captured["mandate_id"]
        self.automatic_mandate_package_activation_mandate_version_id = captured["version_id"]
        self.automatic_mandate_package_activation_package_id = None
        self.live_crypto_order_submission_enabled = True
        self.autonomous_proof_sell_worker_enabled = True
        self.autonomous_position_exit_submission_enabled = True
        self.autonomous_proof_sell_campaign_id = fixture.campaign_id
        self.autonomous_proof_sell_campaign_version = 1
        self.autonomous_proof_sell_runtime_campaign_id = None

    def __getattr__(self, name):
        return getattr(self._base, name)


async def _truncate(session):
    await session.execute(text("""DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'
      LOOP EXECUTE format('TRUNCATE TABLE %I CASCADE', r.tablename); END LOOP; END $$"""))
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verification_mode",
    ["replay", "package_rollback", "custody_rollback", "sell_replay", "sell_recovery"],
)
async def test_real_roster_reaches_one_mandate_bound_ready_buy_package(monkeypatch, verification_mode):
    engine = create_async_engine(URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    boundary = _ExternalBoundary(ambiguous_sell_once=verification_mode == "sell_recovery")
    captured = {}

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: boundary)
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _row: {"test": "only"})
    monkeypatch.setattr("app.services.crypto_order_previews.service.get_exchange_provider", lambda _provider: boundary)
    monkeypatch.setattr("app.services.crypto_order_previews.service.get_decrypted_credentials_for_connection", lambda _row: {"test": "only"})

    async def create_real_scheduled_cycle(*, db, now, account, runtime_campaign, candle, **_kwargs):
        db.add_all([
            RiskKillSwitch(scope="global", paper_account_id=None, engaged=False, rearm_required=False, changed_by="test"),
            RiskKillSwitch(scope="account", paper_account_id=account.id, engaged=False, rearm_required=False, changed_by="test"),
            RiskRuleConfig(paper_account_id=account.id, max_position_size_pct=Decimal("1"),
                           max_daily_loss_pct=Decimal("1"), max_drawdown_pct=Decimal("1"),
                           default_stop_loss_pct=Decimal("0.03"), cooldown_after_losses=3,
                           cooldown_duration_hours=24),
            RiskEquityBaseline(paper_account_id=account.id, session_date=now.date(),
                               start_of_day_equity=Decimal("25"), start_of_day_source="test",
                               start_of_day_recorded_at=now, high_water_mark_equity=Decimal("25"),
                               high_water_mark_source="test", high_water_mark_recorded_at=now,
                               last_equity=Decimal("25"), last_cash_balance=Decimal("25"),
                               last_position_value=Decimal("0"), valuation_source="test", valuation_state="ready"),
        ])
        profile = LiveTradingProfile(
            paper_account_id=account.id, operating_mode="live", lifecycle_state="enabled",
            approval_state="approved", live_opt_in=True, human_approval_recorded=True,
            paper_default_mode=True, governance_approved=True, risk_authority_model="risk_engine_final",
            autonomous_capital_allocation=False, autonomous_strategy_evolution=False,
            automatic_promotion_enabled=False, provenance_metadata={"ordinary_production": True},
        )
        connection = ExchangeConnection(
            provider="kraken_spot", connection_name="canonical-autonomous-buy", environment="production",
            status="connected", credentials_encrypted="test-only", api_key_masked="test",
            api_secret_masked="test", credentials_valid=True, api_permissions=["trade", "read"],
            account_status="active", balances=[{"currency": "USD", "available": "25"}],
            total_equity_usd="25", last_successful_sync_at=now, last_heartbeat_at=now,
            last_verified_at=now, last_readiness_verdict="READY_FOR_PREVIEW", last_readiness_report=[],
        )
        db.add_all([profile, connection]); await db.flush()
        mandate = AutonomousCapitalMandate(
            owner_actor_id="test:ordinary-production", status="ACTIVE", autonomy_level="LEVEL_2",
            purpose="PRODUCTION", provider="kraken_spot", exchange_environment="production",
            exchange_connection_id=connection.exchange_connection_id, live_trading_profile_id=profile.id,
            paper_account_id=account.id, capital_campaign_id=runtime_campaign.id,
        )
        db.add(mandate); await db.flush()
        version = AutonomousCapitalMandateVersion(
            mandate_id=mandate.mandate_id, version_number=1, version_hash="canonical-autonomous-buy-v1",
            base_currency="USD", authorized_capital_usd=Decimal("25"), max_order_notional_usd=Decimal("5"),
            max_open_exposure_usd=Decimal("5"), max_daily_deployed_usd=Decimal("10"),
            max_daily_realized_loss_usd=Decimal("5"), max_campaign_drawdown_usd=Decimal("5"),
            max_consecutive_losses=3, position_limit=1, price_evidence_max_age_seconds=60,
            max_slippage_bps=Decimal("50"), max_fee_bps=Decimal("100"), allowed_products=["BTC-USD"],
            allowed_order_sides=["BUY", "SELL"], allowed_strategy_versions=[
                "ma_crossover@1.0.0", "strategy_roster_aggregate@1.0.0",
            ],
            entry_policy={"minimum_base_quantity": "0.00000001"}, exit_policy={}, cooldown_policy={},
            operating_schedule={}, approval_policy="MANDATE_ALLOWED", reconciliation_policy={},
            kill_switch_policy={}, owner_acknowledgements={"test": True},
            authorization_evidence_summary={"ordinary_production": True}, is_authorized=True, is_active=True,
        )
        db.add(version); await db.flush()
        db.add(AutonomousCapitalMandateAuthorization(
            mandate_id=mandate.mandate_id, mandate_version_id=version.mandate_version_id,
            authorization_state="AUTHORIZED", approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            authorized_by_actor_id="test:ordinary-production", authorization_method="test-fixed-evidence",
            owner_acknowledgements={"test": True}, authorization_evidence={"test": True},
            deterministic_explanation={"test": True}, idempotency_key=f"authorization:{mandate.mandate_id}",
        ))
        result = await run_autonomous_preview_cycle(db=db, request=AutonomousCycleRequest(
            mandate_id=mandate.mandate_id, actor="test:ordinary-production", product_id="BTC-USD",
            strategy_interval="15m", trigger="kraken_btc_15m_candle_close",
            idempotency_seed=candle.close_time.isoformat(), candle_id=candle.id,
            candle_close_time=candle.close_time,
            allow_paper_execution_handoff=False,
        ))
        await db.commit()
        assert result.proposed_action == "BUY"
        assert result.mandate_verdict == "AUTHORIZED"
        assert result.risk_verdict in {"ACCEPTED", "RESIZED"}
        captured.update(mandate_id=mandate.mandate_id, version_id=version.mandate_version_id,
                        profile_id=profile.id, autonomous_cycle_id=result.cycle_id)
        return result.cycle_id

    try:
        async with sessions() as session:
            await _truncate(session)
            fixture = await build_canonical_buy_roster(
                db=session, scheduled_cycle_builder=create_real_scheduled_cycle,
                seed_positive_buy_scorecard=True,
            )
            payload = await run_campaign_orchestration_preview_for_candle(
                db=session, campaign_id=fixture.campaign_id, version=1,
                trigger=fixture.trigger,
            )
            assert payload["cycle_count"] == 1
            await worker._attempt_automatic_ready_package_creation(
                db=session, orchestration_payload=payload,
                originating_autonomous_cycle_id=captured["autonomous_cycle_id"],
                autonomous_cycle_ids_by_product={"BTC-USD": captured["autonomous_cycle_id"]},
            )
            await session.flush()

            if verification_mode == "package_rollback":
                assert await session.scalar(select(func.count()).select_from(CanonicalPreviewPackage)) == 1
                assert await session.scalar(select(func.count()).select_from(AutonomousCapitalMandateEvaluation)) == 2
                await session.rollback()
                assert await session.scalar(select(func.count()).select_from(CanonicalPreviewPackage)) == 0
                # Mandate evaluations are immutable audit events and their
                # service commits them independently. The campaign evaluation
                # and its cycle linkage remain truthful authorization evidence;
                # only the uncommitted READY package must disappear.
                assert await session.scalar(select(func.count()).select_from(AutonomousCapitalMandateEvaluation)) == 2
                campaign_cycle = await session.scalar(
                    select(AutonomousCycleRun).where(AutonomousCycleRun.cycle_kind == "campaign")
                )
                assert campaign_cycle is not None
                campaign_evaluation = await session.get(
                    AutonomousCapitalMandateEvaluation, campaign_cycle.mandate_evaluation_id,
                )
                assert campaign_evaluation is not None
                assert campaign_cycle.mandate_id == campaign_evaluation.mandate_id
                assert campaign_cycle.mandate_version_id == campaign_evaluation.mandate_version_id
                assert campaign_evaluation.decision_id == campaign_cycle.decision_record_id
                assert await session.scalar(select(func.count()).select_from(StrategyAggregateDecision)) == 1
                assert boundary.submissions == 0
                return

            await session.commit()

            package = await session.scalar(select(CanonicalPreviewPackage))
            assert package is not None and package.package_state == "READY" and package.side == "BUY"
            assert package.proposed_order_amount == Decimal("5")
            assert package.campaign_id == fixture.campaign_id
            assert package.paper_account_id == fixture.account_id
            assert package.live_trading_profile_id == captured["profile_id"]
            assert package.mandate_id == captured["mandate_id"]
            assert package.mandate_version_id == captured["version_id"]
            assert await session.scalar(select(func.count()).select_from(StrategyRosterRun)) == 1
            assert await session.scalar(select(func.count()).select_from(StrategyRosterProposal)) == 7
            assert await session.scalar(select(func.count()).select_from(StrategyAggregateDecision)) == 1
            assert await session.scalar(select(func.count()).select_from(CanonicalPreviewPackage)) == 1
            assert await session.scalar(select(func.count()).select_from(ControlledProofRun)) == 0
            assert boundary.submissions == 0

            evaluations = list((await session.scalars(
                select(AutonomousCapitalMandateEvaluation).order_by(AutonomousCapitalMandateEvaluation.created_at)
            )).all())
            assert len(evaluations) == 2
            assert {item.decision_id for item in evaluations} == {
                (await session.get(AutonomousCycleRun, captured["autonomous_cycle_id"])).decision_record_id,
                package.decision_record_id,
            }
            evaluation_ids = {item.evaluation_id for item in evaluations}

            settings = _ExecutionSettings(fixture, captured)
            monkeypatch.setattr("app.services.orchestration.automatic_package_executor.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.orchestration.autonomous_execution_claims.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.live_crypto_orders.get_settings", lambda: settings)
            monkeypatch.setattr("app.services.live_crypto_orders._load_decrypted_credentials", lambda _row: {"test": "only"})
            monkeypatch.setattr("app.services.live_crypto_orders.get_exchange_provider", lambda *_a, **_k: boundary)
            monkeypatch.setattr("app.services.live.accounting_reconciliation.get_exchange_provider", lambda *_a, **_k: boundary)
            monkeypatch.setattr(exchange_connection_service, "get_exchange_provider", lambda *_a, **_k: boundary)
            monkeypatch.setattr(exchange_connection_service, "_decrypt_credentials", lambda _row: {"test": "only"})

            activation = await execute_automatic_ready_package_through_activation(
                db=session,
                request=AutomaticPackageExecutionRequest(
                    campaign_id=package.campaign_id, campaign_version=package.campaign_version,
                    decision_record_id=package.decision_record_id, package_id=package.package_id,
                ),
            )
            assert activation.activation_state == "ACTIVATED" and activation.failed_closed is False
            claim_outcome = await claim_activated_package(db=session, package_id=package.package_id)
            assert claim_outcome.claim is not None
            await advance_claimed_execution(db=session, claim=claim_outcome.claim)
            await session.commit()
            claim = await session.get(AutonomousExecutionClaim, claim_outcome.claim.claim_id)
            order = await session.get(LiveCryptoOrder, claim.live_order_id)
            assert order is not None and order.provider_order_id == "canonical-buy-provider-order-1"
            assert boundary.submissions == 1

            first_reconciliation = await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=order.live_crypto_order_id,
                operator_identity="test:ordinary-production",
            )
            await session.commit()
            connection = await session.get(ExchangeConnection, claim.connection_id)
            await exchange_connection_service.refresh_exchange_balances(
                db=session, exchange_connection_id=connection.exchange_connection_id,
                actor="test:ordinary-production",
            )
            second_reconciliation = await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=order.live_crypto_order_id,
                operator_identity="test:ordinary-production",
            )
            claim_status_before_custody = claim.claim_status
            claim_id = claim.claim_id
            activation_id = claim.activation_id
            live_order_id = order.live_crypto_order_id
            await release_execution_claim_scope_if_order_resolved(
                db=session, live_crypto_order_id=order.live_crypto_order_id, order_status=order.status,
            )
            if verification_mode == "custody_rollback":
                # Simulate a caller-owned unit failing after custody composition
                # and claim terminalization have both flushed but before commit.
                assert await session.scalar(select(func.count()).select_from(AutonomousPositionCustody)) == 1
                assert claim.claim_status == "BUY_RECONCILED"
                await session.rollback()

                recovered_claim = await session.get(AutonomousExecutionClaim, claim_id)
                recovered_activation = await session.get(CanonicalProvingActivation, activation_id)
                recovered_order = await session.get(LiveCryptoOrder, live_order_id)
                assert recovered_claim.claim_status == claim_status_before_custody
                assert recovered_claim.completed_at is None
                assert recovered_activation.activation_state == "ACTIVE"
                assert recovered_order.status == "RECONCILIATION_REQUIRED"
                assert await session.scalar(select(func.count()).select_from(AutonomousPositionCustody)) == 0
                # Accounting writes are immutable evidence committed by the
                # accounting service itself. They remain truthful and exactly
                # once, while the caller-owned order terminalization, custody,
                # and claim release roll back together and remain recoverable.
                recovered_accounting = list((await session.scalars(select(LiveAccountingRecord))).all())
                assert {item.record_type for item in recovered_accounting} == {
                    "fill_accounting", "fee_attribution",
                }
                assert {item.provider_fill_id for item in recovered_accounting} == {"canonical-buy-fill-1"}
                assert boundary.submissions == 1
                return
            await session.commit()
            assert first_reconciliation["provider_fill_observed"] is True
            assert second_reconciliation["accounting_completion_status"] == "complete", second_reconciliation

            custody = await session.scalar(select(AutonomousPositionCustody))
            accounting = list((await session.scalars(select(LiveAccountingRecord))).all())
            assert custody is not None and custody.custody_state == "ACTIVE" and custody.proof_eligible is True
            assert custody.original_acquired_quantity == Decimal("0.0001")
            assert custody.observed_remaining_quantity == Decimal("0.0001")
            assert {item.record_type for item in accounting} == {"fill_accounting", "fee_attribution"}
            fill_accounting = next(item for item in accounting if item.record_type == "fill_accounting")
            fee_accounting = next(item for item in accounting if item.record_type == "fee_attribution")
            assert fill_accounting.filled_quantity == Decimal("0.0001")
            assert fill_accounting.fill_price == Decimal("50000")
            assert fill_accounting.gross_notional == Decimal("5")
            assert fee_accounting.fee_amount == Decimal("0.001")
            assert fill_accounting.gross_notional + fee_accounting.fee_amount == Decimal("5.001")
            assert claim.claim_status == "BUY_RECONCILED"
            assert custody.provenance_classification == "SCHEDULED_PRODUCTION_AUTONOMOUS"
            assert custody.autonomous_origin is True
            assert custody.continuing_exit_authority_state == "UNARMED"
            assert custody.audit_metadata["sell_supervisor_connected"] is False
            assert await session.scalar(select(func.count()).select_from(ControlledProofRun)) == 0

            if verification_mode in {"sell_replay", "sell_recovery"}:
                settings.autonomous_proof_sell_runtime_campaign_id = custody.runtime_campaign_id
                sell_now = datetime.now(timezone.utc).replace(microsecond=0)
                session.add(Candle(
                    asset_id=fixture.asset_id, interval="15m",
                    open_time=sell_now - timedelta(minutes=16),
                    close_time=sell_now - timedelta(minutes=1),
                    open=Decimal("79900"), high=Decimal("80100"), low=Decimal("79800"),
                    close=Decimal("80000"), volume=Decimal("100"), source="fixed-test-price",
                ))
                await session.commit()
                evaluation = await evaluate_due_custodies(db=session, now=sell_now, limit=1)
                await session.commit()
                assert evaluation.exit_recommended == 1

                monkeypatch.setattr(proof_sell_worker, "get_settings", lambda: settings)
                monkeypatch.setattr(
                    "app.services.orchestration.autonomous_position_exit_submission.get_settings",
                    lambda: settings,
                )
                monkeypatch.setattr(
                    "app.services.orchestration.autonomous_position_exit_submission.get_exchange_provider",
                    lambda *_a, **_k: boundary,
                )
                monkeypatch.setattr(
                    "app.services.orchestration.autonomous_position_exit_submission._load_decrypted_credentials",
                    lambda _row: {"test": "only"},
                )

                transitions = []
                maximum_calls = 10 if verification_mode == "sell_recovery" else 9
                for index in range(maximum_calls):
                    existing_attempt = await session.scalar(select(AutonomousProofSellAttempt))
                    starting_stage = None if existing_attempt is None else existing_attempt.stage
                    result = await proof_sell_worker.advance_one_autonomous_proof_sell_stage(
                        db=session, now=sell_now + timedelta(seconds=31 * index), cadence_seconds=30,
                    )
                    await session.commit()
                    transitions.append((starting_stage, result.action, result.stage))
                    if (verification_mode == "sell_recovery" and result.action == "submitted"
                            and result.stage == "ORDERED"):
                        failed_attempt = await session.get(AutonomousProofSellAttempt, result.attempt_id)
                        failed_custody = await session.get(AutonomousPositionCustody, custody.custody_id)
                        failed_order = await session.get(LiveCryptoOrder, failed_attempt.order_id)
                        assert failed_order.status == "RECONCILIATION_REQUIRED"
                        assert failed_custody.custody_state == "EXIT_PENDING"
                        assert failed_custody.realized_net_profit is None
                        assert failed_custody.autonomous_proof_sell_verified is False
                        assert boundary.sell_submissions == 1
                    if result.action == "submitted":
                        await exchange_connection_service.refresh_exchange_balances(
                            db=session, exchange_connection_id=connection.exchange_connection_id,
                            actor="test:ordinary-production-sell",
                        )
                        await session.commit()

                attempt = await session.scalar(select(AutonomousProofSellAttempt))
                assert attempt.order_id is not None, (transitions, attempt.blocker, attempt.terminal_reason)
                authority = await session.get(AutonomousPositionExitAuthority, attempt.authority_id)
                sell_package = await session.get(CanonicalPreviewPackage, attempt.package_id)
                sell_claim = await session.get(AutonomousExecutionClaim, attempt.claim_id)
                sell_order = await session.get(LiveCryptoOrder, attempt.order_id)
                custody = await session.get(AutonomousPositionCustody, custody.custody_id)
                sell_accounting = list((await session.scalars(select(LiveAccountingRecord).where(
                    LiveAccountingRecord.live_crypto_order_id == sell_order.live_crypto_order_id,
                ))).all())

                assert transitions[0] == (None, "selected", "SELECTED")
                assert transitions[-1] == ("TERMINAL", "hard_stopped", "TERMINAL")
                assert attempt.stage == "TERMINAL" and attempt.proof_sell_verified is True
                assert authority.authority_state == "CONSUMED"
                assert sell_package.side == "SELL" and sell_package.package_state == "ACTIVATED"
                assert sell_claim.side == "SELL" and sell_claim.claim_status == "COMPLETED"
                assert sell_order.status == "FILLED"
                assert sell_order.provider_order_id == "canonical-sell-provider-order-1"
                assert custody.custody_state == "CLOSED" and custody.observed_remaining_quantity == 0
                assert custody.autonomous_proof_sell_verified is True
                assert custody.realized_sold_quantity == Decimal("0.0001")
                assert custody.realized_gross_sell_proceeds == Decimal("8")
                assert custody.realized_sell_fees == Decimal("0.002")
                assert custody.realized_net_sell_proceeds == Decimal("7.998")
                assert custody.allocated_buy_cost_basis == Decimal("5")
                assert custody.allocated_buy_fees == Decimal("0.001")
                assert custody.realized_net_profit == Decimal("2.997")
                assert custody.realized_return == Decimal("2.997") / Decimal("5.001")
                assert {row.provider_fill_id for row in sell_accounting} == {"canonical-sell-fill-1"}
                assert {row.record_type for row in sell_accounting} == {"fill_accounting", "fee_attribution"}
                assert boundary.submissions == 2 and boundary.sell_submissions == 1
                assert boundary.sell_recoveries == (1 if verification_mode == "sell_recovery" else 0)
                assert await session.scalar(select(func.count()).select_from(AutonomousProofSellAttempt)) == 1
                assert await session.scalar(select(func.count()).select_from(CanonicalPreviewPackage).where(
                    CanonicalPreviewPackage.side == "SELL",
                )) == 1
                assert await session.scalar(select(func.count()).select_from(AutonomousExecutionClaim).where(
                    AutonomousExecutionClaim.side == "SELL",
                )) == 1
                assert await session.scalar(select(func.count()).select_from(LiveCryptoOrder).where(
                    LiveCryptoOrder.side == "SELL",
                )) == 1
                assert await session.scalar(select(func.count()).select_from(ControlledProofRun)) == 0
                return

            replay_activation = await execute_automatic_ready_package_through_activation(
                db=session,
                request=AutomaticPackageExecutionRequest(
                    campaign_id=package.campaign_id, campaign_version=package.campaign_version,
                    decision_record_id=package.decision_record_id, package_id=package.package_id,
                ),
            )
            replay_claim = await claim_activated_package(db=session, package_id=package.package_id)
            await advance_claimed_execution(db=session, claim=replay_claim.claim)
            await reconcile_live_order_and_fills(
                db=session, live_crypto_order_id=order.live_crypto_order_id,
                operator_identity="test:ordinary-production",
            )
            await release_execution_claim_scope_if_order_resolved(
                db=session, live_crypto_order_id=order.live_crypto_order_id, order_status=order.status,
            )
            await session.commit()
            assert replay_activation.replayed is True
            assert boundary.submissions == 1
            assert await session.scalar(select(func.count()).select_from(CanonicalProvingActivation)) == 1
            assert await session.scalar(select(func.count()).select_from(AutonomousExecutionClaim)) == 1
            assert await session.scalar(select(func.count()).select_from(LiveCryptoOrder)) == 1
            # Each bounded reconciliation pass is an immutable observation.
            # Four attempts (initial, post-balance refresh, replay, and the
            # replay's claim advance) retain one provider fill identity and
            # one semantic accounting outcome rather than overwriting audit.
            assert await session.scalar(select(func.count()).select_from(LiveReconciliationEvent).where(
                LiveReconciliationEvent.reconciliation_status == "filled",
            )) == 4
            assert await session.scalar(select(func.count()).select_from(LiveAccountingRecord)) == 2
            assert await session.scalar(select(func.count()).select_from(AutonomousPositionCustody)) == 1

            await worker._attempt_automatic_ready_package_creation(
                db=session, orchestration_payload=payload,
                originating_autonomous_cycle_id=captured["autonomous_cycle_id"],
                autonomous_cycle_ids_by_product={"BTC-USD": captured["autonomous_cycle_id"]},
            )
            await session.commit()
            assert await session.scalar(select(func.count()).select_from(CanonicalPreviewPackage)) == 1
            assert await session.scalar(select(func.count()).select_from(AutonomousCapitalMandateEvaluation)) == 3
            replayed_evaluations = list((await session.scalars(select(AutonomousCapitalMandateEvaluation))).all())
            assert evaluation_ids < {item.evaluation_id for item in replayed_evaluations}
            assert {item.decision_id for item in replayed_evaluations} == {
                (await session.get(AutonomousCycleRun, captured["autonomous_cycle_id"])).decision_record_id,
                package.decision_record_id,
            }
    finally:
        await engine.dispose()
