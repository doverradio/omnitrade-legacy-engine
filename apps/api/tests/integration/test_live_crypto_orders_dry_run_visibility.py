from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.db.session import get_db
from app.main import create_app
from app.models.audit_log import AuditLog
from app.models.live_crypto_order import LiveCryptoOrder
from app.schemas.mission_control import MissionControlIntelligenceTimelineEventResponse
from app.schemas.operations import (
    LiveCryptoReadinessItemResponse,
    LiveCryptoReadinessResponse,
    OperationalAlertResponse,
    OperationalHealthIndicatorResponse,
    OperationalMonitoringResponse,
    OperationalRunStatusResponse,
    OperationalStatusResponse,
)
from app.schemas.dashboard import DashboardIntelligenceScoreResponse, DashboardIntelligenceTimelinePointResponse
from app.services import mission_control_intelligence as mission_control_service
from app.services import live_crypto_orders as live_crypto_orders_service
from app.services.live_crypto_orders import service as live_crypto_orders


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _ExecuteResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _ScalarResult(self._items)


class _FakeSession:
    def __init__(self, *, profile, preview, connection, paper_account, campaign, asset, global_switch, account_switch) -> None:
        self.profile = profile
        self.preview = preview
        self.connection = connection
        self.paper_account = paper_account
        self.campaign = campaign
        self.asset = asset
        self.global_switch = global_switch
        self.account_switch = account_switch
        self.audit_logs: list[AuditLog] = []
        self.live_orders: list[LiveCryptoOrder] = []

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM crypto_order_previews" in sql:
            return self.preview
        if "FROM exchange_connections" in sql:
            return self.connection
        if "FROM paper_accounts" in sql:
            return self.paper_account
        if "FROM capital_campaigns" in sql:
            return self.campaign
        if "FROM assets" in sql:
            return self.asset
        if "FROM risk_kill_switches" in sql:
            scope = params.get("scope_1")
            account_id = params.get("paper_account_id_1")
            if scope == "global" and account_id is None:
                return self.global_switch
            if scope == "account" and account_id == self.account_switch.paper_account_id:
                return self.account_switch
            return None
        if "FROM live_crypto_orders" in sql:
            preview_id = params.get("crypto_order_preview_id_1")
            for order in self.live_orders:
                if order.crypto_order_preview_id == preview_id:
                    return order
            return None
        if "FROM audit_log" in sql:
            return None
        return None

    async def scalars(self, _statement):
        return _ScalarResult([])

    async def execute(self, _statement, *_args, **_kwargs):
        return _ExecuteResult(list(self.audit_logs))

    def add(self, obj):
        if isinstance(obj, LiveCryptoOrder):
            if obj.live_crypto_order_id is None:
                obj.live_crypto_order_id = uuid4()
            now = datetime.now(timezone.utc)
            if obj.created_at is None:
                obj.created_at = now
            if obj.updated_at is None:
                obj.updated_at = now
            self.live_orders.append(obj)
            return
        if isinstance(obj, AuditLog):
            if obj.id is None:
                obj.id = len(self.audit_logs) + 1
            if obj.created_at is None:
                obj.created_at = datetime.now(timezone.utc)
            self.audit_logs.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, _obj):
        return None


class _RiskRules:
    def __init__(self) -> None:
        self.rules = {
            "max_position_size_pct": Decimal("0.05"),
            "max_daily_loss_pct": Decimal("0.03"),
            "max_drawdown_pct": Decimal("0.15"),
        }


def _now() -> datetime:
    return datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


def _build_session(*, approval_allowed: bool = True, approval_reason: str | None = None):
    profile = SimpleNamespace(
        id=uuid4(),
        paper_account_id=uuid4(),
        operating_mode="live",
        lifecycle_state="enabled",
    )
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid4(),
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=Decimal("5.00"),
        requested_amount_currency="USD",
        created_at=_now(),
        estimated_average_price=Decimal("1"),
        estimated_total_value=Decimal("5.00"),
        estimated_base_size=Decimal("5.00"),
        estimated_quote_size=Decimal("5.00"),
        readiness_verdict="READY_FOR_DRY_RUN",
        risk_verdict="approve",
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        provider="coinbase_advanced",
        environment="production",
        credentials_encrypted="{}",
        api_key_masked="********1234",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=True,
        api_permissions=["view", "trade"],
        balances=[{"currency": "USD", "available": "10.00", "reserved": "0.00", "total": "10.00"}],
        last_successful_sync_at=_now(),
        last_heartbeat_at=_now(),
        last_verified_at=_now(),
    )
    paper_account = SimpleNamespace(id=profile.paper_account_id, current_cash_balance=Decimal("100"), starting_balance=Decimal("100"))
    campaign = SimpleNamespace(id=uuid4(), uuid=uuid4(), paper_account_id=paper_account.id, status="RUNNING", starting_capital=Decimal("25"), realized_profit=Decimal("0"))
    asset = SimpleNamespace(id=uuid4(), symbol="BTC", asset_class="crypto", is_active=True, min_order_notional=Decimal("0.01"), qty_step_size=Decimal("0.00000001"), supports_fractional=True)
    global_switch = SimpleNamespace(scope="global", paper_account_id=None, engaged=False, rearm_required=False)
    account_switch = SimpleNamespace(scope="account", paper_account_id=paper_account.id, engaged=False, rearm_required=False)
    db = _FakeSession(
        profile=profile,
        preview=preview,
        connection=connection,
        paper_account=paper_account,
        campaign=campaign,
        asset=asset,
        global_switch=global_switch,
        account_switch=account_switch,
    )

    async def _approval_gate(**_kwargs):
        return SimpleNamespace(
            allowed=approval_allowed,
            reason=approval_reason,
            matched_approval_event_id=None if not approval_allowed else uuid4(),
        )

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _persist_risk(**_kwargs):
        return SimpleNamespace(risk_event_id=uuid4())

    async def _risk_rules(**_kwargs):
        return _RiskRules()

    def _risk_eval(**_kwargs):
        return SimpleNamespace(action=live_crypto_orders_service.RiskDecisionAction.APPROVE, approved_quantity=Decimal("5.00"), reason_code=None)

    return db, profile, preview, _approval_gate, _submission_guard, _persist_risk, _risk_rules, _risk_eval


@pytest.mark.asyncio
async def test_authenticated_dry_run_persists_audit_evidence_and_is_visible_in_mission_control(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _approval_gate, _submission_guard, _persist_risk, _risk_rules, _risk_eval = _build_session()

    monkeypatch.setattr(live_crypto_orders_service, "get_settings", lambda: SimpleNamespace(
        live_crypto_order_submission_enabled=False,
        live_crypto_dry_run_enabled=True,
        live_crypto_preparation_enabled=True,
        live_crypto_max_order_usd=Decimal("5"),
        live_crypto_preview_max_age_seconds=30,
        live_crypto_balance_max_age_seconds=30,
        live_crypto_readiness_max_age_seconds=60,
        live_crypto_price_max_age_seconds=30,
        live_crypto_confirmation_challenge_minutes=1,
    ))
    monkeypatch.setattr(live_crypto_orders_service, "evaluate_live_approval_gate", _approval_gate)
    monkeypatch.setattr(live_crypto_orders_service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(live_crypto_orders_service, "persist_risk_decision", _persist_risk)
    monkeypatch.setattr(live_crypto_orders_service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(live_crypto_orders_service, "evaluate_signal_risk", _risk_eval)
    monkeypatch.setattr(live_crypto_orders_service, "_utcnow", _now)

    app = create_app()

    async def override_get_db():
        yield db

    async def _run_read_with_retry(operation, *, operation_name):
        _ = operation_name
        return await operation(db)

    async def _operations_stub(*_args, **_kwargs):
        return OperationalStatusResponse(
            overall_health="green",
            run_status=OperationalRunStatusResponse(
                run_id="run-1",
                started_at=_now(),
                expected_end=_now(),
                uptime="24:00:00",
                current_phase="researching",
                health_status="green",
            ),
            system_health={
                "api": OperationalHealthIndicatorResponse(state="green", detail="API responsive"),
                "orchestrator": OperationalHealthIndicatorResponse(state="green", detail="Heartbeat active"),
                "database": OperationalHealthIndicatorResponse(state="green", detail="Database connected"),
                "research_agent": OperationalHealthIndicatorResponse(state="green", detail="Adapter available"),
            },
            research_status={"current_campaign": "Campaign Alpha", "current_champion": "RSI Mean Reversion", "campaign_status": "RUNNING"},
            monitoring=OperationalMonitoringResponse(
                candles_processed=120000,
                signals_generated=900,
                paper_trades_executed=120,
                decision_records_created=900,
                replay_count=140,
                candidate_count=80,
                campaign_count=3,
                laboratory_runs=25,
                evolution_count=44,
                current_champion="RSI Mean Reversion",
                paper_equity="104523.55",
                signals_today=42,
                trades_today=8,
                research_memory_growth=350,
            ),
            live_crypto_readiness=LiveCryptoReadinessResponse(
                ready=True,
                items=[
                    LiveCryptoReadinessItemResponse(
                        key="coinbase_production_dry_run_executed",
                        label="coinbase_spot Production Dry Run Executed",
                        ready=True,
                        detail="Latest coinbase_spot production dry run result: DRY_RUN_READY",
                    )
                ],
            ),
            alerts=[OperationalAlertResponse(code="worker_restart", severity="yellow", message="Worker restarted")],
        )

    async def _runs_stub(*_args, **_kwargs):
        return []

    async def _events_stub(*_args, **_kwargs):
        return []

    async def _campaign_metrics_stub(*_args, **_kwargs):
        return {
            "campaigns_near_profit_target": 0,
            "campaigns_at_target": 0,
            "profit_eligible_for_compounding": Decimal("0"),
            "profit_recommended_for_withdrawal": Decimal("0"),
            "profit_awaiting_review": Decimal("0"),
            "active_compounding_policies": 0,
        }

    async def _total_managed_capital_stub(*_args, **_kwargs):
        return Decimal("25.00")

    async def _timeline_stub(*_args, **_kwargs):
        return (
            "25.00",
            "0.00",
            {
                "paper_pnl_source": "bound_paper_account",
                "paper_pnl_status": "evidence_backed",
            },
        )

    async def _dashboard_stub(*_args, **_kwargs):
        return DashboardIntelligenceScoreResponse(
            score=88,
            data_completeness=100,
            range="24h",
            generated_at=_now(),
            components=[],
            timeline=[
                DashboardIntelligenceTimelinePointResponse(
                    timestamp=_now(),
                    score=88,
                    equity=Decimal("104523.55"),
                    decision_quality=88,
                    research_quality=88,
                    operational_health=88,
                )
            ],
        )

    monkeypatch.setattr("app.db.session.run_read_with_retry", _run_read_with_retry)
    monkeypatch.setattr("app.api.routes.mission_control.run_read_with_retry", _run_read_with_retry)
    monkeypatch.setattr(mission_control_service, "build_operations_status", _operations_stub)
    monkeypatch.setattr(mission_control_service, "build_dashboard_intelligence_score", _dashboard_stub)
    monkeypatch.setattr(mission_control_service, "list_validation_runs", _runs_stub)
    monkeypatch.setattr(mission_control_service, "list_validation_run_events", _events_stub)
    monkeypatch.setattr(mission_control_service, "_load_total_managed_capital", _total_managed_capital_stub)
    monkeypatch.setattr(mission_control_service, "_load_campaign_profit_metrics", _campaign_metrics_stub)
    async def _live_operation_annotations_stub(**_kwargs):
        return [
            MissionControlIntelligenceTimelineEventResponse(
                event_id="live-ops-1",
                timestamp=_now(),
                title="DRY_RUN_READY",
                description="Live operations annotation recorded: DRY_RUN_READY",
                related_validation_run=None,
                health_at_that_moment=88,
                paper_equity="104523.55",
                paper_pnl="0.00",
                signals=42,
                trades=8,
                decision_count=900,
                severity="green",
                category="system",
                event_type="DRY_RUN_READY",
                metadata={
                    "mode": "dry_run",
                    "submission_skipped": True,
                        "submission_skip_reason": "Provider order submission intentionally skipped for dry-run verification.",
                    "approval_event_id": str(uuid4()),
                    "risk_event_id": str(uuid4()),
                    "approved_quote_size": "5.00",
                    "readiness_result": "ready",
                    "kill_switch_result": "clear",
                    "product_id": "BTC-USD",
                    "failure_reason": None,
                },
            )
        ]

    monkeypatch.setattr(mission_control_service, "_load_live_operation_annotations", _live_operation_annotations_stub)
    monkeypatch.setattr(mission_control_service, "_resolve_timeline_equity_and_pnl", _timeline_stub)

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        dry_run_response = client.post(
            "/live-crypto-orders/dry-run",
            json={
                "live_trading_profile_id": str(profile.id),
                "crypto_order_preview_id": str(preview.crypto_order_preview_id),
                "operator_identity": "operator:human",
                "idempotency_token": "token-e2e-1",
            },
            headers={"Authorization": "Bearer operator:human"},
        )
        mc_response = client.get("/mission-control/intelligence?range=24h")

    assert dry_run_response.status_code == 200
    dry_run_payload = dry_run_response.json()
    live_order = dry_run_payload["live_crypto_order"]
    assert live_order["safe_provider_response"]["mode"] == "dry_run"
    assert live_order["safe_provider_response"]["submission_skipped"] is True
    assert live_order["provider_order_id"] is None
    assert live_order["submitted_at"] is None
    assert live_order["acknowledged_at"] is None
    assert live_order["product_id"] == "BTC-USD"
    assert live_order["side"] == "BUY"
    assert live_order["order_type"] == "MARKET"
    assert live_order["safe_provider_response"]["max_order_usd"] == "5"
    assert dry_run_payload["submission_skipped"] is True
    assert dry_run_payload["submission_skip_reason"].startswith("Provider order submission intentionally skipped")
    assert len(db.audit_logs) == 1

    assert mc_response.status_code == 200
    mc_payload = mc_response.json()
    matching_events = [item for item in mc_payload["timeline_events"] if item["event_type"] in {"DRY_RUN_READY", "DRY_RUN_BLOCKED"}]
    assert matching_events
    annotation = matching_events[0]
    assert annotation["metadata"]["mode"] == "dry_run"
    assert annotation["metadata"]["submission_skipped"] is True
    assert annotation["metadata"]["submission_skip_reason"].startswith("Provider order submission intentionally skipped")
    assert annotation["metadata"]["failure_reason"] is None


@pytest.mark.asyncio
async def test_authenticated_dry_run_blocked_result_surfaces_failure_reason_in_read_model(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _approval_gate, _submission_guard, _persist_risk, _risk_rules, _risk_eval = _build_session(approval_allowed=False, approval_reason="approval_checkpoint_missing")

    monkeypatch.setattr(live_crypto_orders_service, "get_settings", lambda: SimpleNamespace(
        live_crypto_order_submission_enabled=False,
        live_crypto_dry_run_enabled=True,
        live_crypto_preparation_enabled=True,
        live_crypto_max_order_usd=Decimal("5"),
        live_crypto_preview_max_age_seconds=30,
        live_crypto_balance_max_age_seconds=30,
        live_crypto_readiness_max_age_seconds=60,
        live_crypto_price_max_age_seconds=30,
        live_crypto_confirmation_challenge_minutes=1,
    ))
    monkeypatch.setattr(live_crypto_orders_service, "evaluate_live_approval_gate", _approval_gate)
    monkeypatch.setattr(live_crypto_orders_service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(live_crypto_orders_service, "persist_risk_decision", _persist_risk)
    monkeypatch.setattr(live_crypto_orders_service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(live_crypto_orders_service, "evaluate_signal_risk", _risk_eval)
    monkeypatch.setattr(live_crypto_orders_service, "_utcnow", _now)

    app = create_app()

    async def override_get_db():
        yield db

    async def _run_read_with_retry(operation, *, operation_name):
        _ = operation_name
        return await operation(db)

    async def _operations_stub(*_args, **_kwargs):
        return OperationalStatusResponse(
            overall_health="yellow",
            run_status=OperationalRunStatusResponse(
                run_id="run-1",
                started_at=_now(),
                expected_end=_now(),
                uptime="24:00:00",
                current_phase="researching",
                health_status="yellow",
            ),
            system_health={
                "api": OperationalHealthIndicatorResponse(state="green", detail="API responsive"),
                "orchestrator": OperationalHealthIndicatorResponse(state="green", detail="Heartbeat active"),
                "database": OperationalHealthIndicatorResponse(state="green", detail="Database connected"),
                "research_agent": OperationalHealthIndicatorResponse(state="green", detail="Adapter available"),
            },
            research_status={"current_campaign": "Campaign Alpha", "current_champion": "RSI Mean Reversion", "campaign_status": "RUNNING"},
            monitoring=OperationalMonitoringResponse(
                candles_processed=120000,
                signals_generated=900,
                paper_trades_executed=120,
                decision_records_created=900,
                replay_count=140,
                candidate_count=80,
                campaign_count=3,
                laboratory_runs=25,
                evolution_count=44,
                current_champion="RSI Mean Reversion",
                paper_equity="104523.55",
                signals_today=42,
                trades_today=8,
                research_memory_growth=350,
            ),
            live_crypto_readiness=LiveCryptoReadinessResponse(
                ready=False,
                items=[
                    LiveCryptoReadinessItemResponse(
                        key="coinbase_production_dry_run_executed",
                        label="coinbase_spot Production Dry Run Executed",
                        ready=False,
                        detail="Latest coinbase_spot production dry run result: DRY_RUN_BLOCKED",
                    )
                ],
            ),
            alerts=[],
        )

    async def _runs_stub(*_args, **_kwargs):
        return []

    async def _events_stub(*_args, **_kwargs):
        return []

    async def _campaign_metrics_stub(*_args, **_kwargs):
        return {
            "campaigns_near_profit_target": 0,
            "campaigns_at_target": 0,
            "profit_eligible_for_compounding": Decimal("0"),
            "profit_recommended_for_withdrawal": Decimal("0"),
            "profit_awaiting_review": Decimal("0"),
            "active_compounding_policies": 0,
        }

    async def _total_managed_capital_stub(*_args, **_kwargs):
        return Decimal("25.00")

    async def _timeline_stub(*_args, **_kwargs):
        return (
            "25.00",
            None,
            {
                "paper_pnl_source": "unavailable",
                "paper_pnl_status": "baseline_unresolved",
            },
        )

    async def _dashboard_stub(*_args, **_kwargs):
        return DashboardIntelligenceScoreResponse(
            score=71,
            data_completeness=100,
            range="24h",
            generated_at=_now(),
            components=[],
            timeline=[
                DashboardIntelligenceTimelinePointResponse(
                    timestamp=_now(),
                    score=71,
                    equity=Decimal("104523.55"),
                    decision_quality=71,
                    research_quality=71,
                    operational_health=71,
                )
            ],
        )

    monkeypatch.setattr("app.db.session.run_read_with_retry", _run_read_with_retry)
    monkeypatch.setattr("app.api.routes.mission_control.run_read_with_retry", _run_read_with_retry)
    monkeypatch.setattr(mission_control_service, "build_operations_status", _operations_stub)
    monkeypatch.setattr(mission_control_service, "build_dashboard_intelligence_score", _dashboard_stub)
    monkeypatch.setattr(mission_control_service, "list_validation_runs", _runs_stub)
    monkeypatch.setattr(mission_control_service, "list_validation_run_events", _events_stub)
    monkeypatch.setattr(mission_control_service, "_load_total_managed_capital", _total_managed_capital_stub)
    monkeypatch.setattr(mission_control_service, "_load_campaign_profit_metrics", _campaign_metrics_stub)
    async def _live_operation_annotations_stub(**_kwargs):
        return [
            MissionControlIntelligenceTimelineEventResponse(
                event_id="live-ops-1",
                timestamp=_now(),
                title="DRY_RUN_BLOCKED",
                description="Live operations annotation recorded: DRY_RUN_BLOCKED",
                related_validation_run=None,
                health_at_that_moment=71,
                paper_equity="104523.55",
                paper_pnl=None,
                signals=42,
                trades=8,
                decision_count=900,
                severity="yellow",
                category="system",
                event_type="DRY_RUN_BLOCKED",
                metadata={
                    "mode": "dry_run",
                    "submission_skipped": True,
                    "submission_skip_reason": "Provider order submission intentionally skipped for dry-run verification.",
                    "failure_reason": "approval checkpoint missing",
                },
            )
        ]

    monkeypatch.setattr(mission_control_service, "_load_live_operation_annotations", _live_operation_annotations_stub)
    monkeypatch.setattr(mission_control_service, "_resolve_timeline_equity_and_pnl", _timeline_stub)

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        dry_run_response = client.post(
            "/live-crypto-orders/dry-run",
            json={
                "live_trading_profile_id": str(profile.id),
                "crypto_order_preview_id": str(preview.crypto_order_preview_id),
                "operator_identity": "operator:human",
                "idempotency_token": "token-e2e-blocked",
            },
            headers={"Authorization": "Bearer operator:human"},
        )
        mc_response = client.get("/mission-control/intelligence?range=24h")

    assert dry_run_response.status_code == 200
    dry_run_payload = dry_run_response.json()
    assert dry_run_payload["dry_run_status"] == "DRY_RUN_BLOCKED"
    assert dry_run_payload["live_crypto_order"]["failure_reason"]
    assert len(db.audit_logs) == 1

    assert mc_response.status_code == 200
    mc_payload = mc_response.json()
    matching_events = [item for item in mc_payload["timeline_events"] if item["event_type"] in {"DRY_RUN_READY", "DRY_RUN_BLOCKED"}]
    assert matching_events
    annotation = matching_events[0]
    assert annotation["metadata"]["mode"] == "dry_run"
    assert annotation["metadata"]["submission_skipped"] is True
    assert annotation["metadata"]["failure_reason"]
    assert annotation["metadata"]["submission_skip_reason"].startswith("Provider order submission intentionally skipped")
