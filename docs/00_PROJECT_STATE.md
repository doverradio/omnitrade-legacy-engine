# OmniTrade Legacy Engine
# PROJECT STATE

Version:
2.0

Last Updated:
2026-07-18

Authority:
Highest

---

# Purpose

This document is the authoritative snapshot of the OmniTrade project.

It records what has actually been proven, what remains unproven, the current engineering objective, and the immediate direction of development.

If this document conflicts with conversation history, this document is considered authoritative until intentionally updated.

---

# Project Vision

OmniTrade is **not** a cryptocurrency trading bot.

OmniTrade is being engineered as an Autonomous Capital Management Platform capable of intelligently allocating capital across multiple financial markets while continuously improving the quality of its own decisions.

The architecture is intentionally market-neutral.

Future asset classes include:

- Cryptocurrency
- Equities
- ETFs
- Options
- Futures
- Forex
- Prediction Markets
- Additional financial markets

No future expansion should require architectural redesign.

---

# Current Objective

Achieve the first fully autonomous profitable real-money trade while preserving:

- Explainability
- Auditability
- Deterministic behavior
- Capital preservation
- Risk governance

Every engineering decision should move the platform closer to this objective.

---

# Current Milestone

## FIRST AUTONOMOUS PROFIT

Definition of Done

One commissioned autonomous capital campaign performs:

Campaign Selection

↓

Strategy Selection

↓

Risk Approval

↓

Production BUY

↓

Autonomous Position Management

↓

Production SELL

↓

Reconciliation

↓

Accounting Completion

↓

Verified Positive Net Profit

without operator intervention during execution.

Success is waking up to more money than when the campaign began.

---

# Proven Capabilities

## Execution Layer

✅ Live Kraken authentication

✅ Live production BUY

✅ Live production SELL

✅ Live production reconciliation

## Decision Layer

✅ Decision Records

✅ Replay architecture

✅ Decision Intelligence

✅ Risk Engine

✅ Position lifecycle

✅ Immutable audit evidence

## Capital Management

✅ Autonomous Capital Campaign architecture

✅ Campaign governance

✅ Campaign lifecycle

✅ Campaign identity persistence

## Platform

✅ Provider-neutral execution layer

✅ Exchange abstraction

✅ Production accounting framework

✅ Commissioned proving workflow

---

# Not Yet Proven

The following remain before the First Autonomous Profit milestone is complete.

□ One commissioned campaign executes a production BUY.

□ Campaign identity remains authoritative throughout reconciliation.

□ Accounting completes successfully.

□ Autonomous lifecycle manages the position.

□ Production SELL completes.

□ Net profit is verified.

---

# Engineering Philosophy

OmniTrade optimizes for:

Correctness before speed.

Evidence before assumptions.

Architecture before features.

Production proof before expansion.

Safety before automation.

Decision quality before profitability.

Long-term compounding over short-term gains.

---

# Current Development Philosophy

Development proceeds in small, bounded implementation tasks.

Large speculative implementation prompts are avoided.

Every completed task should be:

- testable
- reviewable
- deterministic
- independently valuable

---

# Current Priority

Before implementing any new feature, ask:

"Does this move OmniTrade closer to First Autonomous Profit?"

If the answer is no, the work should normally be postponed.

---

# Long-Term North Star

The long-term objective is not merely profitable trading.

The objective is a continuously improving autonomous capital management platform whose knowledge compounds alongside its capital.

Every decision becomes permanent knowledge.

The knowledge compounds.

The capital compounds.

Both improve together.


---

# Current Proven Runtime Behavior

✓ Autonomous worker cycles execute repeatedly.

✓ Multiple strategies generate BUY and SELL proposals.

✓ Decision Records continue to be generated.

✓ Risk Engine evaluates candidate trades.

✓ Current production blocker:

Risk Engine is rejecting BUY candidates before execution.

The immediate engineering objective is to determine whether these
rejections are caused by intentional risk policy,
position sizing,
minimum order limits,
campaign constraints,
or a configuration defect.