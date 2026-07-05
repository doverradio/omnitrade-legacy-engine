from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.parameter_set import ParameterSet


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
    def __init__(self, parameter_sets: list[ParameterSet]) -> None:
        self.parameter_sets = parameter_sets

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        if "FROM parameter_sets" in sql:
            ordered = sorted(self.parameter_sets, key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc))
            return _ExecuteResult(ordered)
        return _ExecuteResult([])


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_list_parameter_sets() -> None:
    strategy_id = uuid.uuid4()
    parameter_set = ParameterSet(
        id=uuid.uuid4(),
        strategy_id=strategy_id,
        label="conservative-v1",
        params={"fast_period": 10, "slow_period": 50},
        created_by="system",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    with create_test_client(_FakeSession([parameter_set])) as client:
        response = client.get("/parameter-sets")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(parameter_set.id),
                "strategy_id": str(strategy_id),
                "name": "conservative-v1",
                "parameters": {"fast_period": 10, "slow_period": 50},
            }
        ]
    }


def test_list_parameter_sets_empty_database() -> None:
    with create_test_client(_FakeSession([])) as client:
        response = client.get("/parameter-sets")

    assert response.status_code == 200
    assert response.json() == {"items": []}