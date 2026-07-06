from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.api.routes import paper as paper_route_module
from app.db.session import get_db
from app.main import create_app
from app.services.signals.execution_orchestrator import SignalExecutionResult


class _FakeSession:
    async def scalar(self, statement):
        return None

    async def execute(self, statement):
        return None

    def add(self, obj):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_submit_stock_signal_uses_centralized_execution_route(monkeypatch) -> None:
    signal_id = uuid.uuid4()
    account_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    async def fake_orchestrate_paper_signal_execution(*args, **kwargs):
        return SignalExecutionResult(
            signal_id=signal_id,
            paper_account_id=account_id,
            asset_id=asset_id,
            execution_status="executed",
            execution_venue="alpaca_paper",
            is_paper=True,
            trade_id=uuid.uuid4(),
            broker_order_id="alpaca-order-1",
            venue_status="filled",
            message="Signal submitted to Alpaca paper adapter",
        )

    monkeypatch.setattr(
        paper_route_module,
        "orchestrate_paper_signal_execution",
        fake_orchestrate_paper_signal_execution,
    )

    with create_test_client(_FakeSession()) as client:
        response = client.post(
            "/paper/signals/execute",
            json={
                "signal_id": str(signal_id),
                "account_id": str(account_id),
                "asset_id": str(asset_id),
                "side": "buy",
                "quantity": "0.5",
                "actor": "system",
                "client_order_id": "coid-1",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_venue"] == "alpaca_paper"
    assert payload["is_paper"] is True
    assert payload["broker_order_id"] == "alpaca-order-1"


def test_direct_alpaca_execution_endpoint_not_exposed() -> None:
    with create_test_client(_FakeSession()) as client:
        response = client.post(
            "/paper/orders/alpaca",
            json={
                "account_id": str(uuid.uuid4()),
                "asset_id": str(uuid.uuid4()),
                "side": "buy",
                "quantity": "0.5",
            },
        )

    assert response.status_code == 404
