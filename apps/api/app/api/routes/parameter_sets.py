from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.parameter_set import ParameterSet
from app.schemas.parameter_set import ParameterSetListResponse, ParameterSetResponse

router = APIRouter(prefix="/parameter-sets", tags=["parameter-sets"])


@router.get("", response_model=ParameterSetListResponse)
async def list_parameter_sets(db: AsyncSession = Depends(get_db)) -> ParameterSetListResponse:
    parameter_sets = (await db.execute(select(ParameterSet).order_by(ParameterSet.created_at.asc()))).scalars().all()

    return ParameterSetListResponse(
        items=[
            ParameterSetResponse(
                id=parameter_set.id,
                strategy_id=parameter_set.strategy_id,
                name=parameter_set.label,
                parameters=parameter_set.params,
            )
            for parameter_set in parameter_sets
        ]
    )