from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.services.decisions.quality import (
    persist_decision_quality_score,
    read_latest_decision_quality_score,
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
        explainability_records: list[DecisionExplainabilityRecord],
        counterfactual_records: list[DecisionCounterfactualResult],
    ) -> None:
        self.decision_records = decision_records
        self.explainability_records = explainability_records
        self.counterfactual_records = counterfactual_records
        self.quality_scores: list[DecisionQualityScore] = []

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

        if "FROM decision_quality_scores" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            for item in self.quality_scores:
                if item.idempotency_key == key:
                    return item.id
            return None

        if "FROM decision_quality_scores" in sql and "decision_id" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.quality_scores if item.decision_id == decision_id]
            if not rows:
                return None
            rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
            return rows[0]

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_explainability_records" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.explainability_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.created_at, str(item.id)))
            return _ExecuteResult(rows)

        if "FROM decision_counterfactual_results" in sql:
            decision_id = params.get("decision_id_1")
            rows = [item for item in self.counterfactual_records if item.decision_id == decision_id]
            rows.sort(key=lambda item: (item.horizon_minutes, str(item.id)))
            return _ExecuteResult(rows)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, DecisionQualityScore):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not getattr(obj, "created_at", None):
                obj.created_at = datetime.now(timezone.utc)
            self.quality_scores.append(obj)


def _decision_record() -> DecisionRecord:
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
        generated_signals=[{"action": "buy", "status": "executed"}],
        signal_strength=Decimal("0.6"),
        confidence=Decimal("0.7"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[{"action_taken": "resized"}],
        expected_risk=None,
        expected_reward=None,
        position_size=Decimal("0.01"),
        trade_accepted=True,
        trade_rejected_reason=None,
        execution_details={"trade_id": str(uuid.uuid4())},
        exit_details=None,
        pnl={"pct": "0.01"},
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


def _explainability(decision_id: uuid.UUID) -> list[DecisionExplainabilityRecord]:
    roles = ["supporting", "opposing", "confidence_factor", "risk_adjustment"]
    return [
        DecisionExplainabilityRecord(
            id=uuid.uuid4(),
            decision_id=decision_id,
            idempotency_key=f"k-{index}",
            evidence_role=role,
            evidence_name=role,
            evidence_payload={},
            provenance={},
            availability_state="known",
            state_reason=None,
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
        for index, role in enumerate(roles)
    ]


def _counterfactuals(decision_id: uuid.UUID) -> list[DecisionCounterfactualResult]:
    return [
        DecisionCounterfactualResult(
            id=uuid.uuid4(),
            decision_id=decision_id,
            idempotency_key=f"h-{horizon}",
            horizon_label="15m" if horizon == 15 else "1h" if horizon == 60 else "24h",
            horizon_minutes=horizon,
            decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
            evaluated_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            asset_symbol="BTCUSDT",
            actual_action="buy",
            shadow_buy_return_pct=Decimal("0.01"),
            shadow_sell_return_pct=Decimal("-0.01"),
            shadow_wait_return_pct=Decimal("0"),
            best_action="buy",
            actual_action_correct=True,
            evaluation_state="resolved",
            state_reason=None,
            lesson_tags=[],
            feature_snapshot={},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
        for horizon in (15, 60, 1440)
    ]


@pytest.mark.asyncio
async def test_quality_score_persistence_is_append_only_repeatable_and_preserves_provenance() -> None:
    decision = _decision_record()
    session = _FakeSession(
        decision_records=[decision],
        explainability_records=_explainability(decision.decision_id),
        counterfactual_records=_counterfactuals(decision.decision_id),
    )

    inserted_first = await persist_decision_quality_score(db=session, decision_id=decision.decision_id)
    inserted_second = await persist_decision_quality_score(db=session, decision_id=decision.decision_id)
    latest = await read_latest_decision_quality_score(db=session, decision_id=decision.decision_id)

    assert inserted_first is True
    assert inserted_second is False
    assert len(session.quality_scores) == 1
    assert latest is not None
    assert latest.provenance["source_ids"]["decision_record"] == str(decision.decision_id)
    assert len(latest.provenance["source_ids"]["explainability_records"]) == 4
    assert len(latest.provenance["source_ids"]["counterfactual_results"]) == 3
