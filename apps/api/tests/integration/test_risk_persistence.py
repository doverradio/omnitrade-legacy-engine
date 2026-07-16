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
    def __init__(self, session: "_FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "_BeginContext":
        self._session._in_transaction = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self._session.commit_calls += 1
        else:
            self._session.rollback_calls += 1
        self._session._in_transaction = False
        return None


class _FakeSession:
    def __init__(self, *, fail_flush: bool = False) -> None:
        self.risk_events: list[RiskEvent] = []
        self.audit_logs: list[AuditLog] = []
        self.begin_calls = 0
        self._in_transaction = False
        self.flush_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self._fail_flush = fail_flush

    def begin(self) -> _BeginContext:
        self.begin_calls += 1
        return _BeginContext(self)

    def in_transaction(self) -> bool:
        return self._in_transaction

    def add(self, obj: Any) -> None:
        if isinstance(obj, RiskEvent):
            self.risk_events.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        self.flush_calls += 1
        if self._fail_flush:
            raise RuntimeError("flush failed")
        for event in self.risk_events:
            if getattr(event, "id", None) is None:
                event.id = uuid.uuid4()
        for index, audit in enumerate(self.audit_logs, start=1):
            if getattr(audit, "id", None) is None:
                audit.id = index


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
    assert session.flush_calls == 1
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
    assert session.flush_calls == 1
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
    assert session.flush_calls == 1
    assert session.risk_events[0].detail["decision"] == "resize"
    assert session.risk_events[0].detail["approved_quantity"] == "0.50"


@pytest.mark.asyncio
async def test_persist_risk_decision_joins_existing_transaction_without_begin() -> None:
    session = _FakeSession()
    session._in_transaction = True
    result = RiskEvaluationResult(
        action=RiskDecisionAction.REJECT,
        reason_code="max_daily_loss_breached",
        approved_quantity=Decimal("0"),
        steps=[RiskEvaluationStep(step="daily_loss", status="reject", reason_code="max_daily_loss_breached")],
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

    assert persisted.risk_event_action == "blocked"
    assert session.begin_calls == 0
    assert session.flush_calls == 1
    assert len(session.risk_events) == 1


@pytest.mark.asyncio
async def test_persist_risk_decision_standalone_uses_begin_once() -> None:
    session = _FakeSession()
    session._in_transaction = False
    result = RiskEvaluationResult(
        action=RiskDecisionAction.APPROVE,
        reason_code=None,
        approved_quantity=Decimal("0.25"),
        steps=[RiskEvaluationStep(step="global_kill_switch", status="pass")],
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

    assert persisted.risk_event_action == "approved"
    assert session.begin_calls == 1
    assert session.flush_calls == 1
    assert persisted.risk_event_id == session.risk_events[0].id
    assert session.risk_events[0].id is not None
    assert len(session.risk_events) == 1


@pytest.mark.asyncio
async def test_persist_risk_decision_returns_populated_risk_event_identity() -> None:
    session = _FakeSession()
    result = RiskEvaluationResult(
        action=RiskDecisionAction.REJECT,
        reason_code="max_drawdown_breached",
        approved_quantity=Decimal("0"),
        steps=[RiskEvaluationStep(step="drawdown", status="reject", reason_code="max_drawdown_breached")],
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

    assert persisted.risk_event_id is not None
    assert persisted.risk_event_id == session.risk_events[0].id


@pytest.mark.asyncio
async def test_persist_risk_decision_flush_failure_rolls_back_transaction() -> None:
    session = _FakeSession(fail_flush=True)
    result = RiskEvaluationResult(
        action=RiskDecisionAction.REJECT,
        reason_code="max_daily_loss_breached",
        approved_quantity=Decimal("0"),
        steps=[RiskEvaluationStep(step="daily_loss", status="reject", reason_code="max_daily_loss_breached")],
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        await persist_risk_decision(
            db=session,
            request=RiskDecisionPersistenceRequest(
                paper_account_id=uuid.uuid4(),
                signal_id=uuid.uuid4(),
                actor="system",
                evaluation_result=result,
            ),
        )

    assert session.begin_calls == 1
    assert session.flush_calls == 1
    assert session.commit_calls == 0
    assert session.rollback_calls == 1