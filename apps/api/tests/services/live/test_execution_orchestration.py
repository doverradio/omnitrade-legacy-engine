from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.live.broker_adapters import (
    BrokerAdapterContract,
    ProviderBrokerRequestEnvelope,
)
from app.services.live.contracts import (
    LiveExecutionOrchestrationRequest,
    LiveRiskVerificationResult,
)
from app.services.live.execution_orchestration import orchestrate_live_execution


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(
        self,
        *,
        profiles: list[LiveTradingProfile],
        approval_events: list[LiveApprovalEvent],
    ) -> None:
        self.profiles = profiles
        self.approval_events = approval_events
        self.execution_events: list[LiveExecutionEvent] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_execution_events" in sql and "idempotency_key_1" in params:
            key = params["idempotency_key_1"]
            for item in self.execution_events:
                if item.idempotency_key == key:
                    return item
            return None

        if "max(live_execution_events.sequence_number)" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            seqs = [item.sequence_number for item in self.execution_events if item.live_trading_profile_id == profile_id]
            return max(seqs) if seqs else None

        if "FROM live_trading_profiles" in sql:
            profile_id = params.get("id_1")
            for profile in self.profiles:
                if profile.id == profile_id:
                    return profile
            return None

        if "FROM live_approval_events" in sql and "ORDER BY" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            checkpoint_type = params.get("checkpoint_type_1")
            rows = [
                item
                for item in self.approval_events
                if item.live_trading_profile_id == profile_id and item.checkpoint_type == checkpoint_type
            ]
            if not rows:
                return None
            return sorted(rows, key=lambda x: x.sequence_number, reverse=True)[0]

        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveExecutionEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.execution_events.append(obj)

    async def flush(self) -> None:
        return None


class _FakeAdapter(BrokerAdapterContract):
    def __init__(self) -> None:
        self.provider_name = "paper-sim"
        self.calls = 0
        self.last_correlation_id: str | None = None
        self.last_risk_decision_id: uuid.UUID | None = None
        self.last_approval_event_id: uuid.UUID | None = None

    def build_provider_order_request(self, *, request):
        self.calls += 1
        self.last_correlation_id = request.orchestration_ids.audit_correlation_id
        self.last_risk_decision_id = request.orchestration_ids.risk_decision_id
        self.last_approval_event_id = request.orchestration_ids.approval_event_id
        return ProviderBrokerRequestEnvelope(
            orchestration_ids=request.orchestration_ids,
            idempotency=request.idempotency,
            adapter_request_id=request.adapter_request_id,
            provider_name=self.provider_name,
            endpoint_operation="submit_order",
            payload={"symbol": request.symbol, "side": request.side},
            created_at=datetime.now(timezone.utc),
        )

    def normalize_provider_order_status(self, *, response, client_order_id):
        raise NotImplementedError

    def normalize_provider_fill(self, *, response, client_order_id):
        raise NotImplementedError

    def normalize_provider_rejection(self, *, response, client_order_id):
        raise NotImplementedError

    def normalize_provider_error(self, *, response, category, error_code, message, details=None):
        raise NotImplementedError


def _profile() -> LiveTradingProfile:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    return LiveTradingProfile(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        operating_mode="live",
        lifecycle_state="enabled",
        approval_state="approved",
        live_opt_in=True,
        human_approval_recorded=True,
        paper_default_mode=True,
        governance_approved=True,
        risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={"seed": "test"},
        created_at=now,
        updated_at=now,
    )


def _approval_event(profile_id: uuid.UUID, *, expires_in_minutes: int = 30) -> LiveApprovalEvent:
    now = datetime.now(timezone.utc)
    return LiveApprovalEvent(
        id=uuid.uuid4(),
        idempotency_key="approval-key",
        event_hash="approval-hash",
        live_trading_profile_id=profile_id,
        sequence_number=1,
        event_type="approval_granted",
        checkpoint_type="first_live_enablement",
        approval_state="approved",
        approver_id="human.approver",
        approver_role="risk_owner",
        rationale="approved",
        approval_scope={"scope": ["live_enablement"]},
        expires_at=now + timedelta(minutes=expires_in_minutes),
        renewal_condition="renew",
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )


def _request(profile_id: uuid.UUID, approval_event_id: uuid.UUID, **overrides: Any) -> LiveExecutionOrchestrationRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": profile_id,
        "provider_name": "paper-sim",
        "broker_account_ref": "acct-1",
        "adapter_request_id": "adapter-req-1",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "limit",
        "quantity": "1.25",
        "limit_price": "200.5",
        "stop_price": None,
        "time_in_force": "day",
        "risk_decision_id": uuid.uuid4(),
        "approval_event_id": approval_event_id,
        "audit_correlation_id": "audit-correlation-1",
        "requested_by": "operator",
        "provenance_metadata": {"ticket": "LIVE-95"},
        "idempotency_key": "exec-intent-key-1",
    }
    payload.update(overrides)
    return LiveExecutionOrchestrationRequest(**payload)


@pytest.mark.asyncio
async def test_orchestrator_prepares_execution_intent_when_all_gates_pass() -> None:
    profile = _profile()
    approval_event = _approval_event(profile.id)
    session = _FakeSession(profiles=[profile], approval_events=[approval_event])
    adapter = _FakeAdapter()

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=True, reason=None)

    result = await orchestrate_live_execution(
        db=session,
        request=_request(profile.id, approval_event.id),
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )

    assert result.accepted is True
    assert result.status == "prepared"
    assert adapter.calls == 1
    assert adapter.last_correlation_id == "audit-correlation-1"
    assert adapter.last_approval_event_id == approval_event.id
    assert len(session.execution_events) == 1
    assert session.execution_events[0].event_type == "execution_intent_created"


@pytest.mark.asyncio
async def test_orchestrator_fails_closed_when_approval_is_expired() -> None:
    profile = _profile()
    expired_approval = _approval_event(profile.id, expires_in_minutes=-1)
    session = _FakeSession(profiles=[profile], approval_events=[expired_approval])
    adapter = _FakeAdapter()

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=True, reason=None)

    result = await orchestrate_live_execution(
        db=session,
        request=_request(profile.id, expired_approval.id, idempotency_key="exec-intent-key-2"),
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )

    assert result.accepted is False
    assert result.status == "blocked"
    assert result.reason == "approval_expired"
    assert adapter.calls == 0
    assert session.execution_events[0].event_type == "execution_blocked"


@pytest.mark.asyncio
async def test_orchestrator_fails_closed_when_risk_verification_fails() -> None:
    profile = _profile()
    approval_event = _approval_event(profile.id)
    session = _FakeSession(profiles=[profile], approval_events=[approval_event])
    adapter = _FakeAdapter()

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=False, reason="risk_rejected")

    result = await orchestrate_live_execution(
        db=session,
        request=_request(profile.id, approval_event.id, idempotency_key="exec-intent-key-3"),
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )

    assert result.accepted is False
    assert result.reason == "risk_rejected"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_orchestrator_requires_live_enabled_operating_mode() -> None:
    profile = _profile()
    profile.operating_mode = "paper"
    profile.lifecycle_state = "approved"
    approval_event = _approval_event(profile.id)
    session = _FakeSession(profiles=[profile], approval_events=[approval_event])
    adapter = _FakeAdapter()

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=True, reason=None)

    result = await orchestrate_live_execution(
        db=session,
        request=_request(profile.id, approval_event.id, idempotency_key="exec-intent-key-4"),
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )

    assert result.accepted is False
    assert result.reason == "approved_live_operating_mode_required"
    assert adapter.calls == 0


@pytest.mark.asyncio
async def test_orchestrator_is_idempotent_and_replays_without_adapter_call() -> None:
    profile = _profile()
    approval_event = _approval_event(profile.id)
    session = _FakeSession(profiles=[profile], approval_events=[approval_event])
    adapter = _FakeAdapter()

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=True, reason=None)

    req = _request(profile.id, approval_event.id, idempotency_key="exec-intent-key-5")

    first = await orchestrate_live_execution(
        db=session,
        request=req,
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )
    second = await orchestrate_live_execution(
        db=session,
        request=req,
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )

    assert first.status == "prepared"
    assert second.status == "replayed"
    assert len(session.execution_events) == 1
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_orchestrator_fails_closed_when_adapter_missing() -> None:
    profile = _profile()
    approval_event = _approval_event(profile.id)
    session = _FakeSession(profiles=[profile], approval_events=[approval_event])

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=True, reason=None)

    result = await orchestrate_live_execution(
        db=session,
        request=_request(profile.id, approval_event.id, provider_name="missing", idempotency_key="exec-intent-key-6"),
        adapters={},
        verify_risk_decision=risk_verifier,
    )

    assert result.accepted is False
    assert result.reason == "adapter_not_registered"
    assert session.execution_events[0].event_type == "execution_blocked"
