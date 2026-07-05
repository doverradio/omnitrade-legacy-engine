from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy


@dataclass(slots=True)
class SeedSummary:
    strategy_created: bool
    parameter_set_created: bool


DEFAULT_STRATEGY_NAME = "MA Crossover"
DEFAULT_STRATEGY_SLUG = "ma_crossover"
DEFAULT_STRATEGY_VERSION = "1.0.0"
DEFAULT_PARAMETER_SET_LABEL = "default-v1"
DEFAULT_PARAMETER_SET_PARAMS = {"fast_period": 10, "slow_period": 50}


async def seed_strategies(db_session: AsyncSession) -> SeedSummary:
    strategy = await db_session.scalar(select(Strategy).where(Strategy.slug == DEFAULT_STRATEGY_SLUG))
    strategy_created = False
    parameter_set_created = False

    if strategy is None:
        strategy = Strategy(
            name=DEFAULT_STRATEGY_NAME,
            slug=DEFAULT_STRATEGY_SLUG,
            description="Seeded default strategy for backtesting validation",
            module_version=DEFAULT_STRATEGY_VERSION,
            is_active=False,
        )
        db_session.add(strategy)
        await db_session.flush()
        strategy_created = True

    existing_parameter_set = await db_session.scalar(
        select(ParameterSet)
        .where(ParameterSet.strategy_id == strategy.id)
        .where(ParameterSet.label == DEFAULT_PARAMETER_SET_LABEL)
    )
    if existing_parameter_set is None:
        db_session.add(
            ParameterSet(
                strategy_id=strategy.id,
                label=DEFAULT_PARAMETER_SET_LABEL,
                params=DEFAULT_PARAMETER_SET_PARAMS,
                created_by="system",
            )
        )
        parameter_set_created = True

    await db_session.commit()
    return SeedSummary(strategy_created=strategy_created, parameter_set_created=parameter_set_created)


def print_summary(summary: SeedSummary) -> None:
    print("Seed strategies summary")
    print(f"Strategy created: {summary.strategy_created}")
    print(f"Default parameter set created: {summary.parameter_set_created}")


async def _async_main() -> int:
    setup_logging()

    async with AsyncSessionLocal() as db_session:
        summary = await seed_strategies(db_session)

    print_summary(summary)
    return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
