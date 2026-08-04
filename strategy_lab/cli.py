"""Command-line entrypoint.

Usage:
    python3 -m strategy_lab.cli --candles path/to/btc_candles.csv --output-dir out/

All strategy parameters are optional overrides of SimulationConfig's
defaults; run with --help for the full list.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from .candles import load_candles_csv
from .config import SimulationConfig
from .costs import CostModel
from .engine import run_simulation
from .metrics import compute_metrics
from .report import (
    render_human_readable_report,
    write_summary_json,
    write_trade_log_csv,
)
from .strategies.trailing_limit_v1 import TrailingLimitV1Strategy


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strategy Laboratory V1 offline simulator")
    parser.add_argument("--candles", required=True, help="Path to a candle CSV (timestamp,open,high,low,close,volume)")
    parser.add_argument("--output-dir", required=True, help="Directory to write trades.csv, summary.json, report.txt")
    parser.add_argument("--entry-offset-pct", type=Decimal, default=SimulationConfig.entry_offset_pct)
    parser.add_argument("--initial-stop-pct", type=Decimal, default=SimulationConfig.initial_stop_pct)
    parser.add_argument("--profit-activation-pct", type=Decimal, default=SimulationConfig.profit_activation_pct)
    parser.add_argument("--trailing-distance-pct", type=Decimal, default=SimulationConfig.trailing_distance_pct)
    parser.add_argument("--required-declining-candles", type=int, default=SimulationConfig.required_declining_candles)
    parser.add_argument("--fee-pct", type=Decimal, default=SimulationConfig.fee_pct)
    parser.add_argument("--slippage-pct", type=Decimal, default=SimulationConfig.slippage_pct)
    parser.add_argument("--initial-capital", type=Decimal, default=SimulationConfig.initial_capital)
    parser.add_argument("--candle-interval", default=SimulationConfig.candle_interval)
    return parser


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    config = SimulationConfig(
        entry_offset_pct=args.entry_offset_pct,
        initial_stop_pct=args.initial_stop_pct,
        profit_activation_pct=args.profit_activation_pct,
        trailing_distance_pct=args.trailing_distance_pct,
        required_declining_candles=args.required_declining_candles,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
        initial_capital=args.initial_capital,
        candle_interval=args.candle_interval,
    )

    candles = load_candles_csv(args.candles)
    if not candles:
        raise SystemExit("no candles loaded")

    strategy = TrailingLimitV1Strategy(config=config)
    result = run_simulation(candles, strategy, config, CostModel.from_config(config))
    metrics = compute_metrics(result)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_trade_log_csv(result.trades, output_dir / "trades.csv")
    write_summary_json(result, metrics, config, output_dir / "summary.json")
    report_text = render_human_readable_report(result, metrics, config)
    (output_dir / "report.txt").write_text(report_text, encoding="utf-8")

    print(report_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
