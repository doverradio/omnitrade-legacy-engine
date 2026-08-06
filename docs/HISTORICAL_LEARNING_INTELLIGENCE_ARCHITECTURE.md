# HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md

## OmniTrade Legacy Engine — Historical Learning Intelligence Architecture

**Status:** Architectural Design  
**Authority:** Subordinate to `PROJECT_CONSTITUTION.md`, `PROJECT_VISION.md`, `SYSTEM_ARCHITECTURE.md`, `RISK_ENGINE.md`, `DECISION_INTELLIGENCE_ENGINE.md`, and the authoritative project-state documents  
**Implementation Authorization:** None by this document alone  
**Primary Constraint:** Must operate safely on the current development workstation: AMD Ryzen 9 5900X, 32 GB RAM, NVIDIA GTX 1650 4 GB VRAM  
**Primary Goal:** Create the architecture required for OmniTrade to learn from point-in-time historical replay through feedforward inference, explicit loss functions, backpropagation, gradient-based weight updates, and governed model promotion.

---

# 1. Purpose

This document defines the architecture for a new historical learning capability inside OmniTrade.

The capability will allow OmniTrade to:

1. Replay markets chronologically as if each point were being encountered for the first time.
2. Construct point-in-time training examples without future-data leakage.
3. Train neural models through:
   - feedforward passes,
   - explicit prediction targets,
   - loss computation,
   - backpropagation,
   - gradient updates,
   - versioned checkpointing.
4. Learn first from one asset and one prediction problem.
5. Expand deliberately toward multi-asset learning.
6. Produce asset-level opportunity estimates.
7. Feed those estimates into a future governed Global Capital Allocator.
8. Preserve every experiment, model version, prediction, decision, and outcome for later Decision Intelligence analysis.
9. Remain completely isolated from live production authority until explicitly promoted through existing governance.

This architecture does not authorize autonomous model changes, live execution, campaign changes, mandate changes, Risk Engine changes, or production deployment.

---

# 2. Relationship to OmniTrade’s Permanent Architecture

OmniTrade permanently consists of four foundational engines:

1. Market Intelligence
2. Strategy Evolution
3. Portfolio Intelligence
4. Decision Intelligence

Historical Learning Intelligence is **not a fifth foundational engine**.

It is a cross-cutting subsystem distributed primarily across:

- **Market Intelligence**
  - point-in-time market data,
  - historical replay,
  - feature generation,
  - regime context.

- **Strategy Evolution**
  - neural model definitions,
  - supervised learning,
  - reinforcement-learning research,
  - model comparison,
  - governed model promotion.

- **Decision Intelligence**
  - immutable predictions,
  - experiment evidence,
  - decision snapshots,
  - counterfactual labels,
  - model-quality analysis,
  - lessons learned.

- **Portfolio Intelligence**
  - portfolio state used as model context,
  - allocation constraints,
  - future Global Capital Allocator inputs and outputs,
  - performance and drawdown evaluation.

The Risk Engine remains a cross-cutting mandatory authority and is never bypassed.

---

# 3. Architectural Principles

## 3.1 Point-in-Time Truth

At simulated time `T`, the replay system may only provide data that would have been available at or before `T`.

No model, feature, label, transformation, normalization statistic, universe membership list, or portfolio state may contain information learned after `T`.

This includes subtle leakage sources such as:

- future-adjusted prices,
- current constituent lists applied to historical periods,
- delisted assets omitted from the historical universe,
- full-dataset normalization,
- labels accidentally exposed as inputs,
- indicators calculated with centered windows,
- revised macroeconomic data unavailable at the original date,
- survivorship bias,
- timestamp misalignment,
- exchange data that became available after the decision time.

Point-in-time integrity is a hard safety boundary.

## 3.2 Learning Is Observational Until Promoted

A trained model initially has no execution authority.

It may:

- generate predictions,
- generate simulated actions,
- participate in historical replay,
- participate in paper-only evaluation,
- create Decision Intelligence evidence.

It may not:

- submit live orders,
- alter campaign authority,
- alter mandate authority,
- bypass strategy activation rules,
- bypass the Risk Engine,
- modify production risk policy,
- auto-promote itself,
- replace production strategy logic without explicit human review.

## 3.3 Small Models Before Large Models

The first goal is not maximum parameter count.

The first goal is a trustworthy learning pipeline.

The initial architecture must support useful models that can train on the current workstation:

- linear baselines,
- logistic regression,
- gradient-boosted baselines,
- compact multilayer perceptrons,
- small temporal convolutional networks,
- small recurrent networks,
- compact attention models only after baselines are proven.

A model with a few thousand or a few million parameters is acceptable.

No implementation may assume access to expensive GPUs.

## 3.4 Evidence Before Complexity

Each model must beat defined baselines on genuinely unseen data after realistic fees and slippage before the next complexity layer is justified.

Required comparisons include, where applicable:

- always cash,
- buy and hold,
- random policy,
- simple momentum,
- simple mean reversion,
- existing deterministic OmniTrade strategies.

## 3.5 Reproducibility

Every training run must preserve:

- dataset version,
- replay configuration,
- asset universe,
- feature schema version,
- target schema version,
- model architecture version,
- hyperparameters,
- random seeds,
- train/validation/test boundaries,
- optimizer,
- loss function,
- checkpoint identity,
- code commit,
- dependency versions,
- evaluation outputs.

A model result that cannot be reproduced is not valid evidence.

## 3.6 Fail Closed

If point-in-time integrity, dataset identity, feature completeness, checkpoint identity, or evaluation integrity cannot be proven, the run must stop and produce explicit failure evidence.

No fallback may silently substitute:

- a later dataset,
- a different feature schema,
- a missing model,
- a default normalization state,
- production data,
- live execution.

---

# 4. High-Level Architecture

```text
Historical Data Sources
        |
        v
Historical Data Registry
        |
        v
Point-in-Time Data Store
        |
        v
Deterministic Replay Clock
        |
        v
Point-in-Time Market Snapshot
        |
        v
Feature Pipeline
        |
        +----------------------------+
        |                            |
        v                            v
Training Dataset Builder      Replay Decision Context
        |                            |
        v                            v
Neural Model Training         Frozen Model Inference
        |                            |
        v                            v
Model Registry                Simulated Action Proposal
        |                            |
        +------------+---------------+
                     |
                     v
Historical Portfolio Simulator
                     |
                     v
Outcome / Counterfactual Evaluation
                     |
                     v
Decision Intelligence Evidence
                     |
                     v
Human Review and Governed Promotion
```

Future expansion:

```text
Asset Specialist Models
        |
        v
Cross-Asset Opportunity Layer
        |
        v
Global Capital Allocator
        |
        v
Multi-Step Portfolio Planner
        |
        v
Risk Engine
        |
        v
Paper Execution
        |
        v
Explicit Human-Governed Live Promotion
```

---

# 5. Core Components

## 5.1 Historical Data Registry

The Historical Data Registry records every dataset available for replay.

Each dataset entry should include:

- dataset ID,
- provider,
- asset,
- venue,
- interval,
- start timestamp,
- end timestamp,
- timezone,
- ingestion timestamp,
- source revision,
- adjustment policy,
- known gaps,
- known quality limitations,
- checksum,
- storage location,
- approval status,
- point-in-time suitability.

The registry must distinguish:

- raw source data,
- normalized data,
- adjusted data,
- derived features,
- labels,
- replay-ready snapshots.

No dataset becomes replay-authoritative merely because it exists.

## 5.2 Simulation Persistence Boundary

Historical learning must use a persistence layer that is structurally isolated from production.

Required properties:

- dedicated simulation database URL,
- no fallback to production database configuration,
- separate metadata root where applicable,
- explicit `SimulationMode`,
- an `IsolationGuard`,
- startup failure if production and simulation connection identities overlap,
- no production writes,
- no production migrations,
- no live provider order calls.

The simulation environment may read approved historical exports but must not mutate production state.

## 5.3 Deterministic Replay Clock

The replay clock is the authoritative simulated time source.

Responsibilities:

- advance chronologically,
- expose current simulated timestamp,
- define candle-close boundaries,
- define when data becomes visible,
- support pause/resume,
- support deterministic restart,
- support bounded replay windows,
- preserve replay seed/configuration,
- prevent wall-clock time from influencing results.

No replayed component may call the real current time directly for decision logic.

## 5.4 Point-in-Time Snapshot Builder

At each replay timestamp, the Snapshot Builder produces the exact state available to the model.

Potential contents:

- current and prior OHLCV,
- known indicators,
- market regime features,
- liquidity features,
- spread and fee assumptions,
- available asset universe,
- portfolio cash,
- current positions,
- unrealized P&L,
- recent realized outcomes,
- active constraints,
- model and feature versions.

The snapshot must contain values, not live references.

Once recorded, a snapshot is immutable.

## 5.5 Feature Pipeline

The Feature Pipeline converts snapshots into model-ready tensors or arrays.

Initial feature families may include:

- logarithmic returns,
- rolling returns,
- rolling volatility,
- volume change,
- range and candle structure,
- moving-average distance,
- RSI-like momentum,
- ATR-like volatility,
- trend slope,
- drawdown,
- time-of-day and day-of-week,
- recent cross-asset returns after multi-asset expansion.

The pipeline must:

- fit transformations only on training data,
- version every feature schema,
- store fitted preprocessing state,
- apply identical transformations during validation, testing, replay, and serving,
- reject missing or unexpected features,
- record feature availability masks where necessary.

## 5.6 Target and Label Pipeline

Targets must be separated from features.

Initial supervised targets may include:

- next-period return,
- next-24-hour return,
- probability of positive net return after costs,
- maximum favorable excursion,
- maximum adverse excursion,
- future realized volatility,
- future drawdown,
- best action among BUY / SELL / WAIT under a defined horizon and cost model.

Every target must define:

- horizon,
- execution timing,
- fee model,
- slippage model,
- label timestamp,
- missing-data behavior,
- whether overlapping examples are permitted.

Labels may use future data because they represent ground truth.

Features may not.

## 5.7 Dataset Builder

The Dataset Builder creates immutable dataset manifests.

A manifest should identify:

- dataset version,
- asset(s),
- date range,
- interval,
- feature schema,
- target schema,
- train split,
- validation split,
- test split,
- embargo periods,
- sampling policy,
- missing-data policy,
- preprocessing checkpoint,
- checksum.

Time-series splits must be chronological.

Random shuffling across future and past periods is prohibited for evaluation.

## 5.8 Model Training Service

The training service is responsible for actual machine learning.

Minimum supported training lifecycle:

```text
batch inputs
    |
    v
feedforward pass
    |
    v
predictions
    |
    v
loss function
    |
    v
backpropagation
    |
    v
gradient calculation
    |
    v
optimizer step
    |
    v
updated weights
```

Initial supported model:

- compact multilayer perceptron.

Initial supported optimizer:

- Adam or AdamW.

Initial supported losses:

- mean squared error for return regression,
- binary cross-entropy for positive-return classification,
- cross-entropy for discrete action classification.

The architecture must later support multi-task learning, but version 0 should use one primary target.

## 5.9 Model Registry

Every trained model receives an immutable model identity.

Required metadata:

- model ID,
- model family,
- architecture version,
- checkpoint path,
- checkpoint checksum,
- training-run ID,
- dataset manifest ID,
- feature schema version,
- target schema version,
- metrics,
- promotion status,
- created timestamp,
- code commit,
- approved-by identity where applicable.

Model statuses:

- `TRAINED`
- `VALIDATED`
- `REJECTED`
- `RESEARCH_APPROVED`
- `PAPER_APPROVED`
- `RETIRED`

No status transition is automatic beyond `TRAINED`.

## 5.10 Evaluation Engine

Evaluation must measure more than prediction loss.

Potential metrics:

- mean squared error,
- mean absolute error,
- directional accuracy,
- precision and recall for positive-return classifications,
- calibration,
- rank correlation,
- net simulated return,
- maximum drawdown,
- turnover,
- fee drag,
- win rate,
- downside deviation,
- risk-adjusted return,
- probability of loss,
- probability of reaching a target return,
- Decision Quality metrics.

Evaluation must occur on:

1. training data,
2. validation data,
3. untouched test data,
4. walk-forward replay,
5. later paper-forward observation.

The untouched test set must not guide model selection repeatedly.

## 5.11 Historical Portfolio Simulator

The simulator translates predictions into simulated portfolio effects.

It must model:

- cash,
- positions,
- fractional quantities,
- order timing,
- fees,
- slippage,
- minimum notional,
- quantity increments,
- liquidity constraints,
- rejected orders,
- holding periods,
- exits,
- drawdown,
- realized and unrealized P&L.

The simulator is not the production execution engine.

It must share economic semantics where appropriate without sharing production authority.

## 5.12 Experiment Ledger

Every experiment must create an append-only record.

Experiment evidence includes:

- hypothesis,
- configuration,
- dataset,
- model,
- training result,
- validation result,
- test result,
- baseline comparisons,
- failure reason,
- human conclusion,
- next recommended experiment.

The ledger exists to prevent repeated, undocumented experimentation and hindsight rewriting.

## 5.13 Decision Intelligence Integration

Historical learning should eventually create Decision Records for simulated decisions.

Each record may include:

- simulated timestamp,
- point-in-time snapshot,
- model prediction,
- confidence,
- candidate actions,
- selected action,
- rejected actions,
- expected return,
- expected downside,
- simulated portfolio constraints,
- realized outcome,
- counterfactual outcomes,
- lesson tags,
- model version,
- feature version,
- target version.

This gives OmniTrade a structured learning corpus spanning:

- correct decisions,
- incorrect decisions,
- missed opportunities,
- avoided losses,
- high-confidence errors,
- low-confidence successes.

## 5.14 Asset Specialist Models

An Asset Specialist is a model trained to estimate opportunity and risk for a specific asset or related asset group.

Possible outputs:

- expected return distribution,
- probability of positive net return,
- downside distribution,
- expected holding period,
- volatility,
- liquidity risk,
- confidence,
- regime compatibility.

Asset specialists may share a common architecture while retaining separate model versions.

Separate per-asset models are not automatically preferred over a shared model.

The system must compare:

- one model per asset,
- one shared model with asset embeddings,
- grouped models by asset class,
- hybrid shared encoder plus asset-specific heads.

## 5.15 Cross-Asset Opportunity Layer

The Cross-Asset Opportunity Layer standardizes outputs from asset specialists.

It converts heterogeneous model outputs into a common opportunity contract.

Example:

```json
{
  "asset": "BTC-USD",
  "as_of": "2020-01-01T12:00:00Z",
  "horizon": "4h",
  "expected_return_pct": "0.012",
  "positive_return_probability": "0.64",
  "downside_p10_pct": "-0.018",
  "upside_p90_pct": "0.031",
  "expected_cost_pct": "0.002",
  "confidence": "0.71",
  "model_id": "uuid"
}
```

This layer does not allocate capital.

It creates comparable evidence for allocation.

## 5.16 Global Capital Allocator

The future Global Capital Allocator sits above asset-level models.

It answers:

> Given all current asset opportunities, current portfolio state, costs, uncertainty, and constraints, where should capital be allocated now?

Inputs:

- opportunity contracts from all eligible assets,
- current cash,
- current positions,
- covariance/correlation estimates,
- liquidity,
- fees,
- turnover,
- risk limits,
- target horizon,
- optional aspirational return target.

Outputs:

- recommended allocation,
- recommended sequence or rebalance,
- expected portfolio return distribution,
- probability of reaching target,
- downside probability,
- expected drawdown,
- confidence,
- reasons,
- rejected alternatives.

The allocator initially remains deterministic or supervised.

Reinforcement learning must not be the first implementation.

## 5.17 Multi-Step Portfolio Planner

The planner represents the “GPS for capital” concept.

It does not promise a fixed outcome.

It estimates a route under uncertainty.

Example output:

```text
Current route:
1. Hold cash until confidence threshold is met.
2. Allocate 20% to Asset A.
3. Re-evaluate after one hour.
4. Exit if downside probability exceeds threshold.
5. Rotate only if Asset B's risk-adjusted opportunity exceeds switching costs.
```

The planner must continually recalculate.

It must represent:

- uncertainty,
- route invalidation,
- transaction costs,
- opportunity costs,
- risk budget,
- target probability,
- changing market conditions.

A requested target such as 7% over 24 hours is treated as:

- an objective for probability estimation,
- not an instruction to violate risk limits,
- not a guaranteed result,
- not a reward that forces reckless behavior.

---

# 6. Initial Version Scope

## Version 0: Single-Asset Supervised Learning

Initial scope:

- Asset: BTC-USD
- Interval: one hour
- Model type: compact feedforward neural network
- Input window: configurable, initial candidate 168 hours
- Primary target: next-hour net return or probability of positive next-hour net return
- Secondary reporting targets:
  - next-24-hour return,
  - maximum favorable excursion,
  - maximum adverse excursion
- Historical replay only
- No production writes
- No live execution
- No model authority
- CPU-compatible training
- Optional limited GTX 1650 acceleration

Version 0 exists to prove the pipeline, not profitability.

## Version 1: Walk-Forward Prediction and Simulated Decisions

Adds:

- frozen-model inference during replay,
- BUY / SELL / WAIT decision mapping,
- cost-aware simulated portfolio,
- baseline comparisons,
- Decision Records,
- counterfactual outcomes.

## Version 2: Multi-Asset Shared Model

Adds:

- BTC-USD,
- ETH-USD,
- SOL-USD,
- asset identity feature or embedding,
- synchronized point-in-time snapshots,
- cross-asset features,
- comparative opportunity outputs.

## Version 3: Asset Specialists and Standardized Opportunity Contracts

Adds:

- specialist-model registry,
- standardized forecast distributions,
- per-asset confidence calibration,
- opportunity ranking.

## Version 4: Global Capital Allocator

Adds:

- portfolio-level allocation recommendations,
- switching-cost awareness,
- constrained optimization,
- target-attainment probability,
- no execution authority.

## Version 5: Portfolio Planner

Adds:

- multi-step route planning,
- continual replanning,
- Decision Intelligence feedback,
- paper-only evaluation.

Reinforcement learning remains a later research option, not a prerequisite.

---

# 7. Hardware-Constrained Design

The architecture must be usable on the current workstation.

## 7.1 CPU

The Ryzen 9 5900X is suitable for:

- data normalization,
- feature generation,
- replay,
- classical baselines,
- compact neural-network training,
- parallel data preparation,
- bounded hyperparameter search.

## 7.2 RAM

With 32 GB RAM:

- datasets must be streamed or memory-mapped when large,
- replay should operate in chunks,
- training should use bounded batch sizes,
- feature matrices should use appropriate dtypes,
- unnecessary DataFrame copies must be avoided,
- cached datasets must have explicit limits,
- no design may require loading all assets and all history into memory simultaneously.

## 7.3 GPU

The GTX 1650 4 GB may accelerate compact models.

Requirements:

- CPU fallback must always work,
- small batch sizes,
- mixed precision only when numerically safe,
- no assumption that complete models fit in VRAM,
- no architecture chosen merely because it is fashionable.

## 7.4 Storage

Historical data should use efficient formats such as:

- Parquet,
- Arrow,
- compressed NumPy arrays,
- database-backed chunk retrieval.

Raw, normalized, feature, and label data must remain distinguishable.

---

# 8. Training Methodology

## 8.1 Baseline First

Before neural training, establish simple baselines.

Examples:

- predict zero return,
- predict previous-period return,
- moving-average signal,
- logistic regression,
- gradient-boosted tree.

The neural model must justify its complexity.

## 8.2 Chronological Splits

Example:

```text
Training: 2015–2021
Validation: 2022–2023
Test: 2024
Walk-forward: 2025
Forward paper observation: 2026
```

Actual dates depend on available, trustworthy data.

## 8.3 Embargo and Purging

When examples use overlapping windows or future horizons, data splits must include an embargo sufficient to prevent target overlap across train and validation/test boundaries.

## 8.4 Loss Functions

Potential version-0 losses:

### Regression

Predict net future return.

```text
loss = mean squared error(predicted_return, realized_net_return)
```

### Classification

Predict whether net future return is positive.

```text
loss = binary cross entropy(predicted_probability, realized_positive_label)
```

### Later Multi-Task Objective

```text
total_loss =
    return_loss
  + downside_loss_weight * downside_loss
  + volatility_loss_weight * volatility_loss
  + calibration_loss_weight * calibration_loss
```

A portfolio reward function is deferred until the supervised system is trustworthy.

## 8.5 Optimization

Initial recommendations:

- AdamW,
- conservative learning rate,
- gradient clipping,
- early stopping,
- deterministic seeds,
- checkpoint best validation epoch,
- log train and validation curves.

## 8.6 Hyperparameter Discipline

Initial search must remain bounded.

Examples:

- 2–3 hidden-layer widths,
- 2 learning rates,
- 2 window lengths,
- 1–2 dropout values.

No massive brute-force search is justified on current hardware.

---

# 9. Evaluation of the 7% / 24-Hour Objective

The architecture must support the question:

> Given information available at time T, what was the estimated probability of growing capital by 7% over the next 24 hours under allowed constraints?

It must also support the hindsight research question:

> Given perfect future knowledge, did any feasible sequence of trades exist that could have produced 7% net growth over the next 24 hours?

These are different analyses.

## 9.1 Hindsight Feasibility Oracle

A research-only oracle may use full future information to estimate the best feasible route under:

- actual historical prices,
- transaction costs,
- slippage,
- liquidity,
- timing granularity,
- position constraints,
- no impossible fills.

Its purpose is to create an upper bound.

It must never be used as a decision-time model.

## 9.2 Predictive Attainability Model

The predictive system sees only information available at time T.

It estimates:

- probability of reaching 7%,
- expected return,
- downside probability,
- expected drawdown,
- best current allocation,
- uncertainty.

## 9.3 Learnability Gap

The system should explicitly measure:

```text
Hindsight Feasible Return
minus
Predictable Achievable Return
```

This gap reveals how much of the theoretical route was actually identifiable in advance.

That distinction is central to honest evaluation.

---

# 10. Governance and Promotion

Model promotion sequence:

```text
TRAINED
    |
    v
VALIDATED
    |
    v
RESEARCH_APPROVED
    |
    v
PAPER_APPROVED
    |
    v
Observed Forward in Paper
    |
    v
Human Review
    |
    v
Future Explicit Production Initiative
```

Required promotion evidence:

- no leakage found,
- reproducible run,
- baseline comparisons,
- untouched test results,
- walk-forward results,
- cost-aware portfolio simulation,
- risk and drawdown analysis,
- explanation of failure modes,
- explicit human approval.

No model promotes itself.

---

# 11. Security and Safety Boundaries

Historical Learning Intelligence must never:

- connect to live order submission,
- modify production environment files,
- modify live campaign scope,
- modify mandate scope,
- weaken Risk Engine logic,
- alter production balances,
- write fabricated Decision Records,
- overwrite historical experiment evidence,
- use future data during inference,
- report hindsight-oracle performance as predictive performance,
- imply guaranteed returns.

---

# 12. Proposed Repository Structure

```text
apps/api/app/services/historical_learning/
├── __init__.py
├── config.py
├── modes.py
├── isolation_guard.py
├── replay/
│   ├── clock.py
│   ├── engine.py
│   ├── snapshot_builder.py
│   └── checkpoints.py
├── data/
│   ├── registry.py
│   ├── manifests.py
│   ├── point_in_time_store.py
│   └── quality.py
├── features/
│   ├── schema.py
│   ├── pipeline.py
│   ├── transforms.py
│   └── preprocessing_state.py
├── targets/
│   ├── schema.py
│   ├── returns.py
│   ├── excursions.py
│   └── actions.py
├── datasets/
│   ├── builder.py
│   ├── splits.py
│   ├── embargo.py
│   └── loader.py
├── models/
│   ├── base.py
│   ├── mlp.py
│   ├── registry.py
│   └── checkpoints.py
├── training/
│   ├── trainer.py
│   ├── losses.py
│   ├── optimizers.py
│   ├── early_stopping.py
│   └── metrics.py
├── evaluation/
│   ├── evaluator.py
│   ├── baselines.py
│   ├── walk_forward.py
│   ├── calibration.py
│   └── reports.py
├── simulation/
│   ├── portfolio.py
│   ├── fills.py
│   ├── costs.py
│   └── actions.py
├── experiments/
│   ├── ledger.py
│   ├── manifests.py
│   └── comparison.py
├── opportunity/
│   ├── contract.py
│   └── normalization.py
├── allocator/
│   ├── constraints.py
│   ├── baseline_allocator.py
│   └── evaluator.py
└── oracle/
    ├── hindsight_feasibility.py
    └── learnability_gap.py
```

Tests:

```text
apps/api/tests/historical_learning/
├── unit/
├── integration/
├── leakage/
├── determinism/
├── simulation/
└── evaluation/
```

The exact structure may be adjusted after repository reconnaissance, but the architectural boundaries must remain.

---

# 13. Initial Success Criteria

The first implementation milestone is complete when OmniTrade can:

1. Load an approved BTC-USD hourly historical dataset.
2. Replay it deterministically.
3. Produce point-in-time snapshots.
4. Build leakage-safe chronological datasets.
5. Train a compact feedforward neural network.
6. Perform feedforward inference.
7. Compute an explicit loss.
8. Backpropagate gradients.
9. update weights.
10. Save and reload a versioned checkpoint.
11. Evaluate on untouched test data.
12. Compare against simple baselines.
13. Produce an immutable experiment report.
14. Prove no production state or live authority was touched.

Profitability is not required for this milestone.

Trustworthy learning infrastructure is the milestone.

---

# 14. Long-Term Success Criteria

The long-term architecture succeeds when OmniTrade can:

- learn asset-level opportunity distributions,
- compare opportunities across asset classes,
- estimate target-attainment probability,
- allocate capital under explicit constraints,
- continually replan,
- learn from both actions and inaction,
- preserve every decision and model version,
- improve through governed retraining,
- remain explainable,
- remain auditable,
- preserve capital,
- never confuse hindsight possibility with real-time predictability.

---

# 15. Architectural Decision

Historical Learning Intelligence should be implemented as a production-isolated, CPU-compatible, point-in-time research subsystem that grows from a single compact supervised model into multi-asset opportunity estimation and a governed Global Capital Allocator.

The first priority is not scale.

The first priority is truth:

- truthful time,
- truthful data,
- truthful labels,
- truthful evaluation,
- truthful limits,
- truthful evidence.
