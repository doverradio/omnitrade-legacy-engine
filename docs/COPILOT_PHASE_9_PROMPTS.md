# OmniTrade Decision Intelligence Platform - GitHub Copilot Prompts: Phase 9

## Status
Planned

## Phase Name
Live Trading Foundation

## Purpose
Phase 9 introduces a tightly controlled Live Trading Foundation as an optional execution mode after successful paper validation. This phase does not change the platform's four permanent foundational engines and does not introduce autonomous capital deployment.

Live trading is treated as a governed operating mode under Portfolio Intelligence execution boundaries, with Risk Engine authority and human approval gates enforced at every material transition.

## Non-Negotiable Boundary
Phase 9 must:
- keep Paper Trading as the default execution mode
- treat Live Trading as optional and explicitly enabled per account
- preserve Risk Engine as the mandatory final authority before order submission
- require explicit human approval before any live-capital deployment
- ensure every live decision and execution event is explainable and auditable

Phase 9 must never:
- implement autonomous capital allocation
- implement autonomous strategy evolution
- auto-promote any strategy, agent, account, or portfolio from paper to live
- bypass or weaken Risk Engine authority

## ADR Check
Before starting any prompt below, perform the ADR check from docs/adr/README.md.

Expected outcome for normal prompt execution in this file:
- No new foundational engine ADR is required if work remains inside existing Portfolio Intelligence execution boundaries, preserves Risk Engine authority, and keeps live deployment human-gated.

Stop and request ADR guidance before coding if any proposal:
- changes permanent four-engine boundaries
- introduces autonomous promotion or autonomous real-capital routing
- materially changes risk authority model or allows alternate execution gates
- introduces non-auditable live execution paths

## Scope Guardrails (Non-Negotiable)
Do implement in Phase 9:
- Live account registration and eligibility lifecycle
- Broker adapter abstraction for live order routing
- Live execution workflow with pre-trade and post-trade controls
- Human approval checkpoints for live enablement and material risk changes
- Kill switch and emergency stop semantics for live operations
- Live portfolio accounting integration
- Order reconciliation and fill reconciliation pipelines
- Slippage tracking and partial-fill handling
- Broker outage detection, fail-safe behavior, and recovery procedures
- Real-money audit logging and compliance-aware record retention patterns
- Rollback and recovery procedures for live incidents
- Read-safe operational dashboards and review endpoints for live controls

Do not implement in Phase 9:
- autonomous capital allocation engine behavior
- autonomous strategy evolution behavior
- automatic promotion from paper to live
- any execution path that bypasses Risk Engine authority

## Architectural Alignment Rules
- Preserve the four permanent foundational engines.
- Keep dependency direction clean:
  - Market/Strategy/Portfolio produce runtime candidates and state.
  - Risk Engine remains the final mandatory gate before live execution.
  - Decision Intelligence records decision context, rationale, and outcomes.
  - Live execution consumes approved actions and emits auditable execution evidence.
- Live mode is opt-in at account scope and always human-approved.
- Live and paper execution share orchestration contracts where possible, but credentials, approvals, and safeguards remain mode-specific.
- Live artifacts are append-only and auditable with explicit provenance.

## Proposed Phase 9 Architecture
### Component Topology
1. services/live/registration
- live account onboarding, broker linkage metadata, eligibility state, and approval prerequisites.

2. services/live/approvals
- human approval workflow contracts for live enable/disable, risk-profile changes, and re-arm actions.

3. services/live/broker_adapters
- broker abstraction interface and concrete adapters; standardized request/response/error envelope.

4. services/live/execution_orchestration
- deterministic execution pipeline from approved signal to broker submission with idempotency and replay-safe semantics.

5. services/live/risk_gate
- explicit integration contract to existing Risk Engine for every live candidate action.

6. services/live/reconciliation
- order reconciliation, fill reconciliation, and account state reconciliation between internal ledger and broker state.

7. services/live/accounting
- live portfolio accounting updates, fee capture, slippage attribution, and realized/unrealized PnL rollups.

8. services/live/resilience
- outage detection, fail-closed behavior, retry/backoff policy, dead-letter/event replay handling, and controlled recovery.

9. services/live/audit
- real-money audit trail persistence, immutable event lineage, operator action logging, and compliance export surfaces.

10. api/routes/live.py
- controlled API endpoints for registration, approvals, kill switch operations, reconciliation status, and read models.

11. web/app/live-trading and risk-monitor integrations
- operator-focused UI for live readiness, approvals, kill switch state, incident state, and reconciliation visibility.

### Reference Data Flow
1. Strategy/agent emits candidate action under configured schedule.
2. Orchestration performs duplicate/idempotency pre-check.
3. Candidate action passes through Risk Engine (mandatory).
4. If approved and live account is human-enabled, live execution orchestration prepares broker order.
5. Broker adapter submits order and records request/response envelopes.
6. Execution events (accepted, partial fill, full fill, reject, cancel) stream into reconciliation.
7. Accounting updates portfolio state, fees, slippage, and realized/unrealized outcomes.
8. Decision Intelligence links decision rationale to live execution outcomes.
9. Audit subsystem records all operator actions, approvals, risk outcomes, broker events, and reconciliation adjustments.
10. Recovery workflows handle outages/mismatches with fail-closed controls and explicit operator re-approval where required.

### Safety Controls
- Mandatory Risk Engine gate before any live order submission.
- Global and account-level kill switches with explicit human re-arm.
- Fail-closed behavior on reconciliation mismatch, broker outage, or ambiguous execution state.
- Idempotency keys on live execution requests and broker callback processing.
- Immutable audit/event log for all real-money state transitions.
- Explicit approval checkpoints before first live enablement and any material control changes.

## Prompt Breakdown Summary
1. 9.1 Live trading domain foundations and boundary contracts
2. 9.2 Live account registration and readiness state machine
3. 9.3 Human approval workflow and operator controls
4. 9.4 Broker adapter abstraction and provider integration contracts
5. 9.5 Live execution orchestration with mandatory risk gate
6. 9.6 Live portfolio accounting, order/fill reconciliation, and partial-fill handling
7. 9.7 Slippage tracking and execution quality telemetry
8. 9.8 Kill switch, emergency stop, outage handling, and controlled recovery
9. 9.9 Real-money audit logging, compliance records, and reporting surfaces
10. 9.10 Live trading API/UI operational surfaces and Phase 9 exit gate

## Dependencies
- Completed and validated Phases 1 through 8, including Decision Arena completion.
- Stable Risk Engine behavior with deterministic evaluation and auditable outcomes.
- Mature paper execution pipeline and portfolio accounting baseline for parity checks.
- Decision Intelligence record linkage available for explainability and post-trade analysis.
- Existing authN/authZ, audit log conventions, and error envelope behavior.
- Background workers/event processing infrastructure for reconciliation and recovery jobs.
- Secure secret management and environment isolation for broker live credentials.
- Operational runbooks for incident response and kill-switch governance.

## Risks
- Real-capital loss risk from execution/reconciliation divergence.
- Broker API instability causing ambiguous order states.
- Latency/slippage drift degrading live execution quality versus paper assumptions.
- Control-plane misuse risk if approval and kill-switch permissions are too broad.
- Compliance exposure from incomplete retention, attribution, or operator-action logging.
- Boundary erosion risk if live adapters acquire undocumented side channels around risk checks.
- False confidence risk if paper validation metrics are over-generalized to live conditions.
- Recovery-risk amplification if retries/resubmissions are not idempotent and auditable.

## Exit Criteria
- Live mode remains disabled by default and is enabled only through explicit human approval flow.
- Every live order attempt is traceably routed through Risk Engine final authority.
- Broker adapter layer supports deterministic request/response mapping with normalized error handling.
- Order and fill reconciliation detect, surface, and resolve mismatches with fail-closed controls.
- Partial fills, cancel/replace, and broker rejects are correctly represented in accounting and audit records.
- Slippage and fee attribution are persisted and visible in operational read models.
- Kill switch and emergency stop flows halt live submissions immediately and require explicit re-arm.
- Outage recovery runbooks and code paths are validated for safe restart and replay semantics.
- Real-money audit logs are append-only, attributable, and exportable for compliance review.
- Backend/frontend test and lint gates pass with no regression in paper workflows.
- No autonomous capital allocation, autonomous strategy evolution, auto-promotion, or risk-bypass path exists.

## Success Metrics
- Risk gate integrity: zero live order submissions without recorded risk decision linkage.
- Approval integrity: 100% of live-enabled accounts have complete approval provenance.
- Reconciliation timeliness: percentage of live orders/fills reconciled within target SLA.
- Reconciliation accuracy: percentage of sessions with zero unresolved broker/internal state mismatches.
- Slippage observability: percentage of live fills with complete expected-vs-actual slippage attribution.
- Incident containment: mean time from emergency condition detection to kill-switch enforcement.
- Recovery correctness: percentage of outage recoveries completed without duplicate order side effects.
- Audit completeness: percentage of material live state transitions with full actor/action/context evidence.
- Explainability linkage: percentage of live executions linked to Decision Intelligence rationale/outcome records.

## Regulatory and Compliance Considerations
- Maintain immutable, timestamped, actor-attributed records for all live trading decisions and operator interventions.
- Preserve order lifecycle evidence (submit, acknowledge, reject, cancel, partial/full fill, reconciliation adjustment).
- Ensure retention and exportability of records needed for audit/compliance review in supported jurisdictions.
- Enforce least-privilege operational permissions for approvals, kill switches, and re-arm actions.
- Keep clear separation between advisory analytics and executable control paths.
- Document broker-specific compliance constraints and map them to adapter-level validations.

## Rollback and Recovery Procedures (Planning Scope)
- Immediate containment:
  - trigger account/global kill switch
  - block new live submissions
  - preserve in-flight event journal
- State assessment:
  - reconcile open orders and positions against broker truth
  - classify mismatches by severity and required action
- Controlled rollback:
  - revert live mode to paper-only for affected scope
  - preserve immutable incident trace and human approval history
- Recovery:
  - replay safe, idempotent reconciliation jobs
  - require explicit human approval to re-enable live mode
  - publish post-incident findings and controls update recommendations

## ADR Recommendations
Recommended ADR check outcomes before implementation:
1. Confirm Live Trading remains an optional execution mode, not a foundational engine.
2. Confirm Risk Engine final-authority contract is unchanged and non-bypassable.
3. Confirm human approval gates are mandatory for live enablement and re-arm operations.
4. Confirm broker adapter abstraction does not introduce hidden execution pathways.
5. Confirm compliance/audit retention obligations are satisfied by append-only evidence design.

Potential new ADR may be required if any of the following is proposed:
- changing permanent foundational engine boundaries
- introducing autonomous capital routing or promotion logic
- materially changing kill-switch governance semantics
- adopting a broker interaction model that conflicts with established audit/risk controls

## Execution Rule
Run prompts in order. Complete one prompt at a time. Stop after each prompt for review.

---

## Prompt 9.1 - Live Trading Foundation Contracts

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/PROJECT_CONSTITUTION.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/RISK_ENGINE.md

Exact scope:
- Define Live Trading domain entities, state machine boundaries, and immutable event contracts.
- Establish explicit optional-mode semantics (paper default, live opt-in).
- Define append-only provenance fields for real-money actions.

Explicit exclusions:
- No broker implementation details yet.
- No live order routing yet.
- No autonomous enablement logic.

Validation commands:
- cd apps/api
- pytest tests/unit -v

Stop-for-review:
Stop and report files changed, commands run, validation results, and ADR status.

---

## Prompt 9.2 - Live Account Registration and Readiness

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/PROJECT_STATUS.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Implement live account registration lifecycle with eligibility prerequisites.
- Add readiness states (draft, pending_approval, approved, enabled, suspended).
- Enforce explicit paper-default behavior until human approval is recorded.

Explicit exclusions:
- No order submission.
- No auto-approval.

Validation commands:
- cd apps/api
- pytest tests/unit/services -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.3 - Human Approval Workflow

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/PROJECT_CONSTITUTION.md
- docs/RISK_ENGINE.md
- docs/DECISION_INTELLIGENCE_ENGINE.md

Exact scope:
- Implement approval checkpoints for first live enablement and material control changes.
- Capture approver identity, rationale, scope, and expiry/renewal conditions.
- Integrate approval revocation/suspension pathways.

Explicit exclusions:
- No autonomous escalation.
- No bypass path around approval checks.

Validation commands:
- cd apps/api
- pytest tests/api tests/unit -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.4 - Broker Adapter Abstraction

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/RISK_ENGINE.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Define provider-agnostic broker adapter interfaces.
- Normalize broker order/fill/reject/error payloads and status mapping.
- Add idempotency and correlation-id contracts for request/response lifecycle.

Explicit exclusions:
- No direct adapter calls from UI.
- No risk-gate bypass routes.

Validation commands:
- cd apps/api
- pytest tests/unit/adapters -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.5 - Live Execution Orchestration and Risk Gate

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/SYSTEM_ARCHITECTURE.md
- docs/PROJECT_CONSTITUTION.md

Exact scope:
- Implement live execution orchestration from approved signal to broker submit.
- Enforce mandatory Risk Engine check for every live candidate action.
- Persist deterministic execution intent and submission provenance.

Explicit exclusions:
- No autonomous risk policy mutation.
- No alternate gate in parallel with Risk Engine.

Validation commands:
- cd apps/api
- pytest tests/services tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.6 - Live Accounting and Reconciliation

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/MVP_BUILD_PLAN.md
- docs/PROJECT_STATUS.md

Exact scope:
- Implement live portfolio accounting updates tied to broker execution events.
- Implement order reconciliation and fill reconciliation against broker state.
- Handle partial fills, cancel/replace flows, and fee attribution coherently.

Explicit exclusions:
- No hidden auto-corrections without audit trace.
- No destructive history rewrites.

Validation commands:
- cd apps/api
- pytest tests/integration tests/unit/services -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.7 - Slippage and Execution Quality Tracking

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/RISK_ENGINE.md
- docs/SYSTEM_ARCHITECTURE.md

Exact scope:
- Implement expected-vs-actual execution price capture.
- Persist slippage metrics by asset, venue, and market condition context.
- Expose execution-quality read models for operations and review.

Explicit exclusions:
- No auto-adjustment of risk/strategy parameters.
- No automatic mode switching between live and paper.

Validation commands:
- cd apps/api
- pytest tests/unit tests/api -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.8 - Kill Switch, Emergency Stop, Outage Recovery

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/PROJECT_CONSTITUTION.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Extend and validate kill-switch behavior for live mode operations.
- Implement broker outage detection and fail-closed submission behavior.
- Implement controlled recovery procedures with explicit re-approval requirements.

Explicit exclusions:
- No automatic re-arm after emergency stop.
- No silent retry loops that can duplicate live orders.

Validation commands:
- cd apps/api
- pytest tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.9 - Real-Money Audit and Compliance Surfaces

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/PROJECT_CONSTITUTION.md
- docs/DECISION_INTELLIGENCE_ENGINE.md
- docs/SYSTEM_ARCHITECTURE.md

Exact scope:
- Implement immutable, attributable audit records for all live trading operations.
- Add compliance-focused retrieval/export surfaces for order lifecycle and operator actions.
- Ensure record retention and provenance integrity across incident/recovery flows.

Explicit exclusions:
- No mutable audit history paths.
- No hidden operator actions outside audited APIs.

Validation commands:
- cd apps/api
- pytest tests/api tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 9.10 - Live Trading API/UI and Phase 9 Exit Gate

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/PROJECT_STATUS.md
- docs/MVP_BUILD_PLAN.md

Exact scope:
- Deliver controlled API and UI surfaces for live registration, approvals, operational status, and reconciliation views.
- Ensure fail-visible unknown/unavailable states and explicit operator warnings.
- Execute Phase 9 validation checklist and publish exit-gate evidence.

Explicit exclusions:
- No feature paths that submit orders directly from UI without backend controls.
- No autonomous live enablement.

Validation commands:
- cd apps/api && pytest -v
- cd apps/web && pnpm test
- cd apps/web && pnpm lint

Stop-for-review:
Stop and report files changed, commands run, validation results, ADR status, and final go/no-go recommendation.
