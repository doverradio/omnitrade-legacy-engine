from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.arena_comparison_record import ArenaComparisonRecord


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
    def __init__(self, rows: list[ArenaComparisonRecord]) -> None:
        self.rows = rows
        self.add_calls = 0
        self.begin_calls = 0

    def begin(self) -> Any:
        self.begin_calls += 1
        raise AssertionError("Read endpoint should not open write transactions")

    def add(self, _obj: Any) -> None:
        self.add_calls += 1
        raise AssertionError("Read endpoint should not add records")

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params
        if "FROM arena_comparison_records" in sql:
            competition_id = params.get("competition_id_1")
            filtered = [item for item in self.rows if item.competition_id == competition_id]
            return _ExecuteResult(filtered)
        return _ExecuteResult([])


def _client(fake_db: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_db

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_get_latest_arena_comparison_returns_known_payload() -> None:
    competition_id = uuid.uuid4()
    tournament_id = uuid.uuid4()
    cycle_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    row = ArenaComparisonRecord(
        id=uuid.uuid4(),
        idempotency_key="k1",
        comparison_hash="hash-1",
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        comparison_scope="cycle",
        compared_agent_ids=[str(agent_id)],
        comparison_payload={
            "agent_summaries": [
                {
                    "agent_id": str(agent_id),
                    "decision_quality": {"value": "0.5000", "status": "available", "reason": None},
                    "explainability_support_ratio": {"value": "0.5000", "status": "available", "reason": None},
                    "counterfactual_correctness": {"value": "0.5000", "status": "available", "reason": None},
                    "evidence_provenance": {"sources": ["decision_quality_scores"]},
                }
            ],
            "portfolio_dimensions": {
                "decision_quality": {"value": "0.5000", "status": "available", "reason": None},
                "explainability_support_ratio": {"value": "0.5000", "status": "available", "reason": None},
                "counterfactual_correctness": {"value": "0.5000", "status": "available", "reason": None},
            },
        },
        evidence_sources={"decision_quality_score_ids": [str(uuid.uuid4())]},
        provenance={"deterministic": True},
        comparison_timestamp=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, 12, tzinfo=timezone.utc),
    )

    client = _client(_FakeSession([row]))
    response = client.get(
        "/decisions/arena-comparisons/latest",
        params={
            "competition_id": str(competition_id),
            "tournament_id": str(tournament_id),
            "cycle_id": str(cycle_id),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["availability_state"] == "known"
    assert body["comparison_hash"] == "hash-1"
    assert body["compared_agent_ids"] == [str(agent_id)]
    assert body["portfolio_dimensions"]["decision_quality"]["value"] == "0.5000"


def test_get_latest_arena_comparison_returns_unavailable_state_when_missing() -> None:
    competition_id = uuid.uuid4()
    client = _client(_FakeSession([]))

    response = client.get(
        "/decisions/arena-comparisons/latest",
        params={"competition_id": str(competition_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["availability_state"] == "unavailable"
    assert body["state_reason"] == "arena_comparison_unavailable"
    assert body["comparison_hash"] is None
