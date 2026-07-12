from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.services.autonomous_cycle.contracts import AutonomousCycleRequest, ReconciliationStatus, RiskEvaluationSummary
from app.services.autonomous_cycle.orchestrator import run_autonomous_preview_cycle
from app.services.strategies.identity import build_strategy_identity


class _FakeDb:
    def __init__(self) -> None:
        self.cycles_by_key: dict[str, AutonomousCycleRun] = {}
        self.connection = None
        self.added: list[object] = []
        self.authorizations: list[dict[str, object]] = []
        self.enforce_authorization_rows = False

    def add(self, item: object) -> None:
        if isinstance(item, AutonomousCycleRun):
            if getattr(item, "cycle_id", None) is None:
                item.cycle_id = uuid.uuid4()
            self.cycles_by_key[item.idempotency_key] = item
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, _item: object) -> None:
        return None

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM autonomous_cycle_runs" in sql and "idempotency_key" in sql:
            return next(iter(self.cycles_by_key.values()), None)
        if "count(*)" in sql and "FROM live_crypto_orders" in sql:
            return 0
        if "FROM autonomous_capital_mandate_authorizations" in sql:
            if not self.enforce_authorization_rows:
                return uuid.uuid4()

            compiled = statement.compile()
            params = compiled.params
            uuid_values = [value for value in params.values() if isinstance(value, uuid.UUID)]
            datetime_values = [value for value in params.values() if isinstance(value, datetime)]
            mandate_id = uuid_values[0] if uuid_values else None
            mandate_version_id = uuid_values[1] if len(uuid_values) > 1 else None
            observed_at = datetime_values[0] if datetime_values else None

            for item in self.authorizations:
                if mandate_id is not None and item.get("mandate_id") != mandate_id:
                    continue
                if mandate_version_id is not None and item.get("mandate_version_id") != mandate_version_id:
                    continue
                if item.get("authorization_state") != "AUTHORIZED":
                    continue
                if item.get("revoked_at") is not None:
                    continue
                expires_at = item.get("expires_at")
                if observed_at is not None and isinstance(expires_at, datetime) and expires_at <= observed_at:
                    continue
                return item.get("mandate_authorization_id", uuid.uuid4())

            return None
        if "FROM assets" in sql:
            return None
        return None

    async def get(self, _cls, _key):
        return self.connection


def _async_return(value):
    async def _inner(**_kwargs):
        return value

    return _inner


def _mandate(*, status: str = "ACTIVE"):
    return SimpleNamespace(
        mandate_id=uuid.uuid4(),
        owner_actor_id="operator:owner",
        status=status,
        autonomy_level="LEVEL_2",
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=42,
    )


def _version(*, allowed_order_sides: list[str] | None = None):
    return SimpleNamespace(
        mandate_version_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(),
        version_number=1,
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("10"),
        max_daily_deployed_usd=Decimal("10"),
        max_daily_realized_loss_usd=Decimal("3"),
        max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=2,
        position_limit=1,
        price_evidence_max_age_seconds=60,
        max_slippage_bps=Decimal("25"),
        max_fee_bps=Decimal("10"),
        allowed_products=["BTC-USD"],
        allowed_order_sides=allowed_order_sides or ["BUY", "SELL", "HOLD"],
        allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
        approval_policy="MANDATE_ALLOWED",
        is_authorized=True,
        is_active=True,
    )


def _version_with_identity(*, mandate_id: uuid.UUID, version_number: int, identity: str, is_authorized: bool = True, is_active: bool = True):
    version = _version(allowed_order_sides=["BUY", "SELL", "HOLD"])
    version.mandate_id = mandate_id
    version.version_number = version_number
    version.allowed_strategy_versions = [identity]
    version.is_authorized = is_authorized
    version.is_active = is_active
    return version


def _market_tuple():
    return (
        SimpleNamespace(
            evidence_id=uuid.uuid4(),
            provider="kraken_spot",
            product_id="BTC-USD",
            bid=Decimal("30000"),
            ask=Decimal("30001"),
            observed_at=datetime.now(timezone.utc),
        ),
        Decimal("30000"),
        0,
    )


def _patch_happy_path(monkeypatch: pytest.MonkeyPatch, mandate, version, *, action: str, risk_verdict: str = "ACCEPTED") -> None:
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._reconcile_state",
        _async_return(ReconciliationStatus(True, True, 0, 0, False, ())),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.load_current_execution_price_evidence", _async_return(_market_tuple()))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._evaluate_risk",
        _async_return(RiskEvaluationSummary(risk_verdict=risk_verdict, risk_event_id=uuid.uuid4(), reason_code=None)),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._persist_decision_intelligence", _async_return(uuid.uuid4()))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.evaluate_and_record_mandate", _async_return(SimpleNamespace(evaluation_id=uuid.uuid4())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _async_return(SimpleNamespace(crypto_order_preview_id=uuid.uuid4())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})


def _authorized_row(*, mandate_id: uuid.UUID, mandate_version_id: uuid.UUID, revoked_at: datetime | None = None, expires_at: datetime | None = None) -> dict[str, object]:
    return {
        "mandate_authorization_id": uuid.uuid4(),
        "mandate_id": mandate_id,
        "mandate_version_id": mandate_version_id,
        "authorization_state": "AUTHORIZED",
        "revoked_at": revoked_at,
        "expires_at": expires_at,
    }


@pytest.mark.asyncio
async def test_cycle_buy_generates_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="ACTIVE")
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")
    _patch_happy_path(monkeypatch, mandate, version, action="BUY")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="buy-cycle-1"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "BUY"
    assert result.preview_id is not None


@pytest.mark.asyncio
async def test_cycle_buy_generates_preview_for_operator_review_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="ACTIVE")
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_OPERATOR_REVIEW", provider="kraken_spot", environment="production")
    _patch_happy_path(monkeypatch, mandate, version, action="BUY")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="buy-cycle-operator-review"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "BUY"
    assert result.preview_id is not None


@pytest.mark.asyncio
async def test_cycle_accepts_authorized_exact_version_even_when_version_flag_false(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            expires_at=datetime.now(timezone.utc).replace(year=2099),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")
    _patch_happy_path(monkeypatch, mandate, version, action="BUY")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="auth-row-canonical"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "BUY"
    assert result.preview_id is not None
    assert result.diagnostics.failure_reason is None


@pytest.mark.asyncio
async def test_cycle_selects_latest_canonical_governing_version_and_keeps_legacy_version_historical(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    legacy_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.0")
    canonical_identity = build_strategy_identity(slug="ma_crossover", module_version="1.0.1")
    version_1 = _version_with_identity(mandate_id=mandate.mandate_id, version_number=1, identity=legacy_identity, is_authorized=False, is_active=False)
    version_2 = _version_with_identity(mandate_id=mandate.mandate_id, version_number=2, identity=canonical_identity, is_authorized=False, is_active=False)
    db.authorizations = [
        _authorized_row(mandate_id=mandate.mandate_id, mandate_version_id=version_1.mandate_version_id, expires_at=datetime.now(timezone.utc).replace(year=2099)),
        _authorized_row(mandate_id=mandate.mandate_id, mandate_version_id=version_2.mandate_version_id, expires_at=datetime.now(timezone.utc).replace(year=2099)),
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version_2, version_1]))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._reconcile_state", _async_return(ReconciliationStatus(True, True, 0, 0, False, ())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.load_current_execution_price_evidence", _async_return(_market_tuple()))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._evaluate_risk", _async_return(RiskEvaluationSummary(risk_verdict="ACCEPTED", risk_event_id=uuid.uuid4(), reason_code=None)))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator._persist_decision_intelligence", _async_return(uuid.uuid4()))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.evaluate_and_record_mandate", _async_return(SimpleNamespace(evaluation_id=uuid.uuid4())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _async_return(SimpleNamespace(crypto_order_preview_id=uuid.uuid4())))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="governing-version-selection"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is not None
    assert result.mandate_version_id == version_2.mandate_version_id


@pytest.mark.asyncio
async def test_cycle_holds_when_exact_version_authorization_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = []
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="missing-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
async def test_cycle_holds_when_authorization_is_for_another_version(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=uuid.uuid4(),
            expires_at=datetime.now(timezone.utc).replace(year=2099),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="other-version-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
async def test_cycle_holds_when_exact_version_authorization_is_revoked(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            revoked_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc).replace(year=2099),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="revoked-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
async def test_cycle_holds_when_exact_version_authorization_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    db.enforce_authorization_rows = True
    mandate = _mandate(status="ACTIVE")
    version = _version()
    version.is_authorized = False
    db.authorizations = [
        _authorized_row(
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            expires_at=datetime.now(timezone.utc).replace(year=2000),
        )
    ]
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="expired-auth"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "active_mandate_policy_requires_authorized_version"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PAUSED", "REVOKED", "EXPIRED", "DRAFT"])
async def test_cycle_non_active_mandate_finishes_hold(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    db = _FakeDb()
    mandate = _mandate(status=status)
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed=f"status-{status}"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "HOLD"
    assert result.preview_id is None
    assert "mandate_status" in (result.diagnostics.failure_reason or "")


@pytest.mark.asyncio
async def test_provider_readiness_failure_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="NOT_READY", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="not-ready"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "provider_not_ready"


@pytest.mark.asyncio
async def test_reconciliation_failure_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._reconcile_state",
        _async_return(ReconciliationStatus(True, True, 1, 0, False, ("CHECK_FAILED:unresolved_live_order_exists",))),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="reconcile-fail"),
    )

    assert result.state == "COMPLETE"
    assert result.diagnostics.failure_reason == "reconciliation_not_ready"


@pytest.mark.asyncio
async def test_hold_proposal_does_not_generate_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="HOLD", risk_verdict="NOT_EVALUATED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="HOLD", idempotency_seed="hold-1"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "HOLD"
    assert result.preview_id is None


@pytest.mark.asyncio
async def test_sell_proposal_generates_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version(allowed_order_sides=["BUY", "SELL", "HOLD"])
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="SELL", risk_verdict="ACCEPTED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="SELL", idempotency_seed="sell-1"),
    )

    assert result.state == "COMPLETE"
    assert result.proposed_action == "SELL"
    assert result.preview_id is not None


@pytest.mark.asyncio
async def test_mandate_rejection_blocks_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version(allowed_order_sides=["HOLD"])
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))
    monkeypatch.setattr(
        "app.services.autonomous_cycle.orchestrator._reconcile_state",
        _async_return(ReconciliationStatus(True, True, 0, 0, False, ())),
    )
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.load_current_execution_price_evidence", _async_return(_market_tuple()))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_exchange_provider", lambda _provider: object())
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_decrypted_credentials_for_connection", lambda _c: {"x": "y"})

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="scope-reject"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.diagnostics.failure_reason == "side_not_allowed"


@pytest.mark.asyncio
async def test_risk_rejection_blocks_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY", risk_verdict="REJECTED")

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="risk-reject-1"),
    )

    assert result.state == "COMPLETE"
    assert result.preview_id is None
    assert result.risk_verdict == "REJECTED"


@pytest.mark.asyncio
async def test_idempotent_repeated_cycle_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    calls = {"preview": 0}

    async def _preview(**_kwargs):
        calls["preview"] += 1
        return SimpleNamespace(crypto_order_preview_id=uuid.uuid4())

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.create_crypto_order_preview", _preview)

    req = AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="idem-1")
    first = await run_autonomous_preview_cycle(db=db, request=req)
    second = await run_autonomous_preview_cycle(db=db, request=req)

    assert first.replayed is False
    assert second.replayed is True
    assert calls["preview"] == 1


@pytest.mark.asyncio
async def test_audit_event_written_for_completed_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")

    await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="audit-1"),
    )

    assert any(isinstance(item, AuditLog) and item.action == "AUTONOMOUS_CYCLE_COMPLETED" for item in db.added)


@pytest.mark.asyncio
async def test_cycle_diagnostics_include_termination_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="PAUSED")
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.get_mandate", _async_return(mandate))
    monkeypatch.setattr("app.services.autonomous_cycle.orchestrator.list_mandate_versions", _async_return([version]))

    result = await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", idempotency_seed="diag-1"),
    )

    assert result.diagnostics.termination_stage == "validate_mandate"
    assert result.diagnostics.duration_ms >= 0


@pytest.mark.asyncio
async def test_no_submission_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate()
    version = _version()
    db.connection = SimpleNamespace(last_readiness_verdict="READY_FOR_PREVIEW", provider="kraken_spot", environment="production")

    called = {"submit": 0}

    def _submit_fail(*_args, **_kwargs):
        called["submit"] += 1
        raise AssertionError("submission path must not be called")

    _patch_happy_path(monkeypatch, mandate, version, action="BUY")
    monkeypatch.setattr("app.services.live_crypto_orders.service.submit", _submit_fail)

    await run_autonomous_preview_cycle(
        db=db,
        request=AutonomousCycleRequest(mandate_id=mandate.mandate_id, actor="operator:owner", forced_action="BUY", idempotency_seed="nosubmit-1"),
    )

    assert called["submit"] == 0
