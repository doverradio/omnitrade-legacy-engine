from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.services.data.binance_client import NormalizedCandle


logger = logging.getLogger(__name__)


def _deduplicate_candles_by_open_time(candles: list[NormalizedCandle]) -> list[NormalizedCandle]:
    if len(candles) <= 1:
        return list(candles)

    deduplicated_by_open_time: dict = {}
    for candle in candles:
        # Last occurrence wins so later provider pages/entries can correct earlier values.
        deduplicated_by_open_time[candle.open_time] = candle

    deduplicated = list(deduplicated_by_open_time.values())
    deduplicated.sort(key=lambda candle: candle.open_time)
    return deduplicated


async def upsert_candles(
    db_session: AsyncSession,
    asset_id: UUID,
    interval: str,
    candles: list[NormalizedCandle],
) -> int:
    if not candles:
        return 0

    deduplicated_candles = _deduplicate_candles_by_open_time(candles)
    duplicate_count = len(candles) - len(deduplicated_candles)
    if duplicate_count > 0:
        open_time_counts: dict = {}
        for candle in candles:
            open_time_counts[candle.open_time] = open_time_counts.get(candle.open_time, 0) + 1
        duplicate_open_times = sorted(
            {
                open_time.isoformat()
                for open_time, count in open_time_counts.items()
                if count > 1
            }
        )
        source = deduplicated_candles[-1].source if deduplicated_candles else "unknown"
        logger.warning(
            "candle_ingestion_batch_deduplicated provider=%s asset_id=%s interval=%s input_count=%s unique_count=%s duplicate_count=%s duplicate_open_times=%s",
            source,
            asset_id,
            interval,
            len(candles),
            len(deduplicated_candles),
            duplicate_count,
            duplicate_open_times[:8],
        )

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
        for candle in deduplicated_candles
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