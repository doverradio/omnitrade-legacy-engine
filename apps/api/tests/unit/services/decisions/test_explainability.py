from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_explainability_record import (
    DecisionExplainabilityRecord,
    _prevent_decision_explainability_record_delete,
    _prevent_decision_explainability_record_update,
)
from app.models.decision_record import DecisionRecord
from app.services.decisions.explainability import (
    build_explainability_evidence_drafts,
    persist_explainability_evidence_for_decision,
    read_decision_explainability,
)


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self, *, decision_records: list[DecisionRecord]) -> None:
        self.decision_records = decision_records
        self.explainability_records: list[DecisionExplainabilityRecord] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql:
            decision_id = params.get("decision_id_1")
            for item in self.decision_records:
                if item.decision_id == decision_id:
                    return item
            return None

        if "FROM decision_explainability_records" in sql and "idempotency_key" in sql:
            key = params.get("idempotency_key_1")
            for item in self.explainability_records:
                if item.idempotency_key == key:
                    return item.id
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_explainability_records" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.explainability_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)))
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DecisionExplainabilityRecord):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.now(timezone.utc)
            self.explainability_records.append(obj)


def _decision_record(
    *,
    decision_id: uuid.UUID | None = None,
    supporting: list[dict[str, Any]] | None = None,
    opposing: list[dict[str, Any]] | None = None,
    risk_adjustments: list[dict[str, Any]] | None = None,
    source_lineage: dict[str, list[str]] | None = None,
    confidence: Decimal | None = Decimal("0.71"),
    signal_strength: Decimal | None = Decimal("0.62"),
    confidence_calibration: dict[str, Any] | None = None,
    generated_action: str = "buy",
    trade_accepted: bool = True,
    trade_rejected_reason: str | None = None,
) -> DecisionRecord:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    return DecisionRecord(
        decision_id=decision_id or uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        source_lineage=source_lineage
        or {
            "signals": [str(uuid.uuid4())],
            "model_outputs": [str(uuid.uuid4())],
            "risk_events": [str(uuid.uuid4())],
            "trades": [],
        },
        field_provenance={},
        version="v1",
        timestamp=now,
        asset={"asset_id": str(uuid.uuid4())},
        timeframe="unknown",
        market_regime={},
        indicators={},
        generated_signals=[{"action": generated_action, "status": "generated"}],
        signal_strength=signal_strength,
        confidence=confidence,
        supporting_strategies=supporting
        or [{"model_name": "signal_scorer", "evidence": {"score": "0.71"}}],
        opposing_strategies=opposing or [{"model_name": "regime_guard", "evidence": {"flag": "weak"}}],
        risk_adjustments=risk_adjustments
        or [{"event_type": "position_limit", "action_taken": "resized", "detail": {"factor": "0.5"}}],
        expected_risk=None,
        expected_reward=None,
        position_size=Decimal("0.01"),
        trade_accepted=trade_accepted,
        trade_rejected_reason=trade_rejected_reason,
        execution_details={"paper_account_id": str(uuid.uuid4())},
        exit_details=None,
        pnl=None,
        duration=None,
        outcome=None,
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=None,
        confidence_calibration=confidence_calibration,
        review_status="unreviewed",
        human_notes=None,
    )


def test_supporting_and_opposing_evidence_linkage() -> None:
    record = _decision_record()

    drafts = build_explainability_evidence_drafts(decision_record=record)

    supporting = [item for item in drafts if item.evidence_role == "supporting"]
    opposing = [item for item in drafts if item.evidence_role == "opposing"]

    assert len(supporting) == 1
    assert len(opposing) == 1
    assert supporting[0].availability_state == "known"
    assert opposing[0].availability_state == "known"
    assert sorted(supporting[0].provenance["record_ids"]["model_outputs"]) == sorted(
        record.source_lineage["model_outputs"]
    )


@pytest.mark.asyncio
async def test_confidence_factor_persistence_and_read_model() -> None:
    record = _decision_record(confidence_calibration={"evaluation_state": "pending_outcome"})
    session = _FakeSession(decision_records=[record])

    inserted = await persist_explainability_evidence_for_decision(db=session, decision_id=record.decision_id)
    read_model = await read_decision_explainability(db=session, decision_id=record.decision_id)

    assert inserted > 0
    assert read_model is not None
    assert len(read_model.confidence_factors) >= 2
    assert all(item["availability_state"] == "known" for item in read_model.confidence_factors)


def test_risk_adjustment_lineage_is_preserved() -> None:
    risk_event_id = str(uuid.uuid4())
    record = _decision_record(source_lineage={"signals": [], "model_outputs": [], "risk_events": [risk_event_id], "trades": []})

    drafts = build_explainability_evidence_drafts(decision_record=record)
    risk_entries = [item for item in drafts if item.evidence_role == "risk_adjustment"]

    assert len(risk_entries) == 1
    assert risk_entries[0].availability_state == "known"
    assert risk_entries[0].provenance["record_ids"]["risk_events"] == [risk_event_id]


def test_unknown_and_unavailable_evidence_states_are_explicit() -> None:
    unknown_record = _decision_record(
        supporting=[],
        opposing=[],
        risk_adjustments=[],
        source_lineage={"signals": [], "model_outputs": [str(uuid.uuid4())], "risk_events": [str(uuid.uuid4())], "trades": []},
        confidence=None,
        signal_strength=None,
        confidence_calibration=None,
    )
    unavailable_record = _decision_record(
        supporting=[],
        opposing=[],
        risk_adjustments=[],
        source_lineage={"signals": [], "model_outputs": [], "risk_events": [], "trades": []},
        confidence=None,
        signal_strength=None,
        confidence_calibration=None,
    )

    unknown_drafts = build_explainability_evidence_drafts(decision_record=unknown_record)
    unavailable_drafts = build_explainability_evidence_drafts(decision_record=unavailable_record)

    assert any(item.availability_state == "unknown" for item in unknown_drafts)
    assert any(item.availability_state == "unavailable" for item in unavailable_drafts)


def test_append_only_behavior_for_explainability_records() -> None:
    record = DecisionExplainabilityRecord(
        decision_id=uuid.uuid4(),
        idempotency_key="k",
        evidence_role="supporting",
        evidence_name="signal_scorer",
        evidence_payload={},
        provenance={},
        availability_state="known",
        state_reason=None,
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_explainability_record_update(None, None, record)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_explainability_record_delete(None, None, record)
