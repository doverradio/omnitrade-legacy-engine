from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.asset import Asset
from app.services.data.binance_client import BinanceClientError, BinanceUSClient
from app.services.data.candle_writer import upsert_candles
from app.services.data.http_client import AsyncHTTPClient


logger = logging.getLogger(__name__)

SUPPORTED_INTERVALS = {"1m", "5m", "15m", "1h", "1d"}
INTERVAL_TO_DELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}


@dataclass(slots=True)
class BackfillArgs:
    symbol: str
    interval: str
    start_date: datetime
    end_date: datetime


@dataclass(slots=True)
class BackfillReport:
    symbol: str
    interval: str
    requested_start: datetime
    requested_end: datetime
    succeeded_start: datetime | None
    succeeded_end: datetime | None
    rows_written: int
    failure_message: str | None


def parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def parse_cli_args(argv: Sequence[str] | None = None) -> BackfillArgs:
    parser = argparse.ArgumentParser(description="Backfill historical Binance.US candles into the OmniTrade DB")
    parser.add_argument("--symbol", required=True, help="Asset symbol, e.g. BTCUSDT")
    parser.add_argument("--interval", required=True, help="Candle interval: 1m, 5m, 15m, 1h, 1d")
    parser.add_argument("--start-date", required=True, help="ISO datetime, e.g. 2025-07-05T00:00:00Z")
    parser.add_argument("--end-date", required=True, help="ISO datetime, e.g. 2026-07-05T00:00:00Z")

    parsed = parser.parse_args(argv)
    interval = parsed.interval.strip()
    if interval not in SUPPORTED_INTERVALS:
        parser.error(f"Unsupported interval '{interval}'. Supported values: {', '.join(sorted(SUPPORTED_INTERVALS))}")

    start_date = parse_iso_datetime(parsed.start_date)
    end_date = parse_iso_datetime(parsed.end_date)
    if start_date >= end_date:
        parser.error("start-date must be earlier than end-date")

    return BackfillArgs(
        symbol=parsed.symbol.strip().upper(),
        interval=interval,
        start_date=start_date,
        end_date=end_date,
    )


async def get_asset_by_symbol(db_session: AsyncSession, symbol: str) -> Asset | None:
    return await db_session.scalar(select(Asset).where(Asset.symbol == symbol).order_by(Asset.created_at.asc()))


async def backfill_symbol(
    db_session: AsyncSession,
    client: BinanceUSClient,
    args: BackfillArgs,
) -> BackfillReport:
    asset = await get_asset_by_symbol(db_session, args.symbol)
    if asset is None:
        raise ValueError(f"Asset '{args.symbol}' not found. Run scripts/seed_assets.py first.")

    if asset.exchange != "binance_us":
        raise ValueError(
            f"Asset '{args.symbol}' is on exchange '{asset.exchange}', but this backfill script currently targets binance_us assets only."
        )

    interval_delta = INTERVAL_TO_DELTA[args.interval]
    cursor = args.start_date
    rows_written = 0
    succeeded_start: datetime | None = None
    succeeded_end: datetime | None = None

    while cursor < args.end_date:
        page_end = min(cursor + interval_delta * 1000, args.end_date)

        try:
            candles = await client.fetch_klines(
                symbol=args.symbol,
                interval=args.interval,
                start_time=cursor,
                end_time=page_end,
            )
        except BinanceClientError as exc:
            failure_message = (
                f"Backfill stopped after partial success: succeeded_window={succeeded_start.isoformat() if succeeded_start else None}"
                f"..{succeeded_end.isoformat() if succeeded_end else None}, failed_window={cursor.isoformat()}..{page_end.isoformat()},"
                f" error={exc}"
            )
            return BackfillReport(
                symbol=args.symbol,
                interval=args.interval,
                requested_start=args.start_date,
                requested_end=args.end_date,
                succeeded_start=succeeded_start,
                succeeded_end=succeeded_end,
                rows_written=rows_written,
                failure_message=failure_message,
            )

        if candles:
            written = await upsert_candles(db_session, asset.id, args.interval, candles)
            await db_session.commit()

            rows_written += written
            if succeeded_start is None:
                succeeded_start = candles[0].open_time
            succeeded_end = candles[-1].open_time

            cursor = candles[-1].open_time + interval_delta
        else:
            cursor = page_end + interval_delta

    return BackfillReport(
        symbol=args.symbol,
        interval=args.interval,
        requested_start=args.start_date,
        requested_end=args.end_date,
        succeeded_start=succeeded_start,
        succeeded_end=succeeded_end,
        rows_written=rows_written,
        failure_message=None,
    )


def print_report(report: BackfillReport) -> None:
    print("Historical backfill report")
    print(f"Symbol: {report.symbol}")
    print(f"Interval: {report.interval}")
    print(f"Requested window: {report.requested_start.isoformat()} .. {report.requested_end.isoformat()}")
    print(
        "Succeeded window: "
        f"{report.succeeded_start.isoformat() if report.succeeded_start else 'none'}"
        f" .. {report.succeeded_end.isoformat() if report.succeeded_end else 'none'}"
    )
    print(f"Rows written: {report.rows_written}")

    if report.failure_message:
        print(f"Status: PARTIAL_FAILURE")
        print(report.failure_message)
    else:
        print("Status: SUCCESS")


async def _async_main(argv: Sequence[str] | None = None) -> int:
    setup_logging()
    args = parse_cli_args(argv)

    async with AsyncSessionLocal() as db_session:
        async with AsyncHTTPClient() as http_client:
            client = BinanceUSClient(http_client)
            report = await backfill_symbol(db_session, client, args)

    print_report(report)
    return 1 if report.failure_message else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
