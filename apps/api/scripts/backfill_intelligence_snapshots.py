from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from app.db.session import AsyncSessionLocal
from app.services.system_intelligence_snapshots import capture_system_intelligence_snapshot_if_due


_WINDOW_BY_RANGE = {
    "24h": timedelta(hours=24),
    "72h": timedelta(hours=72),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": timedelta(days=3650),
}


async def run_backfill(*, range_value: str, batch_size: int, dry_run: bool) -> int:
    now = datetime.now(timezone.utc)
    start = now - _WINDOW_BY_RANGE[range_value]
    created = 0

    async with AsyncSessionLocal() as db:
        cursor = start
        while cursor < now:
            for _ in range(batch_size):
                if cursor >= now:
                    break
                if not dry_run:
                    snapshot = await capture_system_intelligence_snapshot_if_due(db=db)
                    if snapshot is not None:
                        created += 1
                        await db.commit()
                cursor += timedelta(minutes=15)
            if dry_run:
                break

    return created


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill system intelligence snapshots")
    parser.add_argument("--range", choices=sorted(_WINDOW_BY_RANGE.keys()), default="24h")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    created = asyncio.run(run_backfill(range_value=args.range, batch_size=max(1, args.batch_size), dry_run=args.dry_run))
    print(f"backfill_range={args.range} dry_run={args.dry_run} created_snapshots={created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
