from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.schemas.arena import StrategyArenaScoreboardItem, StrategyArenaScoreboardResponse
from app.schemas.replay_agent import ReplayAgentCapabilityResponse, ReplayAgentRegistrationResponse
from app.services.replay.registry import list_registered_replay_agents

router = APIRouter(prefix="/arena", tags=["arena"])


@router.get("/replay-agents", response_model=list[ReplayAgentRegistrationResponse])
async def get_replay_agents() -> list[ReplayAgentRegistrationResponse]:
    return [
        ReplayAgentRegistrationResponse(
            replay_agent_id=item.replay_agent_id,
            name=item.name,
            status=item.status,
            capabilities=[
                ReplayAgentCapabilityResponse(name=capability.name, description=capability.description)
                for capability in item.capabilities
            ],
            decision_package_consumer=item.decision_package_consumer,
            execution_logic=item.execution_logic,
            processing_enabled=item.processing_enabled,
            scheduling_enabled=item.scheduling_enabled,
            writes_enabled=item.writes_enabled,
        )
        for item in list_registered_replay_agents()
    ]


@router.get("/strategy-scoreboard", response_model=StrategyArenaScoreboardResponse)
async def get_strategy_scoreboard(db: AsyncSession = Depends(get_db)) -> StrategyArenaScoreboardResponse:
    strategies = (
        await db.execute(select(Strategy).order_by(Strategy.is_active.desc(), Strategy.name.asc(), Strategy.created_at.asc()))
    ).scalars().all()

    if not strategies:
        return StrategyArenaScoreboardResponse(items=[])

    strategy_ids = [strategy.id for strategy in strategies]
    signals = (
        await db.execute(
            select(Signal)
            .where(Signal.strategy_id.in_(strategy_ids))
            .order_by(Signal.strategy_id.asc(), Signal.signal_time.asc(), Signal.id.asc())
        )
    ).scalars().all()
    trades = (
        await db.execute(
            select(Trade)
            .where(Trade.is_paper.is_(True))
            .where(Trade.signal_id.is_not(None))
            .order_by(Trade.executed_at.asc(), Trade.id.asc())
        )
    ).scalars().all()
    decision_records = (await db.execute(select(DecisionRecord).order_by(DecisionRecord.timestamp.asc()))).scalars().all()

    signals_by_strategy: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    for signal in signals:
        signals_by_strategy[signal.strategy_id].append(signal)

    signal_ids_by_strategy: dict[uuid.UUID, set[uuid.UUID]] = {
        strategy_id: {signal.id for signal in strategy_signals}
        for strategy_id, strategy_signals in signals_by_strategy.items()
    }

    latest_prices_by_asset_id = await _load_latest_prices_by_asset_id(
        db=db,
        asset_ids=sorted({trade.asset_id for trade in trades}, key=str),
    )

    items: list[StrategyArenaScoreboardItem] = []
    for strategy in strategies:
        strategy_signals = signals_by_strategy.get(strategy.id, [])
        strategy_signal_ids = signal_ids_by_strategy.get(strategy.id, set())
        strategy_trades = [trade for trade in trades if trade.signal_id in strategy_signal_ids]
        strategy_decision_records = [
            record
            for record in decision_records
            if _decision_record_matches_strategy(record, strategy, strategy_signal_ids)
        ]

        trade_snapshot = _compute_strategy_trade_snapshot(
            strategy_trades=strategy_trades,
            latest_prices_by_asset_id=latest_prices_by_asset_id,
        )

        action_counts = Counter(signal.action for signal in strategy_signals)
        items.append(
            StrategyArenaScoreboardItem(
                strategy_id=strategy.id,
                strategy_name=strategy.name,
                enabled=strategy.is_active,
                status="active" if strategy.is_active else "disabled",
                signals_generated=len(strategy_signals),
                buy_signals=action_counts.get("buy", 0),
                sell_signals=action_counts.get("sell", 0),
                hold_signals=action_counts.get("hold", 0),
                paper_trades=len(strategy_trades),
                open_positions=trade_snapshot["open_positions"],
                realized_pnl=trade_snapshot["realized_pnl"],
                unrealized_pnl=trade_snapshot["unrealized_pnl"],
                total_return_pct=trade_snapshot["total_return_pct"],
                decision_records=len(strategy_decision_records),
                last_signal_timestamp=max((signal.signal_time for signal in strategy_signals), default=None),
                last_trade_timestamp=max((trade.executed_at for trade in strategy_trades), default=None),
            )
        )

    return StrategyArenaScoreboardResponse(items=items)


async def _load_latest_prices_by_asset_id(*, db: AsyncSession, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, Decimal]:
    latest_prices_by_asset_id: dict[uuid.UUID, Decimal] = {}

    for asset_id in asset_ids:
        latest_close = await db.scalar(
            select(Candle.close)
            .where(Candle.asset_id == asset_id)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        if isinstance(latest_close, Decimal):
            latest_prices_by_asset_id[asset_id] = latest_close

    return latest_prices_by_asset_id


def _compute_strategy_trade_snapshot(
    *,
    strategy_trades: list[Trade],
    latest_prices_by_asset_id: dict[uuid.UUID, Decimal],
) -> dict[str, Decimal | int]:
    positions: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    realized_pnl = Decimal("0")
    deployed_capital = Decimal("0")

    for trade in sorted(strategy_trades, key=lambda item: (item.executed_at, item.id)):
        quantity = Decimal(str(trade.quantity))
        price = Decimal(str(trade.price))
        fee = Decimal(str(trade.fee))
        current_qty, current_avg = positions.get(trade.asset_id, (Decimal("0"), Decimal("0")))

        if trade.side == "buy":
            total_cost = (current_qty * current_avg) + (quantity * price) + fee
            next_qty = current_qty + quantity
            next_avg = total_cost / next_qty if next_qty > 0 else Decimal("0")
            positions[trade.asset_id] = (next_qty, next_avg)
            deployed_capital += (quantity * price) + fee
            continue

        if trade.side == "sell":
            sell_qty = min(current_qty, quantity)
            realized_pnl += (sell_qty * price) - (sell_qty * current_avg) - fee
            remaining_qty = current_qty - sell_qty
            if remaining_qty <= 0:
                positions[trade.asset_id] = (Decimal("0"), Decimal("0"))
            else:
                positions[trade.asset_id] = (remaining_qty, current_avg)

    unrealized_pnl = Decimal("0")
    open_positions = 0
    for asset_id, (quantity, avg_entry_price) in positions.items():
        if quantity <= 0:
            continue
        open_positions += 1
        mark_price = latest_prices_by_asset_id.get(asset_id, avg_entry_price)
        unrealized_pnl += (mark_price - avg_entry_price) * quantity

    equity = realized_pnl + unrealized_pnl
    total_return_pct = Decimal("0")
    if deployed_capital > 0:
        total_return_pct = equity / deployed_capital

    return {
        "open_positions": open_positions,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_return_pct": total_return_pct,
    }


def _decision_record_matches_strategy(
    decision_record: DecisionRecord,
    strategy: Strategy,
    strategy_signal_ids: set[uuid.UUID],
) -> bool:
    strategy_identifiers = {
        str(strategy.id),
        strategy.name,
        strategy.slug,
    }
    strategy_signal_id_strings = {str(value) for value in strategy_signal_ids}

    for item in decision_record.generated_signals or []:
        signal_id = item.get("signal_id")
        if signal_id and signal_id in strategy_signal_id_strings:
            return True

    for item in decision_record.supporting_strategies or []:
        if _dict_matches_strategy_identifiers(item, strategy_identifiers):
            return True

    for item in decision_record.opposing_strategies or []:
        if _dict_matches_strategy_identifiers(item, strategy_identifiers):
            return True

    return False


def _dict_matches_strategy_identifiers(value: dict[str, Any], strategy_identifiers: set[str]) -> bool:
    candidate_values = [
        value.get("strategy_id"),
        value.get("strategyId"),
        value.get("id"),
        value.get("strategy_name"),
        value.get("name"),
        value.get("slug"),
    ]
    return any(str(candidate).strip() in strategy_identifiers for candidate in candidate_values if candidate is not None)
