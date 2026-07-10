from __future__ import annotations

from fastapi import APIRouter, Query

from app.db.session import run_read_with_retry
from app.schemas.mission_control import MissionControlIntelligenceResponse
from app.services.mission_control_intelligence import build_mission_control_intelligence

router = APIRouter(prefix="/mission-control", tags=["mission-control"])


@router.get("/intelligence", response_model=MissionControlIntelligenceResponse)
async def get_mission_control_intelligence(
    range_value: str = Query(default="24h", alias="range"),
) -> MissionControlIntelligenceResponse:
    return await run_read_with_retry(
        lambda db: build_mission_control_intelligence(db=db, range_value=range_value),
        operation_name="mission_control_intelligence",
    )