from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_resilience_event import LiveResilienceEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.live.broker_adapters import BrokerAdapterContract, ProviderBrokerRequestEnvelope
from app.services.live.contracts import (
    LiveExecutionOrchestrationRequest,
    LiveKillSwitchRequest,
    LiveOutageDetectionRequest,
    LiveRecoveryRequest,
    LiveRiskVerificationResult,
)
from app.services.live.execution_orchestration import orchestrate_live_execution
from app.services.live.resilience import (
    approve_live_recovery,
    engage_live_kill_switch,
    evaluate_live_submission_guard,
    record_live_broker_outage,
    request_live_recovery,
)


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
        approvals: list[LiveApprovalEvent],
    ) -> None:
        self.profiles = profiles
        self.approvals = approvals
        self.execution_events: list[LiveExecutionEvent] = []
        self.resilience_events: list[LiveResilienceEvent] = []

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
                for item in self.approvals
                if item.live_trading_profile_id == profile_id and item.checkpoint_type == checkpoint_type
            ]
            if not rows:
                return None
            return sorted(rows, key=lambda x: x.sequence_number, reverse=True)[0]

        if "FROM live_resilience_events" in sql and "idempotency_key_1" in params:
            key = params["idempotency_key_1"]
            for item in self.resilience_events:
                if item.idempotency_key == key:
                    return item
            return None

        if "max(live_resilience_events.sequence_number)" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            seqs = [item.sequence_number for item in self.resilience_events if item.live_trading_profile_id == profile_id]
            return max(seqs) if seqs else None

        if "FROM live_resilience_events" in sql and "ORDER BY" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            rows = [item for item in self.resilience_events if item.live_trading_profile_id == profile_id]
            if not rows:
                return None
            return sorted(rows, key=lambda x: x.sequence_number, reverse=True)[0]

        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveExecutionEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.execution_events.append(obj)
            return

        if isinstance(obj, LiveResilienceEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.resilience_events.append(obj)

    async def flush(self) -> None:
        return None


class _FakeAdapter(BrokerAdapterContract):
    def __init__(self) -> None:
        self.provider_name = "paper-sim"
        self.calls = 0

    def build_provider_order_request(self, *, request):
        self.calls += 1
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
    now = datetime.now(timezone.utc)
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


def _approval(profile_id: uuid.UUID) -> LiveApprovalEvent:
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
        approval_scope={"controls": ["live_enablement"]},
        expires_at=now + timedelta(days=1),
        renewal_condition="renew",
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=now,
        created_at=now,
    )


def _orchestration_request(profile_id: uuid.UUID, approval_id: uuid.UUID, **overrides: Any) -> LiveExecutionOrchestrationRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": profile_id,
        "provider_name": "paper-sim",
        "broker_account_ref": "acct-1",
        "adapter_request_id": "adapter-req-1",
        "symbol": "AAPL",
        "side": "buy",
        "order_type": "limit",
        "quantity": "1.0",
        "limit_price": "200.0",
        "stop_price": None,
        "time_in_force": "day",
        "risk_decision_id": uuid.uuid4(),
        "approval_event_id": approval_id,
        "audit_correlation_id": "corr-1",
        "requested_by": "operator",
        "provenance_metadata": {"ticket": "LIVE-98"},
        "idempotency_key": "orchestration-key-1",
    }
    payload.update(overrides)
    return LiveExecutionOrchestrationRequest(**payload)


@pytest.mark.asyncio
async def test_kill_switch_engagement_blocks_live_submission() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile], approvals=[])

    await engage_live_kill_switch(
        db=session,
        request=LiveKillSwitchRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            reason_code="manual_kill_switch",
            provenance_metadata={"ticket": "LIVE-98-A"},
            idempotency_key="res-key-1",
        ),
    )

    guard = await evaluate_live_submission_guard(db=session, live_trading_profile_id=profile.id)
    assert guard.allowed is False
    assert guard.submission_blocked is True
    assert guard.reapproval_required is True


@pytest.mark.asyncio
async def test_outage_ambiguity_fails_closed() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile], approvals=[])

    result = await record_live_broker_outage(
        db=session,
        request=LiveOutageDetectionRequest(
            live_trading_profile_id=profile.id,
            provider_name="paper-sim",
            requested_by="monitor",
            reason_code="provider_timeout",
            ambiguity_detected=True,
            provenance_metadata={"ticket": "LIVE-98-B"},
            idempotency_key="res-key-2",
        ),
    )

    guard = await evaluate_live_submission_guard(db=session, live_trading_profile_id=profile.id)
    assert result.reason_code == "outage_ambiguous_state"
    assert guard.allowed is False
    assert guard.ambiguity_detected is True


@pytest.mark.asyncio
async def test_recovery_request_does_not_auto_rearm() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile], approvals=[])

    await engage_live_kill_switch(
        db=session,
        request=LiveKillSwitchRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            reason_code="manual_kill_switch",
            provenance_metadata={"ticket": "LIVE-98-C"},
            idempotency_key="res-key-3",
        ),
    )
    await request_live_recovery(
        db=session,
        request=LiveRecoveryRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            rationale="request recovery",
            approval_event_id=None,
            provenance_metadata={"ticket": "LIVE-98-C"},
            idempotency_key="res-key-4",
        ),
    )

    guard = await evaluate_live_submission_guard(db=session, live_trading_profile_id=profile.id)
    assert guard.allowed is False
    assert guard.reapproval_required is True


@pytest.mark.asyncio
async def test_recovery_approval_requires_explicit_reapproval_event() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile], approvals=[])

    result = await approve_live_recovery(
        db=session,
        request=LiveRecoveryRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            rationale="approve recovery",
            approval_event_id=None,
            provenance_metadata={"ticket": "LIVE-98-D"},
            idempotency_key="res-key-5",
        ),
    )

    guard = await evaluate_live_submission_guard(db=session, live_trading_profile_id=profile.id)
    assert result.event_type == "recovery_rejected"
    assert result.reason_code == "reapproval_event_required"
    assert guard.allowed is False


@pytest.mark.asyncio
async def test_recovery_approval_with_valid_reapproval_unblocks_submission() -> None:
    profile = _profile()
    approval = _approval(profile.id)
    session = _FakeSession(profiles=[profile], approvals=[approval])

    await engage_live_kill_switch(
        db=session,
        request=LiveKillSwitchRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            reason_code="manual_kill_switch",
            provenance_metadata={"ticket": "LIVE-98-E"},
            idempotency_key="res-key-6",
        ),
    )

    result = await approve_live_recovery(
        db=session,
        request=LiveRecoveryRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            rationale="risk and approval restored",
            approval_event_id=approval.id,
            provenance_metadata={"ticket": "LIVE-98-E"},
            idempotency_key="res-key-7",
        ),
    )

    guard = await evaluate_live_submission_guard(db=session, live_trading_profile_id=profile.id)
    assert result.event_type == "recovery_approved"
    assert guard.allowed is True


@pytest.mark.asyncio
async def test_orchestration_blocks_before_adapter_when_resilience_guard_blocks() -> None:
    profile = _profile()
    approval = _approval(profile.id)
    session = _FakeSession(profiles=[profile], approvals=[approval])
    adapter = _FakeAdapter()

    await engage_live_kill_switch(
        db=session,
        request=LiveKillSwitchRequest(
            live_trading_profile_id=profile.id,
            requested_by="human.operator",
            reason_code="manual_kill_switch",
            provenance_metadata={"ticket": "LIVE-98-F"},
            idempotency_key="res-key-8",
        ),
    )

    async def risk_verifier(_: uuid.UUID) -> LiveRiskVerificationResult:
        return LiveRiskVerificationResult(approved=True, reason=None)

    result = await orchestrate_live_execution(
        db=session,
        request=_orchestration_request(profile.id, approval.id, idempotency_key="orchestration-key-2"),
        adapters={"paper-sim": adapter},
        verify_risk_decision=risk_verifier,
    )

    assert result.accepted is False
    assert result.status == "blocked"
    assert result.reason == "manual_kill_switch"
    assert adapter.calls == 0
