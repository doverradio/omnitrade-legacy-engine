#!/usr/bin/env python3
"""Read-only export of OmniTrade's already-ingested candle history into a
strategy_lab-compatible CSV.

Source of truth: the `candles` table (apps/api/app/models/candle.py),
joined to `assets` (apps/api/app/models/asset.py) for symbol/exchange
identity. Candles are ingested into this table by OmniTrade's own
production data-ingestion pipeline (apps/api/app/services/data/), which
this script does NOT invoke or duplicate -- it only reads what is already
there. This script issues SELECT statements only. It never INSERTs,
UPDATEs, or DELETEs, and never opens a network connection to Kraken or any
other exchange.

Requires the apps/api Python environment (SQLAlchemy + asyncpg) and network
access to wherever DATABASE_URL points -- typically the same Postgres
instance the API service itself uses. Run from the repo root:

    apps/api/.venv/bin/python3 -m tools.export_btc_candles_for_strategy_lab \\
        --symbol BTC-USD --exchange kraken_spot --interval 1h \\
        --output strategy_lab_data/btc_usd_1h.csv

or, with the conda environment used elsewhere in this repo for Alembic:

    /home/eric/miniconda3/envs/omnitrade311/bin/python3.11 \\
        tools/export_btc_candles_for_strategy_lab.py \\
        --symbol BTC-USD --exchange kraken_spot --interval 1h \\
        --output strategy_lab_data/btc_usd_1h.csv

Prints a data-quality report to stderr: asset identity, interval, first/
last timestamp, candle count, any gaps or duplicate timestamps, and how
many trailing not-yet-closed candles were excluded from the export.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# Make the apps/api "app" package importable regardless of cwd, without
# requiring this script to be run from inside apps/api.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _REPO_ROOT / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

_INTERVAL_TIMEDELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
}


@dataclass
class ExportReport:
    source_table: str
    symbol: str
    exchange: str
    interval: str
    candle_count: int = 0
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    distinct_sources: List[str] = field(default_factory=list)
    gap_count: int = 0
    gaps: List[str] = field(default_factory=list)
    duplicate_timestamps: int = 0
    excluded_not_yet_closed: int = 0
    insufficiency_notes: List[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "Candle Export Report",
            "=====================",
            f"Source table:            {self.source_table}",
            f"Asset identity:          symbol={self.symbol} exchange={self.exchange}",
            f"Interval:                {self.interval}",
            f"Candle count (exported): {self.candle_count}",
            f"First timestamp:         {self.first_timestamp.isoformat() if self.first_timestamp else 'n/a'}",
            f"Last timestamp:          {self.last_timestamp.isoformat() if self.last_timestamp else 'n/a'}",
            f"Distinct 'source' values:{', '.join(self.distinct_sources) or 'n/a'}",
            f"Gaps detected:           {self.gap_count}",
        ]
        for gap in self.gaps[:20]:
            lines.append(f"  - {gap}")
        if len(self.gaps) > 20:
            lines.append(f"  ... and {len(self.gaps) - 20} more")
        lines.append(f"Duplicate timestamps:    {self.duplicate_timestamps}")
        lines.append(f"Excluded (not yet closed at query time): {self.excluded_not_yet_closed}")
        if self.insufficiency_notes:
            lines.append("Data insufficiency:")
            for note in self.insufficiency_notes:
                lines.append(f"  - {note}")
        else:
            lines.append("Data insufficiency:      none noted")
        return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--exchange", default="kraken_spot")
    parser.add_argument("--interval", required=True, choices=sorted(_INTERVAL_TIMEDELTA))
    parser.add_argument("--output", required=True, help="Path to write the strategy_lab CSV to")
    parser.add_argument("--start", default=None, help="ISO timestamp, inclusive lower bound (optional)")
    parser.add_argument("--end", default=None, help="ISO timestamp, exclusive upper bound (optional)")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the DB connection string (defaults to the app's configured DATABASE_URL)",
    )
    return parser


async def _export(args: argparse.Namespace) -> ExportReport:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models.asset import Asset
    from app.models.candle import Candle

    database_url = args.database_url
    if database_url is None:
        from app.config import get_settings

        database_url = get_settings().database_url

    engine = create_async_engine(database_url, future=True)
    try:
        async with engine.connect() as conn:
            query = (
                select(Candle, Asset.symbol, Asset.exchange)
                .join(Asset, Candle.asset_id == Asset.id)
                .where(Asset.symbol == args.symbol)
                .where(Asset.exchange == args.exchange)
                .where(Candle.interval == args.interval)
                .order_by(Candle.open_time.asc())
            )
            if args.start is not None:
                query = query.where(Candle.open_time >= datetime.fromisoformat(args.start))
            if args.end is not None:
                query = query.where(Candle.open_time < datetime.fromisoformat(args.end))

            result = await conn.execute(query)
            rows = result.all()
    finally:
        await engine.dispose()

    return _build_report_and_write_csv(rows, args)


def _build_report_and_write_csv(rows, args: argparse.Namespace) -> ExportReport:
    report = ExportReport(
        source_table="apps/api/app/models/candle.py (Candle, table 'candles', joined to 'assets')",
        symbol=args.symbol,
        exchange=args.exchange,
        interval=args.interval,
    )

    if not rows:
        report.insufficiency_notes.append(
            f"No candles found for symbol={args.symbol} exchange={args.exchange} interval={args.interval}."
        )
        _write_csv([], args.output)
        return report

    now = datetime.now(timezone.utc)
    expected_delta = _INTERVAL_TIMEDELTA[args.interval]

    seen_open_times = set()
    duplicates = 0
    deduped = []
    for candle, symbol, exchange in rows:
        open_time = candle.open_time
        if open_time in seen_open_times:
            duplicates += 1
            continue
        seen_open_times.add(open_time)
        deduped.append(candle)

    excluded = 0
    closed_candles = []
    for candle in deduped:
        if candle.close_time > now:
            excluded += 1
            continue
        closed_candles.append(candle)

    report.duplicate_timestamps = duplicates
    report.excluded_not_yet_closed = excluded
    report.distinct_sources = sorted({c.source for c in closed_candles})

    if not closed_candles:
        report.insufficiency_notes.append(
            "All returned candles were excluded as not-yet-closed at query time; "
            "nothing usable was exported."
        )
        _write_csv([], args.output)
        return report

    report.candle_count = len(closed_candles)
    report.first_timestamp = closed_candles[0].open_time
    report.last_timestamp = closed_candles[-1].open_time

    gaps = []
    for previous, current in zip(closed_candles, closed_candles[1:]):
        delta = current.open_time - previous.open_time
        if delta != expected_delta:
            gaps.append(
                f"{previous.open_time.isoformat()} -> {current.open_time.isoformat()} "
                f"(expected {expected_delta}, got {delta})"
            )
    report.gap_count = len(gaps)
    report.gaps = gaps

    if report.candle_count < 200:
        report.insufficiency_notes.append(
            f"Only {report.candle_count} closed candles available -- likely too few for a "
            "meaningful trade-count sample at this interval."
        )
    if gaps:
        report.insufficiency_notes.append(
            f"{len(gaps)} gap(s) in the candle sequence -- the exported CSV is not a "
            "contiguous history; strategy_lab will still load it, but results should be "
            "read as multiple disjoint segments, not one continuous timeline."
        )

    _write_csv(closed_candles, args.output)
    return report


def _write_csv(candles, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow(
                [
                    candle.open_time.isoformat(),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                ]
            )


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    report = asyncio.run(_export(args))
    print(report.render(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
