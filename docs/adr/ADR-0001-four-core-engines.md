# ADR-0001: Four Core Engines

## Status
Accepted

## Context

Early in the project's architecture work, OmniTrade Legacy Engine's capabilities — data ingestion, strategy evaluation, backtesting, AI scoring, risk gating, paper execution — were described as a flat list of components (`SYSTEM_ARCHITECTURE.md`'s component breakdown). As the platform grew to include a permanent memory/reasoning subsystem (the Decision Intelligence Engine, ADR-0002) alongside the original components, it became clear that a flat component list doesn't communicate which parts of the system are *permanent architectural pillars* versus which are implementation details within a pillar. Without an explicit top-level framing, there was a real risk that future additions (like the Decision Intelligence Engine) would be treated as bolt-on features rather than being understood as standing at the same architectural level as data ingestion or execution.

## Decision

OmniTrade Legacy Engine's architecture is organized around exactly **four permanent foundational engines**:

1. **Market Intelligence Engine** — ingestion, candles, indicators, regime detection (spans `DATA_SOURCES.md`, `STRATEGY_ENGINE.md`'s regime/filter modules, `AI_LAYER.md`'s regime classifier).
2. **Strategy Evolution Engine** — strategy modules, parameter sets, backtesting, allocation, promotion lifecycle (spans `STRATEGY_ENGINE.md`, `AI_LAYER.md`'s allocator).
3. **Decision Intelligence Engine** — the platform's permanent memory and reasoning system (`DECISION_INTELLIGENCE_ENGINE.md`).
4. **Portfolio Intelligence Engine** — paper account state, positions, risk posture, equity/performance tracking (spans `RISK_ENGINE.md`, `paper_accounts`/`trades` in `DATABASE_SCHEMA.md`).

These four are a fixed, named set — not an open-ended list that grows every time a new subsystem is added. New subsystems (e.g., the Counterfactual Outcome Ledger, the Decision Quality Engine) are added *underneath* one of these four engines, never as a fifth peer, unless a future ADR explicitly revisits this decision. None of the four engines necessarily has its own standalone document — three of the four are deliberately implemented as a conceptual grouping over existing docs (`DATA_SOURCES.md`, `STRATEGY_ENGINE.md`, `AI_LAYER.md`, `RISK_ENGINE.md`, `DATABASE_SCHEMA.md`); only the Decision Intelligence Engine has a dedicated document, because its scope didn't fit naturally inside any single existing doc.

## Alternatives Considered

- **A flat component list with no top-level grouping** (the original state). Rejected because it gives every component equal apparent weight, making it hard to communicate that some things (like the Decision Intelligence Engine) are permanent architectural pillars, not features.
- **A growing, unbounded list of "engines"** that expands every time a significant subsystem is added. Rejected because it would dilute the term "engine" and make the architecture harder to hold in one's head over years of extension — exactly the failure mode this fixed framing is meant to prevent.
- **Giving each engine its own standalone document regardless of overlap with existing docs.** Rejected as unnecessary duplication for three of the four engines, whose scope is already well-covered by existing docs; only introduced a new document (`DECISION_INTELLIGENCE_ENGINE.md`) where the scope genuinely didn't fit elsewhere.

## Consequences

- Every future architectural addition must be evaluated against this fixed set: "which of the four engines does this belong under?" If the answer is "none, this needs a fifth," that itself is a decision requiring a new ADR that revisits this one — as happened when the Decision Quality Engine was explicitly placed under the Decision Intelligence Engine rather than proposed as a fifth engine (ADR-0007).
- Documentation and onboarding (`HANDOFF_TO_COPILOT.md`, `PROJECT_VISION.md`) can now use a consistent, stable four-part framing rather than an ever-changing component list.
- There is a minor cost in indirection: understanding "the Market Intelligence Engine" requires reading three separate docs rather than one, since it has no dedicated file. This trade-off was accepted deliberately (see Alternatives) to avoid unnecessary duplication.
