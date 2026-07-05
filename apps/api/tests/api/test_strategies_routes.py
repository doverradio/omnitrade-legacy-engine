from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy


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
    def __init__(self, strategies: list[Strategy], parameter_sets: list[ParameterSet]) -> None:
        self.strategies = strategies
        self.parameter_sets = parameter_sets

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)

        if "FROM strategies" in sql:
            params = statement.compile().params
            lowered_sql = sql.lower()
            if "strategies.is_active = false" in lowered_sql:
                return _ExecuteResult([strategy for strategy in self.strategies if strategy.is_active is False])
            if "strategies.is_active = true" in lowered_sql:
                return _ExecuteResult([strategy for strategy in self.strategies if strategy.is_active is True])
            if "WHERE" in sql and params:
                is_active = None
                for key, value in params.items():
                    if "is_active" not in str(key):
                        continue
                    if isinstance(value, bool):
                        is_active = value
                        break
                    if value in (0, 1):
                        is_active = bool(value)
                        break
                if is_active is not None:
                    filtered = [strategy for strategy in self.strategies if strategy.is_active == is_active]
                    return _ExecuteResult(filtered)

            ordered = sorted(
                self.strategies,
                key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc),
            )
            return _ExecuteResult(ordered)

        if "FROM parameter_sets" in sql:
            ordered = sorted(
                self.parameter_sets,
                key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return _ExecuteResult(ordered)

        return _ExecuteResult([])


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_get_strategies_returns_seeded_strategies() -> None:
    strategy_id = uuid.uuid4()
    strategy = Strategy(
        id=strategy_id,
        name="MA Crossover",
        slug="ma_crossover",
        module_version="1.0.0",
        is_active=False,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    parameter_set = ParameterSet(
        id=uuid.uuid4(),
        strategy_id=strategy_id,
        label="default-v1",
        params={"fast_period": 10, "slow_period": 50},
        created_by="system",
        created_at=datetime(2026, 7, 5, 1, tzinfo=timezone.utc),
    )

    with create_test_client(_FakeSession([strategy], [parameter_set])) as client:
        response = client.get("/strategies")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": str(strategy_id),
                "name": "MA Crossover",
                "slug": "ma_crossover",
                "is_active": False,
                "module_version": "1.0.0",
                "default_params": {"fast_period": 10, "slow_period": 50},
            }
        ]
    }


def test_get_strategies_filter_is_active_false() -> None:
    inactive_strategy = Strategy(
        id=uuid.uuid4(),
        name="MA Crossover",
        slug="ma_crossover",
        module_version="1.0.0",
        is_active=False,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )
    active_strategy = Strategy(
        id=uuid.uuid4(),
        name="Breakout",
        slug="breakout",
        module_version="1.0.0",
        is_active=True,
        created_at=datetime(2026, 7, 5, 1, tzinfo=timezone.utc),
    )

    with create_test_client(_FakeSession([inactive_strategy, active_strategy], [])) as client:
        response = client.get("/strategies", params={"is_active": "false"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == [
        {
            "id": str(inactive_strategy.id),
            "name": "MA Crossover",
            "slug": "ma_crossover",
            "is_active": False,
            "module_version": "1.0.0",
            "default_params": None,
        }
    ]


def test_get_strategies_empty_database() -> None:
    with create_test_client(_FakeSession([], [])) as client:
        response = client.get("/strategies")

    assert response.status_code == 200
    assert response.json() == {"items": []}
