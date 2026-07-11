from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.risk_event import RiskEvent
from app.services import mission_control_intelligence as mission_control_service
from scripts import review_live_crypto_dry_run_evidence as script


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


class _FakeDb:
    def __init__(
        self,
        *,
        live_order: LiveCryptoOrder,
        preview: CryptoOrderPreview,
        profile: LiveTradingProfile,
        approval_event: LiveApprovalEvent | None,
        risk_event: RiskEvent | None,
        campaign: CapitalCampaign | None,
        live_accounting_count: int = 0,
        reconciliation_count: int = 0,
        capital_audit_count: int = 0,
        profit_cycle_count: int = 0,
    ) -> None:
        self.live_order = live_order
        self.preview = preview
        self.profile = profile
        self.approval_event = approval_event
        self.risk_event = risk_event
        self.campaign = campaign
        self.live_accounting_count = live_accounting_count
        self.reconciliation_count = reconciliation_count
        self.capital_audit_count = capital_audit_count
        self.profit_cycle_count = profit_cycle_count

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM live_crypto_orders" in sql:
            return self.live_order
        if "FROM crypto_order_previews" in sql:
            return self.preview
        if "FROM exchange_connections" in sql:
            return SimpleNamespace(environment=self.preview.environment)
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM live_approval_events" in sql:
            return self.approval_event
        if "FROM risk_events" in sql:
            return self.risk_event
        if "FROM capital_campaigns" in sql:
            return self.campaign
        if "FROM live_accounting_records" in sql:
            return self.live_accounting_count
        if "FROM live_reconciliation_events" in sql:
            return self.reconciliation_count
        if "FROM audit_log" in sql:
            return self.capital_audit_count
        if "FROM capital_campaign_profit_cycles" in sql:
            return self.profit_cycle_count
        return None

    async def execute(self, _statement, *_args, **_kwargs):
        return _ExecuteResult([])


class _AsyncSessionFactory:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Settings:
    live_crypto_order_submission_enabled = False
    live_crypto_preview_max_age_seconds = 30
    live_crypto_readiness_max_age_seconds = 60
    live_crypto_balance_max_age_seconds = 30
    live_crypto_price_max_age_seconds = 30


def _now() -> datetime:
    return datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _build_success_case(*, approval_event: bool = True, risk_event: bool = True) -> tuple[_FakeDb, UUID, UUID]:
    profile = LiveTradingProfile(
        id=uuid4(),
        paper_account_id=uuid4(),
        operating_mode="live",
        lifecycle_state="enabled",
        approval_state="approved",
        live_opt_in=True,
        human_approval_recorded=True,
        paper_default_mode=True,
        governance_approved=True,
        risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={"exchange_environment": "sandbox", "registration_source": "human_sandbox_initializer"},
    )
    preview = CryptoOrderPreview(
        crypto_order_preview_id=uuid4(),
        idempotency_key="preview-key",
        preview_version=1,
        refreshed_from_preview_id=None,
        exchange_connection_id=uuid4(),
        provider="coinbase_advanced",
        environment="sandbox",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        quote_size=Decimal("5.00"),
        base_size=Decimal("0.0001"),
        requested_amount=Decimal("5.00"),
        requested_amount_currency="USD",
        status="READY_FOR_DRY_RUN",
        readiness_verdict="READY_FOR_DRY_RUN",
        risk_event_id=uuid4() if risk_event else None,
        decision_record_id=uuid4(),
        validation_run_id=uuid4(),
        strategy_id=None,
        strategy_name=None,
        preview_id="preview-1",
        estimated_average_price=Decimal("50000"),
        estimated_total_value=Decimal("5.00"),
        estimated_base_size=Decimal("0.0001"),
        estimated_quote_size=Decimal("5.00"),
        estimated_fee=Decimal("0.01"),
        estimated_fee_currency="USD",
        estimated_slippage=Decimal("0"),
        estimated_commission_total=Decimal("0"),
        best_bid=Decimal("49999"),
        best_ask=Decimal("50001"),
        available_balance_before=Decimal("10"),
        estimated_balance_after=Decimal("5"),
        risk_verdict="approve",
        risk_explanation=None,
        failure_reason=None,
        warning_messages=[],
        exchange_response_summary={},
        expires_at=_now(),
        generated_by="system",
        audit_correlation_id=None,
    )
    safe_provider_response = {
        "mode": "dry_run",
        "submission_skipped": True,
        "submission_skip_reason": "Provider order submission intentionally skipped (LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false, LIVE_CRYPTO_DRY_RUN_ENABLED=true)",
        "exchange_environment": "sandbox",
        "provider_mock_mode_enabled": True,
        "rehearsal_mode": "controlled_provider_mock",
        "approval_event_id": str(uuid4()) if approval_event else None,
        "risk_event_id": str(preview.risk_event_id) if preview.risk_event_id else None,
        "approved_intent_fingerprint": "intent-fingerprint",
        "evidence_fingerprint": "evidence-fingerprint",
        "preview_age_seconds": 1,
        "readiness_age_seconds": 1,
        "heartbeat_age_seconds": 1,
        "balance_age_seconds": 1,
        "price_age_seconds": 1,
    }
    live_order = LiveCryptoOrder(
        crypto_order_preview_id=preview.crypto_order_preview_id,
        exchange_connection_id=preview.exchange_connection_id,
        provider=preview.provider,
        environment=preview.environment,
        product_id=preview.product_id,
        side=preview.side,
        order_type=preview.order_type,
        requested_quote_size=Decimal("5.00"),
        client_order_id="client-order-id",
        status="DRY_RUN_READY",
        risk_event_id=preview.risk_event_id,
        decision_record_id=preview.decision_record_id,
        validation_run_id=preview.validation_run_id,
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response=safe_provider_response,
        audit_correlation_id=uuid4(),
        operator_confirmation_id=None,
        created_at=_now(),
        updated_at=_now(),
    )
    approval = None
    if approval_event:
        approval = LiveApprovalEvent(
            id=UUID(safe_provider_response["approval_event_id"]),
            idempotency_key="approval-key",
            event_hash="approval-hash",
            live_trading_profile_id=profile.id,
            sequence_number=1,
            event_type="approval_granted",
            checkpoint_type="first_live_enablement",
            approval_state="approved",
            approver_id="operator:human",
            approver_role="operator",
            rationale="approved",
            approval_scope={"environment": "sandbox", "provider": "coinbase_advanced"},
            expires_at=_now(),
            renewal_condition=None,
            event_payload={},
            provenance={},
            immutable_contract_version="1",
            recorded_at=_now(),
            created_at=_now(),
        )
    risk = None
    if risk_event:
        risk = RiskEvent(
            id=preview.risk_event_id,
            paper_account_id=profile.paper_account_id,
            related_signal_id=preview.crypto_order_preview_id,
            event_type="approval",
            action_taken="approved",
            detail={},
            created_at=_now(),
        )
    campaign = CapitalCampaign(
        id=1,
        uuid=uuid4(),
        owner="owner",
        name="Campaign A",
        status="RUNNING",
        campaign_type="live",
        exchange="coinbase",
        paper_account_id=profile.paper_account_id,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal("25"),
        current_equity=Decimal("25"),
        realized_profit=Decimal("0"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        roi=Decimal("0"),
        created_at=_now(),
        updated_at=_now(),
    )
    db = _FakeDb(
        live_order=live_order,
        preview=preview,
        profile=profile,
        approval_event=approval,
        risk_event=risk,
        campaign=campaign,
    )
    return db, live_order.live_crypto_order_id, live_order.audit_correlation_id


@pytest.mark.asyncio
async def test_review_helper_passes_for_clean_dry_run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db, live_order_id, audit_correlation_id = _build_success_case()
    monkeypatch.setattr(script, "AsyncSessionLocal", lambda: _AsyncSessionFactory(db))
    monkeypatch.setattr(script, "get_settings", lambda: _Settings())

    async def _mission_control_stub(**_kwargs):
        return SimpleNamespace(
            timeline_events=[SimpleNamespace(event_type="DRY_RUN_READY", metadata={"mode": "dry_run", "environment": "sandbox"})],
            operations=SimpleNamespace(
                live_crypto_readiness=SimpleNamespace(
                    items=[
                        SimpleNamespace(key="sandbox_exchange_connection", ready=True),
                        SimpleNamespace(key="production_account_status", ready=False),
                    ]
                )
            ),
        )

    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", _mission_control_stub)
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is True
    assert any(check.name == "mission_control_annotation_present" and check.passed for check in report.checks)

    result = await script._run_review(SimpleNamespace(live_crypto_order_id=live_order_id, audit_correlation_id=None, mission_control_range="24h", expected_environment="sandbox"))
    assert result == 0
    captured = capsys.readouterr().out
    assert "PASS mode" in captured
    assert "review_summary=PASS" in captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator, expected_check",
    [
        (lambda order: setattr(order, "provider_order_id", "provider-123"), "provider_order_id_absent"),
        (lambda order: setattr(order, "submitted_at", _now()), "submitted_at_absent"),
        (lambda order: setattr(order, "filled_at", _now()), "filled_at_absent"),
    ],
)
async def test_review_helper_fails_on_contradictory_provider_evidence(monkeypatch: pytest.MonkeyPatch, mutator, expected_check: str) -> None:
    db, live_order_id, _ = _build_success_case()
    mutator(db.live_order)
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    failing = next(check for check in report.checks if check.name == expected_check)
    assert failing.passed is False


@pytest.mark.asyncio
async def test_review_helper_fails_when_accounting_row_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    db, live_order_id, _ = _build_success_case()
    db.live_accounting_count = 1
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    assert any(check.name == "live_accounting_absent" and not check.passed for check in report.checks)


@pytest.mark.asyncio
async def test_review_helper_fails_when_mission_control_annotation_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    db, live_order_id, _ = _build_success_case()
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    assert any(check.name == "mission_control_annotation_present" and not check.passed for check in report.checks)


@pytest.mark.asyncio
async def test_review_helper_fails_when_campaign_mutation_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    db, live_order_id, _ = _build_success_case()
    db.capital_audit_count = 1
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    assert any(check.name == "capital_mutation_absent" and not check.passed for check in report.checks)


@pytest.mark.asyncio
async def test_review_helper_fails_when_profit_cycle_mutation_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    db, live_order_id, _ = _build_success_case()
    db.profit_cycle_count = 1
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    assert any(check.name == "profit_cycle_mutation_absent" and not check.passed for check in report.checks)


@pytest.mark.asyncio
async def test_review_helper_rejects_environment_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    db, live_order_id, _ = _build_success_case()
    db.preview.environment = "production"
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    assert any(check.name == "preview_environment_matches" and not check.passed for check in report.checks)


@pytest.mark.asyncio
async def test_review_helper_rejects_fabricated_production_provider_id(monkeypatch: pytest.MonkeyPatch) -> None:
    db, live_order_id, _ = _build_success_case()
    db.live_order.provider_order_id = "prod-order-123"
    monkeypatch.setattr(mission_control_service, "build_mission_control_intelligence", lambda **_kwargs: SimpleNamespace(timeline_events=[], operations=SimpleNamespace(live_crypto_readiness=SimpleNamespace(items=[]))))
    monkeypatch.setattr(script, "inspect_live_crypto_environment", lambda **_kwargs: SimpleNamespace(ready=False))

    report = await script.verify_dry_run_evidence(
        db=db,
        live_crypto_order_id=live_order_id,
        audit_correlation_id=None,
        mission_control_range="24h",
        expected_environment="sandbox",
    )

    assert report.passed is False
    assert any(check.name == "provider_order_id_absent" and not check.passed for check in report.checks)


def test_review_helper_parse_args_requires_exactly_one_identifier() -> None:
    args = script.parse_args(["--live-crypto-order-id", "11111111-1111-1111-1111-111111111111"])
    assert str(args.live_crypto_order_id) == "11111111-1111-1111-1111-111111111111"
    assert args.audit_correlation_id is None
    assert args.mission_control_range == "24h"
    assert args.expected_environment == "production"

    args = script.parse_args(["--audit-correlation-id", "22222222-2222-2222-2222-222222222222", "--mission-control-range", "72h"])
    assert args.live_crypto_order_id is None
    assert str(args.audit_correlation_id) == "22222222-2222-2222-2222-222222222222"
    assert args.mission_control_range == "72h"

    with pytest.raises(SystemExit):
        script.parse_args([])
