# IMPLEMENTATION_MASTER_PLAN.md

Status: Execution plan (not architecture, not constitutional)
Purpose: The exact, dependency-ordered sequence of implementation work that converts OmniTrade's frozen constitutional architecture into a proven production platform — shortest path to First Autonomous Profit first, expansion after.
Authority: Subordinate to the five constitutional documents and to `00_PROJECT_STATE.md`.

**Evidence tags:** `[VERIFIED]` confirmed in the repository · `[DECIDED]` prior ADR/authorization · `[RECOMMENDATION]` · `[UNKNOWN]`.

---

## 0. Repository reality: what already exists (do not rebuild)

`[VERIFIED]` this session. The repository is a large, live, autonomous system — not a greenfield. The plan below plans **only what remains**.

**Built and operating — do not re-implement:**
- FastAPI backend (728 py files, 79 models, 51 migrations, 271 tests) + Next.js frontend.
- **Provider-neutral execution**: `ExchangeProviderClient` Protocol + registry (`kraken_spot`, `coinbase_advanced`), capability model, `submit_order` at three gated sites, encrypted-at-rest credentials (Fernet), masked read models, dry-run boundary, full reconciliation/accounting/live-event stack.
- **Autonomous mandates & campaigns**: autonomy levels 0–3, `HUMAN_REQUIRED`/`MANDATE_ALLOWED` policy, version-pinned scope (`allowed_products`/`sides`/`strategy_versions`, `max_order_notional_usd`), campaigns with `owner`, full lifecycle, DB-enforced capital/risk limits, `MAXIMUM_GOVERNED` ceiling.
- **Risk Engine**: pure, deterministic 12-gate `evaluate_signal_risk`; kill switches with human-only rearm; risk persistence.
- **Decision pipeline**: strategy registry (10 deterministic strategies), frozen `StrategyContext`/`Signal`, `generate_signal`.
- **Decision Intelligence**: immutable `DecisionRecord`/`DecisionSnapshot` (field_provenance, five version pins) + DIE/COL/DQE tables (`decision_counterfactual_result`, `decision_quality_score`, `decision_alternative_action`, `decision_explainability_record`).
- **Decision Replay** (`DefaultReplayAgent`, reconstruction) + `replay_context` (identity lineage).
- **Backtesting**: engine + deterministic `fills.py` + metrics + persistence.
- **Autonomous runtime**: `continuous_pipeline_worker.py::run_forever()` poll loop → Kraken candle ingestion → `run_autonomous_preview_cycle`; proving/activation watchdog scripts.
- **Research/arena/tournament/validation** services; append-only `audit_log`; immutable evidence enforcement; operator CLI.

**Partial / scaffolded — complete, do not rebuild:**
- `[VERIFIED]` Risk inputs unwired on the autonomous path: `campaign_authorized_notional` never passed (min-order rescue disabled); `has_computable_stop_loss` defaults `True` (stop-loss inert); `consecutive_losses_on_pair=0`/`last_loss_at=None` hardcoded (cooldown inert). **This is where the current BUY-rejection blocker lives.**
- `[VERIFIED]` API auth: bearer scheme present, full claim verification "outside scope."
- `[VERIFIED]` AI research LLM adapter: `NotImplementedError`/`PLANNED`.

**Absent / net-new — build (confirmed empty this session):**
- Operating-mode/adapter abstraction, Historical Simulation, Synthetic Broker, simulation namespace (`OT_SIMULATION_DATABASE_URL`, `SimulationBase`), `evidence_class`/simulation provenance, PIT gateway, Immutable Datasets, WorldState/ObservationView, custody hardening.

---

## Phase 1 — First Autonomous Profit Unblock (Risk Path Completion) · **CRITICAL PATH**

### Objective
Make a commissioned autonomous campaign produce a risk-**APPROVED** (or correctly **RESIZED**) production BUY candidate on the $25 proving ground, by first proving the exact current rejection cause and then surgically wiring the unwired risk inputs — without altering the pure Risk Engine's math.

### Why This Phase Exists
Directly serves the platform's sole current milestone (`00_PROJECT_STATE.md`: First Autonomous Profit) and honors `02_DECISIONS.md` ("Production Before Expansion," "Runtime Evidence Before Expansion"). Preserves `AUTHORITY_AND_ACCOUNTABILITY_MODEL.md` (Risk remains the independent veto) and `WORLD_STATE_AND_KNOWLEDGE_MODEL.md` (Risk consumes SelfState + observation-quality, never outcomes).

### Repository Impact
- **Modules:** `autonomous_cycle/orchestrator.py::_evaluate_risk` (wire inputs); `risk/risk_context.py` (populate loss history, stop-loss availability, campaign authorization); no change to `risk_engine.py` math.
- **DB:** none required (inputs already modeled); possibly read paths for loss history.
- **APIs/UI:** a diagnostic read of the rejection `reason_code`/`steps` (already persisted in `risk_events`).
- **Workers:** none structural.
- **Tests:** deterministic reproduction of the rejection; regression tests for each newly-wired input.
- **Docs:** update `00_PROJECT_STATE.md` "Not Yet Proven"; append a `02_DECISIONS.md` entry.

### Dependencies
None (operates on the existing live system).

### Deliverables
- A recorded, reproduced `reason_code` for the current BUY rejection.
- `campaign_authorized_notional` passed from the mandate/campaign into `RiskEvaluationRequest`, restoring the min-order rescue ceiling.
- Stop-loss availability and loss-history correctly supplied via `RiskEvaluationContext` (stop-loss gate meaningfully enforced; cooldown fed real history).
- Tests proving each fix in isolation.

### Acceptance Criteria
- The exact production rejection is reproduced deterministically in a unit test feeding real captured inputs to `evaluate_signal_risk`, asserting the same `reason_code`.
- With `campaign_authorized_notional` wired, a $25-account BUY whose notional meets the campaign's authorized ceiling is **no longer** rejected as `position_below_minimum_order_size` (or is correctly RESIZED up to minimum within bounds).
- Stop-loss gate rejects when no stop-loss is computable and passes when one is — verified by test (no longer defaulting silently to `True`).
- No change to `evaluate_signal_risk`'s decision math; all existing risk tests pass unchanged.

### Verification
- Automated: new unit tests in `tests/unit/services/risk/`; full existing suite green.
- Runtime: one commissioned dry-run cycle yields an APPROVED/RESIZED verdict with a recorded, explainable path.
- Manual: operator confirms the reproduced `reason_code` matches the live blocker before any wiring change.

### Estimated Complexity
Moderate.

### Suggested Commit
`fix(risk): wire campaign authorization, stop-loss, and loss-history inputs to unblock autonomous BUY`

### Claude Code / Codex Implementation Prompt
```
You are working in the OmniTrade repository. Implement ONLY Phase 1 (Risk Path Completion) and then stop.

FIRST, inspect (do not assume):
- apps/api/app/services/autonomous_cycle/orchestrator.py :: _evaluate_risk
- apps/api/app/services/risk/risk_engine.py :: evaluate_signal_risk, compute_position_sizing, RiskEvaluationRequest, RiskEvaluationContext
- apps/api/app/services/risk/risk_context.py :: resolve_execution_risk_context
- how risk_events persists reason_code/steps

DIAGNOSE FIRST: write a unit test that reconstructs the current production BUY rejection by feeding representative small-account inputs to evaluate_signal_risk and asserting the emitted reason_code and steps. Do not change behavior until this test exists and passes against the observed rejection.

THEN wire the unwired inputs, surgically, WITHOUT modifying evaluate_signal_risk's math:
1. Pass campaign_authorized_notional from the active mandate/campaign into RiskEvaluationRequest so the compute_position_sizing minimum-order rescue ceiling is restored.
2. Populate has_computable_stop_loss and stop-loss context truthfully via RiskEvaluationContext (do not leave it defaulting to True).
3. Populate consecutive_losses_on_pair / last_loss_at from real account/pair loss history instead of the hardcoded 0/None.

CONSTRAINTS:
- Preserve the Risk Engine as a pure, deterministic function; only its CALLERS may change.
- Preserve constitutional principles: Risk stays an independent veto; never let outcomes or future data enter Risk.
- Add regression tests for each wired input. Keep all existing tests green.
- Update 00_PROJECT_STATE.md and append a 02_DECISIONS.md entry.
- Do not touch execution, campaigns, or historical simulation. Stop immediately once tests pass.
```

---

## Phase 1.5 — Bounded Live Multi-Asset Expansion · implemented, parallel-authorized lane

`[VERIFIED]` implemented 2026-07-25, in parallel with (not blocking or blocked by) Phase 1/2. Not originally in this plan; added because operator intent explicitly judged waiting on BTC alone unnecessary given that the pipeline downstream of candle ingestion (strategy roster, campaign composition's per-instrument ranking/selection, Risk Engine, canonical package/claim/execution) was already asset-parametrized. See `02_DECISIONS.md` ("Bounded Live Multi-Asset Expansion") for the full decision record.

**Delivered:** the autonomous worker evaluates a configured roster of Kraken spot products (BTC-USD plus `AUTONOMOUS_CYCLE_ADDITIONAL_PRODUCTS`, default unset/BTC-only) per cycle, reusing every existing gate unchanged; campaign composition's existing deterministic ranking selects at most one winner per cycle. Default configuration is byte-identical in behavior to before.

**Not delivered / still requires manual operator action:** no asset beyond BTC-USD is authorized to trade — `canonical_campaign_binding.py` still hard-asserts the canonical proving campaign to `allowed_instruments={"BTC-USD"}`. Enabling a second asset requires an explicit campaign version + mandate version change, applied manually by the operator (exact commands recorded alongside this implementation's report), never automatically.

**Does not affect dependency order:** Phase 1 → Phase 2 remains the critical path to First Autonomous Profit; Phase 3+ (Historical Simulation) remains fully independent of this lane.

---

## Phase 2 — First Autonomous Profit Completion · **CRITICAL PATH**

### Objective
Demonstrate the full unattended loop on the $25 proving ground: campaign selection → strategy → risk approval → **production BUY** → autonomous position management → **production SELL** → reconciliation → accounting → **verified net profit**, without operator intervention during execution.

### Why This Phase Exists
This *is* the milestone (`00_PROJECT_STATE.md`). Fulfills the Constitution's Article VIII (Safety) and Article XI (Capital Stewardship) end-to-end, and `SMALL_ACCOUNT_MODE`'s "$25 proving ground, not a toy."

### Repository Impact
- **Modules:** position lifecycle, execution orchestration, reconciliation guard, accounting/profit cycle — all `[VERIFIED]` present; this phase hardens their end-to-end composition, not new subsystems.
- **DB:** none new expected; verify campaign-identity propagation through reconciliation/accounting.
- **Workers:** `continuous_pipeline_worker` executes the full lifecycle unattended.
- **Tests:** an integration test covering BUY→manage→SELL→reconcile→accounting with a mocked provider; a proving-window runbook.
- **Docs:** flip the `00_PROJECT_STATE.md` "Not Yet Proven" checklist as items are proven.

### Dependencies
Phase 1.

### Deliverables
- One commissioned campaign completes a full production BUY→SELL→reconcile→accounting cycle unattended.
- Campaign identity authoritative throughout (`[VERIFIED]` Campaign Identity decision) end-to-end.
- Verified positive net profit recorded and reconciled.

### Acceptance Criteria
- A commissioned campaign executes a production BUY; campaign identity remains authoritative through reconciliation and accounting; a production SELL completes; **net profit is verified** — every currently-unchecked `00_PROJECT_STATE.md` box is checked.
- No operator intervention occurred during execution (auditable from the event trail).
- Reconciliation fails closed on any unresolved/ambiguous order state (`[VERIFIED]` existing guard exercised by test).

### Verification
- Automated integration test of the full lifecycle against a mock/dry-run provider.
- Runtime: a real proving-window run on the $25 account with the activation watchdog; go/no-go from the recorded evidence.
- Production-readiness: kill switch, dry-run boundary, and reconciliation guard all verified active.

### Estimated Complexity
Complex (integration across live subsystems, real capital).

### Suggested Commit
`feat(campaign): demonstrate end-to-end autonomous BUY→SELL→reconcile with verified net profit`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 2 (First Autonomous Profit Completion) and then stop.

INSPECT the existing (already-built) lifecycle:
- apps/api/app/services/position_lifecycle, services/orchestration (continuous_pipeline_worker, reconciliation_guard), services/capital_campaign_profit, live accounting/reconciliation models.
Acknowledge what already works; do NOT rebuild it.

DELIVER an integration test that drives one commissioned campaign through BUY → autonomous management → SELL → reconciliation → accounting against a mocked exchange provider, asserting: campaign identity is authoritative at every step, reconciliation fails closed on ambiguity, and verified net profit is recorded.

Then produce a proving-window runbook (documentation) for the real $25 run. Do NOT enable live trading or propose deployment. Preserve every safety gate (kill switch, dry-run boundary, risk veto). Update 00_PROJECT_STATE.md checklist items that the integration test proves. Keep all tests green. Stop on success.
```

---

## Phase 3 — Governance Adoption & Simulation-Isolation Foundation

### Objective
Adopt ADR-0008–0012 as decision records, introduce the shared `RunMode`/`EvidenceContext` contracts and the `evidence_class` taxonomy, and stand up the **isolated simulation persistence foundation** (`OT_SIMULATION_DATABASE_URL`, `SimulationBase`, dedicated session, `IsolationGuard` scaffold) — with zero change to production decision tables.

### Why This Phase Exists
Establishes the structural isolation and provenance vocabulary every later phase depends on, per `[DECIDED]` ADR-0010 (separate synthetic tables + separate connection) and `CUSTODY_AND_SECURITY_MODEL.md` (compartmentalization). No production behavior changes.

### Repository Impact
- **Modules:** new `services/historical_simulation/{run_mode,persistence,isolation}`; `config.py` gains `OT_SIMULATION_DATABASE_URL`.
- **DB:** new `SimulationBase` + a separate migration path bound to the simulation DB; **no production-table change**.
- **Tests:** isolation-guard startup tests; connection-target tests.
- **Docs:** finalize ADR-0008–0012 under `docs/adr/`; append `02_DECISIONS.md`.

### Dependencies
Phases 1–2 (so the FAP lane is not disturbed). Governance-only; parallelizable with post-FAP work.

### Deliverables
- ADR-0008 (modes/adapters), 0009 (terminology), 0010 (provenance/isolation), 0011 (knowledge boundary), 0012 (immutable datasets) committed.
- `RunMode`, `EvidenceContext`, `evidence_class` enum (`PRODUCTION_LIVE`, `FORWARD_PAPER`, `HISTORICAL_POINT_IN_TIME`, `COUNTERFACTUAL`, `UNIT_TEST`).
- Simulation engine/session on `OT_SIMULATION_DATABASE_URL`; `IsolationGuard.verify_or_die` scaffold.

### Acceptance Criteria
- `SimulationBase` metadata contains no production tables and vice versa (asserted by test).
- `IsolationGuard` raises at startup if a historical mode is bound to the production `DATABASE_URL` or to a live provider.
- Production migrations and behavior are byte-unchanged; full suite green.

### Estimated Complexity
Moderate.

### Suggested Commit
`chore(governance): adopt ADR-0008..0012 and stand up isolated simulation persistence foundation`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 3 and then stop.

INSPECT apps/api/app/db/{session,base}.py and app/config.py. Confirm production uses a single Base and database_url.

DELIVER:
1. docs/adr/ADR-0008..0012 (concise decision records matching the constitutional documents; do not restate them).
2. A new services/historical_simulation package with: RunMode enum, EvidenceContext + evidence_class enum, a SEPARATE SimulationBase(DeclarativeBase), a dedicated async engine/sessionmaker bound to a new OT_SIMULATION_DATABASE_URL setting, and IsolationGuard.verify_or_die.
3. Tests proving SimulationBase and the production Base share no tables, and that IsolationGuard refuses to start when a historical mode is bound to the production DATABASE_URL or a live provider.

CONSTRAINTS: add NO columns to production decision_records/decision_snapshots. Do not alter production behavior or migrations. Keep tests green. Stop on success.
```

---

## Phase 4 — Historical Simulation: Golden Historical Path · [ADR-0008/0009/0010/0011]

### Objective
The smallest production-isolated slice that runs the **real** decision logic (`generate_signal`, `evaluate_signal_risk`) through historical time for one asset, candle-only, deterministically and leak-proof, writing provenance-complete `HISTORICAL_POINT_IN_TIME` synthetic Decision Records — and doubling as a deterministic harness for the Risk path.

### Why This Phase Exists
Realizes `HISTORICAL_INTELLIGENCE_PLATFORM.md` Stage 1 and the World-State principle "WorldState is a query, not a store" (`world(as_of=T)`). Reuses shared financial logic per ADR-0008; enforces the fail-closed knowledge boundary per ADR-0011.

### Repository Impact
- **Modules:** `historical_simulation/{clock,data_gateway,synthetic_broker,ledger,orchestrator,provider_registry_sim}`; reuse `strategies`, `risk_engine`, `backtesting/fills.py` unchanged.
- **DB:** `simulation_run`, `simulation_decision_record`, `simulation_decision_snapshot`, `simulation_ledger_entry`, `simulation_fill` (simulation namespace) with provenance columns; immutability listeners.
- **Tests:** determinism, leakage (incl. poisoned-future), candle-close visibility, isolation, zero-production-write, provenance completeness.

### Dependencies
Phase 3.

### Deliverables
- `HistoricalClock`, `PointInTimeDataGateway` (candle-close visibility, fail-closed), `SyntheticBroker` (implements `ExchangeProviderClient`, no credentials, reuses `fills.py`), `SyntheticLedger` + `AccountStateSource`, `SimulationOrchestrator`, sim-only provider registry.
- One-asset, one-window replay producing provenance-complete synthetic Decision Records.

### Acceptance Criteria
- **Determinism:** identical `(dataset, seed, versions, config)` → identical decision sequence + ledger (byte-stable).
- **Leakage:** the gateway never returns a candle with `close_time > T`; an injected extreme future candle never changes the decision at `T`.
- **Candle-close:** a candle is visible at `T` iff `close_time ≤ T`; forming candles excluded.
- **Isolation:** a historical run cannot bind a live provider; zero real `submit_order`; zero writes to production tables.
- **Provenance:** every synthetic record carries all required fields (`simulation_id`, `dataset_id`, `dataset_version`, `knowledge_cutoff_timestamp`, `run_mode`, versions, `random_seed`, `created_at`) or the write fails closed.

### Verification
- Automated: the six test classes above under a new `tests/.../historical_simulation/`.
- Replay validation: rerun a fixed simulation twice; diff is empty.
- Diagnostic (optional): feed captured production risk inputs through the harness and reproduce the Phase-1 rejection deterministically.

### Estimated Complexity
Very Complex.

### Suggested Commit
`feat(historical-simulation): golden historical path — deterministic, leak-proof, production-isolated single-asset replay`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 4 (Golden Historical Path) and then stop.

INSPECT and REUSE UNCHANGED: app/services/strategies/base.py (StrategyContext, generate_signal), app/services/risk/risk_engine.py (evaluate_signal_risk), app/services/backtesting/fills.py, app/services/exchange_connections/providers/base.py (ExchangeProviderClient Protocol). Do NOT fork the decision logic.

BUILD under app/services/historical_simulation/ (bound to the Phase-3 SimulationBase / OT_SIMULATION_DATABASE_URL):
- HistoricalClock (deterministic simulated time).
- PointInTimeDataGateway.get_candles(asset, interval, as_of): returns only candles with close_time <= as_of; excludes forming candles; carries provenance; fails closed (raise InsufficientPointInTimeEvidence) if the boundary cannot be proven. Enforcement lives in the gateway, not the caller.
- SyntheticBroker implementing ExchangeProviderClient with NO credentials, deterministic fills via fills.py.
- SyntheticLedger + a synthetic AccountStateSource feeding RiskEvaluationContext.
- SimulationOrchestrator: init -> clock T -> gateway.get_candles(as_of=T) -> StrategyContext -> generate_signal -> evaluate_signal_risk -> SyntheticBroker fill -> SyntheticLedger update -> write simulation_decision_record/snapshot with full provenance and evidence_class=HISTORICAL_POINT_IN_TIME -> advance -> repeat.
- simulation_* tables with immutability listeners.

TESTS (all required): determinism, future-data leakage (including a poisoned future candle), candle-close visibility, isolation (no live provider; zero real submit_order; zero production writes), zero-production-write, provenance completeness (fail closed on missing field).

CONSTRAINTS: no production-table changes; production path untouched; keep all tests green. Stop on success.
```

---

## Phase 5 — Immutable Historical Datasets · [ADR-0012]

### Objective
Introduce content-addressed, bitemporal, hash-verified datasets and make the Phase-4 gateway bind an **immutable dataset version** (by Merkle root / bundle hash) instead of reading the mutable candle table — guaranteeing reproducibility.

### Why This Phase Exists
Realizes `IMMUTABLE_HISTORICAL_DATASETS.md`: immutability (content-addressing) + point-in-time correctness (bitemporal availability), and the reproducibility contract.

### Repository Impact
- **Modules:** `historical_simulation/datasets/{manifest,hashing,builder,binding}`; gateway reads from a bound dataset.
- **DB/Storage:** dataset manifests + content artifacts (file-based, open format); a `simulation` binding table records `(dataset_id, dataset_version, bundle_hash)`.
- **Tests:** hash/Merkle verification; bitemporal as-of query correctness; fail-closed on integrity mismatch; reproducibility across a rebuild.

### Dependencies
Phase 4. **Requires the operator's Section-E dataset-sourcing decision** (snapshot into sim DB vs read-only) — `[UNKNOWN]`, to be resolved before build.

### Deliverables
- Dataset manifest + Merkle/chunk hashing + a builder that freezes a bounded universe into a versioned, self-describing artifact.
- Gateway binds a dataset version; the simulation records the bundle hash.

### Acceptance Criteria
- A dataset's identity equals its content hash; two different byte sets cannot share a version (asserted).
- A point-in-time query as-of `T` returns the value knowable at `T`, honoring bitemporal availability, not the latest revision.
- Integrity mismatch (corrupted chunk) fails closed (`DatasetIntegrityError`).
- Rebuilding the same dataset from source yields the identical Merkle root; a simulation bound to it reproduces byte-identical decisions.

### Verification
- Automated integrity + bitemporal + reproducibility tests.
- Replay validation: same bundle hash + seed → identical results across two independent runs.

### Estimated Complexity
Complex.

### Suggested Commit
`feat(datasets): content-addressed, bitemporal immutable datasets bound by hash into historical simulation`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 5 and then stop. Confirm the operator's dataset-sourcing decision is recorded before building; if absent, stop and request it.

INSPECT app/services/historical_simulation (Phase 4). Extend the gateway to read from a bound immutable dataset rather than the live candle table.

BUILD: dataset manifest (JSON/TOML) with dataset_id, dataset_version, content merkle_root, schema_version, dataset_type, availability_basis, provenance; chunk hashing rolled into a Merkle root; a builder that freezes a bounded universe into an open, self-describing, content-addressed artifact; a binding that records (dataset_id, dataset_version, bundle_hash) on the simulation_run.

TESTS: content-hash identity; bitemporal as-of correctness (availability, not latest revision); fail-closed integrity mismatch; reproducibility (rebuild yields identical merkle_root; bound simulation reproduces byte-identical decisions).

CONSTRAINTS: authoritative dataset is file-based and immutable; the sim DB is a rebuildable projection. No production changes. Keep tests green. Stop on success.
```

---

## Phase 6 — Synthetic Ground Truth & Decision-Intelligence Consumption

### Objective
Add the ground-truth outcome engine (outcomes revealed only as simulated time advances) and wire the existing DQE/DIE to consume synthetic evidence **evidence-class-aware**, so simulation produces scored, learnable decision history without contaminating production truth.

### Why This Phase Exists
Realizes the World-State learning discipline (learning never flows backward) and the Authority model's "AI/learning proposes, never mutates production." Reuses existing `decision_quality`/`decision_intelligence` services.

### Repository Impact
- **Modules:** `historical_simulation/ground_truth`; extend DQE/DIE read paths to accept synthetic evidence tagged by class.
- **DB:** synthetic outcome + quality tables in the simulation namespace.
- **Tests:** ground truth never available before its time; DQE never blends synthetic with production evidence.

### Dependencies
Phases 4–5.

### Deliverables
- Ground-truth outcome engine keyed on advancing simulated time.
- Evidence-class-aware DQE/DIE consumption over synthetic records.

### Acceptance Criteria
- No outcome is computable before its simulated time occurs (asserted).
- DQE/DIE queries carry `evidence_class` through every aggregate; a test proves synthetic and production evidence are never co-mingled in a single metric.

### Estimated Complexity
Complex.

### Suggested Commit
`feat(historical-simulation): ground-truth outcomes and evidence-class-aware decision-intelligence consumption`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 6 and then stop.

INSPECT app/services/decision_quality, app/services/decision_intelligence, and the Phase-4 simulation decision records.

BUILD a ground-truth outcome engine that reveals outcomes (forward returns, realized outcome/drawdown, opportunity cost) ONLY after simulated time advances past them. Extend DQE/DIE to consume synthetic Decision Records while carrying evidence_class through every aggregate.

TESTS: ground truth unavailable before its simulated time; synthetic and production evidence never combined in one metric. No production changes. Keep tests green. Stop on success.
```

---

## Phase 7 — Longitudinal & Multi-Asset Simulation + Canonical Identity

### Objective
Scale simulation from one asset/one window to full-history longitudinal runs (checkpoint/resume/budget), then to synchronized multi-asset opportunity competition, backed by an Asset Registry with canonical identity and historical availability.

### Why This Phase Exists
Realizes HIP Stages 2–4 and `CUSTODY_AND_SECURITY_MODEL.md` §11 (canonical identity survives implementation; assets have stable identity through forks/renames).

### Repository Impact
- **Modules:** checkpoint/resume/budget in the orchestrator; multi-asset synchronization; Asset Registry (`REFERENCE` dataset type) with availability windows.
- **DB:** checkpoint tables; registry/availability (simulation namespace or reference dataset).
- **Tests:** resume-equals-continuous; an asset cannot enter the opportunity set before it existed; cross-asset no-leakage.

### Dependencies
Phases 4–6.

### Acceptance Criteria
- A resumed run equals an uninterrupted run (byte-identical from the checkpoint).
- An asset is excluded from the opportunity set at any `T` before its canonical launch/availability; survivorship/delisting handled.
- Multi-asset runs preserve the per-`T` knowledge boundary across all assets.

### Estimated Complexity
Very Complex.

### Suggested Commit
`feat(historical-simulation): longitudinal + multi-asset replay with canonical asset identity and availability`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 7 and then stop.

INSPECT the Phase-4 orchestrator and Phase-5 datasets. Add checkpoint/resume/budget governance; multi-asset synchronization under one knowledge cutoff; an Asset Registry (canonical identity decoupled from venue symbols) with historical availability (launch/delist/fork/rename).

TESTS: resume equals continuous; an asset cannot be considered before its availability at T; cross-asset no-leakage. No production changes. Keep tests green. Stop on success.
```

---

## Phase 8 — Counterfactual Branching, Tournaments & Promotion Gate

### Objective
Add counterfactual branches from shared historical state, strategy/risk tournaments under shared evidence, and the governed promotion gate (train → validate → untouched-test → forward-paper → **human approval** → bounded live).

### Why This Phase Exists
Realizes HIP Stages 5–6/9 and the Authority model's most consequential rule: promotion is human-approved and non-delegable; "untouched test" is falsifiable via immutable datasets.

### Repository Impact
- **Modules:** counterfactual branch engine; tournament runner (reuse `tournament`/`arena`); promotion gate with a test-window lockout.
- **DB:** `replay_branch` lineage; promotion decisions (immutable, reuse `ArenaRiskGateDecision` pattern).
- **Tests:** branch isolation; no promotion on development history alone; test-window untouchability enforced by dataset hash.

### Dependencies
Phases 5–7.

### Acceptance Criteria
- Branches are isolated (no cross-contamination) and inherit the same point-in-time boundary.
- No candidate reaches "production-eligible" without passing an untouched test window, verified by the test dataset's fixed Merkle root before and after tuning.
- The final promotion step requires a recorded human approval and is impossible to trigger by an AI/automated actor (asserted).

### Estimated Complexity
Very Complex.

### Suggested Commit
`feat(research): counterfactual branching, tournaments, and human-gated promotion path`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 8 and then stop.

INSPECT app/services/{tournament,arena,validation_runs} and the Phase-5 datasets. Add counterfactual branching (isolated timelines from shared state), tournaments over shared evidence, and a promotion gate enforcing train -> validate -> untouched-test -> forward-paper -> human approval -> bounded live.

TESTS: branch isolation; untouched-test enforced via fixed dataset Merkle root; promotion requires recorded human approval and cannot be initiated by an automated/AI actor. No production changes. Keep tests green. Stop on success.
```

---

## Phase 9 — Custody & Security Hardening

### Objective
Close the physical-root-of-trust gaps: separate and rotate the master key (envelope encryption), give `ExchangeConnection` an accountable owner, enforce repository governance (`CODEOWNERS` + CI), complete API auth/claim verification, and document custody roster + succession.

### Why This Phase Exists
Realizes `CUSTODY_AND_SECURITY_MODEL.md`. `[VERIFIED]` gaps: key/ciphertext co-located in one `.env`; single non-rotating Fernet key; `ExchangeConnection` has no `owner`; API claim verification "outside scope"; no `CODEOWNERS`/CI.

### Repository Impact
- **Modules:** `exchange_connections/crypto.py` (envelope/rotation); `core/security.py` (complete verification); `models/exchange_connection.py` (owner); credential-access auditing.
- **Infra:** `CODEOWNERS`, CI governance, secret custody separation.
- **Tests:** rotation without data loss; auth enforcement; credential-access audit events.
- **Docs:** custody roster + succession plan (out-of-band references); amendment-process section appended to `PROJECT_CONSTITUTION.md`.

### Dependencies
None hard; a subset (key/ciphertext separation, `ExchangeConnection` owner, full auth) is a **prerequisite for scaling capital beyond the proving ground** and should precede meaningful real-capital growth even though it is not required for the first proof.

### Acceptance Criteria
- The master key and encrypted data no longer share a custody boundary; key rotation is demonstrated without re-encrypting from scratch (envelope/`MultiFernet`).
- `ExchangeConnection` records an accountable owner; every credential decryption emits an audit event.
- API operator authorization fully verifies tokens/claims (no "outside scope" gap); unauthorized access is refused by test.
- `CODEOWNERS` requires owner-level approval for constitutional docs/ADRs; CI enforces review.

### Estimated Complexity
Complex.

### Suggested Commit
`feat(security): custody hardening — key envelope/rotation, connection ownership, repo governance, full auth`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 9 and then stop.

INSPECT app/services/exchange_connections/crypto.py, app/core/security.py, app/models/exchange_connection.py, and repo governance (.github, CODEOWNERS).

DELIVER:
1. Envelope encryption / key rotation for exchange credentials (do not co-locate the master key with the database; support MultiFernet-style rotation). Migrate existing ciphertext safely.
2. An accountable owner field on ExchangeConnection + a migration.
3. Complete operator token/claim verification in core/security.py (remove the "outside scope" gap); refuse unauthorized access.
4. CODEOWNERS requiring owner-level approval for docs/adr and constitutional documents; a CI workflow enforcing review/tests.
5. Audit events on every credential decryption.

TESTS: rotation preserves decryptability; unauthorized API access refused; credential-access audited. Append an amendment-process section to PROJECT_CONSTITUTION.md. Keep tests green. Stop on success.
```

---

## Phase 10 — Knowledge-Model Evolution (WorldState / ObservationView) & Historical Media · **Longest horizon**

### Objective
Evolve strategies from raw candles to a scoped, as-of-bounded `ObservationView(as_of=T)`, and add Historical Media Intelligence (bitemporal media datasets) — the richest breadth, gated on everything before it.

### Why This Phase Exists
Realizes `WORLD_STATE_AND_KNOWLEDGE_MODEL.md` §11 (scoped observation, not candles, not god-object) and the media integration of the datasets doc. Explicitly evidence-gated and last.

### Repository Impact
Strategy interface (additive `ObservationView`); media dataset type (bitemporal); gateway extension. Broad; deferred by design.

### Dependencies
Phases 4–8 (and only after First Autonomous Profit is well established).

### Acceptance Criteria
- A strategy can consume an `ObservationView(as_of=T)` that is immutable, uncertainty-carrying, and scoped to its declared needs; legacy candle-only strategies still work unchanged.
- Media facts are bitemporal; simulation keys media visibility on `knowledge_available_at`, failing closed when unknown.

### Estimated Complexity
Very Complex.

### Suggested Commit
`feat(knowledge): scoped ObservationView strategy interface and bitemporal historical media`

### Claude Code / Codex Implementation Prompt
```
Implement ONLY Phase 10 and then stop. Do not begin unless First Autonomous Profit is demonstrated and Phases 4-8 exist.

INSPECT app/services/strategies/base.py and the Phase-4/5 gateway/datasets. Add a scoped, immutable, as-of-bounded ObservationView(as_of=T) as an additive strategy input (candles remain valid). Add a bitemporal media dataset type; simulation must key media visibility on knowledge_available_at and fail closed when unknown.

TESTS: least-observation scoping; legacy strategies unaffected; media availability fail-closed. No production changes to the live path. Keep tests green. Stop on success.
```

---

## Critical Path — minimum sequence to First Autonomous Profit

**Absolutely required before First Autonomous Profit:** **Phase 1 → Phase 2.** Nothing else. `[VERIFIED]` the live system already has execution, campaigns, mandates, risk, decision records, and the autonomous worker loop; the milestone is blocked only on the risk-input wiring (Phase 1) and demonstrating the full unattended profit loop (Phase 2).

**Everything from Phase 3 onward is post-milestone** and must not precede or delay Phases 1–2 (`02_DECISIONS.md`: "Production Before Expansion"). Phases 3–8 build the Historical Intelligence Platform; Phase 9 hardens custody; Phase 10 is the longest-horizon breadth.

**One qualification:** a subset of Phase 9 (separating the master key from the database; giving `ExchangeConnection` an owner; completing API auth) is not required for the *first* $25 proof but *is* strongly advisable before scaling real capital meaningfully. It can run in parallel with Phase 3+ once Phase 2 is proven.

---

## Nice-to-Have Work (intentionally deferred until after First Autonomous Profit)

- All of Historical Simulation (Phases 3–8) — production-isolated research value, not milestone-blocking.
- Immutable datasets, ground-truth, tournaments, promotion gate.
- WorldState/ObservationView evolution and Historical Media (Phase 10).
- Full custody hardening beyond the "before scaling capital" subset.
- Frontend expansion for any of the above (the existing pages suffice for the proving window).

---

## Architectural Risks (criticism, not reassurance)

1. **`[CHALLENGE]` The plan's own length is a risk.** Ten phases and five constitutional documents exist; **two phases** stand between today and the milestone. The dominant risk is continued planning displacing execution. The plan is deliberately front-loaded so Phases 1–2 can be done without any of the rest.
2. **The BUY-rejection root cause is diagnosed but not runtime-confirmed.** `[VERIFIED]` candidate causes (unwired `campaign_authorized_notional`; small-account minimum vs venue; possibly stale-data no-trade-zone). Phase 1 *must* reproduce the real `reason_code` before changing anything — do not fix by assumption.
3. **Risk-input wiring could weaken protections if done carelessly.** Wiring `campaign_authorized_notional` raises the rescue ceiling — correct, but it must remain bounded by the campaign's own governed limits, never a blank check. Stop-loss and cooldown were *inert*; turning them on changes behavior and needs its own regression coverage.
4. **Historical Simulation is genuinely Very Complex and entirely net-new.** `[VERIFIED]` absent. It is easy to under-estimate; the leakage and determinism guarantees are subtle. Its value is real but strictly post-FAP.
5. **Over-engineering watch.** The full 10-phase platform may exceed what a single-family, small-capital system needs. Phases 6–8 and 10 should be re-justified against evidence after FAP; if the platform proves profitable simply, some breadth may never be worth building. Say so honestly at that point.
6. **Custody debt is real while live capital is at stake.** `[VERIFIED]` master key co-located with the DB, single non-rotating key, no connection owner, scaffolded auth. The first proof on $25 is acceptable under current custody; scaling is not. Do not let Phase 9 slip indefinitely because it is not milestone-blocking.
7. **Dataset sourcing decision is unresolved** (`[UNKNOWN]`, Section E). Phase 5 cannot start until the operator chooses snapshot-into-sim-DB vs read-only. This is a decision, not a missing foundation.

---

## Final Reflection

**1. Is the constitutional architecture now sufficient?** Yes. The five frozen documents cover values, knowing, evidence, authority, and physical capability coherently; the one residual (an amendment process) is a *section* of `PROJECT_CONSTITUTION.md` (Phase 9), not a new document. No further constitutional document is warranted.

**2. Is this roadmap internally consistent?** Yes. Dependencies are acyclic (1→2 stand alone; 3 seeds 4; 4→5→6→7→8 chain; 9 mostly independent; 10 last). The one external dependency is the operator's Phase-5 dataset decision. The ordering respects the platform's own "Production Before Expansion" law by placing the only milestone-critical work first.

**3. Remaining architectural gaps requiring planning before implementation continues?** None. Every remaining item is implementation detail against an established foundation, not a missing foundation. The Section-E decisions and the runtime confirmation of the BUY-rejection are *inputs to execution*, not new architecture.

**4. Should OmniTrade now stop creating planning documents and shift almost entirely to implementation until First Autonomous Profit?** **Yes — unequivocally, and starting now.** This is intended to be the final planning artifact. The constitution is frozen; the roadmap is set; the milestone is two phases away and blocked on a diagnosed, wireable risk-input gap. Every further planning document from here has negative expected value until a running result reveals a real, specific gap. The most constitutional act available today is to execute Phase 1: reproduce the rejection, wire the inputs, and take the platform to its first autonomous profit.

---

*This is an execution plan only. No code was written, the repository was not modified, and no deployment is proposed. It is submitted as the definitive implementation roadmap, with the recommendation that planning now cease and implementation of Phase 1 begin.*
