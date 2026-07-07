from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.arena_tournament import ArenaTournament
from app.models.arena_tournament_history_record import ArenaTournamentHistoryRecord
from app.models.audit_log import AuditLog
from app.services.arena.contracts import (
    ArenaTournamentAgentOutcomeContract,
    ArenaTournamentLifecycleEventRequest,
    ArenaTournamentLifecycleEventResult,
    ArenaTournamentLifecycleReadModel,
    ArenaTournamentMetricContract,
    ArenaTournamentStandingContract,
)

ORDERING_RULES = [
    "composite_score_desc",
    "decision_quality_desc",
    "risk_discipline_desc",
    "drawdown_asc",
    "fee_drag_asc",
    "profit_desc",
    "agent_id_asc",
]

TIE_BREAK_RULES = [
    "decision_quality_desc",
    "risk_discipline_desc",
    "drawdown_asc",
    "fee_drag_asc",
    "profit_desc",
    "agent_id_asc",
]


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _serialize_metric(metric: ArenaTournamentMetricContract) -> dict[str, Any]:
    return {
        "value": format(metric.value, "f") if metric.value is not None else None,
        "status": metric.status,
        "reason": metric.reason,
    }


def _deserialize_metric(payload: dict[str, Any]) -> ArenaTournamentMetricContract:
    return ArenaTournamentMetricContract(
        value=_to_decimal(payload.get("value")),
        status=str(payload.get("status") or "unknown"),
        reason=payload.get("reason"),
    )


def _normalize_outcome(item: ArenaTournamentAgentOutcomeContract) -> ArenaTournamentAgentOutcomeContract:
    return ArenaTournamentAgentOutcomeContract(
        agent_id=item.agent_id,
        composite_score=item.composite_score,
        decision_quality=item.decision_quality,
        risk_discipline=item.risk_discipline,
        drawdown=item.drawdown,
        fee_drag=item.fee_drag,
        profit=item.profit,
        evidence_provenance=item.evidence_provenance,
    )


def _sorting_key(item: ArenaTournamentAgentOutcomeContract) -> tuple[Any, ...]:
    def _available_value(metric: ArenaTournamentMetricContract, unknown: Decimal, *, desc: bool) -> Decimal:
        if metric.status != "available" or metric.value is None:
            return unknown
        return metric.value if not desc else -metric.value

    missing_composite = 1 if item.composite_score.status != "available" or item.composite_score.value is None else 0
    return (
        missing_composite,
        _available_value(item.composite_score, Decimal("999999999"), desc=True),
        _available_value(item.decision_quality, Decimal("999999999"), desc=True),
        _available_value(item.risk_discipline, Decimal("999999999"), desc=True),
        _available_value(item.drawdown, Decimal("999999999"), desc=False),
        _available_value(item.fee_drag, Decimal("999999999"), desc=False),
        _available_value(item.profit, Decimal("999999999"), desc=True),
        str(item.agent_id),
    )


def _rank_standings(standings: list[ArenaTournamentAgentOutcomeContract]) -> list[ArenaTournamentStandingContract]:
    ordered = sorted((_normalize_outcome(item) for item in standings), key=_sorting_key)
    return [
        ArenaTournamentStandingContract(
            rank=index,
            agent_id=item.agent_id,
            composite_score=item.composite_score,
            decision_quality=item.decision_quality,
            risk_discipline=item.risk_discipline,
            drawdown=item.drawdown,
            fee_drag=item.fee_drag,
            profit=item.profit,
            evidence_provenance=item.evidence_provenance,
        )
        for index, item in enumerate(ordered, start=1)
    ]


def _serialize_standing(item: ArenaTournamentStandingContract) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "agent_id": str(item.agent_id),
        "composite_score": _serialize_metric(item.composite_score),
        "decision_quality": _serialize_metric(item.decision_quality),
        "risk_discipline": _serialize_metric(item.risk_discipline),
        "drawdown": _serialize_metric(item.drawdown),
        "fee_drag": _serialize_metric(item.fee_drag),
        "profit": _serialize_metric(item.profit),
        "evidence_provenance": item.evidence_provenance,
    }


def _deserialize_standing(payload: dict[str, Any]) -> ArenaTournamentStandingContract:
    return ArenaTournamentStandingContract(
        rank=int(payload["rank"]),
        agent_id=uuid.UUID(payload["agent_id"]),
        composite_score=_deserialize_metric(payload["composite_score"]),
        decision_quality=_deserialize_metric(payload["decision_quality"]),
        risk_discipline=_deserialize_metric(payload["risk_discipline"]),
        drawdown=_deserialize_metric(payload["drawdown"]),
        fee_drag=_deserialize_metric(payload["fee_drag"]),
        profit=_deserialize_metric(payload["profit"]),
        evidence_provenance=dict(payload.get("evidence_provenance") or {}),
    )


def _deserialize_result(item: ArenaTournamentHistoryRecord) -> ArenaTournamentLifecycleEventResult:
    event_payload = item.event_payload
    return ArenaTournamentLifecycleEventResult(
        history_record_id=item.id,
        event_hash=item.event_hash,
        tournament_id=item.tournament_id,
        competition_id=item.competition_id,
        sequence_number=item.sequence_number,
        event_type=item.event_type,
        lifecycle_state=item.lifecycle_state,
        event_timestamp=item.event_timestamp,
        schedule_payload=item.schedule_payload,
        replay_metadata=item.replay_metadata,
        standings=[_deserialize_standing(row) for row in event_payload.get("standings", [])],
        tie_break_rules=list(item.tie_break_rules),
        ordering_rules=list(item.ordering_rules),
        provenance=item.provenance,
    )


async def record_arena_tournament_lifecycle_event(
    *,
    db: AsyncSession,
    request: ArenaTournamentLifecycleEventRequest,
) -> ArenaTournamentLifecycleEventResult:
    if request.event_type not in {"scheduled", "activated", "completed", "archived", "standings_recorded"}:
        raise InvalidRequestError("Invalid tournament event_type")
    if request.lifecycle_state not in {"planned", "active", "completed", "archived"}:
        raise InvalidRequestError("Invalid tournament lifecycle_state")

    tournament = await db.scalar(
        select(ArenaTournament)
        .where(
            ArenaTournament.id == request.tournament_id,
            ArenaTournament.competition_id == request.competition_id,
        )
        .limit(1)
    )
    if tournament is None:
        raise InvalidRequestError("Tournament does not belong to competition")

    standings = _rank_standings(request.standings)
    event_payload = {
        "standings": [_serialize_standing(item) for item in standings],
        "ordered_agent_ids": [str(item.agent_id) for item in standings],
    }

    hash_payload = {
        "competition_id": str(request.competition_id),
        "tournament_id": str(request.tournament_id),
        "event_type": request.event_type,
        "lifecycle_state": request.lifecycle_state,
        "schedule_payload": request.schedule_payload,
        "replay_metadata": request.replay_metadata,
        "event_payload": event_payload,
        "event_timestamp": request.as_of.isoformat(),
        "ordering_rules": ORDERING_RULES,
        "tie_break_rules": TIE_BREAK_RULES,
    }
    event_hash = hashlib.sha256(_stable_json(hash_payload).encode("utf-8")).hexdigest()
    idempotency_key = hashlib.sha256(
        _stable_json(
            {
                "kind": "arena_tournament_history_record",
                "event_hash": event_hash,
            }
        ).encode("utf-8")
    ).hexdigest()

    existing = await db.scalar(
        select(ArenaTournamentHistoryRecord)
        .where(ArenaTournamentHistoryRecord.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return _deserialize_result(existing)

    history_result = await db.execute(
        select(ArenaTournamentHistoryRecord).where(
            ArenaTournamentHistoryRecord.tournament_id == request.tournament_id
        )
    )
    history_rows = list(history_result.scalars().all())
    history_rows.sort(key=lambda item: (item.sequence_number, item.event_timestamp))
    next_sequence = (history_rows[-1].sequence_number + 1) if history_rows else 1

    provenance = {
        **request.provenance,
        "event_hash": event_hash,
        "deterministic_ordering": True,
        "observational_only": True,
    }

    async with db.begin():
        record = ArenaTournamentHistoryRecord(
            idempotency_key=idempotency_key,
            event_hash=event_hash,
            tournament_id=request.tournament_id,
            competition_id=request.competition_id,
            sequence_number=next_sequence,
            event_type=request.event_type,
            lifecycle_state=request.lifecycle_state,
            schedule_payload=request.schedule_payload,
            replay_metadata=request.replay_metadata,
            tie_break_rules=TIE_BREAK_RULES,
            ordering_rules=ORDERING_RULES,
            event_payload=event_payload,
            provenance=provenance,
            event_timestamp=request.as_of,
            created_at=request.as_of,
        )
        db.add(record)
        await db.flush()

        db.add(
            AuditLog(
                actor=request.actor,
                action="arena.tournament_history_recorded",
                entity_type="arena_tournament_history_record",
                entity_id=record.id,
                before_state=None,
                after_state={
                    "competition_id": str(request.competition_id),
                    "tournament_id": str(request.tournament_id),
                    "sequence_number": next_sequence,
                    "event_type": request.event_type,
                    "lifecycle_state": request.lifecycle_state,
                    "event_hash": event_hash,
                    "standings_count": len(standings),
                },
            )
        )

    return ArenaTournamentLifecycleEventResult(
        history_record_id=record.id,
        event_hash=event_hash,
        tournament_id=request.tournament_id,
        competition_id=request.competition_id,
        sequence_number=next_sequence,
        event_type=request.event_type,
        lifecycle_state=request.lifecycle_state,
        event_timestamp=request.as_of,
        schedule_payload=request.schedule_payload,
        replay_metadata=request.replay_metadata,
        standings=standings,
        tie_break_rules=TIE_BREAK_RULES,
        ordering_rules=ORDERING_RULES,
        provenance=provenance,
    )


async def read_arena_tournament_lifecycle_state(
    *,
    db: AsyncSession,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
) -> ArenaTournamentLifecycleReadModel | None:
    result = await db.execute(
        select(ArenaTournamentHistoryRecord).where(
            ArenaTournamentHistoryRecord.competition_id == competition_id,
            ArenaTournamentHistoryRecord.tournament_id == tournament_id,
        )
    )
    rows = list(result.scalars().all())
    if not rows:
        return None

    rows.sort(key=lambda item: (item.sequence_number, item.event_timestamp))
    latest = rows[-1]
    latest_payload = latest.event_payload

    return ArenaTournamentLifecycleReadModel(
        tournament_id=tournament_id,
        competition_id=competition_id,
        current_state=latest.lifecycle_state,
        latest_event_type=latest.event_type,
        latest_event_timestamp=latest.event_timestamp,
        history_count=len(rows),
        replay_metadata=latest.replay_metadata,
        latest_schedule_payload=latest.schedule_payload,
        latest_standings=[
            _deserialize_standing(item) for item in latest_payload.get("standings", [])
        ],
    )


async def read_arena_tournament_history_events(
    *,
    db: AsyncSession,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
) -> list[ArenaTournamentLifecycleEventResult]:
    result = await db.execute(
        select(ArenaTournamentHistoryRecord).where(
            ArenaTournamentHistoryRecord.competition_id == competition_id,
            ArenaTournamentHistoryRecord.tournament_id == tournament_id,
        )
    )
    rows = list(result.scalars().all())
    rows.sort(key=lambda item: (item.sequence_number, item.event_timestamp))
    return [_deserialize_result(item) for item in rows]
