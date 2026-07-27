# CROSS_ASSET_OPPORTUNITY_SELECTION.md

Version: 1.0
Status: Constitutional Vision
Priority: Core Financial Intelligence
Scope: Capital Allocation Decision Making

---

# Purpose

The Cross-Asset Opportunity Selection Engine determines which eligible opportunity should receive available capital when multiple assets simultaneously satisfy the requirements for autonomous trading.

Trading strategies answer:

> "Should this asset be considered?"

This engine answers:

> "Among every eligible opportunity, where should the next dollar of capital be deployed?"

This document establishes the decision-making framework for autonomous capital allocation across all supported assets.

---

# Guiding Principle

Capital is limited.

Opportunities are not.

The purpose of OmniTrade is not to execute every acceptable trade.

Its purpose is to allocate capital to the highest-quality opportunity available.

Every allocation represents an opportunity cost.

Selecting one opportunity means intentionally declining every alternative.

---

# Relationship to Other Documents

ASSET_REGISTRY.md

Defines canonical financial assets.

VENUE_INSTRUMENT_REGISTRY.md

Defines provider-specific trading instruments.

ASSET_UNIVERSE.md

Defines which assets are eligible for evaluation.

This document defines how OmniTrade selects one opportunity from among all eligible candidates.

Future documents such as DYNAMIC_CAPITAL_REALLOCATION.md extend these principles to capital already deployed.

---

# Fundamental Question

The Cross-Asset Opportunity Selection Engine continuously answers:

> If new capital becomes available right now, where should it be invested?

This question remains independent of:

- execution venue
- brokerage
- provider
- account implementation

The engine reasons about opportunities rather than APIs.

---

# Candidate Generation

Every eligible asset independently progresses through:

Market Data

↓

Strategy Evaluation

↓

Economics

↓

Risk

↓

Operational Readiness

↓

Eligible Candidate

Only fully eligible candidates enter Cross-Asset Opportunity Selection.

---

# Opportunity Independence

Each asset must be evaluated independently.

A Bitcoin opportunity must never influence the mathematical evaluation of an Ethereum opportunity.

Likewise:

- Equities
- Commodities
- Foreign Exchange
- Prediction Markets

should remain independently evaluated prior to selection.

---

# Selection Philosophy

The engine does not attempt to predict which asset will become the most valuable.

Instead, it determines which opportunity currently provides the greatest expected value after considering:

- expected return
- execution costs
- risk
- uncertainty
- operational readiness
- capital constraints

Selection is based upon expected quality rather than excitement.

---

# Opportunity Scoring

Every eligible opportunity should receive a deterministic opportunity score.

Future scoring models may consider factors including:

- expected net edge
- expected net profit
- confidence
- probability of success
- downside risk
- volatility
- liquidity
- spread
- estimated slippage
- execution quality
- historical strategy performance
- portfolio impact
- opportunity duration
- capital efficiency

The precise mathematical model may evolve over time.

The governing philosophy remains constant.

---

# Opportunity Ranking

Eligible opportunities should be ranked according to their overall expected value.

Example:

Ethereum

Score:

0.92

Bitcoin

Score:

0.84

Solana

Score:

0.77

The highest-ranked admissible opportunity becomes the allocation candidate.

---

# Selection Constraints

Ranking alone does not authorize capital deployment.

Additional constraints may include:

- available capital
- campaign limits
- portfolio exposure
- position limits
- provider readiness
- execution availability
- regulatory restrictions
- active mandates

Only opportunities satisfying all operational constraints may be selected.

---

# Opportunity Cost

Every allocation carries opportunity cost.

Selecting one opportunity intentionally declines every competing opportunity.

The engine should explicitly recognize this principle.

Future decision records should preserve:

Selected Opportunity

Declined Opportunities

Reasons for selection

Reasons alternatives were not selected

---

# Explainability

Every selection should be explainable.

Example:

Selected:

Ethereum

Reasons:

Highest expected net edge

Lowest execution cost

Risk within campaign limits

Highest opportunity score

Not Selected:

Bitcoin

Lower expected edge

Not Selected:

Solana

Economics rejected

Autonomous systems should always be capable of explaining why capital was allocated.

---

# Determinism

Given identical inputs, the engine should always produce the same ranking and selection.

Replay should reproduce identical results.

Deterministic behavior is essential for:

- auditing
- debugging
- simulation
- AI review
- historical analysis

---

# Failure Behavior

If no opportunity satisfies required standards:

No allocation occurs.

The engine should never force a trade to maintain activity.

The absence of an acceptable opportunity is itself a valid decision.

---

# Provider Neutrality

Opportunity Selection operates using canonical assets.

Venue selection occurs later.

Execution providers remain outside the scope of this engine.

This separation allows financial reasoning to remain independent of brokerage implementation.

---

# Scalability

The engine should support evaluation across:

- dozens of assets
- hundreds of assets
- thousands of assets

without changing the underlying decision philosophy.

Increasing the number of monitored assets should increase opportunity.

It must not weaken selection standards.

---

# Risk Relationship

Risk determines whether an opportunity is acceptable.

Opportunity Selection determines whether an acceptable opportunity is preferable to alternatives.

Risk answers:

"May we trade?"

Opportunity Selection answers:

"Which acceptable trade deserves the capital?"

---

# Economics Relationship

Economics determines whether expected value is positive.

Opportunity Selection compares positive opportunities.

Economics filters.

Opportunity Selection ranks.

---

# Portfolio Relationship

Portfolio Management determines:

How much capital may be deployed.

Opportunity Selection determines:

Where that capital should be deployed.

These responsibilities remain intentionally separate.

---

# Future Extensions

Future versions may incorporate:

- adaptive opportunity scoring
- market regime awareness
- portfolio correlation
- capital efficiency modeling
- execution quality prediction
- venue intelligence
- AI-assisted comparative analysis
- historical opportunity learning

These enhancements should refine opportunity evaluation without changing the governing philosophy.

---

# Design Principles

The Cross-Asset Opportunity Selection Engine should remain:

- Deterministic
- Provider Neutral
- Explainable
- Auditable
- Replayable
- Extensible
- Risk Aware
- Capital Efficient
- Constitutionally Consistent

---

# Constitutional Principles

Capital should always flow toward the highest-quality admissible opportunity.

Monitoring more opportunities must never weaken admission standards.

Every allocation should be explainable.

Every declined opportunity should be explainable.

No opportunity should be selected merely because no better alternative exists.

The absence of an acceptable opportunity is a successful decision.

---

# Long-Term Vision

The Cross-Asset Opportunity Selection Engine represents the transition from autonomous trading toward autonomous capital management.

Rather than asking:

"What should we buy?"

OmniTrade continually asks:

"Among every admissible opportunity currently available, where can this capital accomplish the greatest expected good?"

As the platform expands across asset classes, execution venues, and financial ecosystems, this engine becomes the central allocator of capital, ensuring every deployment is intentional, explainable, and aligned with the platform's governing principles.