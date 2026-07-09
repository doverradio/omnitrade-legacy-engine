from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.operations import OperationalStatusResponse
from app.services.operations_status import build_operations_status

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/status", response_model=OperationalStatusResponse)
async def get_operations_status(db: AsyncSession = Depends(get_db)) -> OperationalStatusResponse:
    return await build_operations_status(db=db)
