from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.api.routes import arena as arena_routes


class _FakeSession:
    async def execute(self, _statement: Any) -> Any:
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


def _client() -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield _FakeSession()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_replay_route_returns_replay_result(monkeypatch: pytest.MonkeyPatch) -> None:
    replay_id = uuid.uuid4()

    async def _fake_replay_decision_package_v0(*, db: Any, decision_package_id: str) -> Any:
        return SimpleNamespace(
            replay_id=replay_id,
            replay_agent_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            decision_package_id=decision_package_id,
            replay_timestamp=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
            reconstructed_action="BUY",
            confidence=Decimal("0.875"),
            supporting_evidence=({"type": "decision_record"},),
            explanation="Replayed immutable decision package.",
            metadata={"mode": "read_only"},
        )

    monkeypatch.setattr(arena_routes, "replay_decision_package_v0", _fake_replay_decision_package_v0)

    with _client() as client:
        response = client.post(
            "/arena/replay",
            json={"decision_package_id": "dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["replay_id"] == str(replay_id)
    assert payload["reconstructed_action"] == "BUY"
    assert payload["reconstructed_confidence"] == "0.875"


def test_replay_route_returns_404_for_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise_not_found(*, db: Any, decision_package_id: str) -> Any:
        raise arena_routes.ReplayPackageNotFoundError(decision_package_id)

    monkeypatch.setattr(arena_routes, "replay_decision_package_v0", _raise_not_found)

    with _client() as client:
        response = client.post("/arena/replay", json={"decision_package_id": "dpkg:missing"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Replay package not found"