from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import live_crypto_orders as service
from app.services.live.contracts import LiveApprovalGateResult


class _FakeDb:
    def __init__(self, *, profile, preview, approval_event=None, activation=None, package=None, connection=None) -> None:
        self.profile = profile
        self.preview = preview
        self.approval_event = approval_event
        self.activation = activation
        self.package = package
        self.connection = connection

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM crypto_order_previews" in sql:
            return self.preview
        if "FROM exchange_connections" in sql:
            return self.connection
        if "FROM live_approval_events" in sql:
            if params.get("id_1") is not None:
                return self.approval_event if getattr(self.approval_event, "id", None) == params["id_1"] else None
            if params.get("live_trading_profile_id_1") is not None:
                return self.approval_event if getattr(self.approval_event, "live_trading_profile_id", None) == params["live_trading_profile_id_1"] else None
        if "FROM canonical_proving_activations" in sql:
            return self.activation
        if "FROM canonical_preview_packages" in sql:
            return self.package
        if "FROM capital_campaigns" in sql:
            return SimpleNamespace(uuid=uuid4(), definition_version=1, paper_account_id=self.profile.paper_account_id)
        if "FROM decision_snapshots" in sql:
            return SimpleNamespace(decision_id=self.preview.decision_record_id, strategy_version="v1", parameter_set_version="baseline")
        return None

    async def scalars(self, _statement):
        return []

    def add(self, _item):
        return None

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_canonical_activation_missing_blocks_buy_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid4(), paper_account_id=uuid4(), provenance_metadata={"exchange_environment": "production", "provider": "kraken_spot"})
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        exchange_connection_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=Decimal("3"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        decision_record_id=uuid4(),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        provider="kraken_spot",
        environment="production",
        credentials_valid=True,
        api_permissions=["funds_query", "open_order_query", "closed_order_query", "ledger_query"],
        last_verified_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
        last_successful_sync_at=datetime.now(timezone.utc),
        balances=[{"currency": "USD", "available": "10"}],
    )
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_preparation_enabled=True,
            live_crypto_order_submission_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_balance_max_age_seconds=60,
            live_crypto_price_max_age_seconds=60,
        ),
    )
    approval_event = SimpleNamespace(
        id=uuid4(),
        live_trading_profile_id=profile.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        approval_scope={
            "canonical_preview_package_id": str(uuid4()),
            "live_trading_profile_id": str(profile.id),
            "paper_account_id": str(profile.paper_account_id),
            "product": preview.product_id,
            "side": preview.side,
            "provider": preview.provider,
            "environment": preview.environment,
        },
        approval_state="approved",
    )
    db = _FakeDb(profile=profile, preview=preview, approval_event=approval_event, activation=None, package=None, connection=connection)

    async def _canonical_gate(**_kwargs):
        return LiveApprovalGateResult(allowed=True, reason=None, matched_approval_event_id=approval_event.id)

    async def _legacy_gate(**_kwargs):
        return LiveApprovalGateResult(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None)

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _risk_context(**_kwargs):
        raise AssertionError("risk context should not be reached when activation is missing")

    async def _campaign(*_args, **_kwargs):
        return SimpleNamespace(uuid=uuid4(), definition_version=1)

    async def _preview_identity(*_args, **_kwargs):
        return {"strategy_version": "v1", "parameter_set_version": "baseline"}

    monkeypatch.setattr(service, "evaluate_live_approval_gate", lambda **kwargs: _canonical_gate(**kwargs) if kwargs.get("checkpoint_type") == "bounded_proving_entry" else _legacy_gate(**kwargs))
    monkeypatch.setattr(service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)
    monkeypatch.setattr(service, "_load_active_campaign_for_account", _campaign)
    monkeypatch.setattr(service, "_load_preview_decision_identity", _preview_identity)

    with pytest.raises(PermissionError, match="canonical proving activation missing"):
        await service._evaluate_live_preflight_guards(
            db=db,
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            require_submission_enabled=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("activation_state,expires_offset,message", [
    ("PAUSED", 1, "canonical proving activation is not active"),
    ("REVOKED", 1, "canonical proving activation is not active"),
    ("INVALIDATED", 1, "canonical proving activation is not active"),
    ("ACTIVE", -1, "canonical proving activation expired"),
])
async def test_canonical_activation_state_blocks_buy_preflight(
    monkeypatch: pytest.MonkeyPatch,
    activation_state: str,
    expires_offset: int,
    message: str,
) -> None:
    profile = SimpleNamespace(id=uuid4(), paper_account_id=uuid4(), provenance_metadata={"exchange_environment": "production", "provider": "kraken_spot"})
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        exchange_connection_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=Decimal("3"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        decision_record_id=uuid4(),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        provider="kraken_spot",
        environment="production",
        credentials_valid=True,
        api_permissions=["funds_query", "open_order_query", "closed_order_query", "ledger_query"],
        last_verified_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
        last_successful_sync_at=datetime.now(timezone.utc),
        balances=[{"currency": "USD", "available": "10"}],
    )
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_preparation_enabled=True,
            live_crypto_order_submission_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_balance_max_age_seconds=60,
            live_crypto_price_max_age_seconds=60,
        ),
    )
    approval_event = SimpleNamespace(
        id=uuid4(),
        live_trading_profile_id=profile.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        approval_scope={
            "canonical_preview_package_id": str(uuid4()),
            "live_trading_profile_id": str(profile.id),
            "paper_account_id": str(profile.paper_account_id),
            "product": preview.product_id,
            "side": preview.side,
            "provider": preview.provider,
            "environment": preview.environment,
        },
        approval_state="approved",
    )
    activation = SimpleNamespace(
        package_id=uuid4(),
        paper_account_id=profile.paper_account_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        activation_state=activation_state,
        expires_at=datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_offset),
    )
    package = SimpleNamespace(
        package_id=activation.package_id,
        package_state="ACTIVATED",
        paper_account_id=profile.paper_account_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        approval_event_id=approval_event.id,
        dry_run_live_crypto_order_id=uuid4(),
    )
    db = _FakeDb(profile=profile, preview=preview, approval_event=approval_event, activation=activation, package=package, connection=connection)

    async def _canonical_gate(**_kwargs):
        return LiveApprovalGateResult(allowed=True, reason=None, matched_approval_event_id=approval_event.id)

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _risk_context(**_kwargs):
        raise AssertionError("risk context should not be reached when activation is blocked")

    async def _campaign(*_args, **_kwargs):
        return SimpleNamespace(uuid=uuid4(), definition_version=1)

    async def _preview_identity(*_args, **_kwargs):
        return {"strategy_version": "v1", "parameter_set_version": "baseline"}

    monkeypatch.setattr(service, "evaluate_live_approval_gate", lambda **kwargs: _canonical_gate(**kwargs) if kwargs.get("checkpoint_type") == "bounded_proving_entry" else LiveApprovalGateResult(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None))
    monkeypatch.setattr(service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)
    monkeypatch.setattr(service, "_load_active_campaign_for_account", _campaign)
    monkeypatch.setattr(service, "_load_preview_decision_identity", _preview_identity)

    with pytest.raises(PermissionError, match=message):
        await service._evaluate_live_preflight_guards(
            db=db,
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            require_submission_enabled=True,
        )


@pytest.mark.asyncio
async def test_campaign_scoped_submission_buy_requires_canonical_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid4(), paper_account_id=uuid4(), provenance_metadata={"exchange_environment": "production", "provider": "kraken_spot"})
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        exchange_connection_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=Decimal("3"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        decision_record_id=uuid4(),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        provider="kraken_spot",
        environment="production",
        credentials_valid=True,
        api_permissions=["funds_query", "open_order_query", "closed_order_query", "ledger_query"],
        last_verified_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
        last_successful_sync_at=datetime.now(timezone.utc),
        balances=[{"currency": "USD", "available": "10"}],
    )
    approval_event = SimpleNamespace(
        id=uuid4(),
        live_trading_profile_id=profile.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        approval_scope={
            "canonical_preview_package_id": str(uuid4()),
            "live_trading_profile_id": str(profile.id),
            "paper_account_id": str(profile.paper_account_id),
            "product": preview.product_id,
            "side": preview.side,
            "provider": preview.provider,
            "environment": preview.environment,
            "capital_campaign_id": str(uuid4()),
            "capital_campaign_version": "1",
            "strategy_version": "v1",
            "parameter_set_version": "baseline",
            "crypto_order_preview_id": str(preview.crypto_order_preview_id),
            "max_order_usd": "5",
            "max_total_deployed_campaign_capital_usd": "5",
            "no_leverage": True,
        },
        approval_state="approved",
    )
    db = _FakeDb(profile=profile, preview=preview, approval_event=approval_event, activation=None, package=None, connection=connection)

    async def _campaign(*_args, **_kwargs):
        return SimpleNamespace(uuid=uuid4(), definition_version=1)

    monkeypatch.setattr(service, "_load_active_campaign_for_account", _campaign)

    with pytest.raises(PermissionError, match="canonical proving activation missing"):
        await service._validate_campaign_scoped_submission_authority(
            db=db,
            approval_event_id=approval_event.id,
            profile=profile,
            preview=preview,
            connection=connection,
            requested_quote_size=Decimal("3"),
            side="BUY",
        )


@pytest.mark.asyncio
async def test_campaign_scoped_submission_sell_can_bypass_canonical_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid4(), paper_account_id=uuid4(), provenance_metadata={"exchange_environment": "production", "provider": "kraken_spot"})
    campaign_id = uuid4()
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid4(),
        exchange_connection_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="SELL",
        order_type="MARKET",
        requested_amount=Decimal("3"),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        decision_record_id=uuid4(),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        provider="kraken_spot",
        environment="production",
        credentials_valid=True,
        api_permissions=["funds_query", "open_order_query", "closed_order_query", "ledger_query"],
        last_verified_at=datetime.now(timezone.utc),
        last_heartbeat_at=datetime.now(timezone.utc),
        last_successful_sync_at=datetime.now(timezone.utc),
        balances=[{"currency": "USD", "available": "10"}],
    )
    approval_event = SimpleNamespace(
        id=uuid4(),
        live_trading_profile_id=profile.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        approval_scope={
            "canonical_preview_package_id": str(uuid4()),
            "live_trading_profile_id": str(profile.id),
            "paper_account_id": str(profile.paper_account_id),
            "product": preview.product_id,
            "side": preview.side,
            "provider": preview.provider,
            "environment": preview.environment,
            "capital_campaign_id": str(campaign_id),
            "capital_campaign_version": "1",
            "strategy_version": "v1",
            "parameter_set_version": "baseline",
            "crypto_order_preview_id": str(preview.crypto_order_preview_id),
            "max_order_usd": "5",
            "max_total_deployed_campaign_capital_usd": "5",
            "no_leverage": True,
        },
        approval_state="approved",
    )
    db = _FakeDb(profile=profile, preview=preview, approval_event=approval_event, activation=None, package=None, connection=connection)

    async def _campaign(*_args, **_kwargs):
        return SimpleNamespace(id=42, uuid=campaign_id, definition_version=1)

    monkeypatch.setattr(service, "_load_active_campaign_for_account", _campaign)

    resolved_capital_campaign_id = await service._validate_campaign_scoped_submission_authority(
        db=db,
        approval_event_id=approval_event.id,
        profile=profile,
        preview=preview,
        connection=connection,
        requested_quote_size=Decimal("3"),
        side="SELL",
    )

    assert resolved_capital_campaign_id == 42
