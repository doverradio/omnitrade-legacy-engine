from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.live.approval import (
    evaluate_live_approval_gate,
    record_live_approval_checkpoint,
    revoke_live_approval,
    suspend_live_approval,
)
from app.services.live.contracts import LiveApprovalCheckpointRequest, LiveApprovalStateChangeRequest


class _BeginContext:
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self, *, profiles: list[LiveTradingProfile], raise_on_commit: bool = False) -> None:
        self.profiles = profiles
        self.approval_events: list[LiveApprovalEvent] = []
        self.commit_count = 0
        self.raise_on_commit = raise_on_commit

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def commit(self) -> None:
        if self.raise_on_commit:
            raise RuntimeError("simulated commit failure")
        self.commit_count += 1

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_approval_events" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            for item in self.approval_events:
                if item.idempotency_key == key:
                    return item
            return None

        if "FROM live_approval_events" in sql and "ORDER BY" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            checkpoint_type = params.get("checkpoint_type_1")
            filtered = [
                item
                for item in self.approval_events
                if item.live_trading_profile_id == profile_id and item.checkpoint_type == checkpoint_type
            ]
            if not filtered:
                return None
            return sorted(filtered, key=lambda x: x.sequence_number, reverse=True)[0]

        if "max(live_approval_events.sequence_number)" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            seqs = [item.sequence_number for item in self.approval_events if item.live_trading_profile_id == profile_id]
            if not seqs:
                return None
            return max(seqs)

        if "FROM live_trading_profiles" in sql:
            profile_id = params.get("id_1")
            for profile in self.profiles:
                if profile.id == profile_id:
                    return profile
            return None

        return None

    async def flush(self) -> None:
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveApprovalEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.approval_events.append(obj)


def _profile(*, lifecycle_state: str = "pending_approval", operating_mode: str = "paper") -> LiveTradingProfile:
    return LiveTradingProfile(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        operating_mode=operating_mode,
        lifecycle_state=lifecycle_state,
        approval_state="pending",
        live_opt_in=True,
        human_approval_recorded=False,
        paper_default_mode=True,
        governance_approved=True,
        risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={"seed": "test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _checkpoint_request(profile_id: uuid.UUID, **overrides: Any) -> LiveApprovalCheckpointRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": profile_id,
        "checkpoint_type": "first_live_enablement",
        "approver_id": "human.approver",
        "approver_role": "risk_owner",
        "rationale": "Validated readiness and controls.",
        "approval_scope": {"controls": ["live_enablement", "risk_profile"]},
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        "renewal_condition": "Revalidate monthly",
        "requested_by": "human.operator",
        "provenance_metadata": {"ticket": "LIVE-93"},
        "idempotency_key": "approval-key-1",
    }
    payload.update(overrides)
    return LiveApprovalCheckpointRequest(**payload)


def _state_change_request(profile_id: uuid.UUID, **overrides: Any) -> LiveApprovalStateChangeRequest:
    payload: dict[str, Any] = {
        "live_trading_profile_id": profile_id,
        "checkpoint_type": "first_live_enablement",
        "approver_id": "human.approver",
        "approver_role": "risk_owner",
        "rationale": "Safety intervention",
        "approval_scope": {"controls": ["live_enablement"]},
        "requested_by": "human.operator",
        "provenance_metadata": {"ticket": "LIVE-94"},
        "idempotency_key": "approval-state-key-1",
    }
    payload.update(overrides)
    return LiveApprovalStateChangeRequest(**payload)


@pytest.mark.asyncio
async def test_record_live_approval_checkpoint_captures_identity_rationale_scope_and_expiry() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile])

    result = await record_live_approval_checkpoint(
        db=session,
        request=_checkpoint_request(profile.id),
    )

    assert result.approval_state == "approved"
    assert result.lifecycle_state == "enabled"
    assert result.operating_mode == "live"
    assert result.expires_at is not None
    assert result.renewal_condition == "Revalidate monthly"
    assert len(session.approval_events) == 1
    event = session.approval_events[0]
    assert event.approver_id == "human.approver"
    assert event.approver_role == "risk_owner"
    assert event.rationale == "Validated readiness and controls."
    assert event.approval_scope["controls"] == ["live_enablement", "risk_profile"]


@pytest.mark.asyncio
async def test_material_control_change_checkpoint_grants_approved_without_forced_live_mode() -> None:
    profile = _profile(lifecycle_state="pending_approval", operating_mode="paper")
    session = _FakeSession(profiles=[profile])

    result = await record_live_approval_checkpoint(
        db=session,
        request=_checkpoint_request(
            profile.id,
            checkpoint_type="material_control_change",
            idempotency_key="approval-key-2",
        ),
    )

    assert result.approval_state == "approved"
    assert result.lifecycle_state == "approved"
    assert result.operating_mode == "paper"


@pytest.mark.asyncio
async def test_record_live_approval_checkpoint_is_idempotent() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile])
    request = _checkpoint_request(profile.id, idempotency_key="approval-key-3")

    first = await record_live_approval_checkpoint(db=session, request=request)
    second = await record_live_approval_checkpoint(db=session, request=request)

    assert first.approval_event_id == second.approval_event_id
    assert len(session.approval_events) == 1


@pytest.mark.asyncio
async def test_record_live_approval_checkpoint_commits_durable_first_live_enablement_state() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile])

    result = await record_live_approval_checkpoint(
        db=session,
        request=_checkpoint_request(profile.id, idempotency_key="approval-key-commit-1"),
    )

    assert session.commit_count == 1
    assert len(session.approval_events) == 1
    assert result.operating_mode == "live"
    assert result.lifecycle_state == "enabled"
    assert result.approval_state == "approved"
    assert profile.operating_mode == "live"
    assert profile.lifecycle_state == "enabled"
    assert profile.approval_state == "approved"
    assert profile.human_approval_recorded is True


@pytest.mark.asyncio
async def test_record_live_approval_checkpoint_idempotent_replay_does_not_recommit() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile])
    request = _checkpoint_request(profile.id, idempotency_key="approval-key-commit-2")

    first = await record_live_approval_checkpoint(db=session, request=request)
    second = await record_live_approval_checkpoint(db=session, request=request)

    assert first.approval_event_id == second.approval_event_id
    assert len(session.approval_events) == 1
    assert session.commit_count == 1
    assert profile.operating_mode == "live"
    assert profile.lifecycle_state == "enabled"


@pytest.mark.asyncio
async def test_record_live_approval_checkpoint_commit_failure_does_not_report_success() -> None:
    profile = _profile()
    session = _FakeSession(profiles=[profile], raise_on_commit=True)

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await record_live_approval_checkpoint(
            db=session,
            request=_checkpoint_request(profile.id, idempotency_key="approval-key-commit-3"),
        )


@pytest.mark.asyncio
async def test_revoke_live_approval_suspends_profile_and_returns_to_paper_mode() -> None:
    profile = _profile(lifecycle_state="enabled", operating_mode="live")
    profile.approval_state = "approved"
    profile.human_approval_recorded = True
    session = _FakeSession(profiles=[profile])

    result = await revoke_live_approval(
        db=session,
        request=_state_change_request(profile.id, idempotency_key="approval-key-4"),
    )

    assert result.approval_state == "revoked"
    assert result.lifecycle_state == "suspended"
    assert result.operating_mode == "paper"
    assert profile.approval_state == "revoked"
    assert profile.human_approval_recorded is False
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_revoke_live_approval_commit_failure_does_not_report_success() -> None:
    profile = _profile(lifecycle_state="enabled", operating_mode="live")
    profile.approval_state = "approved"
    profile.human_approval_recorded = True
    session = _FakeSession(profiles=[profile], raise_on_commit=True)

    with pytest.raises(RuntimeError, match="simulated commit failure"):
        await revoke_live_approval(
            db=session,
            request=_state_change_request(profile.id, idempotency_key="approval-key-4-fail"),
        )


@pytest.mark.asyncio
async def test_suspend_live_approval_records_suspension_without_bypass() -> None:
    profile = _profile(lifecycle_state="enabled", operating_mode="live")
    profile.approval_state = "approved"
    profile.human_approval_recorded = True
    session = _FakeSession(profiles=[profile])

    result = await suspend_live_approval(
        db=session,
        request=_state_change_request(profile.id, idempotency_key="approval-key-5"),
    )

    assert result.approval_state == "suspended"
    assert result.lifecycle_state == "suspended"
    assert session.commit_count == 1
    assert result.operating_mode == "paper"


@pytest.mark.asyncio
async def test_evaluate_live_approval_gate_blocks_when_missing_or_expired() -> None:
    profile = _profile(lifecycle_state="approved", operating_mode="paper")
    session = _FakeSession(profiles=[profile])

    missing = await evaluate_live_approval_gate(
        db=session,
        live_trading_profile_id=profile.id,
        checkpoint_type="first_live_enablement",
    )
    assert missing.allowed is False
    assert missing.reason == "approval_checkpoint_missing"

    event = LiveApprovalEvent(
        id=uuid.uuid4(),
        idempotency_key="expired-approval-key",
        event_hash="expired-hash",
        live_trading_profile_id=profile.id,
        sequence_number=1,
        event_type="approval_granted",
        checkpoint_type="first_live_enablement",
        approval_state="approved",
        approver_id="human.approver",
        approver_role="risk_owner",
        rationale="expired",
        approval_scope={"controls": ["live_enablement"]},
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        renewal_condition="renew",
        event_payload={"x": 1},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.approval_events.append(event)

    expired = await evaluate_live_approval_gate(
        db=session,
        live_trading_profile_id=profile.id,
        checkpoint_type="first_live_enablement",
    )
    assert expired.allowed is False
    assert expired.reason == "approval_expired"
