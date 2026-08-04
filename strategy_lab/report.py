"""Output writers: CSV trade log, JSON summary, human-readable report."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Union

from .config import SimulationConfig
from .engine import SimulationResult
from .metrics import SimulationMetrics

PathLike = Union[str, Path]


def write_trade_log_csv(trades, path: PathLike) -> None:
    fieldnames = [
        "entry_candle_index",
        "entry_timestamp",
        "entry_order_price",
        "raw_fill_price",
        "effective_entry_price",
        "entry_fee",
        "entry_slippage_cost",
        "exit_candle_index",
        "exit_timestamp",
        "raw_exit_price",
        "effective_exit_price",
        "exit_fee",
        "exit_slippage_cost",
        "exit_reason",
        "quantity",
        "equity_before",
        "equity_after",
        "gross_return_pct",
        "net_return_pct",
        "highest_price_during_trade",
        "lowest_price_during_trade",
        "holding_candles",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trade in trades:
            row = asdict(trade)
            row["entry_timestamp"] = trade.entry_timestamp.isoformat()
            row["exit_timestamp"] = trade.exit_timestamp.isoformat()
            row["exit_reason"] = trade.exit_reason.value
            writer.writerow(row)


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):  # Enum
        return value.value
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def build_summary_dict(
    result: SimulationResult,
    metrics: SimulationMetrics,
    config: SimulationConfig,
) -> Dict[str, Any]:
    return {
        "config": asdict(config),
        "initial_capital": result.initial_capital,
        "final_equity": result.final_equity,
        "ended_in_position": result.ended_in_position,
        "metrics": asdict(metrics),
    }


def write_summary_json(
    result: SimulationResult,
    metrics: SimulationMetrics,
    config: SimulationConfig,
    path: PathLike,
) -> None:
    summary = build_summary_dict(result, metrics, config)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=_json_default)
        handle.write("\n")


def render_human_readable_report(
    result: SimulationResult,
    metrics: SimulationMetrics,
    config: SimulationConfig,
) -> str:
    def fmt(value, suffix: str = "") -> str:
        return "n/a" if value is None else f"{value}{suffix}"

    def fmt_ratio(value) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    lines = [
        "Strategy Laboratory -- Simulation Report",
        "=" * 41,
        f"Initial Capital:        {result.initial_capital}",
        f"Final Equity:           {result.final_equity}",
        f"Ended In Position:      {result.ended_in_position}",
        "",
        "Trade Counts",
        "-" * 41,
        f"Total Trades:           {metrics.total_trades}",
        f"Winning Trades:         {metrics.winning_trades}",
        f"Losing Trades:          {metrics.losing_trades}",
        f"Win Rate:               {fmt(metrics.win_rate_pct, '%')}",
        "",
        "Returns",
        "-" * 41,
        f"Gross Return:           {metrics.gross_return_pct:.4f}%",
        f"Net Return:             {metrics.net_return_pct:.4f}%",
        f"Fees Paid:              {metrics.fees_paid}",
        f"Estimated Slippage:     {metrics.estimated_slippage}",
        f"Average Trade:          {fmt(metrics.average_trade_pct, '%')}",
        f"Average Holding Time:   {fmt(metrics.average_holding_candles, ' candles')}",
        "",
        "Risk",
        "-" * 41,
        f"Maximum Drawdown:       {metrics.max_drawdown_pct:.4f}%",
        f"Max Consecutive Losses: {metrics.max_consecutive_losses}",
        f"Largest Winner:         {fmt(metrics.largest_winner_pct, '%')}",
        f"Largest Loser:          {fmt(metrics.largest_loser_pct, '%')}",
        f"Avg Favorable Excursion:{fmt(metrics.average_mfe_pct, '%')}",
        f"Avg Adverse Excursion:  {fmt(metrics.average_mae_pct, '%')}",
        f"Profit Factor:          {fmt(metrics.profit_factor)}",
        f"Sharpe Ratio (/trade):  {fmt_ratio(metrics.sharpe_ratio_per_trade)}",
        f"Sortino Ratio (/trade): {fmt_ratio(metrics.sortino_ratio_per_trade)}",
        "",
        "Configuration",
        "-" * 41,
    ]
    for key, value in asdict(config).items():
        lines.append(f"{key}: {value}")
    lines.append("")
    return "\n".join(lines)


def write_human_readable_report(
    result: SimulationResult,
    metrics: SimulationMetrics,
    config: SimulationConfig,
    path: PathLike,
) -> None:
    text = render_human_readable_report(result, metrics, config)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
