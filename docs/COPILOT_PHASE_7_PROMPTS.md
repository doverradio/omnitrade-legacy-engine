# OmniTrade Decision Intelligence Platform - GitHub Copilot Prompts: Phase 7

## Status
Planned

## Phase Name
Decision Intelligence Foundation

## Purpose
Phase 7 implements the first production slice of the Decision Intelligence Engine (DIE) as an observational and analytical subsystem.

This phase turns completed execution history into explainable decision intelligence that answers:
- What happened
- Why it happened
- What changed
- What should be tested next

## Non-Negotiable Boundary
Decision Intelligence in Phase 7:
- consumes historical decisions, risk decisions, portfolio history, and strategy results
- generates observations, diagnostics, and recommendations

Decision Intelligence in Phase 7 must never:
- submit trades
- bypass or override the Risk Engine
- rewrite strategy code or mutate strategy logic
- modify historical records in place

## ADR Check
Before starting any prompt below, perform the ADR check from docs/adr/README.md.

Expected outcome for normal prompt execution in this file:
- No new ADR is required if implementation stays within existing architecture and accepted ADR boundaries.
- If a prompt implementation changes foundational engine boundaries, weakens Risk Engine authority, or introduces a write-path that mutates historical decision evidence, stop and request ADR guidance before coding.

## Scope Guardrails (Non-Negotiable)
Do implement in Phase 7:
- Decision Record and Decision Snapshot foundations
- Decision timeline and explainability evidence views
- Counterfactual Outcome Ledger v1 (observational only)
- WAIT decision analysis support
- Decision Quality scoring foundations
- Experiment recommendation generation (advisory only)
- Explainability/decision API read surfaces
- Decision Intelligence dashboard foundations
- Validation and exit-gate criteria

Do not implement in Phase 7:
- Decision Arena runtime
- Capital Allocation Engine runtime
- live trading
- autonomous strategy/risk mutation
- any execution path from Decision Intelligence to trading adapters

## Architectural Alignment Rules
- Preserve the four permanent foundational engines.
- Keep Decision Intelligence observational and post-decision.
- Keep dependency direction clean: execution/risk write history; Decision Intelligence reads and enriches it.
- Maintain immutable historical records; append-only enrichment only.
- Any recommendation output is advisory and human-reviewable.

## Proposed Phase 7 Architecture
### Component Topology
1. `services/decisions/recording`: normalizes decision evidence from signals, model outputs, risk events, trades into Decision Records.
2. `services/decisions/snapshots`: captures immutable Decision Snapshot payloads at decision creation time.
3. `services/decisions/timeline`: read model for chronological decision narratives.
4. `services/decisions/explainability`: supporting/opposing evidence, risk adjustments, confidence and rationale traces.
5. `services/decisions/counterfactuals`: Counterfactual Outcome Ledger v1 (BTC-first, lightweight, scheduled evaluation).
6. `services/decisions/quality`: Decision Quality scoring using counterfactual outputs and process-quality dimensions.
7. `services/decisions/recommendations`: experiment recommendation generation from observed patterns.
8. `api/routes/decisions.py`: read-only and review-safe API surfaces for Decision Intelligence.
9. `web/app/decision-intelligence/*`: dashboard and explorer pages consuming Decision Intelligence APIs.

### Data Flow
1. Existing runtime writes remain unchanged for execution authority (`signals`, `model_outputs`, `risk_events`, `trades`).
2. DIE recording consumes those outputs and writes Decision Record + Decision Snapshot.
3. Counterfactual workers append shadow outcomes and per-horizon evaluations.
4. Decision Quality and recommendations append derived analytics.
5. UI and API surfaces read aggregated decision intelligence; no trade execution path is exposed.

### Safety Controls
- Explicit prohibition of execution adapters in DIE modules.
- Append-only write policy for decision-history entities.
- Audit logging for all Decision Intelligence state transitions and review actions.
- Fail-visible behavior for unavailable analytics (never silent defaults implying certainty).

## Prompt Breakdown Summary
1. 7.1 Decision Record and Snapshot model foundations
2. 7.2 Decision ingestion pipeline (from existing historical sources)
3. 7.3 Decision timeline read model
4. 7.4 Explainability evidence (supporting/opposing, risk-adjustments)
5. 7.5 Counterfactual Outcome Ledger v1
6. 7.6 Alternative-actions and WAIT decision analysis
7. 7.7 Decision Quality scoring foundations
8. 7.8 Experiment recommendation generation
9. 7.9 Explainability and decision API read surfaces
10. 7.10 Decision Intelligence dashboard, validation, and exit gate

## Risks
- Schema overreach risk: adding too much ahead of validated usage.
- Compute creep risk in counterfactual processing.
- Data lineage drift between source tables and Decision Records.
- Misinterpretation risk: advisory outputs mistaken for execution directives.
- Confidence overfitting risk if quality metrics collapse to pure PnL.

## Dependencies
- Completed and stable Phase 6 risk and orchestration outputs.
- Reliable writes to `signals`, `model_outputs`, `risk_events`, and `trades`.
- Background worker/scheduler infrastructure for counterfactual evaluation.
- Existing auth/audit conventions and error envelope behavior.
- Frontend charting/state patterns for dashboard integration.

## Exit Criteria
- Decision records written deterministically for evaluated decisions, including rejects and WAIT outcomes where applicable.
- Decision snapshots immutable and reproducible.
- Counterfactual v1 produces bounded, auditable horizon evaluations.
- Decision Quality score and experiment recommendations available as advisory outputs.
- API and dashboard provide explainable read access with fail-visible unknown states.
- Full backend/frontend test and lint gates pass.

## Success Metrics
- Decision coverage rate: percentage of eligible decisions with complete Decision Record + Snapshot.
- Explainability completeness: percentage with supporting + opposing evidence and risk-adjustment traces.
- Counterfactual completion rate by horizon.
- Confidence calibration trend over time.
- Recommendation acceptance/review throughput (human-in-loop).
- Zero incidents of DIE-triggered trade execution or risk bypass.

## Execution Rule
Run prompts in order. Complete one prompt at a time. Stop after each prompt for review.

---

## Prompt 7.1 - Decision Record and Snapshot Foundations

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/DATABASE_SCHEMA.md
- docs/PROJECT_CONSTITUTION.md
- docs/SYSTEM_ARCHITECTURE.md

Exact scope:
- Introduce Decision Record and Decision Snapshot foundational models and service contracts aligned with DIE architecture.
- Define append-only write semantics and immutable snapshot enforcement.
- Establish clear field-level provenance mappings from existing source records.

Explicit exclusions:
- No execution integration changes.
- No counterfactual computation yet.
- No dashboard implementation yet.

Validation commands:
- cd apps/api
- pytest tests/unit -v

Stop-for-review:
Stop and report files changed, commands run, validation results, and ADR status before continuing.

---

## Prompt 7.2 - Decision Ingestion Pipeline from Historical Sources

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/RISK_ENGINE.md
- docs/DATABASE_SCHEMA.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Implement deterministic ingestion that composes Decision Records from `signals`, `model_outputs`, `risk_events`, and `trades`.
- Ensure non-executed decisions (rejected/held) are captured.
- Add idempotent ingestion behavior and deterministic deduping.

Explicit exclusions:
- No write-back into execution/risk tables.
- No trade submission capabilities.
- No recommendations or scoring yet.

Validation commands:
- cd apps/api
- pytest tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.3 - Decision Timeline Read Model

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/API_CONTRACTS.md
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/PROJECT_VISION.md

Exact scope:
- Implement timeline-oriented read model(s) for chronological decision narratives.
- Support filtering by account, asset, strategy, and status.
- Preserve explicit unknown/unavailable state semantics in read payloads.

Explicit exclusions:
- No scoring logic.
- No experiment recommendation logic.
- No dashboard UI work yet.

Validation commands:
- cd apps/api
- pytest tests/api tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.4 - Explainability Evidence Records

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/PROJECT_CONSTITUTION.md
- docs/API_CONTRACTS.md

Exact scope:
- Implement explainability record structures for supporting evidence, opposing evidence, confidence factors, and risk adjustments.
- Ensure each decision can answer why accepted, resized, rejected, or held.
- Enforce no-null-silent semantics for required explainability fields.

Explicit exclusions:
- No counterfactual computation.
- No DQE scoring.
- No experiment recommendations.

Validation commands:
- cd apps/api
- pytest tests/unit tests/api -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.5 - Counterfactual Outcome Ledger v1

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/MVP_BUILD_PLAN.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/SECURITY_AND_SAFETY.md

Exact scope:
- Implement COL v1 shadow outcomes for BUY/SELL/WAIT as observational records only.
- Implement bounded horizon evaluation workflow (v1 horizons only) with append-only results.
- Attach structured lesson tags to per-horizon evaluations.

Explicit exclusions:
- No order routing or execution side effects.
- No heavy-compute expansion beyond v1 constraints.
- No live-trading behavior.

Validation commands:
- cd apps/api
- pytest tests/services tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.6 - Alternative Actions and WAIT Decision Support

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/RISK_ENGINE.md
- docs/PROJECT_VISION.md

Exact scope:
- Implement explicit alternative-action representations per decision.
- Add WAIT support semantics to ensure inactivity is modeled and evaluated as a first-class decision.
- Ensure comparison payloads highlight what changed between chosen vs alternative actions.

Explicit exclusions:
- No auto-action recommendations to execution.
- No strategy mutation.

Validation commands:
- cd apps/api
- pytest tests/unit/services tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.7 - Decision Quality Scoring Foundations

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/RISK_ENGINE.md
- docs/PROJECT_CONSTITUTION.md

Exact scope:
- Implement Decision Quality score model and scoring pipeline foundations based on COL outputs and process-quality dimensions.
- Keep scoring explicitly advisory and post-factum.
- Implement score breakdown fields for explainable diagnostics.

Explicit exclusions:
- No real-time strategy confidence replacement.
- No automatic risk or allocation updates.

Validation commands:
- cd apps/api
- pytest tests/unit/services -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.8 - Experiment Recommendation Generation

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/STRATEGY_ENGINE.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Generate experiment recommendations from decision patterns, lesson tags, and quality metrics.
- Classify recommendations by confidence, expected impact, and required human review level.
- Ensure recommendations are non-executing and non-mutating.

Explicit exclusions:
- No autonomous parameter changes.
- No automated strategy promotion/demotion.

Validation commands:
- cd apps/api
- pytest tests/unit tests/api -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.9 - Explainability and Decision API Surfaces

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/API_CONTRACTS.md
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/DECISION_INTELLIGENCE_ENGINE.md

Exact scope:
- Implement read-only Decision Intelligence API endpoints for listing, detail, search, explanations, and quality outputs.
- Enforce contract-level unknown/unavailable semantics for partial data.
- Ensure authorization and error-envelope consistency with existing API conventions.

Explicit exclusions:
- No write endpoint that mutates historical decision evidence.
- No endpoint that can trigger execution/risk state changes.

Validation commands:
- cd apps/api
- pytest tests/api -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 7.10 - Decision Intelligence Dashboard and Phase Exit Gate

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/PROJECT_STATUS.md
- docs/VALIDATION_CHECKLIST.md

Exact scope:
- Implement Decision Intelligence dashboard/explorer surfaces for timeline, explainability, counterfactuals, quality, and recommendations.
- Ensure fail-visible UI states for unknown/unavailable analytics.
- Run full Phase 7 validation gate and produce exit report artifacts.

Explicit exclusions:
- No Decision Arena features.
- No Capital Allocation runtime behavior.
- No live-trading routing.

Validation commands:
- cd apps/api && pytest -v
- cd apps/web && pnpm test
- cd apps/web && pnpm lint

Stop-for-review:
Stop and report files changed, commands run, validation results, ADR status, exit-gate result, and whether next phase planning is permitted.
