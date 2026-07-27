# OPPORTUNITY_COST_MODEL.md

Version: 1.0
Status: Constitutional Vision
Priority: Core Financial Intelligence
Scope: Opportunity Cost Evaluation

---

# Purpose

The Opportunity Cost Model defines how OmniTrade measures the value of capital that remains committed to one opportunity instead of another.

Every investment decision carries two outcomes:

1. The value gained by making the investment.
2. The value forgone by not selecting an alternative.

OmniTrade should understand both.

The purpose of this model is to ensure that capital is evaluated not only by what it earns, but also by what it prevents.

---

# Guiding Principle

Every allocation has a cost.

The greatest cost is frequently invisible.

It is the opportunity that was never pursued.

---

# Relationship to Other Documents

ASSET_REGISTRY.md

Defines canonical financial assets.

CROSS_ASSET_OPPORTUNITY_SELECTION.md

Determines which opportunity should receive newly available capital.

DYNAMIC_CAPITAL_REALLOCATION.md

Determines whether deployed capital should remain invested.

This document defines the mathematical and philosophical framework used to compare competing opportunities.

---

# Fundamental Question

The Opportunity Cost Model continuously asks:

> What is sacrificed by choosing this allocation instead of another?

Investment decisions should always be comparative.

Capital can only exist in one place at a time.

---

# Opportunity Cost

Opportunity cost is the expected value forgone by selecting one opportunity instead of another.

It is not:

- a commission
- a trading fee
- a realized loss
- a bookkeeping entry

It is an economic comparison.

---

# Capital Scarcity

Capital is finite.

Time is finite.

Buying power is finite.

Because resources are limited, every deployment excludes competing possibilities.

The Opportunity Cost Model exists because OmniTrade cannot invest everywhere simultaneously.

---

# Opportunity Comparison

Every opportunity should be evaluated against realistic alternatives.

Examples include:

Remain in current position

versus

Move to another asset

Remain in cash

versus

Open a new position

Allocate to Bitcoin

versus

Allocate to Ethereum

Allocate to cryptocurrency

versus

Allocate to equities

Every allocation is implicitly a comparison.

---

# Opportunity Cost Is Dynamic

Opportunity cost changes continuously.

An opportunity that was superior one hour ago may no longer be superior.

Likewise, a position that justified capital yesterday may not justify it today.

Opportunity cost should therefore be reevaluated continuously.

---

# Opportunity Is Not Profit

Expected profit alone is insufficient.

Example

Opportunity A

Expected Profit

$0.30

Opportunity B

Expected Profit

$0.28

Opportunity A is not automatically superior.

Additional considerations may include:

- risk
- duration
- uncertainty
- liquidity
- execution quality
- switching cost
- portfolio impact

Opportunity cost compares expected value rather than raw profit.

---

# Remaining Value

Existing positions possess remaining expected value.

OmniTrade should compare:

Remaining Expected Value

versus

Expected Value of Alternative Opportunity

This comparison forms the foundation of Dynamic Capital Reallocation.

---

# Cash Is Also An Allocation

Remaining in cash is an intentional investment decision.

Cash possesses:

- liquidity
- optionality
- purchasing power
- flexibility

If no superior opportunity exists, remaining in cash may produce the highest expected value.

The absence of investment is sometimes the optimal allocation.

---

# Time As A Cost

Capital is not the only scarce resource.

Time also possesses value.

Examples:

Expected Profit

$0.20

Expected Duration

20 minutes

versus

Expected Profit

$0.22

Expected Duration

8 hours

Future versions may evaluate expected return relative to expected capital commitment over time.

Opportunity cost should consider both value and duration.

---

# Portfolio Perspective

Opportunity cost should not be evaluated solely at the position level.

The portfolio itself possesses opportunity cost.

Examples include:

- excessive concentration
- poor diversification
- unused buying power
- redundant exposure
- unnecessary correlation

Portfolio optimization is itself an opportunity-cost problem.

---

# Switching Costs

Opportunity cost should not ignore transition costs.

Changing positions may require:

- exit commissions
- entry commissions
- spreads
- slippage
- execution latency
- taxes (where applicable)
- temporary market exposure

These costs reduce the value of reallocation.

---

# Comparative Decision Making

Every meaningful capital decision compares:

Current Allocation

↓

Alternative Allocation

↓

Expected Benefit

↓

Expected Cost

↓

Expected Risk

↓

Net Expected Improvement

Only meaningful improvement should justify changing allocations.

---

# Explainability

Every opportunity comparison should be explainable.

Example

Current Position

Bitcoin

Remaining Expected Value

Higher

Alternative

Ethereum

Expected Improvement

Insufficient after switching costs

Decision

Remain Invested

Opportunity cost should never be a hidden calculation.

---

# Determinism

Given identical inputs, the Opportunity Cost Model should produce identical conclusions.

Deterministic evaluation is essential for:

- replay
- auditing
- debugging
- simulation
- AI review

---

# Relationship to Economics

Economics determines whether an opportunity possesses positive expected value.

Opportunity Cost compares positive opportunities against one another.

Economics asks:

"Is this opportunity worthwhile?"

Opportunity Cost asks:

"Is this opportunity better than the alternatives?"

---

# Relationship to Risk

Risk determines whether an opportunity is acceptable.

Opportunity Cost determines whether it is preferable.

Unsafe opportunities should never enter opportunity-cost comparisons.

Risk remains the first gate.

---

# Failure Behavior

If no superior opportunity exists:

Maintain the current allocation.

If uncertainty exceeds confidence:

Maintain the current allocation.

If opportunity cost cannot be confidently evaluated:

Do not reallocate.

The inability to prove superiority is a valid reason not to move capital.

---

# Future Enhancements

Future versions may incorporate:

- market regime awareness
- historical opportunity learning
- adaptive opportunity models
- AI-assisted comparative reasoning
- cross-asset optimization
- portfolio-wide optimization
- multi-position optimization
- execution forecasting
- tax-aware opportunity evaluation

These enhancements refine decision quality while preserving constitutional principles.

---

# Design Principles

The Opportunity Cost Model should remain:

- Deterministic
- Explainable
- Replayable
- Auditable
- Portfolio Aware
- Provider Neutral
- Economics Driven
- Risk Aware
- Extensible

---

# Constitutional Principles

Every allocation excludes another allocation.

Opportunity cost should always be considered before moving capital.

Remaining invested is an active decision.

Remaining in cash is an active decision.

Expected value should always be evaluated comparatively.

Capital should move only when meaningful superiority has been demonstrated.

The inability to identify a superior opportunity is itself valuable information.

---

# Long-Term Vision

The Opportunity Cost Model elevates OmniTrade from evaluating individual trades to evaluating competing uses of capital.

Rather than asking:

"Will this trade make money?"

OmniTrade continually asks:

"What is the highest expected use of this capital among every admissible opportunity currently available?"

As the platform expands across providers, asset classes, portfolios, and financial ecosystems, opportunity cost becomes one of the governing principles ensuring that every dollar is intentionally allocated, continually justified, and consistently directed toward its greatest expected contribution.