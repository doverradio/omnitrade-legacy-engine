from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.services.strategies.promotion import activate_strategy


DEFAULT_STRATEGY_SLUG = "ma_crossover"


async def _async_main() -> int:
    setup_logging()

    async with AsyncSessionLocal() as db_session:
        strategy = await db_session.scalar(select(Strategy).where(Strategy.slug == DEFAULT_STRATEGY_SLUG).limit(1))
        if strategy is None:
            raise RuntimeError("Seeded MA Crossover strategy not found")

        if strategy.is_active:
            print("Strategy already active.")
            return 0

        await activate_strategy(db_session, strategy_id=strategy.id, activated_by="system")

    print("Activated strategy:")
    print("MA Crossover")
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())