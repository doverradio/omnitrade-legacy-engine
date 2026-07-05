from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.services.data.ingestion_status import (
    reset_last_successful_ingestion_at,
    set_last_successful_ingestion_at,
)


class _HealthySession:
    async def execute(self, _statement):
        return None


def create_health_test_client() -> TestClient:
    app = create_app()

    async def override_get_db():
        yield _HealthySession()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_health_returns_last_ingestion_at_from_shared_status() -> None:
    reset_last_successful_ingestion_at()
    set_last_successful_ingestion_at(datetime(2026, 7, 5, 3, 0, tzinfo=timezone.utc))

    with create_health_test_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["db"] == "connected"
    assert payload["last_ingestion_at"] == "2026-07-05T03:00:00Z"

    reset_last_successful_ingestion_at()
