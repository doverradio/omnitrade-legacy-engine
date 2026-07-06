# OmniTrade Decision Intelligence Platform - GitHub Copilot Prompts: Phase 8

## Status
Planned

## Phase Name
Decision Arena

## Purpose
Phase 8 introduces a Decision Arena for structured, auditable comparison of multiple competing decision agents operating exclusively on paper portfolios.

The Decision Arena is a comparative evaluation subsystem. It consumes existing platform evidence and does not replace or bypass established execution, risk, or decision-intelligence authority boundaries.

## Non-Negotiable Boundary
Decision Arena in Phase 8:
- consumes existing Decision Intelligence outputs (Decision Records, Decision Snapshot context, explainability evidence, counterfactual outcomes, Decision Quality, recommendations)
- compares agent behavior and outcomes under identical inputs and constraints
- operates only in paper-portfolio context

Decision Arena in Phase 8 must never:
- submit live trades
- bypass or override the Risk Engine
- bypass or replace Decision Intelligence pipelines
- mutate historical decision evidence in place
- auto-promote agents to live capital

## ADR Check
Before starting any prompt below, perform the ADR check from docs/adr/README.md.

Expected outcome for normal prompt execution in this file:
- No new ADR is required if implementation stays within existing four-engine boundaries and accepted ADR constraints.
- If implementation introduces foundational engine changes, weakens Risk Engine authority, creates autonomous self-modifying behavior, or introduces live-capital promotion logic, stop and request ADR guidance before coding.

## Scope Guardrails (Non-Negotiable)
Do implement in Phase 8:
- Multi-agent orchestration in paper context
- Agent registration and immutable version identity for arena participation
- Paper capital allocation for arena competitions (arena-scoped, paper-only)
- Agent performance tracking and comparison
- Decision Quality comparison
- Explainability comparison
- Counterfactual comparison
- Tournament history and replayable competition records
- Arena leaderboard and portfolio-level competitions
- Read-only/controlled API and dashboard surfaces for arena workflows

Do not implement in Phase 8:
- Capital Allocation Engine runtime
- Live Trading
- autonomous self-modifying agents
- automatic promotion to live capital
- any execution path that bypasses Risk Engine authority

## Architectural Alignment Rules
- Preserve the four permanent foundational engines.
- Keep dependency direction clean:
  - Market/Strategy/Portfolio produce runtime evidence.
  - Risk Engine remains final authority before any paper execution.
  - Decision Intelligence records and enriches outcomes.
  - Decision Arena consumes this evidence for comparison.
- Every participating agent receives identical:
  - market data
  - portfolio state
  - risk constraints
- Agent action space is strictly BUY, SELL, WAIT.
- Arena recommendations remain advisory and human-reviewable.
- Arena artifacts are append-only and auditable.

## Proposed Phase 8 Architecture
### Component Topology
1. `services/arena/registration`: agent registration, eligibility checks, immutable agent version identity.
2. `services/arena/orchestration`: synchronized evaluation cycles, identical input fan-out, deterministic cycle IDs.
3. `services/arena/evaluation`: agent decision capture (BUY/SELL/WAIT) and per-cycle comparison payloads.
4. `services/arena/paper_allocation`: arena-scoped paper capital partitioning for competitions (not Capital Allocation Engine runtime).
5. `services/arena/risk_gate`: integration contract ensuring all arena candidate actions pass existing Risk Engine path.
6. `services/arena/performance`: returns, drawdown, fee drag, consistency, risk-discipline metrics.
7. `services/arena/decision_intelligence_bridge`: joins to Decision Records, explainability evidence, counterfactuals, Decision Quality.
8. `services/arena/tournaments`: tournament lifecycle, match scheduling, replayable history.
9. `services/arena/leaderboard`: ranking views by configurable multi-dimensional criteria.
10. `api/routes/arena.py`: read-safe and review-safe API surfaces.
11. `web/app/decision-arena/*`: dashboard/explorer pages for competitions, comparisons, and leaderboard.

### Data Flow
1. Arena cycle starts for a paper portfolio competition.
2. Orchestrator snapshots identical inputs (market data, portfolio state, risk constraints) for all participating agents.
3. Each agent independently proposes BUY/SELL/WAIT.
4. Proposed actions are evaluated through existing Risk Engine path; no direct execution path from arena modules.
5. Paper execution (if approved) remains in existing paper execution subsystem.
6. Decision Intelligence continues recording decision evidence and outcomes.
7. Arena ingests Decision Intelligence outputs and computes comparative metrics.
8. Tournament records and leaderboard entries are appended and exposed via API/UI.

### Safety Controls
- Explicit prohibition of execution adapters in arena modules.
- Mandatory Risk Engine gate integration for every candidate action.
- Append-only storage policy for arena history, tournament outcomes, and ranking snapshots.
- Deterministic cycle identity and idempotency controls for orchestration.
- Fail-visible unknown/unavailable semantics for comparison surfaces.

## Prompt Breakdown Summary
1. 8.1 Arena model foundations and append-only contracts
2. 8.2 Agent registration and immutable version identity
3. 8.3 Synchronized multi-agent orchestration with identical inputs
4. 8.4 Arena-scoped paper capital allocation layer (paper-only, non-CAE runtime)
5. 8.5 Risk Engine integration and authority boundary enforcement
6. 8.6 Performance tracking and portfolio-level competition metrics
7. 8.7 Decision Intelligence comparison bridge (DQE, explainability, counterfactuals)
8. 8.8 Tournament lifecycle and history
9. 8.9 Leaderboard computation and read models
10. 8.10 Decision Arena API/UI surfaces and Phase 8 exit gate

## Dependencies
- Completed and validated Phase 7 Decision Intelligence Foundation.
- Stable existing runtime writes for `signals`, `model_outputs`, `risk_events`, `trades`, and Decision Intelligence entities.
- Existing Risk Engine authority and deterministic evaluation behavior.
- Existing paper execution stack and portfolio accounting flows.
- Background worker/scheduler support for arena cycles and tournament processing.
- Existing auth/audit conventions and error envelope behavior.
- Existing frontend chart/state patterns for dashboard integration.

## Risks
- Fairness drift risk if agent inputs are not perfectly synchronized.
- Boundary erosion risk if arena modules gain indirect execution authority.
- Metric gaming risk if leaderboard weights overemphasize short-term PnL.
- Compute creep risk from unbounded tournament permutations.
- Data lineage ambiguity risk when joining arena outcomes to Decision Intelligence evidence.
- Misinterpretation risk if users treat leaderboard rank as auto-deployment approval.

## Exit Criteria
- Multi-agent cycles run with identical market/portfolio/risk inputs across agents and deterministic cycle IDs.
- Every arena candidate action remains routed through Risk Engine authority path.
- Arena-scoped paper allocation works without introducing Capital Allocation Engine runtime behavior.
- Tournament history and leaderboard are reproducible, append-only, and auditable.
- Comparative views include performance, Decision Quality, explainability, and counterfactual dimensions.
- API and UI expose fail-visible unknown/unavailable states.
- Full backend/frontend test and lint gates pass.
- No live-trading routes, live-capital promotion paths, or autonomous self-modifying agent behavior introduced.

## Success Metrics
- Arena cycle fairness rate: percentage of cycles with provably identical input sets across agents.
- Risk-gate integrity: zero incidents of arena actions bypassing Risk Engine.
- Comparison completeness rate: percentage of arena decisions with linked DQE/explainability/counterfactual evidence.
- Tournament reproducibility rate: percentage of re-run tournament slices producing identical outcomes under fixed inputs.
- Leaderboard stability signal: rank churn explained by evidence changes rather than ingestion/order nondeterminism.
- Human review throughput: percentage of arena recommendations reviewed before any strategy/agent promotion action.
- Zero incidents of implicit live-capital enablement.

## ADR Recommendations
Recommended ADR check outcomes before implementation:
1. Confirm no new foundational engine is introduced (Decision Arena remains a subsystem, not a fifth engine).
2. Confirm arena-scoped paper allocation is explicitly distinct from Capital Allocation Engine runtime scope (ADR-0008 boundary preservation).
3. Confirm Risk Engine final-authority contract remains unchanged and non-bypassable for all arena candidate actions.
4. Confirm agent evolution and promotions remain human-approved and non-autonomous.

Potential new ADR needed only if any of the following is proposed:
- changing permanent engine boundaries
- introducing live-capital promotion automation
- introducing autonomous self-modifying agents
- materially changing risk authority model

## Execution Rule
Run prompts in order. Complete one prompt at a time. Stop after each prompt for review.

---

## Prompt 8.1 - Arena Model Foundations

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/PROJECT_CONSTITUTION.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/DECISION_INTELLIGENCE_ENGINE.md

Exact scope:
- Define Decision Arena foundational entities and service contracts.
- Enforce append-only semantics and idempotency for arena lifecycle records.
- Define immutable cycle/tournament identity and provenance fields.

Explicit exclusions:
- No execution integration changes.
- No live trading.
- No Capital Allocation Engine runtime behavior.

Validation commands:
- cd apps/api
- pytest tests/unit -v

Stop-for-review:
Stop and report files changed, commands run, validation results, and ADR status.

---

## Prompt 8.2 - Agent Registration and Version Identity

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/PROJECT_CONSTITUTION.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Implement agent registration for arena participation.
- Enforce immutable version identity and registration auditability.
- Implement eligibility validation for paper-only competitions.

Explicit exclusions:
- No autonomous evolution.
- No live-capital promotion.

Validation commands:
- cd apps/api
- pytest tests/unit/services -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.3 - Synchronized Multi-Agent Orchestration

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/RISK_ENGINE.md
- docs/DECISION_INTELLIGENCE_ENGINE.md

Exact scope:
- Implement deterministic orchestration cycles.
- Guarantee each agent receives identical market data, portfolio state, and risk constraints.
- Capture agent decisions (BUY/SELL/WAIT) with cycle-level provenance.

Explicit exclusions:
- No direct execution from orchestration modules.
- No risk bypass paths.

Validation commands:
- cd apps/api
- pytest tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.4 - Arena-Scoped Paper Capital Allocation Layer

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/MVP_BUILD_PLAN.md
- docs/PROJECT_CONSTITUTION.md

Exact scope:
- Implement arena-scoped paper capital partitioning for competitions.
- Support portfolio-level competition setups and accounting isolation.
- Keep allocation behavior bounded to Decision Arena competitions.

Explicit exclusions:
- No Capital Allocation Engine runtime implementation.
- No live capital allocation.

Validation commands:
- cd apps/api
- pytest tests/unit tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.5 - Risk Engine Authority Integration

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/PROJECT_CONSTITUTION.md

Exact scope:
- Integrate arena candidate actions with existing Risk Engine path.
- Enforce hard guarantee that no arena action bypasses risk checks.
- Add auditable rejection/resize/approval capture for arena decisions.

Explicit exclusions:
- No risk authority weakening.
- No alternate execution gate.

Validation commands:
- cd apps/api
- pytest tests/services tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.6 - Performance Tracking and Portfolio Competitions

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/PROJECT_CONSTITUTION.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Implement arena performance metrics across profit, drawdown, fee drag, consistency, and risk discipline.
- Support portfolio-level competition aggregation.
- Preserve metric provenance and reproducibility.

Explicit exclusions:
- No winner-based auto-promotion.
- No live trading coupling.

Validation commands:
- cd apps/api
- pytest tests/unit/services -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.7 - Decision Intelligence Comparison Bridge

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/PROJECT_CONSTITUTION.md
- docs/SYSTEM_ARCHITECTURE.md

Exact scope:
- Implement comparison joins to Decision Quality, explainability evidence, and counterfactual outcomes.
- Build normalized comparison payloads per agent/cycle/tournament context.
- Preserve fail-visible unknown/unavailable semantics.

Explicit exclusions:
- No replacement of Decision Intelligence outputs.
- No mutation of historical decision evidence.

Validation commands:
- cd apps/api
- pytest tests/api tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.8 - Tournament Lifecycle and History

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/PROJECT_CONSTITUTION.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Implement tournament lifecycle state model and scheduling.
- Persist append-only tournament history and replay metadata.
- Add deterministic tie-break and ordering rules.

Explicit exclusions:
- No autonomous agent mutation.
- No non-audited tournament state transitions.

Validation commands:
- cd apps/api
- pytest tests/unit tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.9 - Leaderboard and Comparison Read Models

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/PROJECT_CONSTITUTION.md

Exact scope:
- Implement leaderboard read models using multi-dimensional scoring.
- Expose transparent scoring breakdown and ranking provenance.
- Support filtering by portfolio, tournament, horizon, and time window.

Explicit exclusions:
- No automatic deployment or allocation actions from rank outcomes.
- No live-capital routing.

Validation commands:
- cd apps/api
- pytest tests/api -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 8.10 - Decision Arena API/UI and Phase Exit Gate

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/RISK_ENGINE.md
- docs/PROJECT_STATUS.md
- docs/VALIDATION_CHECKLIST.md

Exact scope:
- Implement Decision Arena API surfaces and dashboard/explorer pages.
- Ensure comparisons are observational, auditable, and fail-visible.
- Run full Phase 8 validation gate and produce exit report artifacts.

Explicit exclusions:
- No Capital Allocation Engine runtime implementation.
- No live trading.
- No automatic promotion to live capital.

Validation commands:
- cd apps/api && pytest -v
- cd apps/web && pnpm test
- cd apps/web && pnpm lint

Stop-for-review:
Stop and report files changed, commands run, validation results, ADR status, exit-gate result, and whether next-phase planning is permitted.
