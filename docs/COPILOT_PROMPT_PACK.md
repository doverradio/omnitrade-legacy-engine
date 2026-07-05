# COPILOT_PROMPT_PACK.md

## OmniTrade Legacy Engine — GitHub Copilot Implementation Prompt Pack

> **Status note (added after Phase 0/1 completion):** Prompts 1–3 below (Phases 0–1) are **superseded** by `COPILOT_PHASE_0_PROMPTS.md` and `COPILOT_PHASE_1_PROMPTS.md`, which reflect the actual `apps/web`/`apps/api` repo structure (`REPO_STRUCTURE.md`) and Small Account Mode requirements added after this pack was originally written — use those files instead for Phases 0–1, not the prompts below. Prompts 4–10 (Phases 2–8) have not yet been superseded and remain the best available reference for those phases' intent, but their folder-path references (`/frontend`, `/backend`, `/workers`) are outdated and should be read as `apps/web`, `apps/api` respectively. Before starting each of Phases 2–8, author a dedicated `COPILOT_PHASE_N_PROMPTS.md` following the Phase 0/1 pattern rather than pasting Prompts 4–10 directly.

This file contains ready-to-use prompts for GitHub Copilot (Chat/agent mode) to scaffold and implement the MVP, phase by phase, referencing the other `.md` files in this repo as ground truth. Paste each prompt in order; do not skip ahead — later prompts assume earlier scaffolding exists.

**General instruction to prepend to every session:**
> "Read PROJECT_VISION.md, SYSTEM_ARCHITECTURE.md, and DATABASE_SCHEMA.md in this repo before making changes. Follow the schema and architecture exactly; if a conflict arises, ask before deviating. Never implement live real-money trading. Every trade/signal must be logged per the audit requirements in these docs."

---

### Prompt 1 — Repo Scaffold (Phase 0)
```
Scaffold a monorepo with:
- /frontend: Next.js 14+ (App Router), TypeScript, Tailwind CSS
- /backend: FastAPI (Python 3.11+), with a clear module layout:
  backend/
    main.py
    api/ (routers per resource: assets, candles, strategies, backtests, signals, trades, paper_accounts, risk, ai, audit)
    strategies/ (one file per strategy module, per STRATEGY_ENGINE.md)
    ai/ (regime_classifier.py, signal_scorer.py, allocator.py, explainer.py, post_trade_review.py)
    risk/ (risk_engine.py, rules/)
    ingestion/ (binance_client.py, alpaca_client.py, yfinance_backfill.py)
    db/ (models.py using SQLAlchemy, migrations/ using Alembic)
    core/ (config.py for env var loading, logging.py)
- /workers: scheduled job entrypoints (ingestion, signal generation loop) as a separate deployable service
- Root-level docker-compose.yml for local dev (postgres, backend, workers, frontend)
- .env.example listing every required environment variable (never real secrets) referencing DATA_SOURCES.md
- README.md explaining how to run locally
Do not implement business logic yet — this is scaffolding only.
```

### Prompt 2 — Database Schema & Migrations (Phase 0)
```
Using DATABASE_SCHEMA.md as the exact source of truth, implement:
1. SQLAlchemy models in backend/db/models.py for every table listed.
2. Alembic migration(s) that create these tables with the exact columns, types, constraints, and indexes specified.
3. A seed script (backend/db/seed.py) that inserts a handful of example assets (e.g., BTCUSDT, ETHUSDT, AAPL) and the six MVP strategies (inactive by default) from STRATEGY_ENGINE.md.
Do not deviate from the schema without flagging the discrepancy.
```

### Prompt 3 — Data Ingestion (Phase 1)
```
Implement backend/ingestion/binance_client.py and backend/ingestion/alpaca_client.py per DATA_SOURCES.md:
- Binance client: fetch klines for a given symbol/interval/date range from the public REST API, normalize into the candles schema, handle pagination for historical backfill, implement exponential backoff on 429/5xx.
- Alpaca client: fetch historical bars using the Alpaca market data API (free/IEX tier), same normalization target.
- Both clients must be idempotent writers (upsert on the (asset_id, interval, open_time) unique constraint) and log failures to audit_log per SYSTEM_ARCHITECTURE.md.
- Add a worker entrypoint (workers/ingest_recent.py) that runs on a schedule (every N minutes) to pull the latest candles for all active assets.
- Add a one-off backfill script (workers/backfill_historical.py) with CLI args for symbol/interval/date range.
- Add yfinance backfill as backend/ingestion/yfinance_backfill.py, clearly gated/labeled as research-only per DATA_SOURCES.md — write source='yfinance_backfill' on inserted rows.
Include unit tests with mocked HTTP responses for both clients.
```

### Prompt 4 — Chart UI (Phase 2)
```
In /frontend, implement the Markets page per UI_SPEC.md §2.2:
- Asset list view fetching from GET /api/assets, showing current price and data-source badge.
- Candlestick chart using TradingView Lightweight Charts for a selected asset, fetching candles from GET /api/candles.
- Interval selector (1m/5m/15m/1h/1d) that re-fetches candles.
- Overlay toggles for indicators (start with MA lines) computed client-side or fetched from a backend indicators endpoint — prefer backend-computed for consistency with strategy logic.
Style with Tailwind per the project's design system (dark-mode-friendly, clear typography for numeric data).
```

### Prompt 5 — Strategy Engine + Backtesting (Phase 3)
```
Implement the Strategy protocol and the six MVP strategy modules exactly as specified in STRATEGY_ENGINE.md section 2 (ma_crossover, rsi_mean_reversion, breakout, volatility_filter, trend_regime_filter, ensemble_scorer), each as a pure function per the Strategy contract in section 1.

Then implement an event-driven backtesting engine (backend/backtesting/engine.py) that:
- Loads candles for the requested asset/interval/date range.
- Steps through candles in order, calling the active strategy's generate_signal at each step.
- Simulates fills with configurable fee_bps and slippage_bps.
- Computes and stores metrics matching the backtests.metrics JSONB shape: total_return, win_rate, max_drawdown, sharpe_like ratio, trade_count, average_trade.
- Writes results to backtests and backtest_trades tables.
Add a POST /api/backtests endpoint that queues and runs a backtest synchronously for MVP (async/background job can come later), and a GET /api/backtests/{id} to fetch results.
Write unit tests for each strategy against synthetic price series with known expected signals (e.g., a clean uptrend should trigger ma_crossover buy signals).
```

### Prompt 6 — Strategy Parameters UI (Phase 4)
```
Implement the Strategy Lab page per UI_SPEC.md §2.3:
- List strategies with active/inactive state.
- Dynamically generate a parameter form from each strategy's default_params shape.
- "Save as new parameter set" calls POST /api/parameter_sets, then "Run backtest" navigates to a pre-filled Backtests page.
- Show parameter set history with links to associated backtest results.
Ensure toggling a strategy active/inactive calls a distinct, audited endpoint (POST /api/strategies/{id}/activate) rather than a generic PATCH, matching the "promote to active" flow in STRATEGY_ENGINE.md §4.
```

### Prompt 7 — Paper Trading Engine (Phase 5)
```
Implement:
1. backend/execution/alpaca_paper.py — routes stock orders to Alpaca's paper trading API.
2. backend/execution/internal_sim.py — simulates crypto fills against recent Binance market data with configurable slippage/fees, per SYSTEM_ARCHITECTURE.md §2.5.
3. A signal generation loop (workers/generate_signals.py) that runs on a schedule per active strategy/asset, calling the strategy, then the AI layer stubs (Phase 6), then the risk engine (Phase 7), then execution if approved.
4. API endpoints: POST /api/paper_accounts, GET /api/paper_accounts/{id}, GET /api/paper_accounts/{id}/trades, GET /api/paper_accounts/{id}/positions.
Implement the Paper Trading UI page per UI_SPEC.md §2.5.
All trades must set is_paper=true and reference the originating signal_id.
```

### Prompt 8 — AI Signal Review Layer (Phase 6)
```
Implement the AI layer modules per AI_LAYER.md:
- backend/ai/regime_classifier.py — starts with an interpretable rules/gradient-boosted-tree model over engineered features (volatility, ADX-like trend strength); outputs a regime label + confidence.
- backend/ai/signal_scorer.py — computes ai_confidence for a signal using regime, recent strategy performance, and volatility context; returns the score plus a breakdown of contributing factors.
- backend/ai/allocator.py — computes recommended strategy weights bounded per AI_LAYER.md §2.3 (no strategy below a floor or above a cap).
- backend/ai/explainer.py — generates the human-readable explanation string per AI_LAYER.md §2.4, using the specific inputs (not generic boilerplate).
- backend/ai/post_trade_review.py — scheduled job comparing trade outcomes to their stated rationale/confidence, flagging divergences.
Every module writes its inputs, outputs, and explanation to model_outputs per DATABASE_SCHEMA.md §2.10.
Wire these into the signal generation loop from Phase 5, between strategy signal generation and the risk engine.
Implement the AI Review UI page per UI_SPEC.md §2.8, including the human approve/dismiss action for post-trade review recommendations (recommendations must never auto-apply).
```

### Prompt 9 — Risk Engine (Phase 7)
```
Implement backend/risk/risk_engine.py per RISK_ENGINE.md, following the exact evaluation order in section 3 (kill switches → no-trade zones → cooldowns → daily loss → drawdown → stop-loss requirement → position size resize → AI confidence scaling → approve).
Implement each rule as its own function under backend/risk/rules/ for testability.
Every decision writes a risk_events row and an audit_log row.
Implement:
- Account-level and global kill switch state + endpoints (POST /api/risk/kill-switch/global, POST /api/risk/kill-switch/account/{id}) requiring explicit re-arm actions.
- Risk parameter storage/editing per account with system defaults fallback.
Implement the Risk Monitor UI page per UI_SPEC.md §2.7, including confirmation modals for kill-switch actions and visual flagging of risk-loosening parameter changes.
Write unit tests for every rule, including edge cases (exactly at limit, just over limit, missing/stale data triggering fail-closed behavior).
```

### Prompt 10 — Deployment (Phase 8)
```
Prepare deployment configs:
- frontend: vercel.json / Next.js config for Vercel deployment, environment variable references (never values) documented in .env.example.
- backend + workers: Dockerfiles for backend and workers services, plus deployment config for Railway/Fly.io/Render (pick one primary target and document it in a DEPLOYMENT.md), including a cron/scheduler config for the ingestion and signal-generation workers.
- Supabase: migration deployment steps (Alembic or Supabase CLI) for staging and production projects.
- Add a health-check endpoint (GET /api/health) verifying DB connectivity and last successful ingestion timestamp, used by the platform's uptime monitoring.
- Document required environment variables per service in DEPLOYMENT.md, cross-referencing DATA_SOURCES.md for API key acquisition steps.
Do not include any real secrets in any committed file.
```

---

### Usage Notes for Copilot Sessions

- Run prompts sequentially; commit after each phase.
- If Copilot proposes deviating from `DATABASE_SCHEMA.md`, `RISK_ENGINE.md`, or `AI_LAYER.md`'s "should not do" constraints, stop and resolve the conflict in the docs first — the docs are the source of truth, not the generated code.
- Every phase should end with passing unit tests before moving to the next prompt.
