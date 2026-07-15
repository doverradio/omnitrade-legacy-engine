from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import live_crypto_environment as service


class _FakeDb:
    def __init__(self) -> None:
        self.paper_account = None
        self.connection = None
        self.profile = None
        self.asset = None
        self.campaign = None
        self.preview = None
        self.approval = None

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM exchange_connections" in sql:
            return self.connection
        if "FROM paper_accounts" in sql:
            return self.paper_account
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM assets" in sql:
            return self.asset
        if "FROM capital_campaigns" in sql:
            return self.campaign
        if "FROM crypto_order_previews" in sql:
            return self.preview
        if "FROM live_approval_events" in sql:
            return self.approval
        return None


def _paper_account():
    return SimpleNamespace(id=uuid4(), starting_balance=Decimal("25"), is_active=True, asset_class="crypto")


def _stock_account():
    return SimpleNamespace(id=uuid4(), starting_balance=Decimal("25"), is_active=True, asset_class="stock")


def _connection():
    return SimpleNamespace(
        exchange_connection_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        credentials_valid=True,
        last_readiness_verdict="READY_FOR_OPERATOR_REVIEW",
        last_readiness_report=[
            {"code": "usd_balance_retrieved", "status": "pass"},
            {"code": "product_btc_usd_available", "status": "pass"},
        ],
        balances=[{"currency": "USD", "available": "10.00"}],
    )


def _profile(account_id, *, environment: str = "production", provider: str = "coinbase_advanced"):
    return SimpleNamespace(
        id=uuid4(),
        paper_account_id=account_id,
        created_at=datetime.now(timezone.utc),
        provenance_metadata={"exchange_environment": environment, "registration_source": f"human_{environment}_initializer", "provider": provider},
    )


def _asset():
    return SimpleNamespace(
        id=uuid4(),
        created_at=datetime.now(timezone.utc),
        min_order_notional=Decimal("5"),
        qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
    )


def _campaign(account_id):
    return SimpleNamespace(id=1, uuid=uuid4(), paper_account_id=account_id, created_at=datetime.now(timezone.utc))


def _preview(connection_id):
    return SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        exchange_connection_id=connection_id,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc).replace(year=2030),
    )


def _approval(profile_id, *, environment: str = "production"):
    return SimpleNamespace(
        id=uuid4(),
        live_trading_profile_id=profile_id,
        expires_at=datetime.now(timezone.utc).replace(year=2030),
        approval_scope={"environment": environment, "provider": "coinbase_advanced"},
    )


@pytest.mark.asyncio
async def test_inspection_reports_missing_items(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    readiness = await service.inspect_live_crypto_environment(db=db)

    assert readiness.ready is False
    assert any(item.key == "exchange_connection" and not item.ready for item in readiness.items)
    assert any(item.key == "asset" and not item.ready for item in readiness.items)
    assert any(item.key == "live_trading_profile" and not item.ready for item in readiness.items)
    assert any(item.key == "capital_campaign" and not item.ready for item in readiness.items)


@pytest.mark.asyncio
async def test_inspection_reports_missing_exchange_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    db.profile = _profile(db.paper_account.id)
    db.asset = _asset()
    db.campaign = _campaign(db.paper_account.id)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    readiness = await service.inspect_live_crypto_environment(db=db)

    exchange_item = next(item for item in readiness.items if item.key == "exchange_connection")
    assert exchange_item.ready is False
    assert "missing" in exchange_item.detail.lower()


@pytest.mark.asyncio
async def test_inspection_reports_ready_when_all_objects_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    db.connection = _connection()
    db.profile = _profile(db.paper_account.id)
    db.asset = _asset()
    db.campaign = _campaign(db.paper_account.id)
    db.preview = _preview(db.connection.exchange_connection_id)
    db.approval = _approval(db.profile.id)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    readiness = await service.inspect_live_crypto_environment(db=db)

    assert readiness.ready is True
    assert readiness.exchange_connection_id == db.connection.exchange_connection_id
    assert readiness.live_trading_profile_id == db.profile.id


@pytest.mark.asyncio
async def test_initialize_creates_only_missing_objects_and_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()

    calls = {"exchange": 0, "asset": 0, "profile": 0, "campaign": 0}

    async def _create_exchange_connection(*, db, payload, actor):
        _ = payload, actor
        calls["exchange"] += 1
        db.connection = _connection()
        return SimpleNamespace(exchange_connection_id=db.connection.exchange_connection_id)

    async def _ensure_asset(*, db, request):
        _ = request
        created = db.asset is None
        if created:
            db.asset = _asset()
        calls["asset"] += 1
        return SimpleNamespace(asset=db.asset, created=created)

    async def _register_live_account(*, db, request):
        _ = request
        calls["profile"] += 1
        db.profile = _profile(db.paper_account.id)
        return SimpleNamespace(accepted=True, rejection_reason=None)

    async def _create_capital_campaign(*, db, request):
        _ = request
        calls["campaign"] += 1
        db.campaign = _campaign(db.paper_account.id)
        return SimpleNamespace(id=db.campaign.id)

    async def _refresh_exchange_balances(*, db, exchange_connection_id, actor):
        _ = db, exchange_connection_id, actor
        return SimpleNamespace(readiness=SimpleNamespace(verdict="READY_FOR_DRY_RUN"))

    monkeypatch.setattr(service, "create_exchange_connection", _create_exchange_connection)
    monkeypatch.setattr(service, "ensure_coinbase_crypto_asset", _ensure_asset)
    monkeypatch.setattr(service, "register_live_account", _register_live_account)
    monkeypatch.setattr(service, "create_capital_campaign", _create_capital_campaign)
    monkeypatch.setattr(service, "refresh_exchange_balances", _refresh_exchange_balances)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    request = service.InitializeLiveCryptoEnvironmentRequest(
        actor="operator:human",
        exchange_api_key_name="key",
        exchange_private_key="secret",
    )
    first = await service.initialize_live_crypto_environment(db=db, request=request)
    second = await service.initialize_live_crypto_environment(db=db, request=request)

    assert first.created_exchange_connection is True
    assert first.created_live_trading_profile is True
    assert first.created_capital_campaign is True
    assert second.created_exchange_connection is False
    assert second.created_live_trading_profile is False
    assert second.created_capital_campaign is False
    assert calls["exchange"] == 1
    assert calls["profile"] == 1
    assert calls["campaign"] == 1


@pytest.mark.asyncio
async def test_partial_initialization_creates_only_missing_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    db.connection = _connection()
    db.asset = _asset()
    db.profile = _profile(db.paper_account.id)

    calls = {"exchange": 0, "asset": 0, "profile": 0, "campaign": 0}

    async def _create_exchange_connection(*, db, payload, actor):
        _ = db, payload, actor
        calls["exchange"] += 1
        return SimpleNamespace()

    async def _ensure_asset(*, db, request):
        _ = db, request
        calls["asset"] += 1
        return SimpleNamespace(asset=_asset(), created=False)

    async def _register_live_account(*, db, request):
        _ = db, request
        calls["profile"] += 1
        return SimpleNamespace(accepted=True, rejection_reason=None)

    async def _create_capital_campaign(*, db, request):
        _ = request
        calls["campaign"] += 1
        db.campaign = _campaign(db.paper_account.id)
        return SimpleNamespace(id=db.campaign.id)

    async def _refresh_exchange_balances(*, db, exchange_connection_id, actor):
        _ = db, exchange_connection_id, actor
        return SimpleNamespace(readiness=SimpleNamespace(verdict="READY_FOR_DRY_RUN"))

    monkeypatch.setattr(service, "create_exchange_connection", _create_exchange_connection)
    monkeypatch.setattr(service, "ensure_coinbase_crypto_asset", _ensure_asset)
    monkeypatch.setattr(service, "register_live_account", _register_live_account)
    monkeypatch.setattr(service, "create_capital_campaign", _create_capital_campaign)
    monkeypatch.setattr(service, "refresh_exchange_balances", _refresh_exchange_balances)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    await service.initialize_live_crypto_environment(
        db=db,
        request=service.InitializeLiveCryptoEnvironmentRequest(
            actor="operator:human",
            exchange_api_key_name="key",
            exchange_private_key="secret",
        ),
    )

    assert calls["exchange"] == 0
    assert calls["profile"] == 0
    assert calls["campaign"] == 1


@pytest.mark.asyncio
async def test_initialize_fails_closed_when_credentials_missing_for_missing_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    with pytest.raises(ValueError, match="Coinbase credentials are required"):
        await service.initialize_live_crypto_environment(
            db=db,
            request=service.InitializeLiveCryptoEnvironmentRequest(actor="operator:human"),
        )


@pytest.mark.asyncio
async def test_initialize_fails_closed_when_exchange_readiness_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    db.connection = _connection()

    async def _ensure_asset(*, db, request):
        _ = request
        if db.asset is None:
            db.asset = _asset()
        return SimpleNamespace(asset=db.asset, created=True)

    async def _refresh_exchange_balances(*, db, exchange_connection_id, actor):
        _ = db, exchange_connection_id, actor
        return SimpleNamespace(readiness=SimpleNamespace(verdict="PERMISSION_BLOCKED"))

    monkeypatch.setattr(service, "ensure_coinbase_crypto_asset", _ensure_asset)
    monkeypatch.setattr(service, "refresh_exchange_balances", _refresh_exchange_balances)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    with pytest.raises(ValueError, match="Coinbase readiness check failed") as exc_info:
        await service.initialize_live_crypto_environment(
            db=db,
            request=service.InitializeLiveCryptoEnvironmentRequest(actor="operator:human"),
        )

    message = str(exc_info.value)
    assert "readiness_details=" in message
    assert '"verdict":"PERMISSION_BLOCKED"' in message
    assert '"reason_codes"' in message


@pytest.mark.asyncio
async def test_initialize_accepts_kraken_initialized_but_unfunded(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    db.connection = _connection()

    async def _ensure_asset(*, db, request):
        _ = request
        if db.asset is None:
            db.asset = _asset()
        return SimpleNamespace(asset=db.asset, created=True)

    async def _register_live_account(*, db, request):
        _ = request
        db.profile = _profile(db.paper_account.id, provider="kraken_spot")
        return SimpleNamespace(accepted=True, rejection_reason=None)

    async def _create_capital_campaign(*, db, request):
        _ = request
        db.campaign = _campaign(db.paper_account.id)
        return SimpleNamespace(id=db.campaign.id)

    async def _refresh_exchange_balances(*, db, exchange_connection_id, actor):
        _ = db, exchange_connection_id, actor
        return SimpleNamespace(readiness=SimpleNamespace(verdict="INITIALIZED_BUT_UNFUNDED"))

    monkeypatch.setattr(service, "ensure_coinbase_crypto_asset", _ensure_asset)
    monkeypatch.setattr(service, "register_live_account", _register_live_account)
    monkeypatch.setattr(service, "create_capital_campaign", _create_capital_campaign)
    monkeypatch.setattr(service, "refresh_exchange_balances", _refresh_exchange_balances)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    result = await service.initialize_live_crypto_environment(
        db=db,
        request=service.InitializeLiveCryptoEnvironmentRequest(
            actor="operator:human",
            provider="kraken_spot",
        ),
    )

    assert result.created_asset is True
    assert result.created_live_trading_profile is True
    assert result.created_capital_campaign is True


@pytest.mark.asyncio
async def test_initialize_retry_after_readiness_failure_is_idempotent_for_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()

    calls = {"exchange": 0, "refresh": 0}

    async def _create_exchange_connection(*, db, payload, actor):
        _ = payload, actor
        calls["exchange"] += 1
        db.connection = _connection()
        return SimpleNamespace(exchange_connection_id=db.connection.exchange_connection_id)

    async def _refresh_exchange_balances(*, db, exchange_connection_id, actor):
        _ = db, exchange_connection_id, actor
        calls["refresh"] += 1
        return SimpleNamespace(readiness=SimpleNamespace(verdict="PERMISSION_BLOCKED"))

    monkeypatch.setattr(service, "create_exchange_connection", _create_exchange_connection)
    monkeypatch.setattr(service, "refresh_exchange_balances", _refresh_exchange_balances)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    request = service.InitializeLiveCryptoEnvironmentRequest(
        actor="operator:human",
        provider="kraken_spot",
        exchange_api_key_name="key",
        exchange_private_key="secret",
    )

    with pytest.raises(ValueError, match="Provider readiness check failed"):
        await service.initialize_live_crypto_environment(db=db, request=request)
    with pytest.raises(ValueError, match="Provider readiness check failed"):
        await service.initialize_live_crypto_environment(db=db, request=request)

    assert calls["exchange"] == 1
    assert calls["refresh"] == 2
    assert db.profile is None
    assert db.campaign is None


@pytest.mark.asyncio
async def test_initialize_rejects_non_crypto_paper_account(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _stock_account()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    with pytest.raises(ValueError, match="Selected paper account is not crypto"):
        await service.inspect_live_crypto_environment(db=db)


@pytest.mark.asyncio
async def test_preview_helper_uses_exact_btc_usd_buy_five_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _create_preview_stub(*, db, request, actor):
        _ = db, actor
        captured["product_id"] = request.product_id
        captured["side"] = request.side
        captured["quote_size"] = request.quote_size
        return SimpleNamespace(crypto_order_preview_id=uuid4(), status="PREVIEW_READY")

    monkeypatch.setattr(service, "create_crypto_order_preview", _create_preview_stub)

    result = await service.generate_fresh_btc_dry_run_preview(
        db=_FakeDb(),
        request=service.GeneratePreviewHelperRequest(actor="operator:human", exchange_connection_id=uuid4()),
    )

    assert result.status == "PREVIEW_READY"
    assert captured["product_id"] == "BTC-USD"
    assert captured["side"] == "BUY"
    assert captured["quote_size"] == Decimal("5")


@pytest.mark.asyncio
async def test_approval_helper_uses_first_live_enablement_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    db = _FakeDb()
    db.profile = _profile(uuid4())
    db.campaign = _campaign(db.profile.paper_account_id)
    db.campaign.definition_version = 1
    db.campaign.starting_capital = Decimal("25")
    preview_id = uuid4()

    async def _approval_stub(*, db, request):
        _ = db
        captured["checkpoint_type"] = request.checkpoint_type
        captured["max_order_usd"] = request.approval_scope.get("max_order_usd")
        captured["provider"] = request.approval_scope.get("provider")
        captured["capital_campaign_id"] = request.approval_scope.get("capital_campaign_id")
        captured["strategy_version"] = request.approval_scope.get("strategy_version")
        captured["parameter_set_version"] = request.approval_scope.get("parameter_set_version")
        captured["crypto_order_preview_id"] = request.approval_scope.get("crypto_order_preview_id")
        return SimpleNamespace(approval_event_id=uuid4(), approval_state="approved")

    async def _preview_identity_stub(*, db, preview_id):
        _ = db, preview_id
        return {
            "crypto_order_preview_id": str(preview_id),
            "decision_record_id": str(uuid4()),
            "strategy_version": "ma_crossover@1.0.0",
            "parameter_set_version": "param-set-v1",
        }

    monkeypatch.setattr(service, "record_live_approval_checkpoint", _approval_stub)
    monkeypatch.setattr(service, "_load_preview_decision_identity", _preview_identity_stub)

    result = await service.record_first_live_enablement_approval(
        db=db,
        request=service.RecordApprovalHelperRequest(
            actor="operator:human",
            live_trading_profile_id=db.profile.id,
            crypto_order_preview_id=preview_id,
        ),
    )

    assert result.approval_state == "approved"
    assert captured["checkpoint_type"] == "first_live_enablement"
    assert captured["max_order_usd"] == "5"
    assert captured["provider"] == "coinbase_advanced"
    assert captured["capital_campaign_id"] == str(db.campaign.uuid)
    assert captured["strategy_version"] == "ma_crossover@1.0.0"
    assert captured["parameter_set_version"] == "param-set-v1"
    assert captured["crypto_order_preview_id"] == str(preview_id)


@pytest.mark.asyncio
async def test_environment_separation_for_asset_and_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()

    async def _load_asset_for_exchange(*, db, exchange):
        _ = db
        if exchange == "coinbase_advanced_sandbox":
            return _asset()
        return None

    async def _load_campaign_for_exchange(*, db, paper_account_id, exchange):
        _ = db, paper_account_id
        if exchange == "coinbase_advanced_sandbox":
            return _campaign(uuid4())
        return None

    monkeypatch.setattr(service, "_load_coinbase_btc_asset_for_exchange", _load_asset_for_exchange)
    monkeypatch.setattr(service, "_load_campaign_for_account_exchange", _load_campaign_for_exchange)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    production = await service.inspect_live_crypto_environment(db=db, exchange_environment="production")
    sandbox = await service.inspect_live_crypto_environment(db=db, exchange_environment="sandbox")

    production_asset = next(item for item in production.items if item.key == "asset")
    sandbox_asset = next(item for item in sandbox.items if item.key == "asset")
    assert production_asset.ready is False
    assert sandbox_asset.ready is True


@pytest.mark.asyncio
async def test_environment_separation_for_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()

    async def _load_connection(*, db, environment):
        _ = db
        return _connection() if environment == "sandbox" else None

    monkeypatch.setattr(service, "_load_coinbase_connection", _load_connection)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    production = await service.inspect_live_crypto_environment(db=db, exchange_environment="production")
    sandbox = await service.inspect_live_crypto_environment(db=db, exchange_environment="sandbox")

    assert next(item for item in production.items if item.key == "exchange_connection").ready is False
    assert next(item for item in sandbox.items if item.key == "exchange_connection").ready is True


@pytest.mark.asyncio
async def test_environment_separation_for_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()

    async def _load_campaign_for_exchange(*, db, paper_account_id, exchange):
        _ = db, paper_account_id
        if exchange == "coinbase_advanced_sandbox":
            return _campaign(uuid4())
        return None

    monkeypatch.setattr(service, "_load_campaign_for_account_exchange", _load_campaign_for_exchange)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    production = await service.inspect_live_crypto_environment(db=db, exchange_environment="production")
    sandbox = await service.inspect_live_crypto_environment(db=db, exchange_environment="sandbox")

    assert next(item for item in production.items if item.key == "capital_campaign").ready is False
    assert next(item for item in sandbox.items if item.key == "capital_campaign").ready is True


@pytest.mark.asyncio
async def test_preview_helper_uses_requested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _create_preview_stub(*, db, request, actor):
        _ = db, actor
        captured["environment"] = request.environment
        return SimpleNamespace(crypto_order_preview_id=uuid4(), status="PREVIEW_READY")

    monkeypatch.setattr(service, "create_crypto_order_preview", _create_preview_stub)

    await service.generate_fresh_btc_dry_run_preview(
        db=_FakeDb(),
        request=service.GeneratePreviewHelperRequest(
            actor="operator:human",
            exchange_connection_id=uuid4(),
            exchange_environment="sandbox",
        ),
    )

    assert captured["environment"] == "sandbox"


@pytest.mark.asyncio
async def test_approval_helper_scopes_requested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    db = _FakeDb()
    db.profile = _profile(uuid4(), environment="sandbox", provider="kraken_spot")

    async def _approval_stub(*, db, request):
        _ = db
        captured["environment"] = request.approval_scope.get("environment")
        captured["provider"] = request.approval_scope.get("provider")
        return SimpleNamespace(approval_event_id=uuid4(), approval_state="approved")

    monkeypatch.setattr(service, "record_live_approval_checkpoint", _approval_stub)

    await service.record_first_live_enablement_approval(
        db=db,
        request=service.RecordApprovalHelperRequest(
            actor="operator:human",
            live_trading_profile_id=db.profile.id,
            provider="kraken_spot",
            exchange_environment="sandbox",
        ),
    )

    assert captured["environment"] == "sandbox"
    assert captured["provider"] == "kraken_spot"


@pytest.mark.asyncio
async def test_inspection_does_not_reuse_production_profile_for_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.paper_account = _paper_account()
    db.profile = _profile(db.paper_account.id, environment="production")

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=Decimal("5"),
        ),
    )

    readiness = await service.inspect_live_crypto_environment(db=db, exchange_environment="sandbox")

    profile_item = next(item for item in readiness.items if item.key == "live_trading_profile")
    assert profile_item.ready is False


@pytest.mark.asyncio
async def test_approval_helper_rejects_profile_environment_mismatch() -> None:
    db = _FakeDb()
    db.profile = _profile(uuid4(), environment="production")

    with pytest.raises(ValueError, match="environment mismatch"):
        await service.record_first_live_enablement_approval(
            db=db,
            request=service.RecordApprovalHelperRequest(
                actor="operator:human",
                live_trading_profile_id=db.profile.id,
                exchange_environment="sandbox",
            ),
        )


@pytest.mark.asyncio
async def test_approval_helper_rejects_profile_provider_mismatch() -> None:
    db = _FakeDb()
    db.profile = _profile(uuid4(), environment="production", provider="coinbase_advanced")

    with pytest.raises(ValueError, match="provider mismatch"):
        await service.record_first_live_enablement_approval(
            db=db,
            request=service.RecordApprovalHelperRequest(
                actor="operator:human",
                live_trading_profile_id=db.profile.id,
                provider="kraken_spot",
                exchange_environment="production",
            ),
        )


@pytest.mark.asyncio
async def test_rehearsal_rerun_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    readiness = SimpleNamespace(
        ready=True,
        exchange_connection_id=uuid4(),
        live_trading_profile_id=uuid4(),
        paper_account_id=uuid4(),
        capital_campaign_id=1,
        crypto_order_preview_id=uuid4(),
        approval_event_id=uuid4(),
        items=(),
    )
    dry_run_response = SimpleNamespace(
        live_crypto_order=SimpleNamespace(live_crypto_order_id=uuid4(), audit_correlation_id=uuid4()),
        dry_run_status="DRY_RUN_READY",
    )

    async def _initialize_stub(**_kwargs):
        return None

    async def _inspect_stub(**kwargs):
        _ = kwargs
        return readiness if kwargs.get("exchange_environment") == "sandbox" else SimpleNamespace(ready=False)

    async def _review_stub(**_kwargs):
        return SimpleNamespace(passed=True, checks=[1, 2, 3])

    async def _dry_run_stub(**_kwargs):
        return dry_run_response

    monkeypatch.setattr(service, "initialize_live_crypto_environment", _initialize_stub)
    monkeypatch.setattr(service, "inspect_live_crypto_environment", _inspect_stub)
    monkeypatch.setattr(service.live_crypto_orders_service.service, "dry_run", _dry_run_stub)

    result = await service.run_live_crypto_rehearsal(
        db=_FakeDb(),
        request=service.InitializeLiveCryptoEnvironmentRequest(
            actor="operator:human",
            exchange_environment="sandbox",
            registration_source="human_sandbox_initializer",
        ),
        verify_rehearsal_evidence=_review_stub,
    )

    assert result.preview_created is False
    assert result.approval_created is False
    assert result.review_passed is True
    assert result.production_ready is False
