# OmniTrade — Repository Reality Check & Phase 1 Plan (Historical Intelligence Platform)

**Role:** Lead Software Architect — inspection only. No code written, repository unmodified, no deployment proposed.
**Source of truth:** the attached repository (`omnitrade-legacy-engine-master`) for implementation facts; the Constitution/architecture docs for governing intent.
**Evidence tags:** `[VERIFIED]` read directly in code · `[INFERRED]` reasoned from code but not exhaustively traced · `[RECOMMENDATION]` · `[UNKNOWN]` not established by inspection.
**Scope note:** all cited paths are relative to `apps/api/`.

---

## A. Repository Reality Report

`[VERIFIED]` Monorepo: `apps/api` (FastAPI, Python) + `apps/web` (Next.js). Backend is large and real: **728 `.py` files**, **79 ORM models** (`app/models/`), **51 Alembic migrations** (`app/db/migrations/versions/`), **271 test files** (`tests/` with `unit/`, `integration/`, `support/`, `conftest.py`).

`[VERIFIED]` Entry point `app/main.py::create_app()` registers ~24 routers, including `decisions`, `live`, `live_crypto_orders`, `instant_trades`, `capital_campaigns`, `autonomous_capital_mandates`, `risk`, `arena`, `research`, `validation_runs`, `mission_control`, `exchange_connections`. This is far beyond the documented paper-MVP surface.

`[VERIFIED]` The service layer confirms the "live autonomous-campaign" system, not the documented paper MVP. Notable service packages: `autonomous_cycle/`, `orchestration/`, `capital_campaign_*`, `mandates/`, `risk/`, `decisions/`, `decision_intelligence/`, `decision_quality/`, `exchange_connections/providers/`, `live/broker_adapters/`, `replay/`, `backtesting/`, `research_agents/`, `tournament/`, `arena/`.

`[VERIFIED]` Providers registered in `services/exchange_connections/providers/registry.py`: `kraken_spot`, `coinbase_advanced` (behind an `ExchangeProviderClient` Protocol). A full live-execution stack exists as models: `live_crypto_order`, `live_accounting_record`, `live_execution_event`, `live_reconciliation_event`, `live_execution_quality_metric`, `live_trading_profile`, `venue_commissioning_run`, `canonical_proving_activation`.

`[VERIFIED]` Repository hygiene: three junk artifacts at repo root — `= [`, `operator`, `t \` — appear to be accidental shell-redirection commits. Harmless but worth removing (Stewardship / Article X).

---

## B. Documentation Drift Report

| # | Doc claims | Repository reality | Tag |
|---|---|---|---|
| B1 | `DATABASE_SCHEMA.md` §3a: DIE/COL/DQE tables are *"future placeholders … No new tables … introduced by this note."* | Tables exist as real models + migrations: `decision_record`, `decision_snapshot`, `decision_counterfactual_result`, `decision_quality_score`, `decision_alternative_action`, `decision_explainability_record`, `decision_experiment_recommendation`. | `[VERIFIED]` drift |
| B2 | `SYSTEM_ARCHITECTURE.md`/`REPO_STRUCTURE.md`: Alpaca/Binance data, `paper_accounts`, `execution_venue IN ('alpaca_paper','internal_sim')`, *"production means paper only."* | Live providers are `kraken_spot`/`coinbase_advanced`; live execution via `live_crypto_order` + accounting/reconciliation; autonomous mandates/campaigns; dry-run–gated real submission. | `[VERIFIED]` drift |
| B3 | `DATABASE_SCHEMA.md`: `assets` CHECK enums `('crypto','stock')`, exchange `('binance_us','alpaca')`. | Running provider registry is Kraken/Coinbase. Actual `models/asset.py` constraint contents not inspected. | provider-layer drift `[VERIFIED]`; asset CHECK contents `[UNKNOWN]` |
| B4 | `REPLAY_AGENT_INTERFACE.md`: *"single placeholder registration … no replay implementation."* | `services/replay/default_agent.py::DefaultReplayAgent.replay()` is implemented — it reconstructs a `DecisionPackage` and returns a `ReplayResult`. | `[VERIFIED]` drift (implementation exists) |
| B5 | HIP/HMRE use "replay" for **historical market simulation**. | Repo uses "replay" for **decision-package reconstruction** (`services/replay/`) and `replay_context` for **decision identity/evidence lineage** (`services/decisions/replay_context.py`). | `[VERIFIED]` terminology collision |

`[RECOMMENDATION]` Before implementation, reconcile the architecture/schema docs to the running system (a real `REPO_STRUCTURE`/`DATABASE_SCHEMA` refresh). The reuse plan below is code-grounded and does not depend on those stale docs.

---

## C. Canonical Production Path (autonomous decision cycle)

`[VERIFIED]` Traced through imports and call sites in `services/autonomous_cycle/orchestrator.py::run_autonomous_preview_cycle`:

1. **Mandate/authorization gate** — loads `AutonomousCapitalMandate` + version + authorization (`services/mandates/*`).
2. **Strategy proposal** — `_run_approved_strategy` (line 916): selects an active `Strategy` whose identity is in `version.allowed_strategy_versions`, loads candles (see F), builds `StrategyContext`, calls `strategy_registry.get(slug).generate_signal(context)` (line 1049).
3. **Risk evaluation** — `_evaluate_risk` (line 1132) → `services/risk/risk_engine.py::evaluate_signal_risk` (line 1186); then `services/risk/risk_persistence.py::persist_risk_decision`.
4. **Decision intelligence** — `_persist_decision_intelligence` (line 1246): `build_canonical_replay_context` (line 1289) → constructs immutable `DecisionRecord` (line 1327) + `DecisionSnapshot` (line 1401).
5. **Order preview** — `services/crypto_order_previews/service.py::create_crypto_order_preview` (line 548).
6. **Execution handoff** — `_attempt_autonomous_paper_execution_handoff` → `services/signals/execution_orchestrator.py::orchestrate_paper_signal_execution` (line 872); live path resolves `get_exchange_provider(connection.provider)` (line 360) + `get_decrypted_credentials_for_connection`.

`[INFERRED]` The runtime driver is `services/orchestration/continuous_pipeline_worker.py` (worker loop); `instant_trades.py` and `live_crypto_orders.py` are additional (operator-triggered) execution entry points, distinct from the autonomous cycle.

---

## D. Capability Classification

**Deterministic & pure — reusable in replay with no change**
- `[VERIFIED]` `risk_engine.py::evaluate_signal_risk` — no DB/session/`now()`/network (grep-confirmed empty). Account state enters via `RiskEvaluationContext`/request fields; `evaluation_time` is an explicit parameter. This is already the `AccountStateSource` seam.
- `[VERIFIED]` `strategies/base.py`: `StrategyContext` is a frozen dataclass over `MappingProxyType` candles/params; `Strategy` Protocol = `generate_signal(context) -> Signal`. Pure input → output.
- `[VERIFIED]` `backtesting/fills.py::simulate_buy_fill`/`simulate_sell_fill` — deterministic fill math (reusable for the Synthetic Broker).
- `[VERIFIED]` `decisions/replay_context.py::build_canonical_replay_context` — pure normalization with explicit `UNKNOWN`/`evidence_completeness` handling.

**Stateful / I/O — require an adapter for replay**
- `[VERIFIED]` Candle loading (direct DB query, no as-of boundary — see F).
- `[VERIFIED]` `DecisionRecord`/`DecisionSnapshot` persistence (DB writes).
- `[VERIFIED]` Exchange providers (`kraken_spot.py`, `coinbase_advanced.py`) — `httpx` + decrypted credentials.
- `[VERIFIED]` Orchestrator wall-clock reads (`datetime.now(timezone.utc)` pervasive — see F).

**Scaffold / partial — do not assume complete**
- `[VERIFIED]` `evaluate_signal_risk` docstring: *"Prompt 6.1 scaffold … Rule math and persistence behavior are intentionally deferred to later prompts."* Ordering/contract is real; some rule math may be incomplete. (Directly relevant to the current production BUY-rejection blocker.)
- `[VERIFIED]` `research_agents/llm_adapter/interface.py` — all methods `raise NotImplementedError()`; `OpenAIResearchAgent` status `PLANNED`.
- `[VERIFIED]` On the autonomous path, `DecisionSnapshot.ai_model_version="none"` (orchestrator line 1434), `configuration_version="unknown"` (`decisions/ingestion.py:491`).

---

## E. Reuse Matrix (HIP subsystem → existing code)

| HIP subsystem | Existing asset | Action | Tag |
|---|---|---|---|
| Canonical decision pipeline | `strategy_registry` + `generate_signal` + `evaluate_signal_risk` | **Reuse unchanged** (mandatory per HIP "shared financial logic") | `[VERIFIED]` fit |
| Synthetic Broker | `ExchangeProviderClient` Protocol (`providers/base.py`: `submit_order`, `preview_market_order`, `fetch_balances`, `fetch_price_evidence`, …) + `registry.py` + `backtesting/fills.py` | **New provider implementing the Protocol**, reusing fill math | `[VERIFIED]` seam |
| Account state source | `RiskEvaluationContext` / request fields (already parameterized) | **Reuse**; add a synthetic-ledger-backed builder | `[VERIFIED]` fit |
| Synthetic Decision Records | `DecisionRecord` + `DecisionSnapshot` (immutable, versioned, provenance-bearing) | **Reuse schema + add evidence-class/simulation provenance** (see H) | `[VERIFIED]` partial |
| Evidence consumption (DQE/DIE) | `decision_quality/`, `decision_intelligence/`, `decision_*` tables | **Reuse once `evidence_class` exists** | `[VERIFIED]` fit |
| Decision reconstruction | `services/replay/` (`DefaultReplayAgent`) | **Keep separate** (decision replay ≠ historical simulation) | `[VERIFIED]` |
| Point-in-time data | `candle` table + `replay_context.market_data_timestamp`/`candle_close_time` | **New gateway** over existing candles (add as-of boundary) | `[VERIFIED]` partial |
| Historical clock | none | **Net-new** | `[VERIFIED]` gap |
| Persistence namespace / isolation | single Postgres, no schema separation | **Net-new** | `[VERIFIED]` gap |
| Orchestrator / checkpoint / budget | `autonomous_cycle` (analogue only) | **Net-new** (pattern reuse) | `[INFERRED]` |

---

## F. Time & Future-Data Leakage Audit

`[VERIFIED]` **Candle load has no temporal boundary.** In `_run_approved_strategy`:
```
select(Candle).where(Candle.asset_id == asset.id, Candle.interval == request.strategy_interval)
    .order_by(Candle.open_time.desc()).limit(200)
```
It returns the latest 200 rows *in the table*. Safe in production (table only holds data up to now); **unsafe if reused for replay** against a table preloaded with full history — it would read the end of history, not the window up to simulated `T`. This is the primary leakage vector and it is real, not hypothetical.

`[VERIFIED]` **Wall-clock reads throughout the decision path** — `datetime.now(timezone.utc)` in `orchestrator.py` at lines 177, 246, 315, 522, 591, 758, 1014, 1310, 1345, 1459, 1477, 1517, including the `DecisionRecord.timestamp` (1345) and snapshot timeline. In replay these must come from a clock adapter.

`[VERIFIED]` **Latent strategy fallback to `now()`** — `strategies/helpers.py:15` and `strategies/ma_crossover.py:254` (`resolve_timestamp`): prefer candle `open_time`, fall back to `datetime.now()` only if candles are absent/malformed. Unreachable on the normal path (orchestrator HOLDs on <3 candles) but a determinism seam that must be closed for replay.

`[VERIFIED]` **Positive signals:** `evaluate_signal_risk` takes `evaluation_time` as an explicit input (no internal clock); `replay_context` already records `market_data_timestamp` and `candle_close_time`; the orchestrator annotates `current_incomplete_candle_excluded: True`.

`[VERIFIED]` **No `as_of`/`knowledge_cutoff` concept exists anywhere** (grep across models + migrations empty). The gateway must introduce it and **fail closed** when it cannot be guaranteed.

---

## G. Execution Isolation Audit

`[VERIFIED]` Real `submit_order` call sites (the live boundary): `live_crypto_orders.py:2326`, `instant_trades.py:540`, `live/venue_commissioning.py:554`. Each requires a registry-resolved `provider` + decrypted `credentials` + `environment`.

`[VERIFIED]` Existing gates: `config.py::live_crypto_dry_run_enabled` (line 87); `OT_KRAKEN_SANDBOX_MOCK_MODE` env (`kraken_spot.py:41`); a dry-run boundary guard asserting `dry_run` + `submission_skipped` (`orchestration/automatic_package_inspection.py:424`), with `submission_skipped`/`submission_skip_reason` recorded on orders.

`[VERIFIED]` **Single Postgres, no schema/namespace separation** (`config.py::database_url`; no `schema=`/`search_path` in `db/session.py`/`db/base.py`).

`[RECOMMENDATION]` Isolation today is **config- and credential-gated, not mode-structural** — the same registry/providers/tables are reachable from any code path. For HIP the isolation must be structural: `HISTORICAL_REPLAY` bound to a credential-less Synthetic Broker + a separate persistence namespace, with an `IsolationGuard` that **refuses to start** if a live provider or production namespace is bound.

---

## H. Decision Evidence & Provenance Audit

`[VERIFIED]` `DecisionRecord` (`models/decision_record.py`): carries `source_lineage` (JSONB) + `field_provenance` (per-field JSONB); **immutability enforced** via SQLAlchemy `before_update`/`before_delete` listeners that raise. Rich fields (regime, indicators, supporting/opposing strategies, risk_adjustments, pnl, outcome, ai_reflection, review_status). Relationships to `decision_snapshot`, `explainability_records`, `counterfactual_results`, `quality_scores`, `alternative_actions`.

`[VERIFIED]` `DecisionSnapshot` (`models/decision_snapshot.py`): immutable (event-enforced); five version-pin fields present — `parameter_set_version`, `strategy_version`, `ai_model_version`, `decision_engine_version`, `configuration_version`; full point-in-time context (`ohlcv_context`, `indicators`, `generated_features`, `market_regime`, `volatility`, `risk_inputs`, `current_position_state`, `open_trades`, `portfolio_exposure`).

`[VERIFIED]` **Missing for HIP:** no `record_origin`, `evidence_class`, `simulation_id`, `replay_branch_id`, `knowledge_cutoff_timestamp`, or `dataset_version` anywhere (grep empty). **Every existing record is implicitly `PRODUCTION_LIVE`.** Synthetic records would be indistinguishable from production without these — the single most important additive gap, and the crux of the isolation design fork in M.

---

## I. AI Determinism Audit

`[VERIFIED]` The trading decision path imports **no AI/LLM** — grep across `autonomous_cycle`, `signals`, `strategies`, `risk`, `decisions` is empty. There are **no `openai`/`anthropic` SDK imports anywhere** in `app/`.

`[VERIFIED]` LLM code is confined to the research laboratory: `LLMResearchAgentAdapter` methods all `raise NotImplementedError()`; `OpenAIResearchAgent` (`research_agents/openai/agent.py`, model `gpt-4o-mini`) is registered `PLANNED` and gated by `is_available()`. It generates strategy *hypotheses/critiques*, never trade decisions.

`[VERIFIED]` On the live path `ai_model_version="none"`.

**Conclusion:** Phase 1 replay of the trading path is **deterministic without excluding any LLM** — this revises the more conservative stance in the prior review. The only determinism seam is the two `now()` fallbacks in F.

---

## J. Existing Replay & Backtesting Audit

`[VERIFIED]` `services/replay/`: `DefaultReplayAgent.replay(db, decision_package_id)` reconstructs a stored decision into a `DecisionPackage` and returns a deterministic (`uuid5`) `ReplayResult`. Read-only **decision reconstruction** — matches `REPLAY_AGENT_INTERFACE.md` in spirit, and is *not* historical market simulation.

`[VERIFIED]` `services/backtesting/engine.py`: `BacktestEngine.run(candles)` iterates a *provided* candle list; deterministic fills/metrics; it **does not run the risk/AI/decision pipeline**. Reusable component (fills), not the replay path.

**Conclusion:** neither is the HIP's historical market replay. The historical-simulation engine is net-new but composes the canonical pipeline (D) with the existing fill math and a new clock/gateway/broker/ledger.

---

## K. Smallest Golden Historical Path (vertical slice)

**Goal:** prove the real production decision pipeline can run through historical time for **one asset, candle-only, one window**, deterministically, leak-proof, production-isolated, with fully provenanced synthetic Decision Records.

**Reused unchanged:** `strategy_registry` + `generate_signal` + `StrategyContext`; `evaluate_signal_risk`; `backtesting/fills.py`; `DecisionRecord`/`DecisionSnapshot` schema (extended per H).

**Net-new (minimal):**
- `RunMode`/`EvidenceContext` threaded through the slice to select adapters.
- `HistoricalClock` — deterministic simulated time.
- `PointInTimeDataGateway.get_candles(asset, interval, as_of=T)` over the existing `candle` table — **never returns rows with `open_time`/`close_time > T`**.
- `SyntheticBroker` implementing `ExchangeProviderClient` (no credentials; deterministic fill via `fills.py`).
- `SyntheticLedger` + a synthetic `AccountStateSource` feeding `RiskEvaluationContext`.
- `IsolationGuard` — refuses to start if a live provider/production namespace is bound.
- `SimulationOrchestrator` (minimal loop: init → tick → decide → fill → advance → record).
- Synthetic Decision Record writer stamping `record_origin`, `evidence_class=HISTORICAL_POINT_IN_TIME`, `simulation_id`, `knowledge_cutoff_timestamp`, `dataset_version`, in an **isolated persistence namespace**.

**Definition of done:** identical config+seed → identical decision sequence and ledger; the leakage test proves no future candle influences the decision at `T`; zero real orders; zero writes to production tables.

---

## L. Commit-by-Commit Phase 1 Plan (bounded units — no code emitted here)

1. **Docs/ADR stubs (not finalized):** draft ADR-0008 Operating Modes & Adapters, ADR-0009 replay-vs-simulation naming, ADR-0010 Synthetic Evidence Provenance & Isolation — **held pending authorization** (M). Docs only.
2. **Replay-namespace migration:** create isolated `simulation`, synthetic decision-record, and synthetic-ledger tables (or schema) with provenance columns. **No changes to production tables.** (Introduces an Alembic migration.)
3. **`RunMode`/`EvidenceContext` + adapter interfaces + `IsolationGuard`** (interfaces + guard; production path untouched, defaults to `PRODUCTION`).
4. **`HistoricalClock` + `PointInTimeDataGateway`** (candle-only, as-of boundary) + close the two `now()` strategy fallbacks (F) behind the clock.
5. **`SyntheticBroker`** (implements `ExchangeProviderClient`, reuses `fills.py`) + **`SyntheticLedger`** + synthetic `AccountStateSource`.
6. **`SimulationOrchestrator`** minimal loop + synthetic Decision Record writer (full provenance, `evidence_class`).
7. **Tests:** end-to-end single-asset run; determinism (same seed → identical); **leakage** (gateway never returns `>T`; poisoned-future candle never influences decision at `T`); **isolation** (replay cannot bind a live provider; zero production writes; zero real `submit_order`); ledger correctness; provenance completeness (fail-closed on missing fields). Precedent harness exists: `tests/unit/services/risk/`, `tests/unit/services/backtesting/`, `tests/integration/`.
8. **Docs refresh:** architecture addendum for the mode abstraction; update `00_PROJECT_STATE.md` roadmap; `06_NEXT_SESSION.md` handoff.

---

## M. Unresolved Decisions Requiring Authorization

1. **Milestone go/no-go (governance).** `02_DECISIONS.md` ("Production Before Expansion") and `06_NEXT_SESSION.md` prioritize the First Autonomous Profit blocker (Risk Engine rejecting BUYs). Phase 1 is production-isolated and can double as a deterministic harness to investigate that rejection (`evaluate_signal_risk` is already pure and reproducible). **Confirm whether Phase 1 proceeds on that basis, or is held.** Not my call.
2. **Isolation design fork (the pivotal one).** Synthetic evidence can live either as (a) **new nullable provenance columns on the existing `decision_records`/`decision_snapshots`** (maximises DQE/DIE reuse; risks contamination unless every read filters on `evidence_class`), or (b) **separate synthetic tables in a separate namespace** (maximises isolation; requires a read-path that unions/branches by origin). Recommendation leans (b) for Phase 1 (structural isolation first), but this is a genuine architectural decision needing sign-off.
3. **Namespace mechanism:** separate Postgres schema vs separate database vs origin-tagged tables in the same schema. `[UNKNOWN]` which best fits the current deploy/VPS topology — needs your input.
4. **Evidence-class enum:** reconcile the two docs' differing hierarchies into one canonical `evidence_class` set (ADR-0010).
5. **ADR finalization:** ADR-0008/0009/0010 are **not finalized** pending the above.

---

*Stopping here per instruction: architecture report + Phase 1 plan only. No code written, repository unmodified, no deployment proposed, ADRs not finalized.*
