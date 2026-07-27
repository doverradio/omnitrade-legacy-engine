# HISTORICAL_INTELLIGENCE_PLATFORM.md

Version: 1.0
Status: Constitutional Architecture
Priority: Immediate Strategic Build
Scope: Historical Replay, Synthetic Evidence, Counterfactual Research, and Governed Improvement

---

# Purpose

The Historical Intelligence Platform is OmniTrade's synthetic research institution.

It allows the complete autonomous capital-management system to experience historical markets chronologically, generate point-in-time Decision Records, simulate portfolio evolution, compare counterfactual choices, evaluate outcomes, and produce governed improvement evidence without exposing the system to future information.

The platform exists to create trustworthy institutional memory faster than live operation alone can provide.

It does not replace production proving.

It complements production by creating a second, accelerated source of experience.

---

# Strategic Objective

Live markets provide one day of new experience per day.

The Historical Intelligence Platform should be capable of processing years, decades, and eventually centuries of combined market history within bounded compute windows while preserving strict temporal integrity.

The objective is not merely to backtest strategies.

The objective is to simulate the behavior of an entire autonomous capital-management institution across historical time.

---

# Guiding Principle

The system must never know the future.

At simulated time T, every participating engine may access only information that was genuinely knowable at or before T.

Synthetic intelligence must be earned chronologically.

Never granted through hindsight.

---

# Platform Questions

The Historical Intelligence Platform should eventually answer:

- What was knowable at this historical moment?
- Which assets and instruments were genuinely available?
- Which opportunities deserved capital?
- Which existing positions should have been retained, reduced, exited, or replaced?
- How much capital should have been allocated?
- How much should have remained liquid?
- Which provider should have held the capital?
- Where should an order have been routed?
- What execution quality was realistically achievable?
- How did the complete portfolio evolve afterward?
- Which counterfactual decision would have produced a better risk-adjusted outcome?
- Did a proposed strategy or policy improvement survive unseen history?

---

# Relationship to Production

Production and historical replay are distinct evidence environments operating through shared financial logic.

Production proves:

- real authentication
- real balances
- real orders
- real fills
- real fees
- real provider behavior
- real reconciliation
- real unattended operation

Historical replay provides:

- accelerated decision experience
- broad regime coverage
- cross-asset comparison
- counterfactual evaluation
- strategy validation
- policy validation
- portfolio evolution evidence
- error attribution
- improvement research

Neither replaces the other.

Production remains the highest-confidence evidence source.

---

# Canonical Operating Modes

Every compatible OmniTrade engine should support the following operating modes:

- `PRODUCTION`
- `FORWARD_PAPER`
- `HISTORICAL_REPLAY`
- `COUNTERFACTUAL_REPLAY`

The financial decision logic should remain shared across modes.

Only environment adapters should change.

Examples include:

- clock
- market data source
- news and fundamentals source
- account state source
- execution provider
- persistence namespace
- evidence classification

Replay-specific shortcuts inside financial logic are prohibited.

---

# High-Level Architecture

Historical Data Sources

↓

Point-in-Time Data Normalization

↓

Historical Clock

↓

Historical Market Replay Engine

↓

Canonical OmniTrade Decision Pipeline

↓

Synthetic Broker and Execution Model

↓

Synthetic Portfolio and Ledger

↓

Decision Records and Decision Packages

↓

Ground-Truth Outcome Generation

↓

Decision Quality and Counterfactual Evaluation

↓

Research Analytics and Improvement Proposals

↓

Out-of-Sample Validation

↓

Governed Promotion

---

# Core Subsystems

## Historical Replay Orchestrator

Coordinates replay runs from initialization through completion.

Responsibilities include:

- simulation creation
- dataset binding
- engine-version binding
- starting-state initialization
- worker coordination
- checkpointing
- cancellation
- resumption
- completion reporting

---

## Historical Clock

Provides deterministic simulated time.

The clock must:

- advance chronologically
- prevent access beyond the current knowledge cutoff
- support configurable time steps
- remain independent of wall-clock speed
- reproduce identical timelines from identical inputs

Replay may execute slowly or massively accelerated.

Simulated chronology must never change.

---

## Point-in-Time Data Gateway

Provides historical data through a strict temporal boundary.

At simulated time T, the gateway returns only data available at or before T.

It should eventually support:

- candles
- trades
- quotes
- order books
- corporate actions
- economic releases
- earnings
- blockchain events
- protocol events
- historical news
- provider capabilities
- fees
- instrument metadata

All responses should identify their provenance and availability timestamp.

---

## Historical Feature Engine

Computes technical, statistical, regime, correlation, and portfolio features using only point-in-time inputs.

Features must never be precomputed using future observations unless they are reconstructed with a verified point-in-time process.

Every feature should carry:

- feature name
- feature version
- observation cutoff
- source dataset version
- calculation timestamp

---

## Synthetic Broker

Models the provider-facing behavior required for historical execution.

Responsibilities may include:

- order acceptance
- order rejection
- fee calculation
- spread application
- slippage modeling
- partial fills
- latency modeling
- minimum size rules
- precision rules
- provider availability

Synthetic execution confidence should reflect the quality of the historical evidence available.

---

## Synthetic Portfolio and Ledger

Maintains continuous financial state across the replay timeline.

It should track:

- cash
- provider balances
- positions
- cost basis
- realized profit and loss
- unrealized profit and loss
- fees
- reserved capital
- available capital
- portfolio value
- drawdown
- exposure
- liquidity

Every state transition must be auditable and replayable.

---

## Synthetic Evidence Store

Stores replay-generated evidence separately from production evidence.

Synthetic records may share canonical structures with production records, but must carry explicit provenance.

Required provenance should include:

- `record_origin`
- `evidence_class`
- `simulation_id`
- `replay_branch_id`
- `simulated_timestamp`
- `knowledge_cutoff_timestamp`
- `dataset_version`
- `strategy_version`
- `risk_policy_version`
- `engine_version`
- `execution_model_version`

Synthetic evidence must never masquerade as live production evidence.

---

## Ground-Truth Outcome Engine

Reveals outcomes only after simulated time naturally advances.

It may calculate:

- forward returns
- realized trade outcomes
- realized drawdowns
- time to recovery
- maximum favorable excursion
- maximum adverse excursion
- opportunity cost
- realized execution quality
- portfolio contribution

Ground truth is downstream of the committed decision.

It must never influence that decision retroactively.

---

## Counterfactual Branch Engine

Creates isolated alternative timelines from a common historical state.

Examples include:

- BUY versus HOLD
- SELL versus retain
- allocate to Asset A versus Asset B
- remain in cash
- use Strategy A versus Strategy B
- use Risk Policy A versus Risk Policy B
- route through Provider A versus Provider B

Every branch must inherit the same point-in-time evidence boundary.

Branches must remain isolated from one another.

---

## Replay Worker Fleet

Executes replay workloads in parallel.

Workers may be partitioned by:

- asset
- instrument
- provider
- strategy
- policy version
- historical period
- counterfactual branch
- portfolio experiment

Parallelism must never compromise determinism or evidence isolation.

---

## Research Analytics

Aggregates replay evidence into understandable research results.

Potential outputs include:

- return distributions
- drawdown distributions
- regime performance
- strategy comparisons
- asset comparisons
- execution-quality comparisons
- turnover
- capital utilization
- opportunity cost
- portfolio resilience
- failure clusters
- confidence intervals

Analytics should distinguish training, validation, test, forward-paper, and production evidence.

---

## Improvement Proposal Engine

Produces evidence-backed proposals for improving strategies, models, policies, routing, or allocation.

A proposal is not an automatic production change.

Every proposal should identify:

- observed weakness
- supporting replay evidence
- proposed modification
- expected benefit
- expected risk
- affected components
- validation plan
- rollback plan

---

## Replay Promotion Gate

Controls whether replay-supported changes may progress toward production.

A candidate change should pass through:

Historical Training Window

↓

Historical Validation Window

↓

Untouched Historical Test Window

↓

Forward Paper Proving

↓

Bounded Live Production

No change should reach production solely because it performed well on development history.

---

# Integration with OmniTrade Intelligence Engines

## Asset Registry

Defines canonical assets independently from venue symbols.

Historical replay must respect asset launch dates, instrument availability, redenominations, forks, mergers, delistings, and symbol changes.

---

## Asset Universe

Reconstructs the assets genuinely eligible for consideration at each simulated moment.

An asset that did not yet exist or was not yet accessible must not enter the opportunity set.

---

## Multi-Asset Data Pipeline

Supplies synchronized point-in-time evidence across assets and instruments.

The pipeline must prevent faster or more complete datasets from leaking future information into slower datasets.

---

## Market Regime Classification

Classifies the current regime using only evidence available at the simulated timestamp.

Historical regime labels created with hindsight may be used as downstream ground truth, but not as decision-time inputs unless they were available contemporaneously.

---

## Asset Correlation Model

Calculates relationships through rolling point-in-time windows.

Full-history correlation must never be exposed to historical decisions.

---

## Cross-Asset Opportunity Selection

Allows historically eligible opportunities to compete for capital under a shared knowledge boundary.

---

## Opportunity Cost Model

Measures the value of rejected alternatives after outcomes become available.

Opportunity cost must remain downstream of the original decision.

---

## Dynamic Capital Reallocation

Evaluates whether an existing position should retain capital or yield it to a sufficiently superior opportunity after transaction costs, cooldowns, risk, and evidence confidence.

---

## Portfolio Intelligence

Evaluates the synthetic portfolio as a unified investment rather than a collection of isolated trades.

---

## Capital Efficiency Model

Measures how productively capital contributes to growth, liquidity, resilience, diversification, and opportunity readiness throughout the replay timeline.

---

## Multi-Provider Capital Routing

Determines where capital should reside when trustworthy historical provider information exists.

Provider launch dates, supported instruments, settlement behavior, access limitations, and historical reliability must be respected.

---

## Execution Routing

Chooses among venues and instruments genuinely available at the simulated timestamp.

---

## Execution Quality

Measures the difference between expected and modeled realized execution.

Every result should disclose the confidence of the historical execution model.

---

## Decision Intelligence

Consumes synthetic Decision Records, outcomes, counterfactuals, and quality evidence while preserving their evidence class.

---

# Synthetic Data Policy

The platform may use multiple forms of synthetic data.

## Historical Reconstruction

Real historical observations revealed chronologically.

## Modeled Missing Data

Estimated values used where historical evidence is incomplete.

These values must be labeled and confidence-scored.

## Counterfactual Data

Alternative outcomes generated from a shared historical state.

## Stress and Scenario Data

Artificial crises, gaps, outages, liquidity shocks, and regime changes created to test resilience.

## Generative Market Data

Statistically or procedurally generated market histories used for robustness research.

Generated history must never be presented as actual history.

---

# Evidence Classes

Recommended evidence classes include:

- `PRODUCTION_LIVE`
- `FORWARD_PAPER`
- `HISTORICAL_POINT_IN_TIME`
- `HISTORICAL_MODELED`
- `COUNTERFACTUAL`
- `STRESS_SCENARIO`
- `GENERATIVE_SYNTHETIC`

Every analytic conclusion should disclose the evidence classes supporting it.

---

# Evidence Hierarchy

Highest confidence

Production Evidence

↓

Forward Paper Evidence

↓

Point-in-Time Historical Replay Evidence

↓

Historically Modeled Evidence

↓

Counterfactual Evidence

↓

Stress and Generative Synthetic Evidence

All classes may be useful.

They are not equally authoritative.

---

# Anti-Leakage Requirements

The platform must defend against:

- look-ahead bias
- future-data leakage
- survivorship bias
- delisted-asset omission
- revised-data leakage
- future universe membership
- future provider availability
- future strategy configuration
- cross-branch contamination
- training/test contamination
- repeated tuning against untouched test history

If point-in-time integrity cannot be guaranteed, the run must fail closed or be explicitly downgraded to a lower-confidence evidence class.

---

# Data Provenance

Every historical input should identify, where applicable:

- event time
- publication time
- effective time
- revision time
- ingestion time
- source
- source version
- dataset version
- quality level
- reconstruction method

The system must distinguish when something happened from when it became knowable.

---

# Determinism and Reproducibility

Given identical:

- datasets
- seeds
- engine versions
- strategy versions
- risk policies
- execution models
- starting state
- replay configuration

The platform should produce identical results.

Non-deterministic models must record seeds, versions, prompts, and sampling configuration sufficient for reproducibility where technically possible.

---

# Isolation

Historical replay must remain isolated from production.

It must not:

- submit real orders
- mutate live balances
- modify production mandates
- overwrite production Decision Records
- affect production schedules
- alter production risk state
- impersonate production evidence

Separate persistence namespaces and explicit runtime modes are mandatory.

---

# Scalability

The platform should evolve toward support for:

- thousands of assets
- multiple asset classes
- multiple providers
- decades or centuries of history
- millions or billions of decision moments
- parallel replay workers
- distributed compute
- checkpointed long-running experiments
- reusable feature caches
- columnar historical storage
- incremental reruns

Scalability must not weaken temporal integrity.

---

# Performance Philosophy

The fastest replay is not automatically the best replay.

Performance should be optimized only after correctness, determinism, and point-in-time integrity are proven.

The platform should support progressively richer fidelity levels.

Example:

Level 1

Candle-based replay with deterministic fills.

Level 2

Quote-aware replay with spreads and fees.

Level 3

Order-book-aware replay with partial fills and latency.

Level 4

Provider-aware replay with outages, limits, and capital routing.

Level 5

News, fundamentals, macroeconomic, and cross-market replay.

Lower-fidelity runs may scan broad hypothesis spaces.

Higher-fidelity runs should validate finalists.

---

# Cost Governance

Replay compute and data usage should remain budget-aware.

Every run should estimate:

- assets
- time range
- resolution
- strategies
- branches
- expected decision moments
- expected storage
- expected compute
- expected duration

The orchestrator should support budgets, quotas, cancellation, and priority queues.

---

# Initial Implementation Boundary

The first implementation should remain intentionally narrow.

Recommended initial scope:

- one asset
- one venue instrument
- candle data only
- deterministic historical clock
- existing production strategy logic
- existing risk and economics logic
- synthetic cash and position ledger
- deterministic market-order fill model
- explicit historical evidence provenance
- no production writes
- reproducible Decision Records
- one complete end-to-end replay test

The first goal is not maximum asset coverage.

The first goal is proof that the actual OmniTrade decision pipeline can run through historical time without cheating.

---

# Expansion Sequence

## Stage 1: Golden Historical Path

One asset.

One strategy.

One historical period.

One synthetic portfolio.

Complete Decision Record lineage.

## Stage 2: Longitudinal Single-Asset Replay

Run the asset from earliest trustworthy history to the present.

## Stage 3: Multi-Asset Opportunity Competition

Allow several synchronized assets to compete for capital.

## Stage 4: Persistent Portfolio Intelligence

Enable allocation, concentration, liquidity, and reallocation decisions across the combined portfolio.

## Stage 5: Counterfactual Branching

Compare alternative decisions and policies from identical historical states.

## Stage 6: Strategy and Risk Tournaments

Evaluate multiple strategies and policy versions under shared evidence.

## Stage 7: Multi-Provider and Execution Fidelity

Add historically accurate venue and execution behavior where evidence supports it.

## Stage 8: Cross-Asset-Class Historical Intelligence

Expand into equities, ETFs, commodities, metals, foreign exchange, fixed income, and other supported markets.

## Stage 9: Governed Self-Improvement

Generate proposals, validate them out of sample, and promote only after forward proving.

---

# Failure Behavior

If future leakage is detected:

Abort the run.

If evidence provenance is incomplete:

Return `INSUFFICIENT_POINT_IN_TIME_EVIDENCE` or downgrade the evidence class.

If replay state diverges from deterministic expectations:

Stop and preserve diagnostics.

If production isolation cannot be guaranteed:

Refuse to start.

The platform must never fabricate certainty or quietly continue after integrity failure.

---

# Explainability

Every replay result should explain:

- what the system knew
- what it did not know
- which opportunity it selected
- which alternatives it rejected
- why capital was allocated or withheld
- which risk and economics rules applied
- how execution was modeled
- what happened afterward
- how confident the system is in the evidence

---

# Design Principles

The Historical Intelligence Platform should remain:

- Deterministic
- Point-in-Time Correct
- Auditable
- Explainable
- Replayable
- Provider Neutral
- Asset Neutral
- Strategy Neutral
- Evidence Classified
- Production Isolated
- Horizontally Scalable
- Cost Governed
- Extensible

---

# Constitutional Principles

The future must remain hidden.

History must unfold chronologically.

Synthetic evidence must never impersonate production evidence.

The same financial logic should govern production and replay.

Every result must disclose its provenance and confidence.

Broad historical coverage must never excuse weak data integrity.

A proposed improvement must survive unseen evidence before promotion.

Historical replay exists to create trustworthy experience, not impressive hindsight.

Production remains the final authority.

---

# Long-Term Vision

The Historical Intelligence Platform transforms OmniTrade from a system that waits for markets to provide experience into a system capable of building institutional memory continuously.

While production advances one moment at a time, the historical platform may allow thousands of isolated research workers to experience different assets, strategies, providers, regimes, and portfolio choices in parallel.

The result is not simply a faster backtester.

It is an autonomous financial research institution capable of asking, across vast histories:

What deserved capital?

What preserved capital?

What wasted capital?

What decision generalized?

What strategy survived?

What risk was justified?

What improvement remained valid when the system could no longer see the answers?

Over time, this platform should become the evidence foundation for Adaptive Strategy Selection, the Self-Improvement Framework, the Market Knowledge Graph, the Financial Ontology, and Global Capital Intelligence.

The ultimate objective is to let OmniTrade accumulate experience at machine speed while preserving the honesty, discipline, and stewardship required for real capital.
