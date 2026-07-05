# MVP_BUILD_PLAN.md

## OmniTrade Legacy Engine — MVP Build Plan

### Guiding Rule

No phase begins until the previous phase's exit criteria are met. Every phase produces working, tested code — not just stubs — before moving on. This mirrors the project's core philosophy: process over speed (`PROJECT_VISION.md`).

---

### Phase 0 — Repo Scaffold
**Goal:** A running skeleton with no business logic.
- Monorepo structure (`/frontend`, `/backend`, `/workers`) per `COPILOT_PROMPT_PACK.md` Prompt 1.
- Database schema + migrations fully implemented per `DATABASE_SCHEMA.md` (Prompt 2).
- Local dev environment (docker-compose or equivalent) runs frontend + backend + Postgres.
- `.env.example` complete; secrets strategy documented (env vars only, per `SYSTEM_ARCHITECTURE.md` §2.11).
- **Exit criteria:** `docker-compose up` (or equivalent) yields a frontend that can hit a backend health endpoint backed by a real Postgres schema.

### Phase 1 — Data Ingestion
**Goal:** Real market data flowing into the database.
- Binance/Binance.US client + Alpaca client implemented per `DATA_SOURCES.md` (Prompt 3).
- Historical backfill script functional for at least 2 crypto pairs and 2 stocks.
- Scheduled recent-candle ingestion job running reliably.
- Ingestion failures visibly logged to `audit_log`.
- **Exit criteria:** `candles` table contains at least 1 year of daily data and 30 days of intraday (e.g., 15m) data for the seed assets, with no duplicate rows and documented data-source labels.

### Phase 2 — Chart UI
**Goal:** Users can see real market data in the browser.
- Markets page implemented per `UI_SPEC.md` §2.2 (Prompt 4).
- Candlestick rendering, interval switching, and at least one indicator overlay (MA) working against real ingested data.
- **Exit criteria:** A non-technical family member can open the app, pick BTCUSDT or AAPL, and view a correct, readable candlestick chart with switchable intervals.

### Phase 3 — Backtesting
**Goal:** Strategies can be objectively evaluated against history.
- All six MVP strategy modules implemented per `STRATEGY_ENGINE.md` §2 (Prompt 5).
- Event-driven backtesting engine functional, producing metrics matching `DATABASE_SCHEMA.md` §2.5.
- Backtests page implemented per `UI_SPEC.md` §2.4.
- **Minimum activation criteria for any strategy to proceed to Phase 5 paper trading** (documented here as the canonical threshold, referenced by `STRATEGY_ENGINE.md` §3):
  - Backtested across at least 2 distinct market regimes/time periods.
  - Max drawdown in backtest does not exceed the account's configured max drawdown risk limit.
  - Sharpe-like ratio and win rate are reviewed and explicitly accepted by a human (no fully automatic promotion).
- **Exit criteria:** Each of the 6 strategies has at least one completed backtest with stored metrics, viewable and comparable in the UI.

### Phase 4 — Strategy Parameters
**Goal:** Parameters are adjustable and auditable from the UI, not just code.
- Strategy Lab page implemented per `UI_SPEC.md` §2.3 (Prompt 6).
- Parameter set creation, backtest-from-parameter-set flow, and audited "promote to active" flow all functional.
- **Exit criteria:** A user can change `fast_period`/`slow_period` on `ma_crossover` in the UI, save it as a new parameter set, run a backtest against it, and see the result — all without touching code, and all logged to `audit_log`.

### Phase 5 — Paper Trading
**Goal:** Strategies trade automatically against paper accounts using live/recent data.
- Alpaca paper execution + internal crypto simulator implemented per `SYSTEM_ARCHITECTURE.md` §2.5 (Prompt 7).
- Scheduled signal-generation loop running for active strategies.
- Paper Trading and Signals pages implemented per `UI_SPEC.md` §2.5–2.6.
- **Exit criteria:** At least one strategy runs unattended for 5+ consecutive trading days against a paper account, producing trades with correct P&L accounting and no missed/duplicated executions.

### Phase 6 — AI Signal Review
**Goal:** Every signal and trade carries a grounded explanation; regime/confidence/allocation are live.
- Regime classifier, signal scorer, allocator, explainer, and post-trade review engine implemented per `AI_LAYER.md` (Prompt 8).
- AI Review page implemented per `UI_SPEC.md` §2.8, including the mandatory human approve/dismiss step for any AI recommendation.
- **Exit criteria:** Every non-`hold` signal generated during Phase 5's paper-trading run (retroactively backfilled if needed) has a stored regime tag, confidence score, and human-readable explanation; a sample of explanations is manually reviewed for accuracy against the underlying data.

### Phase 7 — Risk Engine
**Goal:** No trade can occur without passing a fully enforced, tested risk gate.
- Full risk engine implemented per `RISK_ENGINE.md` (Prompt 9), integrated into the signal generation loop ahead of execution.
- Risk Monitor page implemented per `UI_SPEC.md` §2.7.
- Kill switches (account + global) tested end-to-end, including re-arm flow.
- **Exit criteria:** A deliberate test scenario (e.g., simulated large loss) correctly trips the daily loss limit and blocks further trades; a deliberate data-gap test correctly triggers a no-trade zone; a manual kill-switch trip correctly halts all trading and requires explicit human re-arm to resume.

### Phase 8 — Deployment
**Goal:** The system runs reliably outside of a local machine.
- Frontend deployed to Vercel; backend + workers deployed to the chosen provider (Railway/Fly.io/Render) per `COPILOT_PROMPT_PACK.md` Prompt 10.
- Supabase production project provisioned with migrations applied.
- Scheduled workers (ingestion, signal generation, post-trade review) running on the deployed environment on their intended cadence.
- Health-check and basic uptime monitoring in place.
- **Exit criteria:** The full pipeline (ingestion → chart UI → backtesting → paper trading → AI review → risk gating) runs unattended in a deployed "production" (still paper-only) environment for at least one full week without manual intervention, with all audit logs intact and reviewable.

### Future Phase — Decision Intelligence Engine (Not Scheduled)
**Goal:** Not yet scheduled into a numbered MVP phase. The Decision Intelligence Engine (`DECISION_INTELLIGENCE_ENGINE.md`) is a permanent foundational subsystem, but its dedicated implementation work — Decision Record schema, recording/query services, and the Decision Explorer/Timeline/Detail UI pages — begins only after Phase 8's MVP is validated and a dedicated phase is explicitly planned.
- In the meantime, Phases 5–7 should continue populating `signals`, `model_outputs`, and `risk_events` completely and consistently (per `DATABASE_SCHEMA.md` §3a), since these are the most likely source data the DIE's Decision Records will be built from once implemented.
- The DIE's Counterfactual Outcome Ledger (COL, `DECISION_INTELLIGENCE_ENGINE.md` §8) is included in this same future, unscheduled phase — it is a core subsystem of the DIE, not a separate feature with its own timeline. When this future phase is eventually planned, COL's version 1 should be scoped narrowly and lightly: BTC only, evaluated once per minute, three horizons (15 minutes, 1 hour, 24 hours), a small feature snapshot, and no heavy compute — expansion to more assets, more horizons, higher frequency, and richer features is explicitly deferred to later versions (`DECISION_INTELLIGENCE_ENGINE.md` §8.7–§8.8).
- No Copilot prompts, schema migrations, or endpoints for the DIE or the COL should be generated until this future phase is formally scheduled.

---

### Explicit Constraints Across All Phases

- **No live real-money trading in MVP** — every execution path in Phases 5–8 is paper-only; live trading is out of scope and requires a separate, explicitly-approved future initiative (see `RISK_ENGINE.md` §5, `PROJECT_VISION.md` §5).
- **No claims of guaranteed profit** anywhere in the UI, docs, or generated explanations.
- **Every trade must be explainable** — enforced technically by Phase 6's exit criteria and structurally by the AI layer's fail-closed behavior (`AI_LAYER.md` §5).
- **Every signal must be logged** — including `hold` and `risk_rejected` signals, per `DATABASE_SCHEMA.md` §2.7 and `UI_SPEC.md` §2.6.
- **Every strategy must be backtested before paper trading** — enforced by the Phase 3 minimum activation criteria feeding into Phase 5.
- **Prioritize correctness, safety, and extensibility over speed** — reflected in the exit criteria above requiring multi-day unattended runs and deliberate failure-mode testing before a phase is considered complete.
- **Every decision leaves a trace the future Decision Intelligence Engine can build from** — MVP phases don't implement the DIE itself, but Phases 3/5/6/7 must not take shortcuts that would make `signals`, `model_outputs`, or `risk_events` incomplete or inconsistent, since that data is the DIE's eventual foundation (`DECISION_INTELLIGENCE_ENGINE.md` §8).
