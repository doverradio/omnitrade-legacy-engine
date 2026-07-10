from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_intelligence_snapshot import SystemIntelligenceSnapshot
from app.services.mission_control_intelligence import build_mission_control_intelligence
from app.services.profit_intelligence import build_profit_metrics

SNAPSHOT_SCHEMA_VERSION = "v1"
SNAPSHOT_INTERVAL_MINUTES = 15


def _bucket_bounds(now: datetime) -> tuple[datetime, datetime]:
    normalized = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    bucket_minute = (normalized.minute // SNAPSHOT_INTERVAL_MINUTES) * SNAPSHOT_INTERVAL_MINUTES
    bucket_start = normalized.replace(minute=bucket_minute)
    bucket_end = bucket_start + timedelta(minutes=SNAPSHOT_INTERVAL_MINUTES)
    return bucket_start, bucket_end


async def capture_system_intelligence_snapshot_if_due(*, db: AsyncSession) -> SystemIntelligenceSnapshot | None:
    if not hasattr(db, "scalar") or not hasattr(db, "execute"):
        return None
    now = datetime.now(timezone.utc)
    bucket_start, bucket_end = _bucket_bounds(now)
    existing = await db.scalar(
        select(SystemIntelligenceSnapshot)
        .where(SystemIntelligenceSnapshot.bucket_start == bucket_start)
        .where(SystemIntelligenceSnapshot.bucket_end == bucket_end)
        .where(SystemIntelligenceSnapshot.schema_version == SNAPSHOT_SCHEMA_VERSION)
        .limit(1)
    )
    if existing is not None:
        return None

    intelligence = await build_mission_control_intelligence(db=db, range_value="24h")
    paper_profit = await build_profit_metrics(db=db, range_value="24h", mode="paper")
    live_profit = await build_profit_metrics(db=db, range_value="24h", mode="live")
    combined_profit = await build_profit_metrics(db=db, range_value="24h", mode="combined")

    score_by_name = {item.name: item.score for item in intelligence.metric_breakdown}
    snapshot = SystemIntelligenceSnapshot(
        captured_at=now,
        bucket_start=bucket_start,
        bucket_end=bucket_end,
        overall_score=intelligence.current_score,
        confidence=intelligence.confidence,
        data_completeness=paper_profit.data_completeness,
        market_awareness_score=score_by_name.get("Market Awareness"),
        decision_quality_score=score_by_name.get("Prediction Quality"),
        execution_reliability_score=score_by_name.get("Execution Reliability") or score_by_name.get("Execution Health"),
        risk_discipline_score=score_by_name.get("Risk Discipline"),
        research_progress_score=score_by_name.get("Research Progress") or score_by_name.get("Research Activity"),
        adaptation_rate_score=score_by_name.get("Adaptation Rate"),
        operational_health_score=score_by_name.get("Operational Health") or score_by_name.get("Infrastructure Health"),
        capital_efficiency_score=score_by_name.get("Capital Efficiency") or score_by_name.get("Paper Trading Health"),
        profit_performance_score=score_by_name.get("Profit Performance"),
        paper_net_profit=paper_profit.net_profit,
        live_net_profit=live_profit.net_profit,
        combined_net_profit=combined_profit.net_profit,
        paper_equity=paper_profit.ending_equity,
        live_equity=live_profit.ending_equity,
        combined_equity=combined_profit.ending_equity,
        realized_pnl=paper_profit.realized_pnl,
        unrealized_pnl=paper_profit.unrealized_pnl,
        fees=paper_profit.fees,
        drawdown_percent=paper_profit.max_drawdown_percent,
        source_counts=paper_profit.source_counts,
        explanations={
            "intelligence_notes": intelligence.notes,
            "profit_explanation": paper_profit.calculation_explanation,
        },
        annotations=[item.model_dump(mode="json") for item in intelligence.timeline_events[:20]],
        schema_version=SNAPSHOT_SCHEMA_VERSION,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot
