# ADR-0016: Immutable Historical Dataset Requirement

## Status
Accepted

## Context

`IMMUTABLE_HISTORICAL_DATASETS.md` requires that any dataset a historical or counterfactual run is bound to be reproducible: the same `(dataset_id, dataset_version)` must always yield the same content, or a promotion decision (`ADR-0011`-style governed gate, future Phase 8) built on top of a replay result cannot be trusted. Today, historical evidence would be read from the same mutable `candles` table production ingestion writes to — adequate for this phase's scaffolding, but not a basis for reproducible research once real datasets are built (Phase 5).

## Decision

Record the requirement now, ahead of the implementation (Phase 5 builds the actual content-addressed dataset system):

- Any dataset a `HISTORICAL_SIMULATION` or `COUNTERFACTUAL` run binds to must, once Phase 5 exists, be identified by content hash (e.g. a Merkle root over its chunks), not by a mutable table reference.
- `EvidenceContext.dataset_id` / `dataset_version` (ADR-0013) are the fields this identity will be carried on; this phase defines the fields, not the hashing/build/verification machinery.
- Until Phase 5 ships, no `HISTORICAL_SIMULATION`/`COUNTERFACTUAL` run may claim reproducibility as a proven property — this phase's scaffolding is isolation-only (ADR-0014), not yet dataset-immutable.

## Alternatives Considered

- Read directly from the production `candles` table for historical replay indefinitely. Rejected long-term: `candles` is mutable (ingestion can backfill/correct rows), so two runs against "the same" data at different times could silently diverge — incompatible with the reproducibility guarantee later promotion gates (Phase 8) require.
- Build the full content-addressed dataset system in this phase. Out of scope: explicitly deferred to Phase 5 by the master plan; this phase only needs the vocabulary (`dataset_id`/`dataset_version` already exist on `EvidenceContext`) to exist so Phase 5 has a field to populate rather than a schema change to make.

## Consequences

Benefits:
- Reproducibility is recorded as a requirement before any research conclusion could be drawn from a non-reproducible replay, closing off the temptation to treat early scaffolding results as trustworthy research evidence.
- Phase 5 has a pre-agreed field contract (`dataset_id`/`dataset_version` on `EvidenceContext`) to build against.

Trade-offs:
- Until Phase 5, `dataset_id`/`dataset_version` on any `EvidenceContext` constructed in this phase are necessarily `None`/placeholder — there is no dataset system yet for them to identify.
