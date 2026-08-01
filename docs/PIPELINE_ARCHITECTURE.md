# OmniTrade Pipeline Architecture

# Status: Draft for Peer Review

Version: 0.1

Owner: OmniTrade Architecture

Supersedes: PIPELINE_ARCHITECTURE_VISION.md

Next Step:
Peer review by Claude, followed by architectural revision before implementation.

OmniTrade is not fundamentally a trading application. It is an information-transformation system whose primary purpose is to convert raw market observations into governed capital-allocation decisions through deterministic, auditable, and replayable pipeline stages.

**Architecture Review Draft**

This document is intended to be reviewed by Claude before implementation.

It defines a higher-level information-flow architecture for OmniTrade. It does not replace the Project Constitution, Project Vision, System Architecture, Risk Engine, Decision Intelligence Engine, API Contracts, or existing operational documents. It defines how information should move through the platform so the system can be normalized, audited, replayed, tested, scaled, and evolved without creating parallel or contradictory execution paths.

---

# 1. Purpose

OmniTrade should be understood as a deterministic information-transformation pipeline.

The platform receives raw external information, converts it into canonical internal contracts, processes it through a governed sequence of stages, produces outputs and results, stores the complete causal history, and feeds validated knowledge back into future decisions.

The governing flow is:

```text
RAW INPUT
    ↓
INPUT
    ↓
NORMALIZATION
    ↓
VALIDATION
    ↓
CANONICAL INPUT
    ↓
MARKET INTELLIGENCE
    ↓
FEATURE ENGINEERING
    ↓
STRATEGY
    ↓
ECONOMICS
    ↓
DECISION
    ↓
RISK
    ↓
GOVERNANCE
    ↓
EXECUTION
    ↓
PROVIDER
    ↓
RECONCILIATION
    ↓
ACCOUNTING
    ↓
KNOWLEDGE
    ↓
RESULTS
    ↓
FEEDBACK
    ↓
NEW INPUT
```

This architecture exists so OmniTrade can answer, for every material outcome:

> What exact information entered the platform, how was it transformed, what did each stage conclude, what action resulted, what was verified, and what did the system learn?

---

# 2. INPUT, PROCESS, OUTPUT, RESULTS, AND FEEDBACK

The detailed pipeline in this document is an expanded implementation of the higher-level OmniTrade operating model:

```text
INPUT
    ↓
PROCESS
    ↓
OUTPUT
    ↓
RESULTS
    ↓
FEEDBACK
    ↓
NEW INPUT

# 2. Governing Principles

## 2.1 One Responsibility Per Stage

Each pipeline stage must have one clearly defined responsibility.

A stage must not silently perform work that belongs to another stage.

Examples:

- Normalization must not make strategy decisions.
- Strategy must not place orders.
- Risk must not fabricate missing market data.
- Execution must not recalculate strategy logic.
- Reconciliation must not rewrite the original execution intent.
- Knowledge capture must not alter live behavior automatically.

## 2.2 One Versioned Input Contract

Every stage must receive a stable, versioned input contract.

## 2.3 One Versioned Output Contract

Every stage must emit a stable, versioned output contract.

## 2.4 Deterministic Transformation

Given the same:

- input contract,
- stage implementation version,
- configuration version,
- policy version,
- and execution context,

the stage should produce the same result, except where explicitly documented nondeterminism exists.

## 2.5 Auditability

Every stage must preserve enough evidence to explain:

- what it received,
- what it produced,
- why it produced it,
- which code and configuration versions were used,
- whether the stage passed, blocked, failed, quarantined, or deferred,
- and what downstream object was caused by its output.

## 2.6 Replayability

Every stage must be replayable independently from persisted inputs.

## 2.7 Provider Neutrality

Provider-specific concepts must terminate at the Input and Normalization boundary.

No downstream stage may depend on Kraken, Binance, Alpaca, Coinbase, Polygon, or any future provider-specific schema unless it is explicitly operating as a provider adapter.

## 2.8 Same Business Pipeline for Live and Replay

Historical replay must execute through the same normalized contracts and stage transformations as live operation.

No separate backtest-only decision pipeline may duplicate or imitate the live decision logic.

## 2.9 Risk and Governance Remain Authoritative

No operating mode may bypass:

- the Risk Engine,
- mandate governance,
- campaign authority,
- audit evidence,
- execution authorization,
- or fail-closed safety behavior.

## 2.10 Deployment Is Not the Contract

A stage may be implemented as:

- an in-process function,
- an internal service,
- an HTTP endpoint,
- a queue consumer,
- or an independent distributed service.

The semantic contract must remain stable even when deployment changes.

# Architectural Invariants

The following rules are considered permanent unless explicitly superseded by a future governing architecture decision.

- Every pipeline stage owns exactly one primary responsibility.
- Every stage consumes a versioned canonical contract.
- Every stage produces a versioned canonical contract.
- Raw provider evidence is immutable.
- Canonical lineage is immutable.
- Provider-specific schemas terminate at the Normalization Layer.
- Live operation and Historical Replay share the same business pipeline.
- Risk and Governance remain mandatory in every operating mode.
- Every stage must be independently testable.
- Every stage must be independently replayable.
- Every stage must be independently auditable.
- Deployment is independent from contract design.

---

## Decision Cycles and Feedback Control

The canonical pipeline shown in this document represents the processing performed during a single decision cycle.

OmniTrade as a whole is not a purely linear system.

It is a feedback control system composed of many deterministic pipeline executions.

Each completed cycle produces a new portfolio state, accounting state, knowledge state, and operational state.

Those states become inputs to the next decision cycle.

Conceptually:

```text
Decision Cycle T
        │
        ▼
Accounting Result
        │
        ▼
Portfolio State T
        │
        ▼
Decision Cycle T+1
```

This distinction is critical.

Within a decision cycle the pipeline flows in one direction.

Across decision cycles the system forms a controlled feedback loop.

Historical replay must preserve this temporal ordering.

---

# 3. Canonical Information Flow

The pipeline should be modeled as a chain of typed transformations.

```text
RawProviderEvent
    ↓
CanonicalInput
    ↓
MarketContext
    ↓
FeatureSnapshot
    ↓
StrategyEvaluation
    ↓
EconomicEvaluation
    ↓
DecisionIntent
    ↓
RiskDecision
    ↓
GovernedExecutionIntent
    ↓
ExecutionPackage
    ↓
ProviderOrder
    ↓
ProviderResult
    ↓
ReconciliationResult
    ↓
AccountingResult
    ↓
KnowledgeRecord
    ↓
ResultSummary
    ↓
FeedbackCandidate
```

The output of one stage becomes the input of the next.

Each transformation must be explicit, typed, versioned, and auditable.

---

# 4. Canonical Contract Model

Normalization must not produce one giant universal object.

OmniTrade should define a family of canonical contracts that share a common envelope.

## 4.1 Canonical Event Envelope

Every canonical object should include a standard envelope containing fields such as:

```text
event_id
event_type
schema_version
source
provider
venue
asset_class
asset_id
instrument_id
display_symbol
occurred_at
available_at
availability_source
availability_confidence
revision_as_of
supersedes_record_id
received_at
normalized_at
correlation_id
causation_id
run_id
raw_record_id
integrity_hash
quality_status
stage_version
configuration_version
```

Historical `available_at` timestamps must represent when the information
became knowable to OmniTrade, not when historical data was later
downloaded or ingested.

If reliable point-in-time availability cannot be established, the
record must be downgraded or rejected for replay according to evidence
policy.

Asset identity must remain stable even when provider symbols,
ticker symbols, exchanges, or contract identifiers change.

Display symbols exist for human readability.

Canonical processing should reference stable asset and instrument
identifiers.

The envelope creates consistency across unrelated domains without forcing them into the same payload shape.

## 4.2 Canonical Contract Families

Expected canonical families include:

```text
CanonicalCandle
CanonicalTradePrint
CanonicalQuote
CanonicalOrderBookSnapshot
CanonicalFundingRate
CanonicalCorporateAction
CanonicalEconomicEvent
CanonicalNewsEvent

CanonicalBalanceSnapshot
CanonicalPositionSnapshot
CanonicalPortfolioSnapshot
CanonicalCampaignSnapshot
CanonicalMandateSnapshot
CanonicalRiskConfiguration

CanonicalMarketContext
CanonicalFeatureSnapshot
CanonicalStrategyEvaluation
CanonicalEconomicEvaluation
CanonicalDecisionIntent
CanonicalRiskDecision
CanonicalGovernanceDecision
CanonicalAuthorization

CanonicalExecutionPackage
CanonicalProviderOrder
CanonicalProviderResult
CanonicalFill
CanonicalReconciliationResult
CanonicalAccountingResult

CanonicalDecisionRecord
CanonicalDecisionSnapshot
CanonicalCounterfactualEvaluation
CanonicalDecisionQualityResult
CanonicalKnowledgeRecord
CanonicalFeedbackCandidate
```

CanonicalAuthorization represents immutable operator,
governance, or automated authority decisions.

It preserves the exact authorization evidence used for later audit,
replay, and accountability.

## 4.3 Execution Context

Business data alone is insufficient to execute a pipeline stage.

Every stage should also receive a standardized Execution Context that describes the operating environment without modifying the business payload.

Typical fields include:

```text
mode
run_id
pipeline_version
schema_versions
configuration_versions
policy_versions
clock
operator_identity
campaign_identity
portfolio_identity
correlation_id
causation_id
```

Typical operating modes include:

```text
LIVE
CONTROLLED_PROOF
HISTORICAL_REPLAY
SIMULATION
UNIT_TEST
```

The Execution Context is separate from business data.

Its purpose is to describe *how* the pipeline is running rather than *what* information is being processed.

Business stages should consume the Execution Context instead of directly inspecting system clocks, environment variables, or provider state.

## 4.4 Pipeline Context vs Business Data

OmniTrade distinguishes between business information and pipeline context.

Business information represents the subject of the decision.

Examples include:

```text
CanonicalCandle
CanonicalTradePrint
CanonicalPortfolioSnapshot
CanonicalBalanceSnapshot
CanonicalPositionSnapshot
CanonicalFeatureSnapshot
```

Pipeline Context describes the environment in which processing occurs.

Examples include:

```text
ExecutionContext
run_id
clock
pipeline_version
configuration_versions
schema_versions
operator_identity
replay_mode
trace identifiers
```

These concepts should remain independent.

Business data should never contain pipeline-control metadata, and pipeline metadata should never become part of market or portfolio semantics.

Keeping these responsibilities separate reduces coupling, simplifies replay, and preserves deterministic behavior.

## 4.5 Example Canonical Bitcoin Candle

```json
{
  "event_id": "uuid",
  "event_type": "market.candle",
  "schema_version": "canonical-candle/v1",
  "source": "kraken_spot",
  "provider": "kraken",
  "venue": "kraken_spot",
  "asset_class": "crypto",
  "instrument": "BTC-USD",
  "occurred_at": "2026-07-31T15:15:00Z",
  "available_at": "2026-07-31T15:15:02Z",
  "received_at": "2026-07-31T15:15:02.331Z",
  "normalized_at": "2026-07-31T15:15:02.339Z",
  "correlation_id": "uuid",
  "causation_id": "uuid",
  "run_id": "live-uuid",
  "raw_record_id": "uuid",
  "integrity_hash": "sha256:...",
  "quality_status": "accepted",
  "payload": {
    "timeframe": "15m",
    "open": "118250.40",
    "high": "118410.70",
    "low": "118100.00",
    "close": "118380.20",
    "volume_base": "14.932",
    "volume_quote": "1767481.55",
    "trade_count": null
  }
}
```

## 4.6 Example Canonical Stock Candle

```json
{
  "event_id": "uuid",
  "event_type": "market.candle",
  "schema_version": "canonical-candle/v1",
  "source": "alpaca_market_data",
  "provider": "alpaca",
  "venue": "nasdaq",
  "asset_class": "equity",
  "instrument": "AAPL",
  "occurred_at": "2026-07-31T15:15:00Z",
  "available_at": "2026-07-31T15:15:01Z",
  "received_at": "2026-07-31T15:15:01.184Z",
  "normalized_at": "2026-07-31T15:15:01.191Z",
  "correlation_id": "uuid",
  "causation_id": "uuid",
  "run_id": "live-uuid",
  "raw_record_id": "uuid",
  "integrity_hash": "sha256:...",
  "quality_status": "accepted",
  "payload": {
    "timeframe": "15m",
    "open": "212.15",
    "high": "212.50",
    "low": "211.92",
    "close": "212.30",
    "volume_base": "325441",
    "volume_quote": null,
    "trade_count": null
  }
}
```

The payload remains domain-appropriate, while the contract envelope remains consistent.

---

# 5. Raw Input and Evidence Preservation

Raw external payloads are immutable evidence.

The Input Layer must preserve:

- the exact provider payload,
- provider headers where operationally relevant,
- provider identity,
- venue identity,
- request or subscription context,
- receipt timestamp,
- transport metadata,
- and an integrity hash.

Normalization must never overwrite raw evidence.

Every normalized object must retain a pointer to the exact raw record that produced it.

This enables the system to distinguish defects caused by:

- the provider,
- the transport,
- the adapter,
- the normalizer,
- validation,
- or later processing.

---

# 6. Input Layer

The Input Layer is responsible for receiving external and internal events.

Examples include:

- candles,
- quotes,
- trades,
- order books,
- funding rates,
- balances,
- positions,
- provider order updates,
- news,
- economic events,
- operator commands,
- strategy configurations,
- campaign definitions,
- mandate definitions,
- risk policies,
- and historical replay events.

The Input Layer must not perform downstream business reasoning.

Its job ends when raw evidence has been captured and handed to the appropriate normalizer.

---

# 7. Normalization Layer

The Normalization Layer converts provider-specific schemas into OmniTrade canonical contracts.

Responsibilities include:

- symbol mapping,
- venue mapping,
- asset-class mapping,
- base and quote orientation,
- timestamp conversion,
- decimal conversion,
- interval normalization,
- quantity normalization,
- precision preservation,
- enum normalization,
- missing-field handling,
- provider metadata preservation,
- schema-version assignment,
- and raw-record lineage.

Provider-specific terms must disappear after this boundary.

Examples:

```text
XBT/USD
BTCUSD
BTC-USD
BTC/USD
```

may normalize to:

```text
BTC-USD
```

The normalizer must not silently infer material facts that were not present in the source.

Any inference must be explicit, versioned, and recorded.

---

# 8. Validation and Quarantine

Normalization and validation are separate stages.

A record may be structurally normalized yet still be unsafe for use.

Validation outcomes are:

```text
ACCEPTED
QUARANTINED
REJECTED
```

## 8.1 Accepted

The record satisfies all required structural, temporal, integrity, and quality constraints and may enter the pipeline.

## 8.2 Quarantined

The record is preserved but withheld from downstream decision-making pending review or later resolution.

Examples:

- suspicious timestamp,
- partial order-book snapshot,
- stale data,
- sequence gap,
- provider discrepancy,
- unresolved symbol mapping.

## 8.3 Rejected

The record is unusable for downstream processing.

Examples:

- invalid schema,
- impossible OHLC values,
- negative volume where prohibited,
- invalid asset identity,
- corrupt payload,
- duplicate with conflicting content.

## 8.4 Validation Rules

Validation should check, where applicable:

- schema completeness,
- timestamp ordering,
- `available_at` integrity,
- duplicate identity,
- high/low/open/close consistency,
- quantity and price precision,
- sequence continuity,
- data freshness,
- provider status,
- market-hours context,
- cross-source discrepancy,
- and minimum evidence quality.

Only accepted canonical data may proceed into decision-making.

---

# 9. Pipeline Stage Contracts

Every stage must define:

- stage name,
- stage version,
- input contract type,
- output contract type,
- deterministic behavior,
- validation requirements,
- fail-closed behavior,
- audit requirements,
- replay requirements,
- performance expectations,
- and downstream authority.

## 9.1 Market Intelligence

Input:

```text
Canonical market events
```

Output:

```text
CanonicalMarketContext
```

Responsibilities may include:

- market-state assembly,
- trend state,
- volatility regime,
- liquidity state,
- cross-asset context,
- venue health,
- data-quality context,
- and regime classification.

## 9.2 Feature Engineering

Input:

```text
CanonicalMarketContext
```

Output:

```text
CanonicalFeatureSnapshot
```

The feature snapshot must capture exact values, not live references.

## 9.3 Strategy

Input:

```text
CanonicalFeatureSnapshot
CanonicalPortfolioSnapshot
Strategy configuration
```

Output:

```text
CanonicalStrategyEvaluation
```

Strategies must remain pure and provider-neutral.

## 9.4 Economics

Input:

```text
CanonicalStrategyEvaluation
fees
slippage assumptions
spread
position context
```

Output:

```text
CanonicalEconomicEvaluation
```

The economics stage determines whether expected gross edge remains positive after realistic costs.

## 9.5 Decision

Input:

```text
Strategy and economic evidence
market context
portfolio context
```

Output:

```text
CanonicalDecisionIntent
```

The decision stage synthesizes evidence but does not bypass Risk or governance.

## 9.6 Risk

Input:

```text
CanonicalDecisionIntent
portfolio state
risk policy
position state
```

Output:

```text
CanonicalRiskDecision
```

The Risk Engine remains the mandatory final safety gate.

## 9.7 Portfolio Opportunity Arbitration

Individual strategy evaluations compete for finite capital.

Portfolio Opportunity Arbitration selects the best combination of
approved opportunities while respecting:

- portfolio constraints
- campaign priorities
- capital allocation policy
- opportunity cost
- diversification objectives
- liquidity
- exposure limits

Input:

```text
Approved strategy opportunities
Portfolio state
Capital constraints
```

Output:

```text
Portfolio Allocation Plan
```

## 9.8 Governance

Input:

```text
CanonicalRiskDecision
campaign authority
mandate authority
operator approvals
```

Output:

```text
CanonicalGovernanceDecision
GovernedExecutionIntent
```

## 9.9 Execution

Input:

```text
GovernedExecutionIntent
```

Output:

```text
CanonicalExecutionPackage
```

## 9.10 Provider

Input:

```text
CanonicalExecutionPackage
```

Output:

```text
CanonicalProviderOrder
CanonicalProviderResult
```

Provider-specific translation occurs only inside the provider adapter.

## 9.11 Open Order Management

Input:

```text
CanonicalProviderOrder
CanonicalProviderResult
```

Output:

```text
CanonicalOrderLifecycle
```

Responsibilities:

- monitor submitted orders
- partial fills
- cancellations
- amendments
- expiration
- timeout recovery
- uncertain submission recovery

This stage owns the live order lifecycle until Reconciliation determines canonical truth.

It is responsible for managing order state transitions but must never modify the original execution intent.

### Provider Idempotency

Provider interactions must be idempotent whenever supported by the external venue.

The Provider stage must tolerate:

- duplicate acknowledgements
- duplicate callbacks
- retry operations
- network interruptions
- uncertain submission outcomes

without creating duplicate executions.

When provider-supported idempotency keys exist, they should be used.

When they do not exist, OmniTrade must implement equivalent protection through canonical execution identifiers and reconciliation.


## 9.12 Reconciliation

Input:

```text
Provider order
provider fills
balance evidence
execution package
```

Output:

```text
CanonicalReconciliationResult
```

## 9.13 Accounting

Input:

```text
CanonicalReconciliationResult
prior portfolio state
```

Output:

```text
CanonicalAccountingResult
CanonicalPortfolioSnapshot
```

## 9.14 Knowledge

Input:

```text
all stage evidence
decision outcome
counterfactual evidence
```

Output:

```text
CanonicalKnowledgeRecord
CanonicalFeedbackCandidate
```

The Knowledge stage records and synthesizes. It does not alter live logic automatically.

---

# 10. API and Deployment Philosophy

Every major stage must expose a stable callable contract.

That does not require every stage to become an HTTP microservice immediately.

## 10.1 API Categories

Pipeline APIs fall into three categories.

### Inspection APIs

Read-only.

Examples:

- inspect
- retrieve input
- retrieve output
- retrieve lineage
- retrieve explanation
- retrieve health
- retrieve readiness

### Simulation APIs

Non-production execution.

Examples:

- replay
- compare
- simulate
- historical reconstruction

### Governed Mutation APIs

These may affect production state.

Examples:

- authorize
- activate
- execute
- reconcile
- account

Governed Mutation APIs must never bypass Risk, Governance, operator authorization, or fail-closed safety behavior.

## 10.2 Initial Implementation

A stage may begin as:

```text
Python function or class method
```

## 10.3 Internal Service Boundary

A stage may later be exposed through an internal service interface.

## 10.4 HTTP or Message Boundary

A stage may eventually become:

- REST,
- gRPC,
- event-driven queue consumer,
- or separate service.

The contract must remain semantically stable.

## 10.5 API Requirements

Every pipeline stage should expose only the APIs appropriate for its category.

Inspection APIs may expose:

- retrieve input
- retrieve output
- retrieve lineage
- retrieve explanation
- retrieve health
- retrieve readiness
- retrieve metrics

Simulation APIs may expose:

- replay
- simulate
- compare
- reconstruct

Governed Mutation APIs may expose:

- authorize
- activate
- submit
- reconcile
- account

No API may bypass Risk, Governance, or authorization.

Execution-capable APIs must preserve all existing governance guarantees.

## 10.6 Versioning

Every stage contract must be versioned.

Breaking semantic changes require a new contract version.

Old records must remain interpretable under their original version.

---

# 11. Lineage and Correlation

Every stage output must identify the exact inputs that caused it.

Required lineage fields should include:

```text
object_id
parent_object_ids
correlation_id
causation_id
run_id
pipeline_version
stage_name
stage_version
schema_version
configuration_version
policy_version
strategy_version
model_version
created_at
completed_at
status
explanation
reason_code
integrity_hash
```

This must enable queries such as:

- Which exact candle produced this feature snapshot?
- Which exact feature snapshot produced this strategy evaluation?
- Which decision intent was resized by Risk?
- Which governance record authorized this execution package?
- Which provider fill produced this accounting result?
- Which result changed a later knowledge summary?

Lineage must be immutable.

---

# 12. Observability

In addition to business lineage and audit evidence, every pipeline stage should expose operational telemetry.

Typical metrics include:

- execution duration
- queue delay
- processing latency
- throughput
- success rate
- retry count
- failure rate
- memory usage
- CPU usage
- audit status
- replay status

Observability data is distinct from audit data.

Audit data explains **why** a decision occurred.

Observability explains **how well** the system executed.

Together they provide complete operational visibility into both correctness and performance.

# 13. Audit Modes and Pipeline Breakpoints

OmniTrade must support staged audit execution.

The operator should be able to stop the pipeline after any stage and inspect the result before enabling the next stage.

## 13.1 Audit Mode 1

```text
Input
Normalization
Validation
STOP
```

Verify:

- raw payload preservation,
- canonical mapping,
- decimal precision,
- timestamps,
- asset identity,
- duplicate behavior,
- and validation status.

## 13.2 Audit Mode 2

```text
Input
Normalization
Validation
Market Intelligence
STOP
```

## 13.3 Audit Mode 3

```text
...
Feature Engineering
STOP
```

## 13.4 Audit Mode 4

```text
...
Strategy
STOP
```

## 13.5 Audit Mode 5

```text
...
Economics
Decision
Risk
STOP
```

## 13.6 Audit Mode 6

```text
...
Governance
Execution Package Construction
STOP
```

No provider submission.

## 13.7 Audit Mode 7

```text
...
Dry-Run Provider Adapter
STOP
```

Construct the exact provider request without transmitting it.

## 13.8 Audit Mode 8

```text
Controlled Execution
```

## 13.9 Audit Mode 9

```text
Full Autonomous Pipeline
```

Each stage must expose:

- input,
- output,
- duration,
- pass/fail/block status,
- explanation,
- reason code,
- version information,
- and integrity hash.

---

# 14. Live Operating Mode

Live mode uses:

- real provider inputs,
- real clock,
- production portfolio state,
- governed live execution adapters,
- production reconciliation,
- and production accounting.

Live mode must still use the same canonical contracts and pipeline stages.

Live-specific concerns must remain confined to adapters and operating context.

---

# 15. Historical Replay Engine

Historical replay must use the same canonical pipeline as live operation.

Replay replaces only the outside world.

```text
LIVE MODE
provider input
real clock
production portfolio
live execution adapter

REPLAY MODE
historical archive
simulated clock
isolated portfolio
simulated execution adapter
```

Everything after canonical normalization should remain the same unless an explicitly versioned replay adapter is required.

## 15.1 Historical Replay Goal

The system should be able to run an asset from its earliest trustworthy historical record through the complete pipeline and create a large historical corpus of:

- Decision Records,
- Decision Snapshots,
- Strategy Evaluations,
- Economic Evaluations,
- Risk Decisions,
- Governance Decisions,
- simulated execution evidence,
- reconciliation evidence,
- accounting outcomes,
- counterfactual outcomes,
- Decision Quality Results,
- and Knowledge Records.

## 15.2 Replay Request Example

Conceptually:

```text
Replay BTC-USD
from earliest trustworthy data
through a specified end date
using pipeline version X
strategy set Y
risk policy Z
starting capital $25
execution fidelity level 2
```

---

# 16. Temporal Integrity and Look-Ahead Prevention

Historical replay must never access information that was unavailable at the simulated time.

Every historical object should carry:

```text
occurred_at
available_at
```

`occurred_at` means when the real-world event happened.

`available_at` means when OmniTrade could have known about it.

Replay access must be governed by `available_at`.

## 15.1 Prohibited Future Leakage

Replay must prevent:

- future candle leakage,
- use of a candle close before the candle closed,
- future news,
- revised economic data not yet published,
- future corporate actions,
- future index composition,
- knowledge of delisting outcomes,
- assets that did not yet exist,
- later provider corrections,
- and future portfolio state.

## 15.2 Replay Clock

Every stage must consume time from an injected execution context.

No business stage may read wall-clock time directly when operating in replay.

---

# 17. Two Replay Meanings

## 17.1 Historical-as-Originally-Understood Replay

Uses the pipeline, strategy, normalizer, model, configuration, and risk-policy versions that existed at the historical time.

Purpose:

Reconstruct what OmniTrade actually knew and would have done.

## 17.2 Modern-Policy Replay

Feeds historical events through the current pipeline and current policies.

Purpose:

Determine how the current system would have behaved throughout historical periods.

These replay types must never be mixed silently.

Every replay result must identify which meaning was used.

---

# 18. Replay Fidelity

Replay results must declare their execution-fidelity level.

## Level 1 — Candle-Only Approximation

Uses OHLCV candles and modeled fills.

Appropriate for broad strategy research.

Not execution-grade proof.

## Level 2 — Quote, Spread, Volume, and Fee Simulation

Uses:

- bid/ask,
- spread,
- volume,
- provider fees,
- and more realistic fill assumptions.

## Level 3 — Trade Prints and Order-Book Snapshots

Adds:

- trade-level data,
- partial depth,
- order-book imbalance,
- and more detailed fill simulation.

## Level 4 — Venue-Level Execution Reconstruction

Uses the highest available historical fidelity:

- venue rules,
- latency assumptions,
- partial fills,
- minimums,
- halts,
- order types,
- and historical fee schedules.

No replay result may imply a higher level of certainty than its fidelity supports.

---

# 19. Replay Parallelism and Scale

## 19.1 Sequential Portfolio Replay

A stateful portfolio replay must progress chronologically.

Each result changes the next portfolio state.

Therefore, one portfolio timeline cannot be arbitrarily parallelized.

## 19.2 Valid Parallelization

Replay may be distributed across:

- assets,
- strategies,
- parameter sets,
- policy versions,
- independent portfolios,
- independent experiments,
- and time partitions with sufficient warm-up and safe state boundaries.

## 19.3 Large-Scale Historical Decision Generation

The architecture should support rapidly generating large quantities of historical decision evidence.

However, the system must distinguish:

- complete immutable audit evidence,
- derived summaries,
- representative precedents,
- clustered market states,
- and compressed analytical indexes.

The goal is not merely to create billions of repetitive rows.

The goal is to preserve causal evidence while making experience searchable and useful.

---

# 20. Decision-Memory Generation

Historical replay is not only a P&L calculator.

Its primary long-term value is generation of historical decision memory.

Each replayed decision should preserve:

- exact market context,
- exact feature snapshot,
- supporting evidence,
- opposing evidence,
- strategy outputs,
- economics,
- Risk decision,
- governance decision,
- execution assumptions,
- simulated fills,
- accounting,
- outcome,
- counterfactual alternatives,
- Decision Quality,
- and lessons.

This historical corpus becomes a research input.

It must not automatically become live authority.

---

# 21. Results and Feedback

The pipeline produces outputs and results.

Examples:

- BUY,
- SELL,
- HOLD,
- WAIT,
- Risk rejection,
- governance block,
- provider fill,
- reconciliation completion,
- accounting completion,
- profit,
- loss,
- missed opportunity,
- confidence-calibration result,
- Decision Quality result,
- and operational reliability result.

Results become feedback candidates.

---


# 22 Position Lifecycle

Not every SELL originates from a new Strategy evaluation.

The Position Lifecycle stage continuously evaluates existing positions for:

- stop loss
- take profit
- trailing stop
- time-based exit
- campaign completion
- mandate expiration
- settlement
- rollover
- provider events
- corporate actions

Output:

```text
CanonicalPositionLifecycleDecision
```

Any action generated by Position Lifecycle must re-enter the governed pipeline through the same Risk and Governance process as new trade opportunities.

Position Lifecycle never bypasses the pipeline.



# 23. Governed Learning

Results must not silently modify live behavior.

The learning path should be:

```text
Result
    ↓
Knowledge Candidate
    ↓
Offline Analysis
    ↓
Historical Replay
    ↓
Stress Testing
    ↓
Human or Governance Review
    ↓
Versioned Promotion
    ↓
Future Live Input
```

This preserves:

- explainability,
- reproducibility,
- controlled promotion,
- Risk authority,
- and institutional memory.

---

# 24. Historical Analogue Retrieval

Over time, OmniTrade should retrieve historically relevant market states rather than treating all history equally.

A mature system may compare current conditions against prior states using:

- volatility similarity,
- trend similarity,
- liquidity similarity,
- spread similarity,
- cross-asset similarity,
- macro similarity,
- portfolio similarity,
- strategy-health similarity,
- and structural-market similarity.

Historical usefulness should conceptually reflect:

```text
regime similarity
× structural relevance
× data quality
× recency relevance
× model compatibility
```

The system must be able to conclude that no reliable historical analogue exists.

Low similarity must reduce confidence rather than force a match.

---

# 25. Security and Isolation

Replay and audit modes must never affect production state.

Historical replay must never write to:

- production portfolios,
- live balances,
- live positions,
- live mandates,
- live campaigns,
- production execution queues,
- production reconciliation queues,
- or production accounting state.

Required safeguards include:

- isolated persistence,
- isolated portfolio identities,
- simulated provider adapters,
- explicit operating-mode context,
- fail-closed isolation guards,
- and no fallback from replay storage to production storage.

Any ambiguous environment binding must stop execution.

---

# 26. Migration from the Current Architecture

Implementation should proceed incrementally.

The current monolithic system should not be replaced all at once.

Recommended migration sequence:

1. Inventory the existing live pipeline.
2. Map each existing module to a proposed stage.
3. Identify existing implicit contracts.
4. Define canonical contract schemas without changing behavior.
5. Add lineage and version metadata.
6. Add adapters around current functions.
7. Introduce audit-only stage execution.
8. Introduce pipeline breakpoints.
9. Route historical replay through the same contracts.
10. Add isolated replay persistence.
11. Add stage APIs where operationally valuable.
12. Extract distributed services only when justified by scale or reliability.

The first implementation objective is contract clarity, not microservice proliferation.

---

# 27. Acceptance Criteria

The architecture is considered implemented only when all of the following are true.

## Canonical Data

- Raw provider evidence is preserved.
- Provider-specific schemas terminate at normalization.
- Canonical contracts are versioned.
- Canonical objects preserve lineage to raw evidence.

## Stage Contracts

- Every major stage has a documented input and output contract.
- Every stage records its implementation and configuration versions.
- Every stage can be executed independently in test or audit mode.
- Every stage has explicit fail-closed behavior.

## Auditability

- The exact input and output of every stage can be retrieved.
- Every output identifies its causal inputs.
- A pipeline run can be inspected stage by stage.
- A failure can be localized to the first divergent stage.

## Replay

- Historical replay uses the same normalized contracts and stage transformations as live operation.
- Replay uses an injected clock.
- Replay cannot access future information.
- Replay cannot touch production state.
- Replay results identify their fidelity level.
- Replay can produce complete Decision Records and Decision Snapshots.

## Governance

- Risk remains mandatory.
- Governance remains mandatory.
- Replay discoveries cannot alter live behavior automatically.
- Any promoted change is versioned, reviewed, and replay-tested.

## Deployment

- Stage contracts are independent of whether the stage is in-process or remote.
- A stage can move behind an API without changing the semantic meaning of its contract.

---

# 28. Mapping to Constitutional Engines

This document defines OmniTrade's information-flow architecture.

It does **not** replace the four permanent Constitutional Engines.

Instead, the pipeline describes how information moves through them.

Approximate mapping:

Decision Intelligence Engine

- Market Intelligence
- Feature Engineering
- Strategy
- Economics

Risk Engine

- Decision
- Risk
- Governance

Execution Architecture

- Execution
- Provider
- Open Order Management
- Reconciliation
- Accounting

Historical Intelligence Platform

- Knowledge
- Historical Replay
- Decision Memory
- Feedback
- Institutional Memory

The pipeline is an orthogonal information-flow model rather than a replacement for the permanent architectural responsibilities defined elsewhere.

# 29. Anti-Goals

The following are explicitly prohibited.

- No provider-specific schema beyond the Normalization Layer.
- No giant universal canonical object for unrelated domains.
- No duplicate live and replay business logic.
- No separate backtest-only decision pipeline.
- No silent contract changes.
- No unversioned canonical objects.
- No loss of raw provider evidence.
- No replay access to future information.
- No replay writes to production state.
- No replay bypass of Risk or governance.
- No automatic promotion of replay findings into live trading.
- No direct system-clock access inside replayable business stages.
- No stage that hides multiple unrelated responsibilities.
- No microservice extraction merely for architectural appearance.
- No assumption that all historical periods are structurally comparable.
- No treating candle-only replay as execution-grade proof.
- No mutation of immutable lineage or audit evidence.

---

# 30. Long-Term Vision

Forty years of operation should not leave OmniTrade with forty years of disconnected logs.

It should leave OmniTrade with forty years of:

- normalized market states,
- immutable Decision Snapshots,
- explainable decisions,
- rejected opportunities,
- risk outcomes,
- execution evidence,
- reconciliation evidence,
- accounting outcomes,
- counterfactual outcomes,
- Decision Quality,
- operational lessons,
- and governed strategy evolution.

The greatest long-term asset may not be the code.

It may be the system's institutional memory:

```text
This exact input
produced this exact interpretation
which produced this exact decision
which produced this exact action
which produced this exact verified result
under these exact versions and policies.
```

The purpose of this architecture is to ensure that every future market, provider, asset class, strategy, and deployment model can participate in the same explainable, auditable, replayable, governed information flow.

---

# 31. Permanent Architectural Principle

> Every stage of the OmniTrade pipeline owns one responsibility, consumes one versioned contract, produces one versioned contract, preserves immutable lineage, and can be audited, replayed, tested, or deployed independently without changing the meaning of the information it exchanges.

---

# 32. Claude Peer-Review Questions

Claude should review this architecture and answer:

1. Does this conflict with any existing governing document or implemented architecture?
2. Which concepts already exist in the repository under different names?
3. Which proposed canonical contracts map cleanly onto existing models and schemas?
4. Where does the current live pipeline duplicate or bypass the proposed stage boundaries?
5. Does historical replay currently reuse the live decision pipeline, partially reuse it, or duplicate it?
6. What is the smallest production-safe migration path from the current monolith?
7. Which stages should remain in-process initially?
8. Which stages would benefit from explicit internal or HTTP APIs first?
9. What lineage fields already exist?
10. What migrations would eventually be required?
11. What replay-isolation safeguards already exist?
12. What additional safeguards are required to prevent look-ahead bias?
13. What performance bottlenecks would large-scale replay create?
14. How should sequential portfolio replay and parallel research replay be separated?
15. What portions of this architecture should become ADRs?
16. What is overly broad, redundant, or inconsistent?
17. What should be changed before implementation prompts are written?
18. What phased implementation plan best preserves current production behavior?

Claude should perform read-only reconnaissance first and should not implement anything during the peer-review task.
