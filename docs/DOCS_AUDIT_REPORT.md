# DOCS_AUDIT_REPORT.md

## OmniTrade Legacy Engine — Documentation Audit Report

**Audit date:** 2026-07-04
**Scope:** Full `/docs` folder as uploaded (23 files, including `validation-log.md`), audited against the recent additions — Four Core Engines, Decision Intelligence Engine (DIE), Counterfactual Outcome Ledger (COL), Small Account Mode, Phase 0 completion, and Phase 1 Prompt 1.1 implementation. This report also covers the new Decision Snapshot concept added as part of this same pass.

**Method:** Every file was read in full and cross-checked against the others for stale references, contradictions, outdated paths, old phase assumptions, missing references, and duplicated concepts. `validation-log.md` (the record of actual Phase 0 execution) was used as ground truth for what Phase 0 actually did, since some architecture docs' claims about Phase 0 predated that execution.

---

### 1. Files Inspected

| File | Inspected | Notes |
|---|---|---|
| PROJECT_VISION.md | ✅ | Updated (Decision Snapshot reference) |
| SYSTEM_ARCHITECTURE.md | ✅ | Updated (Decision Snapshot reference) |
| DATA_SOURCES.md | ✅ | No issues found |
| DATABASE_SCHEMA.md | ✅ | Updated (Decision Snapshot reference) |
| STRATEGY_ENGINE.md | ✅ | No issues found (DIE/COL content already consistent) |
| AI_LAYER.md | ✅ | Updated (Decision Snapshot reference) |
| RISK_ENGINE.md | ✅ | No issues found (Small Account Mode rule intact) |
| UI_SPEC.md | ✅ | No issues found (DIE/COL/SMA content already consistent) |
| COPILOT_PROMPT_PACK.md | ✅ | Updated (superseded-status notice added) |
| MVP_BUILD_PLAN.md | ✅ | Updated (Phase 0/1 stale claims fixed) |
| REPO_STRUCTURE.md | ✅ | Updated (docs folder listing completed) |
| ENVIRONMENT_SETUP.md | ✅ | No issues found |
| API_CONTRACTS.md | ✅ | Updated (missing DIE reference added) |
| RISK_AND_AUDIT_API_CONTRACTS.md | ✅ | No issues found (all 10 endpoints intact) |
| SMALL_ACCOUNT_MODE.md | ✅ | No issues found |
| DECISION_INTELLIGENCE_ENGINE.md | ✅ | Updated (Decision Snapshot §4a added; one stale internal cross-reference fixed) |
| FRONTEND_PAGE_SPECS.md | ✅ | Updated (Risk Monitor/Settings/Signals/Paper Trading endpoint references fixed) |
| BACKEND_MODULE_SPECS.md | ✅ | Updated (Decision Snapshot reference) |
| COPILOT_PHASE_0_PROMPTS.md | ✅ | No issues found |
| COPILOT_PHASE_1_PROMPTS.md | ✅ | No issues found |
| VALIDATION_CHECKLIST.md | ✅ | No issues found |
| SECURITY_AND_SAFETY.md | ✅ | No issues found |
| HANDOFF_TO_COPILOT.md | ✅ | Updated (stale prompt-pack reference fixed; DIE/COL awareness note added) |

---

### 2. Issues Found

#### Issue 1 — MVP_BUILD_PLAN.md Phase 0 claimed the database schema was "fully implemented"
**Severity:** High
**Description:** Phase 0's description said "Database schema + migrations fully implemented per `DATABASE_SCHEMA.md`." `validation-log.md` confirms only an empty baseline migration (revision `20260704_0001`, no tables) was actually applied in Phase 0, per `COPILOT_PHASE_0_PROMPTS.md` Prompt 0.4's explicit scope ("no tables yet — this just proves the migration pipeline works"). Full schema implementation actually begins with Phase 1 Prompt 1.1 (`assets`/`candles` tables) and continues incrementally through later phases as each needs its tables. Left uncorrected, this creates a false record of what Phase 0 validated — exactly the kind of drift between docs and reality this audit exists to catch.
**Recommended fix:** Rewrite the Phase 0 bullet to describe the empty baseline migration accurately, and clarify that full schema implementation is incremental starting in Phase 1.
**Status:** ✅ Fixed.

#### Issue 2 — MVP_BUILD_PLAN.md Phase 0 referenced the old `/frontend`, `/backend`, `/workers` folder structure
**Severity:** High
**Description:** Phase 0's description said "Monorepo structure (`/frontend`, `/backend`, `/workers`) per `COPILOT_PROMPT_PACK.md` Prompt 1." The actual, current structure — used by `REPO_STRUCTURE.md`, `COPILOT_PHASE_0_PROMPTS.md`, `COPILOT_PHASE_1_PROMPTS.md`, `HANDOFF_TO_COPILOT.md`, and confirmed by `validation-log.md`'s actual commands (`apps/api`, `apps/web`) — is `apps/web`/`apps/api`/`packages/shared`. This is a direct, confirmed contradiction between an architecture doc and the real repo.
**Recommended fix:** Update the Phase 0 bullet to reference `REPO_STRUCTURE.md` and `apps/web`/`apps/api`, not the legacy paths.
**Status:** ✅ Fixed.

#### Issue 3 — MVP_BUILD_PLAN.md Phases 1–8 cite `COPILOT_PROMPT_PACK.md`'s old "Prompt N" numbering, which is now split across two schemes
**Severity:** Medium
**Description:** Phase 0 and Phase 1 now have their own dedicated, up-to-date prompt files (`COPILOT_PHASE_0_PROMPTS.md` with Prompts 0.1–0.6, `COPILOT_PHASE_1_PROMPTS.md` with Prompts 1.1–1.10), but `MVP_BUILD_PLAN.md` still cited the old `COPILOT_PROMPT_PACK.md` "Prompt 3" for Phase 1, and Phases 2–8 still cite `COPILOT_PROMPT_PACK.md` Prompts 4–10 exclusively — a document that predates `REPO_STRUCTURE.md` and still describes `/frontend`, `/backend`, `/workers`. This isn't actively false for Phases 2–8 (they haven't started), but it will mislead whoever starts Phase 2 if not flagged now.
**Recommended fix:** Fix the Phase 1 citation to point at `COPILOT_PHASE_1_PROMPTS.md`. For Phases 2–8, add a clarifying note (not a full rewrite, since those phases haven't started and their prompts aren't due yet per this task's "do not generate Copilot prompts" instruction) explaining that `COPILOT_PROMPT_PACK.md`'s Prompts 4–10 are legacy, folder paths there should be read as `apps/web`/`apps/api`, and a dedicated `COPILOT_PHASE_N_PROMPTS.md` should be authored per phase before it starts, following the Phase 0/1 pattern.
**Status:** ✅ Fixed (Phase 1 citation corrected; clarifying note added before Phase 2; `COPILOT_PROMPT_PACK.md` itself given a status banner explaining what's superseded vs. still-relevant-but-outdated).

#### Issue 4 — `API_CONTRACTS.md` had zero references to the Decision Intelligence Engine
**Severity:** Medium
**Description:** Every other architecture-adjacent doc (`SYSTEM_ARCHITECTURE.md`, `DATABASE_SCHEMA.md`, `AI_LAYER.md`, `BACKEND_MODULE_SPECS.md`, `PROJECT_VISION.md`, `STRATEGY_ENGINE.md`, `MVP_BUILD_PLAN.md`, `UI_SPEC.md`) already names the DIE and/or COL as forward-looking context. `API_CONTRACTS.md` — the doc most directly parallel to `RISK_AND_AUDIT_API_CONTRACTS.md`, which already names the future `/decisions` and `/counterfactuals` endpoints — had no mention of either at all. This is the one clear "missing reference to Decision Intelligence Engine as a core subsystem" this audit was specifically asked to check for.
**Recommended fix:** Add a short "Future API Surface: Decision Intelligence Engine" section mirroring the one in `RISK_AND_AUDIT_API_CONTRACTS.md`/`DECISION_INTELLIGENCE_ENGINE.md` §10, naming the placeholder endpoints without specifying them in detail.
**Status:** ✅ Fixed.

#### Issue 5 — `FRONTEND_PAGE_SPECS.md`'s Risk Monitor, Settings, Signals, and Paper Trading pages still referenced undefined/"implied" endpoints that are now fully defined
**Severity:** Medium-High
**Description:** `RISK_AND_AUDIT_API_CONTRACTS.md` fully defines `GET /risk/status`, `POST /risk/kill-switch/enable`/`disable`, `GET/PATCH /risk/rules`, `GET/PATCH /settings`, and `GET /audit-log`, and `API_CONTRACTS.md` fully defines `POST /paper/account`. Despite this, `FRONTEND_PAGE_SPECS.md` still described the Risk Monitor page's required API calls as "not yet in `API_CONTRACTS.md`'s initial 12 — flag for addition," and described Settings, Signals, and Paper Trading's relevant calls as "implied" placeholders needing future endpoints. This is stale — the endpoints exist and this doc simply hadn't been updated to point at them.
**Recommended fix:** Update all four pages' "Required API calls" lines to cite the real, defined endpoints from `RISK_AND_AUDIT_API_CONTRACTS.md`/`API_CONTRACTS.md`.
**Status:** ✅ Fixed. One genuinely still-open gap was preserved rather than papered over: no dedicated paginated `GET /risk/events` history endpoint exists yet (only current-state data via `GET /risk/status`), so the Risk Monitor page's event log is now honestly described as showing "currently active" events, not full history, with the gap flagged for Phase 7.

#### Issue 6 — `REPO_STRUCTURE.md`'s `/docs` folder listing was missing three files
**Severity:** Low
**Description:** `REPO_STRUCTURE.md` §5 lists the expected contents of `/docs`, but predates `RISK_AND_AUDIT_API_CONTRACTS.md`, `SMALL_ACCOUNT_MODE.md`, and `DECISION_INTELLIGENCE_ENGINE.md` — all three were missing from the listing. Purely a documentation-completeness issue, not a functional contradiction, since nothing depends on this listing being exhaustive.
**Recommended fix:** Add the three missing filenames (and this report) to the listing.
**Status:** ✅ Fixed.

#### Issue 7 — `HANDOFF_TO_COPILOT.md` had no awareness of the Four Core Engines, DIE, or COL
**Severity:** Low-Medium
**Description:** This is the literal onboarding script for Copilot, and it made no mention of the platform's four-permanent-engines framing or the DIE/COL at all — not even a "these exist, don't build them yet, but don't preclude them" note. Since Phase 0/1 work doesn't touch the DIE, this wasn't causing active harm, but it's exactly the kind of "missing reference to Decision Intelligence Engine as a core subsystem" this audit was asked to check for, and leaving it out risks a future Copilot session building `signals`/`model_outputs`/`risk_events` in ways that make later DIE work harder than necessary.
**Recommended fix:** Add one short hard-rule bullet naming the four engines and noting DIE/COL are future-phase but `signals`/`model_outputs`/`risk_events` completeness matters for them.
**Status:** ✅ Fixed. Also fixed a stale line in the same file referencing `COPILOT_PROMPT_PACK.md` "prompt packs" for Phases 3–8 without acknowledging Phases 2–8 don't yet have dedicated, up-to-date prompt files (see Issue 3).

#### Issue 8 — Stale internal cross-reference inside `DECISION_INTELLIGENCE_ENGINE.md` itself
**Severity:** Low
**Description:** When the Counterfactual Outcome Ledger was added as a new §8 in a prior pass, the trailing sections (old §8–§12) were renumbered to §9–§13, but one internal cross-reference in §4 ("...not necessarily to duplicate their storage (see §8 for how this reconciles with the existing schema)") was missed and still pointed at the old number — which after renumbering pointed at the COL section instead of the intended Database Impact section.
**Recommended fix:** Correct the reference from §8 to §9.
**Status:** ✅ Fixed. (Also relabeled one lifecycle-diagram node from "Decision Intelligence Record Created" to "Decision Record Created" for terminology consistency with the rest of the document, which otherwise exclusively uses "Decision Record.")

---

### 3. Items Identified but Intentionally Left As-Is

These were reviewed and are **not** contradictions requiring a fix — recorded here so they aren't mistaken for oversights in a future audit.

- **`FRONTEND_PAGE_SPECS.md` has 8 MVP pages; `UI_SPEC.md` describes 9 (including AI Review).** This is intentional phasing, not a bug: the AI Review page is scheduled for Phase 6 in `MVP_BUILD_PLAN.md`, and Phase 0 correctly scaffolded only the 8 pages needed at that time (`COPILOT_PHASE_0_PROMPTS.md` Prompt 0.2 correctly says "8 pages"). `RISK_AND_AUDIT_API_CONTRACTS.md` already flags that `/ai-review` should be added as a 9th route "when implemented." No fix needed now; revisit when Phase 6 prompts are authored.
- **Strategy Lab and Backtests pages in `FRONTEND_PAGE_SPECS.md` still reference two smaller "implied" endpoints** (`POST /strategies/:id/parameter-sets` and a list-view `GET /backtests`). These are genuine small gaps, but distinct from the risk/audit/settings gap this task targeted, and low-risk since Phase 4 (Strategy Parameters) hasn't started. Left open for a future, narrower gap-closing pass.
- **The four core engines (Market Intelligence, Strategy Evolution, Portfolio Intelligence) don't have dedicated standalone documents**, unlike the Decision Intelligence Engine. This is by design — `PROJECT_VISION.md` and `SYSTEM_ARCHITECTURE.md` §1 explicitly map each of the other three engines onto existing docs (`DATA_SOURCES.md`/`STRATEGY_ENGINE.md`/`AI_LAYER.md` for Market Intelligence; `STRATEGY_ENGINE.md`/`AI_LAYER.md` for Strategy Evolution; `RISK_ENGINE.md`/`DATABASE_SCHEMA.md` for Portfolio Intelligence) rather than duplicating content into new files. Not a gap.
- **`RISK_ENGINE.md` and `SECURITY_AND_SAFETY.md` don't mention the DIE/COL directly.** The relationship is already documented from the DIE's side (`DECISION_INTELLIGENCE_ENGINE.md` §12 names the Risk Engine's contribution explicitly). Adding a reverse-reference into `RISK_ENGINE.md` and `SECURITY_AND_SAFETY.md` wasn't judged necessary to reach internal consistency, and the task instructed minimal, surgical edits — so these were left untouched.

---

### 4. Decision Snapshot — New Concept Added

Per this task's request, **Decision Snapshot** was added as a new formal concept, §4a in `DECISION_INTELLIGENCE_ENGINE.md`, immediately after the existing Decision Record Schema (§4). It defines an immutable, point-in-time capture of the exact state that produced a decision — timestamp, asset, exchange, timeframe, OHLCV context, indicators, generated features, market regime, strategy inputs, risk inputs, volatility, spread/liquidity context (where available), current position state, open trades, portfolio exposure, and five version-pin fields (parameter set, strategy, AI/model, decision engine, configuration versions) — captured by value, write-once, never updated, and existing in a one-to-one relationship with each Decision Record.

Direct references to Decision Snapshot were added to the five docs identified as needing them:
- **`SYSTEM_ARCHITECTURE.md`** — DIE component entry (§2.7a) now names Decision Snapshot as the mechanism that anchors every Decision Record.
- **`DATABASE_SCHEMA.md`** — §3a (future DIE schema note) now names **Decision Snapshots** as a future table, immutable and one-to-one with Decision Records.
- **`AI_LAYER.md`** — §6 (Versioning & Reproducibility) now explains how its existing `model_version` discipline is exactly what the Decision Snapshot's `ai_model_version` field relies on.
- **`BACKEND_MODULE_SPECS.md`** — the `app/services/decisions/` placeholder now includes a `snapshot.py` responsibility, with an explicit "capture by value, never live references" constraint.
- **`PROJECT_VISION.md`** — the "Memory over history" philosophy bullet now names Decision Snapshot as what makes that memory replayable rather than reconstructed.

No SQL, code, or Copilot prompts were generated for this concept, per this task's constraints — it is documented at the architecture level only.

---

### 5. Summary of All Edits Made

| File | What changed | Why |
|---|---|---|
| `DECISION_INTELLIGENCE_ENGINE.md` | Added §4a Decision Snapshot; fixed one stale internal cross-reference (§8→§9); relabeled one diagram node for terminology consistency; updated opening framing paragraph to name Decision Snapshot | New concept (task requirement) + internal consistency fix |
| `SYSTEM_ARCHITECTURE.md` | Added Decision Snapshot mention to DIE component entry | Direct reference task requirement |
| `DATABASE_SCHEMA.md` | Added Decision Snapshots table to §3a future-schema note | Direct reference task requirement |
| `AI_LAYER.md` | Added Decision Snapshot mention to §6 Versioning & Reproducibility | Direct reference task requirement |
| `BACKEND_MODULE_SPECS.md` | Added `snapshot.py` responsibility to the `app/services/decisions/` placeholder | Direct reference task requirement |
| `PROJECT_VISION.md` | Added Decision Snapshot mention to the "Memory over history" bullet | Direct reference task requirement |
| `MVP_BUILD_PLAN.md` | Corrected Phase 0's false "schema fully implemented" and stale `/frontend`/`/backend`/`/workers` claims; corrected Phase 1's prompt-file citation; added a clarifying note before Phase 2 about legacy prompt-pack citations | Fix known stale issues (explicitly requested) |
| `COPILOT_PROMPT_PACK.md` | Added a status banner marking Prompts 1–3 superseded and Prompts 4–10 legacy-but-still-referenced | Fix known stale issues |
| `REPO_STRUCTURE.md` | Added `RISK_AND_AUDIT_API_CONTRACTS.md`, `SMALL_ACCOUNT_MODE.md`, `DECISION_INTELLIGENCE_ENGINE.md`, and this report to the `/docs` listing | Missing reference |
| `HANDOFF_TO_COPILOT.md` | Fixed stale reference to `COPILOT_PROMPT_PACK.md` "prompt packs" for Phases 3–8; added a hard-rule bullet naming the four core engines and DIE/COL awareness | Missing reference + stale issue |
| `API_CONTRACTS.md` | Added a "Future API Surface: Decision Intelligence Engine" section | Missing reference to DIE as a core subsystem (explicitly requested check) |
| `FRONTEND_PAGE_SPECS.md` | Fixed Risk Monitor, Settings, Signals, and Paper Trading pages' "Required API calls" to cite real, defined endpoints instead of "implied"/"flag for addition" placeholders | Fix known stale issue (endpoint gap was closed in the API docs but not reflected here) |

No file was rewritten from scratch. Every change above is a targeted addition or correction to existing text.

---

### 6. Explicit Confirmations (Per Task Constraints)

- No code was generated.
- No Copilot prompts were generated (the note added to `MVP_BUILD_PLAN.md` and `COPILOT_PROMPT_PACK.md` explicitly defers actual Phase 2–8 prompt authoring to a future, separate step).
- Phase 1 implementation was not started or continued as part of this audit.
- All edits are additive/corrective at the documentation layer only.
