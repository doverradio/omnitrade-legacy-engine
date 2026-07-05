# Architecture Decision Records (ADRs) — OmniTrade Legacy Engine

## Purpose

This folder holds the permanent record of *why* OmniTrade Legacy Engine's architecture looks the way it does. Every major architectural decision — a new core subsystem, a foundational technology choice, a change to how the platform's engines relate to each other — must have a corresponding ADR before or alongside the documentation that describes it.

This is a direct extension of the project's core philosophy (`PROJECT_VISION.md` §2): the platform preserves *why*, not just *what*, for the decisions it makes about markets (`DECISION_INTELLIGENCE_ENGINE.md`) and for the decisions made about the platform itself. An ADR is to the architecture what a Decision Record is to a trade — a structured, permanent, explainable record of a choice and the reasoning behind it.

## What Requires an ADR

An ADR is required for any decision that:

- Introduces a new core subsystem or permanent engine (e.g., the Decision Intelligence Engine, the Counterfactual Outcome Ledger).
- Changes the relationship between existing core engines or subsystems.
- Picks a foundational technology, framework, or architectural pattern that would be costly to reverse (e.g., the backend language/framework, the database, the monorepo layout).
- Changes a safety-, risk-, or audit-relevant guarantee described in `SECURITY_AND_SAFETY.md` or `RISK_ENGINE.md`.
- Reverses or materially amends a previous ADR.

An ADR is **not** required for:

- Ordinary feature work within an already-decided architecture (e.g., adding a new strategy module per `STRATEGY_ENGINE.md`'s existing pattern).
- Routine parameter, schema-column, or endpoint additions that extend an existing table/contract without changing its shape or intent.
- Bug fixes, refactors, or documentation corrections that don't change an architectural decision (see `DOCS_AUDIT_REPORT.md` for an example of this kind of change — none of those fixes needed an ADR).

When it's ambiguous whether something counts as "architectural," default to writing the ADR — a short, unnecessary ADR costs little; a missing one for a real architectural decision costs a lot down the line, especially for a platform meant to be understood and extended by future maintainers who won't have this conversation's context.

## The Rule for Copilot (and Any Future Implementer)

**Before starting any new phase or major subsystem, Copilot must check whether the implementation changes or creates an architectural decision. If yes, it must stop and ask whether an ADR is required before coding.**

This rule is restated in `HANDOFF_TO_COPILOT.md` as a standing hard rule, and applies to every phase, not just the ones active at the time this rule was written. It exists so architecture decisions are never made silently, inside a code-generation session, without a durable record — consistent with `PROJECT_VISION.md`'s "process over speed" philosophy and the guiding constraint that nothing ships by silently trading away auditability.

## ADR Format

Every ADR in this folder uses the same structure:

```markdown
# ADR-000X: Title

## Status
Accepted | Proposed | Superseded by ADR-000Y | Deprecated

## Context
What situation or problem led to this decision being needed.

## Decision
What was decided, stated plainly.

## Alternatives Considered
What else was considered, and why it wasn't chosen.

## Consequences
What this decision makes easier, harder, or newly possible — including honest trade-offs, not just benefits.
```

## Numbering

ADRs are numbered sequentially (`ADR-0001`, `ADR-0002`, ...) in the order they were adopted, and are never renumbered or deleted — if a decision is later reversed, a new ADR supersedes the old one, and the old one's `Status` is updated to point at it. This preserves the same "append-only, never edit history" principle already used for `audit_log` (`DATABASE_SCHEMA.md` §2.12) and for Decision Records (`DECISION_INTELLIGENCE_ENGINE.md`) — the ADR log is itself a decision ledger and should be held to the same standard.

## Current ADR Index

| ADR | Title | Status |
|---|---|---|
| [ADR-0001](./ADR-0001-four-core-engines.md) | Four Core Engines | Accepted |
| [ADR-0002](./ADR-0002-decision-intelligence-engine.md) | Decision Intelligence Engine | Accepted |
| [ADR-0003](./ADR-0003-counterfactual-outcome-ledger.md) | Counterfactual Outcome Ledger | Accepted |
| [ADR-0004](./ADR-0004-decision-snapshot.md) | Decision Snapshot | Accepted |
| [ADR-0005](./ADR-0005-small-account-mode.md) | Small Account Mode | Accepted |
| [ADR-0006](./ADR-0006-fastapi-backend.md) | FastAPI Backend | Accepted |
| [ADR-0007](./ADR-0007-decision-quality-engine.md) | Decision Quality Engine | Accepted |

This index must be updated whenever a new ADR is added or an existing one's status changes.
