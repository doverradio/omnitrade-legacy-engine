# ADR-0014: Synthetic Evidence Provenance and Persistence Isolation

## Status
Accepted

## Context

Future historical/counterfactual runs (Phase 4+) will produce large volumes of synthetic decisions, fills, and ledger entries. `CUSTODY_AND_SECURITY_MODEL.md`'s compartmentalization principle and the Constitution's Article VIII (Safety) require that this synthetic activity can never be mistaken for, or accidentally written into, production truth — a single shared database and a "just tag the rows" discipline is exactly the kind of convention that fails under time pressure or a copy-paste bug.

## Decision

Synthetic evidence gets structural, not conventional, isolation:

- A dedicated `SimulationBase(DeclarativeBase)`, entirely separate from the production `Base` (`app/db/base.py`). No table may be registered on both.
- A dedicated database target, `OT_SIMULATION_DATABASE_URL`, with no fallback to the production `DATABASE_URL` under any configuration state. If simulation persistence is requested and this is unset, startup fails closed (`SimulationConfigurationError`) rather than silently reusing production.
- `IsolationGuard.verify_or_die` (ADR-0012's boundary rule) mechanically checks, at the point a historical/counterfactual session or provider is about to be bound, that the simulation and production database targets are not the same (normalized comparison, not raw string equality) and that no live execution provider is in use.
- `EvidenceContext` (ADR-0013's carrier) is the shared, in-memory provenance contract; this phase does **not** add any provenance columns to production `decision_records`/`decision_snapshots` — synthetic provenance lives only in the simulation namespace, once tables exist there (Phase 4+).

This phase stands up the `SimulationBase`, engine, sessionmaker, and `IsolationGuard` with zero simulation tables defined yet — the isolation boundary is built before there is anything to isolate, deliberately.

## Alternatives Considered

- One database, tag rows by `evidence_class`. Rejected: a single missed filter (an unfiltered `SELECT`, a forgotten `WHERE`) silently contaminates production aggregates; structural separation makes that class of bug impossible rather than merely detectable.
- Add provenance columns to `decision_records` now, ahead of Phase 4. Rejected by explicit scope of this phase: production decision tables must remain byte-unchanged until a real consumer (Phase 4+) exists for the columns.

## Consequences

Benefits:
- A historical run literally cannot write to a production table — there is no shared metadata object that would let it, and `IsolationGuard` refuses to even open a session if the configured target resolves to production.
- Production migrations and schema stay completely unaffected by simulation work, now and through Phase 4+.

Trade-offs:
- Two database connections/engines to operate and monitor once simulation work begins in earnest (Phase 4+).
- Any future model that legitimately needs to exist in both namespaces (unlikely, but possible for shared reference data) will require an explicit decision, not an automatic one — this ADR intentionally makes that hard.
