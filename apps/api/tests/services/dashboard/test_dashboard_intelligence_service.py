from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.services import dashboard_intelligence as service


class _DummySession:
    pass


def _trade(*, minutes_ago: int, side: str, price: str, quantity: str = "1") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        signal_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal("0"),
        is_paper=True,
        executed_at=now - timedelta(minutes=minutes_ago),
    )


async def _account_stub(*_args, **_kwargs) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), starting_balance=Decimal("1000"))


async def _empty_list_stub(*_args, **_kwargs):
    return []


def _install_common_monkeypatches(monkeypatch: pytest.MonkeyPatch, *, trades: list[SimpleNamespace], decision_scores: list[SimpleNamespace], research_evaluations: list[SimpleNamespace], campaigns: list[SimpleNamespace], risk_events: list[SimpleNamespace], summary: SimpleNamespace, operations: SimpleNamespace | None = None) -> None:
    async def _trades_stub(*_args, **_kwargs):
        return trades

    async def _decision_scores_stub(*_args, **_kwargs):
        return decision_scores

    async def _research_stub(*_args, **_kwargs):
        return research_evaluations

    async def _campaigns_stub(*_args, **_kwargs):
        return campaigns

    async def _risk_stub(*_args, **_kwargs):
        return risk_events

    async def _summary_stub(*_args, **_kwargs):
        return summary

    monkeypatch.setattr(service, "_load_account", _account_stub)
    monkeypatch.setattr(service, "_load_trades", _trades_stub)
    monkeypatch.setattr(service, "_load_decision_scores", _decision_scores_stub)
    monkeypatch.setattr(service, "_load_research_evaluations", _research_stub)
    monkeypatch.setattr(service, "_load_campaigns", _campaigns_stub)
    monkeypatch.setattr(service, "_load_risk_events", _risk_stub)
    monkeypatch.setattr(service, "get_paper_performance_summary", _summary_stub)

    async def _operations_stub(*_args, **_kwargs):
        return operations or SimpleNamespace(overall_health="green", alerts=[])

    monkeypatch.setattr(service, "build_operations_status", _operations_stub)


@pytest.mark.asyncio
async def test_dashboard_intelligence_score_with_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = SimpleNamespace(trade_count=0, win_rate=Decimal("0"))
    _install_common_monkeypatches(
        monkeypatch,
        trades=[],
        decision_scores=[],
        research_evaluations=[],
        campaigns=[],
        risk_events=[],
        summary=summary,
    )

    result = await service.build_dashboard_intelligence_score(db=_DummySession(), range_value="24h")

    assert result.score == 0
    assert result.data_completeness == 0
    assert result.range == "24h"
    assert result.timeline == []


@pytest.mark.asyncio
async def test_dashboard_intelligence_score_with_sample_data(monkeypatch: pytest.MonkeyPatch) -> None:
    trades = [_trade(minutes_ago=120, side="buy", price="100"), _trade(minutes_ago=30, side="sell", price="112")]
    decision_scores = [SimpleNamespace(created_at=datetime.now(timezone.utc) - timedelta(hours=2), composite_score=Decimal("82"))]
    research_evaluations = [
        SimpleNamespace(
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
            decision_quality_score=78,
        )
    ]
    campaigns = [
        SimpleNamespace(
            created_at=datetime.now(timezone.utc) - timedelta(hours=4),
            completed_at=datetime.now(timezone.utc) - timedelta(hours=1),
            status="COMPLETED",
        )
    ]
    risk_events = [SimpleNamespace(created_at=datetime.now(timezone.utc) - timedelta(hours=1), action_taken="blocked")]
    summary = SimpleNamespace(trade_count=2, win_rate=Decimal("0.5"))
    _install_common_monkeypatches(
        monkeypatch,
        trades=trades,
        decision_scores=decision_scores,
        research_evaluations=research_evaluations,
        campaigns=campaigns,
        risk_events=risk_events,
        summary=summary,
    )

    result = await service.build_dashboard_intelligence_score(db=_DummySession(), range_value="7d")

    assert result.score > 0
    assert result.data_completeness > 0
    assert result.range == "7d"
    assert result.timeline
    assert all(point.score >= 0 for point in result.timeline)


@pytest.mark.asyncio
async def test_dashboard_intelligence_score_partial_completeness(monkeypatch: pytest.MonkeyPatch) -> None:
    trades = [_trade(minutes_ago=180, side="buy", price="100"), _trade(minutes_ago=60, side="sell", price="101")]
    summary = SimpleNamespace(trade_count=2, win_rate=Decimal("0.25"))
    _install_common_monkeypatches(
        monkeypatch,
        trades=trades,
        decision_scores=[],
        research_evaluations=[],
        campaigns=[],
        risk_events=[],
        summary=summary,
    )

    result = await service.build_dashboard_intelligence_score(db=_DummySession(), range_value="30d")

    assert 0 < result.data_completeness < 100
    assert result.timeline


@pytest.mark.asyncio
async def test_dashboard_intelligence_score_handles_requested_range(monkeypatch: pytest.MonkeyPatch) -> None:
    trades = [_trade(minutes_ago=60 * 24, side="buy", price="100"), _trade(minutes_ago=30, side="sell", price="115")]
    summary = SimpleNamespace(trade_count=2, win_rate=Decimal("0.5"))
    _install_common_monkeypatches(
        monkeypatch,
        trades=trades,
        decision_scores=[],
        research_evaluations=[],
        campaigns=[],
        risk_events=[],
        summary=summary,
    )

    short_result = await service.build_dashboard_intelligence_score(db=_DummySession(), range_value="24h")
    long_result = await service.build_dashboard_intelligence_score(db=_DummySession(), range_value="90d")

    assert short_result.range == "24h"
    assert long_result.range == "90d"
    assert len(long_result.timeline) >= len(short_result.timeline)