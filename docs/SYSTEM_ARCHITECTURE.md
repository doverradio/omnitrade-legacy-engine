# SYSTEM_ARCHITECTURE.md

## OmniTrade Legacy Engine — System Architecture

### 1. High-Level Overview

```
                         ┌─────────────────────────┐
                         │        Frontend          │
                         │   Next.js + TypeScript   │
                         │   Tailwind + Charts      │
                         └────────────┬─────────────┘
                                      │ HTTPS/REST (+ optional WS)
                         ┌────────────▼─────────────┐
                         │        Backend API        │
                         │   FastAPI (Python)        │
                         │  Strategy / Risk / AI      │
                         │  Orchestration Layer       │
                         └───┬─────────┬────────┬────┘
                             │         │        │
              ┌──────────────┘         │        └───────────────┐
              ▼                        ▼                        ▼
    ┌───────────────────┐   ┌───────────────────┐   ┌────────────────────┐
    │ Data Ingestion      │   │ Backtesting &      │   │ Paper Trading       │
    │ Workers (cron)      │   │ Strategy Engine     │   │ Execution Engine    │
    │ Binance / Alpaca /  │   │ (vectorized +       │   │ (Alpaca paper /     │
    │ yfinance backfill   │   │ event-driven)       │   │ simulated crypto)   │
    └─────────┬───────────┘   └─────────┬───────────┘   └─────────┬──────────┘
              │                          │                          │
              └────────────┬─────────────┴────────────┬─────────────┘
                            ▼                          ▼
                  ┌───────────────────┐      ┌───────────────────┐
                  │   Postgres (Supabase) │  │   AI Layer          │
                  │  Candles, Trades,      │  │  Regime classifier, │
                  │  Signals, Audit Log    │  │  Signal scorer,     │
                  └───────────┬───────────┘  │  Allocator,         │
                              │              │  Explanation gen.    │
                              │              └─────────┬───────────┘
                              ▼                        │
                  ┌───────────────────┐                │
                  │   Risk Engine       │◄──────────────┘
                  │  (gatekeeper for     │
                  │  every trade/signal) │
                  └───────────────────┘
```

### 2. Component Breakdown

#### 2.1 Frontend — Next.js / TypeScript / Tailwind
- Renders dashboard pages (see `UI_SPEC.md`).
- Talks to the backend exclusively via a typed REST API client (`/lib/api/*`); no direct DB access from the browser.
- Charts via TradingView Lightweight Charts (price/candles/trade markers) and Recharts (equity curve, drawdown, performance bar/line charts).
- Auth via Supabase Auth (email/password + optional magic link); session forwarded to backend as a JWT.
- Deployed on Vercel.

#### 2.2 Backend — FastAPI (Python) — **Recommended over Next.js API routes**
**Recommendation: FastAPI (Python), not Next.js API routes.**
Reasoning:
- The backtesting engine, strategy math (pandas/numpy/ta-lib-style indicators), and any ML/AI layer are far more natural in Python than in a Node/TypeScript API route.
- Python has a mature quant ecosystem (pandas, numpy, scipy, scikit-learn) that would otherwise need to be reimplemented or shelled out to from Node.
- Keeping one backend language avoids splitting business logic across two runtimes (Node for "simple" routes, Python for "heavy" jobs), which becomes an audit and maintenance liability for a system that prizes explainability.
- FastAPI gives async I/O (good for concurrent exchange API calls), automatic OpenAPI schema (useful for the typed frontend client), and Pydantic validation (useful for strict input validation on every trade/signal endpoint).

Backend responsibilities:
- Expose REST endpoints for: assets, candles, strategies, backtests, signals, trades, paper accounts, risk status, AI review, audit log.
- Own the strategy engine, backtesting engine, risk engine, and orchestration of the AI layer.
- Own all writes to Postgres — the frontend never writes directly to the DB.
- Deployed as a container on Railway/Fly.io/Render.

#### 2.3 Data Ingestion Workers
- Scheduled jobs (cron-based, e.g. APScheduler or a Railway/Fly cron trigger) that:
  - Pull live/recent candles from Binance/Binance.US public REST endpoints (crypto) and Alpaca market data (stocks).
  - Backfill historical candles from Binance/Alpaca, with yfinance as an optional research/backfill-only fallback (never for live signals — see `DATA_SOURCES.md`).
  - Normalize all data into a single internal candle schema before writing to Postgres.
  - Are idempotent (safe to re-run) and write ingestion status to the audit log.
- Deployed as a separate worker service (not the same process as the API) so a stuck job cannot block API responsiveness.

#### 2.4 Backtesting Engine
- Event-driven backtester (not just vectorized) so it can share logic with the live/paper execution path — the same strategy code that runs in backtest also runs in paper trading, avoiding "backtest vs. live" logic drift.
- Vectorized pre-pass (pandas/numpy) for fast indicator computation, then an event loop that simulates order fills, slippage assumptions, and fees.
- Every backtest run is persisted (`backtests` table) with its full parameter set, date range, and resulting metrics, so results are reproducible.

#### 2.5 Paper Trading Execution Engine
- For stocks: routes orders through Alpaca's paper trading API.
- For crypto: since Binance.US paper trading is not generally available, crypto paper trading is **simulated internally** — orders are matched against live/recent Binance market data with configurable slippage/fee assumptions, and fills are recorded exactly like real trades in the `trades` table (flagged `is_paper = true`).
- Shares the strategy/risk pipeline with the backtester — the only thing that changes is the data source (historical vs. live) and the execution adapter (simulated fill vs. Alpaca paper API).

#### 2.6 Strategy Engine
See `STRATEGY_ENGINE.md`. Runs as pluggable modules behind a common interface (`generate_signal(candles, params) -> Signal`), so new strategies can be added without touching orchestration code.

#### 2.7 AI Layer
See `AI_LAYER.md`. Sits alongside — not inside — the strategy engine. It consumes strategy outputs and market context, and produces regime classifications, confidence scores, strategy weight recommendations, and natural-language explanations. It cannot directly place trades; it can only annotate/weight signals that flow through the risk engine.

#### 2.8 Risk Engine
See `RISK_ENGINE.md`. A mandatory gatekeeper: every signal, whether from a rules-based strategy or AI-adjusted, must pass through the risk engine before it can become an order. The risk engine can veto, resize, or delay any trade, and can trip a global kill switch.

#### 2.9 Database — Supabase/Postgres
See `DATABASE_SCHEMA.md`. Single source of truth for assets, candles, strategies, backtests, signals, trades, paper accounts, model outputs, parameter sets, and the audit log. Supabase gives managed Postgres + Auth + row-level security in one place, minimizing infra surface area.

#### 2.10 Logging / Audit
- Every API call that mutates state writes a row to `audit_log` (actor, action, before/after state, timestamp).
- Every AI decision writes its inputs, outputs, and explanation to `model_outputs`.
- Logs are append-only at the application level (no update/delete endpoints exposed for audit rows).

#### 2.11 Deployment
- Frontend: Vercel.
- Backend API + workers: Railway, Fly.io, or Render (containerized FastAPI + a separate worker/cron service).
- Database: Supabase (managed Postgres).
- Secrets: environment variables only, managed per-environment (local `.env`, platform secret managers in staging/prod) — never committed to the repo. See `DATA_SOURCES.md` and `MVP_BUILD_PLAN.md` for specifics.

### 3. Request Flow Example — "Generate a signal, run it through risk, paper-execute it"

1. Scheduler triggers the strategy runner for an active strategy on a schedule (e.g., every closed 15m candle).
2. Strategy engine computes indicators and emits a raw `Signal` (buy/sell/hold + strength).
3. AI layer scores the signal's confidence and tags the current market regime; writes an explanation.
4. Risk engine evaluates the (strategy signal + AI score) against account state, position limits, drawdown limits, and cooldown state.
5. If approved, the paper execution engine places a simulated/paper order and records a `trade` row.
6. All of steps 2–5 write to `audit_log` and (for AI steps) `model_outputs`.
7. Frontend polls/subscribes to updated account, trade, and signal data to update the dashboard.

### 4. Environments

- **local**: docker-compose (or local processes) + local Supabase project or hosted dev project.
- **staging**: full stack on the same providers as prod, using paper-only credentials.
- **production**: same stack; "production" initially still means paper trading only, until the live-trading gate (see `RISK_ENGINE.md` and `MVP_BUILD_PLAN.md`) is explicitly opened by a human decision, out of scope for MVP.
