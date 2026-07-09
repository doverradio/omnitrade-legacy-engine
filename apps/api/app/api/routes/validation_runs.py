from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.db.session import get_db
from app.schemas.validation_runs import (
    ValidationRunCreateRequest,
    ValidationRunDetailResponse,
    ValidationRunEventListResponse,
    ValidationRunListResponse,
    ValidationRunMetricsResponse,
    ValidationRunResponse,
    ValidationRunStartResponse,
)
from app.services.validation_runs.service import (
    cancel_validation_run,
    create_validation_run,
    get_validation_run,
    get_validation_run_metrics,
    list_validation_run_events,
    list_validation_runs,
    start_validation_run,
)

router = APIRouter(prefix="/validation-runs", tags=["validation-runs"])


@router.get("", response_model=ValidationRunListResponse)
async def get_validation_runs(db: AsyncSession = Depends(get_db)) -> ValidationRunListResponse:
    return ValidationRunListResponse(items=await list_validation_runs(db=db))


@router.get("/{validation_run_id}", response_model=ValidationRunDetailResponse)
async def get_validation_run_detail(
    validation_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunDetailResponse:
    try:
        parsed_id = uuid.UUID(validation_run_id)
    except ValueError:
        raise InvalidRequestError(
            message="Invalid validation_run_id",
            details={"validation_run_id": validation_run_id},
        )
    return await get_validation_run(db=db, validation_run_id=parsed_id)


@router.post("", response_model=ValidationRunResponse)
async def post_validation_run(
    request: ValidationRunCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunResponse:
    return await create_validation_run(db=db, request=request)


@router.post("/{validation_run_id}/start", response_model=ValidationRunStartResponse)
async def post_start_validation_run(
    validation_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunStartResponse:
    try:
        parsed_id = uuid.UUID(validation_run_id)
    except ValueError:
        raise InvalidRequestError(
            message="Invalid validation_run_id",
            details={"validation_run_id": validation_run_id},
        )
    run, metrics = await start_validation_run(db=db, validation_run_id=parsed_id)
    return ValidationRunStartResponse(run=run, initial_metrics=metrics)


@router.post("/{validation_run_id}/cancel", response_model=ValidationRunResponse)
async def post_cancel_validation_run(
    validation_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunResponse:
    try:
        parsed_id = uuid.UUID(validation_run_id)
    except ValueError:
        raise InvalidRequestError(
            message="Invalid validation_run_id",
            details={"validation_run_id": validation_run_id},
        )
    return await cancel_validation_run(db=db, validation_run_id=parsed_id)


@router.get("/{validation_run_id}/events", response_model=ValidationRunEventListResponse)
async def get_validation_run_event_history(
    validation_run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    order: str = Query(default="newest"),
    window: str = Query(default="entire_run"),
    category: str = Query(default="all"),
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> ValidationRunEventListResponse:
    try:
        parsed_id = uuid.UUID(validation_run_id)
    except ValueError:
        raise InvalidRequestError(
            message="Invalid validation_run_id",
            details={"validation_run_id": validation_run_id},
        )
    return await list_validation_run_events(
        db=db,
        validation_run_id=parsed_id,
        page=page,
        page_size=page_size,
        order=order,
        window=window,
        category=category,
        search=q,
    )


@router.get("/{validation_run_id}/metrics", response_model=ValidationRunMetricsResponse)
async def get_validation_run_metric_summary(
    validation_run_id: str,
    db: AsyncSession = Depends(get_db),
) -> ValidationRunMetricsResponse:
    try:
        parsed_id = uuid.UUID(validation_run_id)
    except ValueError:
        raise InvalidRequestError(
            message="Invalid validation_run_id",
            details={"validation_run_id": validation_run_id},
        )
    return await get_validation_run_metrics(db=db, validation_run_id=parsed_id)
