# OmniTrade Legacy Engine
# ARCHITECTURAL DECISIONS

Purpose

This document records major architectural decisions.

It is not a changelog.

It records WHY important decisions were made so future chats and contributors understand the reasoning.

Each decision is immutable once recorded.

Append new entries only.

---

# Template

## YYYY-MM-DD

Decision

Reason

Alternatives Considered

Consequences

Future Impact

---

## 2026-07-XX

Decision

Execution became provider-neutral through the Execution Provider Layer.

Reason

No exchange should ever become a permanent dependency.

Coinbase onboarding delays demonstrated the need for interchangeable providers.

Alternatives Considered

Continue building around Coinbase only.

Rejected because it created unnecessary operational risk.

Consequences

Kraken became the primary execution provider.

Future providers require only provider implementations.

Decision Engine, Risk Engine, Portfolio Intelligence, and Decision Intelligence remain unchanged.

Future Impact

Additional providers (Gemini, Interactive Brokers, Alpaca, Kalshi, etc.) can be added without architectural redesign.

---

## 2026-07-XX

Decision

Autonomous Capital Campaigns became a first-class Portfolio Intelligence capability.

Reason

Capital should be managed through campaigns rather than isolated trades.

Future Impact

Future markets, strategies, and asset classes inherit the same campaign architecture.

---

## 2026-07-XX

Decision

Decision Quality became more important than raw profitability.

Reason

Profitable decisions can be poor decisions.

Poor decisions can occasionally be profitable.

The platform optimizes for decision quality first.

Future Impact

All future AI systems evaluate reasoning before profit.

---

## 2026-07-XX

Decision

Small Account Mode became a permanent design constraint.

Reason

If the platform cannot intelligently compound $25, larger balances merely hide weaknesses.

Future Impact

Every future feature must function correctly at the smallest supported balance.

---

## Future Entries

Append only.

Never rewrite history.

Always explain WHY.

---

## 2026-07-16

Decision

Commissioned campaign operator control must remain provider-neutral and non-executing.

Reason

The commissioned control plane exists to mutate operator governance metadata only.

Provider submission authority must remain in the existing governed orchestration and execution layers so pause, resume, acknowledge, and cancel cannot directly place or retry an order.

Alternatives Considered

Allow REST or CLI control-plane handlers to call provider adapters directly.

Rejected because it would blur the recommendation-versus-execution boundary, weaken duplicate-order protection, and create a second live-execution path.

Consequences

The API and CLI wrappers delegate to the shared commissioned control-plane domain service.

The service records audit evidence, enforces allowed source states, requires idempotency keys, rejects changed-intent key reuse, and returns no_execution=true.

Future Impact

Any future commissioning workflow must continue to treat control-plane mutation as a governance surface rather than an execution surface.

---

## 2026-07-16

Decision

Production proving-window evidence must be gathered through read-only commands before any explicit campaign activation approval.

Reason

The final Task 10 handoff must prove runtime stability, observability, reconciliation coherence, and audit visibility without introducing a live economic action during documentation or validation.

Alternatives Considered

Validate readiness by performing a live commissioning action during handoff.

Rejected because documentation and go/no-go preparation must remain operationally safe and reversible.

Consequences

Task 10 defines PASS, FAIL, ABORT, and escalation criteria with exact read-only evidence-gathering commands.

Later mutating steps are documented separately and explicitly marked as requiring operator approval.

Future Impact

Activation decisions can be made from a deterministic evidence package rather than ad hoc judgment.