from __future__ import annotations

from decimal import Decimal

from app.core.errors import InvalidRequestError
from app.schemas.strategy_lab_offline import PatternIntelligenceRequest, PatternIntelligenceTradeRequest, StrategyLabReplayRequest
from app.services.strategy_lab_offline import load_dataset, run_replay

from strategy_lab.pattern_intelligence import AnalysisContext, analyze
from strategy_lab.pattern_intelligence.models import json_value


def analyze_selection(payload: PatternIntelligenceRequest) -> dict[str, object]:
    candles = load_dataset(payload.dataset_id)
    end = len(candles) - 1 if payload.selected_end_index is None else payload.selected_end_index
    if payload.selected_start_index > end or end >= len(candles):
        raise InvalidRequestError(message="Pattern Intelligence selection is outside the dataset")
    replay = _replay(payload.dataset_id, payload.strategy_version, payload.parameters)
    context = _context(payload.dataset_id, payload.strategy_version, payload.partition, payload.selected_start_index, end, replay)
    return json_value(analyze(candles, context))


def analyze_visible_window(payload: PatternIntelligenceRequest) -> dict[str, object]:
    return analyze_selection(payload)


def analyze_trade(payload: PatternIntelligenceTradeRequest) -> dict[str, object]:
    candles = load_dataset(payload.dataset_id)
    replay = _replay(payload.dataset_id, payload.strategy_version, payload.parameters)
    trades = replay["trades"]
    if payload.trade_index >= len(trades):
        raise InvalidRequestError(message="Completed trade index is outside the replay")
    trade = trades[payload.trade_index]
    start = int(trade["entry_candle_index"])
    end = int(trade["exit_candle_index"])
    context = _context(payload.dataset_id, payload.strategy_version, payload.partition, start, end, replay, trade)
    return json_value(analyze(candles, context))


def _replay(dataset_id: str, strategy_version: str, parameters) -> dict[str, object]:
    return run_replay(StrategyLabReplayRequest(
        dataset_id=dataset_id,
        strategy_version=strategy_version,
        research_period="entire_dataset",
        parameters=parameters,
    ))


def _context(
    dataset_id: str,
    strategy_version: str,
    partition: str,
    start: int,
    end: int,
    replay: dict[str, object],
    selected_trade: dict[str, object] | None = None,
) -> AnalysisContext:
    metadata = replay["dataset"]
    metrics = replay["metrics"]
    return AnalysisContext(
        dataset_id=dataset_id,
        asset=str(metadata["asset"]), exchange=str(metadata["exchange"]), interval=str(metadata["interval"]),
        strategy_version=strategy_version, selected_start_index=start, selected_end_index=end, partition=partition,
        replay_events=tuple(replay["events"]), trades=tuple(replay["trades"]), selected_trade=selected_trade,
        equity_curve=tuple(Decimal(str(item["equity"])) for item in replay["equity_curve"]),
        buy_hold_return_pct=Decimal(str(metrics["buy_and_hold_return_pct"])),
        strategy_return_pct=Decimal(str(metrics["net_return_pct"])),
    )