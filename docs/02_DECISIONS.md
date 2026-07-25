# OmniTrade Legacy Engine
# ARCHITECTURAL DECISIONS

Authority:
Append Only

Never rewrite history.

Never remove previous decisions.

Append new decisions as the project evolves.

This document records **why** important architectural decisions were made.

It is not a changelog.

---

# Entry Format

## Date

Decision

Reason

Alternatives Considered

Consequences

Future Impact

---

## 2026-07

### Execution Provider Layer

Decision

Execution became provider-neutral.

Reason

No exchange should ever become a permanent dependency.

Provider onboarding delays demonstrated the need for interchangeable execution providers.

Alternatives Considered

Building directly around one exchange.

Rejected.

Consequences

Execution providers can be replaced independently of the remainder of the architecture.

Future Impact

Future providers require only provider implementations.

No architectural redesign.

---

## 2026-07

### Autonomous Capital Campaigns

Decision

Capital is managed through campaigns rather than isolated trades.

Reason

Investment objectives belong to campaigns, not individual orders.

Future Impact

Every future asset class inherits the same campaign architecture.

---

## 2026-07

### Decision Quality

Decision

Decision quality is more important than raw profitability.

Reason

A profitable decision can still be poor.

A losing decision can still be correct.

Future Impact

The AI layer evaluates reasoning before outcome.

---

## 2026-07

### Small Account Mode

Decision

The platform must succeed with very small balances.

Reason

If the system cannot intelligently compound $25, larger balances merely conceal weaknesses.

Future Impact

Every feature must function correctly for the smallest supported account.

---

## 2026-07

### Replay Architecture

Decision

Every production decision must be replayable.

Reason

Replay enables:

- debugging
- AI coaching
- deterministic audits
- research
- regression testing

Future Impact

Future AI systems learn from immutable historical evidence rather than reconstructed guesses.

---

## 2026-07

### Immutable Decision Records

Decision

Decision Records are immutable.

Reason

Historical decisions are evidence.

Evidence must never change.

Future Impact

Every AI evaluation is based on trustworthy historical facts.

---

## 2026-07

### Fail Closed

Decision

Every production safety boundary fails closed.

Reason

Unexpected behavior should stop execution rather than continue unpredictably.

Future Impact

Safety always overrides opportunity.

---

## 2026-07

### Provider-Neutral Governance

Decision

Governance must never depend upon any exchange.

Reason

Operational control belongs to OmniTrade.

Execution belongs to providers.

Those responsibilities remain separate.

Future Impact

Future providers inherit identical governance.

---

## 2026-07

### Campaign Identity

Decision

Campaign identity is authoritative throughout execution.

Reason

Every production order must remain attributable to the campaign that authorized it.

Future Impact

Reconciliation, accounting, AI analysis, and reporting all preserve campaign ownership.

---

## 2026-07

### Production Before Expansion

Decision

The first autonomous profitable trade takes precedence over new functionality.

Reason

An unfinished platform gains little from additional features.

Production proof creates confidence for every subsequent phase.

Future Impact

Development remains milestone-driven rather than feature-driven.

---

## 2026-07

### Small, Bounded Engineering Tasks

Decision

Large implementation prompts are avoided.

Reason

Smaller implementation tasks consistently produce higher quality code, simpler reviews, and fewer regressions.

Future Impact

Future AI-assisted development remains incremental, verifiable, and maintainable.

---

## 2026-07

### Runtime Evidence Before Expansion

Decision

Operational evidence takes precedence over new feature work.

Reason

The production runtime has reached the point where engineering effort
produces more value by explaining runtime behavior than by adding
additional capabilities.

Future Impact

Engineering remains focused on achieving the first autonomous profit
through evidence-based debugging instead of speculative feature growth.

---

## 2026-07

### Parallel Authorized Lanes

Decision

Production proving-campaign work toward First Autonomous Profit and
expansion-foundation work (Historical Intelligence Platform Phase 3:
operating modes, evidence contracts, isolated simulation persistence)
are now both authorized to proceed in parallel, rather than expansion
work waiting strictly behind First Autonomous Profit.

Reason

The live proving campaign continues operating independently while its
current blocker (mandate package authorization expiry / executor
failure at package progression, not a Risk Engine or strategy-threshold
defect) is diagnosed and fixed. Phase 3 foundation work is
production-isolated by construction: a separate `SimulationBase`
metadata root, a separate `OT_SIMULATION_DATABASE_URL` with no fallback
to the production database, and an `IsolationGuard` that fails closed
on any historical/counterfactual run bound to production. Because this
isolation is structural, not conventional, Phase 3 work carries no risk
to the live proving campaign and does not need to wait behind it.

Also recorded here: `IMPLEMENTATION_MASTER_PLAN.md` Phase 1's original
diagnosis (unwired Risk Engine inputs — `campaign_authorized_notional`,
stop-loss, loss-history) is superseded by runtime evidence. The actual
blocker observed in production is package-progression/mandate
authorization, downstream of a BUY candidate that already passed
Strategy, Economics, and Risk. Phase 1's original risk-input changes
were explicitly NOT implemented as part of this lane; they should be
re-evaluated only if future evidence again implicates the Risk Engine
inputs specifically.

Alternatives Considered

Hold all expansion-foundation work until First Autonomous Profit is
fully proven. Rejected: Phase 3's isolation guarantees make this
unnecessarily conservative — the isolation is enforced by
`IsolationGuard`/separate persistence, not by sequencing discipline
alone, so there is no real coupling to protect by waiting.

Fix Phase 1's originally-diagnosed risk inputs anyway, since the master
plan called for it. Rejected: doing so now would modify Risk Engine
behavior based on a diagnosis the current runtime evidence contradicts,
risking a change with no basis in the actual observed failure.

Consequences

Two lanes of engineering work can proceed without either blocking the
other, as long as expansion-foundation work never touches the live
autonomous crypto path, risk_engine.py decision math, campaign/mandate/
execution/reconciliation/strategy behavior, or production
decision_records/decision_snapshots schema.

Future Impact

Future engineering sessions should treat "production proving" and
"expansion foundation" as two named, independently-tracked lanes, and
should re-diagnose the production blocker from fresh runtime evidence
(package progression / mandate authorization) rather than reusing the
superseded Phase 1 risk-input theory.

---

## Future Decisions

Append only.

Never rewrite previous entries.

Always explain:

- what changed
- why it changed
- alternatives rejected
- long-term consequences

The goal is to preserve engineering reasoning for every future contributor, human or AI.