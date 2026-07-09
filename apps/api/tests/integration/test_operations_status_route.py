from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.operations_status import compute_uptime


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
