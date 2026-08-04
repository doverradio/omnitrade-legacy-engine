"""Strategy #001 evidence-run harness: runs the same candle history through
multiple execution-cost scenarios and capital-allocation policies, compares
against buy-and-hold over the identical date range, and writes one results
directory per (timeframe, cost scenario, capital policy) plus a single
top-level comparison table.

This module intentionally does not fetch or invent data -- it only consumes
whatever `Candle` sequences it is given (see cli_evidence.py for how those
are loaded, and tools/export_btc_candles_for_strategy_lab.py for how they
are meant to be sourced from OmniTrade's own database).
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Sequence, Union

from .candles import Candle
from .capital import BALANCED, FULL_COMPOUNDING, CapitalPolicy, CapitalSimulationResult, apply_capital_policy
from .config import SimulationConfig
from .costs import CostModel
from .engine import SimulationResult, run_simulation
from .metrics import SimulationMetrics, compute_metrics
from .strategies.trailing_limit_v1 import TrailingLimitV1Strategy
from .strategies.trailing_limit_v2 import TrailingLimitV2Strategy

PathLike = Union[str, Path]
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class CostScenario:
    name: str
    fee_pct: Decimal
    slippage_pct: Decimal
    description: str


# Every fee/slippage assumption used for the authoritative Strategy #001
# evidence run, stated explicitly so none of it is implicit:
#
#   OPTIMISTIC: fee 0.05% (5 bps), slippage 0.01% (1 bp) -- a best-case
#     maker-fee tier with a highly liquid, low-impact fill.
#   EXPECTED:   fee 0.20% (20 bps), slippage 0.05% (5 bps) -- a blended
#     maker/taker approximation of Kraken spot's standard retail fee tier,
#     with a small conservative slippage buffer for a $100-sized order
#     (real slippage at that size on BTC-USD is likely smaller).
#   STRESS:     fee 0.40% (40 bps), slippage 0.25% (25 bps) -- a degraded
#     fee tier plus materially wider effective spread during volatile
#     conditions.
#
# These do not model Kraken's maker/taker split, volume-tier fee schedule,
# or order-book depth explicitly -- they are single blended percentages,
# consistent with V1's "keep the architecture extremely small" constraint.
OPTIMISTIC = CostScenario(
    name="optimistic",
    fee_pct=Decimal("0.0005"),
    slippage_pct=Decimal("0.0001"),
    description="Best-case maker fee tier, highly liquid low-impact fills (5bps fee, 1bp slippage).",
)
EXPECTED = CostScenario(
    name="expected",
    fee_pct=Decimal("0.002"),
    slippage_pct=Decimal("0.0005"),
    description="Blended Kraken spot retail fee tier with a conservative slippage buffer (20bps fee, 5bps slippage).",
)
STRESS = CostScenario(
    name="stress",
    fee_pct=Decimal("0.004"),
    slippage_pct=Decimal("0.0025"),
    description="Degraded fee tier plus wide effective spread under volatility (40bps fee, 25bps slippage).",
)
ALL_COST_SCENARIOS = (OPTIMISTIC, EXPECTED, STRESS)
ALL_CAPITAL_POLICIES = (FULL_COMPOUNDING, BALANCED)

# Strategy #001, Version 1 rules -- fixed for the authoritative evidence
# run per the instruction not to sweep parameters yet.
V1_SIGNAL_PARAMS = dict(
    entry_offset_pct=Decimal("0.01"),
    initial_stop_pct=Decimal("0.01"),
    profit_activation_pct=Decimal("0.03"),
    trailing_distance_pct=Decimal("0.01"),
    required_declining_candles=2,
    intra_candle_ambiguity_policy="pessimistic",
)


@dataclass(frozen=True)
class RunOutcome:
    timeframe: str
    cost_scenario: CostScenario
    policy: CapitalPolicy
    config: SimulationConfig
    result: SimulationResult
    metrics: SimulationMetrics
    capital: CapitalSimulationResult
    buy_and_hold_ending_value: Decimal
    beat_buy_and_hold: bool
    strategy_version: str = "001"


def run_one(
    candles: Sequence[Candle],
    timeframe: str,
    cost_scenario: CostScenario,
    policy: CapitalPolicy,
    initial_capital: Decimal = Decimal("100"),
) -> RunOutcome:
    config = SimulationConfig(
        fee_pct=cost_scenario.fee_pct,
        slippage_pct=cost_scenario.slippage_pct,
        initial_capital=initial_capital,
        candle_interval=timeframe,
        **V1_SIGNAL_PARAMS,
    )
    strategy = TrailingLimitV1Strategy(config=config)
    result = run_simulation(candles, strategy, config, CostModel.from_config(config))
    metrics = compute_metrics(result)
    capital = apply_capital_policy(result.trades, initial_capital, policy)
    bnh_ending_value = buy_and_hold_ending_value(candles, initial_capital, cost_scenario)

    return RunOutcome(
        timeframe=timeframe,
        cost_scenario=cost_scenario,
        policy=policy,
        config=config,
        result=result,
        metrics=metrics,
        capital=capital,
        buy_and_hold_ending_value=bnh_ending_value,
        beat_buy_and_hold=capital.total_economic_value_final > bnh_ending_value,
    )


def run_one_v2(
    candles: Sequence[Candle],
    timeframe: str,
    cost_scenario: CostScenario,
    policy: CapitalPolicy,
    initial_capital: Decimal = Decimal("100"),
) -> RunOutcome:
    config = SimulationConfig(
        fee_pct=cost_scenario.fee_pct,
        slippage_pct=cost_scenario.slippage_pct,
        initial_capital=initial_capital,
        candle_interval=timeframe,
        **V1_SIGNAL_PARAMS,
    )
    strategy = TrailingLimitV2Strategy(config=config)
    result = run_simulation(candles, strategy, config, CostModel.from_config(config))
    metrics = compute_metrics(result)
    capital = apply_capital_policy(result.trades, initial_capital, policy)
    bnh_ending_value = buy_and_hold_ending_value(candles, initial_capital, cost_scenario)

    return RunOutcome(
        timeframe=timeframe,
        cost_scenario=cost_scenario,
        policy=policy,
        config=config,
        result=result,
        metrics=metrics,
        capital=capital,
        buy_and_hold_ending_value=bnh_ending_value,
        beat_buy_and_hold=capital.total_economic_value_final > bnh_ending_value,
        strategy_version="002",
    )


def buy_and_hold_ending_value(
    candles: Sequence[Candle],
    initial_capital: Decimal,
    cost_scenario: CostScenario,
) -> Decimal:
    """Buy at the first candle's open with the same entry fee/slippage
    assumptions as the active strategy, then mark to market at the final
    candle's close. No exit costs are applied, since the position is never
    sold -- this is the conventional buy-and-hold benchmark convention."""
    if not candles:
        return initial_capital
    cost_model = CostModel(fee_pct=cost_scenario.fee_pct, slippage_pct=cost_scenario.slippage_pct)
    entry_price = candles[0].open
    effective_entry_price = cost_model.effective_buy_price(entry_price)
    quantity = (initial_capital * (Decimal("1") - cost_scenario.fee_pct)) / effective_entry_price
    final_price = candles[-1].close
    return quantity * final_price


def write_run_outputs(outcome: RunOutcome, output_dir: PathLike) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_capital_trade_log_csv(outcome.capital.records, output_dir / "trades.csv")
    _write_summary_json(outcome, output_dir / "summary.json")
    (output_dir / "report.txt").write_text(_render_report(outcome), encoding="utf-8")


def _write_capital_trade_log_csv(records, path: PathLike) -> None:
    fieldnames = [
        "entry_candle_index",
        "entry_timestamp",
        "exit_candle_index",
        "exit_timestamp",
        "exit_reason",
        "raw_fill_price",
        "raw_exit_price",
        "gross_return_pct",
        "net_return_pct",
        "holding_candles",
        "trading_capital_before",
        "deployed_notional",
        "realized_pnl",
        "compounded_amount",
        "withdrawn_amount",
        "tax_reserved_amount",
        "trading_capital_after",
        "cumulative_withdrawn_after",
        "cumulative_tax_reserve_after",
        "total_economic_value_after",
    ]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            trade = record.trade
            writer.writerow(
                {
                    "entry_candle_index": trade.entry_candle_index,
                    "entry_timestamp": trade.entry_timestamp.isoformat(),
                    "exit_candle_index": trade.exit_candle_index,
                    "exit_timestamp": trade.exit_timestamp.isoformat(),
                    "exit_reason": trade.exit_reason.value,
                    "raw_fill_price": trade.raw_fill_price,
                    "raw_exit_price": trade.raw_exit_price,
                    "gross_return_pct": trade.gross_return_pct,
                    "net_return_pct": trade.net_return_pct,
                    "holding_candles": trade.holding_candles,
                    "trading_capital_before": record.trading_capital_before,
                    "deployed_notional": record.deployed_notional,
                    "realized_pnl": record.realized_pnl,
                    "compounded_amount": record.compounded_amount,
                    "withdrawn_amount": record.withdrawn_amount,
                    "tax_reserved_amount": record.tax_reserved_amount,
                    "trading_capital_after": record.trading_capital_after,
                    "cumulative_withdrawn_after": record.cumulative_withdrawn_after,
                    "cumulative_tax_reserve_after": record.cumulative_tax_reserve_after,
                    "total_economic_value_after": record.total_economic_value_after,
                }
            )


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _write_summary_json(outcome: RunOutcome, path: PathLike) -> None:
    summary = {
    "strategy_version": outcome.strategy_version,
        "timeframe": outcome.timeframe,
        "cost_scenario": asdict(outcome.cost_scenario),
        "capital_policy": asdict(outcome.policy),
        "config": asdict(outcome.config),
        "candle_count": len(outcome.result.equity_curve),
        "metrics": asdict(outcome.metrics),
        "event_counts": {
            "initial_stop_exits": outcome.result.initial_stop_exits,
            "declining_close_exits": outcome.result.declining_close_exits,
            "trailing_exits": outcome.result.trailing_exits,
            "profit_mode_activations": outcome.result.profit_mode_activations,
        },
        "capital": {
            "initial_capital": outcome.capital.initial_capital,
            "trading_capital_final": outcome.capital.trading_capital_final,
            "cumulative_withdrawn_final": outcome.capital.cumulative_withdrawn_final,
            "cumulative_tax_reserve_final": outcome.capital.cumulative_tax_reserve_final,
            "total_economic_value_final": outcome.capital.total_economic_value_final,
            "next_trade_deployed_notional": outcome.capital.next_trade_deployed_notional,
            "raw_strategy_net_return_pct": outcome.capital.raw_strategy_net_return_pct,
        },
        "buy_and_hold_ending_value": outcome.buy_and_hold_ending_value,
        "beat_buy_and_hold": outcome.beat_buy_and_hold,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, default=_json_default)
        handle.write("\n")


def _render_report(outcome: RunOutcome) -> str:
    m = outcome.metrics
    c = outcome.capital

    def fmt(value, suffix=""):
        return "n/a" if value is None else f"{value}{suffix}"

    lines = [
        f"Strategy #{outcome.strategy_version} Evidence Run",
        "===========================",
        f"Timeframe:               {outcome.timeframe}",
        f"Cost scenario:           {outcome.cost_scenario.name} -- {outcome.cost_scenario.description}",
        f"Capital policy:          {outcome.policy.name} "
        f"(deploy {outcome.policy.trade_deployment_pct}%, compound {outcome.policy.profit_compound_pct}%, "
        f"withdraw {outcome.policy.profit_withdrawal_pct}%, tax reserve {outcome.policy.profit_tax_reserve_pct}%)",
        f"Candle count:            {len(outcome.result.equity_curve)}",
        "",
        "Strategy performance (signal-level, capital-policy independent)",
        "-----------------------------------------------------------------",
        f"Total Trades:            {m.total_trades}",
        f"Win Rate:                {fmt(m.win_rate_pct, '%')}",
        f"Average Holding:         {fmt(m.average_holding_candles, ' candles')}",
        f"Initial Stop Exits:      {outcome.result.initial_stop_exits}",
        f"Declining-Close Exits:   {outcome.result.declining_close_exits}",
        f"Trailing Exits:          {outcome.result.trailing_exits}",
        f"Profit Activations:      {outcome.result.profit_mode_activations}",
        f"Gross Return:            {m.gross_return_pct:.4f}%",
        f"Net Return (full compounding): {m.net_return_pct:.4f}%",
        f"Fees:                    {m.fees_paid}",
        f"Slippage:                {m.estimated_slippage}",
        f"Raw strategy net return (always full compounding, for comparison): {c.raw_strategy_net_return_pct:.4f}%",
        f"Maximum Drawdown:        {m.max_drawdown_pct:.4f}%",
        f"Profit Factor:           {fmt(m.profit_factor)}",
        f"Longest Losing Streak:   {m.max_consecutive_losses}",
        f"Fee Drag (gross - net):  {(m.gross_return_pct - m.net_return_pct):.4f} percentage points",
        "",
        "Capital allocation outcome (this policy)",
        "-----------------------------------------------------------------",
        f"Initial capital:         {c.initial_capital}",
        f"Ending trading capital:  {c.trading_capital_final}",
        f"Cumulative withdrawn:    {c.cumulative_withdrawn_final}",
        f"Cumulative tax reserve:  {c.cumulative_tax_reserve_final}",
        f"Total economic value:    {c.total_economic_value_final}",
        f"Next-trade deployed notional (projected): {c.next_trade_deployed_notional}",
        "",
        "Benchmark",
        "-----------------------------------------------------------------",
        f"Buy-and-hold ending value: {outcome.buy_and_hold_ending_value}",
        f"Beat buy-and-hold:          {outcome.beat_buy_and_hold}",
        "",
    ]
    return "\n".join(lines)


def build_comparison_rows(outcomes: List[RunOutcome]) -> List[Dict[str, object]]:
    rows = []
    for o in outcomes:
        m = o.metrics
        c = o.capital
        rows.append(
            {
                "strategy_version": o.strategy_version,
                "timeframe": o.timeframe,
                "cost_scenario": o.cost_scenario.name,
                "capital_policy": o.policy.name,
                "candle_count": len(o.result.equity_curve),
                "trades": m.total_trades,
                "win_rate_pct": m.win_rate_pct,
                "average_holding_candles": m.average_holding_candles,
                "initial_stop_exits": o.result.initial_stop_exits,
                "declining_close_exits": o.result.declining_close_exits,
                "trailing_exits": o.result.trailing_exits,
                "profit_mode_activations": o.result.profit_mode_activations,
                "gross_return_pct": m.gross_return_pct,
                "net_return_pct": m.net_return_pct,
                "fees": m.fees_paid,
                "slippage": m.estimated_slippage,
                "raw_strategy_net_return_pct": c.raw_strategy_net_return_pct,
                "ending_trading_capital": c.trading_capital_final,
                "withdrawn_profit": c.cumulative_withdrawn_final,
                "tax_reserve": c.cumulative_tax_reserve_final,
                "total_economic_value": c.total_economic_value_final,
                "max_drawdown_pct": m.max_drawdown_pct,
                "profit_factor": m.profit_factor,
                "longest_losing_streak": m.max_consecutive_losses,
                "fee_drag_pct": (m.gross_return_pct - m.net_return_pct),
                "buy_and_hold_ending_value": o.buy_and_hold_ending_value,
                "beat_buy_and_hold": o.beat_buy_and_hold,
            }
        )
    return rows


def write_comparison_outputs(outcomes: List[RunOutcome], output_dir: PathLike) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = build_comparison_rows(outcomes)

    fieldnames = list(rows[0].keys()) if rows else []
    with open(output_dir / "comparison.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with open(output_dir / "comparison.json", "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, default=_json_default)
        handle.write("\n")

    versions = sorted({outcome.strategy_version for outcome in outcomes})
    title = f"Strategy #{versions[0]} Evidence Run" if len(versions) == 1 else "Strategy Evidence Run"
    lines = [f"{title} -- Comparison Table", "=" * 47, ""]
    header = (
        f"{'timeframe':>9} {'cost':>10} {'policy':>15} {'candles':>8} {'trades':>7} "
        f"{'win%':>7} {'gross%':>9} {'net%':>9} {'endTEV':>10} {'maxDD%':>7} {'PF':>6} "
        f"{'losingStreak':>12} {'B&H':>10} {'beatB&H':>8}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        lines.append(
            f"{row['timeframe']:>9} {row['cost_scenario']:>10} {row['capital_policy']:>15} "
            f"{row['candle_count']:>8} {row['trades']:>7} "
            f"{_fmt2(row['win_rate_pct']):>7} {_fmt2(row['gross_return_pct']):>9} "
            f"{_fmt2(row['net_return_pct']):>9} {_fmt2(row['total_economic_value']):>10} "
            f"{_fmt2(row['max_drawdown_pct']):>7} {_fmt2(row['profit_factor']):>6} "
            f"{row['longest_losing_streak']:>12} {_fmt2(row['buy_and_hold_ending_value']):>10} "
            f"{str(row['beat_buy_and_hold']):>8}"
        )
    (output_dir / "comparison.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt2(value) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{Decimal(value):.2f}"
    except Exception:
        return str(value)
