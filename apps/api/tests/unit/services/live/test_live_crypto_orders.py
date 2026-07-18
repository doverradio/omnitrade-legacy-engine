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
    def __init__(self, *, profile, preview, live_order=None, connection=None, approval_event=None, campaign=None, decision_snapshot=None) -> None:
        self.profile = profile
        self.preview = preview
        self.live_order = live_order
        self.connection = connection
        self.approval_event = approval_event
        self.campaign = campaign
        self.decision_snapshot = decision_snapshot

    async def scalar(self, statement):
        sql = str(statement)
        params = statement.compile().params
        if "FROM live_trading_profiles" in sql:
            return self.profile
        if "FROM crypto_order_previews" in sql:
            preview_id = params.get("crypto_order_preview_id_1")
            if preview_id is None:
                return self.preview
            return self.preview if getattr(self.preview, "crypto_order_preview_id", None) == preview_id else None
        if "FROM live_crypto_orders" in sql:
            return self.live_order
        if "FROM exchange_connections" in sql:
            return self.connection
        if "FROM live_approval_events" in sql:
            approval_id = params.get("id_1")
            if self.approval_event is None:
                return None
            return self.approval_event if getattr(self.approval_event, "id", None) == approval_id else None
        if "FROM capital_campaigns" in sql:
            paper_account_id = params.get("paper_account_id_1")
            if self.campaign is None:
                return None
            return self.campaign if getattr(self.campaign, "paper_account_id", None) == paper_account_id else None
        if "FROM decision_snapshots" in sql:
            decision_id = params.get("decision_id_1")
            if self.decision_snapshot is None:
                return None
            return self.decision_snapshot if getattr(self.decision_snapshot, "decision_id", None) == decision_id else None
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
    def __init__(self, *, profile, preview, live_order, connection, approval_event=None, campaign=None, decision_snapshot=None) -> None:
        if not hasattr(profile, "paper_account_id"):
            profile.paper_account_id = uuid.uuid4()
        if not hasattr(profile, "provenance_metadata"):
            profile.provenance_metadata = {"exchange_environment": "production", "provider": "coinbase_advanced"}
        decision_id = getattr(preview, "decision_record_id", None)
        if decision_id is not None and getattr(live_order, "decision_record_id", None) is None:
            live_order.decision_record_id = decision_id
        prepared_decision = str(live_order.safe_provider_response.get("decision_record_id") or "")
        if decision_id is not None and not prepared_decision:
            live_order.safe_provider_response["decision_record_id"] = str(decision_id)
        if campaign is None:
            campaign = _submit_campaign(paper_account_id=profile.paper_account_id)
        if approval_event is None:
            approval_event = _submit_approval_event(
                approval_event_id=uuid.UUID(str(live_order.safe_provider_response["approval_event_id"])),
                profile_id=profile.id,
                paper_account_id=profile.paper_account_id,
                campaign_id=campaign.uuid,
                campaign_version=campaign.definition_version,
                provider=getattr(preview, "provider", "coinbase_advanced"),
                environment=getattr(preview, "environment", "production"),
                product_id=getattr(preview, "product_id", "BTC-USD"),
                side=getattr(preview, "side", "BUY"),
                preview_id=preview.crypto_order_preview_id,
                strategy_version=(decision_snapshot.strategy_version if decision_snapshot is not None else "ma_crossover@1.0.0"),
                parameter_set_version=(decision_snapshot.parameter_set_version if decision_snapshot is not None else "param-set-v1"),
            )
        if decision_snapshot is None and decision_id is not None:
            decision_snapshot = _submit_decision_snapshot(decision_id=decision_id)
        super().__init__(
            profile=profile,
            preview=preview,
            live_order=live_order,
            connection=connection,
            approval_event=approval_event,
            campaign=campaign,
            decision_snapshot=decision_snapshot,
        )
        self.audit_logs: list[object] = []
        self.commits = 0

    def add(self, item):
        self.audit_logs.append(item)

    async def commit(self):
        self.commits += 1


def _provider_stub(**methods):
    return SimpleNamespace(**methods)


def _submit_campaign(*, paper_account_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=4242,
        uuid=uuid.uuid4(),
        definition_version=1,
        starting_capital=service.Decimal("25.00"),
        paper_account_id=paper_account_id,
    )


def _submit_decision_snapshot(*, decision_id: uuid.UUID, strategy_version: str = "ma_crossover@1.0.0", parameter_set_version: str = "param-set-v1") -> SimpleNamespace:
    return SimpleNamespace(
        decision_id=decision_id,
        strategy_version=strategy_version,
        parameter_set_version=parameter_set_version,
    )


def _submit_approval_event(
    *,
    approval_event_id: uuid.UUID,
    profile_id: uuid.UUID,
    paper_account_id: uuid.UUID,
    campaign_id: uuid.UUID,
    campaign_version: int,
    provider: str,
    environment: str,
    product_id: str,
    side: str,
    preview_id: uuid.UUID,
    strategy_version: str,
    parameter_set_version: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=approval_event_id,
        approval_state="approved",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        approval_scope={
            "product": product_id,
            "side": side,
            "max_order_usd": "5",
            "provider": provider,
            "environment": environment,
            "live_trading_profile_id": str(profile_id),
            "paper_account_id": str(paper_account_id),
            "capital_campaign_id": str(campaign_id),
            "capital_campaign_version": campaign_version,
            "max_total_deployed_campaign_capital_usd": "25.00",
            "strategy_version": strategy_version,
            "parameter_set_version": parameter_set_version,
            "crypto_order_preview_id": str(preview_id),
            "no_leverage": True,
        },
    )


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
    async def _approval_gate(**kwargs):
        checkpoint_type = kwargs.get("checkpoint_type")
        if checkpoint_type == "bounded_proving_entry":
            return SimpleNamespace(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None)
        return SimpleNamespace(allowed=True, reason=None, matched_approval_event_id=uuid.uuid4())

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
        "Dry run completed. No provider order was submitted.",
        "Dry run blocked. No provider order was submitted.",
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
        provider="coinbase_advanced",
        credentials_valid=True,
        api_permissions=["view", "trade"],
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
        provider="coinbase_advanced",
        credentials_valid=True,
        api_permissions=["view", "trade"],
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
    async def _approval_gate(**kwargs):
        checkpoint_type = kwargs.get("checkpoint_type")
        if checkpoint_type == "bounded_proving_entry":
            return SimpleNamespace(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None)
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
        provider="coinbase_advanced",
        credentials_valid=True,
        api_permissions=["view", "trade"],
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
        provider="coinbase_advanced",
        credentials_valid=True,
        api_permissions=["view", "trade"],
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

    async def _approval_gate(**kwargs):
        checkpoint_type = kwargs.get("checkpoint_type")
        if checkpoint_type == "bounded_proving_entry":
            return SimpleNamespace(allowed=False, reason="approval_checkpoint_missing", matched_approval_event_id=None)
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


def _submit_live_order(
    *,
    status: str = "PENDING_CONFIRMATION",
    preview_id: uuid.UUID | None = None,
    exchange_connection_id: uuid.UUID | None = None,
    decision_record_id: uuid.UUID | None = None,
    risk_event_id: uuid.UUID | None = None,
    approval_event_id: uuid.UUID | None = None,
    provider: str = "coinbase_advanced",
    environment: str = "production",
    product_id: str = "BTC-USD",
    side: str = "BUY",
    requested_quote_size: service.Decimal = service.Decimal("5.00"),
):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    resolved_preview_id = preview_id or uuid.uuid4()
    resolved_connection_id = exchange_connection_id or uuid.uuid4()
    resolved_decision_id = decision_record_id
    resolved_risk_event_id = risk_event_id or uuid.uuid4()
    resolved_approval_event_id = approval_event_id or uuid.uuid4()
    return SimpleNamespace(
        live_crypto_order_id=uuid.uuid4(),
        crypto_order_preview_id=resolved_preview_id,
        exchange_connection_id=resolved_connection_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
        side=side,
        order_type="MARKET",
        requested_quote_size=requested_quote_size,
        client_order_id="stable-client-order-id",
        status=status,
        risk_event_id=resolved_risk_event_id,
        decision_record_id=resolved_decision_id,
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
            "approval_event_id": str(resolved_approval_event_id),
            "risk_event_id": str(resolved_risk_event_id),
            "crypto_order_preview_id": str(resolved_preview_id),
            "decision_record_id": "" if resolved_decision_id is None else str(resolved_decision_id),
            "approved_quote_size": format(requested_quote_size, "f"),
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


def _submit_preview(
    *,
    live_trading_profile_id: uuid.UUID,
    crypto_order_preview_id: uuid.UUID,
    exchange_connection_id: uuid.UUID,
    decision_record_id: uuid.UUID | None = None,
    provider: str = "coinbase_advanced",
    environment: str = "production",
    product_id: str = "BTC-USD",
    side: str = "BUY",
):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    resolved_decision_id = decision_record_id or uuid.uuid4()
    return SimpleNamespace(
        crypto_order_preview_id=crypto_order_preview_id,
        live_trading_profile_id=live_trading_profile_id,
        exchange_connection_id=exchange_connection_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
        side=side,
        order_type="MARKET",
        requested_amount=service.Decimal("5.00"),
        decision_record_id=resolved_decision_id,
        created_at=now - timedelta(seconds=1),
    )


def _submit_connection(*, exchange_connection_id: uuid.UUID, provider: str = "coinbase_advanced", environment: str = "production"):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        exchange_connection_id=exchange_connection_id,
        provider=provider,
        environment=environment,
        credentials_valid=True,
        api_permissions=["view", "trade"],
        last_verified_at=now - timedelta(seconds=1),
        last_successful_sync_at=now - timedelta(seconds=1),
        last_heartbeat_at=now - timedelta(seconds=1),
        balances=[{"currency": "USD", "available": "10.00"}],
        credentials_encrypted="{}",
    )


def _submit_settings() -> SimpleNamespace:
    return SimpleNamespace(
        live_crypto_order_submission_enabled=True,
        live_crypto_preview_max_age_seconds=30,
        live_crypto_readiness_max_age_seconds=60,
        live_crypto_balance_max_age_seconds=30,
        live_crypto_price_max_age_seconds=30,
        live_crypto_max_order_usd=service.Decimal("5"),
    )


def _submit_authority_fixture() -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace, _SubmitStateDb]:
    profile = SimpleNamespace(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        provenance_metadata={"exchange_environment": "production", "provider": "coinbase_advanced"},
    )
    decision_id = uuid.uuid4()
    preview_id = uuid.uuid4()
    exchange_connection_id = uuid.uuid4()
    approval_event_id = uuid.uuid4()
    risk_event_id = uuid.uuid4()
    live_order = _submit_live_order(
        status="PENDING_CONFIRMATION",
        preview_id=preview_id,
        exchange_connection_id=exchange_connection_id,
        decision_record_id=decision_id,
        risk_event_id=risk_event_id,
        approval_event_id=approval_event_id,
        requested_quote_size=service.Decimal("5.00"),
    )
    preview = _submit_preview(
        live_trading_profile_id=profile.id,
        crypto_order_preview_id=preview_id,
        exchange_connection_id=exchange_connection_id,
        decision_record_id=decision_id,
    )
    connection = _submit_connection(exchange_connection_id=exchange_connection_id)
    campaign = _submit_campaign(paper_account_id=profile.paper_account_id)
    decision_snapshot = _submit_decision_snapshot(decision_id=decision_id)
    approval_event = _submit_approval_event(
        approval_event_id=approval_event_id,
        profile_id=profile.id,
        paper_account_id=profile.paper_account_id,
        campaign_id=campaign.uuid,
        campaign_version=campaign.definition_version,
        provider=preview.provider,
        environment=preview.environment,
        product_id=preview.product_id,
        side=preview.side,
        preview_id=preview.crypto_order_preview_id,
        strategy_version=decision_snapshot.strategy_version,
        parameter_set_version=decision_snapshot.parameter_set_version,
    )
    db = _SubmitStateDb(
        profile=profile,
        preview=preview,
        live_order=live_order,
        connection=connection,
        approval_event=approval_event,
        campaign=campaign,
        decision_snapshot=decision_snapshot,
    )
    return profile, live_order, preview, connection, campaign, approval_event, db


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
@pytest.mark.parametrize(
    "case,reason_substring,mutate",
    [
        (
            "campaign_id_mismatch",
            "campaign identity mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("capital_campaign_id", str(uuid.uuid4())),
        ),
        (
            "campaign_version_mismatch",
            "campaign version mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("capital_campaign_version", 999),
        ),
        (
            "strategy_version_mismatch",
            "strategy version mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("strategy_version", "other@9.9.9"),
        ),
        (
            "parameter_set_version_mismatch",
            "parameter set version mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("parameter_set_version", "other-param"),
        ),
        (
            "preview_identity_mismatch",
            "preview identity mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("crypto_order_preview_id", str(uuid.uuid4())),
        ),
        (
            "live_profile_mismatch",
            "live trading profile mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("live_trading_profile_id", str(uuid.uuid4())),
        ),
        (
            "provider_mismatch",
            "provider",
            lambda fixture: fixture[5].approval_scope.__setitem__("provider", "kraken_spot"),
        ),
        (
            "environment_mismatch",
            "environment",
            lambda fixture: fixture[5].approval_scope.__setitem__("environment", "sandbox"),
        ),
        (
            "product_mismatch",
            "product mismatch",
            lambda fixture: fixture[5].approval_scope.__setitem__("product", "ETH-USD"),
        ),
        (
            "max_order_exceeded",
            "max order amount exceeded",
            lambda fixture: fixture[5].approval_scope.__setitem__("max_order_usd", "4.99"),
        ),
        (
            "leverage_requested",
            "leverage boundary violated",
            lambda fixture: fixture[5].approval_scope.__setitem__("no_leverage", False),
        ),
        (
            "campaign_identity_missing",
            "lacks campaign identity",
            lambda fixture: fixture[5].approval_scope.pop("capital_campaign_id"),
        ),
        (
            "strategy_identity_missing",
            "lacks strategy identity",
            lambda fixture: fixture[5].approval_scope.pop("strategy_version"),
        ),
        (
            "parameter_identity_missing",
            "lacks parameter identity",
            lambda fixture: fixture[5].approval_scope.pop("parameter_set_version"),
        ),
    ],
)
async def test_submit_blocks_on_campaign_scoped_authority_mismatch_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    reason_substring: str,
    mutate,
) -> None:
    fixture = _submit_authority_fixture()
    _profile, live_order, _preview, _connection, _campaign, _approval_event, db = fixture
    mutate(fixture)

    calls = {"create_order": 0}

    async def _create_order(*_args, **_kwargs):
        calls["create_order"] += 1
        return {"success": True, "success_response": {"order_id": "provider-order-1", "status": "OPEN"}}, {}

    monkeypatch.setattr(service, "get_settings", _submit_settings)
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda *_args, **_kwargs: _provider_stub(create_order=_create_order))

    with pytest.raises(PermissionError, match=reason_substring):
        await service.service.submit(
            db=db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=live_order.live_crypto_order_id,
                confirmation_challenge_id=live_order.operator_confirmation_id,
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token=f"token-{case}",
            ),
        )

    assert calls["create_order"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,reason_substring,mutate",
    [
        (
            "risk_event_id_mismatch",
            "prepared risk event identity mismatch",
            lambda fixture: fixture[1].safe_provider_response.__setitem__("risk_event_id", str(uuid.uuid4())),
        ),
        (
            "decision_record_id_mismatch",
            "decision record identity mismatch",
            lambda fixture: fixture[1].safe_provider_response.__setitem__("decision_record_id", str(uuid.uuid4())),
        ),
        (
            "prepared_preview_id_mismatch",
            "prepared preview identity mismatch",
            lambda fixture: fixture[1].safe_provider_response.__setitem__("crypto_order_preview_id", str(uuid.uuid4())),
        ),
        (
            "approved_amount_mismatch",
            "prepared approved amount mismatch",
            lambda fixture: fixture[1].safe_provider_response.__setitem__("approved_quote_size", "4.99"),
        ),
    ],
)
async def test_submit_blocks_on_risk_or_identity_mismatch_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    reason_substring: str,
    mutate,
) -> None:
    fixture = _submit_authority_fixture()
    _profile, live_order, _preview, _connection, _campaign, _approval_event, db = fixture
    mutate(fixture)

    calls = {"create_order": 0}

    async def _create_order(*_args, **_kwargs):
        calls["create_order"] += 1
        return {"success": True, "success_response": {"order_id": "provider-order-1", "status": "OPEN"}}, {}

    monkeypatch.setattr(service, "get_settings", _submit_settings)
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda *_args, **_kwargs: _provider_stub(create_order=_create_order))

    with pytest.raises(PermissionError, match=reason_substring):
        await service.service.submit(
            db=db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=live_order.live_crypto_order_id,
                confirmation_challenge_id=live_order.operator_confirmation_id,
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token=f"token-risk-{case}",
            ),
        )

    assert calls["create_order"] == 0


@pytest.mark.asyncio
async def test_submit_allows_exact_campaign_scoped_authority_and_calls_provider_once(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _submit_authority_fixture()
    _profile, live_order, _preview, _connection, _campaign, _approval_event, db = fixture
    calls = {"create_order": 0, "payload_quote_size": None, "idempotency_key": None}

    async def _create_order(*_args, **kwargs):
        calls["create_order"] += 1
        calls["payload_quote_size"] = kwargs["request_payload"]["order_configuration"]["market_market_ioc"]["quote_size"]
        calls["idempotency_key"] = kwargs["idempotency_key"]
        return {"success": True, "success_response": {"order_id": "provider-order-1", "status": "OPEN"}}, {"x-request-id": "ok"}

    monkeypatch.setattr(service, "get_settings", _submit_settings)
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda *_args, **_kwargs: _provider_stub(create_order=_create_order))

    response = await service.service.submit(
        db=db,
        request=service.LiveCryptoOrderSubmitRequest(
            live_crypto_order_id=live_order.live_crypto_order_id,
            confirmation_challenge_id=live_order.operator_confirmation_id,
            confirmation_phrase="BUY BTC",
            operator_identity="operator:human",
            idempotency_token="token-scope-match",
        ),
    )

    assert calls["create_order"] == 1
    assert calls["idempotency_key"] == live_order.client_order_id
    assert service.Decimal(str(calls["payload_quote_size"])) == live_order.requested_quote_size
    assert response.live_crypto_order.status == "ACKNOWLEDGED"
    assert response.live_crypto_order.safe_provider_response["capital_campaign_id"] == _campaign.id


@pytest.mark.asyncio
async def test_submit_persists_verified_capital_campaign_id_for_reconciliation_to_find(monkeypatch: pytest.MonkeyPatch) -> None:
    """A campaign-scoped submission must persist the already-verified campaign identity onto
    the order so accounting reconciliation can resolve it later without re-deriving trust."""
    fixture = _submit_authority_fixture()
    _profile, live_order, _preview, _connection, campaign, _approval_event, db = fixture

    async def _create_order(*_args, **_kwargs):
        return {"success": True, "success_response": {"order_id": "provider-order-1", "status": "OPEN"}}, {"x-request-id": "ok"}

    monkeypatch.setattr(service, "get_settings", _submit_settings)
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda *_args, **_kwargs: _provider_stub(create_order=_create_order))

    response = await service.service.submit(
        db=db,
        request=service.LiveCryptoOrderSubmitRequest(
            live_crypto_order_id=live_order.live_crypto_order_id,
            confirmation_challenge_id=live_order.operator_confirmation_id,
            confirmation_phrase="BUY BTC",
            operator_identity="operator:human",
            idempotency_token="token-campaign-persistence",
        ),
    )

    assert response.live_crypto_order.safe_provider_response["capital_campaign_id"] == campaign.id
    assert isinstance(response.live_crypto_order.safe_provider_response["capital_campaign_id"], int)


@pytest.mark.asyncio
async def test_submit_blocks_when_requested_amount_precision_could_expand_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _submit_authority_fixture()
    _profile, live_order, _preview, _connection, _campaign, _approval_event, db = fixture
    live_order.requested_quote_size = service.Decimal("5.001")
    live_order.safe_provider_response["approved_quote_size"] = "5.001"

    calls = {"create_order": 0}

    async def _create_order(*_args, **_kwargs):
        calls["create_order"] += 1
        return {"success": True, "success_response": {"order_id": "provider-order-1", "status": "OPEN"}}, {}

    monkeypatch.setattr(service, "get_settings", _submit_settings)
    monkeypatch.setattr(service, "_utcnow", lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "_build_intent_fingerprint", lambda **_kwargs: "intent-fingerprint")
    monkeypatch.setattr(service, "_build_evidence_fingerprint", lambda **_kwargs: "evidence-fingerprint")
    monkeypatch.setattr(service, "_load_decrypted_credentials", lambda _connection: {"api_key": "key", "api_secret": "secret"})
    monkeypatch.setattr(service, "get_exchange_provider", lambda *_args, **_kwargs: _provider_stub(create_order=_create_order))

    with pytest.raises(ValueError, match="precision"):
        await service.service.submit(
            db=db,
            request=service.LiveCryptoOrderSubmitRequest(
                live_crypto_order_id=live_order.live_crypto_order_id,
                confirmation_challenge_id=live_order.operator_confirmation_id,
                confirmation_phrase="BUY BTC",
                operator_identity="operator:human",
                idempotency_token="token-precision",
            ),
        )

    assert calls["create_order"] == 0


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
    assert db.commits == 1


@pytest.mark.parametrize("provider_status", ["PARTIALLY_FILLED", "CLOSED"])
@pytest.mark.asyncio
async def test_reconcile_accepts_provider_statuses_kraken_legitimately_reports(
    monkeypatch: pytest.MonkeyPatch, provider_status: str
) -> None:
    """Regression: Kraken's own status-mapping logic (KrakenSpotClient.lookup_order
    and the ClosedOrders fallback) legitimately reports PARTIALLY_FILLED for an
    order closed with a partial fill, and CLOSED for a closed order with zero
    executed volume. _normalize_provider_status already maps both of these to
    an internal reconciliation status. Previously, LiveCryptoOrderResponse's
    provider_status Literal did not include either value, so persisting the
    reconciliation succeeded (no CHECK constraint on the DB column) but
    constructing the HTTP response afterward raised a pydantic ValidationError,
    turning a successful reconciliation into a client-visible failure.
    """
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
                    "status": provider_status,
                    "filled_size": "0.00002",
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

    assert response.live_crypto_order.provider_status == provider_status
    assert db.commits == 1


def test_reconcile_endpoint_returns_http_200_for_partially_filled_provider_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end HTTP regression for the same defect: drives the real
    /live-crypto-orders/{id}/reconcile route (auth, routing, response
    serialization included) and asserts it returns 200, not a 500 from a
    ValidationError while building LiveCryptoOrderResponse.
    """
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import create_app

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
                    "status": "PARTIALLY_FILLED",
                    "filled_size": "0.00002",
                }
            ]
        }, {"x-request-id": "2"}

    async def _list_fills(*_args, **_kwargs):
        return {"fills": []}, {"x-request-id": "3"}

    monkeypatch.setattr(
        "app.services.live.accounting_reconciliation.get_exchange_provider",
        lambda *_args, **_kwargs: _provider_stub(list_historical_orders=_list_orders, list_historical_fills=_list_fills),
    )

    app = create_app()

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/live-crypto-orders/{live_order.live_crypto_order_id}/reconcile",
            json={"operator_identity": "operator:human"},
            headers={"Authorization": "Bearer operator:human"},
        )

    assert response.status_code == 200
    assert response.json()["live_crypto_order"]["provider_status"] == "PARTIALLY_FILLED"
