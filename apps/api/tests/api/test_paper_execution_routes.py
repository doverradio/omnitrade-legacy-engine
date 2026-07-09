from __future__ import annotations

from datetime import datetime, timezone
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


class _ResultRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _PipelineHealthFakeSession:
    def __init__(self, *, empty: bool = False, risk_rejected: bool = False) -> None:
        self.empty = empty
        self.risk_rejected = risk_rejected
        self.signal_id = uuid.uuid4()
        self.signal_created_at = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)

    async def scalar(self, statement):
        sql = str(statement)

        if "count" in sql and "FROM candles" in sql:
            return 0 if self.empty else 1234
        if "count" in sql and "FROM signals" in sql and "signals.action =" not in sql and "signals.action IN" not in sql:
            return 0 if self.empty else 36
        if "count" in sql and "FROM signals" in sql and "signals.action =" in sql:
            return 0 if self.empty else 30
        if "count" in sql and "FROM signals" in sql and "signals.action IN" in sql:
            return 0 if self.empty else 6
        if "count(distinct(audit_log.entity_id))" in sql:
            return 0 if self.empty else 6
        if "count" in sql and "FROM risk_events" in sql and "risk_events.action_taken =" not in sql:
            return 0 if self.empty else 6
        if "count" in sql and "FROM risk_events" in sql and "risk_events.action_taken =" in sql:
            if self.empty:
                return 0
            return 5 if self.risk_rejected else 1
        if "count" in sql and "FROM trades" in sql:
            return 0 if self.empty else 1
        if "count" in sql and "FROM decision_records" in sql:
            return 0 if self.empty else 36

        if "SELECT risk_events.detail" in sql and "risk_events.action_taken =" in sql:
            if self.empty:
                return None
            return "position_below_minimum_order_size" if self.risk_rejected else "max_daily_loss_breached"

        if "SELECT risk_events.detail" in sql and "risk_events.related_signal_id" in sql:
            if self.empty:
                return None
            return "position_below_minimum_order_size" if self.risk_rejected else None

        if "max(candles.created_at)" in sql:
            return None if self.empty else datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
        if "max(signals.created_at)" in sql:
            return None if self.empty else datetime(2026, 7, 8, 12, 1, tzinfo=timezone.utc)
        if "max(risk_events.created_at)" in sql:
            return None if self.empty else datetime(2026, 7, 8, 12, 2, tzinfo=timezone.utc)
        if "max(trades.created_at)" in sql:
            return None if self.empty else datetime(2026, 7, 8, 12, 3, tzinfo=timezone.utc)
        if "max(decision_records.timestamp)" in sql:
            return None if self.empty else datetime(2026, 7, 8, 12, 4, tzinfo=timezone.utc)

        return None

    async def execute(self, statement):
        sql = str(statement)
        if "FROM signals" in sql:
            if self.empty:
                return _ResultRows([])
            return _ResultRows(
                [
                    (
                        self.signal_id,
                        "buy",
                        "risk_rejected" if self.risk_rejected else "executed",
                        self.signal_created_at,
                    )
                ]
            )
        return _ResultRows([])

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


def test_pipeline_health_returns_counts_and_recent_activity() -> None:
    with create_test_client(_PipelineHealthFakeSession()) as client:
        response = client.get("/paper/pipeline-health?window_minutes=120")

    assert response.status_code == 200
    payload = response.json()
    assert payload["window_minutes"] == 120
    assert payload["candles"] == 1234
    assert payload["signals_created"] == 36
    assert payload["hold_signals"] == 30
    assert payload["buy_sell_signals"] == 6
    assert payload["execution_candidates"] == 6
    assert payload["executions_attempted"] == 6
    assert payload["risk_events"] == 6
    assert payload["risk_rejected"] == 1
    assert payload["trades"] == 1
    assert payload["decision_records"] == 36
    assert payload["latest_updated_at"] is not None
    assert len(payload["recent_activity"]) == 1


def test_pipeline_health_empty_state_returns_zeroes() -> None:
    with create_test_client(_PipelineHealthFakeSession(empty=True)) as client:
        response = client.get("/paper/pipeline-health?window_minutes=120")

    assert response.status_code == 200
    payload = response.json()
    assert payload["candles"] == 0
    assert payload["signals_created"] == 0
    assert payload["buy_sell_signals"] == 0
    assert payload["risk_events"] == 0
    assert payload["risk_rejected"] == 0
    assert payload["trades"] == 0
    assert payload["decision_records"] == 0
    assert payload["latest_rejection_reason"] is None
    assert payload["latest_updated_at"] is None
    assert payload["recent_activity"] == []


def test_pipeline_health_risk_rejected_state_exposes_latest_reason() -> None:
    with create_test_client(_PipelineHealthFakeSession(risk_rejected=True)) as client:
        response = client.get("/paper/pipeline-health?window_minutes=120")

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_rejected"] == 5
    assert payload["latest_rejection_reason"] == "position_below_minimum_order_size"
    assert payload["recent_activity"][0]["reason"] == "position_below_minimum_order_size"
