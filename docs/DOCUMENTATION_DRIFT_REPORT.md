# DOCUMENTATION_DRIFT_REPORT.md

## OmniTrade Legacy Engine — Architecture Reconciliation: Documentation Drift Report

**Role:** Principal Software Architect, documentation-reconciliation pass.
**Scope:** `docs/*.md` (160+ files) and `docs/adr/*.md` (17 ADRs), compared against the current repository (`apps/api/app/`, `apps/web/`, `strategy_lab/`).
**Method:** Direct reading of every explicitly-scoped governing document plus targeted, parallel, read-only code verification for every specific claim below. Every row cites `file:line` evidence, not inference. No production behavior was changed to produce this report.
**Treat the repository as authoritative.** Where a document and the code disagree, the code wins; the recommended change is always to the document.

This report complements — and does not repeat — two prior reconciliation passes already in the repository, whose findings are treated as established ground truth here rather than re-derived:
- `docs/DOCS_AUDIT_REPORT.md` (2026-07-04) — an early MVP-era pass, now itself superseded by the scale of what it audited.
- `docs/OMNITRADE_REPOSITORY_REALITY_CHECK.md` (undated, pre-ADR-0008) — first identified the paper-MVP-vs-live-campaign drift and the "replay" terminology collision; several of its recommendations (ADR-0008/0009/0010 in its own numbering) were adopted, but under different final ADR numbers (0008–0010, 0012–0016), and the underlying stale prose in the docs it flagged (`DATABASE_SCHEMA.md`, `SYSTEM_ARCHITECTURE.md`, `REPLAY_AGENT_INTERFACE.md`) was never corrected.
- `docs/PHASE_0_ARCHITECTURAL_INVENTORY.md` (2026-07-31) — the most recent and most exhaustive prior inventory, scoped to the Pipeline/Learning initiative; used here as ground truth for orchestration/execution/reconciliation call paths.
- `docs/MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md` (2026-08-06, this session's earlier deliverable) — the source of the market-state/regime/AI-layer/replay/risk/database findings reused below.

---

## Severity Legend

| Severity | Meaning |
|---|---|
| **Critical** | The document asserts something safety-, capital-, or governance-relevant that is now factually false, and could mislead an operator or implementer into an unsafe action or false assumption about production behavior. |
| **High** | The document's core subject matter (an entire module, table, or subsystem) no longer matches reality, or a specific factual claim is checkably false. |
| **Medium** | The document is materially incomplete (omits large swaths of real, relevant capability) but not actively false about what it does describe. |
| **Low** | Minor staleness — a missing cross-reference, an outdated file listing, terminology drift — that does not risk a wrong architectural or safety decision. |

---

## 1. Critical Severity

### 1.1 `SECURITY_AND_SAFETY.md` §1 "No Live Trading"

**Current description:** "The MVP contains **no code path** capable of placing a real, capital-at-risk order," further asserting Alpaca is paper-only, crypto execution is "entirely internal simulation," and `trades.is_paper` is always `true`. The document opens by stating these are non-negotiable rules: "a PR that violates any rule below should not be merged."

**Actual implementation:** `00_PROJECT_STATE.md` (Authority: Highest) records, as proven capabilities: "✅ Live Kraken authentication," "✅ Live production BUY," "✅ Live production SELL," "✅ Live production reconciliation." `apps/api/app/services/exchange_connections/providers/kraken_spot.py::submit_order` and `apps/api/app/services/capital_campaign_domain/commissioned_entry_execution.py` submit real orders to Kraken today, gated by (not absent) live-submission feature flags. `02_DECISIONS.md` records multiple rounds of live-capital governance work (Controlled Proof, Exit Recovery) explicitly because live capital is at risk. The document's own "Governance Boundary" preamble anticipates this transition ("Until that governance transition is explicitly approved, MVP remains paper-only") but the transition has, in fact, occurred, and the document has not been updated to say so.

**Recommended documentation change:** Add a prominent, dated correction at the top of the document stating that the governed live-trading transition anticipated by its own preamble has occurred (citing `00_PROJECT_STATE.md`, `00_OPERATIONS_MAP.md`, and `02_DECISIONS.md` as the current authoritative safety/governance sources), and that §1's absolute "no live code path" claim is historical (describes the MVP baseline this repo started from), not current. Do not delete or soften the historical rules — they remain the correct description of the MVP starting point and the correct behavior for any genuinely paper-only environment — but a reader must not be able to conclude "no real money is at risk" from this document as currently written.

**Severity:** Critical.

---

### 1.2 `PROJECT_VISION.md` §3 "Non-Goals" and §5 "Long-Term Family Legacy Vision"

**Current description:** Non-Goals states the platform explicitly does not "Execute live trades with real money in its MVP phase." §5 frames "optional small-scale live trading with strict caps" as a Horizon 2 (6–18 month) future capability, not current reality.

**Actual implementation:** Same evidence as 1.1 — live trading is real, in production, today, per `00_PROJECT_STATE.md`. The platform is no longer in an "MVP phase" by its own more recent documents' framing (`docs/PROJECT_STATUS.md` marks Phase 9 "Live Trading Foundation" complete; `docs/MASTER_PRODUCT_ROADMAP.md` §4 explicitly notes "Phase 9 Live Trading Foundation is implemented as controlled operational infrastructure, not immediate autonomous deployment authorization" — i.e., a more recent, more accurate doc already acknowledges this transition where `PROJECT_VISION.md` does not).

**Recommended documentation change:** Update §3's non-goal to read as historically scoped ("did not, in its original MVP phase, execute live trades — see `MASTER_PRODUCT_ROADMAP.md` §4 and `00_PROJECT_STATE.md` for the current, governed live-trading state"), and add a note to §5 that Horizon 2's live-trading capability has been reached under explicit governance (campaigns, mandates, Controlled Proof), ahead of its original 6–18 month estimate, without editorializing on whether that's early or on-schedule.

**Severity:** Critical (a foundational vision document contradicting the platform's own current, governed capital-risk posture).

---

## 2. High Severity

### 2.1 `DATABASE_SCHEMA.md` §3a "Future Schema: Decision Intelligence Engine"

**Current description:** States that Decision Records, Decision Snapshots, Decision Evidence, Decision Outcomes, Decision Reviews, AI Reflections, Human Reviews, Shadow Outcomes, Counterfactual Evaluations, and Decision Quality Scores "will introduce their own tables in a future implementation phase," and "No new tables, columns, or migrations are introduced by this note."

**Actual implementation:** `apps/api/app/models/decision_record.py` (table `decision_records`) and `apps/api/app/models/decision_snapshot.py` (table `decision_snapshots`) are fully built, migrated (`db/migrations/versions/20260706_0007_add_decision_record_snapshot_tables.py`, dated 2026-07-06 — one month before this report), immutable (SQLAlchemy `before_update`/`before_delete` event listeners), and field-complete against the schema `DECISION_INTELLIGENCE_ENGINE.md` §4/§4a describes. `decision_explainability_record.py`, `decision_counterfactual_result.py`, `decision_quality_score.py`, and `decision_alternative_action.py` also exist as real, migrated, FK-linked tables covering the Explainability/COL/DQE/Alternative-Actions ground this note calls future work (under different table names than the note anticipates, but the same responsibilities). This exact drift was independently identified by `docs/OMNITRADE_REPOSITORY_REALITY_CHECK.md` row B1 and corroborated a second time by this session's own market-state audit — three independent passes now agree.

**Recommended documentation change:** Rewrite §3a to describe the tables as built (name the real tables and their real columns at a summary level), remove the "No new tables... introduced by this note" disclaimer (it is describing a note written before the tables existed, and is no longer true of the schema itself), and note the actual migration date for traceability.

**Severity:** High (this is the single most independently-corroborated drift in the entire documentation set — found by three separate audits at three different points in time, never corrected).

---

### 2.2 `BACKEND_MODULE_SPECS.md` — `app/services/decisions/` and `app/services/ai/`

**Current description:** `app/services/decisions/` is documented as "future phase — architectural placeholder... not scheduled for implementation in the current MVP phases... should not be scaffolded until a dedicated phase is defined." `app/services/ai/` is documented as containing `regime_classifier.py`, `signal_scorer.py`, `allocator.py`, `explainer.py`, `post_trade_review.py`.

**Actual implementation:** `app/services/decisions/` is fully implemented — `ingestion.py`, `explainability.py`, `counterfactuals.py`, `quality.py`, `replay_context.py`, `replay_candidates.py`, `package.py` all exist, are wired to the real `decision_records`/`decision_snapshots`/`decision_explainability_record`/`decision_counterfactual_result`/`decision_quality_score` tables, and are consumed by the live autonomous decision path (`autonomous_cycle/orchestrator.py::_persist_decision_intelligence`, per `docs/PHASE_0_ARCHITECTURAL_INVENTORY.md` §2 and §4). Separately, `app/services/ai/` does **not** contain any of the five named files — no such regime classifier, signal scorer, allocator, or post-trade review module exists anywhere in the codebase (confirmed by direct grep in this session's market-state audit). The real advisory/confidence/explanation work that does exist lives in `app/services/entry_intelligence/`, `app/services/ai_coach/`, `app/services/decision_quality/`, and `app/services/decision_intelligence/` (singular — a distinct, narrower subsystem, see §4.3 below) — none of which match the doc's described `app/services/ai/` module shape or filenames.

**Recommended documentation change:** Correct `app/services/decisions/` from "future phase — architectural placeholder" to a description of the real module and its five real files, keeping the still-relevant design constraints (observational-only, snapshot-by-value, no automatic score feedback) since those remain accurate as invariants of the real code. Correct `app/services/ai/`'s description to either mark it as never built under that name, or point to the real, scattered set of modules that fulfill the AI-layer responsibility today.

**Severity:** High.

---

### 2.3 `AI_LAYER.md` §2.1 "Regime Classifier"

**Current description:** Documents an AI/ML regime classifier taking "OHLCV history, realized volatility, trend-strength indicators (ADX), and the rules-based `trend_regime_filter` output," producing a labeled regime with confidence, "written to `signals.regime_tag` and `model_outputs`," validated by backtesting and cross-checked against `trend_regime_filter`.

**Actual implementation:** No such classifier exists anywhere in the codebase (confirmed by full-repo grep in this session's market-state audit). The only two writers of `Signal.regime_tag` are `autonomous_cycle/orchestrator.py:762` (writes a cycle-interval label, e.g. `"autonomous_cycle_15m"` — not a regime) and `continuous_pipeline_worker.py:2752` (writes `None`). The production system's actual regime-aware capability — `apps/api/app/services/strategy_outcomes/service.py::classify_regime_labels`, consumed by `strategy_roster/decision_aggregator.py::_strategy_weight` — is a deterministic, non-AI classifier built via a completely different, unrelated code path, and does not cross-check against `trend_regime_filter` as documented (see `CANONICAL_ARCHITECTURE_MAP.md`'s Market State row for the full picture).

**Recommended documentation change:** Replace §2.1's description with an accurate account of what exists (`classify_regime_labels`'s deterministic trend/volatility/range classification and its consumption by strategy weighting), explicitly marking the AI/ML classifier as not built, and removing or clearly future-tagging the `trend_regime_filter` cross-validation claim, which describes a relationship that has never existed in code.

**Severity:** High.

---

### 2.4 `STRATEGY_ENGINE.md` §1 "Design Contract" (`Strategy`/`Signal` Protocol)

**Current description:** `generate_signal(self, candles: pd.DataFrame, params: dict, context: StrategyContext) -> Signal` (3 arguments); `Signal` as a plain `@dataclass` with `strength: float`; strategy files live under `backend/strategies/<slug>.py`.

**Actual implementation:** `apps/api/app/services/strategies/base.py:23-116` — `generate_signal(self, context: StrategyContext) -> Signal` (1 argument; candles and params are now fields *on* `StrategyContext`); `Signal` is a frozen `pydantic.BaseModel` with `strength: Decimal` (bounded `[0,1]` via `Field`), an additional `timestamp: datetime` field the doc doesn't list, and field validators enforcing non-empty `reason`/non-null `indicators`; candles are `tuple[MappingProxyType[str, Any], ...]`, never a pandas DataFrame; files live under `apps/api/app/services/strategies/<slug>.py` — the doc's `backend/` path does not exist in this repository at all.

**Recommended documentation change:** Rewrite §1's code blocks to match the real `Strategy`/`Signal`/`StrategyContext` definitions verbatim, and correct the file-path convention in §5 ("Adding a New Strategy"). The contract's underlying *spirit* — pure functions, mandatory `reason`/`indicators`, no hidden state — is honored by the real code and needs no change; only the literal signatures and paths are wrong.

**Severity:** High (this is a developer-facing contract doc; a contributor following it verbatim today would write code that does not compile against the real `Strategy` Protocol).

---

### 2.5 `API_CONTRACTS.md` and `RISK_AND_AUDIT_API_CONTRACTS.md` — missing ~15 route modules

**Current description:** Documents an MVP-era API surface: `/health`, `/markets/assets`, `/markets/candles`, `/backtests/run`, `/backtests/:id`, `/strategies`, `/parameter-sets`, `/signals`, `/paper/account`, `/paper/trades`, `/paper/reset`, plus (in the companion doc) `/risk/status`, `/risk/kill-switch/*`, `/risk/rules`, `/audit-log`, `/settings`, `/ai/review`, `/ai/explanations/:signal_id`. `RISK_AND_AUDIT_API_CONTRACTS.md` states every endpoint below it is "subject to `SECURITY_AND_SAFETY.md`: paper trading only, no live-trading code paths" (inheriting the 1.1 drift directly).

**Actual implementation:** `apps/api/app/main.py` registers on the order of 24 routers. Real, live route modules with zero mention in either contract doc include: `decisions.py` (Decision Records/timeline/explainability/counterfactuals/quality/replay — a large, real surface), `arena.py` (Strategy Arena tournaments/replay/capital-allocation/coach-review), `validation_runs.py`, `mission_control.py`, `operator_actions.py`, `research.py`, `strategy_lab_offline.py`, `capital_campaigns.py`, `autonomous_capital_mandates.py`, `controlled_proofs.py`, `crypto_order_previews.py`, `exchange_connections.py`, `instant_trades.py`, `live_crypto_orders.py`, `live.py`, `asset_commissioning.py`. Several of the *documented* endpoints do still exist under recognizable names (`markets.py`, `backtests.py`, `strategies.py`, `paper.py`, `parameter_sets.py`, `health.py`, `risk.py`) but `risk.py`'s actual shape now governs live campaigns, not paper accounts, per this session's market-state audit's Risk Engine findings.

**Recommended documentation change:** Add a prominent status banner to both documents identifying them as the original MVP API contract (still a reasonably accurate description of that original, narrower surface) and pointing to `docs/00_OPERATIONS_MAP.md` (which already documents real production route prefixes under "Notable service/domain modules") as the current, higher-authority index of the live API surface. A full line-by-line rewrite of these contracts to cover ~15 additional route modules is a substantial undertaking better scoped as its own dedicated task, not a documentation-reconciliation correction — flagged for manual review rather than attempted here (see Reconciliation Summary).

**Severity:** High.

---

### 2.6 `REPO_STRUCTURE.md` — missing service packages and `/docs` listing

**Current description:** Lists `apps/api/app/services/{data,strategies,backtesting,signals,risk,paper,ai}/` as the complete service-layer structure, and a `/docs` listing of ~24 files.

**Actual implementation:** The real `apps/api/app/services/` tree includes dozens of additional packages not mentioned at all: `orchestration/`, `capital_campaign_domain/`, `capital_campaign_orchestration/`, `capital_campaigns/`, `capital_allocation/`, `mandates/`, `controlled_proof/`, `entry_intelligence/`, `decisions/`, `decision_intelligence/`, `decision_quality/`, `ai_coach/`, `exchange_connections/`, `autonomous_cycle/`, `strategy_roster/`, `strategy_outcomes/`, `replay/`, `research_agents/`, `arena/`, `tournament/`, `pipeline_contracts/`, `historical_simulation/`, `asset_commissioning/`, `live/`, among others. `docs/` itself now contains 160+ files, not ~24 — `docs/DOCS_AUDIT_REPORT.md`'s 2026-07-04 pass already updated this listing once (adding 3 files), but the doc has not been revisited since, and the growth since then (the majority of the current 160+ files) is entirely unlisted.

**Recommended documentation change:** Either regenerate the `/docs` listing programmatically (`ls docs/*.md`) as part of routine maintenance rather than hand-maintaining it, or replace the hand-maintained listing with a pointer to running that command — hand-maintaining an exhaustive file listing at this scale is itself the root cause of the drift and will recur. For the service-layer tree, add the missing top-level package names (a one-line-per-package addition, not full per-file documentation, is enough to make the map honest about scope).

**Severity:** High (misleads a new contributor about the actual size and shape of the codebase they're working in).

---

### 2.7 `DATA_SOURCES.md` — no mention of Kraken or Coinbase

**Current description:** Documents Binance/Binance.US (primary crypto source), Alpaca (primary stock source), and yfinance (backfill-only) as the platform's data sources.

**Actual implementation:** The platform's actual, live, production execution and market-data providers are Kraken (`apps/api/app/services/data/kraken_client.py`, `exchange_connections/providers/kraken_spot.py`) and, secondarily, Coinbase (`exchange_connections/providers/coinbase_advanced.py`) — per `00_OPERATIONS_MAP.md`'s External Systems table and `docs/PROJECT_STATUS.md`'s "Execution Provider Status" section. Binance is present only as a market-data client (`services/data/binance_client.py`) with "no order-execution integration found" per `00_OPERATIONS_MAP.md`; Alpaca is present only as a paper-trading integration (`services/paper/alpaca_paper.py`), not the primary stock source the doc describes (the platform trades crypto in production today, not stocks).

**Recommended documentation change:** Add Kraken and Coinbase as the current primary crypto data/execution sources, with a note that Binance's role has narrowed to market-data-only and Alpaca's to paper-trading-only, redirecting to `00_OPERATIONS_MAP.md`'s External Systems table as the current source of truth for provider status.

**Severity:** High (the document that's supposed to name the platform's real data dependencies doesn't name either of the two the platform actually depends on for live execution).

---

### 2.8 `IMPLEMENTATION_MASTER_PLAN.md` Phase 1 — known-superseded diagnosis presented as "CRITICAL PATH"

**Current description:** Phase 1 ("First Autonomous Profit Unblock") states the current BUY-rejection blocker is caused by unwired Risk Engine inputs (`campaign_authorized_notional` never passed, `has_computable_stop_loss` hardcoded `True`, cooldown history hardcoded empty) and prescribes wiring them as the critical-path fix.

**Actual implementation:** `02_DECISIONS.md`'s own "Parallel Authorized Lanes" entry (2026-07) explicitly states: *"`IMPLEMENTATION_MASTER_PLAN.md` Phase 1's original diagnosis (unwired Risk Engine inputs...) is superseded by runtime evidence. The actual blocker observed in production is package-progression/mandate authorization... Phase 1's original risk-input changes were explicitly NOT implemented as part of this lane."* `06_NEXT_SESSION.md` and `00_OPERATIONS_MAP.md`'s Controlled Proof Exit Recovery section confirm the blocker has since moved through several more specific, evidenced diagnoses entirely unrelated to Phase 1's risk-input theory. This is not a new finding — it is already recorded, by the project's own append-only decision log, as a correction to this exact plan document — but the plan document itself was never annotated to reflect it.

**Recommended documentation change:** Add a superseded-notice at the top of Phase 1 pointing to `02_DECISIONS.md`'s "Parallel Authorized Lanes" entry, so a reader does not act on a diagnosis the project has already, on the record, retracted.

**Severity:** High (a document titled "CRITICAL PATH" pointing at a retracted diagnosis is exactly the failure mode append-only decision logs exist to prevent, and this one slipped through).

---

### 2.9 `EVIDENCE_LAYER.md` "Current Repository Status → Future" list

**Current description:** Lists Replay Engine, Replay Evidence, Decision Quality Engine, Decision Arena, AI Coach Learning, Decision Intelligence, and Capital Allocation as "Future" (not yet built), against an "Implemented" list of the more basic Signal/Risk Event/Paper Trade Event/Decision Record evidence chain.

**Actual implementation:** Every item on the "Future" list is now built and live, per this session's execution/evidence research: `services/replay/default_agent.py` (real replay), `services/decision_quality/deterministic.py`, `services/decision_intelligence/deterministic.py`, `api/routes/arena.py` (760 lines exposing `/arena/replay`, `/arena/evaluate-replay`, `/arena/coach-review`, `/arena/decision-intelligence`, `/arena/capital-allocation`, `/arena/tournament`), with substantial test coverage. Git history shows these shipped the same day as this document (2026-07-09) — the document's status table was accurate on the day it was written and has simply never been revisited since.

**Recommended documentation change:** Move every item on the "Future" list to "Implemented," citing the real modules/endpoints, and note the shipped date for traceability.

**Severity:** High (this doc's entire organizing structure — an Implemented/Future split — is now backwards for most of its Future column).

---

### 2.10 `EXECUTION_PROVIDER_LAYER.md` — "Kraken not yet implemented" claim

**Current description:** States "no Kraken implementation in this prompt," "current registered provider: coinbase_advanced," and frames Kraken as "additive for later prompts."

**Actual implementation:** `apps/api/app/services/exchange_connections/providers/registry.py:15-18` registers both `"coinbase_advanced"` and `"kraken_spot"` today, and Kraken is in fact the platform's **primary** production execution provider (per `docs/PROJECT_STATUS.md`'s Execution Provider Status section and `00_PROJECT_STATE.md`), not merely "additive."

**Recommended documentation change:** Update the provider-registration status to reflect both providers as registered, with Kraken noted as primary and Coinbase as secondary (per `docs/PROJECT_STATUS.md`'s own, more current framing).

**Severity:** High (a specific, checkable factual claim about what's registered in a two-line registry, and it's simply wrong).

---

## 3. Medium Severity

### 3.1 `CAPITAL_CAMPAIGNS.md` — documents only one of two parallel campaign-governing layers, and its "no live automation" claim no longer holds platform-wide

**Current description:** Documents the `capital_campaigns` (plural) CRUD/lifecycle service and explicitly states the feature "does not enable live automation... does not execute withdrawals or transfers."

**Actual implementation:** A second, larger, undocumented service layer — `apps/api/app/services/capital_campaign_domain/` (singular) plus `capital_campaign_orchestration/` — governs the *same* `capital_campaigns` table via a "runtime pin" reconciliation mechanism (`capital_campaign_domain/service.py::_ensure_runtime_campaign_pin`), and it is this layer, via `commissioned_entry_execution.py`, that submits real live orders. The doc's "no live automation" claim is accurate only for the narrower CRUD layer it actually describes, not for the campaign concept as a whole.

**Recommended documentation change:** Add a note distinguishing the documented `capital_campaigns` CRUD layer from the `capital_campaign_domain`/`capital_campaign_orchestration` governance layer that actually drives live execution, and correct the "no live automation" claim to be explicitly scoped to the CRUD layer only, with a pointer to where live-execution governance is actually documented (`00_OPERATIONS_MAP.md`, `CONTROLLED_PROOF_ACTIVATION.md`).

**Severity:** Medium (not false about what it documents, but silent about the more consequential half of the same responsibility).

---

### 3.2 `PROJECT_STATUS.md` — parallel, partially-conflicting "current status" document

**Current description:** Self-presents as a current project-status snapshot (Last Updated 2026-07-23), including a "Current Runtime Blocker" section describing the Risk Engine rejecting BUY proposals for reasons including "position sizing, minimum order calculations... configuration defects."

**Actual implementation:** `00_PROJECT_STATE.md` (Version 2.0, Authority: Highest, Last Updated 2026-07-25 — two days later) is the project's own designated single source of truth ("If this document conflicts with conversation history, this document is considered authoritative"). `00_PROJECT_STATE.md` and `06_NEXT_SESSION.md` show the "current blocker" has moved through several more specific rounds since 07-23 (external reconciliation, PACKAGE_ONLY SELL progression, Controlled Proof authority-propagation) and is now nothing to do with the risk-sizing theory `PROJECT_STATUS.md` still describes. Two documents both implicitly claim to be "the" current status, only one of which (`00_PROJECT_STATE.md`) declares itself authoritative.

**Recommended documentation change:** Add a banner to `PROJECT_STATUS.md` noting it predates `00_PROJECT_STATE.md`'s authoritative status and pointing readers there for current state; keep `PROJECT_STATUS.md`'s historical phase-completion record (Phases 1–9) as a useful, still-accurate summary of what shipped, since that part has not gone stale.

**Severity:** Medium.

---

### 3.3 `RISK_ENGINE.md` — MVP/paper framing vs. accurate evaluation-order mechanics

**Current description:** §1 and §5 frame the document around "paper accounts" and describe live trading as "explicitly out of MVP scope... a Horizon 2 decision."

**Actual implementation:** Confirmed in this session's market-state audit: the actual 12-step evaluation order in `apps/api/app/services/risk/risk_engine.py::evaluate_signal_risk` matches `RISK_ENGINE.md` §3's documented order almost exactly, including the "re-check minimum size after AI scaling" nuance — this is one of the few docs whose *technical content* has not drifted. The drift is confined to the framing prose (paper-account terminology, "live trading is future") layered on top of otherwise-accurate mechanics, and the document never mentions campaigns, mandates, or `campaign_authorized_notional` — a real, load-bearing field on the actual risk-evaluation request object.

**Recommended documentation change:** Update §1/§5's framing to match current reality (live trading is real, governed by campaigns/mandates) while preserving §2/§3's accurate control descriptions unchanged; add `campaign_authorized_notional` to the documented request shape.

**Severity:** Medium (high-consequence document, but the actually-dangerous-to-rely-on part — the control mechanics — is the part that's still correct).

---

### 3.4 `VENUE_INSTRUMENT_REGISTRY.md` — describes a canonical-asset/venue-instrument separation the schema structurally does not have

**Current description:** Describes a formal separation between a canonical Asset Registry ("what is the asset?") and a per-venue Instrument Registry ("how is it traded here?"), with one canonical Bitcoin having many venue-specific instrument rows.

**Actual implementation:** `apps/api/app/models/asset.py` has a single `assets` table keyed by `UniqueConstraint("symbol", "exchange")` — i.e., Bitcoin-on-Kraken and Bitcoin-on-Coinbase are two independent `Asset` rows linked only by matching symbol text, not a canonical-asset-plus-child-instruments structure. Symbol translation is handled by a hand-maintained alias dict (`asset_commissioning/service.py::_known_product_symbols`), not a registry.

**Recommended documentation change:** Mark this document explicitly as describing a target architecture not yet reflected in the schema (it is already self-labeled "Constitutional Vision," so the honest fix is ensuring that label is prominent and that no reader could mistake the described separation for something queryable today), and cross-reference the real `Asset` model's actual keying so a future implementer understands the gap precisely rather than assuming partial credit.

**Severity:** Medium.

---

## 4. Low Severity / Documentation-Only Observations

### 4.1 `PIPELINE_ARCHITECTURE.md` and `PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md` — not drift, but worth noting as *partially real*

These two documents are explicitly self-labeled "Draft for Peer Review" and "Governing implementation roadmap" respectively — forward-looking, not descriptions of current reality, so they are not "drift" in the usual sense. However, this report notes (as a positive finding, not a correction) that their Phase 0 (`docs/PHASE_0_ARCHITECTURAL_INVENTORY.md`) and Phase 1 (`apps/api/app/services/pipeline_contracts/` — `envelope.py`, `context.py`, `identifiers.py`, `serialization.py`, `btc_kraken.py`, `btc_kraken_adapters.py`, all with real commits: `abe6bc3`, `1b8afbd`, `acabea7`, `1a298eb`, `3fb3543`, and real tests) are genuinely implemented, currently consumed by exactly one production module (`apps/api/app/services/data/canonical_market_identity.py`). Neither `00_PROJECT_STATE.md` nor `02_DECISIONS.md` mentions this work at all, which is itself a minor gap in the append-only decision log's completeness, not a factual error in any document.

**Recommended documentation change:** Add a `02_DECISIONS.md` entry recording the Pipeline Contracts Phase 1 work, consistent with the log's own append-only convention, so future sessions don't have to rediscover it via `git log`.

**Severity:** Low.

### 4.2 `LEARNING_INTELLIGENCE_ARCHITECTURE.md` — governance-status inflation, not factual drift

Self-labeled "Version 1.0 (Draft)" / "Status: Architectural Vision," and makes no false "already implemented" claims (confirmed: zero matches for "implemented"/"already exists" language across all 2088 lines; zero corresponding code for its Dataset Registry, Model Registry, or Shadow Learning concepts). Its own peer review (`docs/LEARNING_INTELLIGENCE_ARCHITECTURE_REVIEW.md`) already recommends demoting it to a non-binding Vision document rather than treating it as a co-equal "governing dependency" of `docs/PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md` — the same disposition this session's earlier `MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md` recommended for a structurally similar document. This report's only addition: `docs/PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md`'s own "Governing dependencies" list has not been updated to reflect its own review's recommendation.

**Recommended documentation change:** Update `PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md`'s dependency list to note `LEARNING_INTELLIGENCE_ARCHITECTURE.md`'s non-binding status per its own review, consistent with how this repo has already handled the same situation for `MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md`.

**Severity:** Low.

### 4.3 Naming collisions worth recording even where no single document is "wrong"

These are cases where multiple documents (and in one case, multiple code modules) use the same term for genuinely different things, none of which is individually false, but which collectively risk a future implementer wiring the wrong concept to the wrong consumer. Full detail and recommended resolution is in `CANONICAL_ARCHITECTURE_MAP.md` and the three new ADRs this report's companion work adds (§ADR-0018–0020); summarized here for completeness:

- **"Replay"** means at least three different things across the repo: decision-package identity replay (`services/replay/`), single-candidate counterfactual replay (`entry_intelligence/shadow_validation.py`), and not-yet-built chronological historical-market replay (the shared subject of `docs/HISTORICAL_INTELLIGENCE_PLATFORM.md`, `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md`, `docs/HISTORICAL_MARKET_REPLAY_ENGINE.md`, and `PIPELINE_ARCHITECTURE.md` §15–20). `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md` is, additionally, **not tracked in git at all** — it has never been committed, meaning it has not gone through any review process despite sitting in the working `docs/` tree alongside documents that have.
- **"Capital Allocation Engine"** means three different things: ADR-0008's unimplemented portfolio/agent hierarchy, `docs/CAPITAL_ALLOCATION_ENGINE.md`'s real, narrow Strategy Arena paper-allocation helper (`services/capital_allocation/`), and casual references to the phrase inside other docs. None of these three is Capital Campaigns.
- **"Regime classification"** means three different things: `AI_LAYER.md`'s undocumented-because-nonexistent AI classifier, `STRATEGY_ENGINE.md`'s dormant `trend_regime_filter.py`, and the real, live, production `classify_regime_labels` in `strategy_outcomes/service.py`.
- **Provider/venue "intelligence"** (`VENUE_INTELLIGENCE_ENGINE.md`) is a near-verbatim restatement of aspirational sections already present in both `EXECUTION_ROUTING.md` and `EXECUTION_QUALITY.md` — three documents, one unbuilt idea, zero implementation.
- **"Decision Intelligence"** as a phrase denotes both the full, documented Decision Intelligence Engine (`services/decisions/` + `DECISION_INTELLIGENCE_ENGINE.md`) and a narrower, unrelated `services/decision_intelligence/` (singular) module that ranks strategies by replay-quality — related but architecturally distinct, easily conflated by name alone.

**Severity:** Low individually; Medium in aggregate, since the pattern (the same phrase reused for unrelated things) recurs often enough to be a systemic documentation-process issue rather than five unrelated coincidences.

---

## 5. Documents Confirmed Accurate (No Action Needed)

Recorded here so a future audit does not re-spend effort re-verifying these:

- `00_PROJECT_STATE.md`, `02_DECISIONS.md`, `06_NEXT_SESSION.md`, `00_OPERATIONS_MAP.md` — actively maintained, internally consistent, already use verification tags (`00_OPERATIONS_MAP.md`) or explicit authority framing (`00_PROJECT_STATE.md`) to manage their own staleness risk. These are the correct "what's true right now" entry points and should remain the first documents any future session reads.
- `PROJECT_CONSTITUTION.md` — principles-level document; nothing in it is falsified by current code (its Git Branch Invariant and API-First Operations Principle were both spot-checked and hold).
- `MASTER_PRODUCT_ROADMAP.md` — internally consistent with `00_PROJECT_STATE.md` and the ADR set; already correctly notes Phase 9 is "controlled operational infrastructure, not immediate autonomous deployment authorization," which is the accurate current framing `PROJECT_VISION.md` (§1.2 above) lacks.
- `RISK_ENGINE.md` §2/§3 (control mechanics, evaluation order) — see 3.3; the mechanics are accurate even though the framing is stale.
- All 17 ADRs — reviewed in full. All are internally honest about their own scope (several explicitly flag themselves as "scaffolding ahead of consumers," e.g. ADR-0012, ADR-0015, ADR-0016), and none makes a false claim about current implementation state.
- `WORLD_STATE_AND_KNOWLEDGE_MODEL.md` — self-labeled "architecture and governance only... no code was written," and this is accurate; its `[VERIFIED]` tags check out on inspection.
- `docs/DECISION_INTELLIGENCE_ENGINE.md`, `docs/entry_intelligence`-adjacent decision layer docs — the market-state audit already confirmed these against code in detail; no new drift found.
