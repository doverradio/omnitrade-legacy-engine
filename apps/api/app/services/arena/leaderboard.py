from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.arena_comparison_record import ArenaComparisonRecord
from app.models.arena_leaderboard_snapshot import ArenaLeaderboardSnapshot
from app.models.arena_performance_snapshot import ArenaPerformanceSnapshot
from app.models.audit_log import AuditLog
from app.services.arena.contracts import (
    ArenaComparisonMetricContract,
    ArenaLeaderboardEntryContract,
    ArenaLeaderboardFilterContract,
    ArenaLeaderboardSnapshotRequest,
    ArenaLeaderboardSnapshotResult,
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


def _normalize_filters(filters: ArenaLeaderboardFilterContract) -> ArenaLeaderboardFilterContract:
    ids = None
    if filters.included_agent_ids:
        ids = sorted(filters.included_agent_ids, key=str)
    return ArenaLeaderboardFilterContract(
        included_agent_ids=ids,
        limit=filters.limit,
        availability_mode=filters.availability_mode,
    )


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


def _metric_from_payload(payload: dict[str, Any] | None, key: str) -> ArenaComparisonMetricContract:
    if payload is None or key not in payload:
        return _metric(None, "unknown", f"{key}_unknown")

    value = payload[key]
    return ArenaComparisonMetricContract(
        value=_to_decimal(value.get("value")),
        status=str(value.get("status") or "unknown"),
        reason=value.get("reason"),
    )


def _collect_agent_metrics(
    *,
    performance_snapshot: ArenaPerformanceSnapshot | None,
    comparison_record: ArenaComparisonRecord | None,
) -> dict[uuid.UUID, dict[str, ArenaComparisonMetricContract]]:
    metrics_by_agent: dict[uuid.UUID, dict[str, ArenaComparisonMetricContract]] = {}

    if performance_snapshot is not None:
        for summary in performance_snapshot.snapshot_payload.get("agent_summaries", []):
            agent_id = uuid.UUID(summary["agent_id"])
            metrics_by_agent.setdefault(agent_id, {})
            metrics_by_agent[agent_id]["profit"] = _metric_from_payload(summary, "profit")
            metrics_by_agent[agent_id]["drawdown"] = _metric_from_payload(summary, "drawdown")
            metrics_by_agent[agent_id]["fee_drag"] = _metric_from_payload(summary, "fee_drag")
            metrics_by_agent[agent_id]["consistency"] = _metric_from_payload(summary, "consistency")
            metrics_by_agent[agent_id]["risk_discipline"] = _metric_from_payload(summary, "risk_discipline")

    if comparison_record is not None:
        for summary in comparison_record.comparison_payload.get("agent_summaries", []):
            agent_id = uuid.UUID(summary["agent_id"])
            metrics_by_agent.setdefault(agent_id, {})
            metrics_by_agent[agent_id]["decision_quality"] = _metric_from_payload(summary, "decision_quality")
            metrics_by_agent[agent_id]["explainability"] = _metric_from_payload(
                summary,
                "explainability_support_ratio",
            )

    required = {
        "decision_quality",
        "profit",
        "drawdown",
        "fee_drag",
        "consistency",
        "risk_discipline",
        "explainability",
    }

    for agent_id in list(metrics_by_agent.keys()):
        for key in required:
            metrics_by_agent[agent_id].setdefault(key, _metric(None, "unknown", f"{key}_unknown"))

    return metrics_by_agent


def _normalized_component_scores(
    *,
    metrics_by_agent: dict[uuid.UUID, dict[str, ArenaComparisonMetricContract]],
) -> dict[uuid.UUID, dict[str, Decimal | None]]:
    positive = {"decision_quality", "profit", "consistency", "risk_discipline", "explainability"}
    negative = {"drawdown", "fee_drag"}

    component_scores: dict[uuid.UUID, dict[str, Decimal | None]] = {
        agent_id: {} for agent_id in metrics_by_agent
    }

    for key in sorted(positive | negative):
        available = [
            item[key].value
            for item in metrics_by_agent.values()
            if item[key].status == "available" and item[key].value is not None
        ]
        if not available:
            for agent_id in component_scores:
                component_scores[agent_id][key] = None
            continue

        minimum = min(available)
        maximum = max(available)

        for agent_id, metrics in metrics_by_agent.items():
            metric = metrics[key]
            if metric.status != "available" or metric.value is None:
                component_scores[agent_id][key] = None
                continue

            if maximum == minimum:
                score = Decimal("1")
            elif key in positive:
                score = (metric.value - minimum) / (maximum - minimum)
            else:
                score = (maximum - metric.value) / (maximum - minimum)

            component_scores[agent_id][key] = score.quantize(Decimal("0.0001"))

    return component_scores


def _composite_scores(
    *,
    component_scores: dict[uuid.UUID, dict[str, Decimal | None]],
) -> dict[uuid.UUID, ArenaComparisonMetricContract]:
    # Profit is deliberately not the dominant factor.
    weights = {
        "decision_quality": Decimal("0.23"),
        "profit": Decimal("0.17"),
        "drawdown": Decimal("0.15"),
        "fee_drag": Decimal("0.10"),
        "consistency": Decimal("0.13"),
        "risk_discipline": Decimal("0.12"),
        "explainability": Decimal("0.10"),
    }

    output: dict[uuid.UUID, ArenaComparisonMetricContract] = {}
    for agent_id, values in component_scores.items():
        weighted_sum = Decimal("0")
        total_weight = Decimal("0")
        for key, weight in weights.items():
            value = values.get(key)
            if value is None:
                continue
            weighted_sum += value * weight
            total_weight += weight

        if total_weight == Decimal("0"):
            output[agent_id] = _metric(None, "unknown", "no_available_ranking_dimensions")
            continue

        composite = (weighted_sum / total_weight).quantize(Decimal("0.0001"))
        output[agent_id] = _metric(composite, "available", None)

    return output


def _build_entries(
    *,
    metrics_by_agent: dict[uuid.UUID, dict[str, ArenaComparisonMetricContract]],
    composite_scores: dict[uuid.UUID, ArenaComparisonMetricContract],
) -> list[ArenaLeaderboardEntryContract]:
    rows: list[tuple[uuid.UUID, ArenaComparisonMetricContract, dict[str, ArenaComparisonMetricContract]]] = []
    for agent_id, metrics in metrics_by_agent.items():
        rows.append((agent_id, composite_scores[agent_id], metrics))

    def _sort_key(item: tuple[uuid.UUID, ArenaComparisonMetricContract, dict[str, ArenaComparisonMetricContract]]) -> tuple[Any, ...]:
        agent_id, score_metric, dims = item
        score = score_metric.value if score_metric.value is not None else Decimal("-1")
        quality = dims["decision_quality"].value if dims["decision_quality"].value is not None else Decimal("-1")
        discipline = dims["risk_discipline"].value if dims["risk_discipline"].value is not None else Decimal("-1")
        profit = dims["profit"].value if dims["profit"].value is not None else Decimal("-1")
        score_missing = 1 if score_metric.status != "available" else 0
        return (score_missing, -score, -quality, -discipline, -profit, str(agent_id))

    rows.sort(key=_sort_key)

    entries: list[ArenaLeaderboardEntryContract] = []
    for index, (agent_id, score, dims) in enumerate(rows, start=1):
        entries.append(
            ArenaLeaderboardEntryContract(
                rank=index,
                agent_id=agent_id,
                composite_rank_score=score,
                decision_quality=dims["decision_quality"],
                profit=dims["profit"],
                drawdown=dims["drawdown"],
                fee_drag=dims["fee_drag"],
                consistency=dims["consistency"],
                risk_discipline=dims["risk_discipline"],
                explainability=dims["explainability"],
                evidence_provenance={
                    "dimensions": [
                        "decision_quality",
                        "profit",
                        "drawdown",
                        "fee_drag",
                        "consistency",
                        "risk_discipline",
                        "explainability",
                    ]
                },
            )
        )

    return entries


def _apply_filters(
    *,
    entries: list[ArenaLeaderboardEntryContract],
    filters: ArenaLeaderboardFilterContract,
) -> list[ArenaLeaderboardEntryContract]:
    filtered = list(entries)

    if filters.included_agent_ids:
        included = set(filters.included_agent_ids)
        filtered = [item for item in filtered if item.agent_id in included]

    if filters.availability_mode == "known_only":
        filtered = [item for item in filtered if item.composite_rank_score.status == "available"]

    if filters.limit is not None:
        filtered = filtered[: filters.limit]

    return [
        ArenaLeaderboardEntryContract(
            rank=index,
            agent_id=item.agent_id,
            composite_rank_score=item.composite_rank_score,
            decision_quality=item.decision_quality,
            profit=item.profit,
            drawdown=item.drawdown,
            fee_drag=item.fee_drag,
            consistency=item.consistency,
            risk_discipline=item.risk_discipline,
            explainability=item.explainability,
            evidence_provenance=item.evidence_provenance,
        )
        for index, item in enumerate(filtered, start=1)
    ]


def _serialize_entry(item: ArenaLeaderboardEntryContract) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "agent_id": str(item.agent_id),
        "composite_rank_score": _serialize_metric(item.composite_rank_score),
        "decision_quality": _serialize_metric(item.decision_quality),
        "profit": _serialize_metric(item.profit),
        "drawdown": _serialize_metric(item.drawdown),
        "fee_drag": _serialize_metric(item.fee_drag),
        "consistency": _serialize_metric(item.consistency),
        "risk_discipline": _serialize_metric(item.risk_discipline),
        "explainability": _serialize_metric(item.explainability),
        "evidence_provenance": item.evidence_provenance,
    }


def _deserialize_entry(payload: dict[str, Any]) -> ArenaLeaderboardEntryContract:
    return ArenaLeaderboardEntryContract(
        rank=int(payload["rank"]),
        agent_id=uuid.UUID(payload["agent_id"]),
        composite_rank_score=_deserialize_metric(payload["composite_rank_score"]),
        decision_quality=_deserialize_metric(payload["decision_quality"]),
        profit=_deserialize_metric(payload["profit"]),
        drawdown=_deserialize_metric(payload["drawdown"]),
        fee_drag=_deserialize_metric(payload["fee_drag"]),
        consistency=_deserialize_metric(payload["consistency"]),
        risk_discipline=_deserialize_metric(payload["risk_discipline"]),
        explainability=_deserialize_metric(payload["explainability"]),
        evidence_provenance=dict(payload.get("evidence_provenance") or {}),
    )


def _deserialize_result(snapshot: ArenaLeaderboardSnapshot) -> ArenaLeaderboardSnapshotResult:
    payload = snapshot.ranking_payload
    filter_payload = snapshot.filter_payload

    return ArenaLeaderboardSnapshotResult(
        leaderboard_snapshot_id=snapshot.id,
        ranking_hash=snapshot.ranking_hash,
        snapshot_scope=snapshot.snapshot_scope,
        competition_id=snapshot.competition_id,
        tournament_id=snapshot.tournament_id,
        cycle_id=snapshot.cycle_id,
        ranking_methodology_version=snapshot.ranking_methodology_version,
        snapshot_timestamp=snapshot.snapshot_timestamp,
        filters=ArenaLeaderboardFilterContract(
            included_agent_ids=[uuid.UUID(item) for item in filter_payload.get("included_agent_ids", [])]
            if filter_payload.get("included_agent_ids") is not None
            else None,
            limit=filter_payload.get("limit"),
            availability_mode=str(filter_payload.get("availability_mode") or "all"),
        ),
        entries=[_deserialize_entry(item) for item in payload.get("entries", [])],
        evidence_sources=snapshot.evidence_sources,
        provenance=snapshot.provenance,
    )


async def build_arena_leaderboard_snapshot(
    *,
    db: AsyncSession,
    request: ArenaLeaderboardSnapshotRequest,
) -> ArenaLeaderboardSnapshotResult:
    filters = _normalize_filters(request.filters)
    scope = _scope(tournament_id=request.tournament_id, cycle_id=request.cycle_id)

    perf_result = await db.execute(
        select(ArenaPerformanceSnapshot).where(ArenaPerformanceSnapshot.competition_id == request.competition_id)
    )
    perf_rows = [
        item
        for item in list(perf_result.scalars().all())
        if (request.tournament_id is None or item.tournament_id == request.tournament_id)
        and (request.cycle_id is None or item.cycle_id == request.cycle_id)
    ]
    perf_rows.sort(key=lambda item: item.created_at, reverse=True)
    latest_perf = perf_rows[0] if perf_rows else None

    comparison_result = await db.execute(
        select(ArenaComparisonRecord).where(ArenaComparisonRecord.competition_id == request.competition_id)
    )
    comparison_rows = [
        item
        for item in list(comparison_result.scalars().all())
        if (request.tournament_id is None or item.tournament_id == request.tournament_id)
        and (request.cycle_id is None or item.cycle_id == request.cycle_id)
    ]
    comparison_rows.sort(key=lambda item: item.comparison_timestamp, reverse=True)
    latest_comparison = comparison_rows[0] if comparison_rows else None

    metrics_by_agent = _collect_agent_metrics(
        performance_snapshot=latest_perf,
        comparison_record=latest_comparison,
    )
    component_scores = _normalized_component_scores(metrics_by_agent=metrics_by_agent)
    composite_scores = _composite_scores(component_scores=component_scores)
    entries = _build_entries(metrics_by_agent=metrics_by_agent, composite_scores=composite_scores)
    filtered_entries = _apply_filters(entries=entries, filters=filters)

    filter_payload = {
        "included_agent_ids": [str(item) for item in filters.included_agent_ids] if filters.included_agent_ids else None,
        "limit": filters.limit,
        "availability_mode": filters.availability_mode,
    }
    ranking_payload = {
        "entries": [_serialize_entry(item) for item in filtered_entries],
        "methodology": {
            "version": request.ranking_methodology_version,
            "multi_factor": True,
            "profit_not_sole_criterion": True,
        },
    }

    ranking_hash_payload = {
        "scope": scope,
        "competition_id": str(request.competition_id),
        "tournament_id": str(request.tournament_id) if request.tournament_id else None,
        "cycle_id": str(request.cycle_id) if request.cycle_id else None,
        "as_of": request.as_of.isoformat(),
        "ranking_methodology_version": request.ranking_methodology_version,
        "filter_payload": filter_payload,
        "ranking_payload": ranking_payload,
        "performance_snapshot_input_hash": latest_perf.snapshot_input_hash if latest_perf else None,
        "comparison_hash": latest_comparison.comparison_hash if latest_comparison else None,
    }
    ranking_hash = hashlib.sha256(_stable_json(ranking_hash_payload).encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        _stable_json({"kind": "arena_leaderboard_snapshot", "ranking_hash": ranking_hash}).encode("utf-8")
    ).hexdigest()

    existing = await db.scalar(
        select(ArenaLeaderboardSnapshot)
        .where(ArenaLeaderboardSnapshot.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return _deserialize_result(existing)

    evidence_sources = {
        "arena_performance_snapshot_id": str(latest_perf.id) if latest_perf else None,
        "arena_comparison_record_id": str(latest_comparison.id) if latest_comparison else None,
    }
    provenance = {
        **request.provenance,
        "ranking_hash": ranking_hash,
        "deterministic": True,
        "observational_only": True,
    }

    async with db.begin():
        snapshot = ArenaLeaderboardSnapshot(
            idempotency_key=idempotency_key,
            ranking_hash=ranking_hash,
            competition_id=request.competition_id,
            tournament_id=request.tournament_id,
            cycle_id=request.cycle_id,
            snapshot_scope=scope,
            ranking_methodology_version=request.ranking_methodology_version,
            filter_payload=filter_payload,
            evidence_sources=evidence_sources,
            ranking_payload=ranking_payload,
            provenance=provenance,
            snapshot_timestamp=request.as_of,
            created_at=request.as_of,
        )
        db.add(snapshot)
        await db.flush()

        db.add(
            AuditLog(
                actor=request.actor,
                action="arena.leaderboard_snapshot_recorded",
                entity_type="arena_leaderboard_snapshot",
                entity_id=snapshot.id,
                before_state=None,
                after_state={
                    "ranking_hash": ranking_hash,
                    "ranking_methodology_version": request.ranking_methodology_version,
                    "snapshot_scope": scope,
                    "competition_id": str(request.competition_id),
                    "tournament_id": str(request.tournament_id) if request.tournament_id else None,
                    "cycle_id": str(request.cycle_id) if request.cycle_id else None,
                    "entry_count": len(filtered_entries),
                },
            )
        )

    return ArenaLeaderboardSnapshotResult(
        leaderboard_snapshot_id=snapshot.id,
        ranking_hash=ranking_hash,
        snapshot_scope=scope,
        competition_id=request.competition_id,
        tournament_id=request.tournament_id,
        cycle_id=request.cycle_id,
        ranking_methodology_version=request.ranking_methodology_version,
        snapshot_timestamp=request.as_of,
        filters=filters,
        entries=filtered_entries,
        evidence_sources=evidence_sources,
        provenance=provenance,
    )


async def read_latest_arena_leaderboard_snapshot(
    *,
    db: AsyncSession,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID | None,
    cycle_id: uuid.UUID | None,
    filters: ArenaLeaderboardFilterContract,
) -> ArenaLeaderboardSnapshotResult | None:
    normalized_filters = _normalize_filters(filters)
    filter_payload = {
        "included_agent_ids": [str(item) for item in normalized_filters.included_agent_ids]
        if normalized_filters.included_agent_ids
        else None,
        "limit": normalized_filters.limit,
        "availability_mode": normalized_filters.availability_mode,
    }

    result = await db.execute(
        select(ArenaLeaderboardSnapshot).where(ArenaLeaderboardSnapshot.competition_id == competition_id)
    )
    rows = [
        item
        for item in list(result.scalars().all())
        if (tournament_id is None or item.tournament_id == tournament_id)
        and (cycle_id is None or item.cycle_id == cycle_id)
        and item.filter_payload == filter_payload
    ]
    if not rows:
        return None

    rows.sort(key=lambda item: item.snapshot_timestamp, reverse=True)
    return _deserialize_result(rows[0])
