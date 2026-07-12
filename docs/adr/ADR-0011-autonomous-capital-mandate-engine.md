# ADR-0011: Autonomous Capital Mandate Engine

## Status
Accepted

## Context

Autonomous Capital Management Mode requires deterministic policy authority that can evaluate whether a proposed autonomous action is permitted before any execution path is considered. Existing governance architecture provides strong risk, approval, resilience, and audit controls, but does not provide a centralized mandate domain that can answer this authorization question in a provider-agnostic and controller-agnostic way.

Without centralized mandate evaluation:
- policy enforcement risks being duplicated across route/service/provider layers;
- approval exemption behavior cannot be deterministic or auditable;
- multi-provider expansion would embed mandate logic into adapters;
- autonomy-level expansion would require redesign instead of extension.

## Decision

Introduce a dedicated Autonomous Capital Mandate Engine domain with centralized, deterministic evaluation.

The Mandate Engine consists of:
- mandate domain model;
- mandate state model with explicit transitions;
- immutable mandate version model for economic envelope and allowed scope;
- append-only authorization model with deterministic explanations;
- eligibility evaluator returning `AUTHORIZED` or `REJECTED` and ordered reasons;
- validation service for autonomy levels, state transitions, version validity, and version immutability.

Architectural boundaries:
- Mandate evaluation is a reusable domain service.
- Provider adapters remain unaware of mandates.
- Controller/UI layers do not determine authorization.
- Mandate Engine never submits orders.

Approval architecture:
- Human approval remains default platform behavior.
- Mandate Engine defines architecture support for `APPROVAL_SATISFIED_BY_ACTIVE_MANDATE` only when deterministic checks pass.
- Integration with live execution flow is deferred.

Autonomy levels:
- `LEVEL_0`: analysis only.
- `LEVEL_1`: recommendation with per-order human approval.
- `LEVEL_2`: bounded autonomous capital management under active mandate and mandatory Risk Engine authority.
- `LEVEL_3`: reserved for future institutional portfolio architecture; no current implementation.

Decision Intelligence linkage contract:
- Future Decision Records reference mandate ID, mandate version ID, autonomy level, and authorization result.
- This contract is established now without changing execution behavior.

## Alternatives Considered

- Keep mandate checks in route/controller code.
  Rejected: non-deterministic reuse, weak testability, and high drift risk.

- Push mandate checks into providers.
  Rejected: violates provider abstraction and blocks multi-provider portability.

- Implement autonomous submission first and add mandate later.
  Rejected: inverts governance order and weakens safety boundaries.

- Keep recommendation-only autonomy permanently.
  Rejected: conflicts with ACMM architectural ground truth.

## Consequences

Benefits:
- Deterministic, auditable policy authority for autonomous eligibility.
- Clear sequencing: mandate authorization before any future autonomous execution integration.
- Strong provider neutrality for Kraken, Coinbase, and future providers.
- Natural extension path to multi-user/multi-campaign/multi-provider level 3 architecture.

Trade-offs:
- Adds new persistence and domain contracts that must be maintained.
- Requires explicit migration and schema governance for future envelope evolution.
- Requires future integration work in approval and execution services, intentionally deferred in this phase.
