# ADR-0012: Operating Modes and Adapter Boundaries

## Status
Accepted

## Context

The platform is expanding beyond a single live proving campaign toward future historical simulation, counterfactual research, and multi-asset work (`IMPLEMENTATION_MASTER_PLAN.md`, Phase 3+). Every one of those future contexts must reuse the same decision logic (`generate_signal`, `evaluate_signal_risk`) that production uses today — the Constitution's Article VII (Evidence) and Article X (Stewardship) rule out forking that logic per context. Without an explicit, named notion of "which context is this running under," future code has no principled way to know whether it is safe to touch a live provider, write to production tables, or submit a real order.

## Decision

Introduce `RunMode` as the single, shared vocabulary for execution context, with five values: `PRODUCTION_LIVE`, `FORWARD_PAPER`, `HISTORICAL_SIMULATION`, `COUNTERFACTUAL`, `UNIT_TEST`.

Adapter boundaries:
- Financial decision logic (strategies, Risk Engine) is reused unchanged across every `RunMode`; only what surrounds it — the data gateway, the broker/provider, the persistence target — changes per mode.
- A live execution provider (`ExchangeProviderClient` registry) may only be bound when `RunMode` is `PRODUCTION_LIVE` or `FORWARD_PAPER`. `HISTORICAL_SIMULATION` and `COUNTERFACTUAL` must use a non-live adapter (a synthetic broker, in a future phase — not built yet).
- This phase defines the enum and the boundary rule (enforced by `IsolationGuard`, ADR-0014); it does not build the synthetic broker or any simulation orchestrator.

## Alternatives Considered

- A boolean `is_live` flag instead of an enum. Rejected: collapses materially different contexts (forward-paper vs. historical vs. counterfactual) into one bit, losing the distinctions later phases need.
- Forking strategy/risk logic per mode (e.g. a "backtest strategy" variant). Rejected: directly contradicts reusing the same decision logic that Phase 4+ depends on, and risks the two forks silently drifting apart.

## Consequences

Benefits:
- One vocabulary for "what context is this" that every future phase (historical simulation, counterfactual branching, tournaments) can share instead of inventing its own.
- Decision logic stays singular and provably identical across contexts.

Trade-offs:
- The enum is aspirational ahead of its consumers — `HISTORICAL_SIMULATION` and `COUNTERFACTUAL` have no orchestrator yet (Phase 4+). It is added now, empty of behavior beyond the isolation guard, specifically so later phases build against a stable vocabulary rather than inventing one under time pressure.
