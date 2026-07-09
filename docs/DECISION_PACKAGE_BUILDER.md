# DECISION_PACKAGE_BUILDER.md

## OmniTrade Legacy Engine - Decision Package Builder

### Status: Core Architectural Contract

The Decision Package Builder is the canonical read-only assembler for replay input.

It converts immutable production decision artifacts into a versioned Decision Package.

It does not reconstruct state from live tables.

It does not mutate production behavior.

It does not perform replay.

---

## Purpose

Replay needs a stable input boundary.

The builder provides that boundary by collecting the immutable records already written by production into a single package with explicit versioning, deterministic ordering, and availability states for optional sections.

---

## Package Contract

### Schema Version

`dp_v1` is the first canonical package schema.

Any future change that alters the logical package shape requires a new schema version.

### Core Fields

The package contains:

- Decision Record
- Decision Snapshot, when present
- Explainability records
- Quality scores
- Counterfactual results
- Alternative actions
- Source lineage
- Field provenance
- Availability state for optional sections
- A deterministic content hash

### Optional Sections

Optional sections are represented explicitly.

Missing sections are not hidden.

They are marked as `unavailable` in the package availability state.

---

## Determinism Rules

- Rows are loaded in stable order.
- The package hash is derived from normalized package content.
- `built_at` is metadata only and does not participate in the logical content hash.
- Decimal, datetime, UUID, and nested JSON values are normalized before hashing.

---

## Canonical Inputs

The current package builder assembles the package from the existing decision artifacts:

- `decision_records`
- `decision_snapshots`
- `decision_explainability_records`
- `decision_quality_scores`
- `decision_counterfactual_results`
- `decision_alternative_actions`

These are append-only or immutable sources in the current architecture.

---

## Non-Goals

- No replay execution.
- No Arena integration.
- No AI Coach changes.
- No live-table reconstruction.
- No production write path.

---

## Relationship To Replay

The Decision Replay Engine consumes Decision Packages.

The Decision Package Builder produces them.

That boundary keeps replay reproducible and prevents accidental coupling to live production tables.

---

## Readiness Certification Layer (v0)

Replay candidate exposure is gated by a read-only readiness certification pass.

The certification verifies that a package is:

- buildable
- deterministic
- hashable
- version-pinned
- explicit about missing artifacts
- safe for replay input consumption

This layer only certifies package readiness metadata.

It does not execute replay.

It does not write to production state.