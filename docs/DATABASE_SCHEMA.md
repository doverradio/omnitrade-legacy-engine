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

### 3a. Future Schema: Decision Intelligence Engine (Architectural Placeholder)

The Decision Intelligence Engine (`DECISION_INTELLIGENCE_ENGINE.md`) is a permanent foundational subsystem that will introduce its own tables in a future implementation phase: **Decision Records**, **Decision Snapshots**, **Decision Evidence**, **Decision Outcomes**, **Decision Reviews**, **AI Reflections**, and **Human Reviews** (see `DECISION_INTELLIGENCE_ENGINE.md` §9 for their responsibilities). **Decision Snapshots** in particular are immutable, one-to-one with a Decision Record, and capture the exact market/portfolio/version context (OHLCV, indicators, regime, position state, and the specific parameter-set/strategy/AI-model/config versions in effect) that produced the decision — write-once, never updated, per `DECISION_INTELLIGENCE_ENGINE.md` §4a. These are expected to relate closely to — and likely partially consume or reference — the existing `signals`, `model_outputs`, and `risk_events` tables above, weaving them into a single coherent per-decision record rather than requiring manual joins across tables after the fact.

The DIE's Counterfactual Outcome Ledger (COL), a core subsystem within it (`DECISION_INTELLIGENCE_ENGINE.md` §8), additionally anticipates **Shadow Outcomes** (one row per shadow BUY/SELL/WAIT action per decision) and **Counterfactual Evaluations** (one row per decision-horizon pair, storing hindsight-best action and lesson tags) tables. COL's version 1 scope is explicitly narrow — BTC only, evaluated once per minute, three horizons, a small feature snapshot — so even once implemented, its data volume is intentionally modest at first and should not be assumed to require heavy-write infrastructure from day one.

No new tables, columns, or migrations are introduced by this note. This section exists so the current schema is read with the DIE's future shape in mind — in particular, `signals`, `model_outputs`, and `risk_events` should continue to be populated completely and consistently, since they are the most likely source data the DIE's Decision Records and the COL's shadow evaluations will be built from. Full column-level schema for the DIE's and COL's tables is deferred to a future revision of this document at implementation time.

### 4. Notes on Row-Level Security (Supabase)

- Enable RLS on `paper_accounts`, `trades`, `parameter_sets` created by users, and any user-owned data — restrict to `owner_user_id = auth.uid()` (directly or via join).
- Reference/system tables (`assets`, `candles`, `strategies`, `signals`, `model_outputs`, `audit_log`) can be read-only for authenticated users and write-only via the backend service role — never written directly from the frontend.

### 5. Migration Strategy

- Use a migration tool (e.g., Supabase CLI migrations or Alembic if managing schema from the FastAPI side) from day one — no manual schema edits in the Supabase UI once Phase 1 begins. See `MVP_BUILD_PLAN.md` Phase 0.
