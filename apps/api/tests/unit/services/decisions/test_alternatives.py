from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_alternative_action import (
    DecisionAlternativeAction,
    _prevent_decision_alternative_action_delete,
    _prevent_decision_alternative_action_update,
)
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_record import DecisionRecord
from app.services.decisions.alternatives import (
    build_alternative_action_drafts,
    persist_alternative_actions_for_decision,
    read_decision_alternative_actions,
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
    def __init__(
        self,
        *,
        decision_records: list[DecisionRecord],
        counterfactual_records: list[DecisionCounterfactualResult],
    ) -> None:
        self.decision_records = decision_records
        self.counterfactual_records = counterfactual_records
        self.alternative_actions: list[DecisionAlternativeAction] = []

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

        if "FROM decision_alternative_actions" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            for item in self.alternative_actions:
                if item.idempotency_key == key:
                    return item.id
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_counterfactual_results" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.counterfactual_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.horizon_minutes, str(item.id)))
            return _ExecuteResult(rows)

        if "FROM decision_alternative_actions" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.alternative_actions if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.alternative_action, item.created_at))
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DecisionAlternativeAction):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.now(timezone.utc)
            self.alternative_actions.append(obj)


def _decision_record(*, action: str = "buy") -> DecisionRecord:
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    return DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        source_lineage={"signals": [str(uuid.uuid4())], "model_outputs": [], "risk_events": [], "trades": []},
        field_provenance={},
        version="v1",
        timestamp=now,
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={},
        generated_signals=[{"action": action, "status": "generated"}],
        signal_strength=Decimal("0.6"),
        confidence=Decimal("0.7"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=action != "hold",
        trade_rejected_reason="wait_signal" if action == "hold" else None,
        execution_details=None,
        exit_details=None,
        pnl=None,
        duration=None,
        outcome=None,
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=None,
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )


def _counterfactuals(decision_id: uuid.UUID) -> list[DecisionCounterfactualResult]:
    return [
        DecisionCounterfactualResult(
            id=uuid.uuid4(),
            decision_id=decision_id,
            idempotency_key=f"cf-{horizon}",
            horizon_label="15m" if horizon == 15 else "1h" if horizon == 60 else "24h",
            horizon_minutes=horizon,
            decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
            evaluated_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            asset_symbol="BTCUSDT",
            actual_action="wait",
            shadow_buy_return_pct=Decimal("0.03"),
            shadow_sell_return_pct=Decimal("-0.02"),
            shadow_wait_return_pct=Decimal("0"),
            best_action="buy",
            actual_action_correct=False,
            evaluation_state="resolved",
            state_reason=None,
            lesson_tags=[{"tag": "missed_breakout", "reason": "buy_outperformed"}],
            feature_snapshot={},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
        for horizon in (15, 60, 1440)
    ]


def test_wait_is_first_class_and_produces_two_alternatives() -> None:
    decision = _decision_record(action="hold")
    drafts = build_alternative_action_drafts(
        decision_record=decision,
        counterfactual_records=_counterfactuals(decision.decision_id),
    )

    assert len(drafts) == 2
    assert all(item.chosen_action == "wait" for item in drafts)
    assert {item.alternative_action for item in drafts} == {"buy", "sell"}


def test_comparison_payload_explains_chosen_vs_alternative_change() -> None:
    decision = _decision_record(action="hold")
    drafts = build_alternative_action_drafts(
        decision_record=decision,
        counterfactual_records=_counterfactuals(decision.decision_id),
    )

    buy_alt = next(item for item in drafts if item.alternative_action == "buy")

    assert buy_alt.availability_state == "known"
    assert buy_alt.comparison_payload["chosen_action"] == "wait"
    assert buy_alt.comparison_payload["alternative_action"] == "buy"
    assert buy_alt.comparison_payload["changed_fields"]
    assert "changes expected return" in buy_alt.comparison_payload["summary"]


def test_unavailable_state_when_counterfactuals_missing() -> None:
    decision = _decision_record(action="buy")
    drafts = build_alternative_action_drafts(
        decision_record=decision,
        counterfactual_records=[],
    )

    assert len(drafts) == 2
    assert all(item.availability_state == "unavailable" for item in drafts)
    assert all(item.state_reason == "counterfactual_unavailable" for item in drafts)


@pytest.mark.asyncio
async def test_persistence_is_idempotent_and_provenance_is_preserved() -> None:
    decision = _decision_record(action="hold")
    session = _FakeSession(
        decision_records=[decision],
        counterfactual_records=_counterfactuals(decision.decision_id),
    )

    first = await persist_alternative_actions_for_decision(db=session, decision_id=decision.decision_id)
    second = await persist_alternative_actions_for_decision(db=session, decision_id=decision.decision_id)
    read_model = await read_decision_alternative_actions(db=session, decision_id=decision.decision_id)

    assert first == 2
    assert second == 0
    assert len(session.alternative_actions) == 2
    assert read_model is not None
    assert read_model.chosen_action == "wait"
    assert len(read_model.alternatives) == 2
    assert all(
        item["provenance"]["source_ids"]["decision_record"] == str(decision.decision_id)
        for item in read_model.alternatives
    )


def test_append_only_behavior_for_alternative_action_records() -> None:
    row = DecisionAlternativeAction(
        decision_id=uuid.uuid4(),
        idempotency_key="k",
        chosen_action="wait",
        alternative_action="buy",
        reference_horizon_minutes=15,
        comparison_payload={},
        provenance={},
        availability_state="known",
        state_reason=None,
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_alternative_action_update(None, None, row)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_decision_alternative_action_delete(None, None, row)
