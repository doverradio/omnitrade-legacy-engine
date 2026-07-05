from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.data.binance_client import NormalizedCandle
from app.services.data.candle_writer import upsert_candles


TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"


@pytest.mark.asyncio
async def test_upsert_candles_twice_with_overlap_creates_no_duplicates() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TEMP TABLE candles (
                    id BIGSERIAL PRIMARY KEY,
                    asset_id UUID NOT NULL,
                    interval TEXT NOT NULL,
                    open_time TIMESTAMPTZ NOT NULL,
                    close_time TIMESTAMPTZ NOT NULL,
                    open NUMERIC NOT NULL,
                    high NUMERIC NOT NULL,
                    low NUMERIC NOT NULL,
                    close NUMERIC NOT NULL,
                    volume NUMERIC NOT NULL,
                    source TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (asset_id, interval, open_time)
                )
                """
            )
        )

        session = AsyncSession(bind=connection, expire_on_commit=False)

        asset_id = uuid.uuid4()
        interval = "1m"

        first_batch = [
            NormalizedCandle(
                open_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                close_time=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.5"),
                close=Decimal("100.5"),
                volume=Decimal("10"),
            ),
            NormalizedCandle(
                open_time=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
                close_time=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
                open=Decimal("100.5"),
                high=Decimal("102.0"),
                low=Decimal("100.2"),
                close=Decimal("101.8"),
                volume=Decimal("11"),
            ),
        ]

        second_batch = [
            NormalizedCandle(
                open_time=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
                close_time=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
                open=Decimal("100.6"),
                high=Decimal("102.1"),
                low=Decimal("100.1"),
                close=Decimal("101.9"),
                volume=Decimal("12"),
            ),
            NormalizedCandle(
                open_time=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
                close_time=datetime(2024, 1, 1, 0, 3, tzinfo=timezone.utc),
                open=Decimal("101.9"),
                high=Decimal("103.0"),
                low=Decimal("101.5"),
                close=Decimal("102.8"),
                volume=Decimal("13"),
            ),
        ]

        first_count = await upsert_candles(session, asset_id, interval, first_batch)
        second_count = await upsert_candles(session, asset_id, interval, second_batch)

        total_rows = await session.scalar(text("SELECT COUNT(*) FROM candles"))
        overlapped_close = await session.scalar(
            text(
                """
                SELECT close
                FROM candles
                WHERE asset_id = :asset_id
                  AND interval = :interval
                  AND open_time = :open_time
                """
            ),
            {
                "asset_id": str(asset_id),
                "interval": interval,
                "open_time": datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
            },
        )

        await session.close()

    await engine.dispose()

    assert first_count == 2
    assert second_count == 2
    assert total_rows == 3
    assert str(overlapped_close) == "101.9"