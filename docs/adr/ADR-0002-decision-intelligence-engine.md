# ADR-0002: Decision Intelligence Engine

## Status
Accepted

## Context

OmniTrade's existing architecture logged individual pieces of a trading decision in separate places: `signals` held the raw signal, `model_outputs` held AI scoring, `risk_events` held risk engine decisions, and `trades` held execution results (`DATABASE_SCHEMA.md`). This is sufficient for operating the platform day-to-day, but it means that understanding "why did the platform do X" requires manually joining several tables and reconstructing a narrative after the fact — and it means the platform has no persistent, coherent notion of its own reasoning history to learn from over time. `PROJECT_VISION.md`'s long-term family-legacy vision explicitly values the platform accumulating wisdom, not just data, over years of operation. A scattered-log approach cannot deliver that; a purpose-built memory system can.

## Decision

OmniTrade Legacy Engine has a permanent core subsystem — the **Decision Intelligence Engine (DIE)** — whose job is to be the platform's permanent memory and reasoning system, not just an explainability add-on. Full architecture is documented in `DECISION_INTELLIGENCE_ENGINE.md`. Key elements of the decision:

- The DIE produces one **Decision Record** per decision-evaluation instance, whether or not it resulted in a trade — including rejected signals and `hold` outcomes, which are treated as equally valuable to the knowledge base as executed trades.
- The DIE is purely **observational**: it never influences a decision in real time, and it never bypasses or duplicates the risk engine's authority. It only ever writes after a decision has already been made elsewhere in the pipeline.
- The DIE is one of the platform's four permanent core engines (ADR-0001), not a feature bolted onto the AI layer or the audit log.
- Implementation of the DIE itself is explicitly deferred to a future, unscheduled phase (`MVP_BUILD_PLAN.md`'s "Future Phase" section) — this ADR records the architectural commitment, not a demand to build it immediately. In the meantime, MVP phases are required to populate `signals`, `model_outputs`, and `risk_events` completely and consistently, since that data is the DIE's eventual foundation.

## Alternatives Considered

- **Do nothing; rely on `signals`/`model_outputs`/`risk_events` plus manual joins for any retrospective analysis.** Rejected because it doesn't scale to the kind of pattern-mining (`DECISION_INTELLIGENCE_ENGINE.md` §6) the platform's long-term vision requires, and because it provides no natural home for genuinely new data types like counterfactual outcomes (ADR-0003) or decision quality scores (ADR-0007).
- **Fold "decision memory" into the existing AI layer (`AI_LAYER.md`) as an additional responsibility** rather than a new engine. Rejected because the AI layer is explicitly advisory/scoring in nature, while the DIE's job (recording, whether or not AI was involved, and regardless of the AI layer's own correctness) is a different kind of responsibility — conflating them would make both harder to reason about independently.
- **Build a full implementation immediately** rather than deferring to a future phase. Rejected for MVP scope-discipline reasons: the DIE's data model benefits from being informed by real operating experience from Phases 1–8 first, and forcing it into the MVP timeline would risk under-designing it.

## Consequences

- Every future phase's implementation must not take shortcuts that would leave `signals`, `model_outputs`, or `risk_events` incomplete, since this is now an explicit architectural dependency, not just good practice.
- The DIE creates an obvious, named home for related future concepts — the Counterfactual Outcome Ledger (ADR-0003), Decision Snapshot (ADR-0004), and Decision Quality Engine (ADR-0007) are all subsystems *within* the DIE rather than separate architectural peers, which keeps the four-engine framing (ADR-0001) stable even as the DIE's internal scope grows.
- There is a real cost of deferred value: none of the DIE's pattern-mining or training-data benefits (`DECISION_INTELLIGENCE_ENGINE.md` §6, §7) are available until a dedicated implementation phase is scheduled and completed.
