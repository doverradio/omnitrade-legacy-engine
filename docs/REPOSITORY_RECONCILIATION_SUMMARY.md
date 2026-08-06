# REPOSITORY_RECONCILIATION_SUMMARY.md

## OmniTrade Legacy Engine — Architecture Reconciliation Summary

**Role:** Principal Software Architect, documentation-reconciliation pass.
**Mandate:** Reconcile architecture documentation with the existing repository so documentation accurately reflects reality. No new features, no new architecture, no production behavior change, no code renames, no file moves, no deletions.
**Date:** 2026-08-06.

This document is the top-level summary of the reconciliation pass. Read alongside its three companion deliverables:
- `docs/DOCUMENTATION_DRIFT_REPORT.md` — the full evidence base: every discrepancy found, with document/section, current description, actual implementation, recommended change, and severity.
- `docs/CANONICAL_ARCHITECTURE_MAP.md` — one canonical implementation named per major responsibility, with non-canonical/deprecated candidates identified.
- `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`, `ADR-0019-canonical-capital-campaign-governance-layer.md`, `ADR-0020-replay-terminology-and-boundaries.md` — new architecture decision records recording canonical ownership for the three responsibilities where duplication was significant enough to warrant one.

---

## Documentation Updated

Sixteen documents received targeted, surgical corrections — additions and rewrites of specific stale sections, following this repository's existing precedent (`docs/DOCS_AUDIT_REPORT.md`'s "targeted addition or correction, not full rewrite" convention). No document was rewritten from scratch; every change is traceable to a specific finding in `DOCUMENTATION_DRIFT_REPORT.md`.

| Document | What changed | Severity addressed |
|---|---|---|
| `SECURITY_AND_SAFETY.md` | Added a prominent current-state correction stating the live-trading governance transition the document's own preamble anticipated has occurred; marked §1 "No Live Trading" as historical. | Critical |
| `PROJECT_VISION.md` | Corrected §3's non-goal ("execute live trades... in its MVP phase") to note the MVP phase has ended and live trading is now real and governed. | Critical |
| `DATABASE_SCHEMA.md` | Rewrote §3a from "future schema, no tables introduced" to an accurate description of the six real, migrated Decision Intelligence Engine tables now in production. | High |
| `BACKEND_MODULE_SPECS.md` | Corrected `app/services/decisions/` from "future phase — architectural placeholder" to a description of its real, live contents; corrected `app/services/ai/` to note the module was never built under that name and point to where the responsibility actually lives. | High |
| `AI_LAYER.md` | Marked §2.1's AI/ML regime classifier as never built; added a "what actually exists" note pointing to the real, canonical deterministic classifier and the new ADR governing it. | High |
| `STRATEGY_ENGINE.md` | Corrected §1's `Strategy`/`Signal` Protocol code blocks to match the real implementation (argument count, pydantic vs. dataclass, tuple-of-mappings vs. DataFrame, real file path). | High |
| `API_CONTRACTS.md` | Added a scope-correction banner naming the ~15 real route modules the document doesn't cover, pointing to `00_OPERATIONS_MAP.md` for the current surface. | High |
| `RISK_AND_AUDIT_API_CONTRACTS.md` | Same scope-correction banner, plus a note that its "paper trading only" framing is stale. | High |
| `REPO_STRUCTURE.md` | Added a correction listing the dozens of real service packages missing from the documented tree, and noting the `/docs` listing is stale by well over 100 files. | High |
| `DATA_SOURCES.md` | Added a correction naming Kraken (primary) and Coinbase (secondary) as the real production providers, absent from the original document entirely. | High |
| `IMPLEMENTATION_MASTER_PLAN.md` | Added a superseded-notice to Phase 1, quoting `02_DECISIONS.md`'s own retraction of that phase's diagnosis. | High |
| `EVIDENCE_LAYER.md` | Corrected the "Future" status list — every item on it shipped the same day the document was written; each is now marked "Implemented" with its real module/endpoint. | High |
| `EXECUTION_PROVIDER_LAYER.md` | Corrected the "Kraken not yet implemented" claim — Kraken is registered and is in fact the primary production provider. | High |
| `RISK_ENGINE.md` | Corrected §1 and §5's paper-only/future-live-trading framing while explicitly preserving §2/§3's control mechanics, which were independently verified still accurate. | Medium |
| `CAPITAL_CAMPAIGNS.md` | Added a scope correction distinguishing the documented CRUD layer from the undocumented governance layer that actually drives live execution, and scoped the "no live automation" claim accordingly. | Medium |
| `PROJECT_STATUS.md` | Added a superseded-notice pointing to `00_PROJECT_STATE.md` as the authoritative current-status document, while preserving its still-accurate Phase 1–9 historical record. | Medium |

`docs/adr/README.md`'s index was also updated: ADR-0017 (which existed as a file but was missing from the index table — a pre-existing, unrelated gap found and fixed along the way) and the three new ADRs (0018–0020) were added.

---

## Documents Requiring Manual Review (Not Corrected in This Pass)

These were identified with specific, evidenced drift but were deliberately **not** edited, because a proper fix requires either a substantive rewrite exceeding "targeted correction," a decision only a human owner should make, or both. Each is flagged in detail in `DOCUMENTATION_DRIFT_REPORT.md`.

- **`API_CONTRACTS.md` / `RISK_AND_AUDIT_API_CONTRACTS.md` — full endpoint coverage.** The scope-correction banners added point to where the real surface is documented, but a full, endpoint-by-endpoint rewrite covering all ~15 undocumented route modules (`decisions.py`, `arena.py`, `capital_campaigns.py`, `controlled_proofs.py`, `live.py`, and more) is a substantial, dedicated task — likely multiple sessions' worth — not a documentation-reconciliation correction.
- **`VENUE_INSTRUMENT_REGISTRY.md`** — describes a canonical-asset/venue-instrument separation the real `Asset` model's schema (`UniqueConstraint("symbol", "exchange")`) structurally does not support. Already self-labeled "Constitutional Vision," so not actively misleading, but a future implementer should read `DOCUMENTATION_DRIFT_REPORT.md` §3.4 before assuming partial credit exists.
- **`VENUE_INTELLIGENCE_ENGINE.md`, `EXECUTION_ROUTING.md`, `EXECUTION_QUALITY.md`** — the "provider scoring / smart order routing" content is triplicated across all three with mutual cross-references and zero corresponding code. All three are already self-labeled "Constitutional Vision," so this is a redundancy/consolidation opportunity for whoever eventually authorizes this work, not an active-drift correction — no false claims to fix, just three copies of one unbuilt idea.
- **`docs/HISTORICAL_INTELLIGENCE_PLATFORM.md`, `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md`, `docs/HISTORICAL_MARKET_REPLAY_ENGINE.md`** — four documents (including `PIPELINE_ARCHITECTURE.md` §15–20) independently describe overlapping, unreconciled visions for chronological historical replay. `ADR-0020` fixes the shared terminology going forward but deliberately does not pick a winner among the four — that is a substantive architecture decision outside this pass's mandate. **`HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md` is not tracked in git at all** (confirmed via `git status`/`git log`) and should be either committed through normal review or removed from the working tree — this is a process gap, not a content one, and is the owner's call.
- **`LEARNING_INTELLIGENCE_ARCHITECTURE.md`'s status as a "governing dependency"** in `PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md`'s header — its own peer review (`docs/LEARNING_INTELLIGENCE_ARCHITECTURE_REVIEW.md`) already recommends demoting it to non-binding Vision status. Not corrected here because doing so touches a second document's governing-dependency list, and the review that should trigger the fix is itself only a few days old — flagged for the owner to action explicitly rather than folded into this pass silently.
- **`PORTFOLIO_INTELLIGENCE.md`** — confirmed 100% aspirational (zero corresponding code), but already honestly self-labeled "Constitutional Vision" throughout. No correction needed; recorded in `CANONICAL_ARCHITECTURE_MAP.md` §10 for completeness.
- **Roadmap-family documents not deep-audited in this pass:** `docs/POST_PHASE_9_ROADMAP.md`, `docs/PHASE_1_ARCHITECTURE_REVIEW.md`, and the ~20 phase-specific `COPILOT_PHASE_N_PROMPTS.md` files were not individually re-verified against current code in this session (time/scope-bounded); they are lower-risk (mostly historical implementation scripts, not standing architecture claims) but a future pass should confirm none of them make standing factual claims that have since gone stale, the way `PROJECT_STATUS.md` and `IMPLEMENTATION_MASTER_PLAN.md` did.

---

## Duplicate Systems Discovered

Full detail in `DOCUMENTATION_DRIFT_REPORT.md` §4.3 and `CANONICAL_ARCHITECTURE_MAP.md`. Summary, ranked by how significant the duplication is:

1. **Capital governance (High)** — two real services (`capital_campaigns` plural CRUD, `capital_campaign_domain`/`capital_campaign_orchestration` singular governance) write to the same table with no single owning document; plus "Capital Allocation Engine" denoting three unrelated things (ADR-0008's unbuilt hierarchy, a real narrow Strategy Arena helper, and casual references inside ADR-0008 itself). Resolved by naming a canonical layer in `ADR-0019`.
2. **Market-state/regime classification (Medium)** — three independent classifiers (`classify_regime_labels` live/production, `trend_regime_filter.py` dormant, `strategy_lab`'s percentile-band detector research-only), no shared code, no named authority. Resolved by `ADR-0018`.
3. **Provider/venue "intelligence" (Medium)** — the same unbuilt provider-scoring/smart-routing idea documented three times (`VENUE_INTELLIGENCE_ENGINE.md`, and sections of `EXECUTION_ROUTING.md` and `EXECUTION_QUALITY.md`), zero code. Flagged for manual review (consolidation), not resolved by an ADR since nothing exists yet to assign ownership over.
4. **Replay terminology (Medium, terminology not implementation)** — "replay" denotes three genuinely distinct, individually-non-duplicative mechanisms (decision-package identity replay, single-candidate counterfactual replay, the Counterfactual Outcome Ledger's horizon re-evaluation), plus a fourth, unbuilt meaning (chronological historical-market replay) described by four separate documents. Resolved by naming conventions in `ADR-0020`; the four historical-replay documents themselves remain unreconciled by design (out of this pass's mandate).
5. **"Decision Intelligence" naming collision (Low)** — `services/decisions/` (the full, documented DIE) vs. `services/decision_intelligence/` singular (a narrower replay-quality-ranking module). Related, not competing; recorded in `CANONICAL_ARCHITECTURE_MAP.md` §4, no ADR needed.
6. **`AI_LAYER.md`'s `app/services/ai/` vs. reality (Low, documentation-only)** — the documented module was never built; the real responsibility is fulfilled by four other, differently-named modules. Not architectural duplication (nothing competes with itself), a pure documentation-naming gap, corrected directly.

---

## Architectural Inconsistencies

Beyond the duplicate systems above, this pass's most important cross-cutting finding: **the three most-independently-corroborated drift items in the entire documentation set are all instances of the same failure mode** — a document declaring a subsystem "future work" or "not yet built" that has since shipped, with the document never revisited after the code caught up to it. This happened to the Decision Intelligence Engine's schema (`DATABASE_SCHEMA.md` §3a, found stale by three separate audits at three different times before this pass finally corrected it), to `app/services/decisions/` (`BACKEND_MODULE_SPECS.md`), and to the entire Evidence Layer's status table (`EVIDENCE_LAYER.md`, stale from the day it was written). This suggests the recurring gap is not any single document's carelessness but a missing *process* step: nothing in this repository's workflow currently prompts a documentation update when a "future work" item is completed, the way `docs/adr/README.md`'s rule prompts an ADR check *before* new architectural work begins. See "Future Cleanup Opportunities" below.

A second pattern, less severe: several MVP-era documents (`SECURITY_AND_SAFETY.md`, `PROJECT_VISION.md`, `RISK_ENGINE.md`, `DATA_SOURCES.md`, `API_CONTRACTS.md`, `REPO_STRUCTURE.md`) all describe the platform's original paper-trading-only, Binance/Alpaca-centered starting point, and none of them were updated when the platform's center of gravity shifted to live Kraken/Coinbase trading under campaign/mandate governance. This is a single underlying transition (documented accurately, if scattered, across `00_PROJECT_STATE.md`, `02_DECISIONS.md`, and the ADR set) that simply never propagated back into the earlier documents describing the world before it.

---

## Deprecated Concepts

Recorded here for completeness; none of these are deleted, per this task's constraints — they remain in the repository, explicitly marked non-canonical:

- `apps/api/app/services/strategies/trend_regime_filter.py` and `strategy_lab/pattern_intelligence/detectors/volatility.py` — real, functioning, but non-authoritative regime classifiers per `ADR-0018`. Remain usable for backtesting/research; must not be silently promoted to production authority.
- ADR-0008's `Master Account → Paper Portfolios → Strategies → Future Agents` hierarchy — recorded, per `ADR-0019`, as not the direction the platform actually took. ADR-0008 itself is not reversed (its Status remains `Accepted`, unchanged by this pass, per the ADR system's own rule that only genuinely-reversed decisions get their Status updated) — but any future work should treat the campaign/mandate model as the real, current answer to "how does capital get authorized," not ADR-0008's hierarchy.
- `apps/api/app/services/capital_campaigns/` (plural) as a *complete* description of campaign governance — remains real and in use for its actual, narrower CRUD scope, but must not be read as covering live-execution authorization (that's `capital_campaign_domain`/`capital_campaign_orchestration`, per `ADR-0019`).

---

## Recommended ADRs

Three were drafted and added as part of this pass (see top of this document). No further ADRs are recommended as *immediately* necessary — the remaining duplication findings (provider/venue "intelligence" triplication, the four historical-replay documents) describe unbuilt concepts, and per this repository's own ADR criteria (`docs/adr/README.md`: "ordinary feature work within an already-decided architecture" and "documentation corrections" don't need one), naming an owner for something that doesn't exist yet is premature — the right time for that ADR is when one of the four historical-replay visions, or the provider-routing concept, is actually authorized for implementation, at which point the authorizing decision itself should be the ADR.

One process recommendation, not a new ADR: consider whether `docs/adr/README.md`'s existing "before starting any new phase... check whether an ADR is required" rule should be paired with a symmetric rule — *when a documented "future phase" or "architectural placeholder" ships, its governing document(s) must be updated in the same PR* — the same maintenance-rule pattern `00_OPERATIONS_MAP.md` already uses successfully for operational changes. This is exactly the gap that let the Decision Intelligence Engine's schema documentation drift for a month across three independent audits.

---

## Future Cleanup Opportunities

- **Consolidate `capital_campaigns` and `capital_campaign_domain`** into one owning service, or explicitly document their split responsibility with a clear boundary — currently reconciled only via an ad hoc runtime-pin mechanism. Real code change, out of scope for this pass.
- **Consolidate the three market-state classifiers**, or explicitly retire the two non-canonical ones' potential for confusion by renaming them to make their non-authoritative status obvious in the filename/module path (e.g., clarifying `trend_regime_filter.py` is backtest-only). Real code change, out of scope for this pass; `ADR-0018` makes the current split legible in the meantime.
- **Regenerate `REPO_STRUCTURE.md`'s `/docs` and service-tree listings programmatically** rather than hand-maintaining them — the single highest-leverage fix to prevent this exact category of drift from recurring a third time.
- **Commit or remove `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md`** — it currently sits in the working tree, fully-formed (1254 lines), never reviewed.
- **Write the full API contract rewrite** covering the ~15 undocumented route modules, once resourced as its own task.
- **Decide among the four historical-replay visions** (or explicitly commission a fifth, reconciling document) — this is the single largest remaining "vision debt" in the documentation set, and per `ADR-0020` at least now shares consistent terminology once that decision is made.

---

## Technical Debt Discovered

Recorded because it surfaced during this pass, even though fixing it is code work outside this pass's mandate:

- `RiskEvaluationRequest`/`RiskEvaluationContext.ai_scaled_quantity` (`apps/api/app/services/risk/risk_engine.py`) is a correctly-bounded, downward-only confidence-scaling hook that has never been populated by any production call site — real, safe, unused capability, not a bug, but worth knowing about before anyone builds a parallel mechanism for the same purpose (already flagged in `docs/MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md`, repeated here for visibility in this pass's scope).
- Three junk artifacts at the repository root (`= [`, `operator`, `t \`), apparently accidental shell-redirection commits, were independently noted by `docs/OMNITRADE_REPOSITORY_REALITY_CHECK.md` — still present as of this pass; harmless, but worth a cleanup commit whenever the owner is doing routine hygiene work (not touched here, since this pass makes documentation-only changes).
- `pipeline_contracts/` (Phase 1 of `PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md`) is real, tested, committed work with exactly one production consumer and zero mention in `00_PROJECT_STATE.md` or `02_DECISIONS.md` — not wrong, just undiscoverable without reading git log directly. Recommended (not performed, since it requires drafting new decision-log content rather than correcting existing content): add a `02_DECISIONS.md` entry recording this work, consistent with that log's own append-only convention.

---

## Explicit Confirmations (Per Task Constraints)

- No new features were implemented.
- No new architecture was introduced — the three new ADRs each name an *existing* implementation as canonical; none authorizes new code.
- No production code, database schema, or API was changed.
- No files were renamed, moved, or deleted.
- No production code paths were touched — every change in this pass is confined to `docs/`.
- Every documentation correction cites specific `file:line` evidence, traceable through `DOCUMENTATION_DRIFT_REPORT.md`.
