from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query

from app.db.session import run_read_with_retry
from app.schemas.mission_control import MissionControlIntelligenceResponse, MissionControlSnapshotHistoryResponse
from app.schemas.position_lifecycle import PositionLifecycleResponse
from app.schemas.profit_intelligence import ProfitMetricResponse
from app.services.mission_control_intelligence import build_mission_control_intelligence
from app.services.mission_control_snapshot_history import build_snapshot_history
from app.services.position_lifecycle import build_position_lifecycle_report
from app.services.profit_intelligence import build_profit_metrics

router = APIRouter(prefix="/mission-control", tags=["mission-control"])


@router.get("/intelligence", response_model=MissionControlIntelligenceResponse)
async def get_mission_control_intelligence(
    range_value: str = Query(default="24h", alias="range"),
) -> MissionControlIntelligenceResponse:
    return await run_read_with_retry(
        lambda db: build_mission_control_intelligence(db=db, range_value=range_value),
        operation_name="mission_control_intelligence",
    )


@router.get("/profit", response_model=ProfitMetricResponse)
async def get_mission_control_profit(
    range_value: str = Query(default="24h", alias="range"),
    mode: str = Query(default="paper"),
    capital_pool_id: str | None = Query(default=None),
    validation_run_id: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
) -> ProfitMetricResponse:
    from uuid import UUID

    parsed_validation_run_id = UUID(validation_run_id) if validation_run_id else None
    parsed_strategy_id = UUID(strategy_id) if strategy_id else None
    return await run_read_with_retry(
        lambda db: build_profit_metrics(
            db=db,
            range_value=range_value,
            mode=mode,
            capital_pool_id=capital_pool_id,
            validation_run_id=parsed_validation_run_id,
            strategy_id=parsed_strategy_id,
            symbol=symbol,
        ),
        operation_name="mission_control_profit",
    )


@router.get("/positions/lifecycle", response_model=PositionLifecycleResponse)
async def get_position_lifecycle_report(
    position_id: str | None = Query(default=None),
    account_id: UUID | None = Query(default=None),
    campaign_id: int | None = Query(default=None),
    asset_class: str | None = Query(default=None),
    recommendation: str | None = Query(default=None),
) -> PositionLifecycleResponse:
    return await run_read_with_retry(
        lambda db: build_position_lifecycle_report(
            db=db,
            position_id=position_id,
            account_id=account_id,
            campaign_id=campaign_id,
            asset_class=asset_class,
            recommendation=recommendation,
        ),
        operation_name="mission_control_position_lifecycle",
    )


@router.get("/intelligence/explain")
async def get_mission_control_intelligence_explain(
    range_value: str = Query(default="24h", alias="range"),
    dimension: str | None = Query(default=None),
) -> dict[str, Any]:
    intelligence = await run_read_with_retry(
        lambda db: build_mission_control_intelligence(db=db, range_value=range_value),
        operation_name="mission_control_intelligence_explain",
    )
    profit = await run_read_with_retry(
        lambda db: build_profit_metrics(db=db, range_value=range_value, mode="paper"),
        operation_name="mission_control_profit_explain",
    )

    metric_breakdown = intelligence.metric_breakdown
    if dimension is not None:
        metric_breakdown = [item for item in metric_breakdown if item.name.lower() == dimension.strip().lower()]

    return {
        "range": range_value,
        "dimension": dimension,
        "score": intelligence.current_score,
        "confidence": intelligence.confidence,
        "formula_version": intelligence.version,
        "weighted_components": [item.model_dump() for item in metric_breakdown],
        "source_counts": profit.source_counts,
        "positive_contributors": [item.name for item in metric_breakdown if item.score >= 70],
        "negative_contributors": [item.name for item in metric_breakdown if item.score < 50],
        "missing_evidence": [] if profit.data_completeness >= 100 else ["incomplete_profit_sources"],
        "alerts": [item.model_dump() for item in intelligence.operations.alerts],
        "related_events": [item.model_dump() for item in intelligence.timeline_events[:20]],
        "related_validation_runs": [item.model_dump() for item in intelligence.validation_runs],
        "related_profit": profit.model_dump(),
    }


@router.get("/intelligence/history", response_model=MissionControlSnapshotHistoryResponse)
async def get_mission_control_intelligence_history(
    range_value: str = Query(default="24h", alias="range"),
    dimension: str | None = Query(default=None),
) -> MissionControlSnapshotHistoryResponse:
    return await run_read_with_retry(
        lambda db: build_snapshot_history(db=db, range_value=range_value, dimension=dimension),
        operation_name="mission_control_intelligence_history",
    )