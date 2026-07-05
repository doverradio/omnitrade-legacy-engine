# BACKEND_MODULE_SPECS.md

## OmniTrade Legacy Engine — FastAPI Backend Module Responsibilities

> Paths below are relative to `apps/api/`, matching `REPO_STRUCTURE.md`.

---

### `app/main.py`
**Responsibility:** Application entrypoint. Creates the FastAPI instance, registers middleware (CORS, request logging, error handlers from `app/core/errors.py`), includes all routers from `app/api/routes/`, and wires startup/shutdown events (DB connection pool init, config validation).
**Must not contain:** business logic, direct DB queries, or strategy/risk logic — this file only assembles the app.

### `app/config.py`
**Responsibility:** Single source of typed configuration via `pydantic-settings`, loading from environment variables per `ENVIRONMENT_SETUP.md` §6. Exposes a `Settings` singleton (`get_settings()`) used everywhere else — no module reads `os.environ` directly outside this file.
**Includes:** database URL, Supabase keys, exchange API credentials, environment name, log level, default risk parameters, feature flags (e.g., `GLOBAL_KILL_SWITCH_DEFAULT`).

### `app/db/`
**Responsibility:** Database connectivity layer.
- `session.py`: async SQLAlchemy engine + session factory, `get_db()` FastAPI dependency.
- `base.py`: declarative base class all models inherit from.
- `migrations/`: Alembic environment and versioned migration scripts — the only sanctioned way to change schema (no manual DDL against any shared environment).
**Must not contain:** business/query logic beyond generic session management — actual queries live in `services/`.

### `app/models/`
**Responsibility:** SQLAlchemy ORM model definitions, one file per table, mirroring `DATABASE_SCHEMA.md` exactly (column names, types, constraints, indexes). These are the single source of truth for the database shape in code; Alembic migrations are generated from changes here.
**Must not contain:** business logic methods beyond simple computed properties (e.g., a `position.unrealized_pnl` property is acceptable; a method that places a trade is not — that belongs in `services/`).

### `app/schemas/`
**Responsibility:** Pydantic models defining API request/response shapes, mirroring `API_CONTRACTS.md`. Kept separate from `app/models/` (ORM) so internal DB shape can evolve independently of the public API contract.
**Includes:** `common.py` for shared shapes (pagination envelope, error envelope).
**Convention:** every route in `app/api/routes/` must declare explicit `response_model=` and request body schemas from here — no raw dict responses.

### `app/services/data/`
**Responsibility:** Market data ingestion. Contains `binance_client.py`, `alpaca_client.py`, `yfinance_backfill.py`, and a shared `http_client.py` (backoff/retry wrapper per `DATA_SOURCES.md` §4). Normalizes exchange-specific responses into the internal candle shape and performs idempotent upserts via `app/models/candle.py`.
**Must not contain:** strategy, risk, or execution logic — this module's job ends at "candles are correctly and reliably in the database."

### `app/services/strategies/`
**Responsibility:** Strategy modules implementing the `Strategy` protocol from `STRATEGY_ENGINE.md` §1 — one file per strategy (`ma_crossover.py`, `rsi_mean_reversion.py`, `breakout.py`, `volatility_filter.py`, `trend_regime_filter.py`, `ensemble_scorer.py`), plus a `registry.py` mapping `slug` → implementation.
**Must not contain:** any DB writes, HTTP calls, or knowledge of risk/execution — strategies are pure functions of `(candles, params, context) -> Signal`.

### `app/services/backtesting/`
**Responsibility:** `engine.py` (event-driven backtest runner), `metrics.py` (Sharpe-like ratio, max drawdown, win rate calculations), `fills.py` (fee/slippage simulation). Consumes strategies from `app/services/strategies/` and candles from the DB; writes results via `app/models/backtest.py` and `app/models/backtest_trade` equivalents.
**Must not contain:** live/paper execution logic — backtesting never touches `app/services/paper/`.

### `app/services/signals/`
**Responsibility:** Orchestration of the live signal-generation loop: for each active strategy/asset pair on schedule, calls the strategy, then `app/services/ai/`, then `app/services/risk/`, then (if approved) `app/services/paper/`. Also handles signal expiry logic and persistence of every intermediate state to `signals` and `model_outputs`.
**Must not contain:** the actual strategy math, AI scoring math, or risk rule math — this module *calls* those services in the correct order and handles persistence/error paths; it does not reimplement their logic.

### `app/services/risk/`
**Responsibility:** `risk_engine.py` (the evaluation-order orchestrator from `RISK_ENGINE.md` §3) and `rules/` (one file per rule: `position_size.py`, `daily_loss.py`, `drawdown.py`, `stop_loss.py`, `cooldown.py`, `no_trade_zone.py`, `kill_switch.py`). Each rule is independently unit-testable and returns an explicit approve/resize/reject decision plus a reason.
**Must not contain:** strategy or AI logic — risk rules only consume already-computed signals, account state, and AI outputs; they don't generate or score signals themselves.

### `app/services/paper/`
**Responsibility:** `alpaca_paper.py` (stock order routing to Alpaca paper API), `internal_sim.py` (crypto fill simulation against recent market data), `account.py` (position/balance accounting, P&L calculation, the reset-account operation). This is the only module permitted to write to `trades` and mutate `paper_accounts` balances.
**Must not contain:** any code path that could route to a live/real-money endpoint in MVP — this is enforced by only ever configuring paper API base URLs (see `SECURITY_AND_SAFETY.md`).

### `app/services/ai/`
**Responsibility:** `regime_classifier.py`, `signal_scorer.py`, `allocator.py`, `explainer.py`, `post_trade_review.py` per `AI_LAYER.md` §2. Each writes its inputs/outputs/explanation to `model_outputs` via `app/models/model_output.py`.
**Must not contain:** any direct order placement or risk-limit bypass — outputs from this module are always advisory inputs to `app/services/risk/`, never a path directly to `app/services/paper/`.

### `app/services/decisions/` (future phase — architectural placeholder)
**Responsibility:** The Decision Intelligence Engine's recording and retrieval layer, per `DECISION_INTELLIGENCE_ENGINE.md`. Anticipated contents: `recorder.py` (writes a Decision Record from the outputs of `strategies/`, `ai/`, and `risk/` at the point `signals/` orchestrates a decision — see `DECISION_INTELLIGENCE_ENGINE.md` §3), `snapshot.py` (captures the immutable Decision Snapshot alongside each Decision Record — the point-in-time OHLCV/indicator/regime/portfolio state plus the exact parameter-set/strategy/AI-model/config version pins, per `DECISION_INTELLIGENCE_ENGINE.md` §4a — written once and never updated), `outcome_tracker.py` (appends realized outcomes once a position closes), `reflection.py` (later hindsight-informed AI commentary passes), `query.py` (supports the search/analysis patterns in `DECISION_INTELLIGENCE_ENGINE.md` §6), a `counterfactual/` subpackage implementing the Counterfactual Outcome Ledger (COL) per `DECISION_INTELLIGENCE_ENGINE.md` §8: `shadow_tracker.py` (spawns shadow BUY/SELL/WAIT outcomes at decision time), `horizon_evaluator.py` (the scheduled background job that revisits shadow outcomes at each configured horizon and computes hindsight-best action), and `lesson_tagger.py` (derives lesson tags from the horizon comparison), and a `quality/` subpackage implementing the Decision Quality Engine (DQE) per `DECISION_INTELLIGENCE_ENGINE.md` §8a: `scorer.py` (computes the composite Decision Quality Score and its per-dimension breakdown once relevant `counterfactual/` data is resolved) and `metrics_aggregator.py` (rolls per-decision scores up into the dashboard metrics in `DECISION_INTELLIGENCE_ENGINE.md` §8a.5).
**Must not contain:** any logic that influences a decision in real time — this module is purely observational, writing after `app/services/risk/` has already acted, never before or in place of it. `snapshot.py` specifically must capture values, never live references — a Decision Snapshot must remain accurate even if the underlying candle/indicator tables are later recalculated or corrected. The `counterfactual/` subpackage specifically must not evolve into a second backtesting engine (no arbitrary historical replay, no alternate-parameter simulation) — see `DECISION_INTELLIGENCE_ENGINE.md` §8.6; that functionality belongs in `app/services/backtesting/`. The `quality/` subpackage must not compute a score before its required counterfactual inputs are resolved, must never write a placeholder/default score, and must never feed a score back into `app/services/ai/` or `app/services/risk/` automatically — per `DECISION_INTELLIGENCE_ENGINE.md` §8a.6, DQE output is a human-reviewed diagnostic only.
**Note:** This module is documented here as an architectural placeholder only. It is not scheduled for implementation in the current MVP phases (`MVP_BUILD_PLAN.md`) and should not be scaffolded until a dedicated phase is defined for it. If/when it is scheduled, COL's version 1 scope is explicitly narrow (BTC only, once-per-minute evaluation, 3 horizons, small feature snapshot, no heavy compute) — implementation should not exceed that scope without a deliberate version-2 decision. Per the ADR system (`docs/adr/README.md`), any implementation-time change to this module's scope beyond what's described here (in `DECISION_INTELLIGENCE_ENGINE.md`, ADR-0002, ADR-0003, ADR-0004, or ADR-0007) is itself an architectural decision and should prompt an ADR check before coding.

### `app/api/routes/`
**Responsibility:** Thin HTTP layer — one router module per resource area (`health.py`, `markets.py`, `backtests.py`, `strategies.py`, `signals.py`, `paper.py`, and later `risk.py`, `settings.py`, `audit.py`, and (future phase) `decisions.py` per `DECISION_INTELLIGENCE_ENGINE.md` §9). Each route: validates input via `app/schemas/`, calls the relevant `app/services/*` function(s), and returns a schema-typed response. Auth dependency (`app/core/security.py`) applied per-router.
**Must not contain:** business logic, direct SQLAlchemy queries beyond simple pass-through fetches, or cross-service orchestration beyond what's needed to call 1-2 services and shape the response.

### `app/core/`
**Responsibility:**
- `logging.py`: structured logging configuration (JSON logs in non-local environments), used everywhere via `logging.getLogger(__name__)`.
- `errors.py`: shared exception classes (`NotFoundError`, `ValidationError`, `ConflictError`, etc.) and a FastAPI exception handler mapping them to the `API_CONTRACTS.md` error envelope.
- `security.py`: Supabase JWT verification dependency (`get_current_user`), used by protected routes.

---

### Dependency Direction (Enforced Convention)

```
api/routes  →  services/*  →  models/  →  db/
                    ↑
             schemas/ (used by routes for I/O shaping,
             and by services only when returning structured
             data that isn't a plain ORM object)
```

- `services/` modules may depend on other `services/` modules only in the direction: `signals` → `strategies`, `ai`, `risk`, `paper`. Reverse dependencies (e.g., `risk` importing from `signals`) are not allowed — this keeps the risk engine testable in isolation.
- `services/decisions/` (future phase) may depend on `strategies`, `ai`, `risk`, and `paper` for read access to their outputs, but none of those modules may depend on `services/decisions/` — the DIE observes and records; it is never a dependency of the decision-making path itself.
- `models/` never import from `services/` or `api/`.
- `config.py` may be imported anywhere; nothing should re-implement env var loading elsewhere.
