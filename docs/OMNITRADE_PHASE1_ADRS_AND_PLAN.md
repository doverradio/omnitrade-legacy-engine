# OmniTrade — Phase 1 ADRs, Golden Historical Path & Risk Investigation

**Status:** Planning deliverable under operator authorization. No code written, repository unmodified, no deployment proposed, production Risk Engine unchanged.
**Evidence tags:** `[VERIFIED]` read in code · `[INFERRED]` reasoned but not exhaustively traced · `[RECOMMENDATION]` · `[UNKNOWN]`.
**Paths** are relative to `apps/api/`. Operator decisions from the authorization memo are baked in throughout.

---

# A. Four Finalized ADR Drafts

## ADR-0008 — Operating Modes and Adapter Boundaries

**Status:** Accepted (Phase 1)
**Context.** `[VERIFIED]` The canonical decision logic is already pure and mode-agnostic: `strategies/base.py::Strategy.generate_signal(StrategyContext) -> Signal` (frozen, no I/O) and `risk/risk_engine.py::evaluate_signal_risk(request, reference_price, context)` (no DB/clock/network; `evaluation_time` is an explicit input). What is hardwired to production lives *around* that logic in `autonomous_cycle/orchestrator.py`: the candle load (`select(Candle)…order_by(open_time.desc()).limit(200)`), wall-clock reads (`datetime.now(timezone.utc)` at ~12 sites), execution via `exchange_connections/providers/registry.py::get_exchange_provider`, and persistence via the single module engine in `db/session.py`.

**Decision.** Introduce an explicit `RunMode` and a fixed set of **adapter boundaries** that the financial logic is driven through. The permanent modes are `PRODUCTION`, `FORWARD_PAPER`, `HISTORICAL_POINT_IN_TIME`, `COUNTERFACTUAL` (`UNIT_TEST` is an evidence class, not a run mode — see ADR-0010). The adapter boundaries are:

- **Clock** — supplies simulated/real time; the financial logic and orchestration never call `datetime.now()` directly in historical mode.
- **PointInTimeDataGateway** — the only source of market data in historical mode (ADR-0011).
- **AccountStateSource** — supplies the account/portfolio inputs that `evaluate_signal_risk` already consumes via `RiskEvaluationRequest`/`RiskEvaluationContext`.
- **ExecutionProvider** — the existing `ExchangeProviderClient` Protocol; historical mode binds only the Synthetic Broker (ADR-0010).
- **PersistenceRouter** — selects the writable namespace/connection by mode (ADR-0010).
- **EvidenceClassifier** — stamps `evidence_class` + provenance on every record produced (ADR-0010).

**Phase 1 boundary (isolation-preserving).** Phase 1 does **not** refactor the production `autonomous_cycle/orchestrator.py` to route through adapters. It builds a **parallel** `HistoricalSimulationOrchestrator` that *composes the same pure functions* (`generate_signal`, `evaluate_signal_risk`, `backtesting/fills.py`) through historical adapters. The production lane keeps its exact current behavior; `RunMode.PRODUCTION` is the default and nothing on the live path changes. This directly honors the operator's requirement that Phase 1 not delay or weaken First Autonomous Profit.

**Consequences.** The pure functions are reused verbatim (no fork of financial logic, satisfying the HIP "shared logic across modes" rule). The production orchestrator is untouched. A later, separately-authorized phase may retrofit the production orchestrator onto the same adapters once the historical lane has proven them.

**Alternatives rejected.** (a) Refactor the production orchestrator now to be mode-parametric — rejected: touches the live path during the First Autonomous Profit push. (b) Copy/fork the decision logic for replay — rejected: violates shared-logic principle and guarantees drift.

---

## ADR-0009 — Decision Replay versus Historical Simulation Terminology

**Status:** Accepted
**Context.** `[VERIFIED]` "Replay" is already a load-bearing term in the repo with a *different* meaning than the HIP's: `services/replay/default_agent.py::DefaultReplayAgent.replay(decision_package_id)` reconstructs/re-evaluates an **existing** immutable Decision Package (read-only), and `services/decisions/replay_context.py::build_canonical_replay_context` builds the **identity/evidence lineage** of a recorded decision. The HIP uses "replay" for **chronological generation of new decisions** from point-in-time data — a genuinely different subsystem.

**Decision.**
- **"Decision Replay"** is reserved for reconstruction/re-evaluation of existing Decision Packages. `services/replay/` and `services/decisions/replay_context.py` keep their names and meaning.
- **"Historical Simulation"** denotes chronological generation of new decisions from point-in-time historical market data.
- The new subsystem is named **`historical_simulation`** (package, modules, tables, classes). No new artifact is named merely "replay". Tables use the `simulation_` prefix; modules live under `services/historical_simulation/`.

**Consequences.** Reviewers and future maintainers can tell reconstruction from generation by name alone. `evidence_class` values and provenance (ADR-0010) reinforce the distinction at the data layer.

**Alternatives rejected.** Overloading "replay" for both — rejected as the exact Article X (Stewardship) drift this ADR exists to prevent.

---

## ADR-0010 — Synthetic Evidence Provenance and Structural Isolation

**Status:** Accepted (Phase 1)
**Context.** `[VERIFIED]` `models/decision_record.py` and `models/decision_snapshot.py` are immutable (SQLAlchemy `before_update`/`before_delete` raise), carry `field_provenance`/`source_lineage` and the five version pins, but have **no** `record_origin`/`evidence_class`/`simulation_id`/`knowledge_cutoff`. `db/session.py` is a single engine on `settings.database_url`; `db/base.py` is a single `Base(DeclarativeBase)` shared by all 79 production models.

**Decision (per operator).**
1. **Separate synthetic tables in a separate simulation namespace.** Do **not** add nullable provenance columns to the production `decision_records`/`decision_snapshots`. New tables carry the `simulation_` prefix and bind to a **separate declarative base** (`SimulationBase`) so production Alembic/`metadata` never sees them and vice versa. Initial set: `simulation_run`, `simulation_decision_record`, `simulation_decision_snapshot`, `simulation_ledger_entry`, `simulation_fill`.
2. **Dedicated connection.** A `OT_SIMULATION_DATABASE_URL` setting with its own engine/sessionmaker. Historical workers receive **only** this writable target. Deployment-grade historical simulation must never write through the production `DATABASE_URL`. A separate *schema* inside an isolated local/CI test database is permitted for local/CI; deployment uses a separate *database*.
3. **Canonical evidence classes:** `PRODUCTION_LIVE`, `FORWARD_PAPER`, `HISTORICAL_POINT_IN_TIME`, `COUNTERFACTUAL`, `UNIT_TEST`. Phase 1 writes `HISTORICAL_POINT_IN_TIME`.
4. **Mandatory provenance** on every synthetic record (fail closed if any is missing): `simulation_id`, `branch_id` (nullable where not applicable), `dataset_id`, `dataset_version`, `knowledge_cutoff_timestamp`, `run_mode`, `strategy_version`, `risk_policy_version`, `decision_engine_version`, `execution_model_version`, `random_seed`, `created_at`.
5. **Structural distinguishability.** Production reads never union synthetic tables; synthetic reads never touch production decision tables. Immutability listeners mirror the production pattern on synthetic decision tables.

**Consequences.** Synthetic evidence is physically incapable of masquerading as production evidence. DQE/DIE consumption of synthetic evidence (a later phase) reads synthetic tables and must carry `evidence_class` through every aggregate.

**Alternatives rejected.** Nullable provenance columns on production tables (max reuse) — rejected by operator in favor of structural isolation.

---

## ADR-0011 — Historical Data Knowledge-Boundary Contract

**Status:** Accepted (Phase 1)
**Context.** `[VERIFIED]` The production candle load returns the latest 200 rows by `open_time desc` with **no as-of boundary** — safe in production, unsafe if reused for replay. No `knowledge_cutoff` concept exists anywhere. `[VERIFIED]` positive signal: `Candle` has `open_time` and `close_time`, and `replay_context` already records `market_data_timestamp`/`candle_close_time`.

**Decision.** Define the `PointInTimeDataGateway` contract as **fail-closed and caller-independent**:
- Every read takes an explicit `as_of=T` (simulated time from the Clock).
- A **completed OHLCV candle becomes visible only at or after its `close_time`.** The gateway must never return a candle with `close_time > T`, and must never return a still-forming candle (`open_time <= T < close_time`).
- Every returned row carries provenance (`dataset_id`, `dataset_version`, availability timestamp = `close_time`).
- If the boundary cannot be proven for a requested range (missing/ambiguous `close_time`, dataset gap), the gateway **raises `InsufficientPointInTimeEvidence` / fails closed** rather than returning possibly-future data.
- Enforcement lives in the gateway, not the caller. Callers cannot opt out or pass a flag to relax the boundary.

**Consequences.** The historical orchestrator obtains candles only through the gateway; leakage and candle-close tests assert the boundary; the production path is unchanged (it does not use the gateway in Phase 1).

**Alternatives rejected.** Caller-side filtering (as the production code does implicitly) — rejected: it is exactly the "caller remembers to filter" failure the operator prohibited.

---

# B. Revised Golden Historical Path

**Objective.** Prove the real production decision logic can run through historical time for **one asset, candle-only, one window**, deterministically, leak-proof, structurally isolated, with fully provenanced `HISTORICAL_POINT_IN_TIME` records — and, optionally, reproduce the autonomous BUY-rejection deterministically (D).

### Reused verbatim (no change)
- `strategies/base.py` (`Strategy`, `StrategyContext`, `Signal`) + `strategies/registry.py`.
- `risk/risk_engine.py::evaluate_signal_risk` + `RiskEvaluationRequest`/`RiskEvaluationContext`/`compute_position_sizing`/`validate_minimum_viable_order`.
- `backtesting/fills.py::simulate_buy_fill`/`simulate_sell_fill` (deterministic fill math for the Synthetic Broker).
- `exchange_connections/providers/base.py::ExchangeProviderClient` Protocol (the Synthetic Broker implements it).

### New modules (all under `services/historical_simulation/`)
| Module | Responsibility | Key interface |
|---|---|---|
| `run_mode.py` | `RunMode` enum + `EvidenceContext` (evidence_class + provenance) | `EvidenceContext.stamp(record)` |
| `clock.py` | Deterministic simulated time | `Clock.now() -> datetime`; `HistoricalClock.advance(step)` |
| `data_gateway.py` | Point-in-time candle reads (ADR-0011) | `PointInTimeDataGateway.get_candles(asset_id, interval, as_of) -> tuple[CandleView, ...]` (fail-closed) |
| `synthetic_broker.py` | Credential-less `ExchangeProviderClient` using `fills.py` | `submit_order`, `preview_market_order`, `fetch_balances`, `fetch_price_evidence` |
| `ledger.py` | Synthetic portfolio state + risk inputs | `SyntheticLedger`; `SyntheticAccountStateSource.build_context() -> RiskEvaluationContext`-compatible inputs |
| `isolation.py` | Startup fail-closed guard | `IsolationGuard.verify_or_die(run_mode, persistence_target, provider_registry, env)` |
| `provider_registry_sim.py` | Sim-only registry exposing **only** the Synthetic Broker | `get_simulation_provider(name)` |
| `orchestrator.py` | The tick loop composing the pure functions through adapters | `HistoricalSimulationOrchestrator.run(config) -> SimulationRunResult` |
| `persistence/` | `SimulationBase`, `simulation_*` models, session bound to `OT_SIMULATION_DATABASE_URL` | `SimulationSessionLocal` |

### Tick loop (orchestrator)
`init(run, dataset, seed, versions)` → `clock.now()=T` → `gateway.get_candles(asset, interval, as_of=T)` → build `StrategyContext` → `strategy.generate_signal(context)` → `SyntheticAccountStateSource.build_context()` → `evaluate_signal_risk(request, reference_price, context)` → `SyntheticBroker.submit_order` (deterministic fill) → `SyntheticLedger` update → write `simulation_decision_record` + `simulation_decision_snapshot` (provenance-complete, `evidence_class=HISTORICAL_POINT_IN_TIME`) → `clock.advance()` → repeat until window end.

### Database & migration boundaries
- New `SimulationBase(DeclarativeBase)` — **separate** from `db/base.py::Base`. Production `metadata` never contains `simulation_*` tables.
- Simulation migrations run **only** against `OT_SIMULATION_DATABASE_URL` (separate Alembic version path or a distinct migration environment). **No production migration is introduced by Phase 1.**
- Local/CI may target a schema inside an isolated test DB; deployment targets a separate DB.

### Startup isolation checks (`IsolationGuard.verify_or_die`, fail closed)
1. `run_mode ∈ {HISTORICAL_POINT_IN_TIME, COUNTERFACTUAL}` ⇒ writable persistence target **is** `OT_SIMULATION_DATABASE_URL` and **is not** `settings.database_url`.
2. No live exchange credentials reachable in the process env / credential source.
3. The active provider registry is the **sim-only** registry — `get_exchange_provider('kraken_spot' | 'coinbase_advanced')` must be unavailable.
4. Any failure ⇒ raise at startup and refuse to run. Nothing proceeds to a tick.

### Test matrix (Phase 1 acceptance)
- **Provider reachability:** sim registry cannot resolve `kraken_spot`/`coinbase_advanced`; a spy asserts live `submit_order` is invoked **0** times across a full run; Synthetic Broker exposes no credential parameter.
- **Future-data leakage:** gateway never returns `close_time > T`; a poisoned future candle (extreme value at `T+1`) does not change the decision at `T`.
- **Candle-close visibility:** a candle with `close_time == T` is visible at `T`; a candle with `close_time == T+ε` is not; a still-forming candle (`open_time ≤ T < close_time`) is excluded.
- **Determinism:** identical `(dataset_id, dataset_version, random_seed, strategy_version, risk_policy_version, decision_engine_version, execution_model_version, config)` ⇒ identical `simulation_decision_record` sequence + ledger state.
- **Zero-production-write:** run a full simulation with `settings.database_url` monkeypatched to raise on connect; the run completes writing only to the simulation DB; no INSERT/UPDATE reaches the production engine.
- **Provenance completeness:** every `simulation_decision_record`/`snapshot` has all ADR-0010 provenance fields non-null; a missing field makes the writer fail closed.
- **Risk-rejection reproduction (optional diagnostic, D):** captured production risk inputs fed to `evaluate_signal_risk` inside the harness reproduce the same `reason_code`.

### Rollback boundaries
Phase 1 is **purely additive**: a new `services/historical_simulation/` package, a new `SimulationBase` + `simulation_*` tables in a separate DB, and a new (optional) internal entry point. It modifies **no** production table, **not** the production orchestrator, and **not** the Risk Engine. Rollback = drop the simulation DB/schema and remove the package. There is **no production migration to revert**. The feature is inert unless explicitly invoked with `OT_SIMULATION_DATABASE_URL` set and `RunMode.HISTORICAL_POINT_IN_TIME` selected.

### Local / CI / eventual VPS behavior
- **Local:** simulation DB = local Postgres (or schema in a local test DB); full test matrix runs; isolation guard passes trivially (no live creds present).
- **CI:** ephemeral simulation Postgres/schema; isolation + leakage + candle-close + determinism + zero-write + provenance tests run; provider-reachability test asserts the sim-only registry.
- **VPS (eventual, not now):** `OT_SIMULATION_DATABASE_URL` points to a **separate** database; the historical worker process is not given production DB credentials. **No deployment is proposed in this plan.**

---

# C. Revised Commit-by-Commit Plan

Each commit is additive, independently testable, and mutates no production path.

1. **ADRs finalized.** Add ADR-0008…0011 under `docs/adr/` and append summaries to `02_DECISIONS.md`. Docs only.
2. **Simulation persistence foundation.** Add `OT_SIMULATION_DATABASE_URL` to `config.py`; add `services/historical_simulation/persistence/` with `SimulationBase`, a dedicated engine/sessionmaker, and Alembic scaffolding bound to the sim DB. No production change.
3. **Simulation schema migration** (sim DB only): `simulation_run`, `simulation_decision_record`, `simulation_decision_snapshot`, `simulation_ledger_entry`, `simulation_fill`; `evidence_class` enum + provenance columns; immutability listeners on synthetic decision tables.
4. **`RunMode` + `EvidenceContext` + provenance contracts** (`run_mode.py`).
5. **`IsolationGuard` + sim-only provider registry** (`isolation.py`, `provider_registry_sim.py`) + provider-reachability and startup-fail-closed tests.
6. **`HistoricalClock` + `PointInTimeDataGateway`** (`clock.py`, `data_gateway.py`) + leakage and candle-close tests. (Also routes the two latent strategy `now()` fallbacks — `strategies/helpers.py:15`, `strategies/ma_crossover.py:254` — through the clock **within the simulation path only**, without editing production behavior.)
7. **`SyntheticBroker` + `SyntheticLedger` + `SyntheticAccountStateSource`** (`synthetic_broker.py`, `ledger.py`) reusing `backtesting/fills.py`.
8. **`HistoricalSimulationOrchestrator` + synthetic decision writer** (`orchestrator.py`) + determinism, zero-production-write, and provenance-completeness tests.
9. **Optional diagnostic:** risk-rejection reproduction harness + test (D).
10. **Docs refresh:** architecture addendum for the mode abstraction; update `00_PROJECT_STATE.md` roadmap; `06_NEXT_SESSION.md` handoff. No deployment.

---

# D. Risk Engine Completeness & BUY-Rejection Investigation Plan

### D.1 Which rules are implemented vs scaffolded/deferred `[VERIFIED]`
`risk/risk_engine.py::evaluate_signal_risk` executes a **complete, ordered 12-gate pipeline**, each backed by real math (not stubs):

| # | Gate | Function | Status |
|---|---|---|---|
| 1 | Global kill switch | `validate_kill_switch_state` | implemented |
| 2 | Account pause/kill switch | `validate_kill_switch_state` | implemented |
| 3 | No-trade zone (data quality + time window) | `validate_no_trade_zone` | implemented |
| 4 | Strategy/asset cooldown | `validate_strategy_asset_cooldown` | implemented (input inert — see D.2) |
| 5 | Daily loss limit | `validate_daily_loss_limit` | implemented |
| 6 | Max drawdown | `validate_max_drawdown` | implemented |
| 7 | Stop-loss present | context `has_computable_stop_loss` | gate implemented, **input unwired** (D.2) |
| 8 | Position sizing | `compute_position_sizing` | implemented (incl. min-viable "rescue") |
| 9 | Minimum viable order (pre-AI) | `validate_minimum_viable_order` | implemented |
| 10 | AI confidence scaling | context `ai_scaled_quantity` | gate implemented, input `None` on autonomous path |
| 11 | Minimum viable order (post-AI) | `validate_minimum_viable_order` | implemented |
| 12 | Final approve/resize | — | implemented |

**The "Prompt 6.1 scaffold … rule math deferred" docstring is stale** — the math is present. What is genuinely deferred is not the math but several *inputs* on the autonomous path (D.2).

### D.2 Unwired inputs on the autonomous path `[VERIFIED]`
`autonomous_cycle/orchestrator.py::_evaluate_risk` builds `RiskEvaluationRequest` and calls `evaluate_signal_risk(request=…, reference_price=…)` **with no `context=` argument**, and `risk_context.py::resolve_execution_risk_context` hardcodes some fields. Consequences:
- **Stop-loss (gate 7) is effectively not enforced:** with no context passed, `has_computable_stop_loss` defaults **`True`** (`risk_engine.py:75`) — the gate always passes. Stop-loss protection is inert on the autonomous path.
- **Cooldown (gate 4) is fed "no losses":** `resolve_execution_risk_context` hardcodes `consecutive_losses_on_pair=0` and `last_loss_at=None` (`risk_context.py:189,191`) — cooldown can never trigger.
- **AI scaling (gate 10) never applies:** no context ⇒ `ai_scaled_quantity=None` (consistent with the LLM-free path).
- **`campaign_authorized_notional` is never passed:** `_evaluate_risk` omits it, so `compute_position_sizing`'s minimum-order **rescue ceiling collapses to `account_equity × max_position_size_pct`** (`risk_engine.py` ~lines 210–285). The mandate's own `max_order_notional_usd` authorization is not offered as the more-specific ceiling the rescue was designed to use.

### D.3 Which rule is rejecting autonomous BUYs
`[INFERRED, strong]` The two leading candidates, in evaluation order:
1. **`asset_in_no_trade_zone_data_quality`** (gate 3, *fires first*): `resolve_execution_risk_context` sets `data_is_stale = candle_data_is_stale or valuation_is_stale`. If candle ingestion is behind or price valuation is stale at cycle time, BUYs reject here **before sizing runs**.
2. **`position_below_minimum_order_size`** (gates 9/11): for a `$25` account, `requested_quantity = version.max_order_notional_usd / reference_price`, then sizing caps to `account_equity × max_position_size_pct`; with `campaign_authorized_notional` unwired (D.2), if the venue's `asset.min_order_notional` exceeds that cap the rescue cannot fire and the order is rejected as sub-minimum. This is the Small-Account-Mode ($25 proving ground) tension made concrete.

`[UNKNOWN]` The **definitive** firing rule for the current blocker cannot be established from static code alone — it depends on the runtime values (`account_equity`, `max_position_size_pct`, `asset.min_order_notional`, `version.max_order_notional_usd`, `reference_price`, and the staleness flags) at rejection time. That evidence lives in the persisted risk decision / `risk_events` of the failing production cycle.

### D.4 Can Phase 1 reproduce it deterministically? `[VERIFIED — yes]`
`evaluate_signal_risk` is pure and deterministic. Given the exact captured inputs from a failing production cycle, the simulation harness can call it and reproduce the identical `reason_code`. **Diagnostic acceptance test (optional):** capture the `RiskEvaluationRequest` inputs + `resolve_execution_risk_context` outputs (or read the persisted `risk_events.detail`/reason_code) from one failing cycle; feed them into `evaluate_signal_risk`; assert the same `reason_code` and `steps`. This turns the blocker into a repeatable, inspectable fixture without touching production.

### D.5 Constraint honored
Per operator instruction 7, **no change to the production Risk Engine is proposed here.** The unwired-input findings (stop-loss, cooldown, `campaign_authorized_notional`) are documented for the **production First-Autonomous-Profit lane** to act on under separate authorization (E). Phase 1 only *reproduces and observes*.

---

# E. Remaining Decisions Requiring Authorization

1. **Simulation dataset sourcing (largest open item).** Operator decision 3 forbids the historical worker from holding the production *writable* connection. Open: may the historical worker **read** production candles read-only to build a dataset, or must candle history be **snapshotted into the simulation DB** as an immutable, versioned `dataset_id/dataset_version`? `[RECOMMENDATION]` Snapshot into the sim DB (cleanest isolation, reproducibility, and determinism), but this needs sign-off and defines commit 2/3 scope.
2. **Local/CI namespace name + env wiring.** Confirm the schema name for local/CI (e.g., `simulation`) and that `OT_SIMULATION_DATABASE_URL` is the sole writable target in those environments.
3. **Golden Path fixture:** which single asset, which historical window, and which registered deterministic strategy (`ma_crossover`, `rsi_mean_reversion`, `momentum`, `breakout`, `donchian_breakout`, `bollinger_reversion`, `mean_reversion`). `[RECOMMENDATION]` `ma_crossover` on one liquid crypto pair over one bounded window.
4. **Fill-model fidelity for Phase 1.** `[RECOMMENDATION]` Level-1 deterministic market-order fill at candle close via `backtesting/fills.py`; `execution_model_version` pinned. Confirm.
5. **Risk-policy source for simulation.** `[RECOMMENDATION]` Pin a `risk_policy_version` snapshot of the effective rules for determinism, rather than reading live rule config. Confirm.
6. **Production-lane authorization (separate from Phase 1).** Whether/when to wire the unwired risk inputs on the **production** path (stop-loss context, loss history, `campaign_authorized_notional`) to address the BUY-rejection. This is out of Phase 1 scope by your instruction 7 and needs its own go-ahead once D's diagnostic identifies the exact firing rule.

---

*Deliverables A–E complete. No code written, repository unmodified, production Risk Engine unchanged, ADRs drafted-but-final-for-review, no deployment proposed. Awaiting authorization on Section E before any implementation.*
