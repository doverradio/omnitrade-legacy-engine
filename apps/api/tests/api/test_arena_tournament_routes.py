from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.arena_tournament_history_record import ArenaTournamentHistoryRecord


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


class _FakeSession:
    def __init__(self, rows: list[ArenaTournamentHistoryRecord]) -> None:
        self.rows = rows
        self.begin_calls = 0
        self.add_calls = 0

    def begin(self) -> Any:
        self.begin_calls += 1
        raise AssertionError("Read endpoint should not open write transactions")

    def add(self, _obj: Any) -> None:
        self.add_calls += 1
        raise AssertionError("Read endpoint should not add records")

    async def scalar(self, statement: Any) -> Any:
        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params
        if "FROM arena_tournament_history_records" in sql:
            competition_id = params.get("competition_id_1")
            tournament_id = params.get("tournament_id_1")
            rows = [
                item
                for item in self.rows
                if item.competition_id == competition_id and item.tournament_id == tournament_id
            ]
            return _ExecuteResult(rows)

        return _ExecuteResult([])


def _client(fake_db: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_get_arena_tournament_history_returns_known_payload() -> None:
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    row = ArenaTournamentHistoryRecord(
        id=uuid.uuid4(),
        idempotency_key="k1",
        event_hash="h1",
        tournament_id=tournament_id,
        competition_id=competition_id,
        sequence_number=1,
        event_type="scheduled",
        lifecycle_state="planned",
        schedule_payload={"cycle_interval_minutes": 30},
        replay_metadata={"deterministic_replay": True},
        tie_break_rules=["decision_quality_desc"],
        ordering_rules=["composite_score_desc"],
        event_payload={
            "standings": [
                {
                    "rank": 1,
                    "agent_id": str(agent_id),
                    "composite_score": {"value": "0.9000", "status": "available", "reason": None},
                    "decision_quality": {"value": "0.9000", "status": "available", "reason": None},
                    "risk_discipline": {"value": "0.9000", "status": "available", "reason": None},
                    "drawdown": {"value": "0.1000", "status": "available", "reason": None},
                    "fee_drag": {"value": "1.0000", "status": "available", "reason": None},
                    "profit": {"value": "5.0000", "status": "available", "reason": None},
                    "evidence_provenance": {},
                }
            ]
        },
        provenance={"deterministic_ordering": True},
        event_timestamp=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
    )

    client = _client(_FakeSession([row]))
    response = client.get(
        "/decisions/arena-tournaments/history",
        params={
            "competition_id": str(competition_id),
            "tournament_id": str(tournament_id),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["availability_state"] == "known"
    assert body["current_state"] == "planned"
    assert body["history_count"] == 1
    assert body["history"][0]["event_hash"] == "h1"
    assert body["latest_standings"][0]["agent_id"] == str(agent_id)


def test_get_arena_tournament_history_returns_unavailable_when_missing() -> None:
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    client = _client(_FakeSession([]))

    response = client.get(
        "/decisions/arena-tournaments/history",
        params={
            "competition_id": str(competition_id),
            "tournament_id": str(tournament_id),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["availability_state"] == "unavailable"
    assert body["state_reason"] == "arena_tournament_history_unavailable"
    assert body["history"] == []
