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
    return SimpleNamespace(exchange_connection_id=uuid4(), created_at=datetime.now(timezone.utc))


def _profile(account_id):
    return SimpleNamespace(id=uuid4(), paper_account_id=account_id, created_at=datetime.now(timezone.utc))


def _asset():
    return SimpleNamespace(id=uuid4(), created_at=datetime.now(timezone.utc))


def _campaign(account_id):
    return SimpleNamespace(id=1, uuid=uuid4(), paper_account_id=account_id, created_at=datetime.now(timezone.utc))


def _preview(connection_id):
    return SimpleNamespace(crypto_order_preview_id=uuid4(), exchange_connection_id=connection_id, created_at=datetime.now(timezone.utc))


def _approval(profile_id):
    return SimpleNamespace(id=uuid4(), live_trading_profile_id=profile_id, expires_at=datetime.now(timezone.utc).replace(year=2030))


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

    with pytest.raises(ValueError, match="Coinbase readiness check failed"):
        await service.initialize_live_crypto_environment(
            db=db,
            request=service.InitializeLiveCryptoEnvironmentRequest(actor="operator:human"),
        )


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

    async def _approval_stub(*, db, request):
        _ = db
        captured["checkpoint_type"] = request.checkpoint_type
        captured["max_order_usd"] = request.approval_scope.get("max_order_usd")
        return SimpleNamespace(approval_event_id=uuid4(), approval_state="approved")

    monkeypatch.setattr(service, "record_live_approval_checkpoint", _approval_stub)

    result = await service.record_first_live_enablement_approval(
        db=_FakeDb(),
        request=service.RecordApprovalHelperRequest(actor="operator:human", live_trading_profile_id=uuid4()),
    )

    assert result.approval_state == "approved"
    assert captured["checkpoint_type"] == "first_live_enablement"
    assert captured["max_order_usd"] == "5"
