from __future__ import annotations

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.services.data.binance_client import NormalizedCandle


async def upsert_candles(
    db_session: AsyncSession,
    asset_id: UUID,
    interval: str,
    candles: list[NormalizedCandle],
) -> int:
    if not candles:
        return 0

    values = [
        {
            "asset_id": asset_id,
            "interval": interval,
            "open_time": candle.open_time,
            "close_time": candle.close_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "source": candle.source,
        }
        for candle in candles
    ]

    statement = insert(Candle).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=[Candle.asset_id, Candle.interval, Candle.open_time],
        set_={
            "close_time": statement.excluded.close_time,
            "open": statement.excluded.open,
            "high": statement.excluded.high,
            "low": statement.excluded.low,
            "close": statement.excluded.close,
            "volume": statement.excluded.volume,
            "source": statement.excluded.source,
        },
    ).returning(Candle.open_time)

    result = await db_session.execute(statement)
    return len(result.fetchall())