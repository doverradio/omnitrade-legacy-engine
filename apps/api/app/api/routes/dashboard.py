from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.dashboard import DashboardIntelligenceScoreResponse
from app.services.dashboard_intelligence import build_dashboard_intelligence_score

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/intelligence-score", response_model=DashboardIntelligenceScoreResponse)
async def get_dashboard_intelligence_score(
    range_value: str = Query(default="24h", alias="range"),
    db: AsyncSession = Depends(get_db),
) -> DashboardIntelligenceScoreResponse:
    return await build_dashboard_intelligence_score(db=db, range_value=range_value)