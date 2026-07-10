from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.capital_ledger import CapitalLedgerResponse
from app.services.capital_ledger import build_capital_ledger

router = APIRouter(prefix="/capital", tags=["capital"])


@router.get("/ledger", response_model=CapitalLedgerResponse)
async def get_capital_ledger(
    status: str = Query(default="all"),
    type_value: str = Query(default="all", alias="type"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> CapitalLedgerResponse:
    return await build_capital_ledger(
        db=db,
        status=status,
        capital_type=type_value,
        page=page,
        page_size=page_size,
    )
