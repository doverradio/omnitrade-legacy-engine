from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.services import mission_control_snapshot_history as service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _statement):
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_snapshot_history_preserves_annotation_payloads() -> None:
    row = SimpleNamespace(
        snapshot_id=uuid.uuid4(),
        captured_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
        bucket_start=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
        bucket_end=datetime(2026, 7, 9, 10, 15, tzinfo=timezone.utc),
        overall_score=82,
        confidence="High",
        data_completeness=100,
        market_awareness_score=80,
        decision_quality_score=82,
        execution_reliability_score=84,
        risk_discipline_score=77,
        research_progress_score=79,
        adaptation_rate_score=75,
        operational_health_score=93,
        capital_efficiency_score=81,
        profit_performance_score=83,
        paper_net_profit=Decimal("523.55"),
        live_net_profit=Decimal("0"),
        combined_net_profit=Decimal("523.55"),
        paper_equity=Decimal("104523.55"),
        live_equity=Decimal("0"),
        combined_equity=Decimal("104523.55"),
        realized_pnl=Decimal("523.55"),
        unrealized_pnl=Decimal("120.00"),
        fees=Decimal("12.50"),
        drawdown_percent=Decimal("0.09"),
        source_counts={"paper_trades": 8, "decision_records": 82},
        annotations=[
            {
                "event_type": "risk_guardrail_triggered",
                "title": "Guardrail Triggered",
                "required_action": "operator_review",
                "metadata": {"severity": "high"},
            },
            "ignore-me",
        ],
        schema_version="v1",
    )

    response = await service.build_snapshot_history(db=_FakeDb([row]), range_value="24h", dimension=None)

    assert response.range == "24h"
    assert len(response.points) == 1
    point = response.points[0]
    assert point.paper_net_profit == "523.55"
    assert point.annotations == [
        {
            "event_type": "risk_guardrail_triggered",
            "title": "Guardrail Triggered",
            "required_action": "operator_review",
            "metadata": {"severity": "high"},
        }
    ]


@pytest.mark.asyncio
async def test_snapshot_history_normalizes_unknown_range() -> None:
    response = await service.build_snapshot_history(db=_FakeDb([]), range_value="bad-range", dimension="risk")

    assert response.range == "24h"
    assert response.dimension == "risk"
    assert response.points == []
