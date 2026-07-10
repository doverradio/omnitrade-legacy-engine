from __future__ import annotations

from fastapi import APIRouter

from app.db.session import run_read_with_retry
from app.schemas.operations import OperationalFreshnessResponse, OperationalStatusResponse
from app.services.operations_status import build_operational_freshness, build_operations_status

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/status", response_model=OperationalStatusResponse)
async def get_operations_status() -> OperationalStatusResponse:
    return await run_read_with_retry(
        lambda db: build_operations_status(db=db),
        operation_name="operations_status",
    )


@router.get("/freshness", response_model=OperationalFreshnessResponse)
async def get_operations_freshness() -> OperationalFreshnessResponse:
    return await run_read_with_retry(
        lambda db: build_operational_freshness(db=db),
        operation_name="operations_freshness",
    )
