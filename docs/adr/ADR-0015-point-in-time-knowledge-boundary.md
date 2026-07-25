# ADR-0015: Point-in-Time Knowledge Boundary

## Status
Accepted

## Context

`WORLD_STATE_AND_KNOWLEDGE_MODEL.md` establishes that a decision may only be informed by what was actually knowable at the time it was made. A historical or counterfactual run replays past time deterministically, which creates the specific, easy-to-get-wrong risk of look-ahead leakage — a gateway or data source that can see data whose `close_time`, `knowledge_available_at`, or dataset-availability timestamp lies in that run's future relative to its simulated clock. This is a correctness property, not an implementation detail: a leaking simulation silently produces decisions no real point in the past could ever have made.

## Decision

Every future evidence source consumed by a `HISTORICAL_SIMULATION` or `COUNTERFACTUAL` run must be bound by an explicit, provable knowledge cutoff:

- `EvidenceContext.knowledge_cutoff_at` (ADR-0013) is the canonical field carrying this boundary; it is required (not optional-and-ignored) for any evidence context whose `run_mode` is `HISTORICAL_SIMULATION` or `COUNTERFACTUAL`.
- Enforcement of the boundary belongs to the data-access layer (a future point-in-time gateway, Phase 4), not to the caller — a strategy or the Risk Engine must never be trusted to self-police what it looks at.
- On any ambiguity about whether a piece of evidence was knowable at the cutoff, the correct behavior is to withhold the evidence and fail closed, never to guess in the permissive direction.
- This ADR records the boundary rule now, ahead of the gateway that will enforce it (Phase 4) — no gateway, dataset, or replay code is built in this phase.

## Alternatives Considered

- Trust callers (strategies) to only request appropriately-bounded data. Rejected: every strategy would need to independently reimplement the same discipline, and one lapse silently corrupts the entire run's validity.
- Defer the knowledge-boundary decision until Phase 4 actually builds the gateway. Rejected: the boundary is a correctness *contract*, and `EvidenceContext` (this phase's deliverable) is the field it will be carried on — deciding the rule and the field together, now, avoids a breaking schema change to `EvidenceContext` later.

## Consequences

Benefits:
- A single, named place (`knowledge_cutoff_at`) every future gateway/dataset must honor, instead of an implicit assumption re-derived per implementer.
- Makes leakage testable as a first-class property (Phase 4's acceptance criteria: an injected future candle must never change a past decision) rather than an informal expectation.

Trade-offs:
- The field exists in `EvidenceContext` now with no enforcing code behind it yet — a real gap between the contract and its guarantee until Phase 4 ships the gateway. This is an accepted, explicitly-scoped gap, not an oversight.
