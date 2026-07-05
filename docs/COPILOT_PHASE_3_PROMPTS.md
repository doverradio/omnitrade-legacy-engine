# OmniTrade Legacy Engine — GitHub Copilot Prompts: Phase 3 Only

**Scope:** Backtesting only.

Phase 3 builds the historical research engine that objectively evaluates strategies against stored candle data.

Do **not** implement:

- Strategy Lab UI beyond what is explicitly needed for Backtests
- Paper trading
- Live trading
- AI layer
- Risk engine
- Signal generation loop
- Decision Intelligence Engine
- Counterfactual Outcome Ledger
- Decision Quality Engine
- Optimization engine
- Auto-activation of strategies

Backtesting is evidence generation, not execution.

Run these prompts **in order**, one at a time, reviewing, validating, and committing after each.

Before every prompt, check whether the implementation creates or changes an architectural decision. If it does, stop and ask whether an ADR is required before writing code.

---

## Prompt 3.1 — Backtesting Database Models + Migration

```text
Read:

- docs/PROJECT_STATUS.md
- docs/PROJECT_CONSTITUTION.md
- docs/DATABASE_SCHEMA.md sections 2.3, 2.4, 2.5, and 2.6
- docs/API_CONTRACTS.md backtest endpoints
- docs/SMALL_ACCOUNT_MODE.md
- docs/BACKEND_MODULE_SPECS.md
- docs/MVP_BUILD_PLAN.md Phase 3

Implement Phase 3 database support only.

Create SQLAlchemy ORM models for:

- strategies
- parameter_sets
- backtests
- backtest_trades

Match docs/DATABASE_SCHEMA.md exactly.

Requirements:

- UUID primary keys where documented
- correct foreign keys
- correct CHECK constraints
- numeric fields use numeric/Decimal-compatible database types
- backtests.initial_capital enforces minimum 25
- backtests.status supports pending, running, completed, failed
- metrics stored as JSONB
- small_account_warning stored as nullable JSONB

Generate an Alembic migration.

Do not implement strategy logic yet.

Do not implement backtest engine yet.

Do not implement API endpoints yet.

Validation:

- Run Alembic upgrade locally
- Run backend tests
- Add model/migration tests if project convention supports it

Report:

- Files created
- Files modified
- Migration name
- Commands run
- Test results
- Whether an ADR is required
```

---

## Prompt 3.2 — Strategy Interface + Registry

```text
Read:

- docs/PROJECT_STATUS.md
- docs/PROJECT_CONSTITUTION.md
- docs/STRATEGY_ENGINE.md section 1
- docs/BACKEND_MODULE_SPECS.md app/services/strategies section
- docs/MVP_BUILD_PLAN.md Phase 3
- docs/SMALL_ACCOUNT_MODE.md section 9

Implement the shared strategy interface only.

Create:

- Strategy protocol/interface
- Signal dataclass or Pydantic model for strategy outputs
- StrategyContext read-only input object
- strategy registry mapping slug to implementation
- parameter validation helpers

Requirements:

- Strategies are pure functions
- No DB access inside strategy implementations
- No network access inside strategies
- Every signal must include:
  - action: buy, sell, or hold
  - strength: 0.0 to 1.0
  - reason: non-empty string
  - indicators: dict
- Registry should be extensible for the six MVP strategies

Do not implement actual strategy modules yet except placeholders if needed for typing tests.

Do not implement backtesting engine.

Do not implement paper trading.

Validation:

- Unit tests for interface validation
- Unit tests for registry behavior

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.3 — Moving Average Crossover Strategy

```text
Read:

- docs/PROJECT_STATUS.md
- docs/STRATEGY_ENGINE.md section 2.1
- docs/SMALL_ACCOUNT_MODE.md section 9
- docs/BACKEND_MODULE_SPECS.md app/services/strategies section

Implement only the Moving Average Crossover strategy.

Strategy slug:

ma_crossover

Default params:

{
  "fast_period": 10,
  "slow_period": 50,
  "ma_type": "sma"
}

Logic:

- Buy when fast MA crosses above slow MA
- Sell when fast MA crosses below slow MA
- Hold otherwise

Indicators logged:

- fast_ma
- slow_ma
- prior_fast_ma
- prior_slow_ma

Requirements:

- Pure function
- No DB access
- No network access
- No trading execution
- Non-empty reason for every signal
- Strength between 0 and 1
- Gracefully handle insufficient candle history by returning hold with reason

Write unit tests using synthetic candle data for:

- buy crossover
- sell crossover
- hold
- insufficient history
- invalid params

Do not implement other strategies.

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.4 — Remaining MVP Strategy Modules

```text
Read:

- docs/PROJECT_STATUS.md
- docs/STRATEGY_ENGINE.md section 2
- docs/SMALL_ACCOUNT_MODE.md section 9
- docs/BACKEND_MODULE_SPECS.md app/services/strategies section

Implement the remaining documented MVP strategy/filter modules:

- rsi_mean_reversion
- breakout
- volatility_filter
- trend_regime_filter
- ensemble_scorer

Follow docs/STRATEGY_ENGINE.md exactly.

Requirements:

- Pure functions only
- No DB access
- No network access
- No execution logic
- Every signal includes action, strength, reason, indicators
- Filter modules must be callable by strategies/ensemble but must not place trades
- Ensemble scorer combines signals but does not bypass future AI/risk phases

Write unit tests for each module using synthetic candle data.

Do not implement AI.

Do not implement Risk Engine.

Do not implement Paper Trading.

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.5 — Event-Driven Backtesting Engine

```text
Read:

- docs/PROJECT_STATUS.md
- docs/PROJECT_CONSTITUTION.md
- docs/SYSTEM_ARCHITECTURE.md section 2.4
- docs/BACKEND_MODULE_SPECS.md app/services/backtesting section
- docs/STRATEGY_ENGINE.md
- docs/DATABASE_SCHEMA.md sections 2.5 and 2.6
- docs/SMALL_ACCOUNT_MODE.md
- docs/MVP_BUILD_PLAN.md Phase 3

Implement the event-driven backtesting engine.

Create:

- app/services/backtesting/engine.py
- any supporting internal types needed

Engine responsibilities:

- Load candles already provided by caller or query layer
- Iterate candles in chronological order
- Call selected strategy through the shared interface
- Simulate position entry and exit
- Track cash, position quantity, equity, and trades
- Produce a completed backtest result object
- Never use future candle data when generating a signal
- Never call paper trading services
- Never call AI services
- Never call risk engine services

Small Account Mode:

- initial_capital minimum is 25
- support fractional quantities
- never assume large account size
- use Decimal where money/quantity precision matters

Keep this engine deterministic and testable.

Write unit tests using synthetic candle series.

Do not persist to database yet unless necessary for the test boundary.

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.6 — Backtest Fill, Fee, Slippage, and Metrics Engine

```text
Read:

- docs/PROJECT_STATUS.md
- docs/SMALL_ACCOUNT_MODE.md sections 3, 5, 8, 10, and 11
- docs/DATABASE_SCHEMA.md section 2.5
- docs/BACKEND_MODULE_SPECS.md app/services/backtesting section
- docs/MVP_BUILD_PLAN.md Phase 3

Implement backtesting support modules:

- fills.py
- metrics.py

fills.py must support:

- fee_bps
- slippage_bps
- fractional crypto/stocks
- Decimal-safe calculations
- buy and sell fill simulation
- no live or paper execution

metrics.py must compute:

- total_return_usd
- total_return_pct
- win_rate
- max_drawdown
- sharpe_like
- trade_count
- average_trade_usd
- fee_drag_pct
- equity curve data if appropriate

Small Account Mode:

- surface fee drag honestly
- produce small_account_warning when documented warning conditions are triggered
- never hide small-dollar outcomes behind percentage-only reporting

Write unit tests for:

- fee calculation
- slippage calculation
- fractional quantity precision
- max drawdown
- win rate
- total return
- fee drag
- small account warning

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.7 — Backtest Persistence Service

```text
Read:

- docs/PROJECT_STATUS.md
- docs/DATABASE_SCHEMA.md sections 2.3 through 2.6
- docs/API_CONTRACTS.md POST /backtests/run and GET /backtests/:id
- docs/BACKEND_MODULE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md

Implement the persistence layer for backtests.

Create service functions that:

- create a backtest row
- mark backtest running
- run the engine
- persist completed metrics
- persist backtest_trades
- mark backtest failed with error_detail when appropriate, if schema/code supports it
- return API-ready result objects

Requirements:

- Preserve reproducibility fields:
  - strategy_id
  - parameter_set_id
  - asset_id
  - interval
  - start_time
  - end_time
  - initial_capital
  - fee_bps
  - slippage_bps
- Store metrics in documented JSON shape
- Store trades linked to backtest_id
- Use database transactions carefully
- Failed backtests must not leave misleading completed records

Do not implement UI.

Do not implement paper trading.

Write integration tests if test DB setup supports it.

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.8 — Backtest API Endpoints

```text
Read:

- docs/PROJECT_STATUS.md
- docs/API_CONTRACTS.md backtest endpoints
- docs/FRONTEND_PAGE_SPECS.md Backtests page
- docs/BACKEND_MODULE_SPECS.md app/api/routes and app/schemas sections
- docs/SMALL_ACCOUNT_MODE.md
- docs/MVP_BUILD_PLAN.md Phase 3

Implement Backtest API endpoints.

Endpoints:

- POST /backtests/run
- GET /backtests/{id}
- GET /backtests
- GET /backtests/{id}/trades if useful and consistent with existing architecture

Requirements:

- Follow API_CONTRACTS.md response shapes
- Use explicit Pydantic request/response schemas
- Numeric fields returned as strings
- initial_capital must reject values below 25
- validate date range
- validate asset/strategy/parameter_set existence
- return 404 for unknown ids
- return 400 for invalid date range or insufficient data
- completed backtests return metrics and trades
- failed backtests return status failed with error detail if supported

Do not implement Strategy Lab.

Do not implement Paper Trading.

Do not implement AI or Risk.

Write API tests for success and error cases.

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.9 — Backtests Page UI

```text
Read:

- docs/PROJECT_STATUS.md
- docs/UI_SPEC.md Backtests section
- docs/FRONTEND_PAGE_SPECS.md /backtests section
- docs/API_CONTRACTS.md backtest endpoints
- docs/SMALL_ACCOUNT_MODE.md

Implement the /backtests page UI.

Components:

- BacktestConfigForm
- StartingBalanceInput integration
- fee/slippage inputs
- Run Backtest button
- BacktestResultPanel
- metrics display
- equity curve if data is available
- trade list table
- empty state
- loading/running state
- failed state

Requirements:

- Starting balance presets: 25, 50, 100, 250, 500, 1000
- Minimum starting balance: 25
- Display dollar and percentage results together
- Label values as Backtest Starting Capital where appropriate
- Show fee_drag_pct
- Show small_account_warning if present
- Poll GET /backtests/{id} while status is running
- Do not implement Strategy Lab parameter editing
- Do not implement comparison mode beyond a harmless scaffold if necessary

Use existing API contracts exactly.

Write frontend tests for:

- form validation
- successful run state
- failed state
- result rendering
- small account warning rendering

Report files changed, commands run, tests, and ADR status.
```

---

## Prompt 3.10 — Phase 3 Validation + Documentation Update

```text
Read:

- docs/PROJECT_STATUS.md
- docs/VALIDATION_CHECKLIST.md
- docs/MVP_BUILD_PLAN.md Phase 3
- docs/PROJECT_CONSTITUTION.md

Do not implement new features.

Perform Phase 3 validation.

Run:

- cd apps/api && pytest tests/unit/services/strategies -v
- cd apps/api && pytest tests/unit/services/backtesting -v
- cd apps/api && pytest
- cd apps/web && pnpm lint
- cd apps/web && pnpm test
- cd apps/web && pnpm build

Manual validation:

- Run at least one MA Crossover backtest against real BTCUSDT candle data
- Confirm a completed backtest row exists
- Confirm backtest_trades are stored
- Confirm metrics are non-null and sane
- Confirm /backtests renders the result
- Confirm failed/empty states behave correctly

Update docs/PROJECT_STATUS.md only after validation passes:

- Mark Phase 3 COMPLETE
- Set Current Phase to Phase 4 — Strategy Lab
- Set Current Prompt to Pending Phase 4 prompt creation/approval
- Update Overall Completion modestly
- Add Phase 3 accomplishments
- Preserve known developer environment issue if still present

Do not start Phase 4.

Report:

- Validation results
- Files changed
- Whether Phase 3 is complete
- Whether any ADR is required
```