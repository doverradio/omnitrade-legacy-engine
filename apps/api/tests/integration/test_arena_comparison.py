from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_comparison_record import (
    ArenaComparisonRecord,
    _prevent_arena_comparison_record_delete,
    _prevent_arena_comparison_record_update,
)
from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.audit_log import AuditLog
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.services.arena.comparison import (
    build_arena_comparison_record,
    read_latest_arena_comparison_record,
)
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
        self.performance_snapshots: list[ArenaPerformanceSnapshot] = []
        self.comparison_records: list[ArenaComparisonRecord] = []
        self.decision_records: list[DecisionRecord] = []
        self.quality_scores: list[DecisionQualityScore] = []
        self.explainability_records: list[DecisionExplainabilityRecord] = []
        self.counterfactual_results: list[DecisionCounterfactualResult] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_comparison_records" in sql:
            key = params.get("idempotency_key_1")
            for item in self.comparison_records:
                if item.idempotency_key == key:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_performance_snapshots" in sql:
            competition_id = params.get("competition_id_1")
            rows = [item for item in self.performance_snapshots if item.competition_id == competition_id]
            return _ExecuteResult(rows)

        if "FROM arena_comparison_records" in sql:
            competition_id = params.get("competition_id_1")
            rows = [item for item in self.comparison_records if item.competition_id == competition_id]
            return _ExecuteResult(rows)

        if "FROM decision_records" in sql:
            return _ExecuteResult(self.decision_records)

        if "FROM decision_quality_scores" in sql:
            return _ExecuteResult(self.quality_scores)

        if "FROM decision_explainability_records" in sql:
            return _ExecuteResult(self.explainability_records)

        if "FROM decision_counterfactual_results" in sql:
            return _ExecuteResult(self.counterfactual_results)

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaComparisonRecord):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.comparison_records.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


def _decision(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
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
        signal_strength=Decimal("0.6"),
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
        pnl={"realized_pnl": "10", "fees_paid": "1"},
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


def _request(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_id: uuid.UUID,
    compared_agent_ids: list[uuid.UUID] | None,
) -> ArenaComparisonRecordRequest:
    return ArenaComparisonRecordRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        compared_agent_ids=compared_agent_ids,
        as_of=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        actor="arena.comparison",
        provenance={"ticket": "ARENA-87"},
    )


@pytest.mark.asyncio
async def test_comparison_is_deterministic_reproducible_and_preserves_provenance() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()

    session.performance_snapshots.append(
        ArenaPerformanceSnapshot(
            id=uuid.uuid4(),
            idempotency_key="perf",
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            snapshot_scope="cycle",
            snapshot_input_hash="snapshot-hash",
            snapshot_payload={
                "agent_summaries": [
                    {"agent_id": str(agent_a)},
                    {"agent_id": str(agent_b)},
                ]
            },
            provenance={"source": "integration"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    decision_a = _decision(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        agent_id=agent_a,
    )
    decision_b = _decision(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        agent_id=agent_b,
    )
    session.decision_records.extend([decision_a, decision_b])

    session.quality_scores.extend(
        [
            DecisionQualityScore(
                id=uuid.uuid4(),
                decision_id=decision_a.decision_id,
                idempotency_key="qa",
                scoring_model_version="dqe_v1",
                composite_score=Decimal("0.8"),
                component_scores=[{"name": "rule", "score": "0.9"}],
                weight_profile={"rule": "0.5"},
                provenance={"source": "integration"},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
            DecisionQualityScore(
                id=uuid.uuid4(),
                decision_id=decision_b.decision_id,
                idempotency_key="qb",
                scoring_model_version="dqe_v1",
                composite_score=Decimal("0.4"),
                component_scores=[{"name": "rule", "score": "0.3"}],
                weight_profile={"rule": "0.5"},
                provenance={"source": "integration"},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
        ]
    )

    session.explainability_records.extend(
        [
            DecisionExplainabilityRecord(
                id=uuid.uuid4(),
                decision_id=decision_a.decision_id,
                idempotency_key="ea",
                evidence_role="supporting",
                evidence_name="trend_alignment",
                evidence_payload={},
                provenance={"source": "integration"},
                availability_state="known",
                state_reason=None,
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
            DecisionExplainabilityRecord(
                id=uuid.uuid4(),
                decision_id=decision_b.decision_id,
                idempotency_key="eb",
                evidence_role="opposing",
                evidence_name="volatility_warning",
                evidence_payload={},
                provenance={"source": "integration"},
                availability_state="known",
                state_reason=None,
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
        ]
    )

    session.counterfactual_results.extend(
        [
            DecisionCounterfactualResult(
                id=uuid.uuid4(),
                decision_id=decision_a.decision_id,
                idempotency_key="ca",
                horizon_label="15m",
                horizon_minutes=15,
                decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
                evaluated_at=datetime(2026, 7, 6, 0, 15, tzinfo=timezone.utc),
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
            ),
            DecisionCounterfactualResult(
                id=uuid.uuid4(),
                decision_id=decision_b.decision_id,
                idempotency_key="cb",
                horizon_label="15m",
                horizon_minutes=15,
                decision_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
                evaluated_at=datetime(2026, 7, 6, 0, 15, tzinfo=timezone.utc),
                asset_symbol="BTCUSDT",
                actual_action="buy",
                shadow_buy_return_pct=Decimal("0.01"),
                shadow_sell_return_pct=Decimal("-0.01"),
                shadow_wait_return_pct=Decimal("0"),
                best_action="buy",
                actual_action_correct=False,
                evaluation_state="resolved",
                state_reason=None,
                lesson_tags=[],
                feature_snapshot={},
                created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
            ),
        ]
    )

    request = _request(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        compared_agent_ids=[agent_a, agent_b],
    )
    first = await build_arena_comparison_record(db=session, request=request)
    second = await build_arena_comparison_record(db=session, request=request)

    assert first.comparison_record_id == second.comparison_record_id
    assert first.comparison_hash == second.comparison_hash
    assert first.evidence_sources["decision_quality_score_ids"]
    assert first.provenance["deterministic"] is True

    by_agent = {item.agent_id: item for item in first.agent_summaries}
    assert by_agent[agent_a].decision_quality.value == Decimal("0.8000")
    assert by_agent[agent_b].decision_quality.value == Decimal("0.4000")
    assert by_agent[agent_a].counterfactual_correctness.value == Decimal("1.0000")
    assert by_agent[agent_b].counterfactual_correctness.value == Decimal("0.0000")


@pytest.mark.asyncio
async def test_unknown_unavailable_states_are_explicit_in_comparison() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    session.performance_snapshots.append(
        ArenaPerformanceSnapshot(
            id=uuid.uuid4(),
            idempotency_key="perf",
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            snapshot_scope="cycle",
            snapshot_input_hash="snapshot-hash",
            snapshot_payload={"agent_summaries": [{"agent_id": str(agent_id)}]},
            provenance={"source": "integration"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    result = await build_arena_comparison_record(
        db=session,
        request=_request(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            compared_agent_ids=[agent_id],
        ),
    )

    summary = result.agent_summaries[0]
    assert summary.decision_quality.status == "unknown"
    assert summary.explainability_support_ratio.status == "unknown"
    assert summary.counterfactual_correctness.status == "unknown"


@pytest.mark.asyncio
async def test_read_model_returns_latest_record_without_mutation() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()

    older = ArenaComparisonRecord(
        id=uuid.uuid4(),
        idempotency_key="old",
        comparison_hash="hash-old",
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        comparison_scope="cycle",
        compared_agent_ids=[str(uuid.uuid4())],
        comparison_payload={
            "agent_summaries": [],
            "portfolio_dimensions": {
                "decision_quality": {"value": None, "status": "unknown", "reason": "none"},
                "explainability_support_ratio": {"value": None, "status": "unknown", "reason": "none"},
                "counterfactual_correctness": {"value": None, "status": "unknown", "reason": "none"},
            },
        },
        evidence_sources={},
        provenance={},
        comparison_timestamp=datetime(2026, 7, 6, 10, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, 10, tzinfo=timezone.utc),
    )
    newer = ArenaComparisonRecord(
        id=uuid.uuid4(),
        idempotency_key="new",
        comparison_hash="hash-new",
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        comparison_scope="cycle",
        compared_agent_ids=[str(uuid.uuid4())],
        comparison_payload={
            "agent_summaries": [],
            "portfolio_dimensions": {
                "decision_quality": {"value": "0.5", "status": "available", "reason": None},
                "explainability_support_ratio": {"value": "0.5", "status": "available", "reason": None},
                "counterfactual_correctness": {"value": "0.5", "status": "available", "reason": None},
            },
        },
        evidence_sources={"x": []},
        provenance={"y": True},
        comparison_timestamp=datetime(2026, 7, 6, 11, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, 11, tzinfo=timezone.utc),
    )
    session.comparison_records.extend([older, newer])

    read_model = await read_latest_arena_comparison_record(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
    )

    assert read_model is not None
    assert read_model.comparison_hash == "hash-new"
    assert len(session.comparison_records) == 2


def test_arena_comparison_records_are_append_only() -> None:
    record = ArenaComparisonRecord(
        id=uuid.uuid4(),
        idempotency_key="x",
        comparison_hash="h",
        competition_id=uuid.uuid4(),
        tournament_id=None,
        cycle_id=None,
        comparison_scope="competition",
        compared_agent_ids=[str(uuid.uuid4())],
        comparison_payload={"agent_summaries": [], "portfolio_dimensions": {}},
        evidence_sources={},
        provenance={},
        comparison_timestamp=datetime(2026, 7, 6, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_comparison_record_update(None, None, record)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_comparison_record_delete(None, None, record)
