from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal, dispose_database_engine, is_retryable_db_connection_error
from app.models.asset import Asset
from app.services.data.binance_client import BinanceClientError, BinanceUSClient
from app.services.data.candle_writer import upsert_candles
from app.services.data.http_client import AsyncHTTPClient
from app.services.data.ingestion_status import set_last_successful_ingestion_at
from app.services.data.kraken_client import KrakenClientError, KrakenSpotClient


logger = logging.getLogger(__name__)

MVP_POLL_INTERVAL_SECONDS = 300
MVP_LOOKBACK = timedelta(hours=2)
MVP_INTERVAL = "1m"


@dataclass(slots=True)
class IngestionCycleResult:
    total_assets: int
    successful_assets: int
    failed_assets: int
    rows_written: int
    cycle_completed_at: datetime


async def get_active_crypto_assets(db_session: AsyncSession) -> list[Asset]:
    statement = (
        select(Asset)
        .where(Asset.is_active.is_(True))
        .where(Asset.asset_class == "crypto")
        .order_by(Asset.symbol.asc())
    )
    return (await db_session.execute(statement)).scalars().all()


async def run_ingestion_cycle(
    db_session: AsyncSession,
    client: BinanceUSClient,
    kraken_client: KrakenSpotClient | None = None,
    *,
    lookback: timedelta = MVP_LOOKBACK,
    interval: str = MVP_INTERVAL,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> IngestionCycleResult:
    assets = await get_active_crypto_assets(db_session)
    cycle_completed_at = now_fn()

    successful_assets = 0
    failed_assets = 0
    rows_written = 0

    for asset in assets:
        if asset.exchange == "binance_us":
            source_client = client
            source_interval = interval
            source_lookback = lookback
            source_symbol = asset.symbol
            provider = "binance_us"
        elif asset.exchange == "kraken_spot":
            if kraken_client is None:
                logger.warning(
                    "Skipping active crypto asset because Kraken ingestion is unavailable asset_id=%s symbol=%s exchange=%s",
                    asset.id,
                    asset.symbol,
                    asset.exchange,
                )
                failed_assets += 1
                continue

            source_client = kraken_client
            source_interval = "15m"
            source_lookback = timedelta(hours=24)
            source_symbol = _kraken_product_symbol(asset)
            provider = "kraken_spot"
        else:
            logger.warning(
                "Skipping active crypto asset %s on unsupported exchange %s for candle ingestion",
                asset.symbol,
                asset.exchange,
            )
            failed_assets += 1
            continue

        try:
            end_time = cycle_completed_at
            start_time = end_time - source_lookback
            logger.info(
                "candle_ingestion_fetch_started provider=%s asset_id=%s symbol=%s product=%s interval=%s start_time=%s end_time=%s",
                provider,
                asset.id,
                asset.symbol,
                source_symbol,
                source_interval,
                start_time.isoformat(),
                end_time.isoformat(),
            )
            candles = await source_client.fetch_klines(
                symbol=source_symbol,
                interval=source_interval,
                start_time=start_time,
                end_time=end_time,
            )
            fetched_count = len(candles)
            written = await upsert_candles(db_session, asset.id, source_interval, candles)
            await db_session.commit()

            newest_close_time = candles[-1].close_time if candles else None
            ingestion_lag_seconds = None
            if newest_close_time is not None:
                ingestion_lag_seconds = int((cycle_completed_at - newest_close_time).total_seconds())

            rows_written += written
            successful_assets += 1
            if fetched_count == 0:
                logger.info(
                    "candle_ingestion_no_new_closed_candles provider=%s asset_id=%s symbol=%s product=%s interval=%s",
                    provider,
                    asset.id,
                    asset.symbol,
                    source_symbol,
                    source_interval,
                )
            logger.info(
                "candle_ingestion_persisted provider=%s asset_id=%s symbol=%s product=%s interval=%s fetched_count=%s rows_written=%s newest_close_time=%s ingestion_lag_seconds=%s",
                provider,
                asset.id,
                asset.symbol,
                source_symbol,
                source_interval,
                fetched_count,
                written,
                newest_close_time.isoformat() if newest_close_time else None,
                ingestion_lag_seconds,
            )
        except (BinanceClientError, KrakenClientError):
            logger.exception("Recent-candle ingestion failed for symbol=%s exchange=%s", asset.symbol, asset.exchange)
            failed_assets += 1
            continue
        except Exception:
            logger.exception("Unexpected ingestion failure for symbol=%s exchange=%s", asset.symbol, asset.exchange)
            failed_assets += 1
            continue

    if successful_assets > 0:
        set_last_successful_ingestion_at(cycle_completed_at)

    return IngestionCycleResult(
        total_assets=len(assets),
        successful_assets=successful_assets,
        failed_assets=failed_assets,
        rows_written=rows_written,
        cycle_completed_at=cycle_completed_at,
    )


def _kraken_product_symbol(asset: Asset) -> str:
    symbol = asset.symbol.strip().upper()
    if "-" in symbol or "/" in symbol:
        return symbol
    if asset.base_currency:
        return f"{symbol}-{asset.base_currency.strip().upper()}"
    return symbol


async def run_forever(poll_interval_seconds: int = MVP_POLL_INTERVAL_SECONDS) -> None:
    setup_logging()

    async with AsyncHTTPClient() as http_client:
        client = BinanceUSClient(http_client)
        kraken_client = KrakenSpotClient(http_client)

        while True:
            sleep_seconds = poll_interval_seconds
            try:
                async with AsyncSessionLocal() as db_session:
                    result = await run_ingestion_cycle(db_session, client, kraken_client)

                logger.info(
                    "Ingestion cycle completed total_assets=%s successful_assets=%s failed_assets=%s rows_written=%s",
                    result.total_assets,
                    result.successful_assets,
                    result.failed_assets,
                    result.rows_written,
                )
            except Exception as exc:
                if is_retryable_db_connection_error(exc):
                    sleep_seconds = min(30, poll_interval_seconds)
                    await dispose_database_engine()
                    logger.warning(
                        "Ingestion worker detected transient database disconnect; retrying next cycle after bounded backoff",
                        exc_info=True,
                    )
                else:
                    raise

            await asyncio.sleep(sleep_seconds)


def main() -> int:
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
