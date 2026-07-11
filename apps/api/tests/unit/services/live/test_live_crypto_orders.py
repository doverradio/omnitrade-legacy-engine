from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.schemas.live_crypto_orders import LiveCryptoOrderPrepareRequest
from app.services import live_crypto_orders as service
from app.core.errors import InvalidRequestError, ServiceUnavailableError


class _FakeDb:
    async def scalar(self, _statement):
        return None

    async def scalars(self, _statement):
        return []

    def add(self, _item):
        return None

    async def flush(self):
        return None


class _DryRunFakeDb:
    def __init__(self, *, profile, preview, connection) -> None:
        self.profile = profile
        self.preview = preview
        self.connection = connection

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM crypto_order_previews" in sql:
            return self.preview
        if "FROM exchange_connections" in sql:
            return self.connection
        return None

    async def scalars(self, _statement):
        return []

    def add(self, _item):
        return None

    async def flush(self):
        return None


class _LiveOrderFakeDb:
    def __init__(self, *, profile, preview, live_order=None, connection=None) -> None:
        self.profile = profile
        self.preview = preview
        self.live_order = live_order
        self.connection = connection

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM crypto_order_previews" in sql:
            return self.preview
        if "FROM live_crypto_orders" in sql:
            return self.live_order
        if "FROM exchange_connections" in sql:
            return self.connection
        return None

    async def scalars(self, _statement):
        return []

    def add(self, _item):
        return None

    async def flush(self):
        return None


class _ReplayDetectedDb(_FakeDb):
    async def scalar(self, statement):
        sql = str(statement)
        if "FROM audit_log" in sql:
            return 1
        return None


class _SubmitStateDb(_LiveOrderFakeDb):
    def __init__(self, *, profile, preview, live_order, connection) -> None:
        super().__init__(profile=profile, preview=preview, live_order=live_order, connection=connection)
        self.audit_logs: list[object] = []
        self.commits = 0

    def add(self, item):
        self.audit_logs.append(item)

    async def commit(self):
        self.commits += 1


def _provider_stub(**methods):
    return SimpleNamespace(**methods)


@pytest.mark.asyncio
async def test_get_readiness_returns_closed_when_profile_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    response = await service.service.get_readiness(db=fake_db, live_trading_profile_id=uuid.uuid4())

    assert response.live_mode_enabled is False
    assert response.live_profile_ready is False
    assert response.feature_flag_enabled is False
    assert response.reason == "live_profile_not_found"


@pytest.mark.asyncio
async def test_prepare_confirmation_rejects_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    with pytest.raises(PermissionError, match="disabled"):
        await service.service.prepare_confirmation(
            db=fake_db,
            request=LiveCryptoOrderPrepareRequest(
                live_trading_profile_id=uuid.uuid4(),
                crypto_order_preview_id=uuid.uuid4(),
                operator_identity="operator:human",
                idempotency_token="token-1",
            ),
        )


@pytest.mark.asyncio
async def test_submit_rejects_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    with pytest.raises(PermissionError, match="disabled"):
        await service.service.submit(
            db=fake_db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=uuid.uuid4(),
                confirmation_challenge_id=uuid.uuid4(),
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token="token-2",
            ),
        )


@pytest.mark.asyncio
async def test_submit_firewall_does_not_call_create_order_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()
    create_order_calls = {"count": 0}

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    async def _count_create_order(*_args, **_kwargs):
        create_order_calls["count"] += 1
        raise AssertionError("Coinbase create_order must not be called when submission flag is disabled")

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_count_create_order),
    )

    with pytest.raises(PermissionError, match="disabled"):
        await service.service.submit(
            db=fake_db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=uuid.uuid4(),
                confirmation_challenge_id=uuid.uuid4(),
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token="token-firewall",
            ),
        )

    assert create_order_calls["count"] == 0


@pytest.mark.asyncio
async def test_dry_run_never_calls_coinbase_create_order(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=service.datetime.now(service.timezone.utc),
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
        last_successful_sync_at=service.datetime.now(service.timezone.utc),
        last_heartbeat_at=service.datetime.now(service.timezone.utc),
        last_verified_at=service.datetime.now(service.timezone.utc),
    )

    fake_db = _DryRunFakeDb(profile=profile, preview=preview, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )
    async def _approval_gate(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _persist_risk_decision(**_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(service, "evaluate_live_approval_gate", _approval_gate)
    monkeypatch.setattr(service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(service, "evaluate_signal_risk", lambda *_args, **_kwargs: SimpleNamespace(action=service.RiskDecisionAction.APPROVE))
    monkeypatch.setattr(service, "persist_risk_decision", _persist_risk_decision)

    async def _get_or_create_live_order(*_args, **_kwargs):
        return SimpleNamespace(
            live_crypto_order_id=uuid.uuid4(),
            crypto_order_preview_id=preview.crypto_order_preview_id,
            exchange_connection_id=preview.exchange_connection_id,
            provider=preview.provider,
            environment=preview.environment,
            product_id=preview.product_id,
            side=preview.side,
            order_type=preview.order_type,
            requested_quote_size=service.Decimal("5.00"),
            client_order_id="client-order-id",
            status="PENDING_CONFIRMATION",
            risk_event_id=uuid.uuid4(),
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=None,
            provider_status=None,
            submitted_at=None,
            acknowledged_at=None,
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={"dry_run": True},
            audit_correlation_id=uuid.uuid4(),
            operator_confirmation_id=None,
            created_at=service.datetime.now(service.timezone.utc),
            updated_at=service.datetime.now(service.timezone.utc),
        )

    monkeypatch.setattr(service.LiveCryptoOrderService, "_get_or_create_live_order", _get_or_create_live_order)

    async def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("Coinbase Create Order must not be called during dry run")

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_raise_if_called),
    )

    response = await service.service.dry_run(
        db=fake_db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-3",
        ),
    )

    assert response.order_submitted is False
    assert response.provider_create_order_called is False
    assert response.submission_skipped is True
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false" in response.submission_skip_reason
    assert "LIVE_CRYPTO_DRY_RUN_ENABLED=true" in response.submission_skip_reason
    assert response.dry_run_status in {"DRY_RUN_READY", "DRY_RUN_BLOCKED"}
    assert response.dry_run_message in {
        "Dry run completed. No Coinbase order was submitted.",
        "Dry run blocked. No Coinbase order was submitted.",
    }


@pytest.mark.asyncio
async def test_dry_run_requires_idempotency_token(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = _FakeDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    with pytest.raises(PermissionError, match="idempotency token required"):
        await service.service.dry_run(
            db=fake_db,
            request=service.LiveCryptoOrderDryRunRequest(
                live_trading_profile_id=uuid.uuid4(),
                crypto_order_preview_id=uuid.uuid4(),
                operator_identity="operator:human",
                idempotency_token="   ",
            ),
        )


@pytest.mark.asyncio
async def test_dry_run_blocks_when_preparation_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=service.datetime.now(service.timezone.utc),
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
        last_successful_sync_at=service.datetime.now(service.timezone.utc),
        last_heartbeat_at=service.datetime.now(service.timezone.utc),
        last_verified_at=service.datetime.now(service.timezone.utc),
    )
    fake_db = _DryRunFakeDb(profile=profile, preview=preview, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=False,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=False,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    async def _get_or_create_live_order(*_args, **_kwargs):
        return SimpleNamespace(
            live_crypto_order_id=uuid.uuid4(),
            crypto_order_preview_id=preview.crypto_order_preview_id,
            exchange_connection_id=preview.exchange_connection_id,
            provider=preview.provider,
            environment=preview.environment,
            product_id=preview.product_id,
            side=preview.side,
            order_type=preview.order_type,
            requested_quote_size=service.Decimal("5.00"),
            client_order_id="client-order-id",
            status="PENDING_CONFIRMATION",
            risk_event_id=None,
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=None,
            provider_status=None,
            submitted_at=None,
            acknowledged_at=None,
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={"dry_run": True},
            audit_correlation_id=uuid.uuid4(),
            operator_confirmation_id=None,
            created_at=service.datetime.now(service.timezone.utc),
            updated_at=service.datetime.now(service.timezone.utc),
        )

    monkeypatch.setattr(service.LiveCryptoOrderService, "_get_or_create_live_order", _get_or_create_live_order)

    response = await service.service.dry_run(
        db=fake_db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="dry-run-disabled-prep",
        ),
    )

    assert response.dry_run_status == "DRY_RUN_BLOCKED"
    assert response.order_submitted is False
    assert response.provider_create_order_called is False
    assert response.live_crypto_order.safe_provider_response["dry_run_errors"] == [
        "live crypto order preparation is disabled"
    ]


@pytest.mark.asyncio
async def test_prepare_and_dry_run_use_same_preflight_guard_path(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=now - timedelta(seconds=1),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        last_verified_at=now - timedelta(seconds=1),
        last_successful_sync_at=now - timedelta(seconds=1),
        last_heartbeat_at=now - timedelta(seconds=1),
        balances=[{"currency": "USD", "available": "10.00"}],
    )
    live_order = SimpleNamespace(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=preview.crypto_order_preview_id,
        exchange_connection_id=preview.exchange_connection_id,
        provider=preview.provider,
        environment=preview.environment,
        product_id=preview.product_id,
        side=preview.side,
        order_type=preview.order_type,
        requested_quote_size=service.Decimal("5.00"),
        client_order_id="client-order-id",
        status="PENDING_CONFIRMATION",
        risk_event_id=uuid.uuid4(),
        decision_record_id=None,
        validation_run_id=None,
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={"prepared_by": "operator:human"},
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=None,
        created_at=now,
        updated_at=now,
    )
    db = _LiveOrderFakeDb(profile=profile, preview=preview, live_order=live_order, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_dry_run_enabled=True,
            live_crypto_preparation_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    calls: list[bool] = []

    async def _shared_preflight(**kwargs):
        calls.append(bool(kwargs["require_submission_enabled"]))
        return {
            "profile": profile,
            "preview": preview,
            "connection": connection,
            "requested_quote_size": service.Decimal("5.00"),
            "approved_quote_size": service.Decimal("5.00"),
            "risk_action": service.RiskDecisionAction.APPROVE,
            "risk_event_id": uuid.uuid4(),
            "approval_event_id": uuid.uuid4(),
            "preview_age_seconds": 1,
            "readiness_age_seconds": 1,
            "balance_age_seconds": 1,
            "price_age_seconds": 1,
            "approved_intent_fingerprint": "intent-fingerprint",
            "evidence_fingerprint": "evidence-fingerprint",
        }

    monkeypatch.setattr(service, "_evaluate_live_preflight_guards", _shared_preflight)

    await service.service.prepare_confirmation(
        db=db,
        request=LiveCryptoOrderPrepareRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="prepare-token",
        ),
    )

    await service.service.dry_run(
        db=db,
        request=service.LiveCryptoOrderDryRunRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="dry-run-token",
        ),
    )

    assert calls == [True, False]


@pytest.mark.asyncio
async def test_prepare_confirmation_allows_preview_one_second_before_expiration(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=now - timedelta(seconds=29),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        last_verified_at=now - timedelta(seconds=1),
        last_successful_sync_at=now - timedelta(seconds=1),
        last_heartbeat_at=now - timedelta(seconds=1),
        balances=[{"currency": "USD", "available": "10.00"}],
    )
    db = _LiveOrderFakeDb(profile=profile, preview=preview, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )
    monkeypatch.setattr(service, "_utcnow", lambda: now)
    async def _approval_gate(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None, matched_approval_event_id=uuid.uuid4())

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _persist_risk_decision(**_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    async def _risk_context(**_kwargs):
        return None, service.RiskDecisionAction.APPROVE, service.Decimal("5.00"), uuid.uuid4()

    async def _get_or_create_live_order(*_args, **_kwargs):
        return SimpleNamespace(
            live_crypto_order_id=uuid.uuid4(),
            crypto_order_preview_id=preview.crypto_order_preview_id,
            exchange_connection_id=uuid.uuid4(),
            provider="coinbase_advanced",
            environment="production",
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            requested_quote_size=service.Decimal("5.00"),
            client_order_id="client-1",
            status="PENDING_CONFIRMATION",
            risk_event_id=uuid.uuid4(),
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=None,
            provider_status=None,
            submitted_at=None,
            acknowledged_at=None,
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={"prepared_by": "operator:human"},
            audit_correlation_id=uuid.uuid4(),
            operator_confirmation_id=None,
            created_at=now,
            updated_at=now,
        )

    monkeypatch.setattr(service, "evaluate_live_approval_gate", _approval_gate)
    monkeypatch.setattr(service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(service, "evaluate_signal_risk", lambda *_args, **_kwargs: SimpleNamespace(action=service.RiskDecisionAction.APPROVE))
    monkeypatch.setattr(service, "persist_risk_decision", _persist_risk_decision)
    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)
    monkeypatch.setattr(service.LiveCryptoOrderService, "_get_or_create_live_order", _get_or_create_live_order)

    response = await service.service.prepare_confirmation(
        db=db,
        request=LiveCryptoOrderPrepareRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-4",
        ),
    )

    assert response.preview_age_seconds == 29


@pytest.mark.asyncio
async def test_prepare_confirmation_blocks_preview_at_exact_expiration(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=now - timedelta(seconds=30),
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        last_verified_at=now - timedelta(seconds=1),
        last_successful_sync_at=now - timedelta(seconds=1),
        last_heartbeat_at=now - timedelta(seconds=1),
        balances=[{"currency": "USD", "available": "10.00"}],
    )
    db = _LiveOrderFakeDb(profile=profile, preview=preview, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )
    monkeypatch.setattr(service, "_utcnow", lambda: now)

    with pytest.raises(PermissionError, match="stale"):
        await service.service.prepare_confirmation(
            db=db,
            request=LiveCryptoOrderPrepareRequest(
                live_trading_profile_id=profile.id,
                crypto_order_preview_id=preview.crypto_order_preview_id,
                operator_identity="operator:human",
                idempotency_token="token-5",
            ),
        )


@pytest.mark.asyncio
async def test_submit_rejects_unknown_state_without_resubmission(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=now - timedelta(seconds=1),
    )
    live_order = SimpleNamespace(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=preview.crypto_order_preview_id,
        exchange_connection_id=preview.exchange_connection_id,
        provider=preview.provider,
        environment=preview.environment,
        product_id=preview.product_id,
        side=preview.side,
        order_type=preview.order_type,
        requested_quote_size=service.Decimal("5.00"),
        client_order_id="client-1",
        status="UNKNOWN",
        risk_event_id=uuid.uuid4(),
        decision_record_id=None,
        validation_run_id=None,
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={"prepared_by": "operator:human"},
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=None,
        created_at=now - timedelta(seconds=1),
        updated_at=now,
    )
    db = _LiveOrderFakeDb(profile=profile, preview=preview, live_order=live_order)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    with pytest.raises(ValueError, match="not in a submit-able state"):
        await service.service.submit(
            db=db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=live_order.live_crypto_order_id,
                confirmation_challenge_id=uuid.uuid4(),
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token="token-6",
            ),
        )


@pytest.mark.asyncio
async def test_submit_rejects_replayed_idempotency_token(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _ReplayDetectedDb()

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )

    with pytest.raises(PermissionError, match="replay"):
        await service.service.submit(
            db=db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=uuid.uuid4(),
                confirmation_challenge_id=uuid.uuid4(),
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token="submit-replay-token",
            ),
        )


@pytest.mark.asyncio
async def test_prepare_confirmation_reuses_existing_live_order_for_repeated_request(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    profile = SimpleNamespace(id=uuid.uuid4())
    preview = SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(),
        live_trading_profile_id=profile.id,
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=now - timedelta(seconds=1),
    )
    existing_order_id = uuid.uuid4()
    existing_live_order = SimpleNamespace(
        live_crypto_order_id=existing_order_id,
        crypto_order_preview_id=preview.crypto_order_preview_id,
        exchange_connection_id=uuid.uuid4(),
        provider=preview.provider,
        environment="production",
        product_id=preview.product_id,
        side=preview.side,
        order_type=preview.order_type,
        requested_quote_size=service.Decimal("5.00"),
        client_order_id="client-1",
        status="PENDING_CONFIRMATION",
        risk_event_id=uuid.uuid4(),
        decision_record_id=None,
        validation_run_id=None,
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={"prepared_by": "operator:human"},
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=None,
        created_at=now,
        updated_at=now,
    )
    connection = SimpleNamespace(
        exchange_connection_id=preview.exchange_connection_id,
        last_verified_at=now - timedelta(seconds=1),
        last_successful_sync_at=now - timedelta(seconds=1),
        last_heartbeat_at=now - timedelta(seconds=1),
        balances=[{"currency": "USD", "available": "10.00"}],
    )
    db = _LiveOrderFakeDb(profile=profile, preview=preview, live_order=existing_live_order, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_dry_run_enabled=True,
            live_crypto_max_order_usd=service.Decimal("5"),
            live_crypto_preview_max_age_seconds=30,
                live_crypto_preparation_enabled=True,
                live_crypto_balance_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_price_max_age_seconds=30,
            live_crypto_confirmation_challenge_minutes=1,
        ),
    )
    monkeypatch.setattr(service, "_utcnow", lambda: now)

    async def _approval_gate(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None, matched_approval_event_id=uuid.uuid4())

    async def _submission_guard(**_kwargs):
        return SimpleNamespace(allowed=True, reason=None)

    async def _persist_risk_decision(**_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    async def _risk_context(**_kwargs):
        return None, service.RiskDecisionAction.APPROVE, service.Decimal("5.00"), uuid.uuid4()

    monkeypatch.setattr(service, "evaluate_live_approval_gate", _approval_gate)
    monkeypatch.setattr(service, "evaluate_live_submission_guard", _submission_guard)
    monkeypatch.setattr(service, "evaluate_signal_risk", lambda *_args, **_kwargs: SimpleNamespace(action=service.RiskDecisionAction.APPROVE))
    monkeypatch.setattr(service, "persist_risk_decision", _persist_risk_decision)
    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)

    response = await service.service.prepare_confirmation(
        db=db,
        request=LiveCryptoOrderPrepareRequest(
            live_trading_profile_id=profile.id,
            crypto_order_preview_id=preview.crypto_order_preview_id,
            operator_identity="operator:human",
            idempotency_token="token-7",
        ),
    )

    assert response.live_crypto_order.live_crypto_order_id == existing_order_id


def test_safe_request_summary_redacts_secret_fields() -> None:
    summary = service._safe_request_summary(
        request_payload={
            "product_id": "BTC-USD",
            "side": "BUY",
            "order_configuration": {"market_market_ioc": {"quote_size": "5.00"}},
            "private_key": "secret",
            "fernet_key": "secret",
            "jwt": "secret",
            "authorization": "Bearer secret",
            "api_key_name": "full-api-key-name",
            "credentials_blob": "decrypted-blob",
        },
        provider_response={"ok": True},
    )

    assert summary == {
        "product_id": "BTC-USD",
        "side": "BUY",
        "order_configuration": {"market_market_ioc": {"quote_size": "5.00"}},
        "provider_preview": {"ok": True},
    }


def test_safe_request_summary_redacts_nested_provider_secrets() -> None:
    summary = service._safe_request_summary(
        request_payload={
            "product_id": "BTC-USD",
            "side": "BUY",
            "order_configuration": {"market_market_ioc": {"quote_size": "5.00"}},
        },
        provider_response={
            "ok": True,
            "api_key": "SENTINEL_API_KEY",
            "token": "SENTINEL_TOKEN",
            "nested": {
                "authorization": "Bearer SENTINEL_AUTH",
                "passphrase": "SENTINEL_PASSPHRASE",
                "safe": "value",
            },
            "list": [
                {"jwt": "SENTINEL_JWT"},
                {"signature": "SENTINEL_SIGNATURE"},
            ],
        },
    )

    provider_preview = summary["provider_preview"]
    assert provider_preview["api_key"] == "[REDACTED]"
    assert provider_preview["token"] == "[REDACTED]"
    assert provider_preview["nested"]["authorization"] == "[REDACTED]"
    assert provider_preview["nested"]["passphrase"] == "[REDACTED]"
    assert provider_preview["nested"]["safe"] == "value"
    assert provider_preview["list"][0]["jwt"] == "[REDACTED]"
    assert provider_preview["list"][1]["signature"] == "[REDACTED]"


def _submit_live_order(*, status: str = "PENDING_CONFIRMATION"):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=uuid.uuid4(),
        exchange_connection_id=uuid.uuid4(),
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_quote_size=service.Decimal("5.00"),
        client_order_id="stable-client-order-id",
        status=status,
        risk_event_id=uuid.uuid4(),
        decision_record_id=None,
        validation_run_id=None,
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={
            "prepared_by": "operator:human",
            "approval_event_id": str(uuid.uuid4()),
            "confirmation_expires_at": (now + timedelta(minutes=1)).isoformat(),
            "approved_intent_fingerprint": "intent-fingerprint",
            "evidence_fingerprint": "evidence-fingerprint",
            "execution_risk_verdict": "approve",
        },
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
    )


def _submit_preview(*, live_trading_profile_id: uuid.UUID, crypto_order_preview_id: uuid.UUID, exchange_connection_id: uuid.UUID):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        crypto_order_preview_id=crypto_order_preview_id,
        live_trading_profile_id=live_trading_profile_id,
        exchange_connection_id=exchange_connection_id,
        provider="coinbase_advanced",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        created_at=now - timedelta(seconds=1),
    )


def _submit_connection(*, exchange_connection_id: uuid.UUID):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        exchange_connection_id=exchange_connection_id,
        last_verified_at=now - timedelta(seconds=1),
        last_successful_sync_at=now - timedelta(seconds=1),
        last_heartbeat_at=now - timedelta(seconds=1),
        balances=[{"currency": "USD", "available": "10.00"}],
        credentials_encrypted="{}",
    )


@pytest.mark.asyncio
async def test_second_submit_after_acknowledgement_returns_existing_result_without_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    live_order = _submit_live_order(status="ACKNOWLEDGED")
    live_order.provider_order_id = "provider-order-1"
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=live_order.crypto_order_preview_id,
        exchange_connection_id=live_order.exchange_connection_id,
    )
    connection = _submit_connection(exchange_connection_id=live_order.exchange_connection_id)
    db = _SubmitStateDb(profile=profile, preview=preview, live_order=live_order, connection=connection)

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))

    async def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("create_order must not be called for acknowledged orders")

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_raise_if_called),
    )

    response = await service.service.submit(
        db=db,
        request=service.LiveCryptoOrderSubmitRequest(
            live_crypto_order_id=live_order.live_crypto_order_id,
            confirmation_challenge_id=live_order.operator_confirmation_id,
            confirmation_phrase="BUY BTC",
            operator_identity="operator:human",
            idempotency_token="new-click-token",
        ),
    )

    assert response.live_crypto_order.status == "ACKNOWLEDGED"
    assert response.order_submitted is True


@pytest.mark.asyncio
async def test_second_submit_during_reconciliation_required_blocks_without_provider_call(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    live_order = _submit_live_order(status="RECONCILIATION_REQUIRED")
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=live_order.crypto_order_preview_id,
        exchange_connection_id=live_order.exchange_connection_id,
    )
    connection = _submit_connection(exchange_connection_id=live_order.exchange_connection_id)
    db = _SubmitStateDb(profile=profile, preview=preview, live_order=live_order, connection=connection)

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))

    async def _raise_if_called(*_args, **_kwargs):
        raise AssertionError("create_order must not be called during reconciliation-required state")

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_raise_if_called),
    )

    with pytest.raises(PermissionError, match="reconcile"):
        await service.service.submit(
            db=db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=live_order.live_crypto_order_id,
                confirmation_challenge_id=live_order.operator_confirmation_id,
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token="new-click-token",
            ),
        )


@pytest.mark.asyncio
async def test_explicit_provider_rejection_sets_rejected_without_blind_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    live_order = _submit_live_order(status="PENDING_CONFIRMATION")
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=live_order.crypto_order_preview_id,
        exchange_connection_id=live_order.exchange_connection_id,
    )
    connection = _submit_connection(exchange_connection_id=live_order.exchange_connection_id)
    db = _SubmitStateDb(profile=profile, preview=preview, live_order=live_order, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_preview_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_price_max_age_seconds=30,
            live_crypto_max_order_usd=service.Decimal("5"),
        ),
    )
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})

    async def _risk_context(**_kwargs):
        return None, service.RiskDecisionAction.APPROVE, service.Decimal("5.00"), live_order.risk_event_id

    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)

    async def _reject(*_args, **_kwargs):
        raise InvalidRequestError("Coinbase API request failed", details={"status_code": 400, "response": {"message": "rejected"}})

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_reject),
    )

    response = await service.service.submit(
        db=db,
        request=service.LiveCryptoOrderSubmitRequest(
            live_crypto_order_id=live_order.live_crypto_order_id,
            confirmation_challenge_id=live_order.operator_confirmation_id,
            confirmation_phrase="BUY BTC",
            operator_identity="operator:human",
            idempotency_token="user-click-token",
        ),
    )

    assert response.live_crypto_order.status == "REJECTED"
    assert response.order_submitted is False
    assert response.live_crypto_order.failure_code == "provider_rejected"


@pytest.mark.asyncio
async def test_transport_failure_after_submission_started_enters_reconciliation_required(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    live_order = _submit_live_order(status="PENDING_CONFIRMATION")
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=live_order.crypto_order_preview_id,
        exchange_connection_id=live_order.exchange_connection_id,
    )
    connection = _submit_connection(exchange_connection_id=live_order.exchange_connection_id)
    db = _SubmitStateDb(profile=profile, preview=preview, live_order=live_order, connection=connection)

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_preview_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_price_max_age_seconds=30,
            live_crypto_max_order_usd=service.Decimal("5"),
        ),
    )
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})

    async def _risk_context(**_kwargs):
        return None, service.RiskDecisionAction.APPROVE, service.Decimal("5.00"), live_order.risk_event_id

    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)

    async def _ambiguous(*_args, **_kwargs):
        raise ServiceUnavailableError("Coinbase API is unreachable", details={"provider": "coinbase_advanced"})

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_ambiguous),
    )

    response = await service.service.submit(
        db=db,
        request=service.LiveCryptoOrderSubmitRequest(
            live_crypto_order_id=live_order.live_crypto_order_id,
            confirmation_challenge_id=live_order.operator_confirmation_id,
            confirmation_phrase="BUY BTC",
            operator_identity="operator:human",
            idempotency_token="user-click-token",
        ),
    )

    assert response.live_crypto_order.status == "RECONCILIATION_REQUIRED"
    assert response.live_crypto_order.failure_code == "provider_response_ambiguous"


@pytest.mark.asyncio
async def test_submit_does_not_use_new_client_token_as_provider_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    live_order = _submit_live_order(status="PENDING_CONFIRMATION")
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=live_order.crypto_order_preview_id,
        exchange_connection_id=live_order.exchange_connection_id,
    )
    connection = _submit_connection(exchange_connection_id=live_order.exchange_connection_id)
    db = _SubmitStateDb(profile=profile, preview=preview, live_order=live_order, connection=connection)
    seen = {}

    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(
            live_crypto_order_submission_enabled=True,
            live_crypto_preview_max_age_seconds=30,
            live_crypto_readiness_max_age_seconds=60,
            live_crypto_balance_max_age_seconds=30,
            live_crypto_price_max_age_seconds=30,
            live_crypto_max_order_usd=service.Decimal("5"),
        ),
    )
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})

    async def _risk_context(**_kwargs):
        return None, service.RiskDecisionAction.APPROVE, service.Decimal("5.00"), live_order.risk_event_id

    monkeypatch.setattr(service, "_build_real_risk_context", _risk_context)

    async def _success(*_args, **kwargs):
        seen["idempotency_key"] = kwargs["idempotency_key"]
        return {"success": True, "success_response": {"order_id": "provider-order-1", "status": "OPEN"}}, {"x-request-id": "1"}

    monkeypatch.setattr(
        service,
        "get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(create_order=_success),
    )

    response = await service.service.submit(
        db=db,
        request=service.LiveCryptoOrderSubmitRequest(
            live_crypto_order_id=live_order.live_crypto_order_id,
            confirmation_challenge_id=live_order.operator_confirmation_id,
            confirmation_phrase="BUY BTC",
            operator_identity="operator:human",
            idempotency_token="new-user-token",
        ),
    )

    assert seen["idempotency_key"] == live_order.client_order_id
    assert response.live_crypto_order.status == "ACKNOWLEDGED"


@pytest.mark.asyncio
async def test_reconcile_can_discover_order_by_client_order_id_when_provider_order_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    profile = SimpleNamespace(id=uuid.uuid4())
    live_order = _submit_live_order(status="RECONCILIATION_REQUIRED")
    live_order.provider_order_id = None
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=live_order.crypto_order_preview_id,
        exchange_connection_id=live_order.exchange_connection_id,
    )
    connection = _submit_connection(exchange_connection_id=live_order.exchange_connection_id)
    db = _SubmitStateDb(profile=profile, preview=preview, live_order=live_order, connection=connection)

    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})

    async def _list_orders(*_args, **_kwargs):
        return {
            "orders": [
                {
                    "order_id": "provider-order-1",
                    "client_order_id": live_order.client_order_id,
                    "product_id": live_order.product_id,
                    "status": "OPEN",
                    "filled_size": "0",
                }
            ]
        }, {"x-request-id": "2"}

    async def _list_fills(*_args, **_kwargs):
        return {"fills": []}, {"x-request-id": "3"}

    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(list_historical_orders=_list_orders, list_historical_fills=_list_fills),
    )

    response = await service.service.reconcile(
        db=db,
        live_crypto_order_id=live_order.live_crypto_order_id,
        request=service.LiveCryptoOrderReconcileRequest(operator_identity="operator:human"),
    )

    assert response.live_crypto_order.provider_order_id == "provider-order-1"
    assert response.reconciliation_status in {"ACKNOWLEDGED", "UNKNOWN", "RECONCILIATION_REQUIRED"}
