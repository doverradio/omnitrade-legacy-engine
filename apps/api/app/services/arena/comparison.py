from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.arena_comparison_record import ArenaComparisonRecord
from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.audit_log import AuditLog
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_explainability_record import DecisionExplainabilityRecord
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.services.arena.contracts import (
    ArenaAgentComparisonSummaryContract,
    ArenaComparisonMetricContract,
    ArenaComparisonRecordRequest,
    ArenaComparisonRecordResult,
)


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _scope(*, tournament_id: uuid.UUID | None, cycle_id: uuid.UUID | None) -> str:
    if cycle_id is not None:
        return "cycle"
    if tournament_id is not None:
        return "tournament"
    return "competition"


def _metric(value: Decimal | None, status: str, reason: str | None) -> ArenaComparisonMetricContract:
    return ArenaComparisonMetricContract(value=value, status=status, reason=reason)


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


def _decision_ids_by_agent(
    *,
    decision_records: list[DecisionRecord],
    request: ArenaComparisonRecordRequest,
) -> dict[uuid.UUID, list[uuid.UUID]]:
    by_agent: dict[uuid.UUID, list[uuid.UUID]] = {}
    for record in decision_records:
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

        for agent_id_str in lineage.get("arena_agents") or []:
            agent_id = uuid.UUID(agent_id_str)
            by_agent.setdefault(agent_id, []).append(record.decision_id)

    return by_agent


def _quality_metric(
    *,
    decision_ids: list[uuid.UUID],
    quality_scores: list[DecisionQualityScore],
) -> ArenaComparisonMetricContract:
    if not decision_ids:
        return _metric(None, "unknown", "no_decisions_for_agent")

    scores = [item.composite_score for item in quality_scores if item.decision_id in set(decision_ids)]
    if not scores:
        return _metric(None, "unavailable", "decision_quality_missing")

    total = sum((Decimal(item) for item in scores), start=Decimal("0"))
    return _metric((total / Decimal(len(scores))).quantize(Decimal("0.0001")), "available", None)


def _explainability_metric(
    *,
    decision_ids: list[uuid.UUID],
    explainability_records: list[DecisionExplainabilityRecord],
) -> ArenaComparisonMetricContract:
    if not decision_ids:
        return _metric(None, "unknown", "no_decisions_for_agent")

    rows = [item for item in explainability_records if item.decision_id in set(decision_ids)]
    if not rows:
        return _metric(None, "unavailable", "explainability_records_missing")

    known = [item for item in rows if item.availability_state == "known"]
    if not known:
        return _metric(None, "unavailable", "explainability_unknown_or_unavailable")

    supporting = sum(1 for item in known if item.evidence_role == "supporting")
    ratio = Decimal(supporting) / Decimal(len(known))
    return _metric(ratio.quantize(Decimal("0.0001")), "available", None)


def _counterfactual_metric(
    *,
    decision_ids: list[uuid.UUID],
    counterfactual_records: list[DecisionCounterfactualResult],
) -> ArenaComparisonMetricContract:
    if not decision_ids:
        return _metric(None, "unknown", "no_decisions_for_agent")

    rows = [item for item in counterfactual_records if item.decision_id in set(decision_ids)]
    if not rows:
        return _metric(None, "unavailable", "counterfactual_records_missing")

    resolved = [item for item in rows if item.evaluation_state == "resolved" and item.actual_action_correct is not None]
    if not resolved:
        return _metric(None, "unavailable", "counterfactual_unresolved")

    correct = sum(1 for item in resolved if item.actual_action_correct)
    ratio = Decimal(correct) / Decimal(len(resolved))
    return _metric(ratio.quantize(Decimal("0.0001")), "available", None)


def _aggregate_average(
    metrics: list[ArenaComparisonMetricContract],
    reason_if_empty: str,
) -> ArenaComparisonMetricContract:
    values = [item.value for item in metrics if item.status == "available" and item.value is not None]
    if not values:
        return _metric(None, "unknown", reason_if_empty)

    total = sum(values, start=Decimal("0"))
    return _metric((total / Decimal(len(values))).quantize(Decimal("0.0001")), "available", None)


def _serialize_metric(metric: ArenaComparisonMetricContract) -> dict[str, Any]:
    return {
        "value": format(metric.value, "f") if metric.value is not None else None,
        "status": metric.status,
        "reason": metric.reason,
    }


def _deserialize_metric(payload: dict[str, Any]) -> ArenaComparisonMetricContract:
    return ArenaComparisonMetricContract(
        value=_to_decimal(payload.get("value")),
        status=str(payload.get("status") or "unknown"),
        reason=payload.get("reason"),
    )


def _serialize_summary(summary: ArenaAgentComparisonSummaryContract) -> dict[str, Any]:
    return {
        "agent_id": str(summary.agent_id),
        "decision_quality": _serialize_metric(summary.decision_quality),
        "explainability_support_ratio": _serialize_metric(summary.explainability_support_ratio),
        "counterfactual_correctness": _serialize_metric(summary.counterfactual_correctness),
        "evidence_provenance": summary.evidence_provenance,
    }


def _deserialize_result(record: ArenaComparisonRecord) -> ArenaComparisonRecordResult:
    payload = record.comparison_payload

    summaries = [
        ArenaAgentComparisonSummaryContract(
            agent_id=uuid.UUID(item["agent_id"]),
            decision_quality=_deserialize_metric(item["decision_quality"]),
            explainability_support_ratio=_deserialize_metric(item["explainability_support_ratio"]),
            counterfactual_correctness=_deserialize_metric(item["counterfactual_correctness"]),
            evidence_provenance=dict(item.get("evidence_provenance") or {}),
        )
        for item in payload.get("agent_summaries", [])
    ]

    portfolio_payload = payload.get("portfolio_dimensions") or {}

    return ArenaComparisonRecordResult(
        comparison_record_id=record.id,
        comparison_hash=record.comparison_hash,
        comparison_scope=record.comparison_scope,
        competition_id=record.competition_id,
        tournament_id=record.tournament_id,
        cycle_id=record.cycle_id,
        compared_agent_ids=[uuid.UUID(item) for item in record.compared_agent_ids],
        comparison_timestamp=record.comparison_timestamp,
        agent_summaries=summaries,
        portfolio_dimensions={
            "decision_quality": _deserialize_metric(portfolio_payload.get("decision_quality") or {}),
            "explainability_support_ratio": _deserialize_metric(
                portfolio_payload.get("explainability_support_ratio") or {}
            ),
            "counterfactual_correctness": _deserialize_metric(portfolio_payload.get("counterfactual_correctness") or {}),
        },
        evidence_sources=record.evidence_sources,
        provenance=record.provenance,
    )


async def build_arena_comparison_record(
    *,
    db: AsyncSession,
    request: ArenaComparisonRecordRequest,
) -> ArenaComparisonRecordResult:
    scope = _scope(tournament_id=request.tournament_id, cycle_id=request.cycle_id)

    snapshot_result = await db.execute(
        select(ArenaPerformanceSnapshot).where(ArenaPerformanceSnapshot.competition_id == request.competition_id)
    )
    all_snapshots = list(snapshot_result.scalars().all())
    snapshots = [
        item
        for item in all_snapshots
        if (request.tournament_id is None or item.tournament_id == request.tournament_id)
        and (request.cycle_id is None or item.cycle_id == request.cycle_id)
    ]
    snapshots.sort(key=lambda item: item.created_at, reverse=True)

    latest_snapshot = snapshots[0] if snapshots else None
    snapshot_agents = []
    if latest_snapshot is not None:
        snapshot_agents = [uuid.UUID(item["agent_id"]) for item in latest_snapshot.snapshot_payload.get("agent_summaries", [])]

    requested_agents = sorted(request.compared_agent_ids or [], key=str)
    compared_agent_ids = requested_agents or sorted(snapshot_agents, key=str)

    decision_result = await db.execute(select(DecisionRecord))
    decision_records = list(decision_result.scalars().all())
    decision_ids_by_agent = _decision_ids_by_agent(decision_records=decision_records, request=request)

    quality_result = await db.execute(select(DecisionQualityScore))
    quality_scores = list(quality_result.scalars().all())

    explainability_result = await db.execute(select(DecisionExplainabilityRecord))
    explainability_records = list(explainability_result.scalars().all())

    counterfactual_result = await db.execute(select(DecisionCounterfactualResult))
    counterfactual_records = list(counterfactual_result.scalars().all())

    summaries: list[ArenaAgentComparisonSummaryContract] = []
    for agent_id in compared_agent_ids:
        decision_ids = decision_ids_by_agent.get(agent_id, [])
        summary = ArenaAgentComparisonSummaryContract(
            agent_id=agent_id,
            decision_quality=_quality_metric(decision_ids=decision_ids, quality_scores=quality_scores),
            explainability_support_ratio=_explainability_metric(
                decision_ids=decision_ids,
                explainability_records=explainability_records,
            ),
            counterfactual_correctness=_counterfactual_metric(
                decision_ids=decision_ids,
                counterfactual_records=counterfactual_records,
            ),
            evidence_provenance={
                "decision_ids": [str(item) for item in sorted(decision_ids, key=str)],
                "sources": [
                    "arena_performance_snapshots",
                    "decision_quality_scores",
                    "decision_explainability_records",
                    "decision_counterfactual_results",
                ],
            },
        )
        summaries.append(summary)

    portfolio_dimensions = {
        "decision_quality": _aggregate_average(
            [item.decision_quality for item in summaries],
            "portfolio_decision_quality_unknown_due_to_agent_data_gap",
        ),
        "explainability_support_ratio": _aggregate_average(
            [item.explainability_support_ratio for item in summaries],
            "portfolio_explainability_unknown_due_to_agent_data_gap",
        ),
        "counterfactual_correctness": _aggregate_average(
            [item.counterfactual_correctness for item in summaries],
            "portfolio_counterfactual_unknown_due_to_agent_data_gap",
        ),
    }

    comparison_payload = {
        "agent_summaries": [_serialize_summary(item) for item in summaries],
        "portfolio_dimensions": {
            key: _serialize_metric(value)
            for key, value in portfolio_dimensions.items()
        },
    }
    hash_payload = {
        "scope": scope,
        "competition_id": str(request.competition_id),
        "tournament_id": str(request.tournament_id) if request.tournament_id else None,
        "cycle_id": str(request.cycle_id) if request.cycle_id else None,
        "as_of": request.as_of.isoformat(),
        "compared_agent_ids": [str(item) for item in compared_agent_ids],
        "comparison_payload": comparison_payload,
        "performance_snapshot_input_hash": latest_snapshot.snapshot_input_hash if latest_snapshot else None,
    }
    comparison_hash = hashlib.sha256(_stable_json(hash_payload).encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        _stable_json({"kind": "arena_comparison_record", "comparison_hash": comparison_hash}).encode("utf-8")
    ).hexdigest()

    existing = await db.scalar(
        select(ArenaComparisonRecord)
        .where(ArenaComparisonRecord.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return _deserialize_result(existing)

    evidence_sources = {
        "arena_performance_snapshot_ids": [str(item.id) for item in snapshots[:1]],
        "decision_quality_score_ids": [str(item.id) for item in quality_scores],
        "decision_explainability_record_ids": [str(item.id) for item in explainability_records],
        "decision_counterfactual_result_ids": [str(item.id) for item in counterfactual_records],
    }
    provenance = {
        **request.provenance,
        "comparison_hash": comparison_hash,
        "deterministic": True,
        "observational_only": True,
    }

    async with db.begin():
        record = ArenaComparisonRecord(
            idempotency_key=idempotency_key,
            comparison_hash=comparison_hash,
            competition_id=request.competition_id,
            tournament_id=request.tournament_id,
            cycle_id=request.cycle_id,
            comparison_scope=scope,
            compared_agent_ids=[str(item) for item in compared_agent_ids],
            comparison_payload=comparison_payload,
            evidence_sources=evidence_sources,
            provenance=provenance,
            comparison_timestamp=request.as_of,
            created_at=request.as_of,
        )
        db.add(record)
        await db.flush()

        db.add(
            AuditLog(
                actor=request.actor,
                action="arena.comparison_recorded",
                entity_type="arena_comparison_record",
                entity_id=record.id,
                before_state=None,
                after_state={
                    "comparison_hash": comparison_hash,
                    "comparison_scope": scope,
                    "competition_id": str(request.competition_id),
                    "tournament_id": str(request.tournament_id) if request.tournament_id else None,
                    "cycle_id": str(request.cycle_id) if request.cycle_id else None,
                    "compared_agent_ids": [str(item) for item in compared_agent_ids],
                },
            )
        )

    return ArenaComparisonRecordResult(
        comparison_record_id=record.id,
        comparison_hash=comparison_hash,
        comparison_scope=scope,
        competition_id=request.competition_id,
        tournament_id=request.tournament_id,
        cycle_id=request.cycle_id,
        compared_agent_ids=compared_agent_ids,
        comparison_timestamp=request.as_of,
        agent_summaries=summaries,
        portfolio_dimensions=portfolio_dimensions,
        evidence_sources=evidence_sources,
        provenance=provenance,
    )


async def read_latest_arena_comparison_record(
    *,
    db: AsyncSession,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID | None,
    cycle_id: uuid.UUID | None,
) -> ArenaComparisonRecordResult | None:
    result = await db.execute(
        select(ArenaComparisonRecord).where(ArenaComparisonRecord.competition_id == competition_id)
    )
    rows = list(result.scalars().all())
    scoped = [
        item
        for item in rows
        if (tournament_id is None or item.tournament_id == tournament_id)
        and (cycle_id is None or item.cycle_id == cycle_id)
    ]
    if not scoped:
        return None

    scoped.sort(key=lambda item: item.comparison_timestamp, reverse=True)
    return _deserialize_result(scoped[0])
