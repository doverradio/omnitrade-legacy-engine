"""CLI driver for the Strategy #001 evidence run: given one candle CSV per
available timeframe, runs every (timeframe x cost-scenario x capital-policy)
combination, writes one results directory per combination, and writes a
single top-level comparison table.

Usage:
    python3 -m strategy_lab.run_evidence \\
        --candles-1m path/to/btc_1m.csv \\
        --candles-5m path/to/btc_5m.csv \\
        --candles-15m path/to/btc_15m.csv \\
        --candles-1h path/to/btc_1h.csv \\
        --output-dir strategy_lab_results/strategy_001_evidence

Any subset of --candles-1m/-5m/-15m/-1h may be supplied; only the supplied
timeframes are run. At least one is required.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from .candles import load_candles_csv
from .comparison import (
    ALL_CAPITAL_POLICIES,
    ALL_COST_SCENARIOS,
    run_one,
    write_comparison_outputs,
    write_run_outputs,
)

_TIMEFRAME_FLAGS = ["1m", "5m", "15m", "1h"]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    for tf in _TIMEFRAME_FLAGS:
        parser.add_argument(f"--candles-{tf}", default=None, help=f"Path to a {tf} candle CSV")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--initial-capital", type=Decimal, default=Decimal("100"))
    return parser


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    timeframe_paths = {
        tf: getattr(args, f"candles_{tf}") for tf in _TIMEFRAME_FLAGS if getattr(args, f"candles_{tf}")
    }
    if not timeframe_paths:
        raise SystemExit("at least one --candles-<timeframe> must be supplied")

    output_dir = Path(args.output_dir)
    outcomes = []

    for timeframe, path in timeframe_paths.items():
        candles = load_candles_csv(path)
        if not candles:
            print(f"[warn] {timeframe}: no candles loaded from {path}, skipping")
            continue

        for cost_scenario in ALL_COST_SCENARIOS:
            for policy in ALL_CAPITAL_POLICIES:
                outcome = run_one(
                    candles,
                    timeframe=timeframe,
                    cost_scenario=cost_scenario,
                    policy=policy,
                    initial_capital=args.initial_capital,
                )
                run_dir = output_dir / timeframe / cost_scenario.name / policy.name
                write_run_outputs(outcome, run_dir)
                outcomes.append(outcome)
                print(
                    f"[done] {timeframe}/{cost_scenario.name}/{policy.name}: "
                    f"{outcome.metrics.total_trades} trades, "
                    f"TEV={outcome.capital.total_economic_value_final:.2f}, "
                    f"B&H={outcome.buy_and_hold_ending_value:.2f}, "
                    f"beat_bnh={outcome.beat_buy_and_hold}"
                )

    if outcomes:
        write_comparison_outputs(outcomes, output_dir)
        print(f"\nComparison table written to {output_dir}/comparison.{{csv,json,txt}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
