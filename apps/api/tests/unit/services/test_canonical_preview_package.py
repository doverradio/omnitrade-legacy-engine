from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        expires_at=now + timedelta(minutes=5),
    )


def _approval_event(*, package_id: UUID, expires_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        approval_state="approved",
        checkpoint_type="bounded_proving_entry",
        approval_scope={"canonical_preview_package_id": str(package_id)},
        expires_at=expires_at,
    )


def _cycle(*, proposed_action: str = "OPEN_POSITION_PROPOSED", termination_stage: str | None = None, failure_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        cycle_id=uuid4(),
        started_at=datetime.now(timezone.utc),
        state="COMPLETE",
        termination_stage=termination_stage,
        failure_reason=failure_reason,
        proposed_action=proposed_action,
        cycle_context={
            "authoritative_composition": {
                "proposed_action": proposed_action,
                "selected_decision": {
                    "decision_kind": "BUY" if proposed_action == "OPEN_POSITION_PROPOSED" else "NO_ACTION",
                    "reason": "no_qualifying_candidate" if proposed_action == "NO_ACTION" else None,
                },
            }
        },
    )


def _create_request(*, campaign_id: UUID, profile: SimpleNamespace, idempotency_key: str = "pkg-1") -> cpp.CanonicalPreviewPackageCreateRequest:
    return cpp.CanonicalPreviewPackageCreateRequest(
        campaign_id=campaign_id,
        campaign_version=1,
        paper_account_id=profile.paper_account_id,
        live_trading_profile_id=profile.id,
        provider="kraken_spot",
        environment="production",
        product="BTC-USD",
        max_proposed_order_amount=Decimal("5"),
        actor="operator:human",
        idempotency_key=idempotency_key,
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
    cycle = _cycle()
    request = _create_request(campaign_id=campaign_id, profile=profile)

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}]}))
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _async_return(cycle))
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
async def test_create_canonical_preview_package_returns_hold_outcome_without_package(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    cycle = _cycle(proposed_action="NO_ACTION", termination_stage="hold_terminal")
    request = _create_request(campaign_id=campaign_id, profile=profile, idempotency_key="pkg-hold-1")

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}]}))
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _async_return(cycle))

    db = _FakeDb()
    result = await cpp.create_canonical_preview_package(db=db, request=request)

    assert result["outcome_code"] == "HOLD_NO_PACKAGE_CREATED"
    assert result["package"] is None
    assert result["reason_code"] == "canonical_action_hold"
    assert db.flush_calls == 0


@pytest.mark.asyncio
async def test_create_canonical_preview_package_hold_uses_latest_fresh_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    old_cycle_id = uuid4()
    fresh_cycle = _cycle(proposed_action="NO_ACTION", termination_stage="hold_terminal")
    request = _create_request(campaign_id=campaign_id, profile=profile, idempotency_key="pkg-hold-2")
    loaded_cycle_ids: list[str] = []

    async def _load_cycle(**kwargs):
        loaded_cycle_ids.append(str(kwargs["cycle_id"]))
        return fresh_cycle

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(
        cpp,
        "run_campaign_orchestration_preview_for_candle",
        _async_return({"cycles": [{"cycle_id": str(old_cycle_id)}, {"cycle_id": str(fresh_cycle.cycle_id)}]}),
    )
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _load_cycle)

    db = _FakeDb()
    result = await cpp.create_canonical_preview_package(db=db, request=request)

    assert loaded_cycle_ids == [str(fresh_cycle.cycle_id)]
    assert result["outcome_code"] == "HOLD_NO_PACKAGE_CREATED"
    assert result["campaign_cycle"]["cycle_id"] == str(fresh_cycle.cycle_id)
    assert result["package"] is None
    assert db.flush_calls == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_create_canonical_preview_package_executable_candidate_links_all_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    package_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    preview = _preview(package_id=package_id)
    strategy = SimpleNamespace(id=preview.strategy_id, module_version="strategy-v9")
    parameter_set = SimpleNamespace(id=preview.parameter_set_id, label="ps-v3")
    cycle = _cycle()
    request = _create_request(campaign_id=campaign_id, profile=profile, idempotency_key="pkg-exec-1")

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}]}))
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _async_return(cycle))
    monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(preview))
    monkeypatch.setattr(cpp, "_load_decision_record", _async_return(SimpleNamespace(decision_id=preview.decision_record_id)))
    monkeypatch.setattr(cpp, "_load_risk_event", _async_return(SimpleNamespace(id=preview.risk_event_id)))

    db = _FakeDb(scalar_values=[strategy, parameter_set])
    result = await cpp.create_canonical_preview_package(db=db, request=request)

    package = result["package"]
    assert result["readiness"]["ready"] is True
    assert package["package_state"] == "READY"
    assert package["decision_record_id"] == str(preview.decision_record_id)
    assert package["risk_event_id"] == str(preview.risk_event_id)
    assert package["crypto_order_preview_id"] == str(preview.crypto_order_preview_id)
    assert package["strategy_id"] == str(preview.strategy_id)
    assert package["strategy_version"] == "strategy-v9"
    assert package["parameter_set_id"] == str(preview.parameter_set_id)
    assert package["parameter_set_version"] == "ps-v3"
    assert package["market_evidence_identity"]["provider"] == "kraken_spot"
    assert package["market_evidence_identity"]["environment"] == "production"
    assert db.flush_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "mode"),
    [
        ("canonical_orchestration_cycle_missing", "orchestration_missing"),
        ("canonical_runtime_campaign_missing", "runtime_missing"),
        ("canonical_paper_account_missing", "paper_mismatch"),
        ("canonical_strategy_version_missing", "strategy_missing"),
        ("canonical_parameter_set_version_missing", "parameter_set_missing"),
        ("canonical_decision_record_id_missing", "decision_missing"),
        ("canonical_risk_event_id_missing", "risk_missing"),
        ("canonical_crypto_order_preview_id_missing", "preview_missing"),
        ("canonical_price_evidence_missing", "price_missing"),
        ("canonical_price_evidence_stale", "price_stale"),
        ("canonical_preview_expiration_missing", "preview_expiry_missing"),
        ("canonical_risk_approved_amount_missing", "amount_missing"),
    ],
)
async def test_create_canonical_preview_package_deterministic_failure_code_matrix(monkeypatch: pytest.MonkeyPatch, code: str, mode: str) -> None:
    campaign_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    cycle = _cycle()
    preview = _preview(package_id=uuid4())
    request = _create_request(campaign_id=campaign_id, profile=profile, idempotency_key=f"pkg-fail-{code}")

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}]}))
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _async_return(cycle))
    monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(preview))
    monkeypatch.setattr(cpp, "_load_decision_record", _async_return(SimpleNamespace(decision_id=preview.decision_record_id)))
    monkeypatch.setattr(cpp, "_load_risk_event", _async_return(SimpleNamespace(id=preview.risk_event_id)))

    if mode == "orchestration_missing":
        monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": []}))
    elif mode == "runtime_missing":
        monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(None))
    elif mode == "paper_mismatch":
        monkeypatch.setattr(cpp, "_load_profile", _async_return(SimpleNamespace(id=uuid4(), paper_account_id=uuid4())))
    elif mode == "strategy_missing":
        monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(SimpleNamespace(**{**preview.__dict__, "strategy_id": uuid4()})))
    elif mode == "parameter_set_missing":
        monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(SimpleNamespace(**{**preview.__dict__, "parameter_set_id": uuid4()})))
    elif mode == "decision_missing":
        monkeypatch.setattr(cpp, "_load_decision_record", _async_return(None))
    elif mode == "risk_missing":
        monkeypatch.setattr(cpp, "_load_risk_event", _async_return(None))
    elif mode == "preview_missing":
        monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(None))
    elif mode == "price_missing":
        monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(SimpleNamespace(**{**preview.__dict__, "created_at": None})))
    elif mode == "price_stale":
        monkeypatch.setattr(
            cpp,
            "_load_preview_for_package",
            _async_return(SimpleNamespace(**{**preview.__dict__, "expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc)})),
        )
    elif mode == "preview_expiry_missing":
        monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(SimpleNamespace(**{**preview.__dict__, "expires_at": None})))
    elif mode == "amount_missing":
        monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(SimpleNamespace(**{**preview.__dict__, "requested_amount": None})))

    scalar_values: list[object]
    if code == "canonical_strategy_version_missing":
        scalar_values = [None, SimpleNamespace(id=preview.parameter_set_id, label="baseline")]
    elif code == "canonical_parameter_set_version_missing":
        scalar_values = [SimpleNamespace(id=preview.strategy_id, module_version="v1"), None]
    else:
        scalar_values = [SimpleNamespace(id=preview.strategy_id, module_version="v1"), SimpleNamespace(id=preview.parameter_set_id, label="baseline")]

    db = _FakeDb(scalar_values=scalar_values)
    with pytest.raises(LookupError) as exc_info:
        await cpp.create_canonical_preview_package(db=db, request=request)

    assert code in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_canonical_preview_package_non_executable_decision_reports_code(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    cycle = _cycle(proposed_action="FAILED_CLOSED")
    cycle.cycle_context["authoritative_composition"]["selected_decision"]["decision_kind"] = "BUY"
    request = _create_request(campaign_id=campaign_id, profile=profile, idempotency_key="pkg-non-exec")

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}]}))
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _async_return(cycle))

    with pytest.raises(LookupError) as exc_info:
        await cpp.create_canonical_preview_package(db=_FakeDb(), request=request)

    assert "canonical_action_not_executable" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_canonical_preview_package_idempotent_retry_skips_new_orchestration(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    profile = _profile()
    request = _create_request(campaign_id=campaign_id, profile=profile, idempotency_key="pkg-retry-1")
    existing = SimpleNamespace(
        package_id=uuid4(),
        campaign_id=campaign_id,
        campaign_version=1,
        runtime_campaign_id=campaign_id,
        paper_account_id=profile.paper_account_id,
        live_trading_profile_id=profile.id,
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
        preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        package_state="READY",
        generated_at=datetime.now(timezone.utc),
        idempotency_key=request.idempotency_key,
        input_fingerprint=cpp._input_fingerprint(request),
        approval_event_id=None,
        dry_run_live_crypto_order_id=None,
        superseded_at=None,
        invalidated_reason=None,
    )

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(existing))

    async def _unexpected(**_kwargs):
        raise AssertionError("orchestration must not run for idempotent replay")

    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _unexpected)

    result = await cpp.create_canonical_preview_package(db=_FakeDb(), request=request)

    assert result["idempotent"] is True
    assert result["package"]["package_id"] == str(existing.package_id)


@pytest.mark.asyncio
async def test_create_canonical_preview_package_reports_deterministic_missing_preview_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id = uuid4()
    profile = _profile()
    runtime_campaign = _runtime_campaign(campaign_id=campaign_id)
    definition = _definition(campaign_id=campaign_id, campaign_version=1)
    cycle = _cycle()
    preview = _preview(package_id=uuid4())
    preview.decision_record_id = None
    preview.risk_event_id = None
    preview.strategy_id = None
    preview.parameter_set_id = None
    preview.expires_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
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
        idempotency_key="pkg-diag-1",
    )

    monkeypatch.setattr(cpp, "_load_package_by_idempotency", _async_return(None))
    monkeypatch.setattr(cpp, "_load_profile", _async_return(profile))
    monkeypatch.setattr(cpp, "_load_runtime_campaign", _async_return(runtime_campaign))
    monkeypatch.setattr(cpp, "_load_campaign_definition", _async_return(definition))
    monkeypatch.setattr(cpp, "run_campaign_orchestration_preview_for_candle", _async_return({"cycles": [{"cycle_id": str(cycle.cycle_id)}]}))
    monkeypatch.setattr(cpp, "_load_campaign_cycle", _async_return(cycle))
    monkeypatch.setattr(cpp, "_load_preview_for_package", _async_return(preview))

    db = _FakeDb()
    with pytest.raises(LookupError) as exc_info:
        await cpp.create_canonical_preview_package(db=db, request=request)

    message = str(exc_info.value)
    assert "canonical_decision_record_id_missing" in message
    assert "canonical_risk_event_id_missing" in message
    assert "canonical_strategy_id_missing" in message
    assert "canonical_parameter_set_id_missing" in message
    assert "canonical_price_evidence_stale" in message


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
