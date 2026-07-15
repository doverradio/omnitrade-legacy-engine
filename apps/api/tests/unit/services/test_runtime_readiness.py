from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.schemas.operations import LiveCryptoReadinessItemResponse, LiveCryptoReadinessResponse
from app.services import operations_status as service


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def mappings(self):
        return self

    def first(self):
        return self._value


class _FakeDb:
    async def execute(self, statement, params=None):
        _ = params
        sql = str(statement)
        compiled = statement.compile()
        compiled_params = compiled.params
        if "action = 'orchestration_worker_started'" in sql:
            return _Result({"restart_count": 3})
        if "action = 'orchestration_worker_full_pipeline_completed'" in sql:
            return _Result({"completed_at": datetime(2026, 7, 15, 0, 6, tzinfo=timezone.utc)})
        if "action = 'orchestration_worker_start_failed'" in sql:
            return _Result({"exception_count": 3})
        if "action = 'decision_package_replay_failed'" in sql:
            return _Result({"exception_count": 2})
        if "MAX(c.close_time)" in sql:
            return _Result({"close_time": datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc)})
        if "FROM autonomous_cycle_runs" in sql:
            if compiled_params.get("cycle_kind_1") == "autonomous":
                return _Result(SimpleNamespace(cycle_id="autonomous-1", state="COMPLETE", started_at=datetime(2026, 7, 15, 0, 1, tzinfo=timezone.utc), completed_at=datetime(2026, 7, 15, 0, 2, tzinfo=timezone.utc), failure_reason=None))
            if compiled_params.get("cycle_kind_1") == "campaign":
                return _Result(SimpleNamespace(cycle_id="campaign-1", state="COMPLETE", started_at=datetime(2026, 7, 15, 0, 3, tzinfo=timezone.utc), completed_at=datetime(2026, 7, 15, 0, 4, tzinfo=timezone.utc), failure_reason=None))
        return _Result(0)


@pytest.mark.asyncio
async def test_runtime_readiness_reports_restart_and_health_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _build_operations_status(**_kwargs):
        return SimpleNamespace(
            system_health={"database": SimpleNamespace(state="green", detail="Database connected")},
            live_crypto_readiness=LiveCryptoReadinessResponse(
                ready=True,
                items=[
                    LiveCryptoReadinessItemResponse(
                        key="kraken_production_exchange_connection",
                        label="Kraken Production Exchange Connection",
                        ready=True,
                        detail="ready",
                    )
                ],
            ),
        )

    monkeypatch.setattr(service, "build_operations_status", _build_operations_status)
    monkeypatch.setattr(service, "get_last_successful_full_pipeline_at", lambda: datetime(2026, 7, 15, 0, 5, tzinfo=timezone.utc))

    result = await service.build_runtime_readiness(db=_FakeDb())

    assert result.worker_uptime
    assert result.restart_count == 2
    assert result.last_successful_full_pipeline_at == datetime(2026, 7, 15, 0, 6, tzinfo=timezone.utc)
    assert result.last_kraken_candle_processed_at == datetime(2026, 7, 15, 0, 15, tzinfo=timezone.utc)
    assert result.last_autonomous_cycle is not None
    assert result.last_campaign_preview_cycle is not None
    assert result.unresolved_exceptions == 3
    assert result.database_health.state == "green"
    assert result.provider_health.ready is True