from __future__ import annotations

from fastapi import APIRouter

from app.db.session import run_read_with_retry
from app.schemas.operations import OperationalFreshnessResponse, OperationalStatusResponse, RuntimeReadinessResponse
from app.services.operations_status import build_operational_freshness, build_operations_status, build_runtime_readiness

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


@router.get("/runtime-readiness", response_model=RuntimeReadinessResponse)
async def get_runtime_readiness() -> RuntimeReadinessResponse:
    return await run_read_with_retry(
        lambda db: build_runtime_readiness(db=db),
        operation_name="runtime_readiness",
    )
