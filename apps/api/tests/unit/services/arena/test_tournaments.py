from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.arena_tournament import ArenaTournament
from app.models.arena_tournament_history_record import (
    ArenaTournamentHistoryRecord,
    _prevent_arena_tournament_history_record_delete,
    _prevent_arena_tournament_history_record_update,
)
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


def _request(
    *,
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    event_type: str,
    lifecycle_state: str,
    standings: list[ArenaTournamentAgentOutcomeContract],
    ts: datetime,
) -> ArenaTournamentLifecycleEventRequest:
    return ArenaTournamentLifecycleEventRequest(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type=event_type,
        lifecycle_state=lifecycle_state,
        schedule_payload={
            "cycle_interval_minutes": 60,
            "window_start": "2026-07-06T00:00:00+00:00",
            "window_end": "2026-07-06T06:00:00+00:00",
        },
        replay_metadata={
            "snapshot_seed": "seed-v1",
            "deterministic_replay": True,
        },
        standings=standings,
        as_of=ts,
        actor="arena.tournament",
        provenance={"ticket": "ARENA-89"},
    )


@pytest.mark.asyncio
async def test_tournament_lifecycle_history_is_append_only_idempotent_and_deterministic() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    session.tournaments.append(
        ArenaTournament(
            id=tournament_id,
            idempotency_key="tournament",
            tournament_identity="tour-v1",
            competition_id=competition_id,
            sequence_number=1,
            status="planned",
            config={"mode": "paper_only"},
            provenance={"source": "unit"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    request = _request(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type="scheduled",
        lifecycle_state="planned",
        ts=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        standings=[
            ArenaTournamentAgentOutcomeContract(
                agent_id=agent_a,
                composite_score=_metric("0.8"),
                decision_quality=_metric("0.7"),
                risk_discipline=_metric("0.5"),
                drawdown=_metric("0.1"),
                fee_drag=_metric("1.0"),
                profit=_metric("5"),
                evidence_provenance={},
            ),
            ArenaTournamentAgentOutcomeContract(
                agent_id=agent_b,
                composite_score=_metric("0.8"),
                decision_quality=_metric("0.9"),
                risk_discipline=_metric("0.4"),
                drawdown=_metric("0.1"),
                fee_drag=_metric("1.0"),
                profit=_metric("5"),
                evidence_provenance={},
            ),
        ],
    )

    first = await record_arena_tournament_lifecycle_event(db=session, request=request)
    second = await record_arena_tournament_lifecycle_event(db=session, request=request)

    assert first.history_record_id == second.history_record_id
    assert first.event_hash == second.event_hash
    assert len(session.history) == 1
    assert first.standings[0].agent_id == agent_b
    assert first.standings[0].rank == 1
    assert first.tie_break_rules
    assert first.ordering_rules


@pytest.mark.asyncio
async def test_lifecycle_state_read_model_tracks_latest_state_and_replay_metadata() -> None:
    session = _FakeSession()
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    session.tournaments.append(
        ArenaTournament(
            id=tournament_id,
            idempotency_key="tournament",
            tournament_identity="tour-v2",
            competition_id=competition_id,
            sequence_number=2,
            status="planned",
            config={"mode": "paper_only"},
            provenance={"source": "unit"},
            created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        )
    )

    scheduled = _request(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type="scheduled",
        lifecycle_state="planned",
        ts=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
        standings=[],
    )
    activated = _request(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type="activated",
        lifecycle_state="active",
        ts=datetime(2026, 7, 6, 2, tzinfo=timezone.utc),
        standings=[],
    )
    completed = _request(
        competition_id=competition_id,
        tournament_id=tournament_id,
        event_type="completed",
        lifecycle_state="completed",
        ts=datetime(2026, 7, 6, 3, tzinfo=timezone.utc),
        standings=[],
    )

    await record_arena_tournament_lifecycle_event(db=session, request=scheduled)
    await record_arena_tournament_lifecycle_event(db=session, request=activated)
    await record_arena_tournament_lifecycle_event(db=session, request=completed)

    read_model = await read_arena_tournament_lifecycle_state(
        db=session,
        competition_id=competition_id,
        tournament_id=tournament_id,
    )

    assert read_model is not None
    assert read_model.current_state == "completed"
    assert read_model.latest_event_type == "completed"
    assert read_model.history_count == 3
    assert read_model.replay_metadata["deterministic_replay"] is True


def test_tournament_history_model_is_append_only() -> None:
    with pytest.raises(
        ValueError,
        match="arena_tournament_history_records is append-only and does not support updates",
    ):
        _prevent_arena_tournament_history_record_update(None, None, None)

    with pytest.raises(
        ValueError,
        match="arena_tournament_history_records is append-only and does not support deletes",
    ):
        _prevent_arena_tournament_history_record_delete(None, None, None)
