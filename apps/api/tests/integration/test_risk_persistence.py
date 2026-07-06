from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceRequest,
    RiskEvaluationResult,
    RiskEvaluationStep,
    persist_risk_decision,
)


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.risk_events: list[RiskEvent] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    def add(self, obj: Any) -> None:
        if isinstance(obj, RiskEvent):
            self.risk_events.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)


@pytest.mark.asyncio
async def test_persist_risk_decision_writes_deterministic_risk_event_for_rejection() -> None:
    session = _FakeSession()
    paper_account_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    result = RiskEvaluationResult(
        action=RiskDecisionAction.REJECT,
        reason_code="max_daily_loss_breached",
        approved_quantity=Decimal("0"),
        steps=[
            RiskEvaluationStep(step="global_kill_switch", status="pass"),
            RiskEvaluationStep(step="daily_loss", status="reject", reason_code="max_daily_loss_breached"),
        ],
    )

    persisted = await persist_risk_decision(
        db=session,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=paper_account_id,
            signal_id=signal_id,
            actor="system",
            evaluation_result=result,
        ),
    )

    assert persisted.risk_event_action == "blocked"
    assert persisted.risk_event_type == "daily_loss_limit"
    assert persisted.audit_written is False
    assert len(session.risk_events) == 1
    assert len(session.audit_logs) == 0

    risk_event = session.risk_events[0]
    assert risk_event.paper_account_id == paper_account_id
    assert risk_event.related_signal_id == signal_id
    assert risk_event.action_taken == "blocked"
    assert risk_event.event_type == "daily_loss_limit"
    assert risk_event.detail == {
        "decision": "reject",
        "reason_code": "max_daily_loss_breached",
        "approved_quantity": "0",
        "steps": [
            {"step": "global_kill_switch", "status": "pass", "reason_code": None},
            {"step": "daily_loss", "status": "reject", "reason_code": "max_daily_loss_breached"},
        ],
    }


@pytest.mark.asyncio
async def test_persist_risk_decision_writes_audit_for_state_change() -> None:
    session = _FakeSession()
    paper_account_id = uuid.uuid4()
    signal_id = uuid.uuid4()
    result = RiskEvaluationResult(
        action=RiskDecisionAction.REJECT,
        reason_code="global_kill_switch_engaged",
        approved_quantity=Decimal("0"),
        steps=[
            RiskEvaluationStep(step="global_kill_switch", status="reject", reason_code="global_kill_switch_engaged"),
        ],
    )

    persisted = await persist_risk_decision(
        db=session,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=paper_account_id,
            signal_id=signal_id,
            actor="user:demo",
            evaluation_result=result,
            state_change_action="kill_switch_enabled",
            state_change_entity_type="global",
            state_change_entity_id=None,
            state_before={"engaged": False},
            state_after={"engaged": True, "reason": "manual review"},
        ),
    )

    assert persisted.risk_event_action == "blocked"
    assert persisted.risk_event_type == "kill_switch"
    assert persisted.audit_written is True
    assert len(session.risk_events) == 1
    assert len(session.audit_logs) == 1

    audit = session.audit_logs[0]
    assert audit.actor == "user:demo"
    assert audit.action == "kill_switch_enabled"
    assert audit.entity_type == "global"
    assert audit.before_state == {"engaged": False}
    assert audit.after_state == {"engaged": True, "reason": "manual review"}


@pytest.mark.asyncio
async def test_persist_risk_decision_writes_resize_event_payload() -> None:
    session = _FakeSession()
    result = RiskEvaluationResult(
        action=RiskDecisionAction.RESIZE,
        reason_code="position_resized_by_risk_engine",
        approved_quantity=Decimal("0.50"),
        steps=[
            RiskEvaluationStep(step="position_size", status="resize", reason_code="position_resized_by_risk_engine"),
        ],
    )

    persisted = await persist_risk_decision(
        db=session,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            actor="system",
            evaluation_result=result,
        ),
    )

    assert persisted.risk_event_action == "resized"
    assert persisted.risk_event_type == "position_limit"
    assert persisted.audit_written is False
    assert session.risk_events[0].detail["decision"] == "resize"
    assert session.risk_events[0].detail["approved_quantity"] == "0.50"