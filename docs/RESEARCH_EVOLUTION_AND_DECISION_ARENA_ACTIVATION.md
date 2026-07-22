# RESEARCH_EVOLUTION_AND_DECISION_ARENA_ACTIVATION.md

# Research Evolution & Decision Arena Activation

Version: 1.0

Status: Activation Specification

Applies To:
- Research Agent Framework
- Research Laboratory
- Research Evolution Operations
- Strategy Engine
- Decision Arena
- Replay System
- Decision Intelligence
- Capital Allocation Engine
- Risk Engine

---

# Purpose

This document defines how OmniTrade's existing research architecture becomes a continuously operating strategy discovery and evaluation platform.

It does **not** replace the existing architecture documents.

Instead, it reconciles them into a single activation roadmap and identifies the remaining work required to transform the existing framework into a fully operational autonomous research organization.

This document should be read together with:

- AI_RESEARCH_AND_EVOLUTION_ENGINE.md
- RESEARCH_AGENT_FRAMEWORK.md
- RESEARCH_LABORATORY.md
- RESEARCH_EVOLUTION_OPERATIONS.md
- STRATEGY_ENGINE.md
- DECISION_INTELLIGENCE_ENGINE.md
- RISK_ENGINE.md

---

# Primary Objective

The immediate objective of this activation effort is **not** to build an academic research platform.

The objective is:

> Discover profitable investment strategies faster than manual development while maintaining strict governance, auditability, and capital safety.

The activation of the Research Evolution System must accelerate OmniTrade's path toward:

**First Autonomous Profit**

and eventually continuous autonomous capital growth.

Research exists to improve profitability—not delay it.

---

# Vision

OmniTrade is evolving from a single autonomous trading system into an autonomous capital research organization.

The production trading system should never stop learning.

Even while production strategies are generating returns, the Research Laboratory continues operating independently to discover better strategies, better parameters, and better portfolio allocations.

The laboratory continuously attempts to replace the current production champion with something demonstrably better.

Research never stops.

---

# Existing Architecture

The following major components already exist within the architecture.

## Research Agent Framework

Responsible for:

- candidate generation
- research agent registration
- deterministic proposal generation

This framework is responsible for creating ideas.

It is not responsible for proving them.

---

## Research Laboratory

Responsible for:

- coordinating research agents
- collecting candidate batches
- submitting candidates for evaluation

The laboratory orchestrates research.

It does not evaluate capital performance.

---

## Research Evolution Operations

Responsible for:

- deterministic research cycles
- candidate persistence
- tournament generation
- descendant creation
- research memory

This is the operational heartbeat of the research system.

---

## Strategy Engine

Responsible for executable trading strategies.

Strategies remain:

- deterministic
- pure functions
- independently testable
- explainable

Research produces candidate strategies.

The Strategy Engine executes approved strategies.

---

## Replay

Replay provides deterministic validation.

Every candidate strategy must successfully replay historical Decision Packages before advancing.

Replay remains read-only.

---

## Decision Intelligence

Decision Intelligence evaluates:

- correctness
- reproducibility
- evidence
- explainability

Decision Intelligence measures quality.

It does not decide promotion.

---

## Risk Engine

The Risk Engine remains the final authority.

No research component may bypass Risk.

Ever.

---

## Capital Allocation Engine

Capital Allocation recommends where capital should be deployed based on accumulated evidence.

It does not independently authorize production promotion.

---

# Current Gap

Although the architecture contains these components, they currently operate as largely independent subsystems.

The remaining work is not the invention of new concepts.

The remaining work is activating the complete research lifecycle connecting them together.

---

# Target Lifecycle

The fully activated lifecycle becomes:

Market Data

↓

Research Agents

↓

Candidate Strategies

↓

Validation

↓

Replay

↓

Historical Backtesting

↓

Paper Evaluation

↓

Decision Arena

↓

Tournament Ranking

↓

Research Memory

↓

Promotion Recommendation

↓

Risk Review

↓

Human Approval

↓

Production

↓

Continuous Monitoring

↓

Retirement

↓

Research Continues

---

# Decision Arena

The Decision Arena becomes the permanent proving ground for strategy competition.

Every qualifying strategy competes under identical conditions.

Every strategy receives:

- identical market data
- identical timestamps
- identical fees
- identical slippage assumptions
- identical risk constraints

The objective is evidence-based comparison.

Not opinion.

---

# Research Never Stops

Research is permanently asynchronous from production.

Production continues executing capital.

The laboratory continues searching.

Every production strategy should assume it will eventually be replaced.

Continuous improvement is the intended behavior.

---

# Candidate Lifecycle

Every strategy candidate progresses through explicit states.

PROPOSED

↓

VALIDATED

↓

REPLAY_QUALIFIED

↓

BACKTEST_QUALIFIED

↓

ARENA_ACTIVE

↓

PROMOTION_CANDIDATE

↓

PRODUCTION_APPROVED

↓

ACTIVE

↓

UNDER_REVIEW

↓

RETIRED

State transitions are immutable and auditable.

---

# Tournament Philosophy

The purpose of the tournament is not to produce a winner.

Its purpose is to accumulate evidence.

If insufficient evidence exists:

No champion is declared.

No promotion occurs.

---

# Promotion Philosophy

No strategy earns production simply because it generated higher historical profit.

Promotion considers multiple dimensions.

Examples include:

- profitability
- drawdown
- stability
- consistency
- replay quality
- explainability
- decision quality
- calibration
- capital efficiency
- robustness across market regimes

Production promotion remains conservative.

---

# Research Memory

Research Memory becomes one of OmniTrade's most valuable assets.

It permanently preserves:

- hypotheses
- candidate strategies
- descendants
- tournament history
- failures
- critiques
- rejected promotions
- successful promotions
- production outcomes

Knowledge accumulates forever.

Nothing is discarded.

---

# Future Agent Roles

Research will eventually expand beyond simple Research Agents.

Expected future roles include:

Research Agents

Generate candidate ideas.

Critic Agents

Attempt to disprove research conclusions.

Mutation Agents

Create bounded descendants.

Historian Agents

Study long-term tournament history.

Regime Agents

Classify market environments.

Portfolio Agents

Recommend capital allocation across strategies.

Retirement Agents

Identify aging strategies.

Audit Agents

Verify governance compliance.

Every role remains governed by the Risk Engine and immutable audit records.

---

# Governance Principles

Research may never:

- execute live trades
- bypass Risk Engine
- bypass human approval
- overwrite historical evidence
- silently modify production strategies
- alter production capital

Research recommends.

Governance decides.

---

# Activation Strategy

Activation should proceed incrementally.

The objective is to activate existing architecture rather than redesign it.

Each implementation phase should:

- reuse existing components
- preserve deterministic behavior
- avoid duplicate implementations
- preserve replay reproducibility
- maintain backward compatibility

---

# Definition of Completion

The Research Evolution System is considered operational when:

✓ Research Agents continuously generate candidates.

✓ Candidates automatically enter deterministic validation.

✓ Qualified candidates enter Replay.

✓ Qualified Replay candidates enter historical evaluation.

✓ Qualified strategies compete inside the Decision Arena.

✓ Tournament rankings accumulate over time.

✓ Research Memory permanently records outcomes.

✓ Promotion recommendations are evidence-driven.

✓ Human approval governs production promotion.

✓ Capital Allocation consumes tournament evidence.

✓ Production strategies continue operating while research continues independently.

---

# Long-Term Goal

OmniTrade's ultimate product is not individual trades.

Its product is continuous discovery of better capital allocation decisions.

Trading is the mechanism used to validate research.

Research is the mechanism used to improve trading.

That improvement should never stop.