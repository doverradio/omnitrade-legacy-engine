from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_intelligence_snapshot import SystemIntelligenceSnapshot
from app.schemas.mission_control import MissionControlSnapshotHistoryPointResponse, MissionControlSnapshotHistoryResponse


_WINDOW_BY_RANGE: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}


def _fmt(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


async def build_snapshot_history(*, db: AsyncSession, range_value: str, dimension: str | None) -> MissionControlSnapshotHistoryResponse:
    normalized = range_value.strip().lower()
    if normalized not in _WINDOW_BY_RANGE:
        normalized = "24h"

    now = datetime.now(timezone.utc)
    statement = select(SystemIntelligenceSnapshot).order_by(SystemIntelligenceSnapshot.bucket_start.asc())
    window = _WINDOW_BY_RANGE[normalized]
    if window is not None:
        statement = statement.where(SystemIntelligenceSnapshot.bucket_end >= now - window)

    rows = (await db.execute(statement)).scalars().all()
    points = [
        MissionControlSnapshotHistoryPointResponse(
            snapshot_id=str(item.snapshot_id),
            captured_at=item.captured_at,
            bucket_start=item.bucket_start,
            bucket_end=item.bucket_end,
            overall_score=item.overall_score,
            confidence=item.confidence,
            data_completeness=item.data_completeness,
            market_awareness_score=item.market_awareness_score,
            decision_quality_score=item.decision_quality_score,
            execution_reliability_score=item.execution_reliability_score,
            risk_discipline_score=item.risk_discipline_score,
            research_progress_score=item.research_progress_score,
            adaptation_rate_score=item.adaptation_rate_score,
            operational_health_score=item.operational_health_score,
            capital_efficiency_score=item.capital_efficiency_score,
            profit_performance_score=item.profit_performance_score,
            paper_net_profit=_fmt(item.paper_net_profit),
            live_net_profit=_fmt(item.live_net_profit),
            combined_net_profit=_fmt(item.combined_net_profit),
            paper_equity=_fmt(item.paper_equity),
            live_equity=_fmt(item.live_equity),
            combined_equity=_fmt(item.combined_equity),
            realized_pnl=_fmt(item.realized_pnl),
            unrealized_pnl=_fmt(item.unrealized_pnl),
            fees=_fmt(item.fees),
            drawdown_percent=_fmt(item.drawdown_percent),
            source_counts={k: int(v) for k, v in (item.source_counts or {}).items() if isinstance(v, (int, float))},
            annotations=[entry for entry in (item.annotations or []) if isinstance(entry, dict)],
            schema_version=item.schema_version,
        )
        for item in rows
    ]

    return MissionControlSnapshotHistoryResponse(
        range=normalized,
        dimension=dimension,
        points=points,
        generated_at=now,
    )
