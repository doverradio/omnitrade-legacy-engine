# DECISION_INTELLIGENCE_ENGINE.md

## OmniTrade Legacy Engine — Decision Intelligence Engine (DIE)

### Status: Core Architectural Subsystem

OmniTrade Legacy Engine is built around four permanent foundational engines:

1. **Market Intelligence Engine** — ingestion, candles, indicators, regime detection (spans `DATA_SOURCES.md`, `STRATEGY_ENGINE.md`'s regime/filter modules, `AI_LAYER.md`'s regime classifier).
2. **Strategy Evolution Engine** — strategy modules, parameter sets, backtesting, allocation, promotion lifecycle (spans `STRATEGY_ENGINE.md`, `AI_LAYER.md`'s allocator).
3. **Decision Intelligence Engine (DIE)** — the subject of this document: the platform's permanent memory and reasoning system.
4. **Portfolio Intelligence Engine** — paper account state, positions, risk posture, equity/performance tracking (spans `RISK_ENGINE.md`, `paper_accounts`/`trades` in `DATABASE_SCHEMA.md`).

This document defines the Decision Intelligence Engine (DIE) as a permanent architectural layer of OmniTrade Legacy Engine, standing alongside — not beneath — the Market Intelligence, Strategy Evolution, and Portfolio Intelligence engines described elsewhere in this doc set. The DIE's major subsystems, both covered in full below, are **Decision Records** (§1–§7, §9–§13) — the platform's structured memory of the decisions it actually made — and the **Counterfactual Outcome Ledger** (§8) — the platform's structured memory of what would have happened under the decisions it didn't make. Neither is optional; both are core to what "decision intelligence" means for this platform.

---

### 1. Vision

**Trade history tells you what happened. Decision memory tells you why.**

A conventional trading log records outcomes: side, quantity, price, P&L. That's necessary but shallow — it can tell you a strategy lost money in March, but not whether it lost money for a reason the strategy should have foreseen, a reason the risk engine correctly guarded against, or a reason nobody could have anticipated. Without the reasoning trail, every post-mortem starts from scratch.

The Decision Intelligence Engine exists to close that gap. It treats every point at which the platform *could have acted* — not just the points where it did — as a structured, explainable event worth preserving: the market context, the competing evidence, the confidence assigned, the risk adjustments applied, and eventually the outcome and what was learned from it. This is fundamentally different from ordinary logging:

- **Ordinary logging** answers "what happened" after the fact, in whatever shape the code happened to emit at the time. Log formats drift, fields get added inconsistently, and old logs are rarely structured well enough to query for patterns.
- **Decision memory** answers "what did the platform believe, and why, at the moment it acted or chose not to" — captured in a consistent, versioned schema from day one, specifically so it can be queried, compared, and learned from later.

Why this becomes a long-term competitive advantage: any trading platform can accumulate price history — it's a commodity, available from every data vendor. What no vendor can hand you is a multi-year record of *this specific platform's* reasoning, confidence calibration, and blind spots, tied to real (paper, then eventually live) outcomes. Over months and years, that record becomes something no competitor can replicate by buying data — it can only be built by operating the system faithfully and recording why it did what it did, every time. This is the asset referenced in `PROJECT_VISION.md`'s family-legacy horizon: the platform doesn't just get used, it gets *wiser*, and that wisdom is embodied in the DIE's accumulated Decision Records, not in any single strategy or model version.

---

### 2. Responsibilities

The Decision Intelligence Engine is responsible for:

- **Recording decisions** — every point where the platform evaluated whether to act, regardless of the outcome (trade taken, trade rejected, signal generated as `hold`).
- **Recording reasoning** — the specific, grounded rationale behind the decision, in the same spirit as `AI_LAYER.md`'s explainability requirement, but persisted as structured data rather than only a display string.
- **Recording confidence** — the AI layer's confidence score and its contributing factors at the moment of decision.
- **Recording supporting evidence** — which strategies, indicators, or regime signals argued *for* the decision taken.
- **Recording opposing evidence** — which strategies, indicators, or regime signals argued *against* it, and were overruled or outweighed — this is information ordinary trade logs never capture, and it's often the most valuable part of a post-mortem.
- **Recording risk adjustments** — every way the risk engine resized, delayed, or altered the decision from its raw form.
- **Recording execution decisions** — how and where the decision was carried out (venue, fill assumptions, timing).
- **Recording outcomes** — the eventual result once known: P&L, duration, whether a stop-loss or take-profit was hit, or (for rejected trades) what would have happened had the platform acted.
- **Recording post-trade analysis** — the AI post-trade review engine's structured findings tied to this specific decision.
- **Recording lessons learned** — distilled, reusable takeaways, whether AI-generated or human-annotated.
- **Recording AI reflections** — a dedicated space for AI-generated retrospective commentary distinct from the original real-time explanation, since hindsight often reveals things the original decision-time reasoning could not have known.
- **Recording counterfactual outcomes** — via the Counterfactual Outcome Ledger (COL, §8), tracking what would have happened under the actions *not* taken, so the platform learns from rejected trades and inaction, not only from what it actually did.

The DIE does not itself generate signals, evaluate risk, or execute trades — it is the **recording and retrieval layer** that sits alongside those engines, consuming their outputs and preserving them in queryable, structured form. This mirrors the existing architectural boundary in `AI_LAYER.md` §5, where the AI layer is advisory and never bypasses the risk engine: the DIE is observational and never influences a decision in real time — it only ever writes after a decision has been made, and reads to support later analysis.

---

### 3. Decision Lifecycle

```
Market Snapshot
      │  (candles, current indicators, recent volatility — captured at decision time)
      ▼
Feature Generation
      │  (indicator values, derived features strategies and the AI layer consume)
      ▼
Market Regime Detection
      │  (regime classifier output + confidence, per AI_LAYER.md §2.1)
      ▼
Strategy Evaluations
      │  (every active strategy's raw signal — including those that disagreed)
      ▼
Risk Engine
      │  (approve / resize / reject, per RISK_ENGINE.md §3 evaluation order)
      ▼
Decision Intelligence Record Created
      │  (the moment reasoning, evidence, and risk adjustments are frozen into a record —
      │   this happens for every outcome of the risk engine step, not only approvals)
      ├──────────────────────────────────────────┐
      ▼                                          ▼
Execution                          Counterfactual Outcome Ledger (COL)
      │  (paper fill, or explicit                │  (shadow BUY/SELL/WAIT outcomes spawned
      │   non-execution if rejected/held)         │   here, evaluated at fixed horizons —
      ▼                                          │   see §8; never executed with capital)
Trade Outcome                                     ▼
      │  (realized once the position is    Counterfactual Evaluations
      │   closed, or "no outcome" if             │  (per-horizon hindsight comparison
      │   never opened)                          │   and lesson tags, feeding back into
      ▼                                          │   the same record's outcome/lessons)
Post-Trade Review                                 │
      │  (AI_LAYER.md §2.5's post-trade                │
      │   review engine, now writing                   │
      │   into the same record)                        │
      ▼                                                 │
AI Reflection                                           │
      │  (a later, hindsight-informed commentary        │
      │   pass — distinct from the original explanation)│
      ▼                                                 │
Knowledge Base ◄─────────────────────────────────────────┘
      │  (the accumulated, queryable body of Decision Records — see §6/§7 — 
      │   now including counterfactual evaluations from the COL, §8)
```

Two properties of this lifecycle are load-bearing:

- **A Decision Record is created at the risk engine step for every signal, not only for executed trades.** A rejected or held signal is just as valuable to the knowledge base as an executed one — arguably more valuable, since "what did we correctly avoid, and what did we incorrectly avoid" are exactly the questions §6 is built to answer.
- **The record is not closed at execution.** It stays open — linked, versioned, appendable — through outcome, post-trade review, and AI reflection, which may happen hours, days, or (for reflection passes) much later.
- **The COL branches off immediately at record creation, independent of whether execution happens.** Shadow outcome tracking does not wait for or depend on the real trade's outcome — it runs in parallel, on its own fixed horizons, and merges its findings back into the same Decision Record (see §8 for the full COL design).

---

### 4. Decision Record Schema (Architectural, Not SQL)

A Decision Record is the unit of knowledge the DIE produces. Conceptually, one record aggregates data that in `DATABASE_SCHEMA.md`'s existing tables is currently spread across `signals`, `model_outputs`, `risk_events`, and `trades` — the DIE's job is to weave these into one coherent, retrievable narrative per decision, not necessarily to duplicate their storage (see §8 for how this reconciles with the existing schema).

**Core identity**
- `decision_id` — unique identifier for this record.
- `version` — schema/record version, since the shape of what's captured will evolve over the platform's lifetime; old records remain valid under the version they were written with.
- `timestamp` — moment the decision was evaluated (not necessarily the moment it was executed).

**Market context**
- `asset` — the asset this decision concerns.
- `timeframe` — the interval/timeframe the decision was evaluated on.
- `market_regime` — the regime classification and its confidence at decision time.
- `indicators` — the specific indicator values that fed into the decision (a snapshot, not a live reference, so the record remains meaningful even after those indicators are recalculated differently later).

**Signal & evidence**
- `generated_signals` — the raw signal(s) considered.
- `signal_strength` — the strategy-native strength/confidence.
- `confidence` — the AI layer's confidence score.
- `supporting_strategies` — which strategies/evidence argued for the action taken.
- `opposing_strategies` — which strategies/evidence argued against it, and by how much.

**Risk & sizing**
- `risk_adjustments` — every resize/delay/veto applied and why.
- `expected_risk` — the risk taken on if the decision proceeds (e.g., distance to stop-loss × size).
- `expected_reward` — the target/expected upside (e.g., distance to take-profit × size, or a strategy's typical reward profile).
- `position_size` — the final approved size, if any.

**Decision outcome (at decision time)**
- `trade_accepted` — boolean.
- `trade_rejected_reason` — populated when not accepted; references the specific risk rule or evidence balance that led to rejection.

**Execution & exit**
- `execution_details` — venue, fill price/time, fees/slippage assumptions applied.
- `exit_details` — how and when the position was closed, if it was opened.

**Outcome & review**
- `pnl` — realized profit/loss, dollar and percentage (per `SMALL_ACCOUNT_MODE.md` §3's dollar+percentage convention).
- `duration` — how long the position was held, if opened.
- `outcome` — a categorical summary (e.g., `win`, `loss`, `breakeven`, `not_taken`, `not_taken_would_have_won`, `not_taken_would_have_lost`).
- `post_trade_notes` — structured findings from the post-trade review engine.
- `lessons_learned` — distilled takeaways, AI- or human-authored.
- `ai_reflection` — later, hindsight-informed commentary, versioned separately from the original real-time `confidence`/reasoning so the two can be compared.
- `future_tags` — free-form and structured tags for later retrieval (e.g., `high_volatility_regime`, `overruled_dissent`, `fee_drag_high`).
- `confidence_calibration` — a computed comparison between the original confidence score and the realized outcome, feeding directly into the calibration analysis in §6.
- `review_status` — where this record stands in human review (e.g., `unreviewed`, `reviewed`, `flagged`).
- `human_notes` — free-text human annotation, distinct from AI-generated content.

This schema is intentionally described at the field/architecture level, not as SQL — see §8 for how it maps onto actual tables.

---

### 5. Explainability Layer

The Decision Intelligence Engine is what makes the following questions answerable — not just in principle, but as an actual query against real stored data, for any historical decision:

- Why did we buy?
- Why didn't we buy?
- Why wasn't the position larger?
- Why wasn't another strategy selected (i.e., why did the allocator or ensemble scorer favor one signal over a competing one)?
- Why was this confidence score assigned?
- What risks reduced the position size?
- What evidence opposed this trade?
- Why did we exit?

This is a direct extension of `AI_LAYER.md`'s existing explainability mandate (§2.4, §5) and `RISK_ENGINE.md`'s requirement that every risk decision be logged (§1) — the DIE is where those individually-logged explanations become a single, coherent, queryable narrative per decision rather than scattered rows across several tables that must be manually joined and interpreted after the fact.

---

### 6. AI Training Dataset

Once a meaningful volume of Decision Records exists, they become a supervised dataset the platform can mine for patterns no single trade would reveal. Representative queries this enables:

- Find all decisions with `confidence > 0.90` that resulted in `outcome = loss` — a direct signal that confidence calibration is miscalibrated in some identifiable condition.
- Find all `trade_rejected` decisions where `outcome = not_taken_would_have_won` — surfacing cases where the risk engine or a strategy was too conservative.
- Find strategies that appear in `opposing_strategies` for decisions that later resulted in `win` — identifying a strategy that consistently dissents from good trades (a candidate for allocator weight reduction, per `AI_LAYER.md` §2.3).
- Find market regimes where `confidence_calibration` shows a consistent gap between stated confidence and realized outcome — informing regime-specific confidence adjustments.
- Identify recurring `future_tags` combinations that co-occur with poor outcomes — surfacing failure patterns that aren't obvious from any single trade.

These are exploratory analysis patterns for MVP (human-run queries against the Decision Record store); nothing here implies automated retraining without human review, consistent with `AI_LAYER.md` §5's constraint that model changes never auto-apply.

---

### 7. Future Intelligence

As the Decision Record knowledge base grows, future AI models built on top of it (explicitly out of MVP scope, but architecturally anticipated) could:

- Recommend strategy parameter or allocation improvements grounded in specific historical decisions, not just aggregate backtest metrics.
- Detect a strategy's performance degrading in near-real-time by comparing recent decisions' confidence calibration against its historical baseline.
- Improve confidence estimation itself, by training on the gap between stated confidence and realized outcome across thousands of recorded decisions.
- Recommend new risk rules by mining patterns in `trade_rejected_reason` and `opposing_strategies` that correlate with avoided losses (validating existing rules) or missed gains (suggesting rule refinement).
- Discover hidden market regimes that the current regime classifier doesn't explicitly label, by clustering decisions with similar indicator/outcome profiles.
- Identify parameter drift — cases where a strategy's originally-backtested parameters no longer match the market conditions it's actually operating in, visible as a growing gap between expected and realized outcomes over time.

Any such future capability remains subject to the same human-in-the-loop constraint as the rest of the AI layer: recommendations, never automatic changes.

---

### 8. Counterfactual Outcome Ledger (COL)

**Status:** A core subsystem inside the Decision Intelligence Engine — not a separate optional feature.

#### 8.1 Purpose

Everything in §1–§7 is built to explain and learn from the decisions the platform actually made. That's necessary but incomplete: a platform that only studies its own actions can never learn whether its *inaction* was wise. If OmniTrade waits through a breakout that would have paid off, or correctly waits through a false breakout, neither event leaves a trace unless something is specifically built to record it.

The Counterfactual Outcome Ledger closes this gap. Its governing question is not the ordinary "did our trade make money?" — it is:

> **"What was actually the best decision that could have been made at that moment?"**

Every time the platform evaluates a market and arrives at a recommendation — BUY, SELL, or WAIT — the COL creates **shadow outcomes** for all three possible actions, not just the one chosen. None of these shadow actions are ever executed with real or paper capital; they exist purely as labeled data. A background process revisits each set of shadow outcomes at several fixed time horizons after the original decision, computes what actually would have happened under each action, and records whether the platform's real recommendation was the best one in hindsight.

This makes the COL the mechanism by which OmniTrade learns as much from the trades it *didn't* take as from the ones it did — turning every single market evaluation, not just every executed trade, into a permanent labeled training example.

#### 8.2 Relationship to the Rest of the DIE

The COL is not a parallel system — it is anchored to the Decision Record described in §4. Every Decision Record that includes a real recommendation (BUY/SELL/WAIT) spawns a linked set of shadow outcome evaluations. The COL populates fields that feed directly back into §4's `outcome`, `lessons_learned`, and `confidence_calibration` fields, and into the AI Reflection pass in §3's lifecycle — it is best understood as the mechanism that makes those fields honest about counterfactuals, not just about the path actually taken.

#### 8.3 Shadow Outcomes

For a given decision, the COL tracks three shadow actions:

- **Shadow BUY** — what would have happened had the platform bought at the decision point.
- **Shadow SELL** — what would have happened had the platform sold (or shorted, if/when that's ever supported) at the decision point.
- **Shadow WAIT** — what would have happened had the platform taken no position at all.

All three are tracked regardless of which one matches the real recommendation — including the shadow that duplicates the real action, since tracking it identically to the other two keeps the evaluation methodology consistent and comparable across all three.

Example, per the prompt that motivated this subsystem:
- Real decision: `WAIT`
- Shadow BUY — tracked.
- Shadow SELL — tracked.
- Shadow WAIT — tracked (this one mirrors the real outcome, and serves as a consistency check).

#### 8.4 Evaluation Horizons

A background job revisits each decision's shadow outcomes at configurable fixed horizons after the original decision timestamp. The full horizon set anticipated for the platform's mature state is:

- 5 minutes
- 15 minutes
- 1 hour
- 4 hours
- 24 hours

At each horizon, the COL computes, for every shadow action:

- **BUY outcome** — the hypothetical return had BUY been taken.
- **SELL outcome** — the hypothetical return had SELL been taken.
- **WAIT outcome** — the hypothetical return (typically ~0, minus any opportunity cost framing) had WAIT been taken.
- **Best action in hindsight** — whichever of the three produced the best outcome at this horizon.
- **Was the actual recommendation correct** — a boolean/graded comparison between the real recommendation and the hindsight-best action at this horizon.
- **Lesson tags** — a set of structured tags summarizing what this comparison reveals (see §8.5).

Because a single decision is evaluated at multiple horizons, a decision can be "correct" at one horizon and "incorrect" at another — e.g., WAIT may be correct at 15 minutes (avoiding a fakeout) but incorrect at 24 hours (missing a real breakout that developed slowly). This multi-horizon view is itself valuable data — see §8.7.

#### 8.5 Lesson Tags

Lesson tags are the COL's mechanism for turning a raw outcome comparison into a reusable, queryable label. Representative tags (extensible over time, not an exhaustive enum):

- `missed_breakout` — WAIT or SELL was chosen; BUY would have been meaningfully better in hindsight.
- `false_breakout` — BUY was chosen or would have looked attractive; WAIT or SELL was actually best, because the apparent move reversed.
- `entered_too_early` — the real action matched the eventually-correct direction, but a later entry point would have captured a better outcome.
- `exited_too_early` — relevant when a real position was closed; the shadow evaluation shows continuing to hold would have been better through this horizon.
- `wait_was_correct` — WAIT was the real recommendation and was also the hindsight-best action.
- `volatility_filter_saved_trade` — a volatility-filter-driven WAIT/reject avoided a worse outcome, confirming the filter earned its keep on this decision.
- `trend_filter_incorrect` — a trend-regime-driven suppression of a signal turned out to be wrong in hindsight at this horizon.
- `confidence_overestimated` — the AI layer's confidence score was high, but the real recommendation was not the hindsight-best action.
- `confidence_underestimated` — the AI layer's confidence score was low, but the real recommendation (or an alternative it downweighted) was in fact the hindsight-best action.

Lesson tags are attached per decision *per horizon*, since the same decision can earn different tags at different horizons (§8.4). They are designed to be aggregated later — e.g., "how often does `volatility_filter_saved_trade` occur" is itself a measure of whether that filter is pulling its weight.

#### 8.6 Scope Discipline: This Is Not a Backtesting Engine

The COL must not become a second, shadow backtesting system. It is explicitly scoped as a **lightweight, continuously-running companion process** to the live paper-trading/research platform, not a historical simulation engine:

- It only ever evaluates decisions that were actually made by the running platform, at the moment they were made — it does not replay arbitrary historical windows or generate synthetic decisions the way a backtest does (`STRATEGY_ENGINE.md`/backtesting engine remain the tool for "what if we changed this parameter over the last year").
- It uses a small, fixed feature snapshot per decision (§8.7's V1 scope), not the full indicator/feature surface a strategy or the AI layer might compute.
- Its background jobs are scheduled, bounded evaluations at fixed horizons — not an open-ended simulation loop.

If COL implementation work ever starts trending toward "let's also let it re-run strategies against alternate parameter sets" or "let's have it simulate multi-day scenarios," that is scope creep into backtesting territory and should be redirected back to `STRATEGY_ENGINE.md`'s existing backtesting engine instead.

#### 8.7 Version 1 Scope (Lightweight, Configurable)

V1 is deliberately narrow, per explicit design constraint:

- **Asset scope:** BTC only.
- **Evaluation frequency:** once per minute.
- **Horizons:** 15 minutes, 1 hour, 24 hours (a subset of the full §8.4 horizon list — 5 minutes and 4 hours are deferred to a later version).
- **Feature snapshot:** small — a handful of already-computed values (e.g., current price, the regime tag, the real recommendation and its confidence) rather than a full indicator recomputation. The COL should read from data the platform has already produced for the decision, not independently recompute a rich feature set.
- **Compute profile:** no heavy compute — this is a lightweight, always-on companion job, not a batch/GPU workload. If a proposed V1 implementation requires anything resembling a training run or large batch simulation, it has exceeded V1 scope.

**Explicit non-goals for V1:** multi-asset coverage, sub-minute evaluation frequency, the full 5-horizon set, rich/derived feature snapshots, and any use of shadow outcomes to automatically adjust live strategy behavior (COL output remains observational, feeding the same human-in-the-loop review pattern as the rest of the DIE — see `AI_LAYER.md` §5).

#### 8.8 Later Versions (Explicitly Deferred)

Once V1 is validated as lightweight and reliable in continuous operation, later versions can expand along independent axes without changing the core mechanism:

- **More assets** — beyond BTC, following the same asset registry already used elsewhere in the platform.
- **More frequent evaluation** — sub-minute cadence, if justified by observed value from V1's once-per-minute data.
- **More horizons** — restoring the full 5-minute and 4-hour horizons from §8.4.
- **Richer feature snapshots** — capturing more of the market/indicator context per decision, once the lightweight V1 has demonstrated the mechanism is worth the added compute.

None of this is scheduled into MVP phases (see `MVP_BUILD_PLAN.md`'s Future Phase note) — it is recorded here so V1, whenever it is implemented, is built with this expansion path in mind rather than needing to be redesigned for it later.

---

### 9. Database Impact (Architecture Only — No SQL)

The DIE introduces the following new conceptual tables, described at the architecture level. Exact column-level schema (including how these relate to or absorb the existing `signals`, `model_outputs`, and `risk_events` tables) is deferred to a future `DATABASE_SCHEMA.md` revision at implementation time.

- **Decision Records** — the central table implementing the schema in §4; one row per decision lifecycle instance (§3), whether or not it resulted in a trade.
- **Decision Evidence** — supporting and opposing evidence entries linked to a Decision Record (one-to-many), capturing which strategies/signals argued which way and by how much.
- **Decision Outcomes** — outcome data (P&L, duration, categorical outcome) linked to a Decision Record, populated once known — kept as a related table rather than inline columns since outcomes are populated asynchronously, often long after the record is first created.
- **Decision Reviews** — human review state and notes linked to a Decision Record (`review_status`, `human_notes`), separate from AI-generated content so human judgment is always distinguishable from AI judgment in the data.
- **AI Reflections** — hindsight-informed AI commentary linked to a Decision Record, versioned separately from the original real-time explanation captured at decision time.
- **Human Reviews** — a dedicated table for structured human review actions (e.g., approve/flag/annotate), distinct from free-text `human_notes`, to support the same human-in-the-loop review pattern already established for AI recommendations elsewhere in the platform (`AI_LAYER.md` §2.5, `RISK_AND_AUDIT_API_CONTRACTS.md`'s `GET /ai/review`).
- **Shadow Outcomes** — one row per shadow action (BUY/SELL/WAIT) per decision, linked to the originating Decision Record, per §8.3.
- **Counterfactual Evaluations** — one row per (decision, horizon) pair, linked to a Decision Record and its Shadow Outcomes, storing the per-horizon computed outcomes, hindsight-best action, correctness assessment, and lesson tags, per §8.4/§8.5.

This is expected to relate closely to, and likely partially subsume or reference, the existing `signals`, `model_outputs`, and `risk_events` tables from `DATABASE_SCHEMA.md` — the DIE is not necessarily a wholesale replacement of that schema, but the layer that ties those existing records together into the coherent per-decision narrative described in §4/§5. Reconciling this precisely is implementation-phase work, not an MVP architecture decision to finalize now.

---

### 10. API Impact (Architectural Placeholders Only)

The following endpoints are anticipated for a future phase and are documented here as placeholders — not specified in request/response detail, and not part of the current `API_CONTRACTS.md`/`RISK_AND_AUDIT_API_CONTRACTS.md` MVP surface:

- `GET /decisions` — list/browse Decision Records with filtering.
- `GET /decisions/{id}` — fetch a single Decision Record in full.
- `GET /decisions/search` — structured/pattern search across Decision Records (supporting the query patterns in §6).
- `GET /decisions/outcomes` — outcome-focused aggregate views.
- `GET /decisions/explanations` — explanation-focused views, likely superseding or subsuming the existing `GET /ai/explanations/:signal_id` endpoint once the DIE is implemented.
- `GET /decisions/reviews` — human review queue/history.
- `POST /decisions/review` — submit a human review action on a Decision Record.
- `GET /decisions/{id}/counterfactuals` — fetch the shadow outcomes and per-horizon counterfactual evaluations for a single decision, per §8.
- `GET /counterfactuals/lesson-tags` — aggregate/browse view over lesson tag frequency (e.g., "how often does `volatility_filter_saved_trade` occur"), supporting the kind of pattern-mining described in §6.

These are named and scoped now so future implementation work has a stable target; full contracts (request/response shapes, error states) will be defined in a dedicated `DECISION_INTELLIGENCE_API_CONTRACTS.md` when this engine moves from architecture to implementation.

---

### 11. UI Impact (Future Pages)

The following pages are anticipated for a future phase, extending the page set defined in `UI_SPEC.md` and `FRONTEND_PAGE_SPECS.md`:

- **Decision Explorer** — browse/filter the full Decision Record history.
- **Decision Timeline** — chronological, narrative view of decisions for an asset/strategy/account.
- **Decision Detail** — the full single-record view answering the explainability questions in §5.
- **Decision Compare** — side-by-side comparison of two or more decisions (e.g., two similar setups with different outcomes).
- **Decision Search** — structured search UI over `GET /decisions/search`.
- **AI Reflection Viewer** — surfaces hindsight-informed AI commentary distinctly from original real-time explanations.
- **Confidence Analytics** — visualizes confidence calibration over time, regimes, and strategies (directly surfacing the §6 calibration analysis).
- **Counterfactual Viewer** — a Decision Detail sub-view (or standalone page) showing the three shadow outcomes, their per-horizon results, the hindsight-best action, and accumulated lesson tags for a given decision, per §8.

These pages are not part of the MVP page set (`FRONTEND_PAGE_SPECS.md`'s 8 pages) and are not scheduled into `MVP_BUILD_PLAN.md`'s phases at this time — they are recorded here so the eventual UI work has an architectural home to build toward, and so that MVP pages (particularly Signals and AI Review) are built in a way that doesn't preclude this later expansion.

---

### 12. Relationships to Other Engines

- **Market Intelligence Engine** — supplies the market snapshot, features, and regime classification that seed each Decision Record (§3, steps 1–3), and the ongoing candle/price data the COL's background jobs read at each evaluation horizon (§8.4).
- **Strategy Evolution Engine** — supplies the strategy evaluations, supporting/opposing evidence, and (via the allocator) the weighting context behind "why this strategy was selected" (§5).
- **Risk Engine** — supplies every risk adjustment, rejection reason, and sizing decision recorded in a Decision Record (§3, step 5; §4's risk & sizing fields), and is itself a subject of COL lesson tags such as `volatility_filter_saved_trade`/`trend_filter_incorrect` (§8.5).
- **Portfolio Intelligence Engine** — supplies account-level context (equity, existing positions) relevant to sizing and risk decisions, and consumes Decision Outcomes to update its own performance views.
- **Paper Trading** — the current execution venue for decisions that are accepted; supplies `execution_details`/`exit_details`. The COL's shadow outcomes are explicitly never routed through Paper Trading's execution path — they are computed, not executed (§8.1).
- **Backtesting** — historical backtests are themselves a valuable (if lower-fidelity, since no real-time risk/AI pipeline runs during a vectorized backtest) source of Decision-Record-like data; a future enhancement could have the backtesting engine emit simplified Decision Records so backtested and live paper decisions live in the same queryable knowledge base. The COL itself remains scoped to live/paper decisions only (§8.6) and is not a backtesting mechanism.
- **Future Live Trading** — when live trading is eventually enabled (`RISK_ENGINE.md` §5), the same Decision Record schema and lifecycle apply unchanged — live trading does not get a separate reasoning/memory system, it inherits this one, including the COL's shadow-outcome tracking.

---

### 13. Design Principles

- **Every decision must be explainable.** Not just executed trades — every point where the platform evaluated whether to act.
- **No black-box trades.** If a decision cannot be traced back to specific evidence, confidence, and risk reasoning, something in the pipeline has failed its obligation to the DIE, not the other way around.
- **Every outcome teaches something.** A win confirms or calibrates; a loss reveals a gap; a correctly avoided trade validates caution; an incorrectly avoided trade reveals excess caution. None of these are discarded.
- **The platform should accumulate wisdom, not just data.** Raw logs are data. Structured, reviewable, queryable decision memory — connected to real outcomes — is wisdom. The DIE exists specifically to produce the latter.
- **Knowledge compounds over time.** The value of the Decision Record store is not linear in its size — patterns that are invisible at 100 records become clear at 10,000, and the platform's ability to reason about its own behavior improves as the record grows, without requiring any single trade to have been large or dramatic.
- **The Decision Intelligence Engine is the institutional memory of OmniTrade.** It is what allows the platform — and the family maintaining it — to say not just "here's what we did" years from now, but "here's what we believed, why, and what we learned," which is the actual asset `PROJECT_VISION.md`'s long-term legacy vision is built on.
- **The right question is not "did our trade make money," but "what was actually the best decision that could have been made."** The Counterfactual Outcome Ledger exists specifically to make this question answerable with real, computed data rather than intuition — every decision, taken or not, becomes a labeled example of what was actually possible at that moment.
- **Inaction is a decision, and it deserves the same scrutiny as action.** A WAIT that avoided a loss and a WAIT that missed a gain are both informative; the COL ensures neither is invisible to the platform's learning process.
- **Lightweight and continuous beats heavy and occasional.** The COL is designed to run forever in the background at low cost, not to become a periodic heavy analysis job — this is what allows every single decision, not just a sampled subset, to become a permanent labeled training example.
