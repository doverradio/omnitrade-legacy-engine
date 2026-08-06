# DATABASE_SCHEMA.md

## OmniTrade Legacy Engine — Initial Database Schema (Postgres / Supabase)

### 1. Design Principles

- Every mutable business entity has a corresponding audit trail (either via `audit_log` or an explicit history table).
- Prefer explicit foreign keys and `NOT NULL` constraints over implicit application-level enforcement.
- Use UUID primary keys (`gen_random_uuid()`) for all tables to keep IDs non-guessable and merge-friendly.
- All timestamps stored as `timestamptz`, always UTC.
- Money/quantity fields use `numeric` (never `float`) to avoid floating-point drift in accounting, with scale sufficient for both small dollar amounts and fractional crypto quantities — see `SMALL_ACCOUNT_MODE.md` §5/§7.
- Starting-capital fields (`paper_accounts.starting_balance`, `backtests.initial_capital`) enforce a $25 floor at the database level via `CHECK` constraints, not just application-layer validation — Small Account Mode is a schema-level guarantee, not just a UI convention.

### 2. Core Tables

#### 2.1 `assets`
```sql
CREATE TABLE assets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol TEXT NOT NULL,             -- e.g. 'BTCUSDT', 'AAPL'
  asset_class TEXT NOT NULL CHECK (asset_class IN ('crypto', 'stock')),
  exchange TEXT NOT NULL,           -- e.g. 'binance_us', 'alpaca'
  base_currency TEXT,               -- e.g. 'USDT' for crypto pairs
  supports_fractional BOOLEAN NOT NULL DEFAULT true,  -- crypto: always true; stock: true only if Alpaca supports fractional shares for this symbol — see SMALL_ACCOUNT_MODE.md §6
  min_order_notional NUMERIC,       -- exchange/broker minimum order value in quote currency, nullable if unknown — used by the risk engine's minimum-viable-position check, see SMALL_ACCOUNT_MODE.md §4
  qty_step_size NUMERIC,            -- minimum quantity increment (e.g. Binance LOT_SIZE step), nullable if unknown — see SMALL_ACCOUNT_MODE.md §5
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (symbol, exchange)
);
```

#### 2.2 `candles`
```sql
CREATE TABLE candles (
  id BIGSERIAL PRIMARY KEY,
  asset_id UUID NOT NULL REFERENCES assets(id),
  interval TEXT NOT NULL,           -- '1m','5m','15m','1h','1d', etc.
  open_time TIMESTAMPTZ NOT NULL,
  close_time TIMESTAMPTZ NOT NULL,
  open NUMERIC NOT NULL,
  high NUMERIC NOT NULL,
  low NUMERIC NOT NULL,
  close NUMERIC NOT NULL,
  volume NUMERIC NOT NULL,
  source TEXT NOT NULL,             -- 'binance_us','alpaca','yfinance_backfill'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (asset_id, interval, open_time)
);
CREATE INDEX idx_candles_asset_interval_time ON candles (asset_id, interval, open_time);
```

#### 2.3 `strategies`
```sql
CREATE TABLE strategies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,        -- e.g. 'ma_crossover', 'rsi_mean_reversion'
  description TEXT,
  module_version TEXT NOT NULL,     -- code version tag for reproducibility
  is_active BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 2.4 `parameter_sets`
```sql
CREATE TABLE parameter_sets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id UUID NOT NULL REFERENCES strategies(id),
  label TEXT NOT NULL,              -- human-readable, e.g. 'conservative-v1'
  params JSONB NOT NULL,            -- e.g. {"fast_ma": 10, "slow_ma": 50}
  created_by TEXT NOT NULL,         -- user id / 'system'
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 2.5 `backtests`
```sql
CREATE TABLE backtests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id UUID NOT NULL REFERENCES strategies(id),
  parameter_set_id UUID NOT NULL REFERENCES parameter_sets(id),
  asset_id UUID NOT NULL REFERENCES assets(id),
  interval TEXT NOT NULL,
  start_time TIMESTAMPTZ NOT NULL,
  end_time TIMESTAMPTZ NOT NULL,
  initial_capital NUMERIC NOT NULL CHECK (initial_capital >= 25),  -- Small Account Mode floor — see SMALL_ACCOUNT_MODE.md §2/§8
  fee_bps NUMERIC NOT NULL DEFAULT 10,      -- assumed fee, in basis points
  slippage_bps NUMERIC NOT NULL DEFAULT 5,  -- assumed slippage, in basis points
  status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed')),
  metrics JSONB,                             -- {sharpe_like, win_rate, max_drawdown, total_return, total_return_pct, fee_drag_pct, ...} — total_return and fee-related figures stored as raw dollar values; percentage figures computed alongside per SMALL_ACCOUNT_MODE.md §3
  small_account_warning JSONB,               -- nullable; populated when a SMALL_ACCOUNT_MODE.md §11 warning condition is triggered, e.g. {"type": "high_fee_drag", "detail": "Fees consumed 34% of gross gains"}
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
```

#### 2.6 `backtest_trades`
```sql
CREATE TABLE backtest_trades (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  backtest_id UUID NOT NULL REFERENCES backtests(id),
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity NUMERIC NOT NULL,
  price NUMERIC NOT NULL,
  executed_at TIMESTAMPTZ NOT NULL,
  reason TEXT                        -- short strategy-generated rationale
);
```

#### 2.7 `signals`
```sql
CREATE TABLE signals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_id UUID NOT NULL REFERENCES strategies(id),
  parameter_set_id UUID NOT NULL REFERENCES parameter_sets(id),
  asset_id UUID NOT NULL REFERENCES assets(id),
  signal_time TIMESTAMPTZ NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('buy','sell','hold')),
  raw_strength NUMERIC,               -- strategy's own confidence, e.g. 0-1
  ai_confidence NUMERIC,              -- AI layer's confidence score, 0-1, nullable until scored
  regime_tag TEXT,                    -- AI regime classifier output, nullable
  status TEXT NOT NULL CHECK (status IN ('generated','risk_approved','risk_rejected','executed','expired')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_signals_asset_time ON signals (asset_id, signal_time);
```

#### 2.8 `paper_accounts`
```sql
CREATE TABLE paper_accounts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_user_id UUID NOT NULL,        -- references Supabase auth.users
  name TEXT NOT NULL,
  asset_class TEXT NOT NULL CHECK (asset_class IN ('crypto', 'stock')),
  starting_balance NUMERIC NOT NULL CHECK (starting_balance >= 25),  -- Small Account Mode floor — see SMALL_ACCOUNT_MODE.md §2
  current_cash_balance NUMERIC NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
> `starting_balance` and `current_cash_balance` use `NUMERIC` with sufficient scale (e.g., `NUMERIC(18,8)`) to represent both small dollar amounts and fractional crypto valuations precisely — never truncated to whole dollars. Position `quantity` columns in `trades` and `backtest_trades` similarly require enough decimal scale to hold fractional crypto quantities (e.g., 0.00038 BTC) without rounding error, per `SMALL_ACCOUNT_MODE.md` §5.

#### 2.9 `trades` (live paper trades — distinct from backtest_trades)
```sql
CREATE TABLE trades (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_account_id UUID NOT NULL REFERENCES paper_accounts(id),
  signal_id UUID REFERENCES signals(id),
  asset_id UUID NOT NULL REFERENCES assets(id),
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  quantity NUMERIC NOT NULL,
  price NUMERIC NOT NULL,
  fee NUMERIC NOT NULL DEFAULT 0,
  is_paper BOOLEAN NOT NULL DEFAULT true,
  execution_venue TEXT NOT NULL,      -- 'alpaca_paper','internal_sim'
  executed_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_trades_account_time ON trades (paper_account_id, executed_at);
```

#### 2.10 `model_outputs`
```sql
CREATE TABLE model_outputs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_name TEXT NOT NULL,           -- 'regime_classifier','signal_scorer','allocator','explainer','post_trade_review'
  model_version TEXT NOT NULL,
  related_signal_id UUID REFERENCES signals(id),
  related_trade_id UUID REFERENCES trades(id),
  input_summary JSONB NOT NULL,       -- key inputs used, for reproducibility
  output JSONB NOT NULL,              -- structured output (score, regime label, weights, etc.)
  explanation TEXT NOT NULL,          -- human-readable rationale, always required
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 2.11 `risk_events`
```sql
CREATE TABLE risk_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_account_id UUID REFERENCES paper_accounts(id),
  related_signal_id UUID REFERENCES signals(id),
  event_type TEXT NOT NULL,           -- 'position_limit','daily_loss_limit','drawdown_limit','cooldown','kill_switch', etc.
  action_taken TEXT NOT NULL,         -- 'blocked','resized','paused_account','global_kill'
  detail JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 2.12 `audit_log`
```sql
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  actor TEXT NOT NULL,                -- user id or 'system'
  action TEXT NOT NULL,                -- 'parameter_change','strategy_activated','backtest_run','trade_executed', etc.
  entity_type TEXT NOT NULL,
  entity_id UUID,
  before_state JSONB,
  after_state JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_log_entity ON audit_log (entity_type, entity_id);
```

### 3. Relationships Summary

- `assets` → `candles` (1:many)
- `strategies` → `parameter_sets` → `backtests` / `signals` (1:many chains)
- `backtests` → `backtest_trades` (1:many)
- `paper_accounts` → `trades` (1:many)
- `signals` → `trades` (1:0..1) and → `model_outputs` (1:many, one per model stage)
- `risk_events` link back to `signals`/`paper_accounts` for full traceability of every block/resize/kill decision
- `audit_log` is polymorphic (via `entity_type` + `entity_id`) and covers everything above

### 3a. Decision Intelligence Engine Schema (Built — corrected 2026-08-06)

> **Correction:** This section previously described the Decision Intelligence Engine's tables as future, unbuilt work ("No new tables, columns, or migrations are introduced by this note"). That is no longer accurate. The core tables were migrated on 2026-07-06 (`apps/api/app/db/migrations/versions/20260706_0007_add_decision_record_snapshot_tables.py`) and are live in production, consumed by the autonomous decision path. This drift was independently identified by three separate audits (`docs/OMNITRADE_REPOSITORY_REALITY_CHECK.md` row B1, `docs/MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md`, and this reconciliation pass) before being corrected here — see `docs/DOCUMENTATION_DRIFT_REPORT.md` §2.1.

The Decision Intelligence Engine (`DECISION_INTELLIGENCE_ENGINE.md`) is a permanent foundational subsystem with the following tables **built and in production**:

- **`decision_records`** (`apps/api/app/models/decision_record.py`) — the central table implementing `DECISION_INTELLIGENCE_ENGINE.md` §4's schema field-for-field (`decision_id`, `version`, `timestamp`, `asset`, `timeframe`, `market_regime`, `indicators`, `generated_signals`, `confidence`, `supporting_strategies`/`opposing_strategies`, `risk_adjustments`, `trade_accepted`, `outcome`, `lessons_learned`, `confidence_calibration`, `review_status`, `human_notes`, and more). Append-only at the application layer, enforced via SQLAlchemy `before_update`/`before_delete` event listeners that raise on any attempted mutation.
- **`decision_snapshots`** (`apps/api/app/models/decision_snapshot.py`) — immutable, one-to-one with a Decision Record via a `decision_id` foreign key/primary key, implementing §4a's schema: `ohlcv_context`, `indicators`, `generated_features`, `market_regime`, `volatility`, `spread_liquidity_context`, `strategy_inputs`, `risk_inputs`, `current_position_state`, `open_trades`, `portfolio_exposure`, and all five mandatory version-pin fields (`parameter_set_version`, `strategy_version`, `ai_model_version`, `decision_engine_version`, `configuration_version`). Write-once, never updated, enforced the same way as `decision_records`.
- **`decision_explainability_records`** (`apps/api/app/models/decision_explainability_record.py`) — implements the Explainability Layer (§5): role-tagged (`supporting`/`opposing`/`confidence_factor`/`risk_adjustment`) evidence rows linked to a Decision Record, with explicit `availability_state` (`known`/`unknown`/`unavailable`) rather than fabricated evidence.
- **`decision_counterfactual_results`** (`apps/api/app/models/decision_counterfactual_result.py`) — implements the Counterfactual Outcome Ledger (COL, §8), matching its documented V1 scope precisely: BTC-only, horizons hardcoded to 15m/1h/24h, shadow BUY/SELL/WAIT tracked regardless of the real action taken, lesson tags matching §8.5's list closely.
- **`decision_quality_scores`** (`apps/api/app/models/decision_quality_score.py`) — implements the Decision Quality Engine (DQE, §8a): a composite score plus per-dimension breakdown, linked to a Decision Record and the Counterfactual Evaluations it depends on, with its own `scoring_model_version` field. A row only exists once relevant COL data has resolved for that decision — it should never be assumed to exist at the same time as a decision's Decision Record.
- **`decision_alternative_actions`** (`apps/api/app/models/decision_alternative_action.py`) — records alternative actions considered but not chosen, alongside the chosen one.

These tables relate closely to, and were built alongside, the existing `signals`, `model_outputs`, and `risk_events` tables above — `signals`/`model_outputs`/`risk_events` remain in active use and should continue to be populated completely and consistently, since Decision Record ingestion (`apps/api/app/services/decisions/ingestion.py`) is built to consume them.

All of these are governed by ADR-0002, ADR-0003, ADR-0004, and ADR-0007 (see `docs/adr/`). Full column-level detail beyond the summary above lives in the model files themselves, which are the authoritative source of truth for schema shape per this document's own §1 convention (models mirror `DATABASE_SCHEMA.md`, not the reverse) — but this section itself must not be read as "future work" again.

### 4. Notes on Row-Level Security (Supabase)

- Enable RLS on `paper_accounts`, `trades`, `parameter_sets` created by users, and any user-owned data — restrict to `owner_user_id = auth.uid()` (directly or via join).
- Reference/system tables (`assets`, `candles`, `strategies`, `signals`, `model_outputs`, `audit_log`) can be read-only for authenticated users and write-only via the backend service role — never written directly from the frontend.

### 5. Migration Strategy

- Use a migration tool (e.g., Supabase CLI migrations or Alembic if managing schema from the FastAPI side) from day one — no manual schema edits in the Supabase UI once Phase 1 begins. See `MVP_BUILD_PLAN.md` Phase 0.
