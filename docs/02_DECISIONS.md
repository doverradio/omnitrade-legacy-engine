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

## Future Decisions

Append only.

Never rewrite previous entries.

Always explain:

- what changed
- why it changed
- alternatives rejected
- long-term consequences

The goal is to preserve engineering reasoning for every future contributor, human or AI.