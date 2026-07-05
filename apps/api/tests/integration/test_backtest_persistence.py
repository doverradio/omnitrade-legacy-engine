from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.backtesting.persistence import (
    create_backtest_record,
    mark_backtest_running,
    run_backtest_and_persist,
)
from app.services.strategies.base import Signal, Strategy, StrategyContext


TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade"


@dataclass(slots=True)
class StubStrategy(Strategy):
    slug: str = "stub"
    default_params: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = {}

    def generate_signal(self, context: StrategyContext) -> Signal:
        index = len(context.candles) - 1
        action = "buy" if index == 0 else "sell" if index == 2 else "hold"
        return Signal(
            action=action,
            strength=Decimal("1.0") if action != "hold" else Decimal("0.0"),
            reason=f"stub-{action}",
            indicators={"index": index},
            timestamp=context.candles[-1]["open_time"],
        )


@dataclass(slots=True)
class FailingStrategy(Strategy):
    slug: str = "failing"
    default_params: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.default_params is None:
            self.default_params = {}

    def generate_signal(self, context: StrategyContext) -> Signal:
        raise RuntimeError("strategy execution failed")


def build_candles() -> list[dict[str, object]]:
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    closes = [Decimal("10"), Decimal("12"), Decimal("15")]
    return [
        {
            "open_time": start + timedelta(hours=index),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
        }
        for index, close in enumerate(closes)
    ]


async def _create_temp_schema(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    statements = [
        """
        CREATE TEMP TABLE strategies (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            description TEXT,
            module_version TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TEMP TABLE assets (
            id UUID PRIMARY KEY,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            exchange TEXT NOT NULL,
            base_currency TEXT,
            supports_fractional BOOLEAN NOT NULL DEFAULT true,
            min_order_notional NUMERIC,
            qty_step_size NUMERIC,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TEMP TABLE parameter_sets (
            id UUID PRIMARY KEY,
            strategy_id UUID NOT NULL REFERENCES strategies(id),
            label TEXT NOT NULL,
            params JSONB NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TEMP TABLE backtests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            strategy_id UUID NOT NULL REFERENCES strategies(id),
            parameter_set_id UUID NOT NULL REFERENCES parameter_sets(id),
            asset_id UUID NOT NULL REFERENCES assets(id),
            interval TEXT NOT NULL,
            start_time TIMESTAMPTZ NOT NULL,
            end_time TIMESTAMPTZ NOT NULL,
            initial_capital NUMERIC NOT NULL CHECK (initial_capital >= 25),
            fee_bps NUMERIC NOT NULL DEFAULT 10,
            slippage_bps NUMERIC NOT NULL DEFAULT 5,
            status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed')),
            metrics JSONB,
            small_account_warning JSONB,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TEMP TABLE backtest_trades (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            backtest_id UUID NOT NULL REFERENCES backtests(id),
            side TEXT NOT NULL CHECK (side IN ('buy','sell')),
            quantity NUMERIC NOT NULL,
            price NUMERIC NOT NULL,
            executed_at TIMESTAMPTZ NOT NULL,
            reason TEXT
        )
        """,
    ]

    for statement in statements:
        await session.execute(text(statement))

    strategy_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    parameter_set_id = uuid.uuid4()

    await session.execute(
        text(
            "INSERT INTO strategies (id, name, slug, module_version, is_active) VALUES (:id, 'Stub', 'stub', '1.0.0', false)"
        ),
        {"id": strategy_id},
    )
    await session.execute(
        text(
            "INSERT INTO assets (id, symbol, asset_class, exchange, is_active) VALUES (:id, 'BTCUSDT', 'crypto', 'binance_us', true)"
        ),
        {"id": asset_id},
    )
    await session.execute(
        text(
            "INSERT INTO parameter_sets (id, strategy_id, label, params, created_by) VALUES (:id, :strategy_id, 'default', '{}'::jsonb, 'system')"
        ),
        {"id": parameter_set_id, "strategy_id": strategy_id},
    )
    await session.commit()
    return strategy_id, parameter_set_id, asset_id


@pytest.mark.asyncio
async def test_successful_backtest_persistence() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        strategy_id, parameter_set_id, asset_id = await _create_temp_schema(session)

        backtest = await create_backtest_record(
            session,
            strategy_id=strategy_id,
            parameter_set_id=parameter_set_id,
            asset_id=asset_id,
            interval="1h",
            start_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 7, 2, tzinfo=timezone.utc),
            initial_capital=Decimal("100"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            created_by="system",
        )
        await mark_backtest_running(session, backtest.id)

        persisted = await run_backtest_and_persist(
            session,
            backtest_id=backtest.id,
            strategy=StubStrategy(),
            asset_metadata={"symbol": "BTCUSDT", "asset_class": "crypto"},
            candles=build_candles(),
            strategy_parameters={},
        )

        status = await session.scalar(text("SELECT status FROM backtests WHERE id = :id"), {"id": backtest.id})
        trade_count = await session.scalar(text("SELECT COUNT(*) FROM backtest_trades WHERE backtest_id = :id"), {"id": backtest.id})

        assert persisted.status == "completed"
        assert persisted.metrics is not None
        assert persisted.metrics["trade_count"] == 1
        assert len(persisted.trades) == 2
        assert status == "completed"
        assert trade_count == 2

        await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_backtest_persistence_marks_failed() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        strategy_id, parameter_set_id, asset_id = await _create_temp_schema(session)

        backtest = await create_backtest_record(
            session,
            strategy_id=strategy_id,
            parameter_set_id=parameter_set_id,
            asset_id=asset_id,
            interval="1h",
            start_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end_time=datetime(2026, 7, 2, tzinfo=timezone.utc),
            initial_capital=Decimal("100"),
            fee_bps=Decimal("10"),
            slippage_bps=Decimal("5"),
            created_by="system",
        )
        await mark_backtest_running(session, backtest.id)

        persisted = await run_backtest_and_persist(
            session,
            backtest_id=backtest.id,
            strategy=FailingStrategy(),
            asset_metadata={"symbol": "BTCUSDT", "asset_class": "crypto"},
            candles=build_candles(),
            strategy_parameters={},
        )

        status = await session.scalar(text("SELECT status FROM backtests WHERE id = :id"), {"id": backtest.id})
        trade_count = await session.scalar(text("SELECT COUNT(*) FROM backtest_trades WHERE backtest_id = :id"), {"id": backtest.id})
        metrics = await session.scalar(text("SELECT metrics FROM backtests WHERE id = :id"), {"id": backtest.id})

        assert persisted.status == "failed"
        assert persisted.error_detail == "strategy execution failed"
        assert status == "failed"
        assert trade_count == 0
        assert metrics is None

        await session.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_backtest_reproducibility_fields_are_stored() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        strategy_id, parameter_set_id, asset_id = await _create_temp_schema(session)

        start_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end_time = datetime(2026, 7, 2, tzinfo=timezone.utc)
        backtest = await create_backtest_record(
            session,
            strategy_id=strategy_id,
            parameter_set_id=parameter_set_id,
            asset_id=asset_id,
            interval="1h",
            start_time=start_time,
            end_time=end_time,
            initial_capital=Decimal("250"),
            fee_bps=Decimal("15"),
            slippage_bps=Decimal("7"),
            created_by="system",
        )

        row = await session.execute(
            text(
                "SELECT strategy_id, parameter_set_id, asset_id, interval, start_time, end_time, initial_capital, fee_bps, slippage_bps FROM backtests WHERE id = :id"
            ),
            {"id": backtest.id},
        )
        stored = row.one()

        assert stored.strategy_id == strategy_id
        assert stored.parameter_set_id == parameter_set_id
        assert stored.asset_id == asset_id
        assert stored.interval == "1h"
        assert stored.start_time == start_time
        assert stored.end_time == end_time
        assert str(stored.initial_capital) == "250"
        assert str(stored.fee_bps) == "15"
        assert str(stored.slippage_bps) == "7"

        await session.close()
    await engine.dispose()