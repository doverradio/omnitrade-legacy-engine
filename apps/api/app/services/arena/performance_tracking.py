from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.arena_agent_budget_assignment import ArenaAgentBudgetAssignment
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.arena_risk_gate_decision import ArenaRiskGateDecision
from app.models.audit_log import AuditLog
from app.models.decision_record import DecisionRecord
from app.models.risk_event import RiskEvent
from app.services.arena.contracts import (
    ArenaAgentPerformanceSummaryContract,
    ArenaMetricValueContract,
    ArenaPerformanceSnapshotRequest,
    ArenaPerformanceSnapshotResult,
    ArenaPortfolioPerformanceContract,
)


@dataclass(slots=True)
class _AgentWorkingSet:
    assigned_budget: Decimal | None
    proposal_ids: list[uuid.UUID]
    risk_gate_decisions: list[ArenaRiskGateDecision]
    decision_records: list[DecisionRecord]


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _lineage_matches(lineage: dict[str, list[str]], key: str, expected: str) -> bool:
    values = lineage.get(key) or []
    return expected in values


def _snapshot_scope(*, tournament_id: uuid.UUID | None, cycle_id: uuid.UUID | None) -> str:
    if cycle_id is not None:
        return "cycle"
    if tournament_id is not None:
        return "tournament"
    return "competition"


def _build_input_hash_payload(
    *,
    request: ArenaPerformanceSnapshotRequest,
    proposals: list[ArenaCycleProposal],
    risk_gate_decisions: list[ArenaRiskGateDecision],
    decision_records: list[DecisionRecord],
    risk_events: list[RiskEvent],
    assignments: list[ArenaAgentBudgetAssignment],
) -> dict[str, Any]:
    return {
        "competition_id": str(request.competition_id),
        "tournament_id": str(request.tournament_id) if request.tournament_id else None,
        "cycle_id": str(request.cycle_id) if request.cycle_id else None,
        "as_of": request.as_of.isoformat(),
        "proposals": [
            {
                "id": str(item.id),
                "cycle_id": str(item.cycle_id),
                "tournament_id": str(item.tournament_id),
                "agent_id": str(item.agent_id),
                "proposal_action": item.proposal_action,
                "proposal_payload": item.proposal_payload,
            }
            for item in sorted(proposals, key=lambda row: str(row.id))
        ],
        "risk_gate_decisions": [
            {
                "id": str(item.id),
                "proposal_id": str(item.proposal_id),
                "agent_id": str(item.agent_id),
                "decision_action": item.decision_action,
                "reason_code": item.reason_code,
                "approved_quantity": format(Decimal(item.approved_quantity), "f"),
                "risk_steps": item.risk_steps,
            }
            for item in sorted(risk_gate_decisions, key=lambda row: str(row.id))
        ],
        "decision_records": [
            {
                "decision_id": str(item.decision_id),
                "timestamp": item.timestamp.isoformat(),
                "source_lineage": item.source_lineage,
                "pnl": item.pnl,
                "outcome": item.outcome,
            }
            for item in sorted(decision_records, key=lambda row: str(row.decision_id))
        ],
        "risk_events": [
            {
                "id": str(item.id),
                "related_signal_id": str(item.related_signal_id) if item.related_signal_id else None,
                "event_type": item.event_type,
                "action_taken": item.action_taken,
                "detail": item.detail,
            }
            for item in sorted(risk_events, key=lambda row: str(row.id))
        ],
        "assignments": [
            {
                "id": str(item.id),
                "agent_id": str(item.agent_id),
                "assigned_budget": format(Decimal(item.assigned_budget), "f"),
                "created_at": item.created_at.isoformat(),
            }
            for item in sorted(assignments, key=lambda row: str(row.id))
        ],
    }


def _build_snapshot_idempotency_key(*, scope: str, payload_hash: str) -> str:
    payload = {
        "kind": "arena_performance_snapshot",
        "scope": scope,
        "payload_hash": payload_hash,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _metric(value: Decimal | None, status: str, reason: str | None) -> ArenaMetricValueContract:
    return ArenaMetricValueContract(value=value, status=status, reason=reason)


def _compute_profit(records: list[DecisionRecord]) -> ArenaMetricValueContract:
    values = []
    for item in records:
        pnl = item.pnl or {}
        value = _to_decimal(pnl.get("realized_pnl"))
        if value is not None:
            values.append(value)

    if not records:
        return _metric(None, "unknown", "no_decision_records_for_scope")
    if not values:
        return _metric(None, "unavailable", "realized_pnl_missing")
    return _metric(sum(values, start=Decimal("0")), "available", None)


def _compute_fee_drag(records: list[DecisionRecord]) -> ArenaMetricValueContract:
    values = []
    for item in records:
        pnl = item.pnl or {}
        value = _to_decimal(pnl.get("fees_paid"))
        if value is not None:
            values.append(value)

    if not records:
        return _metric(None, "unknown", "no_decision_records_for_scope")
    if not values:
        return _metric(None, "unavailable", "fees_paid_missing")
    return _metric(sum(values, start=Decimal("0")), "available", None)


def _compute_consistency(records: list[DecisionRecord]) -> ArenaMetricValueContract:
    outcomes = []
    for item in records:
        pnl = item.pnl or {}
        value = _to_decimal(pnl.get("realized_pnl"))
        if value is not None:
            outcomes.append(value)

    if not records:
        return _metric(None, "unknown", "no_decision_records_for_scope")
    if not outcomes:
        return _metric(None, "unavailable", "realized_pnl_missing")

    wins = sum(1 for value in outcomes if value > Decimal("0"))
    return _metric(
        (Decimal(wins) / Decimal(len(outcomes))).quantize(Decimal("0.0001")),
        "available",
        None,
    )


def _compute_drawdown(records: list[DecisionRecord], starting_budget: Decimal | None) -> ArenaMetricValueContract:
    if not records:
        return _metric(None, "unknown", "no_decision_records_for_scope")
    if starting_budget is None:
        return _metric(None, "unavailable", "assigned_budget_missing")

    pnl_points: list[tuple[Any, Decimal]] = []
    for item in records:
        pnl = item.pnl or {}
        realized = _to_decimal(pnl.get("realized_pnl"))
        if realized is not None:
            pnl_points.append((item.timestamp, realized))

    if not pnl_points:
        return _metric(None, "unavailable", "realized_pnl_missing")

    pnl_points.sort(key=lambda row: row[0])

    equity = starting_budget
    peak = starting_budget
    max_drawdown = Decimal("0")
    for _, pnl in pnl_points:
        equity += pnl
        if equity > peak:
            peak = equity
        if peak > Decimal("0"):
            drawdown = (peak - equity) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown

    return _metric(max_drawdown.quantize(Decimal("0.0001")), "available", None)


def _compute_risk_discipline(decisions: list[ArenaRiskGateDecision]) -> ArenaMetricValueContract:
    if not decisions:
        return _metric(None, "unknown", "no_risk_gate_decisions_for_scope")

    approved = sum(1 for item in decisions if item.decision_action == "approve")
    resized = sum(1 for item in decisions if item.decision_action == "resize")
    total = len(decisions)
    score = (Decimal(approved) + (Decimal("0.5") * Decimal(resized))) / Decimal(total)
    return _metric(score.quantize(Decimal("0.0001")), "available", None)


def _build_agent_summary(*, agent_id: uuid.UUID, work: _AgentWorkingSet) -> ArenaAgentPerformanceSummaryContract:
    profit = _compute_profit(work.decision_records)
    drawdown = _compute_drawdown(work.decision_records, work.assigned_budget)
    fee_drag = _compute_fee_drag(work.decision_records)
    consistency = _compute_consistency(work.decision_records)
    risk_discipline = _compute_risk_discipline(work.risk_gate_decisions)

    return ArenaAgentPerformanceSummaryContract(
        agent_id=agent_id,
        profit=profit,
        drawdown=drawdown,
        fee_drag=fee_drag,
        consistency=consistency,
        risk_discipline=risk_discipline,
        provenance={
            "proposal_count": len(work.proposal_ids),
            "risk_gate_decision_count": len(work.risk_gate_decisions),
            "decision_record_count": len(work.decision_records),
            "assigned_budget": format(work.assigned_budget, "f") if work.assigned_budget is not None else None,
        },
    )


def _aggregate_metric_sum(values: list[ArenaMetricValueContract], unknown_reason: str) -> ArenaMetricValueContract:
    available = [item.value for item in values if item.status == "available" and item.value is not None]
    if not available:
        return _metric(None, "unknown", unknown_reason)
    return _metric(sum(available, start=Decimal("0")), "available", None)


def _aggregate_metric_mean(values: list[ArenaMetricValueContract], unknown_reason: str) -> ArenaMetricValueContract:
    available = [item.value for item in values if item.status == "available" and item.value is not None]
    if not available:
        return _metric(None, "unknown", unknown_reason)
    total = sum(available, start=Decimal("0"))
    return _metric((total / Decimal(len(available))).quantize(Decimal("0.0001")), "available", None)


def _aggregate_metric_max(values: list[ArenaMetricValueContract], unknown_reason: str) -> ArenaMetricValueContract:
    available = [item.value for item in values if item.status == "available" and item.value is not None]
    if not available:
        return _metric(None, "unknown", unknown_reason)
    return _metric(max(available), "available", None)


def _portfolio_summary(
    *,
    request: ArenaPerformanceSnapshotRequest,
    agent_summaries: list[ArenaAgentPerformanceSummaryContract],
) -> ArenaPortfolioPerformanceContract:
    return ArenaPortfolioPerformanceContract(
        competition_id=request.competition_id,
        tournament_id=request.tournament_id,
        cycle_id=request.cycle_id,
        profit=_aggregate_metric_sum(
            [item.profit for item in agent_summaries],
            "portfolio_profit_unknown_due_to_agent_data_gap",
        ),
        drawdown=_aggregate_metric_max(
            [item.drawdown for item in agent_summaries],
            "portfolio_drawdown_unknown_due_to_agent_data_gap",
        ),
        fee_drag=_aggregate_metric_sum(
            [item.fee_drag for item in agent_summaries],
            "portfolio_fee_drag_unknown_due_to_agent_data_gap",
        ),
        consistency=_aggregate_metric_mean(
            [item.consistency for item in agent_summaries],
            "portfolio_consistency_unknown_due_to_agent_data_gap",
        ),
        risk_discipline=_aggregate_metric_mean(
            [item.risk_discipline for item in agent_summaries],
            "portfolio_risk_discipline_unknown_due_to_agent_data_gap",
        ),
        provenance={
            "agent_count": len(agent_summaries),
        },
    )


def _serialize_metric(metric: ArenaMetricValueContract) -> dict[str, Any]:
    return {
        "value": format(metric.value, "f") if metric.value is not None else None,
        "status": metric.status,
        "reason": metric.reason,
    }


def _serialize_agent_summary(summary: ArenaAgentPerformanceSummaryContract) -> dict[str, Any]:
    return {
        "agent_id": str(summary.agent_id),
        "profit": _serialize_metric(summary.profit),
        "drawdown": _serialize_metric(summary.drawdown),
        "fee_drag": _serialize_metric(summary.fee_drag),
        "consistency": _serialize_metric(summary.consistency),
        "risk_discipline": _serialize_metric(summary.risk_discipline),
        "provenance": summary.provenance,
    }


def _deserialize_metric(payload: dict[str, Any]) -> ArenaMetricValueContract:
    return ArenaMetricValueContract(
        value=_to_decimal(payload.get("value")),
        status=str(payload.get("status") or "unknown"),
        reason=payload.get("reason"),
    )


def _deserialize_result(snapshot: ArenaPerformanceSnapshot) -> ArenaPerformanceSnapshotResult:
    payload = snapshot.snapshot_payload

    agent_summaries = []
    for item in payload.get("agent_summaries", []):
        agent_summaries.append(
            ArenaAgentPerformanceSummaryContract(
                agent_id=uuid.UUID(str(item["agent_id"])),
                profit=_deserialize_metric(item["profit"]),
                drawdown=_deserialize_metric(item["drawdown"]),
                fee_drag=_deserialize_metric(item["fee_drag"]),
                consistency=_deserialize_metric(item["consistency"]),
                risk_discipline=_deserialize_metric(item["risk_discipline"]),
                provenance=dict(item.get("provenance") or {}),
            )
        )

    portfolio_payload = payload["portfolio"]
    portfolio = ArenaPortfolioPerformanceContract(
        competition_id=snapshot.competition_id,
        tournament_id=snapshot.tournament_id,
        cycle_id=snapshot.cycle_id,
        profit=_deserialize_metric(portfolio_payload["profit"]),
        drawdown=_deserialize_metric(portfolio_payload["drawdown"]),
        fee_drag=_deserialize_metric(portfolio_payload["fee_drag"]),
        consistency=_deserialize_metric(portfolio_payload["consistency"]),
        risk_discipline=_deserialize_metric(portfolio_payload["risk_discipline"]),
        provenance=dict(portfolio_payload.get("provenance") or {}),
    )

    return ArenaPerformanceSnapshotResult(
        snapshot_id=snapshot.id,
        competition_id=snapshot.competition_id,
        tournament_id=snapshot.tournament_id,
        cycle_id=snapshot.cycle_id,
        snapshot_scope=snapshot.snapshot_scope,
        snapshot_input_hash=snapshot.snapshot_input_hash,
        agent_summaries=agent_summaries,
        portfolio=portfolio,
        provenance=snapshot.provenance,
    )


async def build_arena_performance_snapshot(
    *,
    db: AsyncSession,
    request: ArenaPerformanceSnapshotRequest,
) -> ArenaPerformanceSnapshotResult:
    scope = _snapshot_scope(tournament_id=request.tournament_id, cycle_id=request.cycle_id)

    proposals_result = await db.execute(
        select(ArenaCycleProposal).where(ArenaCycleProposal.competition_id == request.competition_id)
    )
    all_proposals = list(proposals_result.scalars().all())
    proposals = [
        item
        for item in all_proposals
        if (request.tournament_id is None or item.tournament_id == request.tournament_id)
        and (request.cycle_id is None or item.cycle_id == request.cycle_id)
    ]

    proposal_ids = {item.id for item in proposals}
    decisions_result = await db.execute(
        select(ArenaRiskGateDecision).where(ArenaRiskGateDecision.competition_id == request.competition_id)
    )
    all_risk_gate_decisions = list(decisions_result.scalars().all())
    risk_gate_decisions = [item for item in all_risk_gate_decisions if item.proposal_id in proposal_ids]

    records_result = await db.execute(select(DecisionRecord))
    all_decision_records = list(records_result.scalars().all())

    assignments_result = await db.execute(
        select(ArenaAgentBudgetAssignment).where(ArenaAgentBudgetAssignment.competition_id == request.competition_id)
    )
    all_assignments = list(assignments_result.scalars().all())

    risk_events_result = await db.execute(select(RiskEvent))
    all_risk_events = list(risk_events_result.scalars().all())

    input_hash_payload = _build_input_hash_payload(
        request=request,
        proposals=proposals,
        risk_gate_decisions=risk_gate_decisions,
        decision_records=all_decision_records,
        risk_events=all_risk_events,
        assignments=all_assignments,
    )
    snapshot_input_hash = hashlib.sha256(_stable_json(input_hash_payload).encode("utf-8")).hexdigest()
    idempotency_key = _build_snapshot_idempotency_key(scope=scope, payload_hash=snapshot_input_hash)

    existing = await db.scalar(
        select(ArenaPerformanceSnapshot)
        .where(ArenaPerformanceSnapshot.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return _deserialize_result(existing)

    latest_assignment_by_agent: dict[uuid.UUID, ArenaAgentBudgetAssignment] = {}
    for assignment in sorted(all_assignments, key=lambda row: row.created_at):
        latest_assignment_by_agent[assignment.agent_id] = assignment

    work_by_agent: dict[uuid.UUID, _AgentWorkingSet] = {}
    for proposal in proposals:
        assignment = latest_assignment_by_agent.get(proposal.agent_id)
        budget = Decimal(assignment.assigned_budget) if assignment is not None else None
        work = work_by_agent.setdefault(
            proposal.agent_id,
            _AgentWorkingSet(
                assigned_budget=budget,
                proposal_ids=[],
                risk_gate_decisions=[],
                decision_records=[],
            ),
        )
        work.proposal_ids.append(proposal.id)

    for decision in risk_gate_decisions:
        work = work_by_agent.get(decision.agent_id)
        if work is not None:
            work.risk_gate_decisions.append(decision)

    for record in all_decision_records:
        lineage = record.source_lineage or {}
        if not _lineage_matches(lineage, "arena_competitions", str(request.competition_id)):
            continue
        if request.tournament_id is not None and not _lineage_matches(
            lineage,
            "arena_tournaments",
            str(request.tournament_id),
        ):
            continue
        if request.cycle_id is not None and not _lineage_matches(lineage, "arena_cycles", str(request.cycle_id)):
            continue

        agent_ids = lineage.get("arena_agents") or []
        for agent_id_text in agent_ids:
            agent_id = uuid.UUID(agent_id_text)
            work = work_by_agent.setdefault(
                agent_id,
                _AgentWorkingSet(
                    assigned_budget=None,
                    proposal_ids=[],
                    risk_gate_decisions=[],
                    decision_records=[],
                ),
            )
            work.decision_records.append(record)

    agent_summaries = [
        _build_agent_summary(agent_id=agent_id, work=work_by_agent[agent_id])
        for agent_id in sorted(work_by_agent.keys(), key=str)
    ]

    portfolio = _portfolio_summary(request=request, agent_summaries=agent_summaries)
    snapshot_payload = {
        "agent_summaries": [_serialize_agent_summary(item) for item in agent_summaries],
        "portfolio": {
            "profit": _serialize_metric(portfolio.profit),
            "drawdown": _serialize_metric(portfolio.drawdown),
            "fee_drag": _serialize_metric(portfolio.fee_drag),
            "consistency": _serialize_metric(portfolio.consistency),
            "risk_discipline": _serialize_metric(portfolio.risk_discipline),
            "provenance": portfolio.provenance,
        },
        "snapshot_input_hash": snapshot_input_hash,
    }
    provenance = {
        **request.provenance,
        "snapshot_input_hash": snapshot_input_hash,
        "observational_only": True,
        "source_models": [
            "arena_cycle_proposals",
            "arena_risk_gate_decisions",
            "arena_agent_budget_assignments",
            "decision_records",
            "risk_events",
        ],
    }

    async with db.begin():
        snapshot = ArenaPerformanceSnapshot(
            idempotency_key=idempotency_key,
            competition_id=request.competition_id,
            tournament_id=request.tournament_id,
            cycle_id=request.cycle_id,
            snapshot_scope=scope,
            snapshot_input_hash=snapshot_input_hash,
            snapshot_payload=snapshot_payload,
            provenance=provenance,
            created_at=request.as_of,
        )
        db.add(snapshot)
        await db.flush()

        db.add(
            AuditLog(
                actor=request.actor,
                action="arena.performance_snapshot_recorded",
                entity_type="arena_performance_snapshot",
                entity_id=snapshot.id,
                before_state=None,
                after_state={
                    "competition_id": str(request.competition_id),
                    "tournament_id": str(request.tournament_id) if request.tournament_id else None,
                    "cycle_id": str(request.cycle_id) if request.cycle_id else None,
                    "snapshot_scope": scope,
                    "snapshot_input_hash": snapshot_input_hash,
                    "agent_count": len(agent_summaries),
                    "observational_only": True,
                },
            )
        )

    return ArenaPerformanceSnapshotResult(
        snapshot_id=snapshot.id,
        competition_id=request.competition_id,
        tournament_id=request.tournament_id,
        cycle_id=request.cycle_id,
        snapshot_scope=scope,
        snapshot_input_hash=snapshot_input_hash,
        agent_summaries=agent_summaries,
        portfolio=portfolio,
        provenance=provenance,
    )
