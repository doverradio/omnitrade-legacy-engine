# MVP_BUILD_PLAN.md

## OmniTrade Legacy Engine — MVP Build Plan

### Guiding Rule

No phase begins until the previous phase's exit criteria are met. Every phase produces working, tested code — not just stubs — before moving on. This mirrors the project's core philosophy: process over speed (`PROJECT_VISION.md`).

---

### Phase 0 — Repo Scaffold
**Goal:** A running skeleton with no business logic.
- Monorepo structure (`apps/web`, `apps/api`, `packages/shared`) per `REPO_STRUCTURE.md` and `COPILOT_PHASE_0_PROMPTS.md` Prompts 0.1–0.3.
- Migration pipeline proven end-to-end with an initial empty baseline migration (no tables yet) per `COPILOT_PHASE_0_PROMPTS.md` Prompt 0.4 — full schema implementation against `DATABASE_SCHEMA.md` begins in Phase 1 (`COPILOT_PHASE_1_PROMPTS.md` Prompt 1.1 onward) and continues incrementally as each phase needs its tables.
- Local dev environment (docker-compose or equivalent) runs frontend + backend + Postgres.
- `.env.example` complete; secrets strategy documented (env vars only, per `SYSTEM_ARCHITECTURE.md` §2.11).
- **Exit criteria:** `docker-compose up` (or equivalent) yields a frontend that can hit a backend health endpoint backed by a real Postgres connection (schema itself is still empty at this point — see migration note above).

### Phase 1 — Data Ingestion
**Goal:** Real market data flowing into the database.
- `assets` and `candles` tables implemented via migration, and Binance/Binance.US client + Alpaca client implemented per `DATA_SOURCES.md` and `COPILOT_PHASE_1_PROMPTS.md` Prompts 1.1–1.9.
- Historical backfill script functional for at least 2 crypto pairs and 2 stocks.
- Scheduled recent-candle ingestion job running reliably.
- Ingestion failures visibly logged to `audit_log`.
- **Exit criteria:** `candles` table contains at least 1 year of daily data and 30 days of intraday (e.g., 15m) data for the seed assets, with no duplicate rows and documented data-source labels.

> **Note on "(Prompt N)" references below (Phases 2–8):** These point to `COPILOT_PROMPT_PACK.md`, the original prompt pack written before `REPO_STRUCTURE.md` and the per-phase `COPILOT_PHASE_0_PROMPTS.md`/`COPILOT_PHASE_1_PROMPTS.md` pattern were introduced. Phases 0 and 1 now have dedicated, up-to-date prompt files and no longer rely on `COPILOT_PROMPT_PACK.md`. Phases 2–8 have not been implemented yet and still only have `COPILOT_PROMPT_PACK.md`'s original prompts to work from — before starting each of those phases, a dedicated `COPILOT_PHASE_N_PROMPTS.md` should be authored following the Phase 0/1 pattern, using `apps/web`/`apps/api` (per `REPO_STRUCTURE.md`) rather than `COPILOT_PROMPT_PACK.md`'s original `/frontend`/`/backend`/`/workers` folder references, which are superseded. This document is not the place to generate those prompts (see `HANDOFF_TO_COPILOT.md`); the note exists so the "(Prompt N)" citations below aren't mistaken for current, ready-to-use instructions.

### Phase 2 — Strategy Framework
**Goal:** Establish strategy architecture and activation discipline before execution phases.
- Strategy interfaces, registry, and module boundaries implemented per `STRATEGY_ENGINE.md`.
- Strategy metadata and activation lifecycle wiring established with audit-compatible transitions.
- Initial strategy readiness checks are in place so only validated strategies proceed into backtesting.
- **Exit criteria:** MVP strategies are registered and callable through the framework with deterministic, test-covered signal generation contracts.

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

### Phase 4 — Research Workspace
**Goal:** Deliver research-oriented UI/UX for strategy tuning and comparison.
- Strategy Lab page implemented per `UI_SPEC.md` §2.3 (Prompt 6).
- Parameter set creation, backtest-from-parameter-set flow, and audited "promote to active" flow all functional.
- Markets/backtest research workflows are coherent for non-destructive experimentation.
- **Exit criteria:** A user can modify strategy parameters, run and compare backtests, and review results end-to-end without touching code, with all state changes audited.

### Phase 5 — Paper Trading
**Goal:** Strategies trade automatically against paper accounts using live/recent data.
- Alpaca paper execution + internal crypto simulator implemented per `SYSTEM_ARCHITECTURE.md` §2.5 (Prompt 7).
- Scheduled signal-generation loop running for active strategies.
- Paper Trading and Signals pages implemented per `UI_SPEC.md` §2.5–2.6.
- **Exit criteria:** At least one strategy runs unattended for 5+ consecutive trading days against a paper account, producing trades with correct P&L accounting and no missed/duplicated executions.

### Phase 6 — Risk Engine
**Goal:** No trade can occur without passing a fully enforced, tested risk gate.
- Full risk engine implemented per `RISK_ENGINE.md` (Prompt 9), integrated into the signal generation loop ahead of execution.
- Risk Monitor page implemented per `UI_SPEC.md` §2.7.
- Kill switches (account + global) tested end-to-end, including re-arm flow.
- **Exit criteria:** A deliberate test scenario (e.g., simulated large loss) correctly trips the daily loss limit and blocks further trades; a deliberate data-gap test correctly triggers a no-trade zone; a manual kill-switch trip correctly halts all trading and requires explicit human re-arm to resume.

### Phase 7 — Decision Intelligence Foundation
**Goal:** Stand up the first implementation slice of the Decision Intelligence Engine as an observational subsystem.
- Implement Decision Record + Decision Snapshot foundations per `DECISION_INTELLIGENCE_ENGINE.md` and ADR-0002/ADR-0004.
- Implement foundational retrieval/review endpoints and basic Decision Explorer workflows without influencing real-time decision routing.
- Keep all Decision Intelligence outputs advisory and reviewable; no automatic strategy/risk mutation.
- **Exit criteria:** Decision records are written for evaluated signals (including rejected/hold outcomes where applicable), snapshots are immutable and reproducible, and review/query workflows are validated end-to-end.

### Phase 8 — Decision Arena
**Goal:** Introduce comparative evaluation workflows on top of established Strategy, Portfolio, Risk, and Decision Intelligence evidence.
- Implement Decision Arena surfaces and services for structured comparison across strategies/versions in paper context.
- Ensure evaluation dimensions include risk discipline, explainability, and decision quality, not profit alone.
- Maintain strict boundary: Decision Arena consumes evidence; it does not bypass risk gates or execute trades directly.
- **Exit criteria:** Decision Arena comparisons are reproducible, auditable, and grounded in existing paper/risk/decision evidence pipelines.

### Future Phase — Live Trading (Not Scheduled)
**Goal:** Remains explicitly future scope. Live trading is a deployment mode, not a foundational engine.
- Live enablement requires explicit human approvals, separate live account types, and independent operational readiness review.
- No live-routing implementation is allowed in MVP phase work.

### Future Capability Note — AI Signal Review and Deployment Hardening
AI review depth and production deployment hardening remain required capabilities, but they are treated as cross-phase readiness tracks rather than standalone numbered phases in this sequence. They should be scheduled within the active phase plan without violating architectural boundaries.

### Future Capability Note — Counterfactual Outcome Ledger (COL) and Decision Quality Engine (DQE)
The COL (`DECISION_INTELLIGENCE_ENGINE.md` §8) and DQE (§8a) remain part of Decision Intelligence evolution and may begin in a constrained form within or after Phase 7 as explicitly planned.
- In the meantime, Phases 5–7 should continue populating `signals`, `model_outputs`, and `risk_events` completely and consistently (per `DATABASE_SCHEMA.md` §3a), since these are the most likely source data the DIE's Decision Records will be built from once implemented.
- The DIE's Counterfactual Outcome Ledger (COL, `DECISION_INTELLIGENCE_ENGINE.md` §8) is included in this same future, unscheduled phase — it is a core subsystem of the DIE, not a separate feature with its own timeline. When this future phase is eventually planned, COL's version 1 should be scoped narrowly and lightly: BTC only, evaluated once per minute, three horizons (15 minutes, 1 hour, 24 hours), a small feature snapshot, and no heavy compute — expansion to more assets, more horizons, higher frequency, and richer features is explicitly deferred to later versions (`DECISION_INTELLIGENCE_ENGINE.md` §8.7–§8.8).
- The DIE's Decision Quality Engine (DQE, `DECISION_INTELLIGENCE_ENGINE.md` §8a) is likewise included in this same future, unscheduled phase, and depends on the COL being implemented first — a Decision Quality Score cannot be computed until counterfactual outcomes exist for a decision. When planned, DQE work should implement the scoring dimensions and dashboard metrics in `DECISION_INTELLIGENCE_ENGINE.md` §8a.3/§8a.5 without shortcutting into a real-time confidence scorer (that role belongs to `AI_LAYER.md`'s existing Signal Confidence Scorer) or an automatic strategy/risk adjustment mechanism (DQE output is a human-reviewed diagnostic only, per §8a.6).
- No schema migrations or runtime endpoints for the COL or DQE should be generated until they are explicitly scheduled and ADR-checked. Per `docs/adr/README.md`, material scope expansion (e.g., DQE scoring methodology changes) should trigger an ADR check before implementation.

---

### Explicit Constraints Across All Phases

- **No live real-money trading in MVP** — every execution path in Phases 5–8 is paper-only; live trading is out of scope and requires a separate, explicitly-approved future initiative (see `RISK_ENGINE.md` §5, `PROJECT_VISION.md` §5).
- **No claims of guaranteed profit** anywhere in the UI, docs, or generated explanations.
- **Every trade must be explainable** — enforced technically by Phase 6's exit criteria and structurally by the AI layer's fail-closed behavior (`AI_LAYER.md` §5).
- **Every signal must be logged** — including `hold` and `risk_rejected` signals, per `DATABASE_SCHEMA.md` §2.7 and `UI_SPEC.md` §2.6.
- **Every strategy must be backtested before paper trading** — enforced by the Phase 3 minimum activation criteria feeding into Phase 5.
- **Prioritize correctness, safety, and extensibility over speed** — reflected in the exit criteria above requiring multi-day unattended runs and deliberate failure-mode testing before a phase is considered complete.
- **Every decision leaves a trace the future Decision Intelligence Engine can build from** — MVP phases don't implement the DIE itself, but Phases 3/5/6/7 must not take shortcuts that would make `signals`, `model_outputs`, or `risk_events` incomplete or inconsistent, since that data is the DIE's eventual foundation (`DECISION_INTELLIGENCE_ENGINE.md` §8).
- **Before starting any new phase or major subsystem, check whether the work changes or creates an architectural decision.** If yes, stop and confirm whether an ADR is required before writing code — see `docs/adr/README.md`. This applies to every phase in this plan, including ones not yet reached.
