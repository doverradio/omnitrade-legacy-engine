# OmniTrade Legacy Engine - GitHub Copilot Prompts: Phase 4 (V2)

Status: Supersedes docs/COPILOT_PHASE_4_PROMPTS.md for Phase 4 execution planning.

Scope: Phase 4 only - Strategy Lab / Research Workspace.

Design principle:
A first-time user with $25 and no trading experience should understand what they are seeing within 30 seconds.

No ADR required.

## Phase 4 Design Principles

1. Beginner First

A user with $25 and no trading experience should understand the screen within 30 seconds.

2. Mobile First

Design every new component for approximately a 390px-wide viewport before desktop enhancements.

3. Explain Before Optimize

Always explain what happened and why before presenting advanced metrics.

4. Deterministic UX

Never fabricate data.

Estimated values must always be clearly labeled.

5. Progressive Disclosure

Simple information first.

Advanced information should appear only when requested.

6. Accessibility

Support keyboard navigation, ARIA labels, readable contrast, and responsive layouts.

7. Reuse Before Rebuild

Parameter-driven UI should be generated from the Parameter Definition System whenever possible.

Do not implement:
- Paper Trading
- AI Layer
- Risk Engine
- Decision Intelligence Engine
- Live trading
- Automated strategy promotion
- Destructive history deletion

Execution rules for this prompt pack:
- Execute prompts in order, one at a time.
- Keep each prompt small and testable.
- Stop after each prompt for review, validation, and commit.
- Before coding each prompt, perform ADR check exactly as written.

---

## Prompt 4.1 - Mobile-First Strategy Lab / Research Workspace Shell + Beginner Mode Frame

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/PROJECT_STATUS.md
- docs/PROJECT_CONSTITUTION.md
- docs/UI_SPEC.md (Strategy Lab)
- docs/FRONTEND_PAGE_SPECS.md (/strategy-lab)
- docs/SMALL_ACCOUNT_MODE.md
- docs/GLOSSARY.md

Exact scope:
- Create/upgrade Strategy Lab page structure as a mobile-first research workspace.
- Add beginner-first information architecture: clear section headings, short explanatory intro, and explicit balance labeling language.
- Add Beginner Mode frame/toggle scaffold (UI state + copy only) that simplifies language and surfaces key helper text.
- Include loading, empty, and error states.

Explicit exclusions:
- No parameter editing logic yet.
- No data mutation flows.
- No backend contract or schema changes.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report files changed, commands run, test/lint results, and ADR status before any next prompt.

---

## Prompt 4.2 - Strategy Selection + Beginner-Friendly Strategy Context

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md (Strategy Lab)
- docs/FRONTEND_PAGE_SPECS.md (/strategy-lab)
- docs/API_CONTRACTS.md (GET /strategies)
- docs/STRATEGY_ENGINE.md (strategy definitions)
- docs/GLOSSARY.md

Exact scope:
- Fetch/display strategy list from GET /strategies.
- Support robust selection state and selected-strategy detail panel.
- In Beginner Mode, show plain-English summary for selected strategy using glossary-aligned language.
- Preserve clear active/inactive metadata display without enabling activation actions.

Explicit exclusions:
- No activation/deactivation mutations.
- No parameter save/run actions yet.
- No backend changes.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status before continuing.

---

## Prompt 4.3 - Parameter Definition System (Schema + Types + Mapping Rules)

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/STRATEGY_ENGINE.md
- docs/API_CONTRACTS.md (strategies/default_params + parameter sets)
- docs/FRONTEND_PAGE_SPECS.md
- docs/GLOSSARY.md

Exact scope:
- Implement a Parameter Definition System that converts strategy defaults into typed parameter definitions.
- Include field metadata model for labels, descriptions, type, constraints, default value, and display hints.
- Add deterministic fallback behavior when inferred metadata is limited.
- Keep definitions reusable by editor, validation, health panel, and explainability panel.

Explicit exclusions:
- No live form rendering yet.
- No persistence or API mutation.
- Do not invent backend schema fields.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report outputs and ADR status.

---

## Prompt 4.4 - Generated Parameter Editor (Mobile-First) + Live Validation

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/GLOSSARY.md

Exact scope:
- Render generated parameter editor from Parameter Definition System.
- Add live validation (field-level + form-level), with instant but non-disruptive feedback.
- Ensure mobile-first layout and readability of controls/help text.
- In Beginner Mode, show concise “what this changes” helper text for each major parameter.

Explicit exclusions:
- No save parameter set yet.
- No run backtest yet.
- No backend changes.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 4.5 - Configuration Health Panel + Explainability Panel (Configuration-Level)

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/SMALL_ACCOUNT_MODE.md
- docs/STRATEGY_ENGINE.md
- docs/GLOSSARY.md
- docs/FRONTEND_PAGE_SPECS.md

Exact scope:
- Add Configuration Health panel that summarizes parameter validity/readiness (clear, warning, error states).
- Add Explainability Panel focused on “why this configuration is considered healthy/risky” using deterministic rules and plain language.
- In Beginner Mode, show top 3 key takeaways for quick scanning.

Explicit exclusions:
- No AI-generated explanations.
- No Risk Engine logic.
- No backend mutation.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report outcomes and ADR status.

---

## Prompt 4.6 - Save Parameter Sets Flow + Saved Presets UX Foundations

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/API_CONTRACTS.md (GET /parameter-sets and strategy contracts)
- docs/FRONTEND_PAGE_SPECS.md
- docs/STRATEGY_ENGINE.md (parameter lifecycle)
- docs/BACKEND_MODULE_SPECS.md

Exact scope:
- Implement contracted save parameter set flow.
- Add naming and duplicate-name handling UX.
- Add saved presets list foundations (list, select, apply, view metadata).
- Keep interactions non-destructive and auditable-friendly.

Explicit exclusions:
- No automated strategy promotion.
- No deletion of presets/history.
- No non-contracted API drift.

Validation commands:
- cd apps/api
- pytest tests/api -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report backend/frontend results and ADR status.

---

## Prompt 4.7 - Run Backtests from Strategy Lab (Prefill + Navigation Flow)

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/API_CONTRACTS.md (POST /backtests/run)
- docs/SMALL_ACCOUNT_MODE.md
- docs/GLOSSARY.md

Exact scope:
- Trigger backtest run from Strategy Lab using selected strategy + parameter set.
- Prefill backtest context (strategy, parameter set, clear starting capital labeling).
- In Beginner Mode, show plain-language pre-run checklist before submit.

Explicit exclusions:
- No new backtest backend contracts.
- No Paper Trading hooks.
- No destructive actions.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report outcomes and ADR status.

---

## Prompt 4.8 - Backtest Comparison Table + Explainability Linkage

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md (comparison expectations)
- docs/FRONTEND_PAGE_SPECS.md (/backtests compare)
- docs/SMALL_ACCOUNT_MODE.md (dollar + percent conventions)
- docs/GLOSSARY.md

Exact scope:
- Implement side-by-side backtest comparison table for selected runs.
- Ensure each return/performance row uses beginner-clear labels and both dollar/percent where applicable.
- Link rows to explainability text snippets (non-AI) that clarify what each metric means.

Explicit exclusions:
- No new chart library.
- No decision engine features.
- No destructive history actions.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report results and ADR status.

---

## Prompt 4.8A (Optional) - Comparison Data Normalization + Empty-State Hardening

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md
- docs/GLOSSARY.md

Exact scope:
- Add optional normalization/view helpers for clearer comparison across different starting capitals.
- Harden comparison empty/error states and mismatch handling.
- Preserve strict transparency about estimated vs unavailable values.

Explicit exclusions:
- No backend contract changes.
- No chart implementation changes beyond data prep/presentation hardening.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report outcomes and ADR status before moving to 4.9.

---

## Prompt 4.9 - Comparison Chart Scaffold + Saved Presets UX Completion

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/SMALL_ACCOUNT_MODE.md

Exact scope:
- Add comparison chart scaffold using existing chart stack/components only.
- Complete saved presets UX: quick apply, current preset indicator, and friendly metadata display.
- Ensure mobile-first behavior and table/chart readability.

Explicit exclusions:
- No new chart libraries.
- No delete actions for history/presets.
- No activation automation.

Validation commands:
- cd apps/web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and report outputs and ADR status.

---

## Prompt 4.10 - Glossary/Tooltips Integration + Full Phase 4 Validation

ADR check:
Before coding, confirm whether this work changes architecture or established boundaries. If yes, stop and request ADR guidance.

Docs to read:
- docs/GLOSSARY.md
- docs/UI_SPEC.md
- docs/FRONTEND_PAGE_SPECS.md
- docs/MVP_BUILD_PLAN.md (Phase 4 exit criteria)
- docs/PROJECT_STATUS.md

Exact scope:
- Integrate glossary/tooltips across Strategy Lab and comparison surfaces.
- Ensure Beginner Mode can explain core concepts quickly and clearly.
- Execute full Phase 4 validation for implemented scope and update documentation only after validation passes.

Explicit exclusions:
- No Paper Trading, AI Layer, Risk Engine, Decision Intelligence, or live trading.
- No destructive deletion behaviors.

Validation commands:
- cd apps/api
- pytest -v
- cd ../web
- pnpm test
- pnpm lint

Stop-for-review:
Stop and provide full validation report, docs updated, ADR status, and readiness recommendation.

---

## Why This V2 Pack Exists

This V2 prompt pack supersedes the original Phase 4 pack by explicitly adding:
- Mobile-first Research Workspace framing
- Beginner Mode across the workflow
- Parameter Definition System as a first-class deliverable
- Live validation + Configuration Health + Explainability panel
- Optional 4.8A split for comparison hardening
- Required per-prompt validation commands and stop-for-review gates
