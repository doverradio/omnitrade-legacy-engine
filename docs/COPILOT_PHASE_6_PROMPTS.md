# OmniTrade Decision Intelligence Platform - GitHub Copilot Prompts: Phase 6

## Status
Completed

## Phase Name
Risk Engine

## Purpose
Phase 6 implements the Risk Engine as the mandatory safety gate for every paper-execution decision.

This phase hardens capital-preservation behavior before any Decision Intelligence Foundation or Decision Arena implementation work proceeds.

## ADR Check
Before starting any prompt below, perform the ADR check from docs/adr/README.md.

Expected outcome for normal prompt execution in this file:
- No new ADR is required if implementation stays within existing architecture and accepted ADR boundaries.
- If a prompt implies changes to foundational-engine boundaries, safety guarantees, or risk-authority relationships, stop and request ADR guidance before writing code.

## Scope Guardrails (Non-Negotiable)
Do implement in Phase 6:
- Risk rules and rule-evaluation ordering.
- Risk events and audit-grade traceability.
- Position sizing and minimum-viable-order protections.
- Daily-loss and drawdown protections.
- Cooldown logic and no-trade zones.
- Account/global kill-switch controls and re-arm behavior.
- Risk Monitor API and UI surfaces.
- Tests and phase validation.

Do not implement in Phase 6:
- Decision Intelligence implementation.
- Decision Arena implementation.
- Capital Allocation Engine implementation.
- Future Agents.
- Live trading.
- Broker architecture redesign.

## Architectural Alignment Rules
- Preserve the four permanent foundational engines.
- Risk Engine remains a cross-cutting gatekeeper, not a foundational engine.
- Keep paper-only guarantees intact.
- Keep dependency direction clean: orchestration calls risk; risk does not depend on orchestration.

## Small Account Mode Rules (Apply To Every Prompt)
Every prompt in this pack must explicitly honor:
- $25 proving-ground assumptions.
- Percentage-based sizing rules.
- Minimum viable position checks using exchange/broker constraints.
- Explicit, explainable rejection reasons.

## Explainability and Audit Rules
- Every risk decision must be explainable and traceable.
- Every state-changing risk action must write auditable records.
- No silent risk-state mutation is allowed.

## Execution Rule
Run prompts in order. Complete one prompt at a time. Stop after each prompt for review.

---

## Prompt 6.1 - Risk Engine Skeleton and Evaluation Contract

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/PROJECT_CONSTITUTION.md
- docs/RISK_ENGINE.md
- docs/BACKEND_MODULE_SPECS.md
- docs/SYSTEM_ARCHITECTURE.md

Exact scope:
- Scaffold Risk Engine service structure aligned to module specs and dependency direction.
- Define explicit risk-decision contract shape (approve, resize, reject) and reason-code conventions.
- Implement evaluation-order entrypoint with no rule logic beyond deterministic flow scaffolding.

Explicit exclusions:
- No live routing.
- No Decision Intelligence or Decision Arena behavior.
- No UI implementation.

Validation commands:
- cd apps/api
- pytest tests/unit -v

Stop-for-review:
Stop and report files changed, commands run, validation results, and ADR status before continuing.

---

## Prompt 6.2 - Position Sizing and Minimum Viable Order Rule

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/DATABASE_SCHEMA.md
- docs/API_CONTRACTS.md

Exact scope:
- Implement max-position-size rule based on percentage of current equity.
- Implement minimum-viable-order rule using min_order_notional and qty_step_size.
- Ensure explicit rejection reason for below-minimum outcomes.

Explicit exclusions:
- No cooldown, drawdown, or kill-switch logic yet.
- No UI changes.

Validation commands:
- cd apps/api
- pytest tests/unit/services/risk -v
- pytest tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.3 - Daily Loss and Drawdown Protections

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/VALIDATION_CHECKLIST.md

Exact scope:
- Implement daily-loss threshold evaluation and trading-paused behavior.
- Implement drawdown threshold evaluation using high-water-mark logic.
- Ensure deterministic pause-state semantics for both controls.

Explicit exclusions:
- No kill-switch controls yet.
- No Risk Monitor UI edits.

Validation commands:
- cd apps/api
- pytest tests/unit/services/risk -v
- pytest tests/api -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.4 - Cooldown Logic and No-Trade Zones

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/DATA_SOURCES.md
- docs/RISK_AND_AUDIT_API_CONTRACTS.md

Exact scope:
- Implement strategy/asset cooldown rule after configured loss streak conditions.
- Implement time-based and data-quality no-trade zone checks.
- Ensure rule-order compatibility with prior prompts.

Explicit exclusions:
- No kill-switch endpoints yet.
- No Decision Intelligence implementation.

Validation commands:
- cd apps/api
- pytest tests/unit/services/risk -v
- pytest tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.5 - Kill Switches and Re-Arm Controls

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_ENGINE.md
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/SECURITY_AND_SAFETY.md

Exact scope:
- Implement account-level and global kill-switch domain behavior.
- Implement explicit human re-arm path and enforcement.
- Ensure fail-closed behavior when kill-switch state is uncertain.

Explicit exclusions:
- No live-trading controls.
- No UI implementation yet.

Validation commands:
- cd apps/api
- pytest tests/api -v
- pytest tests/unit/services/risk -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.6 - Risk Events Persistence and Audit Integration

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/DATABASE_SCHEMA.md
- docs/PROJECT_CONSTITUTION.md

Exact scope:
- Persist risk-event records for approvals, resizes, and rejections with reason details.
- Ensure required audit-log writes for all state-changing risk operations.
- Add explicit transaction safety where risk state and audit/risk events must commit together.

Explicit exclusions:
- No frontend work.
- No Decision Intelligence schema/endpoints.

Validation commands:
- cd apps/api
- pytest tests/api -v
- pytest tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.7 - Orchestration Integration (Risk Gate Before Execution)

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/BACKEND_MODULE_SPECS.md
- docs/RISK_ENGINE.md

Exact scope:
- Integrate Risk Engine into signal-to-execution orchestration as mandatory gate.
- Enforce short-circuit behavior on risk rejection and deterministic status transitions.
- Ensure paper-only execution remains intact and no bypass path exists.

Policy clarification (applies to this prompt and downstream verification):
- Duplicate/idempotent execution detection is a pre-risk safety guard in orchestration.
- A duplicate request is not a new execution attempt and does not require a second risk evaluation.
- Duplicate requests must not submit trades and must not create duplicate risk-event records.
- Duplicate outcomes must remain auditable.

Explicit exclusions:
- No Decision Intelligence runtime work.
- No Risk Monitor UI in this prompt.

Validation commands:
- cd apps/api
- pytest tests/api tests/integration -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.8 - Risk Monitor API Surface

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/API_CONTRACTS.md
- docs/FRONTEND_PAGE_SPECS.md

Exact scope:
- Implement Risk Monitor endpoints: status, kill-switch enable/disable, rules get/patch.
- Align request/response/error behavior to documented contracts.
- Ensure endpoint-level audit semantics are complete.

Explicit exclusions:
- No frontend implementation yet.
- No scope expansion to live controls.

Validation commands:
- cd apps/api
- pytest tests/api -v
- pytest tests/unit -v

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.9 - Risk Monitor UI

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md

Exact scope:
- Implement/upgrade Risk Monitor UI with status strip, limit usage cards, and kill-switch controls.
- Add risk-rules editor UX with explicit loosening confirmations.
- Implement loading, empty, partial-failure, and fail-visible status-unknown behavior.

Explicit exclusions:
- No Decision Intelligence pages.
- No Decision Arena workflows.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 6.10 - Phase 6 Validation and Exit Gate

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/VALIDATION_CHECKLIST.md
- docs/PROJECT_STATUS.md
- docs/MVP_BUILD_PLAN.md
- docs/MASTER_PRODUCT_ROADMAP.md

Exact scope:
- Execute full Phase 6 validation checklist with evidence capture.
- Verify no prohibited-scope work landed (Decision Intelligence, Decision Arena, Capital Allocation, Live Trading).
- Synchronize documentation for Phase 6 completion status when validation passes.

Explicit exclusions:
- Do not start Phase 7 work.
- Do not implement Decision Intelligence Foundation in this prompt.

Validation commands:
- cd apps/api
- pytest -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and provide final Phase 6 completion report, ADR status, validation outcomes, and recommendation for Phase 7 planning.

---

## Completion Criteria For This Prompt Pack
Phase 6 is considered complete only when:
- All Prompt 6.1 through Prompt 6.10 scopes are completed and reviewed.
- Risk decisions are deterministic, auditable, and enforced before execution.
- Risk Monitor API and UI behavior matches documented contracts.
- Validation commands pass (or environment constraints are explicitly documented with evidence).
- No architectural drift from four-core-engine model is introduced.
- No prohibited scope (Decision Intelligence implementation, Decision Arena implementation, Capital Allocation implementation, live trading) was added.
