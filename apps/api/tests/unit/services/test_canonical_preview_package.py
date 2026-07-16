from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services import canonical_preview_package as cpp


class _FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list[object]:
        return list(self._rows)

    def first(self) -> object | None:
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, *, scalar_values: list[object] | None = None, execute_rows: list[object] | None = None) -> None:
        self._scalar_values = list(scalar_values or [])
        self._execute_rows = list(execute_rows or [])
        self.added: list[object] = []
        self.flush_calls = 0

    def add(self, obj: object) -> None:
        for attr_name in ("package_id", "activation_id", "live_crypto_order_id"):
            if hasattr(obj, attr_name) and getattr(obj, attr_name) is None:
                setattr(obj, attr_name, uuid4())
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1

    async def scalar(self, _statement):
        if self._scalar_values:
            return self._scalar_values.pop(0)
        sql = str(_statement)
        params = _statement.compile().params
        if "canonical_proving_activations" in sql:
            package_id = params.get("package_id_1") or params.get("package_id")
            for item in self.added:
                if getattr(item, "package_id", None) == package_id:
                    return item
        if "live_crypto_orders" in sql:
            order_id = params.get("live_crypto_order_id_1") or params.get("live_crypto_order_id")
            for item in self.added:
                if getattr(item, "live_crypto_order_id", None) == order_id:
                    return item
        return None

    async def execute(self, _statement) -> _FakeResult:
        rows = list(self._execute_rows)
        self._execute_rows.clear()
        return _FakeResult(rows)


def _async_return(value: object):
    async def _inner(**_kwargs):
        return value

    return _inner


def _profile() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), paper_account_id=uuid4())


def _runtime_campaign(*, campaign_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(uuid=campaign_id)


def _definition(*, campaign_id: UUID, campaign_version: int) -> SimpleNamespace:
    return SimpleNamespace(campaign_id=campaign_id, version=campaign_version)


def _preview(*, package_id: UUID, requested_amount: Decimal = Decimal("3")) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        crypto_order_preview_id=package_id,
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        requested_amount=requested_amount,
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        strategy_id=uuid4(),
        parameter_set_id=uuid4(),
        exchange_connection_id=uuid4(),
        created_at=now,
        expires_at=now.replace(microsecond=0),
    )


def _approval_event(*, package_id: UUID, expires_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        approval_state="approved",
        checkpoint_type="bounded_proving_entry",
        approval_scope={"canonical_preview_package_id": str(package_id)},
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_create_canonical_preview_package_persists_authoritative_row(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    package_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    preview = _preview(package_id=package_id)
    strategy = SimpleNamespace(id=uuid4(), module_version="v1")
    parameter_set = SimpleNamespace(id=uuid4(), label="baseline")
    request = cpp.CanonicalPreviewPackageCreateRequest(
        campaign_id=campaign_id,
        campaign_version=1,
        paper_account_id=profile.paper_account_id,
        live_trading_profile_id=profile.id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        max_proposed_order_amount=Decimal("5"),
        actor="operator:human",
        idempotency_key="pkg-1",
    )

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(preview))
    monkeypatch.setattr(cpp, "_load_decision_record", _async_return(SimpleNamespace(decision_id=preview.decision_record_id)))
    monkeypatch.setattr(cpp, "_load_risk_event", _async_return(SimpleNamespace(id=preview.risk_event_id)))

    db = _FakeDb(scalar_values=[strategy, parameter_set])
    result = await cpp.create_canonical_preview_package(db=db, request=request)

    assert result["idempotent"] is False
    assert result["readiness"]["ready"] is True
    assert result["package"]["package_state"] == "READY"
    assert db.flush_calls == 1
    assert db.added[0].package_state == "READY"


@pytest.mark.asyncio
async def test_authorize_canonical_preview_package_records_bounded_proving_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id = uuid4()
    package = SimpleNamespace(
        package_id=package_id,
        campaign_id=uuid4(),
        campaign_version=7,
        runtime_campaign_id=uuid4(),
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        side="BUY",
        proposed_order_amount=Decimal("3"),
        risk_approved_amount=Decimal("3"),
        strategy_id=uuid4(),
        strategy_version="v1",
        parameter_set_id=uuid4(),
        parameter_set_version="baseline",
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        crypto_order_preview_id=uuid4(),
        market_evidence_identity={},
        market_evidence_observed_at=datetime.now(timezone.utc),
        preview_expires_at=datetime.now(timezone.utc),
        package_state="READY",
        generated_at=datetime.now(timezone.utc),
        idempotency_key="pkg-1",
        input_fingerprint="fingerprint",
        approval_event_id=None,
        dry_run_live_crypto_order_id=None,
        superseded_at=None,
        invalidated_reason=None,
    )
    request = cpp.CanonicalPreviewPackageAuthorizeRequest(
        package_id=package_id,
        actor="operator:human",
        approver_role="risk_owner",
        rationale="bounded proving",
        expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        max_order_usd=Decimal("5"),
        max_total_deployed_campaign_capital_usd=Decimal("5"),
        no_leverage=True,
        idempotency_key="auth-1",
    )

    captured: dict[str, object] = {}

    async def _checkpoint(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(approval_event_id=uuid4(), checkpoint_type="bounded_proving_entry")

    monkeypatch.setattr(cpp, "_load_package", _async_return(package))
    monkeypatch.setattr(cpp, "record_live_approval_checkpoint", _checkpoint)

    db = _FakeDb()
    result = await cpp.authorize_canonical_preview_package(db=db, request=request)

    assert result["package_id"] == str(package_id)
    assert result["checkpoint_type"] == "bounded_proving_entry"
    assert result["approval_scope"]["canonical_preview_package_id"] == str(package_id)
    assert captured["request"].checkpoint_type == "bounded_proving_entry"
    assert db.flush_calls == 1


@pytest.mark.asyncio
async def test_dry_run_records_package_link_and_rejects_scope_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id = uuid4()
    package = SimpleNamespace(
        package_id=package_id,
        campaign_id=uuid4(),
        campaign_version=7,
        runtime_campaign_id=uuid4(),
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        side="BUY",
        proposed_order_amount=Decimal("3"),
        risk_approved_amount=Decimal("3"),
        strategy_id=uuid4(),
        strategy_version="v1",
        parameter_set_id=uuid4(),
        parameter_set_version="baseline",
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        crypto_order_preview_id=uuid4(),
        market_evidence_identity={"exchange_connection_id": str(uuid4())},
        market_evidence_observed_at=datetime.now(timezone.utc),
        preview_expires_at=datetime.now(timezone.utc),
        package_state="AUTHORIZED",
        generated_at=datetime.now(timezone.utc),
        idempotency_key="pkg-1",
        input_fingerprint="fingerprint",
        approval_event_id=uuid4(),
        dry_run_live_crypto_order_id=None,
        superseded_at=None,
        invalidated_reason=None,
    )
    request = cpp.CanonicalPreviewPackageDryRunRequest(
        package_id=package_id,
        approval_event_id=package.approval_event_id,
        operator_identity="operator:human",
        idempotency_token="dry-1",
    )
    approval = _approval_event(package_id=package_id)

    monkeypatch.setattr(cpp, "_load_package", _async_return(package))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(SimpleNamespace(id=package.live_trading_profile_id, paper_account_id=package.paper_account_id)))

    db = _FakeDb(scalar_values=[approval])
    result = await cpp.run_dry_run_for_canonical_preview_package(db=db, request=request)

    assert result["dry_run_status"] == "DRY_RUN_READY"
    assert result["submission_skipped"] is True
    assert package.package_state == "DRY_RUN_PASSED"
    assert package.dry_run_live_crypto_order_id is not None
    assert db.flush_calls == 2

    mismatched = _approval_event(package_id=uuid4())
    db = _FakeDb(scalar_values=[mismatched])
    with pytest.raises(PermissionError, match="approval scope package mismatch"):
        await cpp.run_dry_run_for_canonical_preview_package(db=db, request=request)


@pytest.mark.asyncio
async def test_activate_creates_activation_and_status_reports_active(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id = uuid4()
    dry_run_order_id = uuid4()
    package = SimpleNamespace(
        package_id=package_id,
        campaign_id=uuid4(),
        campaign_version=7,
        runtime_campaign_id=uuid4(),
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        side="BUY",
        proposed_order_amount=Decimal("3"),
        risk_approved_amount=Decimal("3"),
        strategy_id=uuid4(),
        strategy_version="v1",
        parameter_set_id=uuid4(),
        parameter_set_version="baseline",
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        crypto_order_preview_id=uuid4(),
        market_evidence_identity={"exchange_connection_id": str(uuid4())},
        market_evidence_observed_at=datetime.now(timezone.utc),
        preview_expires_at=datetime.now(timezone.utc),
        package_state="DRY_RUN_PASSED",
        generated_at=datetime.now(timezone.utc),
        idempotency_key="pkg-1",
        input_fingerprint="fingerprint",
        approval_event_id=uuid4(),
        dry_run_live_crypto_order_id=dry_run_order_id,
        superseded_at=None,
        invalidated_reason=None,
    )
    approval = _approval_event(package_id=package_id)
    dry_run_order = SimpleNamespace(live_crypto_order_id=dry_run_order_id, status="DRY_RUN_READY")
    request = cpp.CanonicalPreviewPackageActivationRequest(
        package_id=package_id,
        approval_event_id=package.approval_event_id,
        dry_run_live_crypto_order_id=dry_run_order_id,
        actor="operator:human",
        expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        idempotency_key="activate-1",
    )

    monkeypatch.setattr(cpp, "_load_package", _async_return(package))

    db = _FakeDb(scalar_values=[approval, dry_run_order, None])
    result = await cpp.activate_canonical_proving_campaign(db=db, request=request)

    assert result["activation"]["activation_state"] == "ACTIVE"
    assert result["package"]["package_state"] == "ACTIVATED"
    assert package.package_state == "ACTIVATED"
    assert db.flush_calls == 2

    status = await cpp.get_canonical_proving_activation_status(db=db, package_id=package_id)
    assert status["activated"] is True
    assert status["activation"]["activation_state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_pause_and_revoke_are_idempotent_and_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id = uuid4()
    package = SimpleNamespace(
        package_id=package_id,
        campaign_id=uuid4(),
        campaign_version=7,
        runtime_campaign_id=uuid4(),
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        side="BUY",
        proposed_order_amount=Decimal("3"),
        risk_approved_amount=Decimal("3"),
        strategy_id=uuid4(),
        strategy_version="v1",
        parameter_set_id=uuid4(),
        parameter_set_version="baseline",
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        crypto_order_preview_id=uuid4(),
        market_evidence_identity={"exchange_connection_id": str(uuid4())},
        market_evidence_observed_at=datetime.now(timezone.utc),
        preview_expires_at=datetime.now(timezone.utc),
        package_state="ACTIVATED",
        generated_at=datetime.now(timezone.utc),
        idempotency_key="pkg-1",
        input_fingerprint="fingerprint",
        approval_event_id=uuid4(),
        dry_run_live_crypto_order_id=uuid4(),
        superseded_at=None,
        invalidated_reason=None,
    )
    activation = SimpleNamespace(
        activation_id=uuid4(),
        package_id=package_id,
        approval_event_id=package.approval_event_id,
        dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
        campaign_id=package.campaign_id,
        campaign_version=package.campaign_version,
        paper_account_id=package.paper_account_id,
        live_trading_profile_id=package.live_trading_profile_id,
        provider=package.provider,
        environment=package.environment,
        product=package.product,
        max_order_amount=Decimal("3"),
        max_deployed_capital=Decimal("3"),
        no_leverage=True,
        activated_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        activation_state="ACTIVE",
        revoked_at=None,
        paused_at=None,
        invalidated_reason=None,
    )

    monkeypatch.setattr(cpp, "_load_package", _async_return(package))
    monkeypatch.setattr(cpp, "_load_activation", _async_return(activation))

    db = _FakeDb()
    pause = await cpp.pause_canonical_proving_activation(
        db=db,
        request=cpp.CanonicalPreviewPackagePauseRequest(
            package_id=package_id,
            actor="operator:human",
            reason="pause for review",
            idempotency_key="pause-1",
        ),
    )
    assert pause["activation"]["activation_state"] == "PAUSED"
    assert pause["idempotent"] is False
    assert any(getattr(item, "action", None) == "canonical_proving_activation_paused" for item in db.added)

    second_pause = await cpp.pause_canonical_proving_activation(
        db=db,
        request=cpp.CanonicalPreviewPackagePauseRequest(
            package_id=package_id,
            actor="operator:human",
            reason="pause for review",
            idempotency_key="pause-1",
        ),
    )
    assert second_pause["idempotent"] is True

    revoke = await cpp.revoke_canonical_proving_activation(
        db=db,
        request=cpp.CanonicalPreviewPackageRevokeRequest(
            package_id=package_id,
            actor="operator:human",
            reason="authority revoked",
            idempotency_key="revoke-1",
        ),
    )
    assert revoke["activation"]["activation_state"] == "REVOKED"
    assert any(getattr(item, "action", None) == "canonical_proving_activation_revoked" for item in db.added)


@pytest.mark.asyncio
async def test_hard_cap_rejects_over_five_on_authorize_dry_run_and_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    package_id = uuid4()
    package = SimpleNamespace(
        package_id=package_id,
        campaign_id=uuid4(),
        campaign_version=7,
        runtime_campaign_id=uuid4(),
        paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(),
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        side="BUY",
        proposed_order_amount=Decimal("5.01"),
        risk_approved_amount=Decimal("5.01"),
        strategy_id=uuid4(),
        strategy_version="v1",
        parameter_set_id=uuid4(),
        parameter_set_version="baseline",
        decision_record_id=uuid4(),
        risk_event_id=uuid4(),
        crypto_order_preview_id=uuid4(),
        market_evidence_identity={"exchange_connection_id": str(uuid4())},
        market_evidence_observed_at=datetime.now(timezone.utc),
        preview_expires_at=datetime.now(timezone.utc),
        package_state="READY",
        generated_at=datetime.now(timezone.utc),
        idempotency_key="pkg-1",
        input_fingerprint="fingerprint",
        approval_event_id=uuid4(),
        dry_run_live_crypto_order_id=uuid4(),
        superseded_at=None,
        invalidated_reason=None,
    )

    db = _FakeDb()
    monkeypatch.setattr(cpp, "_load_package", _async_return(package))
    with pytest.raises(PermissionError, match="exceeds canonical cap"):
        await cpp.authorize_canonical_preview_package(
            db=db,
            request=cpp.CanonicalPreviewPackageAuthorizeRequest(
                package_id=package_id,
                actor="operator:human",
                approver_role="risk_owner",
                rationale="bounded proving",
                expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                max_order_usd=Decimal("5.01"),
                max_total_deployed_campaign_capital_usd=Decimal("5.01"),
                no_leverage=True,
                idempotency_key="auth-1",
            ),
        )

    approval = _approval_event(package_id=package_id)
    monkeypatch.setattr(cpp, "_load_package", _async_return(package))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(SimpleNamespace(id=package.live_trading_profile_id, paper_account_id=package.paper_account_id)))
    package.approval_event_id = approval.id
    db = _FakeDb(scalar_values=[approval])
    with pytest.raises(PermissionError, match="exceeds canonical cap"):
        await cpp.run_dry_run_for_canonical_preview_package(
            db=db,
            request=cpp.CanonicalPreviewPackageDryRunRequest(
                package_id=package_id,
                approval_event_id=approval.id,
                operator_identity="operator:human",
                idempotency_token="dry-1",
            ),
        )

    monkeypatch.setattr(cpp, "_load_package", _async_return(package))
    db = _FakeDb(scalar_values=[approval, SimpleNamespace(live_crypto_order_id=package.dry_run_live_crypto_order_id, status="DRY_RUN_READY")])
    with pytest.raises(PermissionError, match="exceeds canonical cap"):
        await cpp.activate_canonical_proving_campaign(
            db=db,
            request=cpp.CanonicalPreviewPackageActivationRequest(
                package_id=package_id,
                approval_event_id=approval.id,
                dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
                actor="operator:human",
                expires_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                idempotency_key="act-1",
            ),
        )
