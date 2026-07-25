# ADR-0013: Canonical Simulation and Evidence Terminology

## Status
Accepted

## Context

`RunMode` (ADR-0012) names the execution context. It does not name what the resulting *evidence* is permitted to be used for downstream — a decision produced by the same code path can be genuine production evidence, a forward-paper rehearsal, a historical replay, or a counterfactual "what if," and the Decision Intelligence Engine (`DECISION_INTELLIGENCE_ENGINE.md`, Article II/III of the Constitution) must never blend those categories in a single metric or learning signal. Without a canonical, shared name for this distinction, each future subsystem (DQE, DIE, tournaments) would invent its own ad hoc tagging.

## Decision

Introduce `EvidenceClass` as the canonical vocabulary for what a piece of evidence *is*, kept deliberately separate from `RunMode`: `PRODUCTION_LIVE`, `FORWARD_PAPER`, `HISTORICAL_POINT_IN_TIME`, `COUNTERFACTUAL`, `UNIT_TEST`.

Terminology rules:
- `EvidenceClass` is always carried explicitly wherever evidence provenance matters — never inferred from `RunMode` by a downstream consumer, so a consumer with no opinion about an unrecognized class fails closed instead of guessing.
- "Historical" evidence is always `HISTORICAL_POINT_IN_TIME`, never plain `HISTORICAL` — the name itself encodes the point-in-time discipline (ADR-0015) so the constraint cannot be silently dropped by a future rename.
- These are the only accepted names for these concepts platform-wide; future code must not introduce synonyms (e.g. "backtest_evidence", "sim_class").

## Alternatives Considered

- Deriving evidence class from `RunMode` implicitly (one enum, not two). Rejected: a single `RunMode` can legitimately produce evidence destined for more than one downstream use (e.g. a `UNIT_TEST` run producing fixtures also usable as illustrative examples); keeping them separate, even though they overlap 1:1 today, avoids a breaking rename later.
- Free-text `evidence_source` strings instead of an enum. Rejected: unenforceable at the type level, and exactly the kind of ad hoc tagging this ADR exists to prevent.

## Consequences

Benefits:
- One name, used everywhere, for "what kind of evidence is this" — DQE/DIE consumption (future Phase 6) can filter/require a class without re-deriving the concept.
- Makes "never blend synthetic and production evidence" a checkable property (compare `EvidenceClass` values) rather than a convention someone has to remember.

Trade-offs:
- Two parallel enums (`RunMode`, `EvidenceClass`) to keep in sync conceptually, even though most call sites will set them to the "matching" pair.
