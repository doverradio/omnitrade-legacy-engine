from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.services.arena.comparison import build_arena_comparison_record
from app.services.arena.contracts import ArenaComparisonRecordRequest


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
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.snapshots: list[ArenaPerformanceSnapshot] = []
        self.decisions: list[DecisionRecord] = []
        self.quality: list[DecisionQualityScore] = []
        self.explainability: list[DecisionExplainabilityRecord] = []
        self.counterfactual: list[DecisionCounterfactualResult] = []
        self.comparisons: list[Any] = []
        self.audit: list[Any] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        params = statement.compile().params
        key = params.get("idempotency_key_1")
        if key is None:
            return None
        for item in self.comparisons:
            if item.idempotency_key == key:
                return item
        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_performance_snapshots" in sql:
            comp = params.get("competition_id_1")
            return _ExecuteResult([item for item in self.snapshots if item.competition_id == comp])
        if "FROM decision_records" in sql:
            return _ExecuteResult(self.decisions)
        if "FROM decision_quality_scores" in sql:
            return _ExecuteResult(self.quality)
        if "FROM decision_explainability_records" in sql:
            return _ExecuteResult(self.explainability)
        if "FROM decision_counterfactual_results" in sql:
            return _ExecuteResult(self.counterfactual)
        if "FROM arena_comparison_records" in sql:
            comp = params.get("competition_id_1")
            return _ExecuteResult([item for item in self.comparisons if item.competition_id == comp])
        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        name = obj.__class__.__name__
        if name == "ArenaComparisonRecord":
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.comparisons.append(obj)
            return
        self.audit.append(obj)

    async def flush(self) -> None:
        return None


@pytest.mark.asyncio
async def test_comparison_hash_is_deterministic_for_same_inputs() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    session.snapshots.append(
        ArenaPerformanceSnapshot(
            id=uuid.uuid4(),
            idempotency_key="perf",
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            snapshot_scope="cycle",
            snapshot_input_hash="input-hash",
            snapshot_payload={"agent_summaries": [{"agent_id": str(agent_id)}]},
            provenance={"source": "unit"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    decision = DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key="d",
        source_lineage={
            "arena_competitions": [str(competition_id)],
            "arena_tournaments": [str(tournament_id)],
            "arena_cycles": [str(cycle_id)],
            "arena_agents": [str(agent_id)],
            "signals": [],
            "model_outputs": [],
            "risk_events": [],
            "trades": [],
        },
        field_provenance={},
        version="v1",
        timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={},
        generated_signals=[{"action": "buy", "status": "generated"}],
        signal_strength=Decimal("0.5"),
        confidence=Decimal("0.7"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[],
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=True,
        trade_rejected_reason=None,
        execution_details=None,
        exit_details=None,
        pnl={"realized_pnl": "2", "fees_paid": "1"},
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
    session.decisions.append(decision)
    session.quality.append(
        DecisionQualityScore(
            id=uuid.uuid4(),
            decision_id=decision.decision_id,
            idempotency_key="q",
            scoring_model_version="dqe_v1",
            composite_score=Decimal("0.8"),
            component_scores=[{"name": "rule", "score": "0.8"}],
            weight_profile={"rule": "1.0"},
            provenance={"source": "unit"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    request = ArenaComparisonRecordRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        compared_agent_ids=[agent_id],
        as_of=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        actor="arena.unit",
        provenance={"ticket": "ARENA-87"},
    )

    a = await build_arena_comparison_record(db=session, request=request)
    b = await build_arena_comparison_record(db=session, request=request)

    assert a.comparison_hash == b.comparison_hash
    assert a.comparison_record_id == b.comparison_record_id
    assert a.agent_summaries[0].decision_quality.value == Decimal("0.8000")
