from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_tournament import ArenaTournament
from app.models.arena_tournament_history_record import ArenaTournamentHistoryRecord
from app.models.audit_log import AuditLog
from app.services.arena.contracts import (
    ArenaTournamentAgentOutcomeContract,
    ArenaTournamentLifecycleEventRequest,
    ArenaTournamentMetricContract,
)
from app.services.arena.tournaments import (
    record_arena_tournament_lifecycle_event,
    read_arena_tournament_lifecycle_state,
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
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.tournaments: list[ArenaTournament] = []
        self.history: list[ArenaTournamentHistoryRecord] = []
        self.audit: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_tournaments" in sql:
            for item in self.tournaments:
                if item.id == params.get("id_1") and item.competition_id == params.get("competition_id_1"):
                    return item
            return None

        if "FROM arena_tournament_history_records" in sql:
            key = params.get("idempotency_key_1")
            for item in self.history:
                if item.idempotency_key == key:
                    return item
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_tournament_history_records" in sql:
            tournament_id = params.get("tournament_id_1")
            competition_id = params.get("competition_id_1")
            if tournament_id is not None and competition_id is None:
                return _ExecuteResult([item for item in self.history if item.tournament_id == tournament_id])
            return _ExecuteResult(
                [
                    item
                    for item in self.history
                    if item.tournament_id == tournament_id and item.competition_id == competition_id
                ]
            )

        return _ExecuteResult([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaTournamentHistoryRecord):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.history.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit.append(obj)

    async def flush(self) -> None:
        return None


def _metric(value: str | None, status: str = "available") -> ArenaTournamentMetricContract:
    return ArenaTournamentMetricContract(
        value=Decimal(value) if value is not None else None,
        status=status,
        reason=None if status == "available" else "unknown",
    )


@pytest.mark.asyncio
async def test_tournament_history_and_replay_metadata_are_reconstructable() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    session.tournaments.append(
        ArenaTournament(
            id=tournament_id,
            idempotency_key="tour",
            tournament_identity="tour-main",
            competition_id=competition_id,
            sequence_number=1,
            status="planned",
            config={"mode": "paper_only"},
            provenance={"source": "integration"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()

    scheduled_request = ArenaTournamentLifecycleEventRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type="scheduled",
        lifecycle_state="planned",
        schedule_payload={"cycle_interval_minutes": 30, "window_start": "2026-07-06T00:00:00+00:00"},
        replay_metadata={"seed": "x1", "deterministic_replay": True},
        standings=[],
        as_of=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        actor="arena.integration",
        provenance={"ticket": "ARENA-89"},
    )

    standings_request = ArenaTournamentLifecycleEventRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type="standings_recorded",
        lifecycle_state="active",
        schedule_payload={"cycle_interval_minutes": 30, "window_start": "2026-07-06T00:00:00+00:00"},
        replay_metadata={"seed": "x1", "deterministic_replay": True},
        standings=[
            ArenaTournamentAgentOutcomeContract(
                agent_id=agent_a,
                composite_score=_metric("0.7000"),
                decision_quality=_metric("0.5000"),
                risk_discipline=_metric("0.7000"),
                drawdown=_metric("0.3000"),
                fee_drag=_metric("3.0"),
                profit=_metric("15.0"),
                evidence_provenance={"sources": ["comparison", "performance"]},
            ),
            ArenaTournamentAgentOutcomeContract(
                agent_id=agent_b,
                composite_score=_metric("0.7000"),
                decision_quality=_metric("0.9000"),
                risk_discipline=_metric("0.6000"),
                drawdown=_metric("0.3000"),
                fee_drag=_metric("3.0"),
                profit=_metric("15.0"),
                evidence_provenance={"sources": ["comparison", "performance"]},
            ),
        ],
        as_of=datetime(2026, 7, 6, 2, tzinfo=timezone.utc),
        actor="arena.integration",
        provenance={"ticket": "ARENA-89"},
    )

    await record_arena_tournament_lifecycle_event(db=session, request=scheduled_request)
    standings_result = await record_arena_tournament_lifecycle_event(db=session, request=standings_request)

    read_model = await read_arena_tournament_lifecycle_state(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
    )

    assert standings_result.sequence_number == 2
    assert standings_result.standings[0].agent_id == agent_b
    assert standings_result.standings[0].rank == 1
    assert read_model is not None
    assert read_model.current_state == "active"
    assert read_model.history_count == 2
    assert read_model.latest_schedule_payload["cycle_interval_minutes"] == 30
    assert read_model.replay_metadata["deterministic_replay"] is True
    assert session.audit
