# HANDOFF_TO_COPILOT.md

## OmniTrade Legacy Engine — First Instruction Block for GitHub Copilot Chat

Paste the block below verbatim into GitHub Copilot Chat (agent mode, with repo context enabled) as your very first message in this repo, **after** all `docs/*.md` files from both documentation rounds are committed to `/docs`.

---

```
You are implementing OmniTrade Legacy Engine, a paper-trading research platform. Before writing any code, read these files in this repo, in this order:

1. docs/PROJECT_VISION.md
2. docs/SYSTEM_ARCHITECTURE.md
3. docs/DATABASE_SCHEMA.md
4. docs/REPO_STRUCTURE.md
5. docs/ENVIRONMENT_SETUP.md
6. docs/API_CONTRACTS.md
7. docs/RISK_AND_AUDIT_API_CONTRACTS.md
8. docs/SMALL_ACCOUNT_MODE.md
9. docs/FRONTEND_PAGE_SPECS.md
10. docs/BACKEND_MODULE_SPECS.md
11. docs/SECURITY_AND_SAFETY.md
12. docs/adr/README.md
13. docs/COPILOT_PHASE_0_PROMPTS.md

Note: docs/RISK_AND_AUDIT_API_CONTRACTS.md is a bridge document — read it alongside docs/API_CONTRACTS.md, not instead of it. Together they define the full API surface (markets/strategies/backtests/signals/paper endpoints in API_CONTRACTS.md; risk, audit, settings, and AI review/explanation endpoints in RISK_AND_AUDIT_API_CONTRACTS.md).

Note: docs/SMALL_ACCOUNT_MODE.md defines a core product requirement — the platform must support paper account and backtest starting balances as low as $25, and this affects the database schema, risk engine rules, API contracts, and UI components you will build. Do not treat it as optional context.

Note: docs/adr/README.md explains the project's Architecture Decision Record system. Every existing ADR in docs/adr/ (ADR-0001 through ADR-0007 as of this writing) records a past architectural decision and its rationale — read the index in docs/adr/README.md so you know what's already been decided and why, and don't propose re-deciding it without flagging that explicitly.

These documents are the source of truth for architecture, schema, folder structure, API contracts, and safety rules. Do not deviate from them without explicitly flagging the conflict to me first and waiting for a decision — do not silently choose your own approach when a doc already specifies one.

Hard rules, non-negotiable, apply to everything you generate in this repo:
- No live real-money trading code path, ever. Alpaca integration is paper-trading-endpoint only. Crypto execution is internal simulation only. See docs/SECURITY_AND_SAFETY.md section 1.
- No real API keys or secrets in any file you create. Only placeholder values in .env.example files. See docs/SECURITY_AND_SAFETY.md section 2.
- Every state-changing action (parameter change, trade, signal, risk event, kill switch action) must write an audit_log row, per docs/DATABASE_SCHEMA.md section 2.12 and docs/SECURITY_AND_SAFETY.md section 7.
- Every AI-layer output must include a non-empty, grounded explanation and write to model_outputs, per docs/AI_LAYER.md.
- Every strategy must be backtested before it can be activated for paper trading, per docs/STRATEGY_ENGINE.md section 3 and docs/MVP_BUILD_PLAN.md Phase 3 exit criteria.
- Follow the exact folder structure in docs/REPO_STRUCTURE.md — do not invent alternate locations for files.
- Follow the exact API request/response shapes in docs/API_CONTRACTS.md and docs/RISK_AND_AUDIT_API_CONTRACTS.md.
- Every kill-switch change (enable or disable) must write an audit_log row in the same transaction, per docs/RISK_AND_AUDIT_API_CONTRACTS.md.
- Every settings change must write an audit_log row, per docs/RISK_AND_AUDIT_API_CONTRACTS.md.
- Every AI explanation response must be tied to a real, existing signal record — never synthesized without a backing signals.id, per docs/RISK_AND_AUDIT_API_CONTRACTS.md.
- Every destructive or state-changing UI action requires a confirmation step, per docs/FRONTEND_PAGE_SPECS.md.
- Paper account and backtest starting balances must support a $25 minimum, enforced at the database, API, and UI layers — per docs/SMALL_ACCOUNT_MODE.md. This is the default proving ground for the platform, not an edge case to handle later.
- OmniTrade's permanent architecture has four core engines — Market Intelligence, Strategy Evolution, Decision Intelligence, and Portfolio Intelligence (docs/PROJECT_VISION.md, docs/SYSTEM_ARCHITECTURE.md §1). The Decision Intelligence Engine and its Counterfactual Outcome Ledger and Decision Quality Engine subsystems (docs/DECISION_INTELLIGENCE_ENGINE.md) are not scheduled for implementation in Phase 0 or Phase 1 — do not build them now. But do not take shortcuts in `signals`, `model_outputs`, or `risk_events` completeness either, since that data is what the Decision Intelligence Engine will be built from later (docs/DATABASE_SCHEMA.md §3a).
- **Before starting any new phase or major subsystem, check whether the implementation changes or creates an architectural decision.** If it does, stop and ask me whether an ADR is required before writing any code — do not decide this unilaterally and do not proceed with implementation until I respond. See docs/adr/README.md for what counts as architectural and the existing ADR index.

Your task right now is Phase 0 only, exactly as specified in docs/COPILOT_PHASE_0_PROMPTS.md. Do not jump ahead to Phase 1 or any later phase. Do not implement any strategy, AI, risk, or execution logic in Phase 0 — that comes later.

Work through docs/COPILOT_PHASE_0_PROMPTS.md prompt by prompt, in order (Prompt 0.1 through Prompt 0.6). After completing each prompt, stop, show me a summary of what you created or changed, and wait for my confirmation before proceeding to the next prompt. Do not run multiple prompts' worth of changes in a single turn.

Before your first change, confirm back to me in plain language:
1. That you've read all 13 files listed above.
2. A one-sentence summary of what Phase 0 will produce.
3. Any conflicts or ambiguities you noticed between the docs that I should resolve before you start.

Then begin with Prompt 0.1 from docs/COPILOT_PHASE_0_PROMPTS.md.
```

---

### After Phase 0 Is Complete

1. Run through `VALIDATION_CHECKLIST.md`'s Phase 0 section yourself (not just Copilot) before proceeding.
2. Once Phase 0 passes validation, start a **new** Copilot Chat session (fresh context) and paste an equivalent handoff block referencing `docs/COPILOT_PHASE_1_PROMPTS.md` instead, keeping the same hard-rules section unchanged.
3. Repeat this pattern for each subsequent phase. Phases 2–8 do not yet have a dedicated `COPILOT_PHASE_N_PROMPTS.md` file — author one for each phase, following the Phase 0/1 pattern, before starting that phase (see `COPILOT_PROMPT_PACK.md`'s status note for what those phases were originally intended to cover, translated to the current `apps/web`/`apps/api` structure). Starting each phase in a fresh session with the docs re-read keeps Copilot's context grounded in the actual current state of the docs rather than a stale in-session memory of them.
4. Before authoring each new phase's prompt file, check `docs/adr/README.md`'s criteria for whether that phase's work introduces or changes an architectural decision. If it does, write the ADR (or confirm one already covers it) before the corresponding `COPILOT_PHASE_N_PROMPTS.md` is authored — the prompt file should reference the relevant ADR(s), not just the feature-level docs.

### Why a Fresh Session Per Phase

Long single-session implementation runs tend to drift from the source-of-truth docs as context fills up with generated code. Re-grounding Copilot in the docs at the start of every phase — and requiring it to restate its understanding before acting — is a deliberate safeguard consistent with this project's "process over speed" philosophy (`PROJECT_VISION.md` §2).
