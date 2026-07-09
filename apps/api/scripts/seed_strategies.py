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


DEFAULT_STRATEGY_SEEDS = (
    {
        "name": "MA Crossover",
        "slug": "ma_crossover",
        "description": "Seeded default strategy for backtesting validation",
        "module_version": "1.0.0",
        "parameter_set_label": "default-v1",
        "parameter_set_params": {"fast_period": 10, "slow_period": 50, "ma_type": "sma"},
    },
    {
        "name": "RSI Mean Reversion",
        "slug": "rsi_mean_reversion",
        "description": "Seeded RSI strategy for backtesting validation",
        "module_version": "1.0.0",
        "parameter_set_label": "default-v1",
        "parameter_set_params": {"rsi_period": 14, "buy_threshold": 30, "sell_threshold": 70},
    },
)


async def seed_strategies(db_session: AsyncSession) -> SeedSummary:
    strategy_created = False
    parameter_set_created = False

    for seed in DEFAULT_STRATEGY_SEEDS:
        strategy = await db_session.scalar(select(Strategy).where(Strategy.slug == seed["slug"]))

        if strategy is None:
            strategy = Strategy(
                name=seed["name"],
                slug=seed["slug"],
                description=seed["description"],
                module_version=seed["module_version"],
                is_active=False,
            )
            db_session.add(strategy)
            await db_session.flush()
            strategy_created = True

        existing_parameter_set = await db_session.scalar(
            select(ParameterSet)
            .where(ParameterSet.strategy_id == strategy.id)
            .where(ParameterSet.label == seed["parameter_set_label"])
        )
        if existing_parameter_set is None:
            db_session.add(
                ParameterSet(
                    strategy_id=strategy.id,
                    label=seed["parameter_set_label"],
                    params=seed["parameter_set_params"],
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
