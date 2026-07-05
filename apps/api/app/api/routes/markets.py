from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.asset import Asset
from app.models.candle import Candle
from app.schemas.asset import AssetListResponse, AssetResponse
from app.schemas.candle import CandleListResponse, CandleResponse

router = APIRouter(prefix="/markets", tags=["markets"])

SUPPORTED_INTERVALS = {"1m", "5m", "15m", "1h", "1d"}
SUPPORTED_ASSET_CLASSES = {"crypto", "stock"}


@router.get("/assets", response_model=AssetListResponse)
async def list_assets(
    asset_class: str | None = Query(default=None),
    is_active: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
) -> AssetListResponse:
    if asset_class is not None and asset_class not in SUPPORTED_ASSET_CLASSES:
        raise InvalidRequestError(
            message="Invalid asset_class",
            details={"asset_class": asset_class},
        )

    statement = select(Asset).where(Asset.is_active.is_(is_active)).order_by(Asset.symbol.asc())
    if asset_class is not None:
        statement = statement.where(Asset.asset_class == asset_class)

    assets = (await db.execute(statement)).scalars().all()

    return AssetListResponse(items=[AssetResponse.model_validate(asset) for asset in assets])


@router.get("/candles", response_model=CandleListResponse)
async def get_candles(
    asset_id: uuid.UUID,
    interval: str,
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> CandleListResponse:
    if interval not in SUPPORTED_INTERVALS:
        raise InvalidRequestError(
            message="Invalid interval",
            details={"interval": interval},
        )

    resolved_end_time = end_time or datetime.now(timezone.utc)
    if start_time is not None and start_time >= resolved_end_time:
        raise InvalidRequestError(
            message="Invalid time range",
            details={"start_time": start_time.isoformat(), "end_time": resolved_end_time.isoformat()},
        )

    asset = await db.scalar(select(Asset.id).where(Asset.id == asset_id))
    if asset is None:
        raise NotFoundError(
            message="Asset not found",
            details={"asset_id": str(asset_id)},
        )

    statement = (
        select(Candle)
        .where(Candle.asset_id == asset_id)
        .where(Candle.interval == interval)
        .where(Candle.open_time <= resolved_end_time)
        .order_by(Candle.open_time.asc())
    )
    if start_time is not None:
        statement = statement.where(Candle.open_time >= start_time)

    candles = (await db.execute(statement)).scalars().all()

    return CandleListResponse(
        asset_id=asset_id,
        interval=interval,
        items=[CandleResponse.model_validate(candle) for candle in candles],
    )
