# OmniTrade Decision Intelligence Platform - GitHub Copilot Prompts: Phase 5

## Status
Planned

## Phase Name
Portfolio Intelligence + Paper Execution Foundation

## Purpose
Phase 5 is the platform's proving-ground phase for safe strategy validation against paper capital while establishing the Portfolio Intelligence base required by future Decision Arena and Decision Intelligence work.

This phase is not "fake trading." It is controlled, auditable paper execution designed to validate behavior before any real capital pathway is considered.

## ADR Check
Before starting any prompt below, perform the ADR check from docs/adr/README.md.

Expected outcome for normal prompt execution in this file:
- No new ADR is required if implementation stays within existing architectural boundaries.
- If any prompt implementation attempts to change core-engine boundaries, promote Capital Allocation Engine to a standalone engine, alter API/schema architecture contracts, or redesign broker architecture, stop and request ADR guidance before writing code.

## Scope Guardrails (Non-Negotiable)
Do implement in Phase 5:
- Portfolio Intelligence + Paper Execution Foundation capabilities only.
- Paper account lifecycle, paper execution, portfolio accounting/performance surfaces, and auditable orchestration.

Do not implement in Phase 5:
- Live trading.
- Risk Engine implementation.
- Decision Intelligence implementation.
- Decision Arena implementation.
- AI Layer implementation.
- Capital Allocation Engine implementation.
- Automated strategy evolution.
- Portfolio rebalancing.
- Broker abstraction redesign.

## Architectural Alignment Rules
- Preserve the four permanent foundational engines (Market Intelligence, Strategy Evolution, Portfolio Intelligence, Decision Intelligence).
- Treat Live Trading as future deployment mode, never as a foundational engine.
- Treat Capital Allocation Engine as a Portfolio Intelligence subsystem, never a standalone engine.
- Preserve existing schema and API contracts unless a prompt explicitly says to use already-documented endpoints.

## Small Account Mode Rules (Apply To Every Prompt)
Every prompt in this pack must explicitly honor:
- $25 default proving ground.
- Fractional crypto support.
- Fractional stock support where supported by broker.
- Percentage-based sizing assumptions.
- Clear paper labeling.
- Fee visibility.
- Dollar + percentage reporting together where applicable.

## Decision Intelligence Preparation Rule (Apply Where Relevant)
Without implementing Decision Intelligence Engine functionality, Phase 5 work should keep data consistency intact for future Decision Intelligence ingestion and analysis:
- signals
- model_outputs
- risk_events
- trades

No Decision Intelligence endpoints, schemas, or runtime logic are implemented in this phase.

## Execution Rule
Run prompts in order. Complete one prompt at a time. Stop after each prompt for review.

---

## Prompt 5.1 - Portfolio Intelligence Shell and Navigation

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/PROJECT_STATUS.md
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md

Exact scope:
- Establish/upgrade Portfolio Intelligence page shell and navigation framing for Phase 5.
- Ensure all user-facing language frames this phase as Portfolio Intelligence + Paper Execution Foundation.
- Add clear paper labels and small-account-aware placeholder language.
- Add loading, empty, and error states for shell sections.

Explicit exclusions:
- No execution logic yet.
- No schema changes.
- No API contract changes.
- No Risk Engine, AI Layer, Decision Arena, Decision Intelligence, or live trading.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report files changed, commands run, validation results, and ADR status before continuing.

---

## Prompt 5.2 - Paper Account Management

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/API_CONTRACTS.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/DATABASE_SCHEMA.md

Exact scope:
- Implement paper account management UI/API usage for creation/select/display/reset using existing contracts.
- Enforce $25 default proving-ground behavior in UX copy and validation handling.
- Ensure paper balance labeling remains explicit and unambiguous.
- Support account state rendering that is compatible with fractional quantities and decimal-safe values.

Explicit exclusions:
- No live account concepts.
- No risk policy implementation.
- No capital allocation implementation.
- No API/schema redesign.

Validation commands:
- cd apps/api
- pytest tests/api -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.3 - Portfolio Accounting Foundations

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/DATABASE_SCHEMA.md
- docs/API_CONTRACTS.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/FRONTEND_PAGE_SPECS.md

Exact scope:
- Implement Portfolio Intelligence accounting foundations for paper accounts (cash/equity/position-value rollups) using existing data model contracts.
- Ensure every P&L/performance output surfaces dollar + percentage together where applicable.
- Ensure accounting outputs remain paper-labeled and suitable for $25-scale usage.

Explicit exclusions:
- No performance optimization redesign.
- No risk-gating implementation.
- No rebalancing logic.
- No Capital Allocation Engine behavior.

Validation commands:
- cd apps/api
- pytest tests/unit tests/integration -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.4 - Internal Crypto Paper Execution Simulator

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/API_CONTRACTS.md
- docs/DATABASE_SCHEMA.md
- docs/SMALL_ACCOUNT_MODE.md

Exact scope:
- Implement internal crypto paper execution simulation path per existing architecture.
- Support fractional crypto quantity handling with precision-safe behavior.
- Ensure fee/slippage visibility and capture in paper execution records.
- Ensure execution outcomes remain auditable and clearly marked paper.

Explicit exclusions:
- No live crypto execution.
- No broker abstraction redesign.
- No Risk Engine implementation.
- No AI-initiated execution changes.

Validation commands:
- cd apps/api
- pytest tests/services tests/integration -v
- cd ../web
- pnpm test

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.5 - Alpaca Paper Execution Integration

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/API_CONTRACTS.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/SECURITY_AND_SAFETY.md

Exact scope:
- Implement Alpaca paper execution integration path only (paper endpoint scope).
- Preserve fractional-stock support assumptions where broker supports it.
- Ensure robust error handling and explicit paper execution labeling.
- Keep integration behavior aligned with existing contract and logging standards.

Explicit exclusions:
- No live Alpaca orders.
- No credential-in-UI pattern changes.
- No contract/schema redesign.
- No broker abstraction rewrite.

Validation commands:
- cd apps/api
- pytest tests/services tests/api -v
- cd ../web
- pnpm test

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.6 - Signal Execution Orchestration (Paper Only)

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SYSTEM_ARCHITECTURE.md
- docs/API_CONTRACTS.md
- docs/RISK_AND_AUDIT_API_CONTRACTS.md
- docs/DATABASE_SCHEMA.md
- docs/HANDOFF_TO_COPILOT.md

Exact scope:
- Implement paper-only signal-to-execution orchestration using existing phase boundaries.
- Ensure deterministic status transitions and durable audit-friendly write patterns.
- Keep future Decision Intelligence preparation in mind by maintaining consistent writes for signals/model_outputs/risk_events/trades where applicable.
- Keep orchestration extension-ready for future Decision Arena/Decision Intelligence/Capital Allocation consumers without implementing them.

Explicit exclusions:
- No Risk Engine implementation logic.
- No AI Layer implementation logic.
- No Decision Arena or Decision Intelligence runtime features.
- No live routing.

Validation commands:
- cd apps/api
- pytest tests/api tests/integration -v
- cd ../web
- pnpm test

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.7 - Trade History + Portfolio Timeline

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/API_CONTRACTS.md
- docs/SMALL_ACCOUNT_MODE.md

Exact scope:
- Implement paper trade history and portfolio timeline views.
- Ensure table/detail surfaces are explainability-friendly and paper-labeled.
- Show fee impact clearly and maintain dollar + percentage reporting where required.
- Preserve robust loading/empty/error behavior for low-data and small-account scenarios.

Explicit exclusions:
- No Decision Arena comparison workflows.
- No Decision Intelligence Explorer pages.
- No live-trading timeline paths.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.8 - Performance Analytics (Portfolio Intelligence)

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/PROJECT_STATUS.md

Exact scope:
- Implement Portfolio Intelligence performance analytics views for paper accounts.
- Include return, drawdown, fee-drag visibility, and consistency-oriented analytics surfaces appropriate for paper validation.
- Keep analytics explanatory and beginner-readable while preserving advanced detail via progressive disclosure.

Explicit exclusions:
- No prediction/recommendation engine.
- No AI Layer implementation.
- No Capital Allocation Engine behavior.
- No Risk Engine policy changes.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.9 - Small Account Validation Hardening

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SMALL_ACCOUNT_MODE.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/API_CONTRACTS.md
- docs/DATABASE_SCHEMA.md

Exact scope:
- Validate and harden all Phase 5 surfaces against Small Account Mode requirements.
- Verify $25 defaults, fractional support behavior, fee visibility, and paper labeling across relevant workflows.
- Add/expand tests for dollar + percentage reporting and small-account warning behavior where applicable.

Explicit exclusions:
- No new architectural features.
- No live-trading capabilities.
- No Risk/AI/DIE/Decision Arena implementation.

Validation commands:
- cd apps/api
- pytest -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 5.10 - Phase 5 Validation and Completion Gate

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/PROJECT_STATUS.md
- docs/MVP_BUILD_PLAN.md
- docs/MASTER_PRODUCT_ROADMAP.md
- docs/VALIDATION_CHECKLIST.md
- docs/HANDOFF_TO_COPILOT.md

Exact scope:
- Execute full Phase 5 validation and completion checklist.
- Confirm architecture boundaries remained intact throughout Phase 5 implementation.
- Confirm extension points exist for future Decision Intelligence, Decision Arena, Capital Allocation Engine, and future live trading mode, without implementing them.
- Produce final Phase 5 readiness summary for handoff into Phase 6 planning.

Explicit exclusions:
- No Phase 6 implementation.
- No prompt execution beyond Phase 5 closure.
- No ADR edits unless prompted by detected boundary change.

Validation commands:
- cd apps/api
- pytest -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and provide final Phase 5 completion report, ADR status, and recommendation for next-phase prompt planning.

---

## Completion Criteria For This Prompt Pack
Phase 5 is considered complete only when:
- All Prompt 5.1 through Prompt 5.10 scopes are completed and reviewed.
- Validation commands pass (or known environment issues are explicitly documented with evidence).
- Documentation and PROJECT_STATUS are updated for phase closure.
- No architectural drift from four-core-engine model is introduced.
- No prohibited scope (live trading, Risk Engine implementation, Decision Intelligence implementation, Decision Arena implementation, AI Layer implementation, Capital Allocation implementation) was added.
