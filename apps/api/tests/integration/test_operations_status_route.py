from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.operations_status import compute_uptime


class _ResultWithScalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def mappings(self):
        return self

    def first(self):
        return self._value


class _DurableCountsSession:
    async def execute(self, statement, params=None):
        _ = params
        sql = str(statement)
        if "SELECT 1" in sql:
            return _ResultWithScalar(1)
        if "SELECT COUNT(*) FROM candles" in sql:
            return _ResultWithScalar(101)
        if "SELECT COUNT(*) FROM signals" in sql and "created_at" not in sql:
            return _ResultWithScalar(34)
        if "SELECT COUNT(*) FROM trades WHERE is_paper = true" in sql and "created_at" not in sql:
            return _ResultWithScalar(12)
        if "SELECT COUNT(*) FROM decision_records" in sql:
            return _ResultWithScalar(44)
        if "SELECT COUNT(*) FROM decision_quality_scores" in sql:
            return _ResultWithScalar(9)
        if "SELECT COUNT(*) FROM research_candidates" in sql:
            return _ResultWithScalar(7)
        if "SELECT COUNT(*) FROM research_campaigns" in sql:
            return _ResultWithScalar(3)
        if "SELECT COUNT(*) FROM research_laboratory_runs" in sql:
            return _ResultWithScalar(5)
        if "SELECT COUNT(*) FROM research_candidate_lineage" in sql:
            return _ResultWithScalar(4)
        if "SELECT COUNT(*) FROM research_memory_entries" in sql:
            return _ResultWithScalar(6)
        if "SELECT MAX(close_time) FROM candles" in sql:
            return _ResultWithScalar(None)
        if "SELECT MAX(signal_time) FROM signals" in sql:
            return _ResultWithScalar(None)
        if "SELECT MAX(executed_at) FROM trades WHERE is_paper = true" in sql:
            return _ResultWithScalar(None)
        if "SELECT COUNT(*) FROM signals WHERE created_at >=" in sql:
            return _ResultWithScalar(2)
        if "SELECT COUNT(*) FROM trades WHERE is_paper = true AND created_at >=" in sql:
            return _ResultWithScalar(1)
        if "FROM research_campaigns" in sql and "ORDER BY updated_at" in sql:
            return _ResultWithScalar(None)
        if "FROM research_campaign_statistics" in sql:
            return _ResultWithScalar(None)
        return _ResultWithScalar(0)

    async def scalar(self, _statement):
        return None


def test_operations_status_heartbeat_endpoint_returns_shape() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/operations/status")

    assert response.status_code == 200
    payload = response.json()

    assert payload["overall_health"] in {"green", "yellow", "red"}
    assert payload["run_status"]["run_id"]
    assert payload["run_status"]["started_at"]
    assert payload["run_status"]["expected_end"]
    assert payload["run_status"]["uptime"]
    assert payload["run_status"]["current_phase"]
    assert payload["run_status"]["health_status"] in {"green", "yellow", "red"}

    for key in ["api", "orchestrator", "database", "research_agent"]:
        assert key in payload["system_health"]
        assert payload["system_health"][key]["state"] in {"green", "yellow", "red"}

    monitoring = payload["monitoring"]
    required_metrics = [
        "candles_processed",
        "signals_generated",
        "paper_trades_executed",
        "decision_records_created",
        "replay_count",
        "candidate_count",
        "campaign_count",
        "laboratory_runs",
        "evolution_count",
        "current_champion",
        "paper_equity",
    ]
    for metric in required_metrics:
        assert metric in monitoring


def test_compute_uptime_formats_elapsed_time_without_negative_values() -> None:
    started_at = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 9, 14, 5, 9, tzinfo=timezone.utc)

    assert compute_uptime(started_at=started_at, now=now) == "02:05:09"

    earlier = datetime(2026, 7, 9, 11, 59, tzinfo=timezone.utc)
    assert compute_uptime(started_at=started_at, now=earlier) == "00:00:00"


def test_compute_uptime_formats_day_boundary() -> None:
    started_at = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 9, 15, 1, 1, tzinfo=timezone.utc)

    assert compute_uptime(started_at=started_at, now=now) == "2d 03:01:01"


def test_operations_status_uses_durable_research_record_counts(monkeypatch) -> None:
    app = create_app()
    fake_db = _DurableCountsSession()

    async def _read(operation, *, operation_name):
        _ = operation_name
        return await operation(fake_db)

    async def _paper_equity_stub(*_args, **_kwargs):
        return Decimal("25")

    monkeypatch.setattr("app.api.routes.operations.run_read_with_retry", _read)
    monkeypatch.setattr("app.services.operations_status._get_paper_equity", _paper_equity_stub)

    with TestClient(app) as client:
        response = client.get("/operations/status")

    assert response.status_code == 200
    monitoring = response.json()["monitoring"]
    assert monitoring["candidate_count"] == 7
    assert monitoring["campaign_count"] == 3
    assert monitoring["laboratory_runs"] == 5
    assert monitoring["evolution_count"] == 4
    assert monitoring["research_memory_growth"] == 6
