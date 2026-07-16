from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.core.errors import ServiceUnavailableError
from app.models.audit_log import AuditLog
from app.models.live_crypto_order import LiveCryptoOrder
from app.services import live_crypto_orders as service


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


class _DryRunFakeDb:
    def __init__(
        self,
        *,
        profile: object,
        preview: object,
        connection: object,
        approval_event: object,
        paper_account: object,
        campaign: object,
        asset: object,
        global_switch: object,
        account_switch: object,
    ) -> None:
        self.profile = profile
        self.preview = preview
        self.connection = connection
        self.approval_event = approval_event
        self.paper_account = paper_account
        self.campaign = campaign
        self.asset = asset
        self.global_switch = global_switch
        self.account_switch = account_switch
        self.live_orders: list[LiveCryptoOrder] = []
        self.audit_logs: list[AuditLog] = []
        self.commit_calls = 0

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_trading_profiles" in sql:
            profile_id = params.get("id_1")
            return self.profile if getattr(self.profile, "id", None) == profile_id else None
        if "FROM crypto_order_previews" in sql:
            preview_id = params.get("crypto_order_preview_id_1")
            return self.preview if getattr(self.preview, "crypto_order_preview_id", None) == preview_id else None
        if "FROM exchange_connections" in sql:
            connection_id = params.get("exchange_connection_id_1")
            return self.connection if getattr(self.connection, "exchange_connection_id", None) == connection_id else None
        if "FROM live_approval_events" in sql:
            approval_id = params.get("id_1")
            return self.approval_event if getattr(self.approval_event, "id", None) == approval_id else None
        if "FROM paper_accounts" in sql:
            account_id = params.get("paper_account_id_1") or params.get("id_1")
            return self.paper_account if getattr(self.paper_account, "id", None) == account_id else None
        if "FROM capital_campaigns" in sql:
            account_id = params.get("paper_account_id_1")
            if self.campaign is None:
                return None
            return self.campaign if getattr(self.campaign, "paper_account_id", None) == account_id else None
        if "FROM assets" in sql:
            symbol = params.get("symbol_1")
            return self.asset if getattr(self.asset, "symbol", None) == symbol else None
        if "FROM risk_kill_switches" in sql:
            scope = params.get("scope_1")
            account_id = params.get("paper_account_id_1")
            if scope == "global" and account_id is None:
                return self.global_switch
            if scope == "account" and getattr(self.account_switch, "paper_account_id", None) == account_id:
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
        self.commit_calls += 1


class _RiskRules:
    def __init__(self) -> None:
        self.rules = {
            "max_position_size_pct": Decimal("0.05"),
            "max_daily_loss_pct": Decimal("0.03"),
            "max_drawdown_pct": Decimal("0.15"),
        }


_APPROVAL_EVENT_ID = uuid4()


def _now() -> datetime:
    return datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)


def _base_context(
    *,
    requested_amount: Decimal = Decimal("5.00"),
    preview_offset: timedelta = timedelta(seconds=1),
    readiness_offset: timedelta = timedelta(seconds=1),
    balance_offset: timedelta = timedelta(seconds=1),
    heartbeat_offset: timedelta = timedelta(seconds=1),
    price_offset: timedelta = timedelta(seconds=1),
    credentials_valid: bool = True,
    api_permissions: list[str] | None = None,
    campaign_status: str = "RUNNING",
    global_kill_switch_engaged: bool = False,
    account_kill_switch_engaged: bool = False,
    live_trading_profile_id: UUID | None = None,
    preview_profile_id: UUID | None = None,
    profile_environment: str = "production",
    preview_environment: str = "production",
) -> tuple[_DryRunFakeDb, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    now = _now()
    profile = SimpleNamespace(
        id=live_trading_profile_id or uuid4(),
        paper_account_id=uuid4(),
        operating_mode="live",
        lifecycle_state="enabled",
        provenance_metadata={"exchange_environment": profile_environment, "registration_source": f"human_{profile_environment}_initializer"},
    )
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        live_trading_profile_id=preview_profile_id or profile.id,
        exchange_connection_id=uuid4(),
        provider="coinbase_advanced",
        environment=preview_environment,
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=requested_amount,
        requested_amount_currency="USD",
        created_at=now - preview_offset,
        estimated_average_price=Decimal("1"),
        estimated_total_value=requested_amount,
        estimated_base_size=requested_amount,
        estimated_quote_size=requested_amount,
        estimated_fee=Decimal("0.01"),
        estimated_fee_currency="USD",
        estimated_slippage=Decimal("0"),
        estimated_commission_total=Decimal("0"),
        best_bid=Decimal("1"),
        best_ask=Decimal("1"),
        readiness_verdict="READY_FOR_DRY_RUN",
        risk_verdict="approve",
        failure_reason=None,
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        provider="coinbase_advanced",
        environment=preview_environment,
        credentials_encrypted="{}",
        api_key_masked="********1234",
        api_secret_masked="********",
        passphrase_configured=True,
        credentials_valid=credentials_valid,
        api_permissions=api_permissions if api_permissions is not None else ["view", "trade"],
        balances=[{"currency": "USD", "available": "10.00", "reserved": "0.00", "total": "10.00"}],
        last_successful_sync_at=now - balance_offset,
        last_heartbeat_at=now - heartbeat_offset,
        last_verified_at=now - readiness_offset,
    )
    paper_account = SimpleNamespace(id=profile.paper_account_id, current_cash_balance=Decimal("100"), starting_balance=Decimal("100"))
    campaign = SimpleNamespace(id=uuid4(), uuid=uuid4(), paper_account_id=paper_account.id, status=campaign_status, starting_capital=Decimal("25"), realized_profit=Decimal("0"))
    asset = SimpleNamespace(id=uuid4(), symbol="BTC", asset_class="crypto", is_active=True, min_order_notional=Decimal("0.01"), qty_step_size=Decimal("0.00000001"), supports_fractional=True)
    approval_event = SimpleNamespace(id=_APPROVAL_EVENT_ID, approval_scope={"environment": preview_environment})
    global_switch = SimpleNamespace(scope="global", paper_account_id=None, engaged=global_kill_switch_engaged, rearm_required=False)
    account_switch = SimpleNamespace(scope="account", paper_account_id=paper_account.id, engaged=account_kill_switch_engaged, rearm_required=False)
    db = _DryRunFakeDb(
        profile=profile,
        preview=preview,
        connection=connection,
        approval_event=approval_event,
        paper_account=paper_account,
        campaign=campaign,
        asset=asset,
        global_switch=global_switch,
        account_switch=account_switch,
    )
    return db, profile, preview, connection


def _approve_risk(**_kwargs):
    return SimpleNamespace(action=service.RiskDecisionAction.APPROVE)


def _reject_risk(**_kwargs):
    return SimpleNamespace(action=service.RiskDecisionAction.REJECT, reason_code="risk_rejected")


async def _persist_risk(**_kwargs):
    return SimpleNamespace(risk_event_id=uuid4())


def _risk_unavailable(**_kwargs):
    raise ServiceUnavailableError("risk engine unavailable", details={"component": "risk_engine"})


def _patch_common_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _approval_gate(**kwargs):
        checkpoint_type = kwargs.get("checkpoint_type")
        if checkpoint_type == "bounded_proving_entry":
            return SimpleNamespace(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None)
        return SimpleNamespace(allowed=True, reason=None, matched_approval_event_id=_APPROVAL_EVENT_ID)

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _risk_rules(**_kwargs):
        return _RiskRules()

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
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
    monkeypatch.setattr(service, "evaluate_live_approval_gate", _approval_gate)
    monkeypatch.setattr(service, "get_risk_rules", _risk_rules)
    monkeypatch.setattr(service, "evaluate_signal_risk", lambda **_kwargs: SimpleNamespace(action=service.RiskDecisionAction.APPROVE, approved_quantity=Decimal("5.00"), reason_code=None))
    monkeypatch.setattr(service, "persist_risk_decision", _persist_risk)
    monkeypatch.setattr(service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(service, "_utcnow", _now)


@pytest.mark.asyncio
async def test_dry_run_blocks_when_live_preparation_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context()
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        live_crypto_order_submission_enabled=False,
        live_crypto_dry_run_enabled=True,
        live_crypto_preparation_enabled=False,
        live_crypto_max_order_usd=Decimal("5"),
        live_crypto_preview_max_age_seconds=30,
        live_crypto_balance_max_age_seconds=30,
        live_crypto_readiness_max_age_seconds=60,
        live_crypto_price_max_age_seconds=30,
        live_crypto_confirmation_challenge_minutes=1,
    ))
    monkeypatch.setattr(service, "_utcnow", _now)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-prep-disabled",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert response.live_crypto_order.failure_code == "dry_run_blocked"
    assert "live crypto order preparation is disabled" in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_rejects_when_dry_run_feature_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context()
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        live_crypto_order_submission_enabled=False,
        live_crypto_dry_run_enabled=False,
        live_crypto_preparation_enabled=True,
        live_crypto_max_order_usd=Decimal("5"),
        live_crypto_preview_max_age_seconds=30,
        live_crypto_balance_max_age_seconds=30,
        live_crypto_readiness_max_age_seconds=60,
        live_crypto_price_max_age_seconds=30,
        live_crypto_confirmation_challenge_minutes=1,
    ))

    with pytest.raises(PermissionError, match="dry run is disabled"):
        await service.service.dry_run(
            db=db,
            request=service.LiveCryptoOrderDryRunRequest(
                live_trading_profile_id=profile.id,
                crypto_order_preview_id=preview.crypto_order_preview_id,
                operator_identity="operator:human",
                idempotency_token="token-dry-disabled",
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "approval_reason,approval_state_name",
    [
        ("approval_checkpoint_missing", "missing"),
        ("approval_revoked", "revoked"),
        ("approval_suspended", "suspended"),
        ("approval_expired", "expired"),
    ],
    ids=["missing", "revoked", "suspended", "expired"],
)
async def test_dry_run_blocks_on_missing_revoked_suspended_or_expired_approval(
    monkeypatch: pytest.MonkeyPatch,
    approval_reason: str,
    approval_state_name: str,
) -> None:
    db, profile, preview, _ = _base_context()
    _patch_common_success(monkeypatch)

    async def _approval_gate(**_kwargs):
        return SimpleNamespace(allowed=False, reason=approval_reason, matched_approval_event_id=None)

    monkeypatch.setattr(service, "evaluate_live_approval_gate", _approval_gate)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token=f"token-approval-{approval_state_name}",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert approval_reason in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None
    assert len(db.audit_logs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name,preview_delta,readiness_delta,balance_delta,heartbeat_delta,price_delta,expected_reason",
    [
        ("stale_preview", timedelta(seconds=30), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), "preview evidence is stale"),
        ("stale_readiness", timedelta(seconds=1), timedelta(seconds=60), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), "readiness evidence is stale"),
        ("stale_balance", timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=60), timedelta(seconds=1), timedelta(seconds=1), "balance evidence is stale"),
        ("missing_evidence_timestamp", timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), "preview timestamp missing"),
        ("future_evidence_timestamp", timedelta(seconds=-1), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1), "preview timestamp is in the future"),
    ],
    ids=["stale_preview", "stale_readiness", "stale_balance", "missing_timestamp", "future_timestamp"],
)
async def test_dry_run_blocks_on_stale_missing_or_future_evidence(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    preview_delta: timedelta,
    readiness_delta: timedelta,
    balance_delta: timedelta,
    heartbeat_delta: timedelta,
    price_delta: timedelta,
    expected_reason: str,
) -> None:
    db, profile, preview, connection = _base_context(
        preview_offset=preview_delta,
        readiness_offset=readiness_delta,
        balance_offset=balance_delta,
        heartbeat_offset=heartbeat_delta,
        price_offset=price_delta,
    )
    if case_name == "missing_evidence_timestamp":
        preview.created_at = None
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token=f"token-{case_name}",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert expected_reason in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name,requested_amount,expected_reason",
    [
        ("amount_above_cap", Decimal("5.01"), "quote size exceeds live order size limit"),
        ("zero_amount", Decimal("0"), "quote size must be greater than zero"),
        ("negative_amount", Decimal("-1"), "quote size must be greater than zero"),
        ("unsupported_precision", Decimal("5.001"), "quote size exceeds supported USD precision"),
    ],
    ids=["above_cap", "zero_amount", "negative_amount", "unsupported_precision"],
)
async def test_dry_run_blocks_on_invalid_quote_amounts(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    requested_amount: Decimal,
    expected_reason: str,
) -> None:
    db, profile, preview, _ = _base_context(requested_amount=requested_amount)
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token=f"token-{case_name}",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert expected_reason in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name,credentials_valid,api_permissions,connection_delta,expected_reason",
    [
        ("missing_coinbase_credentials", False, ["view", "trade"], timedelta(seconds=1), "provider credential evidence unavailable"),
        ("missing_trade_permission", True, ["view"], timedelta(seconds=1), "trade permission missing"),
        ("exchange_connection_not_ready", True, ["view", "trade"], timedelta(seconds=60), "readiness evidence is stale"),
    ],
    ids=["missing_credentials", "missing_trade_permission", "exchange_not_ready"],
)
async def test_dry_run_blocks_on_missing_coinbase_readiness_evidence(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    credentials_valid: bool,
    api_permissions: list[str],
    connection_delta: timedelta,
    expected_reason: str,
) -> None:
    db, profile, preview, connection = _base_context(
        credentials_valid=credentials_valid,
        api_permissions=api_permissions,
        readiness_offset=connection_delta,
        heartbeat_offset=connection_delta,
    )
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token=f"token-{case_name}",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert expected_reason in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_blocks_on_preview_ownership_profile_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context(preview_profile_id=uuid4())
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-ownership-mismatch",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert "preview does not belong to the requested live trading profile" in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_rejects_sandbox_preview_with_production_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context(profile_environment="production", preview_environment="sandbox")
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-sandbox-preview-prod-profile",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert "profile environment does not match preview environment" in response.live_crypto_order.failure_reason


@pytest.mark.asyncio
async def test_dry_run_rejects_production_preview_with_sandbox_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context(profile_environment="sandbox", preview_environment="production")
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-prod-preview-sandbox-profile",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert "profile environment does not match preview environment" in response.live_crypto_order.failure_reason


@pytest.mark.asyncio
async def test_dry_run_records_sandbox_rehearsal_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context(profile_environment="sandbox", preview_environment="sandbox")
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-sandbox-metadata",
        ),
    )

    safe = response.live_crypto_order.safe_provider_response
    assert safe["exchange_environment"] == "sandbox"
    assert safe["profile_environment"] == "sandbox"
    assert safe["preview_environment"] == "sandbox"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name,global_engaged,account_engaged,expected_reason",
    [
        ("global_kill_switch_engaged", True, False, "risk engine rejected live order"),
        ("account_kill_switch_engaged", False, True, "risk engine rejected live order"),
    ],
    ids=["global_kill_switch", "account_kill_switch"],
)
async def test_dry_run_blocks_on_global_or_account_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    global_engaged: bool,
    account_engaged: bool,
    expected_reason: str,
) -> None:
    db, profile, preview, _ = _base_context(
        global_kill_switch_engaged=global_engaged,
        account_kill_switch_engaged=account_engaged,
    )
    _patch_common_success(monkeypatch)

    def _evaluate_signal_risk(*, request, **_kwargs):
        if getattr(request, "global_kill_switch_engaged_state", False) or getattr(request, "account_kill_switch_engaged_state", False):
            return SimpleNamespace(action=service.RiskDecisionAction.REJECT, reason_code="kill_switch_engaged", approved_quantity=Decimal("0"))
        return SimpleNamespace(action=service.RiskDecisionAction.APPROVE, reason_code=None, approved_quantity=Decimal("5.00"))

    monkeypatch.setattr(service, "evaluate_signal_risk", _evaluate_signal_risk)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token=f"token-{case_name}",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert expected_reason in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_blocks_when_campaign_is_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context(campaign_status="PAUSED")
    _patch_common_success(monkeypatch)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-campaign-paused",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert "linked capital campaign is paused" in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_blocks_on_risk_engine_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context()
    _patch_common_success(monkeypatch)
    monkeypatch.setattr(service, "evaluate_signal_risk", _reject_risk)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-risk-reject",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert "risk engine rejected live order" in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_blocks_on_risk_engine_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context()
    _patch_common_success(monkeypatch)
    monkeypatch.setattr(service, "evaluate_signal_risk", _risk_unavailable)

    response = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-risk-unavailable",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert "risk engine unavailable" in response.live_crypto_order.failure_reason
    assert response.live_crypto_order.provider_order_id is None
    assert response.live_crypto_order.submitted_at is None
    assert response.live_crypto_order.acknowledged_at is None


@pytest.mark.asyncio
async def test_dry_run_blocks_when_audit_persistence_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context()
    _patch_common_success(monkeypatch)

    async def _raise_audit(*_args, **_kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(service, "_record_audit", _raise_audit)

    with pytest.raises(RuntimeError, match="audit write failed"):
        await service.service.dry_run(
            db=db,
            request=service.LiveCryptoOrderDryRunRequest(
                live_trading_profile_id=profile.id,
                crypto_order_preview_id=preview.crypto_order_preview_id,
                operator_identity="operator:human",
                idempotency_token="token-audit-failure",
            ),
        )


@pytest.mark.asyncio
async def test_dry_run_replays_same_approved_intent_without_changing_client_order_id_or_duplicate_audit_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    db, profile, preview, _ = _base_context()
    _patch_common_success(monkeypatch)

    request = service.LiveCryptoOrderDryRunRequest(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=preview.crypto_order_preview_id,
        operator_identity="operator:human",
        idempotency_token="token-first",
    )
    first = await service.service.dry_run(db=db, request=request)
    second = await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-first",
        ),
    )

    assert first.dry_run_status == "DRY_RUN_READY"
    assert second.dry_run_status == "DRY_RUN_READY"
    assert first.live_crypto_order.client_order_id == second.live_crypto_order.client_order_id
    assert first.live_crypto_order.live_crypto_order_id == second.live_crypto_order.live_crypto_order_id
    assert len(db.audit_logs) == 1
    assert second.live_crypto_order.submitted_at is None
    assert second.live_crypto_order.acknowledged_at is None
    assert second.live_crypto_order.status == "DRY_RUN_READY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_name,mutation,expected_reason",
    [
        ("approved_intent_fingerprint", "intent", "approved intent fingerprint mismatch"),
        ("evidence_fingerprint", "evidence", "approval evidence fingerprint mismatch"),
    ],
    ids=["intent_drift", "evidence_drift"],
)
async def test_dry_run_replay_rejects_fingerprint_drift(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    mutation: str,
    expected_reason: str,
) -> None:
    db, profile, preview, _ = _base_context()
    _patch_common_success(monkeypatch)

    request = service.LiveCryptoOrderDryRunRequest(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=preview.crypto_order_preview_id,
        operator_identity="operator:human",
        idempotency_token="token-first",
    )
    first = await service.service.dry_run(db=db, request=request)
    assert first.dry_run_status == "DRY_RUN_READY"

    db.live_orders[0].safe_provider_response[field_name] = f"tampered-{mutation}"

    with pytest.raises(PermissionError, match=expected_reason):
        await service.service.dry_run(
            db=db,
            request=service.LiveCryptoOrderDryRunRequest(
                live_trading_profile_id=profile.id,
                crypto_order_preview_id=preview.crypto_order_preview_id,
                operator_identity="operator:human",
                idempotency_token="token-first",
            ),
        )
