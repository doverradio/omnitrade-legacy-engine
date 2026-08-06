# HISTORICAL_LEARNING_INTELLIGENCE_IMPLEMENTATION_PLAN.md

## OmniTrade Legacy Engine — Historical Learning Intelligence Implementation Plan

**Status:** Proposed Implementation Sequence  
**Depends On:** `HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md`  
**Default Execution Environment:** Current local workstation  
**Production Authority:** None  
**Implementation Style:** Small, bounded, deterministic, test-backed prompts  
**Prompt Target:** Local coding model, Codex, Claude Code, or equivalent repository-aware coding agent

---

# 1. Purpose

This document converts the Historical Learning Intelligence architecture into an implementation sequence.

The plan is deliberately incremental.

Each task must:

- produce independently useful infrastructure,
- avoid production behavior changes,
- include targeted tests,
- preserve architectural boundaries,
- avoid broad repository rereads when unnecessary,
- stop when evidence is insufficient,
- update documentation only when actual behavior changes.

No task in this plan authorizes live trading changes.

---

# 2. Operating Rules for Every Prompt

Every implementation prompt must require the coding agent to:

1. Read the named authoritative documents and only the relevant code paths.
2. Perform read-only reconnaissance before editing.
3. State the exact files it expects to change.
4. Preserve production isolation.
5. Preserve Risk Engine final authority.
6. Avoid production database writes.
7. Avoid live-provider order calls.
8. Avoid migrations unless explicitly requested.
9. Add or update focused tests.
10. Run only targeted tests first.
11. Report:
   - files changed,
   - behavior added,
   - tests run,
   - test results,
   - unresolved concerns,
   - whether an Alembic migration exists.
12. Stop rather than guess when repository reality conflicts with the prompt.

---

# 3. Phase Overview

```text
Phase 0  — Repository Reconnaissance and Boundary Confirmation
Phase 1  — Simulation Mode and Isolation Guard
Phase 2  — Historical Data Registry and Dataset Manifest
Phase 3  — Deterministic Replay Clock
Phase 4  — Point-in-Time Snapshot Builder
Phase 5  — Feature and Target Pipelines
Phase 6  — Chronological Dataset Builder
Phase 7  — Baseline Models
Phase 8  — Feedforward Neural Network Training
Phase 9  — Model Registry and Checkpoints
Phase 10 — Evaluation and Walk-Forward Replay
Phase 11 — Historical Portfolio Simulation
Phase 12 — Decision Intelligence Evidence
Phase 13 — Hindsight Feasibility Oracle
Phase 14 — Multi-Asset Expansion
Phase 15 — Opportunity Contract
Phase 16 — Baseline Global Capital Allocator
Phase 17 — Target-Attainment Probability
Phase 18 — Portfolio Planner Research
```

Only one phase should be implemented at a time unless the tasks are mechanically inseparable.

---

# 4. Phase 0 — Repository Reconnaissance and Boundary Confirmation

## Objective

Determine how the existing repository already handles:

- simulation isolation,
- replay,
- historical data,
- Decision Records,
- experiment-like evidence,
- database configuration,
- service boundaries.

Do not implement.

## Deliverable

A concise reconnaissance report naming:

- reusable components,
- conflicts,
- missing pieces,
- proposed exact package location,
- tests that will protect production isolation.

## Prompt 0.1

```text
You are performing read-only reconnaissance for OmniTrade's Historical Learning Intelligence subsystem.

Read these documents first:

1. docs/PROJECT_CONSTITUTION.md
2. docs/PROJECT_VISION.md
3. docs/SYSTEM_ARCHITECTURE.md
4. docs/RISK_ENGINE.md
5. docs/DECISION_INTELLIGENCE_ENGINE.md
6. docs/00_OPERATIONS_MAP.md
7. docs/02_DECISIONS.md
8. docs/00_PROJECT_STATE.md
9. docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md

Then inspect only the repository paths relevant to:

- existing replay services,
- simulation persistence,
- SimulationBase or equivalent metadata roots,
- OT_SIMULATION_DATABASE_URL or equivalent settings,
- IsolationGuard or equivalent safeguards,
- historical data ingestion/storage,
- decision record creation,
- model or experiment metadata,
- configuration loading,
- tests proving production isolation.

Do not edit any file.

Return:

1. Existing components that should be reused.
2. Existing components that conflict with the new architecture.
3. The exact recommended package path for Historical Learning Intelligence.
4. The minimum first implementation task.
5. Exact tests needed to prove the research subsystem cannot write to production.
6. Any architectural question that must be resolved before implementation.
7. A bounded file list for the next task.

Do not redesign existing architecture.
Do not propose live execution changes.
Do not infer missing behavior without repository evidence.
```

---

# 5. Phase 1 — Simulation Mode and Isolation Guard

## Objective

Create or harden the structural boundary preventing historical-learning code from touching production persistence or live execution.

## Required Behavior

- explicit simulation configuration,
- no production database fallback,
- clear mode enum or equivalent,
- fail-closed startup guard,
- deterministic tests.

## Prompt 1.1

```text
Implement the smallest production-isolation foundation required for Historical Learning Intelligence.

First read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- the Phase 0 reconnaissance report
- existing simulation/replay configuration code
- existing database configuration code
- existing isolation tests

Requirements:

1. Reuse existing SimulationBase, OT_SIMULATION_DATABASE_URL, IsolationGuard, or equivalent components when present.
2. Historical-learning code must never silently fall back to the production database URL.
3. Add an explicit historical-learning/simulation mode only if repository evidence shows one is missing.
4. Startup or initialization must fail closed when:
   - the simulation database URL is absent,
   - simulation and production connection identities overlap,
   - the mode is not explicitly simulation/research.
5. Do not change live execution, campaigns, mandates, Risk Engine behavior, or production schemas.
6. Add focused unit tests for every failure condition.
7. Add one integration-style test proving the subsystem selects only the simulation persistence boundary.
8. Do not add an Alembic migration unless a schema change is genuinely necessary; prefer no schema change in this task.

Before editing, report the exact files you will change.

After implementation, run targeted tests only and report:
- files changed,
- tests run,
- results,
- any unresolved isolation risk,
- whether an Alembic migration exists.
```

---

# 6. Phase 2 — Historical Data Registry and Dataset Manifest

## Objective

Define immutable identities for historical datasets and replay manifests.

## Required Behavior

- dataset identity,
- source metadata,
- checksums,
- quality status,
- point-in-time suitability,
- immutable manifest serialization.

## Prompt 2.1

```text
Implement the Historical Data Registry domain types and immutable Dataset Manifest for Historical Learning Intelligence.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- existing asset/data models
- existing historical ingestion code
- existing manifest/value-object conventions
- existing simulation persistence boundary

Scope:

1. Add domain types for:
   - HistoricalDatasetRecord
   - DatasetQualityStatus
   - PointInTimeSuitability
   - DatasetManifest
2. The manifest must identify:
   - dataset_id
   - asset
   - venue
   - interval
   - source
   - start/end timestamps
   - timezone
   - checksum
   - feature schema version placeholder
   - target schema version placeholder
   - chronological split boundaries
   - embargo configuration
3. Values must serialize deterministically.
4. Dataset and manifest identities must be content-derived or otherwise deterministic.
5. Add validation for invalid time ranges, missing checksums, unsupported intervals, and overlapping split boundaries.
6. Do not build ingestion, training, database tables, APIs, or UI in this task.
7. Add focused unit tests.

Preserve existing repository conventions.
Do not add migrations.
Report exact files before editing and targeted test results afterward.
```

---

# 7. Phase 3 — Deterministic Replay Clock

## Objective

Create the authoritative simulated time source.

## Prompt 3.1

```text
Implement a deterministic historical ReplayClock for Historical Learning Intelligence.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- existing replay engine code
- existing time abstractions
- existing checkpoint/restart conventions

Requirements:

1. ReplayClock must:
   - start at an explicit timestamp,
   - advance only through explicit calls,
   - expose current simulated time,
   - enforce monotonic progression,
   - support fixed intervals,
   - support pause/resume state,
   - serialize and restore state deterministically.
2. Wall-clock time must not affect replay decisions.
3. Reject:
   - backward movement,
   - invalid intervals,
   - restore state inconsistent with the manifest.
4. Add tests proving:
   - deterministic repeated runs,
   - identical restored progression,
   - no wall-clock dependency,
   - rejection of backward movement.
5. Do not integrate market data yet.
6. Do not touch production orchestration.

Use the smallest bounded implementation.
No migration.
```

---

# 8. Phase 4 — Point-in-Time Snapshot Builder

## Objective

Build snapshots that expose only information available at replay time.

## Prompt 4.1

```text
Implement the first PointInTimeSnapshotBuilder for BTC-USD hourly historical replay.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- existing candle models and repositories
- existing replay code
- ReplayClock implementation
- DatasetManifest implementation

Scope:

1. Input:
   - ReplayClock current timestamp
   - approved DatasetManifest
   - ordered candle source
2. Output immutable snapshot:
   - asset
   - venue
   - interval
   - as_of timestamp
   - visible candle window
   - data availability metadata
   - dataset_id
3. The builder must never include candles whose availability timestamp is after `as_of`.
4. Define explicit candle visibility semantics, including whether a candle becomes visible at open or close; choose the existing OmniTrade convention when present.
5. Reject gaps or insufficient history according to explicit policy.
6. Add leakage-focused tests:
   - future candle excluded,
   - current incomplete candle excluded when appropriate,
   - exact boundary behavior,
   - deterministic snapshot identity.
7. Do not add indicators, models, or database writes.

No migration.
```

---

# 9. Phase 5 — Feature and Target Pipelines

## Objective

Create strict separation between model inputs and future-derived labels.

## Prompt 5.1 — Feature Schema

```text
Implement versioned feature-schema and preprocessing foundations for Historical Learning Intelligence.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- PointInTimeSnapshotBuilder
- existing indicator utilities
- repository numeric conventions

Initial feature set:

- 1-period log return
- 6-period return
- 24-period return
- rolling volatility
- volume change
- candle range percentage
- moving-average distance
- drawdown from rolling high
- hour-of-day
- day-of-week

Requirements:

1. Features must be computed only from the visible snapshot.
2. Define a versioned FeatureSchema.
3. Return ordered, deterministic numeric vectors plus missing-value masks if needed.
4. Reject NaN/inf unless explicitly represented by policy.
5. Avoid full-dataset normalization in this task.
6. Add tests proving no future data is required.
7. Add exact expected-value tests on a small synthetic candle series.

Do not train a model.
No migration.
```

## Prompt 5.2 — Target Schema

```text
Implement versioned target generation for BTC-USD hourly supervised learning.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- DatasetManifest
- candle source conventions
- feature pipeline

Initial primary target:
- next-hour net return after configured fee and slippage assumptions

Secondary report-only targets:
- next-24-hour return
- maximum favorable excursion over 24 hours
- maximum adverse excursion over 24 hours

Requirements:

1. Targets may read future candles, but must remain structurally separate from feature generation.
2. Define explicit execution timing assumptions.
3. Define missing-horizon behavior.
4. Define deterministic target identity.
5. Add tests proving:
   - feature code cannot access target data,
   - fee/slippage are included,
   - horizon boundaries are correct,
   - missing future data is handled explicitly.

Do not create action labels yet.
No migration.
```

---

# 10. Phase 6 — Chronological Dataset Builder

## Objective

Create training, validation, and test datasets without temporal leakage.

## Prompt 6.1

```text
Implement the chronological DatasetBuilder for Historical Learning Intelligence.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- DatasetManifest
- ReplayClock
- PointInTimeSnapshotBuilder
- feature pipeline
- target pipeline

Requirements:

1. Build examples chronologically.
2. Produce separate train, validation, and test iterables.
3. Apply configurable embargo/purge boundaries when windows or target horizons overlap.
4. Never fit preprocessing on validation or test data.
5. Support chunked or streaming iteration suitable for 32 GB RAM.
6. Avoid loading the entire dataset into memory.
7. Preserve deterministic example order and example IDs.
8. Add tests for:
   - chronological ordering,
   - split isolation,
   - embargo correctness,
   - deterministic repeated builds,
   - bounded-memory iteration using a synthetic large stream.

Do not train models.
No migration.
```

---

# 11. Phase 7 — Baseline Models

## Objective

Establish the minimum performance evidence neural models must beat.

## Prompt 7.1

```text
Implement baseline predictors and an evaluation contract for BTC-USD hourly Historical Learning Intelligence.

Required baselines:

1. Zero-return predictor
2. Previous-return predictor
3. Simple moving-average directional predictor
4. Logistic-regression positive-return classifier, if repository dependencies support it cleanly

Requirements:

- use the chronological DatasetBuilder,
- train only on training data,
- select only with validation data,
- report untouched test metrics,
- include prediction loss,
- directional accuracy,
- calibration where applicable,
- net simulated return under one simple documented action mapping,
- fees and slippage.

Do not implement a neural network yet.
Do not optimize heavily.
Add deterministic tests with synthetic data.
No migration.
```

---

# 12. Phase 8 — Feedforward Neural Network Training

## Objective

Implement the first actual neural-learning loop.

## Prompt 8.1 — Model

```text
Implement a compact PyTorch feedforward model for BTC-USD hourly return prediction.

Read:

- docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md
- DatasetBuilder
- baseline evaluation contract
- repository dependency conventions

Requirements:

1. Create a small versioned MLP model.
2. Input dimension comes from FeatureSchema.
3. Initial architecture should remain CPU-friendly.
4. Output one predicted next-hour net return.
5. Forward method must be explicit and typed.
6. Add tests for:
   - output shape,
   - deterministic initialization under seed,
   - CPU execution,
   - optional CUDA execution when available,
   - serialization compatibility.

Do not implement the full trainer in this task.
No migration.
```

## Prompt 8.2 — Training Loop

```text
Implement the first governed PyTorch training loop for Historical Learning Intelligence.

Requirements:

1. Perform:
   - feedforward pass,
   - MSE loss computation,
   - zero_grad,
   - backward,
   - gradient clipping,
   - optimizer step.
2. Use AdamW.
3. Support CPU by default and CUDA when safely available.
4. Use bounded batch sizes appropriate for GTX 1650 4 GB VRAM.
5. Add:
   - deterministic seeds,
   - train/validation loss history,
   - early stopping,
   - best-validation checkpoint capture,
   - explicit failure on NaN/inf loss or gradients.
6. Never train on validation or test examples.
7. Add tests proving:
   - weights change after a training step,
   - loss decreases on a learnable synthetic dataset,
   - validation examples do not enter optimizer steps,
   - deterministic repeated run under fixed seed,
   - NaN gradients fail closed.

Do not implement model promotion.
No migration.
```

---

# 13. Phase 9 — Model Registry and Checkpoints

## Objective

Make every model result reproducible and immutable.

## Prompt 9.1

```text
Implement an immutable research Model Registry and checkpoint manifest for Historical Learning Intelligence.

Required metadata:

- model_id
- model family
- architecture version
- training_run_id
- dataset_manifest_id
- feature_schema_version
- target_schema_version
- hyperparameters
- random seeds
- optimizer
- loss function
- checkpoint checksum
- code commit
- train/validation/test metrics
- status

Statuses:

- TRAINED
- VALIDATED
- REJECTED
- RESEARCH_APPROVED
- PAPER_APPROVED
- RETIRED

Requirements:

1. No automatic transition beyond TRAINED.
2. Serialize deterministically.
3. Checkpoint load must verify checksum and schema compatibility.
4. Add tests for tampering, incompatible schemas, and invalid transitions.
5. Use simulation/research persistence only.
6. Do not add production APIs or live behavior.

Only add a migration if the approved design genuinely requires a database table. Prefer file-backed immutable manifests for the first version if consistent with repository conventions.
```

---

# 14. Phase 10 — Evaluation and Walk-Forward Replay

## Prompt 10.1

```text
Implement frozen-model walk-forward evaluation.

Requirements:

1. Load a validated checkpoint.
2. Replay the test period chronologically.
3. Generate predictions without weight updates.
4. Preserve every prediction with:
   - timestamp,
   - feature schema,
   - model_id,
   - predicted return,
   - realized target,
   - error.
5. Compare against all Phase 7 baselines.
6. Report:
   - MSE
   - MAE
   - directional accuracy
   - calibration by prediction bucket
   - net simulated return under one simple action mapping
   - fees
   - drawdown
   - turnover
7. Prove test period was never used for fitting.
8. Produce an immutable evaluation report.

No live integration.
No model promotion.
No migration unless required by an explicitly approved persistence design.
```

---

# 15. Phase 11 — Historical Portfolio Simulation

## Prompt 11.1

```text
Implement the first cost-aware historical portfolio simulator for frozen-model BTC-USD decisions.

Initial actions:

- BUY
- SELL
- WAIT

Initial constraints:

- cash-only or one long BTC position,
- no leverage,
- no shorting,
- fractional quantity support,
- configurable fee,
- configurable slippage,
- minimum notional,
- quantity precision,
- explicit order timing.

Requirements:

1. Accept frozen predictions and an explicit action-mapping policy.
2. Track:
   - cash,
   - quantity,
   - average entry,
   - realized P&L,
   - unrealized P&L,
   - equity,
   - drawdown,
   - turnover,
   - fees.
3. Reject impossible fills.
4. Add deterministic accounting tests.
5. Keep this simulator separate from production execution.
6. Do not import live provider clients.

No migration unless explicitly approved.
```

---

# 16. Phase 12 — Decision Intelligence Evidence

## Prompt 12.1

```text
Integrate historical-learning replay with simulation-only Decision Intelligence evidence.

For every evaluated replay timestamp, record:

- simulated timestamp
- dataset manifest
- point-in-time snapshot identity
- feature schema
- model_id
- prediction
- confidence if available
- proposed action
- selected action
- rejected alternatives
- simulated portfolio state
- realized outcome when known
- counterfactual BUY / SELL / WAIT outcomes when available

Requirements:

1. Reuse existing Decision Record concepts where safe.
2. Do not write into production Decision Record tables unless the architecture explicitly provides isolated simulation tables.
3. Preserve immutable evidence.
4. Add tests proving simulated records cannot be confused with production records.
5. Add explicit mode/source labels.
6. No live effects.

Only add migrations if simulation persistence has an approved separate schema/database and the task requires them.
```

---

# 17. Phase 13 — Hindsight Feasibility Oracle

## Objective

Measure whether 7% was feasible in hindsight without confusing hindsight with prediction.

## Prompt 13.1

```text
Implement a bounded research-only Hindsight Feasibility Oracle for one asset and one 24-hour window.

Purpose:

Estimate the maximum feasible net return obtainable with perfect future knowledge under explicit constraints.

Initial constraints:

- BTC-USD only,
- hourly decision points,
- no leverage,
- no shorting,
- one open position maximum,
- configurable fees and slippage,
- realistic quantity precision,
- no impossible intra-candle fills,
- deterministic route calculation.

Requirements:

1. The oracle may use future data.
2. Every output must be labeled HINDSIGHT_ORACLE.
3. It must never implement or expose a decision-time trading signal.
4. Return:
   - maximum feasible net return,
   - trade sequence,
   - fees,
   - number of switches,
   - whether 7% was feasible.
5. Add tests proving:
   - fees change feasibility,
   - impossible fills are excluded,
   - output is deterministic,
   - oracle outputs cannot enter predictive feature pipelines.

Do not integrate with live or paper execution.
```

## Prompt 13.2 — Learnability Gap

```text
Implement a Learnability Gap report comparing:

- Hindsight Feasibility Oracle result
- frozen predictive-model result
- simple baseline results

For each 24-hour period report:

- hindsight maximum feasible return
- predictive realized return
- best baseline return
- 7% hindsight feasibility
- predictive probability assigned to reaching 7%
- realized target attainment
- hindsight-minus-predictive gap

The report must clearly distinguish:

- possible with perfect future knowledge
- predictable from point-in-time information
- actually achieved by the frozen model

Add deterministic tests.
No live integration.
```

---

# 18. Phase 14 — Multi-Asset Expansion

## Prompt 14.1

```text
Extend point-in-time replay from BTC-USD to a bounded three-asset universe:

- BTC-USD
- ETH-USD
- SOL-USD

Requirements:

1. Use only approved historical datasets.
2. Align timestamps without forward-filling unavailable future values.
3. Preserve per-asset availability masks.
4. Do not assume every asset existed for the entire period.
5. Prevent survivorship bias where repository data supports historical universe membership.
6. Extend FeatureSchema with:
   - asset identity
   - same-timestamp cross-asset returns
   - lagged cross-asset features
7. Keep training CPU-compatible and memory-bounded.
8. Add leakage and alignment tests.

Do not implement allocation yet.
```

## Prompt 14.2

```text
Implement and compare two bounded multi-asset model approaches:

A. One shared MLP with asset identity input.
B. Separate MLP per asset using the same architecture.

Requirements:

- identical chronological splits,
- identical cost assumptions,
- per-asset and aggregate metrics,
- no broad hyperparameter search,
- immutable comparison report,
- explicit conclusion based on evidence.

Do not choose a winner automatically for production use.
```

---

# 19. Phase 15 — Standardized Opportunity Contract

## Prompt 15.1

```text
Implement the versioned CrossAssetOpportunity contract.

Fields:

- asset
- as_of
- horizon
- expected_return_pct
- positive_return_probability
- downside_p10_pct
- upside_p90_pct
- expected_cost_pct
- confidence
- liquidity status
- model_id
- feature_schema_version
- dataset_id

Requirements:

1. Validate probabilities and quantile ordering.
2. Preserve numeric precision.
3. Serialize deterministically.
4. Prevent stale opportunities from being used.
5. Add tests for incompatible model horizons and invalid distributions.

Do not allocate capital yet.
No live integration.
```

---

# 20. Phase 16 — Baseline Global Capital Allocator

## Objective

Build the first layer above asset specialists without reinforcement learning.

## Prompt 16.1

```text
Implement a deterministic baseline Global Capital Allocator for historical research.

Inputs:

- CrossAssetOpportunity records
- current simulated portfolio
- cash
- fees
- switching costs
- maximum position percentage
- maximum turnover
- allowed assets

Initial behavior:

- choose WAIT/cash or one asset,
- rank by expected net return adjusted by downside and confidence,
- reject stale or invalid opportunities,
- preserve full reason evidence.

Requirements:

1. No live authority.
2. No leverage.
3. No shorting.
4. No automatic model promotion.
5. Return:
   - selected allocation
   - rejected alternatives
   - expected return
   - downside estimate
   - confidence
   - switching cost
   - decision explanation.
6. Add deterministic tests.
7. Compare against:
   - always BTC
   - equal-weight
   - highest raw predicted return
   - always cash.

Do not implement reinforcement learning.
```

---

# 21. Phase 17 — Target-Attainment Probability

## Prompt 17.1

```text
Implement research reporting for a requested portfolio target such as 7% over 24 hours.

The system must not treat the target as guaranteed or as permission to violate constraints.

Inputs:

- current simulated portfolio
- available CrossAssetOpportunity records
- target return
- target horizon
- allocator constraints

Outputs:

- estimated probability of reaching target
- expected return
- downside probability
- expected drawdown
- recommended current allocation
- confidence interval or uncertainty summary
- reason target appears attainable or unattainable

Requirements:

1. Start with empirical calibration from historical replay, not an LLM.
2. Compare predicted target probability to actual target attainment.
3. Produce reliability/calibration tables.
4. Preserve target requests and outcomes as research evidence.
5. Add tests proving low-probability targets do not force risk-limit violations.

No execution authority.
```

---

# 22. Phase 18 — Portfolio Planner Research

## Prompt 18.1

```text
Design and implement a research-only receding-horizon Portfolio Planner baseline.

Do not use reinforcement learning in the first version.

At each historical replay step:

1. Receive current portfolio state.
2. Receive current opportunity set.
3. Produce a one-step allocation recommendation.
4. Advance the replay clock.
5. Recalculate from new point-in-time information.
6. Record route changes and invalidated prior assumptions.

Requirements:

- continual replanning,
- explicit transaction costs,
- explicit uncertainty,
- no fixed route assumed to remain valid,
- no look-ahead data,
- no live authority,
- full Decision Intelligence evidence.

Compare against the Phase 16 one-step allocator.

Do not claim a guaranteed route to the requested target.
```

---

# 23. Future Reinforcement Learning Gate

Reinforcement learning must not begin until all of the following are proven:

- deterministic replay,
- leakage tests,
- stable portfolio accounting,
- cost-aware fills,
- supervised baseline,
- walk-forward evaluation,
- immutable model registry,
- opportunity contracts,
- deterministic allocator baseline,
- target calibration,
- Decision Intelligence evidence.

Before RL implementation, create a separate ADR evaluating:

- reward design,
- offline RL versus online simulation,
- action space,
- state representation,
- reward hacking risks,
- distribution shift,
- drawdown penalties,
- turnover penalties,
- target-forcing risks,
- safe promotion boundaries.

---

# 24. Recommended Immediate Sequence

The wisest first implementation sequence is:

1. Prompt 0.1 — reconnaissance
2. Prompt 1.1 — isolation
3. Prompt 2.1 — dataset manifest
4. Prompt 3.1 — replay clock
5. Prompt 4.1 — snapshot builder
6. Prompt 5.1 — features
7. Prompt 5.2 — targets
8. Prompt 6.1 — dataset builder
9. Prompt 7.1 — baselines
10. Prompt 8.1 — MLP
11. Prompt 8.2 — trainer

At that point OmniTrade will possess its first genuine gradient-trained historical model.

---

# 25. Definition of Done for the First Learning Milestone

The first Historical Learning Intelligence milestone is complete when:

- simulation isolation is proven,
- BTC-USD hourly replay is deterministic,
- future-data leakage tests pass,
- features and labels are separated,
- chronological train/validation/test splits exist,
- baselines are measured,
- a compact MLP performs feedforward inference,
- an explicit loss is calculated,
- backpropagation calculates gradients,
- an optimizer updates weights,
- a checkpoint is saved and reloaded,
- untouched test metrics are produced,
- an immutable experiment report exists,
- no production state was touched.

This milestone does not require profit.

It requires a truthful learning machine.
