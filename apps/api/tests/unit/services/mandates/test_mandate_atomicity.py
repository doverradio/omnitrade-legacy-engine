from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.models.audit_log import AuditLog
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
)
from app.services.mandates import lifecycle
from app.services.mandates import evidence


class _FakeDb:
    def __init__(self, *, fail_on_add_number: int | None = None) -> None:
        self.fail_on_add_number = fail_on_add_number
        self.add_calls = 0
        self.added: list[object] = []
        self.commits = 0
        self.flushes = 0
        self.refreshed: list[object] = []
        self._objects: dict[tuple[type, object], object] = {}
        self.authorizations_by_idempotency: dict[str, object] = {}

    def register_get(self, cls: type, key: object, value: object) -> None:
        self._objects[(cls, key)] = value

    def add(self, item: object) -> None:
        self.add_calls += 1
        if self.fail_on_add_number is not None and self.add_calls == self.fail_on_add_number:
            raise RuntimeError("audit write failed")
        self.added.append(item)

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, item: object) -> None:
        self.refreshed.append(item)

    async def scalar(self, statement):
        sql = str(statement)
        if "max(autonomous_capital_mandate_versions.version_number)" in sql:
            return None
        if "FROM autonomous_capital_mandate_authorizations" in sql:
            params = statement.compile().params
            idempotency_key = next((value for value in params.values() if isinstance(value, str)), None)
            if idempotency_key is not None:
                return self.authorizations_by_idempotency.get(idempotency_key)
        return None

    async def scalars(self, _statement):
        return []

    async def get(self, cls: type, key: object):
        return self._objects.get((cls, key))


def _mandate(*, status: str = "PENDING_AUTHORIZATION") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
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
        capital_campaign_id=101,
        authorized_at=None,
        activated_at=None,
        paused_at=None,
        expires_at=None,
        revoked_at=None,
        updated_at=now,
    )


def _version(*, mandate_id: uuid.UUID, version_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        mandate_version_id=version_id or uuid.uuid4(),
        mandate_id=mandate_id,
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
        price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("25"),
        max_fee_bps=Decimal("10"),
        allowed_products=["BTC-USD"],
        allowed_order_sides=["BUY", "SELL", "HOLD"],
        allowed_strategy_versions=["strategy.v1"],
        approval_policy="MANDATE_ALLOWED",
        is_authorized=False,
        is_active=False,
        created_at=datetime.now(timezone.utc),
        authorized_at=None,
    )


@pytest.mark.asyncio
async def test_create_version_audit_failure_prevents_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(fail_on_add_number=2)
    mandate = _mandate(status="DRAFT")

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    monkeypatch.setattr(lifecycle, "get_mandate", _get_mandate)

    request = MandateVersionCreateRequest(
        mandate_id=mandate.mandate_id,
        actor="operator:owner",
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("10"),
        max_daily_deployed_usd=Decimal("10"),
        max_daily_realized_loss_usd=Decimal("3"),
        max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=2,
        position_limit=1,
        price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("25"),
        max_fee_bps=Decimal("10"),
        allowed_products=("BTC-USD",),
        allowed_order_sides=("BUY", "SELL", "HOLD"),
        allowed_strategy_versions=("strategy.v1",),
        entry_policy={},
        exit_policy={},
        cooldown_policy={},
        operating_schedule={},
        approval_policy="MANDATE_ALLOWED",
        reconciliation_policy={},
        kill_switch_policy={},
        owner_acknowledgements={"accepted": True},
        authorization_evidence_summary={"source": "owner"},
        idempotency_key="v-atomic-1",
        audit_correlation_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        await lifecycle.create_mandate_version(db=db, request=request)

    assert db.commits == 0
    assert any(not isinstance(item, AuditLog) for item in db.added)


@pytest.mark.asyncio
async def test_authorization_write_persists_correlation_and_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="PENDING_AUTHORIZATION")
    version = _version(mandate_id=mandate.mandate_id)
    db.register_get(type(version), version.mandate_version_id, version)

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    async def _hydrate(*, db, authorization):
        _ = db
        return SimpleNamespace(
            mandate_authorization_id=authorization.mandate_authorization_id,
            mandate_id=authorization.mandate_id,
            mandate_version_id=authorization.mandate_version_id,
            mandate_version_number=1,
            autonomy_level="LEVEL_2",
            authorization_state=authorization.authorization_state,
            approval_result=authorization.approval_result,
            authorized_by_actor_id=authorization.authorized_by_actor_id,
            audit_correlation_id=authorization.audit_correlation_id,
            recorded_at=datetime.now(timezone.utc),
            expires_at=None,
            revoked_at=None,
        )

    monkeypatch.setattr(lifecycle, "get_mandate", _get_mandate)
    monkeypatch.setattr(lifecycle, "_hydrate_authorization_model", _hydrate)

    from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion

    db.register_get(AutonomousCapitalMandateVersion, version.mandate_version_id, version)

    correlation_id = uuid.uuid4()
    request = MandateAuthorizationRequest(
        mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id,
        actor="operator:owner",
        authorization_method="owner_signature",
        owner_acknowledgements={"accepted": True},
        authorization_evidence={"signature": "hash"},
        deterministic_explanation={"reason": "explicit_owner_authorization"},
        expires_at=None,
        idempotency_key="auth-atomic-1",
        audit_correlation_id=correlation_id,
    )

    result = await lifecycle.authorize_mandate_version(db=db, request=request)

    assert db.commits == 1
    assert result.audit_correlation_id == correlation_id
    audit_entries = [item for item in db.added if isinstance(item, AuditLog)]
    assert len(audit_entries) == 1
    assert audit_entries[0].after_state["audit_correlation_id"] == str(correlation_id)


@pytest.mark.asyncio
async def test_authorization_idempotent_duplicate_returns_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="PENDING_AUTHORIZATION")
    version = _version(mandate_id=mandate.mandate_id)

    existing_authorization = SimpleNamespace(
        mandate_authorization_id=uuid.uuid4(),
        mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id,
        authorization_state="AUTHORIZED",
        approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
        authorized_by_actor_id="operator:owner",
        audit_correlation_id=uuid.uuid4(),
        recorded_at=datetime.now(timezone.utc),
        expires_at=None,
        revoked_at=None,
    )
    db.authorizations_by_idempotency["auth-idempotent-1"] = existing_authorization

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    async def _hydrate(*, db, authorization):
        _ = db
        return SimpleNamespace(
            mandate_authorization_id=authorization.mandate_authorization_id,
            mandate_id=authorization.mandate_id,
            mandate_version_id=authorization.mandate_version_id,
            mandate_version_number=1,
            autonomy_level="LEVEL_2",
            authorization_state=authorization.authorization_state,
            approval_result=authorization.approval_result,
            authorized_by_actor_id=authorization.authorized_by_actor_id,
            audit_correlation_id=authorization.audit_correlation_id,
            recorded_at=authorization.recorded_at,
            expires_at=authorization.expires_at,
            revoked_at=authorization.revoked_at,
        )

    monkeypatch.setattr(lifecycle, "get_mandate", _get_mandate)
    monkeypatch.setattr(lifecycle, "_hydrate_authorization_model", _hydrate)

    from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion

    db.register_get(AutonomousCapitalMandateVersion, version.mandate_version_id, version)

    request = MandateAuthorizationRequest(
        mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id,
        actor="operator:owner",
        authorization_method="owner_signature",
        owner_acknowledgements={"accepted": True},
        authorization_evidence={"signature": "hash"},
        deterministic_explanation={"reason": "explicit_owner_authorization"},
        expires_at=None,
        idempotency_key="auth-idempotent-1",
        audit_correlation_id=uuid.uuid4(),
    )

    result = await lifecycle.authorize_mandate_version(db=db, request=request)

    assert result.mandate_authorization_id == existing_authorization.mandate_authorization_id
    assert db.commits == 0
    assert len([item for item in db.added if isinstance(item, AuditLog)]) == 0


@pytest.mark.asyncio
async def test_authorization_audit_failure_prevents_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(fail_on_add_number=2)
    mandate = _mandate(status="PENDING_AUTHORIZATION")
    version = _version(mandate_id=mandate.mandate_id)

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    monkeypatch.setattr(lifecycle, "get_mandate", _get_mandate)

    from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion

    db.register_get(AutonomousCapitalMandateVersion, version.mandate_version_id, version)

    request = MandateAuthorizationRequest(
        mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id,
        actor="operator:owner",
        authorization_method="owner_signature",
        owner_acknowledgements={"accepted": True},
        authorization_evidence={"signature": "hash"},
        deterministic_explanation={"reason": "explicit_owner_authorization"},
        expires_at=None,
        idempotency_key="auth-atomic-fail-1",
        audit_correlation_id=uuid.uuid4(),
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        await lifecycle.authorize_mandate_version(db=db, request=request)

    assert db.commits == 0


@pytest.mark.asyncio
async def test_lifecycle_action_audit_failure_prevents_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(fail_on_add_number=1)
    mandate = _mandate(status="ACTIVE")

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    monkeypatch.setattr(lifecycle, "get_mandate", _get_mandate)

    request = MandateLifecycleActionRequest(
        mandate_id=mandate.mandate_id,
        actor="operator:owner",
        action="PAUSE",
        reason="operator_pause",
        idempotency_key="life-atomic-1",
        audit_correlation_id=uuid.uuid4(),
        software_build_version="build-1",
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        await lifecycle.apply_mandate_lifecycle_action(db=db, request=request)

    assert db.commits == 0


@pytest.mark.asyncio
async def test_evaluation_write_persists_correlation_and_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    mandate = _mandate(status="ACTIVE")
    version = _version(mandate_id=mandate.mandate_id)

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    async def _resolve_version(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return version, True

    def _decision_stub(*, mandate, version, request):
        _ = mandate
        _ = version
        _ = request
        return SimpleNamespace(
            result="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            reason_code="authorized_under_active_mandate",
            passed_checks=("owner_match",),
            failed_checks=(),
            deterministic_explanation=("CHECK_PASSED:owner_match",),
        )

    monkeypatch.setattr(evidence, "get_mandate", _get_mandate)
    monkeypatch.setattr(evidence, "_resolve_version_for_evaluation", _resolve_version)
    monkeypatch.setattr(evidence, "evaluate_mandate_eligibility", _decision_stub)

    correlation_id = uuid.uuid4()
    request = evidence.MandateEvaluationWriteRequest(
        mandate_id=mandate.mandate_id,
        actor="operator:owner",
        strategy_version="strategy.v1",
        product="BTC-USD",
        side="BUY",
        proposed_notional_usd=Decimal("5"),
        current_open_exposure_usd=Decimal("0"),
        daily_deployed_usd=Decimal("0"),
        daily_realized_loss_usd=Decimal("0"),
        campaign_drawdown_usd=Decimal("0"),
        consecutive_losses=0,
        current_position_count=0,
        risk_verdict="ACCEPTED",
        evidence_age_seconds=5,
        kill_switch_engaged=False,
        observed_at=datetime.now(timezone.utc),
        decision_id=uuid.uuid4(),
        request_context={"source": "unit_test"},
        idempotency_key="eval-atomic-1",
        audit_correlation_id=correlation_id,
        software_build_version="build-1",
    )

    result = await evidence.evaluate_and_record_mandate(db=db, request=request)

    assert db.commits == 1
    assert result.audit_correlation_id == correlation_id
    audit_entries = [item for item in db.added if isinstance(item, AuditLog)]
    assert len(audit_entries) == 1
    assert audit_entries[0].after_state["audit_correlation_id"] == str(correlation_id)


@pytest.mark.asyncio
async def test_evaluation_audit_failure_prevents_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb(fail_on_add_number=2)
    mandate = _mandate(status="ACTIVE")
    version = _version(mandate_id=mandate.mandate_id)

    async def _get_mandate(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return mandate

    async def _resolve_version(*, db, mandate_id):
        _ = db
        _ = mandate_id
        return version, True

    def _decision_stub(*, mandate, version, request):
        _ = mandate
        _ = version
        _ = request
        return SimpleNamespace(
            result="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            reason_code="authorized_under_active_mandate",
            passed_checks=("owner_match",),
            failed_checks=(),
            deterministic_explanation=("CHECK_PASSED:owner_match",),
        )

    monkeypatch.setattr(evidence, "get_mandate", _get_mandate)
    monkeypatch.setattr(evidence, "_resolve_version_for_evaluation", _resolve_version)
    monkeypatch.setattr(evidence, "evaluate_mandate_eligibility", _decision_stub)

    request = evidence.MandateEvaluationWriteRequest(
        mandate_id=mandate.mandate_id,
        actor="operator:owner",
        strategy_version="strategy.v1",
        product="BTC-USD",
        side="BUY",
        proposed_notional_usd=Decimal("5"),
        current_open_exposure_usd=Decimal("0"),
        daily_deployed_usd=Decimal("0"),
        daily_realized_loss_usd=Decimal("0"),
        campaign_drawdown_usd=Decimal("0"),
        consecutive_losses=0,
        current_position_count=0,
        risk_verdict="ACCEPTED",
        evidence_age_seconds=5,
        kill_switch_engaged=False,
        observed_at=datetime.now(timezone.utc),
        decision_id=None,
        request_context={},
        idempotency_key="eval-atomic-2",
        audit_correlation_id=uuid.uuid4(),
        software_build_version=None,
    )

    with pytest.raises(RuntimeError, match="audit write failed"):
        await evidence.evaluate_and_record_mandate(db=db, request=request)

    assert db.commits == 0
