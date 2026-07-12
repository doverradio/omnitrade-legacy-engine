from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.audit_log import AuditLog
from app.models.decision_record import DecisionRecord
from app.models.risk_event import RiskEvent
from app.services.decisions.linkage_integrity import guard_preview_linkage_integrity


class _FakeDb:
    def __init__(self, *, decision: DecisionRecord | None, risk_event: RiskEvent | None) -> None:
        self._decision = decision
        self._risk_event = risk_event
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        return None

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM decision_records" in sql:
            return self._decision
        if "FROM risk_events" in sql:
            return self._risk_event
        return None


def _decision(*, preview_id: str, risk_event_id: str, correlation_id: str) -> DecisionRecord:
    return DecisionRecord(
        decision_id=uuid4(),
        idempotency_key="preview-test",
        source_lineage={"crypto_order_previews": [preview_id], "risk_events": [risk_event_id]},
        field_provenance={},
        version="preview_v1",
        timestamp=datetime.now(timezone.utc),
        asset={"asset_id": str(uuid4()), "symbol": "BTC"},
        timeframe="execution_preview",
        market_regime={},
        indicators={},
        generated_signals=[{"action": "buy"}],
        signal_strength=None,
        confidence=None,
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk={"risk_event_id": risk_event_id},
        expected_reward=None,
        position_size=None,
        trade_accepted=False,
        trade_rejected_reason="risk_rejected",
        execution_details={"preview_id": preview_id, "audit_correlation_id": correlation_id},
        exit_details=None,
        pnl=None,
        duration=None,
        outcome="risk_rejected",
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=None,
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )


@pytest.mark.asyncio
async def test_guard_emits_no_event_when_preview_linkage_is_consistent() -> None:
    preview_id = uuid4()
    risk_event_id = uuid4()
    correlation_id = uuid4()

    decision = _decision(
        preview_id=str(preview_id),
        risk_event_id=str(risk_event_id),
        correlation_id=str(correlation_id),
    )
    risk_event = RiskEvent(
        id=risk_event_id,
        event_type="risk_decision",
        action_taken="blocked",
        detail={"reason_code": "risk_rejected"},
    )
    preview = SimpleNamespace(
        crypto_order_preview_id=preview_id,
        status="RISK_REJECTED",
        decision_record_id=decision.decision_id,
        risk_event_id=risk_event.id,
        audit_correlation_id=correlation_id,
    )

    db = _FakeDb(decision=decision, risk_event=risk_event)
    violations = await guard_preview_linkage_integrity(
        db=db,
        actor="test",
        preview=preview,
        stage="risk_rejected",
    )

    assert violations == []
    assert db.added == []


@pytest.mark.asyncio
async def test_guard_emits_violation_event_when_linkage_is_missing() -> None:
    preview_id = uuid4()
    preview = SimpleNamespace(
        crypto_order_preview_id=preview_id,
        status="RISK_REJECTED",
        decision_record_id=None,
        risk_event_id=None,
        audit_correlation_id=None,
    )
    db = _FakeDb(decision=None, risk_event=None)

    violations = await guard_preview_linkage_integrity(
        db=db,
        actor="test",
        preview=preview,
        stage="risk_rejected",
    )

    assert len(violations) >= 3
    assert any(item.code == "missing_decision_record_id" for item in violations)
    assert any(item.code == "missing_risk_event_id" for item in violations)
    assert any(item.code == "missing_audit_correlation_id" for item in violations)

    assert len(db.added) == 1
    event = db.added[0]
    assert isinstance(event, AuditLog)
    assert event.action == "decision_linkage_integrity_violation"
    assert event.entity_type == "decision_linkage_integrity"
    assert event.entity_id == preview_id
