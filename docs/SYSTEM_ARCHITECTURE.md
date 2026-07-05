# SYSTEM_ARCHITECTURE.md

## OmniTrade Legacy Engine — System Architecture

### 1. High-Level Overview

> This architecture is organized around four permanent foundational engines: **Market Intelligence** (ingestion/indicators/regime, §2.3/§2.7 in part), **Strategy Evolution** (§2.4/§2.6), **Decision Intelligence** (§2.7a — the platform's permanent memory and reasoning system, see `DECISION_INTELLIGENCE_ENGINE.md`), and **Portfolio Intelligence** (§2.5/§2.9 in part). The diagram below shows these as they map onto concrete services; the four-engine framing is the conceptual lens for how they relate.

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
                  └─────────┬───────────┘
                            ▼
                  ┌───────────────────────┐
                  │ Decision Intelligence   │
                  │ Engine (DIE)            │
                  │ (records reasoning,     │
                  │  evidence, outcome —    │
                  │  after every decision)  │
                  └───────────────────────┘
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
- Expose REST endpoints for: assets, candles, strategies, backtests, signals, trades, paper accounts, risk status, AI review, audit log, and (future phase) decision records — see `DECISION_INTELLIGENCE_ENGINE.md` §9.
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

#### 2.7a Decision Intelligence Engine (DIE)
See `DECISION_INTELLIGENCE_ENGINE.md`. A permanent foundational engine, standing alongside the Market Intelligence, Strategy Evolution, and Portfolio Intelligence engines. Consumes the output of every decision point — strategy evaluations, AI scoring, and risk engine outcomes — and writes a structured Decision Record for it, whether or not a trade resulted. It is observational, not participatory: it never influences a decision in real time, it only records after the risk engine has acted, and later enriches that record with outcomes, post-trade review, and AI reflection. Its core subsystems are Decision Records and the **Counterfactual Outcome Ledger (COL)** — a lightweight, continuously-running background process that tracks hypothetical shadow BUY/SELL/WAIT outcomes for every decision and revisits them at fixed horizons, so the platform learns from rejected trades and inaction, not only executed ones (`DECISION_INTELLIGENCE_ENGINE.md` §8). Full architecture, schema, and lifecycle are documented separately; this entry establishes its place in the request flow (see §3).

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
5. The Decision Intelligence Engine records a Decision Record capturing the market context, evidence, confidence, and risk engine outcome from steps 2–4 — regardless of whether the risk engine approved, resized, or rejected the signal (see `DECISION_INTELLIGENCE_ENGINE.md` §3).
6. If approved, the paper execution engine places a simulated/paper order and records a `trade` row; the DIE's Decision Record is linked to this trade for later outcome tracking.
7. All of steps 2–6 write to `audit_log` and (for AI steps) `model_outputs`.
8. Frontend polls/subscribes to updated account, trade, and signal data to update the dashboard.
9. (Asynchronous, later) Once a position is closed, outcome data, post-trade review findings, and eventually AI reflection are appended to the same Decision Record.
10. (Asynchronous, parallel to steps 6–9, future phase) The Counterfactual Outcome Ledger spawns shadow BUY/SELL/WAIT outcomes for the decision at step 5, independent of whether execution happened, and background jobs revisit them at fixed horizons to compute hindsight-best action and lesson tags (`DECISION_INTELLIGENCE_ENGINE.md` §8).

### 4. Environments

- **local**: docker-compose (or local processes) + local Supabase project or hosted dev project.
- **staging**: full stack on the same providers as prod, using paper-only credentials.
- **production**: same stack; "production" initially still means paper trading only, until the live-trading gate (see `RISK_ENGINE.md` and `MVP_BUILD_PLAN.md`) is explicitly opened by a human decision, out of scope for MVP.
