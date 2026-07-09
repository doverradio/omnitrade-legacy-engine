from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.mission_control import MissionControlIntelligenceResponse
from app.services.mission_control_intelligence import build_mission_control_intelligence

router = APIRouter(prefix="/mission-control", tags=["mission-control"])


@router.get("/intelligence", response_model=MissionControlIntelligenceResponse)
async def get_mission_control_intelligence(
    range_value: str = Query(default="24h", alias="range"),
    db: AsyncSession = Depends(get_db),
) -> MissionControlIntelligenceResponse:
    return await build_mission_control_intelligence(db=db, range_value=range_value)