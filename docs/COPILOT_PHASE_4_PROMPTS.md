# OmniTrade Legacy Engine - GitHub Copilot Prompts: Phase 4 Only

Scope: Strategy Lab only.

Phase 4 implements the Strategy Lab user experience and supporting API and backend wiring needed for parameter workflows and backtest comparison.

Do not implement:
- Paper Trading
- AI Layer
- Risk Engine
- Decision Intelligence Engine
- Live trading
- Automated strategy promotion
- Destructive history deletion

Run these prompts in order, one at a time.
Each prompt is intentionally small, scoped, and testable.
Stop after each prompt for review, validation, and commit.

Before every prompt, check whether the implementation creates or changes an architectural decision. If it does, stop and ask whether an ADR is required before writing code.

---

## Prompt 4.1 - Strategy Lab Route and Base Layout

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/PROJECT_STATUS.md
- docs/PROJECT_CONSTITUTION.md
- docs/UI_SPEC.md (Strategy Lab section)
- docs/FRONTEND_PAGE_SPECS.md (/strategy-lab section)
- docs/MVP_BUILD_PLAN.md (Phase 4)

Implement only:
- Add Strategy Lab page route and base layout shell in apps/web.
- Render empty/loading/error states per docs.
- No parameter editing logic yet.

Requirements:
- Mobile-first responsive layout.
- Preserve existing app shell/navigation conventions.
- No backend or schema changes.

Validation:
- Add/adjust frontend tests for page render and states.
- Run web tests.

Report:
- Files changed
- Commands run
- Test results
- ADR status

---

## Prompt 4.2 - Strategy Selection and List Data Wiring

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/UI_SPEC.md (Strategy Lab)
- docs/FRONTEND_PAGE_SPECS.md (/strategy-lab)
- docs/API_CONTRACTS.md (GET /strategies)
- docs/BACKEND_MODULE_SPECS.md (routes/services boundaries)

Implement only:
- Fetch and display strategies from GET /strategies.
- Strategy list with active/inactive display.
- Selection state for chosen strategy.

Requirements:
- No activation or mutation behavior yet.
- Handle loading, empty, and error states clearly.
- Keep UI readable at $25-small-account context.

Validation:
- Frontend tests for list rendering, selection, and empty state.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.3 - Parameter Schema Adapter (Frontend)

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/STRATEGY_ENGINE.md (default params)
- docs/API_CONTRACTS.md (strategy/default_params shape)
- docs/FRONTEND_PAGE_SPECS.md (generated parameter form expectations)

Implement only:
- Frontend schema adapter that converts strategy default_params into typed editable fields.
- Support numeric and enum-like values where inferable from existing data.

Requirements:
- Do not invent backend schema.
- Use existing response data only.
- Keep adapter unit-testable.

Validation:
- Unit tests for schema adapter behavior and edge cases.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.4 - Generated Parameter Editor UI

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/UI_SPEC.md (Strategy Lab parameter editor)
- docs/FRONTEND_PAGE_SPECS.md (/strategy-lab ParameterForm)
- docs/SMALL_ACCOUNT_MODE.md (clarity and scale expectations)

Implement only:
- Render generated parameter inputs from Prompt 4.3 adapter.
- Show default values and editable values.
- Add field-level helper text for clarity.

Requirements:
- Mobile-first layout.
- Keep visual language consistent with current web app.
- No save/mutation yet.

Validation:
- Frontend tests for generated fields and controlled input state.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.5 - Parameter Validation Rules and UX

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/STRATEGY_ENGINE.md
- docs/API_CONTRACTS.md
- docs/FRONTEND_PAGE_SPECS.md

Implement only:
- Client-side parameter validation (required fields, numeric sanity, range ordering where relevant).
- Inline validation messages and submit guards.

Requirements:
- Validation must be deterministic and testable.
- Do not enforce undocumented constraints.

Validation:
- Unit/component tests for valid/invalid paths.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.6 - Save Parameter Sets (Backend + Frontend Contracted Path)

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/API_CONTRACTS.md (existing and implied parameter set flows)
- docs/BACKEND_MODULE_SPECS.md
- docs/FRONTEND_PAGE_SPECS.md (save parameter set behavior)

Implement only:
- Minimal contracted path to save parameter sets for a strategy.
- Wire frontend action to persist a new named parameter set.
- Return and surface saved set in Strategy Lab.

Requirements:
- Keep route/service layering clean.
- Do not add strategy auto-activation.
- Do not add destructive actions.

Validation:
- Backend tests for endpoint/service behavior.
- Frontend tests for save flow and error handling.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.7 - Run Backtest from Strategy Lab

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/UI_SPEC.md (Strategy Lab Run Backtest action)
- docs/FRONTEND_PAGE_SPECS.md (RunBacktestButton behavior)
- docs/API_CONTRACTS.md (POST /backtests/run)
- docs/SMALL_ACCOUNT_MODE.md (starting capital clarity)

Implement only:
- From Strategy Lab, trigger run backtest using selected strategy and parameter set.
- Navigate to or open Backtests context with prefilled run details.

Requirements:
- Use existing backtest API contracts.
- Preserve minimum starting capital handling.

Validation:
- Frontend integration tests for launch flow.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.8 - Compare Backtest Results Table View

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/UI_SPEC.md (comparison mode)
- docs/FRONTEND_PAGE_SPECS.md (BacktestCompareView behavior)
- docs/SMALL_ACCOUNT_MODE.md (dollar plus percent display)

Implement only:
- Side-by-side comparison table for selected backtest results.
- Show core metrics with both dollar and percentage where applicable.

Requirements:
- No new chart library.
- Handle different starting capitals clearly.
- Read-only comparison only.

Validation:
- Frontend tests for compare selection and rendered metric rows.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.9 - Comparison Chart Scaffold and Saved Presets UX

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/UI_SPEC.md (comparison chart expectation)
- docs/FRONTEND_PAGE_SPECS.md

Implement only:
- Comparison chart scaffold using existing chart approach (no new chart stack).
- Saved presets list and quick-apply behavior for Strategy Lab parameter sets.

Requirements:
- If data missing, show explicit explanatory empty state.
- Keep interactions non-destructive.

Validation:
- Frontend tests for preset selection and chart scaffold state behavior.

Report files changed, commands, tests, ADR status.

---

## Prompt 4.10 - Glossary/Tooltips Integration and Phase 4 Validation

ADR check (required before coding):
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Read:
- docs/GLOSSARY.md
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/VALIDATION_CHECKLIST.md

Implement only:
- Integrate beginner-friendly glossary/tooltips into Strategy Lab and backtest comparison surfaces.
- Ensure key metrics and terms expose clear definitions in-context.
- Run full Phase 4 validation and update status documentation when validation passes.

Requirements:
- No backend contract drift.
- No Paper Trading, AI, Risk Engine, or Decision Intelligence implementation.

Validation:
- Frontend tests for tooltip rendering and accessibility basics.
- Backend tests for any Phase 4 API touched.
- Manual validation checklist items for Strategy Lab end-to-end flow.

Report:
- Files changed
- Commands run
- Test results
- Validation outcomes
- ADR status

---

## Phase 4 Sequence Summary

1. Build Strategy Lab page shell.
2. Add strategy list and selection.
3. Build schema adapter for parameters.
4. Render generated parameter editor.
5. Add validation UX.
6. Save parameter sets.
7. Trigger backtests from Strategy Lab.
8. Add comparison table.
9. Add comparison chart scaffold and presets UX.
10. Integrate glossary/tooltips and complete Phase 4 validation.
