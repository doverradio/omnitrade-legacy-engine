# ADR-0008: Capital Allocation Engine

## Status
Accepted

## Context

OmniTrade's long-term direction has evolved from "trading bot" framing to a Decision Intelligence Platform framing (`PROJECT_VISION.md`, `PROJECT_CONSTITUTION.md`). In that direction, Paper Trading is not just simulated execution; it is the proving ground where strategies, portfolios, and future agents are validated before any real capital is ever exposed.

As portfolio complexity grows, the architecture needs a dedicated subsystem for disciplined capital stewardship across paper portfolios and, later, explicitly approved live capital. That need must be documented without introducing architectural drift from the four permanent foundational engines established in ADR-0001 and reinforced in `PROJECT_CONSTITUTION.md` Article VI.

This ADR is architectural intent only. It does not introduce implementation details, schema changes, API changes, or code changes.

## Decision

Introduce the **Capital Allocation Engine** as a permanent subsystem of the **Portfolio Intelligence Engine**.

It is **not** a fifth foundational engine. The four-core model remains unchanged:
1. Market Intelligence Engine
2. Strategy Evolution Engine
3. Decision Intelligence Engine
4. Portfolio Intelligence Engine

The Capital Allocation Engine will eventually manage allocation of paper capital and, later, explicitly approved live capital across portfolios and future agents. It remains subordinate to the Risk Engine and explicit human approval requirements at all times.

## Responsibilities

When implemented in a future phase, the Capital Allocation Engine is expected to support:

- Allocate capital to portfolios.
- Allocate portfolio capital to future agents.
- Rebalance allocations.
- Recover idle capital.
- Increase allocation to successful portfolios.
- Reduce allocation to underperforming portfolios.
- Increase allocation to successful agents.
- Reduce allocation to underperforming agents.
- Produce allocation recommendations.
- Track allocation history.
- Support future portfolio optimization.

## Architectural Placement

The long-term hierarchy is:

Master Account  
↓  
Paper Portfolios  
↓  
Strategies  
↓  
Future Agents

Architectural interpretation:
- Portfolios are the capital containers.
- Agents belong inside portfolios.
- Agents compete within portfolios, not directly under the master account.

## Relationship to Portfolio Intelligence

Portfolio Intelligence remains responsible for:

- Portfolio accounting.
- Portfolio performance.
- Portfolio analytics.
- Paper execution.
- Capital Allocation Engine.

The Capital Allocation Engine is therefore a subsystem of Portfolio Intelligence, not a standalone foundational engine.

## Relationship to Decision Arena

The Decision Arena will eventually consume allocation outputs produced by the Capital Allocation Engine.

Boundary:
- The Capital Allocation Engine does not evaluate trade decision quality.
- The Decision Arena remains responsible for decision evaluation and comparative decision workflows.

## Relationship to Decision Intelligence

Decision Intelligence (`DECISION_INTELLIGENCE_ENGINE.md`) analyzes historical decisions and can surface evidence that informs allocation changes.

Boundary:
- Decision Intelligence may recommend allocation changes.
- Decision Intelligence never changes allocations automatically.

## Relationship to Risk Engine

The Risk Engine always has final authority (`RISK_ENGINE.md`, `PROJECT_CONSTITUTION.md` Article VIII).

The Capital Allocation Engine cannot bypass:

- Risk limits.
- Kill switches.
- Position sizing constraints.
- Trading pauses.
- Human approvals.

## Human Approval Rules

The Capital Allocation Engine may recommend:

- Allocation changes.
- Portfolio promotion.
- Agent promotion.

It may never automatically:

- Allocate real money.
- Promote an agent to live trading.
- Deploy new agent versions.
- Modify strategy code.
- Modify AI models.
- Override the Risk Engine.

Every transition from paper capital to live capital requires explicit human approval.

## Versioning

Future agents must be explicitly versioned and remain historically comparable.

Example:

Trend Hunter v1  
↓  
Trend Hunter v2

No agent may silently rewrite itself.

## Alternatives Considered

- **Introduce a fifth foundational engine for capital allocation.** Rejected to preserve the fixed four-core architecture from ADR-0001 and avoid architectural sprawl.
- **Keep allocation concerns inside Strategy Evolution's existing strategy-weight allocator only (`AI_LAYER.md` §2.3).** Rejected because strategy signal weighting and portfolio/agent capital stewardship are related but distinct scopes; this ADR keeps strategy weighting where it is and defines portfolio/agent capital allocation under Portfolio Intelligence.
- **Treat capital allocation as an implementation detail with no ADR.** Rejected because this changes subsystem boundaries and long-term architecture, which meets ADR criteria in `docs/adr/README.md`.

## Consequences

Benefits:

- Cleaner separation of responsibilities.
- Portfolio-first architecture.
- Future multi-agent support.
- Better capital stewardship.
- Better explainability.
- Better auditability.
- Stronger alignment with the Decision Intelligence Platform vision.
- No architectural drift from the Four Core Engine model.

Trade-offs:

- Adds a future subsystem that will require explicit interfaces among Portfolio Intelligence, Decision Arena, Decision Intelligence, and Risk Engine.
- Requires additional implementation ADRs later for scoring, rebalance policy, and rollout controls before any Phase 5+ implementation uses it in production workflows.
