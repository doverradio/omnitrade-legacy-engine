from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_agent_budget_assignment import ArenaAgentBudgetAssignment
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_performance_snapshot import (
    ArenaPerformanceSnapshot,
    _prevent_arena_performance_snapshot_delete,
    _prevent_arena_performance_snapshot_update,
)
from app.models.arena_risk_gate_decision import ArenaRiskGateDecision
from app.models.audit_log import AuditLog
from app.models.decision_record import DecisionRecord
from app.models.risk_event import RiskEvent
from app.services.arena.contracts import ArenaPerformanceSnapshotRequest
from app.services.arena.performance_tracking import build_arena_performance_snapshot


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
        self.proposals: list[ArenaCycleProposal] = []
        self.risk_gate_decisions: list[ArenaRiskGateDecision] = []
        self.decision_records: list[DecisionRecord] = []
        self.risk_events: list[RiskEvent] = []
        self.assignments: list[ArenaAgentBudgetAssignment] = []
        self.snapshots: list[ArenaPerformanceSnapshot] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_performance_snapshots" in sql:
            key = params.get("idempotency_key_1")
            for item in self.snapshots:
                if item.idempotency_key == key:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_cycle_proposals" in sql:
            competition_id = params.get("competition_id_1")
            return _ExecuteResult(
                [item for item in self.proposals if item.competition_id == competition_id]
            )

        if "FROM arena_risk_gate_decisions" in sql:
            competition_id = params.get("competition_id_1")
            return _ExecuteResult(
                [item for item in self.risk_gate_decisions if item.competition_id == competition_id]
            )

        if "FROM decision_records" in sql:
            return _ExecuteResult(list(self.decision_records))

        if "FROM risk_events" in sql:
            return _ExecuteResult(list(self.risk_events))

        if "FROM arena_agent_budget_assignments" in sql:
            competition_id = params.get("competition_id_1")
            return _ExecuteResult(
                [item for item in self.assignments if item.competition_id == competition_id]
            )

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaPerformanceSnapshot):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.snapshots.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


def _decision_record(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    cycle_id: uuid.UUID,
    agent_id: uuid.UUID,
    ts: datetime,
    realized_pnl: str | None,
    fees_paid: str | None,
) -> DecisionRecord:
    pnl_payload: dict[str, Any] | None = None
    if realized_pnl is not None or fees_paid is not None:
        pnl_payload = {
            "realized_pnl": realized_pnl,
            "fees_paid": fees_paid,
        }

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
        timestamp=ts,
        asset={"asset_id": str(uuid.uuid4()), "symbol": "BTCUSDT"},
        timeframe="1m",
        market_regime={"regime_tag": "trend_up"},
        indicators={},
        generated_signals=[{"action": "buy", "status": "generated"}],
        signal_strength=Decimal("0.7"),
        confidence=Decimal("0.8"),
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
        pnl=pnl_payload,
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


def _proposal(
    *,
    cycle_id: uuid.UUID,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> ArenaCycleProposal:
    return ArenaCycleProposal(
        id=uuid.uuid4(),
        idempotency_key=f"proposal-{agent_id}",
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_id,
        proposal_action="buy",
        proposal_payload={"quantity": "1"},
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _assignment(*, competition_id: uuid.UUID, agent_id: uuid.UUID, budget: str) -> ArenaAgentBudgetAssignment:
    return ArenaAgentBudgetAssignment(
        id=uuid.uuid4(),
        idempotency_key=f"assign-{agent_id}",
        competition_budget_allocation_id=uuid.uuid4(),
        competition_id=competition_id,
        agent_id=agent_id,
        assigned_budget=Decimal(budget),
        paper_only=True,
        live_capital_allocation=False,
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _risk_decision(
    *,
    proposal: ArenaCycleProposal,
    action: str,
) -> ArenaRiskGateDecision:
    return ArenaRiskGateDecision(
        id=uuid.uuid4(),
        idempotency_key=f"risk-{proposal.id}",
        cycle_id=proposal.cycle_id,
        proposal_id=proposal.id,
        competition_id=proposal.competition_id,
        tournament_id=proposal.tournament_id,
        agent_id=proposal.agent_id,
        decision_action=action,
        reason_code=None,
        approved_quantity=Decimal("1"),
        risk_steps=[{"step": "position_size", "status": action}],
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _request(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID | None,
    cycle_id: uuid.UUID | None,
) -> ArenaPerformanceSnapshotRequest:
    return ArenaPerformanceSnapshotRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        as_of=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        actor="arena.performance",
        provenance={"ticket": "ARENA-86"},
    )


@pytest.mark.asyncio
async def test_metrics_are_deterministic_and_snapshot_is_reproducible() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    proposal = _proposal(
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_id,
    )
    session.proposals.append(proposal)
    session.assignments.append(_assignment(competition_id=competition_id, agent_id=agent_id, budget="1000"))
    session.risk_gate_decisions.append(_risk_decision(proposal=proposal, action="approve"))
    session.decision_records.append(
        _decision_record(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            agent_id=agent_id,
            ts=datetime(2026, 7, 6, tzinfo=timezone.utc),
            realized_pnl="50",
            fees_paid="2",
        )
    )

    request = _request(competition_id=competition_id, tournament_id=tournament_id, cycle_id=cycle_id)
    first = await build_arena_performance_snapshot(db=session, request=request)
    second = await build_arena_performance_snapshot(db=session, request=request)

    assert first.snapshot_id == second.snapshot_id
    assert first.snapshot_input_hash == second.snapshot_input_hash
    assert len(session.snapshots) == 1


@pytest.mark.asyncio
async def test_profit_is_not_only_metric_and_risk_discipline_uses_risk_gate_outcomes() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()

    proposal_a = _proposal(
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_a,
    )
    proposal_b = _proposal(
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_b,
    )
    session.proposals.extend([proposal_a, proposal_b])
    session.assignments.extend(
        [
            _assignment(competition_id=competition_id, agent_id=agent_a, budget="1000"),
            _assignment(competition_id=competition_id, agent_id=agent_b, budget="1000"),
        ]
    )
    session.risk_gate_decisions.extend(
        [
            _risk_decision(proposal=proposal_a, action="approve"),
            _risk_decision(proposal=proposal_a, action="approve"),
            _risk_decision(proposal=proposal_b, action="reject"),
            _risk_decision(proposal=proposal_b, action="resize"),
        ]
    )

    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    session.decision_records.extend(
        [
            _decision_record(
                competition_id=competition_id,
                tournament_id=tournament_id,
                cycle_id=cycle_id,
                agent_id=agent_a,
                ts=now,
                realized_pnl="20",
                fees_paid="1",
            ),
            _decision_record(
                competition_id=competition_id,
                tournament_id=tournament_id,
                cycle_id=cycle_id,
                agent_id=agent_b,
                ts=now + timedelta(minutes=1),
                realized_pnl="20",
                fees_paid="1",
            ),
        ]
    )

    result = await build_arena_performance_snapshot(
        db=session,
        request=_request(competition_id=competition_id, tournament_id=tournament_id, cycle_id=cycle_id),
    )

    by_agent = {item.agent_id: item for item in result.agent_summaries}
    assert by_agent[agent_a].profit.value == by_agent[agent_b].profit.value == Decimal("20")
    assert by_agent[agent_a].risk_discipline.value == Decimal("1.0000")
    assert by_agent[agent_b].risk_discipline.value == Decimal("0.2500")
    assert by_agent[agent_a].risk_discipline.value != by_agent[agent_b].risk_discipline.value


@pytest.mark.asyncio
async def test_drawdown_and_fee_drag_are_calculated_separately() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    proposal = _proposal(
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_id,
    )
    session.proposals.append(proposal)
    session.assignments.append(_assignment(competition_id=competition_id, agent_id=agent_id, budget="100"))
    session.risk_gate_decisions.append(_risk_decision(proposal=proposal, action="approve"))

    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    session.decision_records.extend(
        [
            _decision_record(
                competition_id=competition_id,
                tournament_id=tournament_id,
                cycle_id=cycle_id,
                agent_id=agent_id,
                ts=now,
                realized_pnl="50",
                fees_paid="2",
            ),
            _decision_record(
                competition_id=competition_id,
                tournament_id=tournament_id,
                cycle_id=cycle_id,
                agent_id=agent_id,
                ts=now + timedelta(minutes=1),
                realized_pnl="-80",
                fees_paid="3",
            ),
        ]
    )

    result = await build_arena_performance_snapshot(
        db=session,
        request=_request(competition_id=competition_id, tournament_id=tournament_id, cycle_id=cycle_id),
    )

    summary = result.agent_summaries[0]
    assert summary.fee_drag.value == Decimal("5")
    assert summary.drawdown.value == Decimal("0.5333")


@pytest.mark.asyncio
async def test_missing_data_is_explicit_unknown_or_unavailable() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    proposal = _proposal(
        cycle_id=cycle_id,
        competition_id=competition_id,
        tournament_id=tournament_id,
        agent_id=agent_id,
    )
    session.proposals.append(proposal)
    session.risk_gate_decisions.append(_risk_decision(proposal=proposal, action="approve"))
    session.decision_records.append(
        _decision_record(
            competition_id=competition_id,
            tournament_id=tournament_id,
            cycle_id=cycle_id,
            agent_id=agent_id,
            ts=datetime(2026, 7, 6, tzinfo=timezone.utc),
            realized_pnl=None,
            fees_paid=None,
        )
    )

    result = await build_arena_performance_snapshot(
        db=session,
        request=_request(competition_id=competition_id, tournament_id=tournament_id, cycle_id=cycle_id),
    )
    summary = result.agent_summaries[0]

    assert summary.profit.status == "unavailable"
    assert summary.fee_drag.status == "unavailable"
    assert summary.drawdown.status == "unavailable"
    assert summary.risk_discipline.status == "available"


def test_snapshot_model_is_append_only() -> None:
    snapshot = ArenaPerformanceSnapshot(
        id=uuid.uuid4(),
        idempotency_key="snap-1",
        competition_id=uuid.uuid4(),
        tournament_id=None,
        cycle_id=None,
        snapshot_scope="competition",
        snapshot_input_hash="hash",
        snapshot_payload={"portfolio": {}},
        provenance={"source": "unit-test"},
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_performance_snapshot_update(None, None, snapshot)

    with pytest.raises(ValueError, match="append-only"):
        _prevent_arena_performance_snapshot_delete(None, None, snapshot)
