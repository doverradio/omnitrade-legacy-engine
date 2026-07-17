from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import BaseModel

import app.operator_cli.service as service
from app.schemas.capital_campaign_domain import CommissionedControlPlaneStatusResponse


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


class _FakeDb:
    def __init__(self, state: "_State") -> None:
        self.state = state

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM canonical_preview_packages" in sql:
            return self.state.package
        if "FROM live_crypto_orders" in sql:
            return self.state.live_order
        return None

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def commit(self) -> None:
        return None

    async def flush(self) -> None:
        return None


class _SessionContext:
    def __init__(self, db: _FakeDb) -> None:
        self._db = db

    async def __aenter__(self) -> _FakeDb:
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        _ = exc_type, exc, tb
        return False


class _State:
    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.campaign_id = uuid4()
        self.paper_account_id = uuid4()
        self.profile_id = uuid4()
        self.runtime_id = 77
        self.preview_id = uuid4()
        self.package = SimpleNamespace(
            package_id=uuid4(),
            campaign_id=self.campaign_id,
            campaign_version=1,
            runtime_campaign_id=self.campaign_id,
            paper_account_id=self.paper_account_id,
            live_trading_profile_id=self.profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
            side="BUY",
            proposed_order_amount=Decimal("5"),
            risk_approved_amount=Decimal("5"),
            strategy_id=uuid4(),
            strategy_version="ma_crossover@1.0.0",
            parameter_set_id=uuid4(),
            parameter_set_version="baseline",
            decision_record_id=uuid4(),
            risk_event_id=uuid4(),
            crypto_order_preview_id=self.preview_id,
            market_evidence_identity={
                "entry_authority": "OPERATOR_COMMISSIONED",
                "entry_reason": "INITIAL_PROVING_ENTRY",
                "strategy_override_scope": "COMMISSIONING_ENTRY_ONLY",
            },
            market_evidence_observed_at=now,
            preview_expires_at=now + timedelta(minutes=5),
            package_state="READY",
            generated_at=now,
            idempotency_key="pkg-1",
            input_fingerprint="pkg-fingerprint",
            approval_event_id=None,
            dry_run_live_crypto_order_id=None,
            superseded_at=None,
            invalidated_reason=None,
        )
        self.preview = SimpleNamespace(
            crypto_order_preview_id=self.preview_id,
            exchange_connection_id=uuid4(),
            provider="kraken_spot",
            environment="production",
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            requested_amount=Decimal("5"),
            estimated_average_price=Decimal("50000"),
            estimated_fee=Decimal("0.01"),
            estimated_slippage=Decimal("0.01"),
            estimated_total_value=Decimal("5"),
            estimated_base_size=Decimal("0.0001"),
            created_at=now,
            expires_at=now + timedelta(minutes=5),
            decision_record_id=self.package.decision_record_id,
        )
        self.approval = SimpleNamespace(
            id=uuid4(),
            approval_state="approved",
            expires_at=now + timedelta(minutes=4),
        )
        self.activation = SimpleNamespace(
            activation_id=uuid4(),
            package_id=self.package.package_id,
            activation_state="ACTIVE",
            expires_at=now + timedelta(minutes=4),
        )
        self.definition = SimpleNamespace(
            campaign_id=self.campaign_id,
            version=1,
            risk_policy_id="risk-v1",
            risk_policy_version="1.0.0",
            metadata_evidence={"commissioned_seed_campaign": {"state": "DRAFT"}},
        )
        self.runtime = SimpleNamespace(id=self.runtime_id, uuid=self.campaign_id, definition_version=1, paper_account_id=self.paper_account_id)
        self.paper_account = SimpleNamespace(id=self.paper_account_id, current_cash_balance=Decimal("25"))
        self.profile = SimpleNamespace(id=self.profile_id, paper_account_id=self.paper_account_id)
        self.connection = SimpleNamespace(
            exchange_connection_id=self.preview.exchange_connection_id,
            credentials_valid=True,
            status="connected",
            last_verified_at=now,
            last_successful_sync_at=now,
            last_heartbeat_at=now,
            balances=[{"currency": "USD", "available": "25"}],
        )
        self.mandate = SimpleNamespace(mandate_id=uuid4())
        self.mandate_version = SimpleNamespace(mandate_version_id=uuid4(), version_number=7, entry_policy={})
        self.asset = SimpleNamespace(id=uuid4(), min_order_notional=Decimal("5"), qty_step_size=Decimal("0.00000001"), supports_fractional=True)
        self.live_order = None
        self.create_calls = 0
        self.authorize_calls = 0
        self.dry_run_calls = 0
        self.activate_calls = 0
        self.prepare_calls = 0
        self.execute_calls = 0
        self.reconcile_calls = 0


def _status_payload(state: _State) -> dict[str, object]:
    blob = state.definition.metadata_evidence["commissioned_seed_campaign"]
    ownership = blob.get("ownership_reconciliation") if isinstance(blob.get("ownership_reconciliation"), dict) else {}
    return {
        "commissioning_status": {
            "state": blob.get("state"),
            "autonomous_lifecycle_owner": bool(ownership.get("position_identity")),
        },
        "read_only": True,
        "no_execution": True,
    }


def _install_common_monkeypatches(monkeypatch: pytest.MonkeyPatch, state: _State) -> None:
    async def _load_definition(**_kwargs):
        return state.definition

    async def _load_runtime(**_kwargs):
        return state.runtime

    async def _load_paper_account(**_kwargs):
        return state.paper_account

    async def _load_profile(**_kwargs):
        return state.profile

    async def _load_package(**_kwargs):
        return state.package

    async def _load_preview(**_kwargs):
        return state.preview

    async def _load_approval(**_kwargs):
        return state.approval if state.package.approval_event_id else None

    async def _load_activation(**_kwargs):
        return state.activation if state.package.package_state == "ACTIVATED" else None

    async def _load_connection(**_kwargs):
        return state.connection

    async def _load_mandate(**_kwargs):
        return state.mandate

    async def _load_mandate_version(**_kwargs):
        return state.mandate_version

    async def _load_asset(**_kwargs):
        return state.asset

    async def _status(**_kwargs):
        return _status_payload(state)

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(_FakeDb(state)))
    monkeypatch.setattr(service, "_load_campaign_definition_by_identity", _load_definition)
    monkeypatch.setattr(service, "_load_runtime_campaign_by_identity", _load_runtime)
    monkeypatch.setattr(service, "_load_paper_account_by_id", _load_paper_account)
    monkeypatch.setattr(service, "_load_profile_by_id", _load_profile)
    monkeypatch.setattr(service, "_load_latest_forced_canonical_package", _load_package)
    monkeypatch.setattr(service, "_load_preview_for_package_row", _load_preview)
    monkeypatch.setattr(service, "_load_latest_approval_for_package", _load_approval)
    monkeypatch.setattr(service, "_load_activation_for_package", _load_activation)
    monkeypatch.setattr(service, "_load_exchange_connection_by_id", _load_connection)
    monkeypatch.setattr(service, "_load_active_mandate_for_commissioning", _load_mandate)
    monkeypatch.setattr(service, "_load_authorized_mandate_version", _load_mandate_version)
    monkeypatch.setattr(service, "_load_asset_for_product_symbol", _load_asset)
    monkeypatch.setattr(service, "canonical_proving_commission_status", _status)
    monkeypatch.setattr(service, "resolve_effective_risk_policy", _async_return(SimpleNamespace(max_position_size_pct=Decimal("0.25"))))
    monkeypatch.setattr(
        service,
        "generate_commissioned_campaign_preview",
        _async_return(SimpleNamespace(
            preview_identity_hash="preview-hash-1",
            estimated_base_quantity=Decimal("0.0001"),
            reference_price=Decimal("50000"),
        )),
    )

    async def _create_package(**_kwargs):
        state.create_calls += 1
        now = datetime.now(timezone.utc)
        state.package = SimpleNamespace(**{**state.package.__dict__, "package_id": uuid4(), "package_state": "READY", "preview_expires_at": now + timedelta(minutes=5), "approval_event_id": None, "dry_run_live_crypto_order_id": None})
        state.preview = SimpleNamespace(**{**state.preview.__dict__, "crypto_order_preview_id": state.package.crypto_order_preview_id, "created_at": now, "expires_at": now + timedelta(minutes=5)})
        return {"package": {"package_id": str(state.package.package_id)}}

    async def _authorize(**_kwargs):
        state.authorize_calls += 1
        state.package.package_state = "AUTHORIZED"
        state.package.approval_event_id = state.approval.id
        state.approval.expires_at = datetime.now(timezone.utc) + timedelta(minutes=4)
        return {"approval_event_id": str(state.approval.id)}

    async def _dry_run(**_kwargs):
        state.dry_run_calls += 1
        state.package.package_state = "DRY_RUN_PASSED"
        state.package.dry_run_live_crypto_order_id = uuid4()
        return {"dry_run_status": "DRY_RUN_READY"}

    async def _activate(**_kwargs):
        state.activate_calls += 1
        state.package.package_state = "ACTIVATED"
        state.activation = SimpleNamespace(
            activation_id=uuid4(),
            package_id=state.package.package_id,
            activation_state="ACTIVE",
            expires_at=state.approval.expires_at,
        )
        return {"activation": {"activation_id": str(state.activation.activation_id)}}

    async def _backfill(**_kwargs):
        state.definition.metadata_evidence["commissioned_seed_campaign"] = {
            "state": "READY",
            "authority_metadata": {"lifecycle_authority": "OMNITRADE_AUTONOMOUS"},
        }
        return {"state": "READY"}

    async def _commission(**_kwargs):
        blob = state.definition.metadata_evidence.setdefault("commissioned_seed_campaign", {})
        blob["state"] = "COMMISSIONED"
        blob["commissioning"] = {"preview_identity_hash": "preview-hash-1", "commissioning_identity": "commission-id", "commissioned_until": (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()}
        return SimpleNamespace(current_state="COMMISSIONED")

    class _FakeLiveService:
        async def prepare_confirmation(self, *, db, request):
            _ = db
            state.prepare_calls += 1
            if state.live_order is None:
                state.live_order = SimpleNamespace(live_crypto_order_id=uuid4(), operator_confirmation_id=uuid4())
            return SimpleNamespace(
                live_crypto_order=SimpleNamespace(live_crypto_order_id=state.live_order.live_crypto_order_id),
                confirmation_challenge_id=state.live_order.operator_confirmation_id,
                confirmation_phrase_required="BUY BTC",
            )

    async def _execute(**_kwargs):
        state.execute_calls += 1
        blob = state.definition.metadata_evidence.setdefault("commissioned_seed_campaign", {})
        blob["state"] = "BUY_RECONCILIATION_PENDING"
        blob["entry_execution"] = {
            "live_crypto_order_id": str(state.live_order.live_crypto_order_id),
            "provider_order_id": "provider-1",
        }
        return SimpleNamespace(current_state="BUY_RECONCILIATION_PENDING", live_crypto_order_id=state.live_order.live_crypto_order_id)

    async def _reconcile(**_kwargs):
        state.reconcile_calls += 1
        blob = state.definition.metadata_evidence.setdefault("commissioned_seed_campaign", {})
        blob["state"] = "ACTIVE_POSITION"
        blob["ownership_reconciliation"] = {"position_identity": "position-1"}
        return SimpleNamespace(current_state="ACTIVE_POSITION", ownership_proven=True, model_dump=lambda mode="json": {"current_state": "ACTIVE_POSITION", "ownership_proven": True})

    monkeypatch.setattr(service, "create_canonical_preview_package", _create_package)
    monkeypatch.setattr(service, "authorize_canonical_preview_package", _authorize)
    monkeypatch.setattr(service, "run_dry_run_for_canonical_preview_package", _dry_run)
    monkeypatch.setattr(service, "activate_canonical_proving_campaign", _activate)
    monkeypatch.setattr(service, "backfill_commissioned_ready_metadata", _backfill)
    monkeypatch.setattr(service, "commission_commissioned_campaign", _commission)
    monkeypatch.setattr(service, "LiveCryptoOrderService", _FakeLiveService)
    monkeypatch.setattr(service, "execute_commissioned_entry", _execute)
    monkeypatch.setattr(service, "reconcile_commissioned_buy_ownership", _reconcile)


@pytest.mark.asyncio
async def test_canonical_proving_commission_reaches_active_position(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    _install_common_monkeypatches(monkeypatch, state)

    payload = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-1",
    )

    assert payload["current_state"] == "ACTIVE_POSITION"
    assert payload["autonomous_lifecycle_owner"] is True
    assert state.execute_calls == 1
    assert state.reconcile_calls == 1


@pytest.mark.asyncio
async def test_canonical_proving_commission_refreshes_expired_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    state.package.preview_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    _install_common_monkeypatches(monkeypatch, state)

    payload = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-2",
    )

    assert payload["current_state"] == "ACTIVE_POSITION"
    assert state.create_calls == 1


@pytest.mark.asyncio
async def test_canonical_proving_commission_refreshes_expired_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    state.package.package_state = "AUTHORIZED"
    state.package.approval_event_id = state.approval.id
    state.approval.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    _install_common_monkeypatches(monkeypatch, state)

    payload = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-3",
    )

    assert payload["current_state"] == "ACTIVE_POSITION"
    assert state.create_calls == 1


@pytest.mark.asyncio
async def test_canonical_proving_commission_repeat_does_not_duplicate_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    _install_common_monkeypatches(monkeypatch, state)

    first = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-4",
    )
    second = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-4",
    )

    assert first["current_state"] == "ACTIVE_POSITION"
    assert second["replayed"] is True
    assert state.execute_calls == 1


@pytest.mark.asyncio
async def test_canonical_proving_commission_timeout_reconciliation_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    _install_common_monkeypatches(monkeypatch, state)

    async def _execute(**_kwargs):
        state.execute_calls += 1
        blob = state.definition.metadata_evidence.setdefault("commissioned_seed_campaign", {})
        blob["state"] = "RECONCILIATION_REQUIRED"
        blob["entry_execution"] = {"live_crypto_order_id": str(state.live_order.live_crypto_order_id), "provider_order_id": None}
        return SimpleNamespace(current_state="RECONCILIATION_REQUIRED", live_crypto_order_id=state.live_order.live_crypto_order_id)

    async def _reconcile(**_kwargs):
        state.reconcile_calls += 1
        blob = state.definition.metadata_evidence.setdefault("commissioned_seed_campaign", {})
        if state.reconcile_calls == 1:
            blob["state"] = "RECONCILIATION_REQUIRED"
            blob["ownership_reconciliation"] = {}
            return SimpleNamespace(current_state="RECONCILIATION_REQUIRED", ownership_proven=False, model_dump=lambda mode="json": {"current_state": "RECONCILIATION_REQUIRED", "ownership_proven": False})
        blob["state"] = "ACTIVE_POSITION"
        blob["ownership_reconciliation"] = {"position_identity": "position-1"}
        return SimpleNamespace(current_state="ACTIVE_POSITION", ownership_proven=True, model_dump=lambda mode="json": {"current_state": "ACTIVE_POSITION", "ownership_proven": True})

    monkeypatch.setattr(service, "execute_commissioned_entry", _execute)
    monkeypatch.setattr(service, "reconcile_commissioned_buy_ownership", _reconcile)

    first = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-5",
    )
    second = await service.canonical_proving_commission_bundle(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        amount_usd=Decimal("5"),
        actor="operator:human",
        approver_role="operator",
        rationale="bounded proving",
        no_leverage=True,
        confirm=True,
        idempotency_key="root-5",
    )

    assert first["current_state"] == "RECONCILIATION_REQUIRED"
    assert second["current_state"] == "ACTIVE_POSITION"
    assert state.execute_calls == 1
    assert state.reconcile_calls == 2


@pytest.mark.asyncio
async def test_canonical_proving_commission_propagates_execution_blockers(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()
    _install_common_monkeypatches(monkeypatch, state)

    async def _execute(**_kwargs):
        raise PermissionError("global kill switch engaged")

    monkeypatch.setattr(service, "execute_commissioned_entry", _execute)

    with pytest.raises(PermissionError, match="global kill switch engaged"):
        await service.canonical_proving_commission_bundle(
            campaign_id=state.campaign_id,
            campaign_version=1,
            paper_account_id=state.paper_account_id,
            live_trading_profile_id=state.profile_id,
            provider="kraken_spot",
            environment="production",
            product="BTC-USD",
            amount_usd=Decimal("5"),
            actor="operator:human",
            approver_role="operator",
            rationale="bounded proving",
            no_leverage=True,
            confirm=True,
            idempotency_key="root-6",
        )


class _StatusEnum(Enum):
    READY = "READY"


@dataclass
class _DataclassEvidence:
    evidence_id: str
    observed_at: datetime


class _NestedEvidenceModel(BaseModel):
    marker: str
    created_at: datetime
    amount: Decimal


def test_to_json_compatible_serializes_nested_non_native_values() -> None:
    class _FakeColumn:
        def __init__(self, name: str) -> None:
            self.name = name

    class _FakeOrmRow:
        __table__ = SimpleNamespace(columns=[_FakeColumn("row_id"), _FakeColumn("created_at"), _FakeColumn("amount")])

        def __init__(self) -> None:
            self.row_id = uuid4()
            self.created_at = datetime(2026, 7, 17, tzinfo=timezone.utc)
            self.amount = Decimal("5.25")

    payload = {
        "uuid": uuid4(),
        "decimal": Decimal("5"),
        "datetime": datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
        "enum": _StatusEnum.READY,
        "dataclass": _DataclassEvidence(evidence_id="ev-1", observed_at=datetime(2026, 7, 17, tzinfo=timezone.utc)),
        "pydantic": _NestedEvidenceModel(marker="nested", created_at=datetime(2026, 7, 17, tzinfo=timezone.utc), amount=Decimal("1.5")),
        "orm_row": _FakeOrmRow(),
        "list": [uuid4(), Decimal("3.14"), _StatusEnum.READY],
    }

    serialized = service._to_json_compatible(payload)

    assert isinstance(serialized["uuid"], str)
    assert serialized["decimal"] == "5"
    assert serialized["enum"] == "READY"
    assert serialized["dataclass"]["evidence_id"] == "ev-1"
    assert serialized["pydantic"]["amount"] == "1.5"
    assert serialized["orm_row"]["amount"] == "5.25"
    assert serialized["list"][1] == "3.14"
    json.dumps(serialized)


@pytest.mark.asyncio
async def test_canonical_proving_commission_status_serializes_commissioned_control_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()

    class _ControlPlaneNested(BaseModel):
        nested_id: str
        nested_amount: Decimal

    class _ControlPlaneEnum(Enum):
        OPEN = "open"

    status_model = CommissionedControlPlaneStatusResponse(
        campaign_id=state.campaign_id,
        version=1,
        state="READY",
        readiness={
            "uuid_value": uuid4(),
            "decimal_value": Decimal("5"),
            "datetime_value": datetime(2026, 7, 17, tzinfo=timezone.utc),
            "enum_value": _ControlPlaneEnum.OPEN,
            "nested_model": _ControlPlaneNested(nested_id="n-1", nested_amount=Decimal("2.5")),
        },
        generated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    async def _load_definition(**_kwargs):
        return state.definition

    async def _load_package(**_kwargs):
        return state.package

    async def _load_approval(**_kwargs):
        return state.approval

    async def _load_activation(**_kwargs):
        return state.activation

    async def _load_preview(**_kwargs):
        return state.preview

    async def _load_package_payload(**_kwargs):
        return {
            "package": {
                "package_id": state.package.package_id,
                "proposed_order_amount": Decimal("5"),
                "generated_at": datetime(2026, 7, 17, tzinfo=timezone.utc),
            }
        }

    async def _status(**_kwargs):
        return status_model

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(_FakeDb(state)))
    monkeypatch.setattr(service, "_load_campaign_definition_by_identity", _load_definition)
    monkeypatch.setattr(service, "_load_latest_forced_canonical_package", _load_package)
    monkeypatch.setattr(service, "_load_latest_approval_for_package", _load_approval)
    monkeypatch.setattr(service, "_load_activation_for_package", _load_activation)
    monkeypatch.setattr(service, "_load_preview_for_package_row", _load_preview)
    monkeypatch.setattr(service, "get_canonical_preview_package", _load_package_payload)
    monkeypatch.setattr(service, "_get_commissioned_control_plane_status", _status)

    payload = await service.canonical_proving_commission_status(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
    )

    assert isinstance(payload["commissioned_control_plane"], dict)
    readiness = payload["commissioned_control_plane"]["readiness"]
    assert isinstance(readiness["uuid_value"], str)
    assert readiness["decimal_value"] == "5"
    assert readiness["enum_value"] == "open"
    assert readiness["nested_model"]["nested_amount"] == "2.5"
    json.dumps(payload)


@pytest.mark.asyncio
async def test_canonical_proving_commission_status_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _State()

    class _TrackingDb(_FakeDb):
        def __init__(self, state: _State) -> None:
            super().__init__(state)
            self.commit_calls = 0
            self.flush_calls = 0

        async def commit(self) -> None:
            self.commit_calls += 1

        async def flush(self) -> None:
            self.flush_calls += 1

    db = _TrackingDb(state)

    async def _load_definition(**_kwargs):
        return state.definition

    async def _load_package(**_kwargs):
        return state.package

    async def _load_approval(**_kwargs):
        return state.approval

    async def _load_activation(**_kwargs):
        return state.activation

    async def _load_preview(**_kwargs):
        return state.preview

    async def _status(**_kwargs):
        return CommissionedControlPlaneStatusResponse(
            campaign_id=state.campaign_id,
            version=1,
            state="READY",
            generated_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
        )

    async def _load_package_payload(**_kwargs):
        return {"package": {"package_id": state.package.package_id}}

    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _SessionContext(db))
    monkeypatch.setattr(service, "_load_campaign_definition_by_identity", _load_definition)
    monkeypatch.setattr(service, "_load_latest_forced_canonical_package", _load_package)
    monkeypatch.setattr(service, "_load_latest_approval_for_package", _load_approval)
    monkeypatch.setattr(service, "_load_activation_for_package", _load_activation)
    monkeypatch.setattr(service, "_load_preview_for_package_row", _load_preview)
    monkeypatch.setattr(service, "_get_commissioned_control_plane_status", _status)
    monkeypatch.setattr(service, "get_canonical_preview_package", _load_package_payload)

    payload = await service.canonical_proving_commission_status(
        campaign_id=state.campaign_id,
        campaign_version=1,
        paper_account_id=state.paper_account_id,
        live_trading_profile_id=state.profile_id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
    )

    assert payload["read_only"] is True
    assert payload["no_execution"] is True
    assert db.commit_calls == 0
    assert db.flush_calls == 0