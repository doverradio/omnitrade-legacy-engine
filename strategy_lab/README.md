# Strategy Laboratory

An offline, deterministic backtesting simulator. It answers one question:

> Would a trading strategy have actually made money over historical market
> data, after realistic execution costs?

This is deliberately disconnected from the live OmniTrade production system
(`apps/api`). No networking, no exchange integration, no machine learning.
Pure Python standard library.

## Layout

- `candles.py` — `Candle` + CSV loader. Enforces strictly increasing
  timestamps and valid OHLC ranges.
- `config.py` — `SimulationConfig`: every tunable parameter (entry offset,
  initial stop, profit activation, trailing distance, required declining
  candles, fee/slippage percentages, initial capital, candle interval).
- `costs.py` — `CostModel`: the fee and slippage model.
- `strategy.py` — shared types (`LimitOrder`, `PositionState`, `TradeRecord`,
  `ExitReason`, ...) and the `Strategy` protocol that future strategy
  versions implement to plug into the same engine.
- `engine.py` — `run_simulation`: the deterministic candle-replay loop. Owns
  limit-order fill mechanics and no-look-ahead enforcement. See its module
  docstring for the exact, documented fill/exit-ordering policy. This file
  should not need to change when a new strategy version is added.
- `strategies/trailing_limit_v1.py` — `TrailingLimitV1Strategy`: Strategy
  Laboratory Version 1 (continuously-replaced BUY LIMIT entry; initial stop
  → profit-activation → trailing-floor exit; declining-closes exit).
- `metrics.py` — `compute_metrics`: win rate, gross/net return, fees,
  slippage, drawdown, consecutive losses, MFE/MAE, profit factor,
  Sharpe/Sortino (per-trade, non-annualized).
- `capital.py` — `CapitalPolicy` / `apply_capital_policy`: capital
  allocation (how much of trading capital to deploy per trade, and how
  realized profit splits between compounding, withdrawal, and a tax
  reserve) as a separate post-processing pass over `engine.py`'s trades, so
  allocation choices never obscure the underlying strategy's own edge. See
  its module docstring.
- `report.py` — CSV trade log, JSON summary, human-readable text report
  (single-run, capital-policy-free view; used by `cli.py`).
- `comparison.py` / `run_evidence.py` — the multi-timeframe, multi-cost-
  scenario, multi-capital-policy evidence-run harness, plus a buy-and-hold
  benchmark and comparison-table writer. See `run_evidence.py`'s module
  docstring for usage.
- `cli.py` — single-run command-line entrypoint (one timeframe, one cost
  config, full-compounding capital view only).
- `tests/` — deterministic unit tests using handcrafted candle sequences.

## Known limitations

- **Lower-timeframe intra-candle resolution is not implemented.** The
  `intra_candle_ambiguity_policy` config (`pessimistic` by default,
  `optimistic` as a named alternative) resolves same-candle ordering
  ambiguity by *assumption* — it does not yet consult finer-grained stored
  candle data to determine what actually happened intra-candle, even where
  that data exists. This is a known, documented gap, not a silent one.
- No position-sizing model beyond the capital policies in `capital.py`.
- Sharpe/Sortino are simple per-trade (non-annualized) ratios.

## Usage

```bash
python3 -m strategy_lab.cli \
  --candles path/to/btc_candles.csv \
  --output-dir out/
```

`btc_candles.csv` must have a header: `timestamp,open,high,low,close,volume`,
rows in strictly increasing timestamp order. Run `python3 -m strategy_lab.cli
--help` for the full list of configurable parameters.

## Tests

```bash
python3 -m pytest strategy_lab/tests -q
```

## Adding a new strategy version

Implement the `Strategy` protocol in `strategy.py` (four methods:
`propose_entry_price`, `open_position`, `check_exit`,
`update_position_state`) in a new file under `strategies/`, then pass an
instance of it to `engine.run_simulation`. The engine, cost model, metrics,
and report writers are all strategy-agnostic and require no changes.
