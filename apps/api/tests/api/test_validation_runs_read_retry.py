from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.api.routes import validation_runs as validation_runs_route


class _FakeSession:
    async def scalar(self, _statement: Any) -> Any:
        return None

    async def scalars(self, _statement: Any) -> Any:
        return []


def _create_client(*, raise_server_exceptions: bool = True) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_validation_runs_post_is_not_retried_automatically(monkeypatch) -> None:
    calls = {"count": 0}

    async def _failing_create_validation_run(*, db, request):
        calls["count"] += 1
        raise RuntimeError("connection is closed")

    monkeypatch.setattr(validation_runs_route, "create_validation_run", _failing_create_validation_run)

    with _create_client(raise_server_exceptions=False) as client:
        response = client.post(
            "/validation-runs",
            json={
                "name": "Validation 24h",
                "objective": "retry boundary",
                "duration_hours": 24,
                "paper_capital": "10000",
                "enabled_strategies": ["MA Crossover"],
                "enabled_research_agents": ["Baseline"],
                "enabled_research_features": ["Laboratory"],
            },
        )

    assert calls["count"] == 1
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
