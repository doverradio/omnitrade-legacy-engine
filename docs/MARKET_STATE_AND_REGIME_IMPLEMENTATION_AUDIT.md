# MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md

## Repository Audit: What Already Exists vs. What `MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` Proposes

Status: **Read-only architectural audit. No runtime behavior changed. No code, services, migrations, or APIs created.**

Audit date: 2026-08-06

Author context: This is Phase 0 ("Repository Audit and Architecture Reconciliation," §23 of the reviewed architecture document) — the only phase `docs/MARKET_STATE_REGIME_INTELLIGENCE_REVIEW.md` authorizes at this time. This document is the Phase 0 deliverable.

Method: four parallel, read-only repository investigations covering (1) market-state classification, regime-aware strategy logic, and data sources; (2) the AI layer, Decision Intelligence Engine, and Replay Engine; (3) the Risk Engine and database/persistence; (4) API routes, frontend UI, and the root-level `strategy_lab/` package. Every finding below is grounded in a specific `file:line` in this checkout, not inference.

---

# Executive Summary

The repository already implements a meaningful fraction of what `MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` proposes — but scattered across at least four independent code paths that were never designed as one subsystem, under names that don't say "regime" or "market state," and in most cases exposed to nothing (no API, no UI). The single most important finding is negative: **building the proposed `apps/api/app/services/market_state/` tree today would not be the first regime-classification system in this repository — it would be the fourth.** Three independent, non-reconciled deterministic trend/volatility classifiers already exist (`trend_regime_filter.py`, `strategy_outcomes/service.py::classify_regime_labels`, and `strategy_lab/pattern_intelligence/detectors/volatility.py`), each with different math, different thresholds, and no shared code.

What is real and production-validated: `00_PROJECT_STATE.md`'s claim "Regime-aware strategy weighting is production-validated" checks out — `strategy_roster/decision_aggregator.py::_strategy_weight` genuinely nudges strategy ensemble weight ±0.1 (clamped to [0.5, 1.5]) based on a deterministic regime classification (`classify_regime_labels`), gated by a minimum-evidence threshold, and is unit-tested. Decision Records and Decision Snapshots — the persistence backbone the new architecture would need — are **already fully built and migrated** (2026-07-06), including all five version-pin fields, market-regime JSONB columns, and feature/indicator snapshots, contrary to `DATABASE_SCHEMA.md` §9 and `DECISION_INTELLIGENCE_ENGINE.md` §9, which still describe these as future work. The Counterfactual Outcome Ledger and Decision Quality Engine are also fully built, matching their documented V1 scope closely. The Risk Engine already has a structurally correct, bounded, downward-only confidence-scaling hook (`RiskEvaluationContext.ai_scaled_quantity`) — it is simply never populated by any live call site today, which is the exact seam future confidence/regime sizing work should use rather than building a second gate.

What is genuinely absent, with no prior art: liquidity-state classification, any Hidden Markov Model or transition-probability machinery, multi-timeframe regime synthesis, a walk-forward/no-lookahead-clock framework, a calibration-curve mechanism (only single scalar "calibration" values exist today), a strategy-regime compatibility *table* (only an evidence-weighting nudge exists), and essentially all Phase B/C data sources from the new document (order-book depth, funding rates, open interest, ETF flows, on-chain data, news, macro data, cross-asset correlation). No API route or frontend component renders anything regime/market-state-related anywhere in the product today.

Two governing documents have drifted materially from the code they describe: `AI_LAYER.md` §2.1 documents an AI/ML regime classifier that does not exist anywhere in the codebase (the two real writers of `signals.regime_tag` write a cycle-interval label and `None`, never a regime), and `STRATEGY_ENGINE.md`'s `Strategy`/`Signal` protocol no longer matches the real `apps/api/app/services/strategies/base.py` implementation (argument count, pydantic vs. dataclass, tuple-of-mappings vs. pandas DataFrame). This drift predates the new architecture document and should be corrected independent of any decision about the new document's fate.

Recommendation: no new market-state service tree, no new database tables, and no new API/UI surfaces are warranted right now. The `market_regime`/`regime_tag` fields present across `decision_records`, `decision_snapshots`, and `signals` are real, already migrated, and structurally ready to receive richer classifier output without a schema change — the work that would matter next is *consolidation* (reconciling the three existing classifiers and the stale docs describing them), not *addition*. A concrete, small, evidence-grounded Phase 1 scope is proposed at the end of this document.

---

# Existing Capabilities

## 1. Market State Classification

| Capability | Path | Status |
|---|---|---|
| Deterministic trend classifier (ADX-proxy + MA slope) | `apps/api/app/services/strategies/trend_regime_filter.py:16-78` | **FULLY IMPLEMENTED**, but registered only in the general strategy registry (`strategies/builtins.py:25`) and **excluded from the live production roster** (`strategy_roster/registry.py:7-15`, `ENABLED_PHASE1_ROSTER`). Used only for standalone backtests (`api/routes/backtests.py:217-220`). |
| Deterministic volatility-band filter (ATR-based) | `apps/api/app/services/strategies/volatility_filter.py:16-65` | **FULLY IMPLEMENTED**, same roster-exclusion as above. |
| The actual production regime classifier | `apps/api/app/services/strategy_outcomes/service.py:153-193` (`classify_regime_labels`) | **FULLY IMPLEMENTED and live.** Produces three independent axes from one OHLC window: `trend` (`TRENDING`/`RANGING`, net-return threshold 0.60%, line 169), `volatility` (`HIGH_VOLATILITY`/`LOW_VOLATILITY`, population-stdev threshold 0.004, lines 171-178), `range` (`EXPANSION`/`COMPRESSION`, first-half vs. second-half range comparison, lines 180-192). DB-enforced via `CheckConstraint` (`strategy_roster_proposal_outcome.py:26-28`, `strategy_roster_run.py:24-26`). |
| Research-tool volatility "finding" detector | `strategy_lab/pattern_intelligence/detectors/volatility.py:1-37` | **FULLY IMPLEMENTED**, but scoped to the offline research/pattern-mining tool (feeds `research_copilot`), uses percentile-band math unrelated to either production classifier above. |
| Ranging/sideways detection | Subsumed by `classify_regime_labels`'s `trend` axis (`RANGING`) | **PARTIALLY IMPLEMENTED** — a binary trend/range split exists; the new doc's richer ranging characterization (§4.2: range boundaries, trend-strength evidence, confidence) does not. |
| Liquidity-state classification | — | **MISSING.** No liquidity-state or participation-state classifier exists anywhere in the repository. |
| Regime tagging on records | `Signal.regime_tag` (`models/signal.py:36`), `DecisionRecord.market_regime` / `DecisionSnapshot.market_regime` (JSONB) | **PARTIALLY IMPLEMENTED / field overloaded** — see Documentation Drift and Duplicate Functionality sections. Only one call site (`capital_campaign_orchestration/authoritative.py:1567-1570`) writes genuine regime data into `market_regime`; other writers use the same field for execution-price-evidence provenance or inert placeholders. |
| Hidden Markov Model / transition probabilities | — | **MISSING entirely.** Zero occurrences of `hmm`, "hidden markov," "transition matrix," or "viterbi" anywhere in `apps/api/`, `strategy_lab/`, or `tools/`. **DOCUMENTED ONLY** (in the new architecture doc itself). |
| Multi-timeframe regime synthesis | — | **MISSING.** Every existing classifier operates on a single interval/window; nothing combines regime state across timeframes. |

## 2. Regime-Aware Strategy Logic

- **Production regime-aware weighting** (the mechanism behind `00_PROJECT_STATE.md:296`'s "Regime-aware strategy weighting is production-validated" claim): `apps/api/app/services/strategy_roster/decision_aggregator.py:204-234` (`_strategy_weight`). A strategy's ensemble weight is nudged by `_REGIME_WEIGHT_ADJUSTMENT = Decimal("0.1")` (line 32) up or down based on whether the current `current_regime_trend` (persisted on `StrategyRosterRun`, `models/strategy_roster_run.py:52`, migration `20260725_0049`) matches the strategy's historically best/worst regime (`StrategyOutcomeSummary.best_regime`/`worst_regime`, `strategy_outcomes/service.py:722-731`), gated by a minimum-evidence threshold (`config.py:234`, default 50 evaluations) and clamped to `[0.5, 1.5]`. Unit-tested: `tests/unit/services/strategy_roster/test_decision_aggregator.py:456-500`. Operator-visible via `operator_cli/formatting.py:806-816`.
  - **Status: FULLY IMPLEMENTED, tested, and the project-state claim is accurate** — but narrower than the new architecture doc implies: it is a bounded evidence-based *nudge*, not compatibility-table-driven filtering, and it uses only the `trend` axis of the three captured regime axes (`volatility`/`range` are persisted per-outcome-row but not yet used to adjust weight).
- **Regime-based strategy filtering/exclusion** (the doc's "suppress incompatible proposals" mode, §12.1): **MISSING** in production. `trend_regime_filter.py` is shaped like a filter but is not wired into the live roster (see above).
- **Strategy-regime compatibility table**: no static or learned strategy↔regime compatibility mapping exists. The closest analogue is `StrategyScorecard.regime_conditioned_buckets` (`strategy_outcomes/service.py:91-103, 693-731`), keyed `[horizon_label][regime_trend]` only — see Duplicate Functionality for a detailed dimension-by-dimension comparison against the new doc's §12.2 compatibility table.
- **Entry-intelligence regime-conditioned evidence hierarchy**: `apps/api/app/services/entry_intelligence/evidence.py:1-207` (`resolve_context_specific_edge_evidence`) — a 4-tier fallback (strategy+asset+timeframe+regime → strategy+asset+timeframe → strategy+asset aggregate → unavailable) with real uncertainty quantification (standard error, z-scaled uncertainty penalty, `missing_input_flags` for fail-closed behavior, lines 100-134). **FULLY IMPLEMENTED**, but scoped narrowly: it only ever runs after the existing net-edge gate has already rejected a market-entry BUY (module docstring, `authoritative.py` call site), and produces an edge-estimate confidence interval, not a general regime-confidence score.

## 3. AI Layer Capabilities (Confidence, Explanation, Probability, Evidence Aggregation)

- **Confidence**: exactly one blended scalar exists in the primary decision path — `DecisionRecord.confidence` (`models/decision_record.py:37`), `Signal.ai_confidence` (`models/signal.py:35`). The new doc's §13.1 seven-way confidence taxonomy (state confidence / regime posterior / transition confidence / data-quality confidence / evidence-agreement score / compatibility confidence / final decision confidence) has **zero code precedent** — confirmed by full-repo grep for each term. The closest thing to a confidence *decomposition* that does exist is the Decision Quality Engine's 7 weighted component scores (`decisions/quality.py:24-32`) — but that is a **post-hoc quality grade of an already-made decision**, not a pre-decision confidence breakdown, and answers a different question.
- **Explanation generation**: real and working, but template-based, not free-text/LLM. `ai_coach/deterministic.py:12-96` (`evaluate_decision_quality_v0`) and `decisions/explainability.py:42-392` (`_decision_explanation_text`) both select from a fixed set of template sentences via boolean branching. **FULLY IMPLEMENTED** as deterministic templating; narrowly scoped (coach explains replay-quality; explainability explains a decision's evidence roles) rather than a general market-state explanation generator.
- **Probability estimation**: `entry_intelligence/evidence.py` produces a mean historical return ± an uncertainty band (a frequentist confidence interval on an expected-return estimate) — **not** a probability of a discrete regime/state, which is what the new doc's §6.3 `HiddenRegimeResult.regime_probabilities` calls for. **MISSING** in the sense the new doc needs it.
- **Evidence aggregation**: exists, but reinvented three separate times with no shared abstraction — `entry_intelligence/evidence.py` (tiered historical-mean + uncertainty), `decisions/quality.py::build_decision_quality_score_draft` (weighted sum of 7 components), and `strategy_roster/decision_aggregator.py::aggregate_strategy_proposals` (evidence-weighted multi-strategy proposal blending). Each independently reimplements sample-size gating and staleness checks. **PARTIALLY IMPLEMENTED / fragmented** — see Duplicate Functionality.
- **Model versioning**: **FULLY IMPLEMENTED**, and more granular than `AI_LAYER.md` §6 asks for. `ModelOutput.model_version` (`models/model_output.py:23`), `DecisionSnapshot.ai_model_version` plus four sibling version fields (`decision_snapshot.py:37-41`), `DecisionQualityScore.scoring_model_version` (`decision_quality_score.py:32`) are all non-nullable, schema-enforced (migrations `20260706_0004a`, `20260706_0007`, `20260706_0011`), and explicit sentinel values (`"none"`, `"unknown"`, `"not_applicable"`) are used rather than silently omitting the field. Any new market-state/regime model versioning should reuse this exact pattern.

## 4. Replay Engine

- **Deterministic package replay**: `apps/api/app/services/decisions/replay_candidates.py:47-107` (`certify_decision_package_readiness_v0`) builds a decision package twice and requires content-hash equality (`content_hash`, `decisions/package.py:273`) before certifying `replay_ready`. **FULLY IMPLEMENTED** — a genuine, working determinism guarantee.
- **Decision-package replay agent**: `apps/api/app/services/replay/default_agent.py:22-58` reconstructs an action/confidence from an already-persisted, immutable `DecisionPackageContract`. Exposed via `POST /arena/replay` (`api/routes/arena.py:152`). This replays **one already-made decision** for audit/consistency purposes — it does not re-run any model against re-fetched market data.
- **Shadow counterfactual replay**: `entry_intelligence/shadow_validation.py:106-197` (`replay_rejected_buy_candidate_counterfactual`) replays a single hypothetical BUY_LIMIT candidate against stored historical candles, computing fill/PnL outcomes at 5 fixed horizons (15m/30m/1h/2h/4h). **PARTIALLY IMPLEMENTED** — real and tested, but single-candidate, offline/manual invocation only (no scheduled job), and a documented gap (`strategy_native_exit` always `None`).
- **No-lookahead clock**: **MISSING.** Zero occurrences of "lookahead"/"look-ahead" anywhere in `apps/api/`. Implicit, per-call bounded-window reads exist in `shadow_validation.py` and `decisions/counterfactuals.py`, but no reusable clock abstraction exists that other code must go through.
- **Walk-forward evaluation** (train window → evaluate window → advance, with retraining policy): **MISSING entirely.** Zero occurrences of "walk-forward" or "walk forward" anywhere in the repository outside the new architecture document itself.
- **Historical state reconstruction**: `DecisionSnapshot` (§ below) enables replaying a *stored* decision's exact context, but nothing reconstructs regime/indicator state at an arbitrary past timestamp not already captured in a snapshot. **PARTIALLY IMPLEMENTED.**
- **Duplication note**: four differently-scoped "replay-adjacent" mechanisms exist under different names and do not share an interface: `replay/` (identity/audit replay of a persisted decision), `entry_intelligence/shadow_validation.py` (what-if counterfactual replay of a hypothetical candidate), the Counterfactual Outcome Ledger (`decisions/counterfactuals.py`, horizon-based re-evaluation of realized decisions), and `decision_quality/` (replay-quality scoring). See Duplicate Functionality.

## 5. Decision Intelligence Engine (vs. `DECISION_INTELLIGENCE_ENGINE.md`)

| Documented subsystem | Doc §§ | Code | Status |
|---|---|---|---|
| Decision Record | §4 | `models/decision_record.py:15-95` | **FULLY IMPLEMENTED** — near-verbatim field match, append-only enforced via SQLAlchemy event listeners (lines 87-94). |
| Decision Snapshot | §4a | `models/decision_snapshot.py:14-53` | **FULLY IMPLEMENTED** — 1:1 with Decision Record, immutable, all 5 mandatory version-pin fields present. |
| Explainability Layer | §5 | `models/decision_explainability_record.py`, `decisions/explainability.py:42-392` | **FULLY IMPLEMENTED** — role-tagged evidence (`supporting`/`opposing`/`confidence_factor`/`risk_adjustment`), explicit `availability_state` rather than fabricated evidence. |
| Counterfactual Outcome Ledger (COL) | §8 | `models/decision_counterfactual_result.py`, `decisions/counterfactuals.py:52-364` | **FULLY IMPLEMENTED, matching the documented V1 scope precisely** — BTC-only filter, horizons hardcoded to exactly the documented V1 subset (15m/1h/24h), shadow BUY/SELL/WAIT tracked regardless of actual action, lesson tags matching §8.5's list almost verbatim. |
| Decision Quality Engine (DQE) | §8a | `models/decision_quality_score.py`, `decisions/quality.py:63-388` | **FULLY IMPLEMENTED** — 7 weighted component scores, content-hashed idempotency key, full provenance lineage, separate `scoring_model_version` axis. |

**Important drift finding**: `DATABASE_SCHEMA.md` §9 and `DECISION_INTELLIGENCE_ENGINE.md` §9 both still describe Decision Records/Snapshots as future, not-yet-built tables. **They were migrated on 2026-07-06** (`db/migrations/versions/20260706_0007_add_decision_record_snapshot_tables.py`), roughly a month before this audit. This is exactly the kind of "docs claim future work that's already built" gap the new architecture document's own Phase 0 was designed to catch — see Documentation Drift.

## 6. Risk Engine

- **Evaluation order**: `apps/api/app/services/risk/risk_engine.py:528-838` (`evaluate_signal_risk`) implements a 12-step gate (kill switch → account pause → no-trade zone → cooldown → daily loss → drawdown → stop-loss → position-size resize → min-viable-order pre-AI → AI confidence scaling → min-viable-order post-AI → approve/resize). This **matches `RISK_ENGINE.md` §3's documented 11-step order almost exactly**, including the "re-check minimum size after AI scaling" nuance — one of the few docs in this repo that has *not* structurally drifted from the code, even though its framing (paper accounts, live trading as future work) has (see Documentation Drift).
- **Position sizing / authority boundaries**: `compute_position_sizing()` (`risk_engine.py:158-287`) enforces `max_notional = equity × max_position_size_pct`, rescues (never fabricates) up to a minimum viable order within a bounded ceiling. `campaign_authorized_notional` (`RiskEvaluationRequest`, line 29) is real, separately-governed authority-boundary plumbing from the campaign/mandate layer. Confirmed **no production call site ever sets `bypass_sizing_rule=True`** — the sizing gate is never bypassed anywhere in live code. **FULLY IMPLEMENTED.**
- **Confidence-aware sizing — the key finding**: `RiskEvaluationContext.ai_scaled_quantity` (`risk_engine.py:77`) is a structurally correct, bounded, **downward-only** clamp (`if ai_scaled_quantity is not None and ai_scaled_quantity < approved_quantity: approved_quantity = ai_scaled_quantity`, lines 777-780) — architecturally exactly what the new doc's §14.1 sizing rule requires, and it lives *inside* the Risk Engine (the safer of the two possible designs), not upstream of it. **However, every single production call site constructs its `RiskEvaluationContext` without ever setting this field**, so it is always `None` and the step is always a no-op. `RiskEvaluationRequest.ai_confidence` similarly exists on the request dataclass but `evaluate_signal_risk()` never reads it.
  - **Status: PARTIALLY IMPLEMENTED — a correctly-bounded scaffold exists; the confidence→multiplier computation that would populate it does not exist anywhere.** This is the single cleanest integration point identified in this audit: any future regime/confidence sizing work is a matter of computing and wiring a value into an existing hook, not building a new gate.
- **Kill switches, daily loss, drawdown, cooldown, no-trade zones**: all **FULLY IMPLEMENTED**, matching `RISK_ENGINE.md` §2.2–2.8, persisted via `risk_persistence.py:115-167` to `RiskEvent` + `AuditLog` per decision, matching §1's audit requirement.

## 7. Database / Persistence

| Concern | Existing location | Status |
|---|---|---|
| Regime storage | `Signal.regime_tag` (Text), `DecisionRecord.market_regime` / `DecisionSnapshot.market_regime` (JSONB), `StrategyRosterRun.current_regime_trend` (Text, CHECK), `StrategyRosterProposalOutcome.regime_trend/regime_volatility/regime_range` (Text, CHECK) | **FULLY IMPLEMENTED**, across 4 different tables — closest single analogue to the new doc's `DeterministicStateResult` (§6.2) is `StrategyRosterProposalOutcome`'s 3-axis regime columns. |
| AI/model output storage | `ModelOutput` (`model_name`, `model_version`, `input_summary` JSONB, `output` JSONB) | **FULLY IMPLEMENTED** as a generic store — structurally capable of holding Deterministic/Hidden-Regime-Result-shaped rows without a new table, given its existing `model_name`/`model_version`/`output` JSONB shape. |
| Confidence storage | `Signal.ai_confidence`, `DecisionRecord.confidence`, `StrategyRosterProposal.confidence`, `DecisionExperimentRecommendation.confidence_level` (categorical) | **FULLY IMPLEMENTED** as scalar/categorical values; no calibration-curve-shaped storage exists (see Missing Capabilities). |
| Replay evidence storage | `ResearchCandidateEvaluation.replay_status`, `ArenaTournamentHistoryRecord.replay_metadata` (JSONB, append-only, immutable, with `event_hash`/`provenance`) | **PARTIALLY IMPLEMENTED** — `ArenaTournamentHistoryRecord` is the strongest existing precedent for an immutable, versioned evidence-ledger shape the new doc's §21 tables would need, but no table matches "Market Evidence Observation" (§6.1) as such. |
| Model versioning | See AI Layer §3 above | **FULLY IMPLEMENTED**, multi-axis. |

## 8. Strategy Outcomes / Scorecards (closest existing analogue to §12.2 Compatibility Table)

`apps/api/app/services/strategy_outcomes/service.py` — `score_due_strategy_roster_proposal_outcomes()` (lines 226-419) and `fetch_strategy_scorecards()` (lines 562-760), backed by `StrategyRosterProposalOutcome` (append-only). Produces `StrategyScorecard` objects with per-horizon buckets, action-scoped average returns, and **action-scoped sample standard deviation** (`buy/sell/hold_raw_return_stdev_pct`, Bessel-corrected, never fabricated below n=2 — `_sample_stdev()`, lines 523-534) — a real, working precedent for the new doc's §13.1 "strategy-regime compatibility confidence."

Dimension-by-dimension comparison against §12.2's proposed compatibility key (strategy × parameter-set × asset × timeframe × venue × state/regime × holding horizon):

| §12.2 dimension | Existing coverage |
|---|---|
| strategy | ✅ `strategy_slug` |
| parameter-set version | ⚠️ approximated by `strategy_identity`, not an explicit parameter-set-version column |
| asset | ✅ `product_id` (outer query filter, one per call — not a joint grouping key) |
| timeframe | ✅ `interval` (same caveat) |
| venue | ✅ `provider` (same caveat) |
| state/regime | ⚠️ only `regime_trend` is used for conditioning (`regime_conditioned_buckets`); `regime_volatility`/`regime_range` are captured on every row but unused in bucketing |
| holding horizon | ✅ `horizon_label`/`horizon_minutes`, but fixed to 4 hardcoded horizons, not an open axis |

**Status: PARTIALLY IMPLEMENTED.** This is unambiguously the closest existing analogue to §6.5/§12.2 of the new document. Extending it (adding a parameter-set-version column, using all 3 captured regime axes, widening the fetch to group jointly rather than filter-per-call) is far more consistent with the codebase's own precedent than a new table.

## 9. Data Sources

| Data type | Status | Evidence |
|---|---|---|
| OHLCV | **FULLY IMPLEMENTED**, live | `models/candle.py:14-38`; `services/data/binance_client.py`, `kraken_client.py`; scheduled ingestion via `services/data/worker_entrypoint.py` |
| Volume | **FULLY IMPLEMENTED** | Part of every OHLCV bar (`Candle.volume`) |
| Trades (executed, market-wide) | **PARTIALLY IMPLEMENTED** | Only own-account trade reconciliation exists (`exchange_connections/service.py:1097`); no market-wide trade-tape feed |
| Order book / spread | **PARTIALLY IMPLEMENTED** | Single on-demand top-of-book quote only (`ExchangePriceEvidence`, `exchange_connections/providers/base.py:99-119`) — not a depth feed, not persisted as a time series, not used for liquidity classification |
| Funding rates | **MISSING** | Zero occurrences of `funding_rate` |
| Open interest | **MISSING** | Zero occurrences of `open_interest` |
| ETF flow data | **MISSING** | Zero occurrences |
| On-chain data | **MISSING** | Zero occurrences |
| News / sentiment | **MISSING** | Zero occurrences |
| Macro data | **MISSING** | Zero occurrences |
| Cross-asset data | **MISSING** | Assets handled entirely independently; no cross-asset correlation service |

Everything in the new document's Phase B (§5.2) and Phase C (§5.3) evidence menus is **genuinely new territory**, not duplication — unlike the market-state classification work in §1 above, there is no existing code to reconcile with here.

## 10. API and Frontend

- **No literal `market_state`/`market-state` string exists anywhere in the repository outside the new architecture document** (confirmed by full-repo grep).
- None of the seven proposed `/market-state/*` endpoints exist under that name. The closest analogues, all **unexposed at the API layer today**:
  - `GET /market-state/current` ↔ `StrategyRosterRun.current_regime_trend` — service-layer only, no route.
  - `GET /market-state/strategy-compatibility` ↔ `regime_conditioned_buckets` scorecard — service-layer only, no route (`grep` for `StrategyScorecard`/`regime_conditioned_buckets` across `api/routes/` and `schemas/` returns zero hits).
  - `GET /market-state/replay/:run_id` ↔ `POST /arena/replay` (`api/routes/arena.py:152`) — real endpoint, but replays a decision package, not a regime-model run.
  - `GET /market-state/models`, `/models/:version/diagnostics` ↔ `ModelOutput.model_name/model_version` — a generic log, not a model registry with promotion/diagnostics; no route exposes it that way.
  - Confidence calibration: exists only as a single scalar tile (`decision-arena/page.tsx:448`, `schemas/decision_quality.py:45`), never a chart or reliability diagram.
- **Frontend**: no page or component renders a regime/market-state indicator, calibration chart, compatibility matrix, transition-matrix viewer, walk-forward results, or model-promotion status for any regime model. `apps/web/components/strategy-lab/ReplayChart.tsx` (price/trade/order visualization) and the strategy-lab branch-comparison/`promotion_status` UI (`StrategyCreationWizard.tsx`, `lib/api/strategyLabOffline.ts:276`) are the closest *structural* precedents to reuse for future regime UI — but they currently visualize trading-rule candidates, not regime models.
- **`strategy_lab/` (repo root) is not a duplicate implementation** — it is the real, imported implementation that thin `apps/api/app/services/*.py` wrappers call into (confirmed via direct imports in `services/rule_discovery.py`, `services/pattern_intelligence.py`, `services/research_copilot.py`, `services/strategy_lab_offline.py`). This is an established, working repository convention: a framework-independent deterministic engine at repo-root, wrapped by a thin FastAPI-facing service. Any new market-state work should follow or consciously extend this precedent rather than invent a third placement convention (repo-root package vs. `apps/api/app/services/X` vs. a wholly new `apps/api/app/services/market_state/` tree).

---

# Missing Capabilities

No prior art exists anywhere in the repository for:

1. **Liquidity-state classification** (§4.1, §7.1 of the new doc).
2. **Hidden Markov Model / any learned regime model**, including transition matrices, state persistence estimates, or label-switching diagnostics (§8, §9).
3. **Multi-timeframe regime synthesis / alignment scoring** (§10).
4. **A walk-forward evaluation framework** — train window → evaluate window → advance, with purge/embargo (§15).
5. **A no-lookahead clock abstraction** usable across services (§15.3) — only implicit, per-call bounded reads exist today.
6. **A calibration-curve / reliability-diagram mechanism** (§13.2) — only single scalar "calibration" values exist.
7. **The seven-way decomposed confidence taxonomy** of §13.1 — the repo has exactly one blended confidence scalar plus a structurally unrelated 7-component post-hoc quality score.
8. **A genuine strategy-regime compatibility table** keyed the way §12.2 proposes (joint grouping across strategy × parameter-set × asset × timeframe × venue × regime × horizon) — the existing scorecard mechanism approximates several of these dimensions as one-per-call filters, not a joint key.
9. **Nearly all Phase B/C data sources**: order-book depth, funding rates, open interest, ETF flows, on-chain data, news/sentiment, macro data, cross-asset correlation.
10. **Any API or UI surface** for regime/market-state data, confidence calibration, or model promotion status.
11. **A model-promotion lifecycle for regime models** (§17) — a promotion/eligibility concept exists (`strategy_lab/rule_discovery/replay.py::promotion_eligibility`), but it governs trading-rule candidates, not regime models, and uses a single static train/validation/final-test split rather than the doc's 10-stage lifecycle or walk-forward folds.
12. **An out-of-distribution / novelty guard** for regime inference — flagged by the prior architectural review (`MARKET_STATE_REGIME_INTELLIGENCE_REVIEW.md`, "Missing Components" §1) and confirmed absent in code.

---

# Documentation Drift

1. **`AI_LAYER.md` §2.1 ("Regime Classifier") describes code that does not exist.** No AI/ML regime classifier exists anywhere in `apps/api/app/services/ai*/`. The two real writers of `signals.regime_tag` write a cycle-interval string (`autonomous_cycle/orchestrator.py:762`) or `None` (`continuous_pipeline_worker.py:2752`) — never a trend/volatility/liquidity label with a confidence score as documented. `STRATEGY_ENGINE.md:49,66`'s references to "the AI regime classifier" validating/downweighting strategies describe a relationship that does not exist in code; the mechanism that *does* provide regime-aware weighting (`decision_aggregator.py::_strategy_weight`) was built later via an entirely different, unrelated path (`strategy_roster`/`strategy_outcomes`) and does not reference an AI layer at all.

2. **`STRATEGY_ENGINE.md`'s `Strategy`/`Signal` protocol (§1) is stale relative to `apps/api/app/services/strategies/base.py:23-116`.** Documented: `generate_signal(self, candles: pd.DataFrame, params: dict, context: StrategyContext)`, a plain dataclass `Signal`, files under `backend/strategies/`. Actual: `generate_signal(self, context: StrategyContext) -> Signal` (candles/params folded into `StrategyContext`), `Signal` is a frozen pydantic model with `Decimal` fields and validators, candles are tuples of `MappingProxyType`, not a pandas DataFrame, and files live under `apps/api/app/services/strategies/`. The contract's *spirit* (purity, mandatory `reason`/`indicators`) is honored; the literal signatures are not.

3. **`DATABASE_SCHEMA.md` §9 and `DECISION_INTELLIGENCE_ENGINE.md` §9 both describe Decision Records, Decision Snapshots, Decision Evidence, Decision Outcomes, Decision Reviews, AI Reflections, and Human Reviews as future, unbuilt tables.** In fact `decision_records` and `decision_snapshots` were migrated 2026-07-06 (`db/migrations/versions/20260706_0007_add_decision_record_snapshot_tables.py`) and are fully field-complete against the documented schema. Much of the remaining documented ground (evidence roles, counterfactual outcomes, quality scores, alternative actions) is covered by separate, already-built, FK-linked tables (`decision_explainability_record`, `decision_counterfactual_result`, `decision_quality_score`, `decision_alternative_action`) rather than the exact table names the docs anticipate (`Decision Evidence`, `Decision Outcomes`, etc.) — the underlying capability exists, but under different table names than either doc predicts.

4. **`RISK_ENGINE.md` §1 and §5 still frame live trading as out-of-scope future work** ("Enabling live trading is explicitly out of MVP scope... a Horizon 2 decision") and its request objects are still named for "paper accounts." This directly contradicts `00_PROJECT_STATE.md`'s proven-capabilities list (live Kraken authentication, live production BUY/SELL, live reconciliation) and the entire campaign/mandate governance layer, none of which `RISK_ENGINE.md` mentions at all. Notably, the actual **evaluation-order mechanics** in `risk_engine.py` have *not* drifted — they match `RISK_ENGINE.md` §3 closely. The drift is confined to the document's framing/prose, not its technical content.

5. **`00_PROJECT_STATE.md:296`'s "Regime-aware strategy weighting is production-validated" is accurate but narrower than a reader would likely assume** given the existence of the new architecture document alongside it — it is a bounded evidence-weighting nudge on one of three captured regime axes, not compatibility-table-driven filtering or anything resembling the new document's proposed subsystem.

6. **The new architecture document's own §28 ("Relationship to Existing OmniTrade Documents") correctly names `STRATEGY_ENGINE.md`'s trend-regime filter and `AI_LAYER.md`'s regime classifier as prior art to reconcile with** — but per Finding 1 above, `AI_LAYER.md`'s regime classifier is itself non-existent in code, so §28's premise needs correcting: the prior art to reconcile with is `classify_regime_labels` (`strategy_outcomes/service.py`) and `decision_aggregator.py`'s weighting mechanism, not anything in `AI_LAYER.md`.

---

# Duplicate Functionality

## Critical: three independent regime/volatility classifiers, no shared code

| Classifier | Path | Math | Consumer |
|---|---|---|---|
| ADX-proxy + MA slope | `strategies/trend_regime_filter.py:16-78` | Mean absolute % return as ADX proxy, threshold 25; SMA slope for direction | Registered but **dormant** — excluded from the live roster |
| Net-return + stdev | `strategy_outcomes/service.py:153-193` (`classify_regime_labels`) | Net-return threshold 0.60%; population-stdev threshold 0.004; first/second-half range comparison | **Live production** — drives `current_regime_trend` and strategy weighting |
| Percentile-band | `strategy_lab/pattern_intelligence/detectors/volatility.py:1-37` | Rolling 20th/80th percentile volatility bands | Offline research tool (`research_copilot`) only |

None share thresholds, constants, or code, and nothing in the repository declares one authoritative over the others. **A new `apps/api/app/services/market_state/` deterministic classifier, as proposed in §20 of the new architecture document, would be a fourth independent implementation of the same concept** unless it explicitly supersedes or consolidates these three. This is the single highest-value consolidation opportunity this audit identified.

## Replay-adjacent mechanisms — four different things named "replay"

`replay/` (decision-package identity/audit replay) · `entry_intelligence/shadow_validation.py` (hypothetical-candidate counterfactual replay) · `decisions/counterfactuals.py` (COL horizon-based shadow-outcome re-evaluation) · `decision_quality/` (replay-quality scoring). None implement a common interface; `shadow_validation.py` does not implement `replay/interface.py`'s `ReplayAgent` Protocol. This is a naming collision more than functional duplication, but any unified "Replay Engine" (as the new document's §15 envisions) would need to explicitly reconcile with — or absorb — all four rather than adding a fifth.

## Evidence-aggregation logic reinvented three times

`entry_intelligence/evidence.py::resolve_context_specific_edge_evidence` (tiered mean + uncertainty band) · `decisions/quality.py::build_decision_quality_score_draft` (7-component weighted sum) · `strategy_roster/decision_aggregator.py::aggregate_strategy_proposals` (evidence-weighted proposal blending). Each independently reimplements sample-size gating and staleness/freshness checks with no shared abstraction. This is the concrete manifestation of the risk the new document's own §11.2 ("agreement is not a vote count") is worried about, at the level of the existing codebase rather than a future one.

## Naming collision: `services/decision_intelligence/` vs. the documented "Decision Intelligence Engine"

`apps/api/app/services/decision_intelligence/deterministic.py:23-95` is a narrow subsystem that ranks strategies by deterministic replay-quality (with a "replay variance" tie-break). This is a **different, smaller thing** than what `DECISION_INTELLIGENCE_ENGINE.md` calls the "Decision Intelligence Engine" — the full Decision Record/Snapshot/Explainability/COL/DQE schema, which actually lives under `services/decisions/` and `models/decision_*.py`. The two are related (the former consumes the latter's output) but an auditor unfamiliar with the code could easily conflate them by name alone.

## Precedent, not duplication: `strategy_lab/` (repo root)

`strategy_lab/` is confirmed to be the real, imported deterministic engine behind several `apps/api/app/services/*.py` thin wrappers (`rule_discovery.py`, `pattern_intelligence.py`, `research_copilot.py`, `strategy_lab_offline.py`), not a parallel/competing implementation. It establishes a real repository convention — framework-independent engine at repo-root, thin FastAPI wrapper in `apps/api/app/services/` — that a new market-state subsystem should consciously follow or deviate from, not ignore.

---

# Architectural Risks

1. **Building the proposed `market_state/` tree without first resolving the triple-classifier duplication would make the fragmentation permanent and harder to unwind**, not better. Every additional regime-labeling code path increases the chance that different parts of the system disagree about "what regime is this" without anyone noticing, since nothing today compares the three existing classifiers' outputs against each other.

2. **The `market_regime`/`regime_tag` fields are unreliable as regime carriers today.** They are reused across multiple call sites for structurally unrelated data (execution-price-evidence provenance, inert placeholders, a cycle-interval label) alongside the one call site that carries genuine regime state. Any future work must not assume "field is populated" implies "field contains a trend/volatility/liquidity classification" — each writer needs individual verification.

3. **The new document's proposed seven-way confidence taxonomy (§13.1) and compatibility table (§12.2) both have combinatorial data requirements the platform's actual evidence volume cannot support yet** — this is not a new risk this audit discovered; it is the dominant finding of the prior architectural review (`MARKET_STATE_REGIME_INTELLIGENCE_REVIEW.md`, Weaknesses #2–#3), and this audit's evidence corroborates it directly: the closest existing analogue (`StrategyScorecard`) already gates every bucket behind a minimum-evidence threshold (default 50) and still only manages to populate one of three captured regime axes in practice.

4. **The Risk Engine's dormant `ai_scaled_quantity` hook is safe exactly because nothing populates it.** Any future work wiring a confidence/regime multiplier into it must preserve its existing downward-only, `min()`-based clamp semantics (`risk_engine.py:777-780`) exactly — this is a correctness-critical boundary, not a convenience API, and the new document's own §14.1/§14.2 rules (bounded [0,1], downward-only) already match what's structurally there. The risk is in a future implementation accidentally changing the clamp direction or bypassing it, not in the current dormant state.

5. **No walk-forward or no-lookahead-clock framework exists anywhere**, which the new document treats as a prerequisite (§15.1: "No state classifier, HMM, compatibility policy, transition model, or confidence model may be promoted based only on performance measured on the same data used to design or train it"). Any Phase 2+ work from the new document is blocked on building this framework first — it cannot be shortcut by reusing an existing walk-forward mechanism, because none exists.

6. **Data source coverage is thin outside OHLCV/volume.** Even the "Phase A — native and immediately accessible" evidence list in the new document (§5.1) includes spread and liquidity data that only exists today as an on-demand top-of-book quote, not a persisted or classified feed. Phases B and C are not merely unbuilt — they would require entirely new ingestion, storage, and reliability engineering (per `DATA_SOURCES.md`'s general provider-reliability concerns), which is a materially larger undertaking than the classification/modeling work the new document spends most of its length on.

7. **Per governing project state, the platform has not yet achieved First Autonomous Profit** (`00_PROJECT_STATE.md`), and the currently active priority is a live production defect (Controlled Proof Exit Recovery activation) per `06_NEXT_SESSION.md`. Any work beyond this Phase 0 audit competes for engineering attention with that milestone, and per the *Parallel Authorized Lanes* decision (`02_DECISIONS.md`), such work is only authorized when it is structurally isolated from the live proving campaign the way Historical Intelligence Platform Phase 3 is. Nothing audited here changes that gating; it is noted as a risk to *not* observe if further phases are considered.

---

# Recommended Repository Changes

These are recommendations for future, separately-authorized work — nothing in this list is implemented by this audit.

1. **Do not create `apps/api/app/services/market_state/` yet.** Consolidate first.
2. **Resolve the triple-classifier duplication** (`trend_regime_filter.py`, `classify_regime_labels`, `strategy_lab`'s volatility detector) — either via an ADR designating one authoritative production classifier and explicitly scoping the other two (e.g., "research-only," "backtest-only"), or by unifying their math. `classify_regime_labels` is the natural authoritative candidate since it is the one already live and unit-tested in production.
3. **Correct `AI_LAYER.md` §2.1** to reflect that no AI/ML regime classifier exists, and either remove the aspirational description or explicitly mark it `[PROPOSED]`/future, consistent with `00_OPERATIONS_MAP.md`'s verification-tag convention.
4. **Correct `STRATEGY_ENGINE.md`'s `Strategy`/`Signal` protocol section** to match `apps/api/app/services/strategies/base.py`'s actual signatures.
5. **Correct `DATABASE_SCHEMA.md` §9 and `DECISION_INTELLIGENCE_ENGINE.md` §9** to reflect that Decision Records, Decision Snapshots, Explainability, COL, and DQE tables are already built, migrated, and in production use — not future work.
6. **Correct `RISK_ENGINE.md`'s framing** (§1, §5) to reflect live-trading reality (campaigns, mandates, live Kraken execution) rather than "paper accounts" and "Horizon 2" language, while preserving its still-accurate evaluation-order documentation (§3).
7. **If/when strategy-regime compatibility work is authorized**, extend `StrategyScorecard`/`regime_conditioned_buckets` (add a parameter-set-version column, use all 3 captured regime axes, widen `fetch_strategy_scorecards` to a joint grouping key) rather than building a new table — this is a smaller, better-grounded change than the new document's proposed `StrategyRegimeCompatibilityResult` table.
8. **If/when new model-output persistence is needed** for deterministic-state or hidden-regime results, evaluate reusing `ModelOutput` (`model_name`/`model_version`/`output` JSONB) before adding a new table family — it already has the right shape.
9. **Any new confidence/model-versioning work should reuse the existing multi-axis versioning pattern** (`model_version`/`ai_model_version`/`scoring_model_version`, explicit `"none"`/`"unknown"` sentinels) rather than introducing a new convention.
10. **Any new regime/confidence-based position sizing should populate the existing `RiskEvaluationContext.ai_scaled_quantity` hook**, preserving its exact downward-only clamp semantics, rather than building a second sizing gate.
11. **Reclassify `MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` itself per the prior review's recommendation** — strip its `Tier-1 Architecture Specification` authority header, since (independent of this audit's findings) Article VII's evidence burden has not been discharged and the project's own standing decisions (*Production Before Expansion*, *Runtime Evidence Before Expansion*) point the same direction this audit's findings do: consolidate what exists before adding what doesn't.

---

# Phase 1 Implementation Scope

> **Update (2026-08-06, same-day follow-up session):** A different Phase 1 was subsequently commissioned directly by the project owner and implemented: the canonical deterministic Market State Classifier (`apps/api/app/services/market_state/`, see `docs/MARKET_STATE_CLASSIFIER.md` and `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`), rather than the scorecard-extension scope proposed below. That implementation is complete, tested (44 unit tests, all passing), confirmed to touch no production code path, and confirmed to leave the full existing test suite's pass/fail results unchanged. The scope proposed below was not implemented and remains available for a future session if still wanted; it is retained here as a record of this audit's own recommendation, not as a description of what actually happened next.

Consistent with `docs/00_PROJECT_STATE.md`'s "Current Development Philosophy" (small, bounded, independently valuable tasks) and the prior architectural review's recommendation that only Phase 0 is currently authorized, the following is proposed as the next small, self-contained, low-risk unit of work — **not implemented by this audit**, offered for separate authorization. It is deliberately not "Phase 1" of the new architecture document's own 9-phase plan (that plan's Phase 1 assumes a new `market_state/` module this audit recommends against building yet); it is a Phase 1 sized to roughly one engineering day that acts directly on this audit's own findings.

**Proposed scope: extend `regime_conditioned_buckets` to use the two already-captured, currently-unused regime axes.**

- `StrategyRosterProposalOutcome` already persists `regime_volatility` and `regime_range` on every row (`models/strategy_roster_proposal_outcome.py:78-80`), alongside `regime_trend`. Today, `strategy_outcomes/service.py`'s `regime_conditioned_buckets` and `best_regime`/`worst_regime` computation use only `regime_trend` (lines 693-731) — the other two axes are collected but discarded.
- Scope: extend the existing bucketing logic to key on all three axes (or, more conservatively, add `regime_volatility` as a second dimension alongside `regime_trend`), and surface the additional axis through the existing read path (`fetch_strategy_scorecards`) and existing operator CLI display (`operator_cli/formatting.py:806-816`).
- Why this qualifies as small and safe: purely additive to an existing, already-tested read/analytics path; **no new service, no new database table, no new migration** (columns already exist and are already populated); **no change to any production trading, Risk, or execution behavior** — `regime_conditioned_buckets` only feeds `decision_aggregator.py`'s strategy-weighting nudge and read-only reporting, and this change would need its own explicit, separately-reviewed decision about whether to also feed the weighting nudge or remain read/reporting-only for this first step.
- Why this is the right first step rather than anything from the new document's own phase plan: it acts on data the platform already collects and already pays the storage/compute cost for, directly closes part of the gap between the existing `StrategyScorecard` mechanism and the new document's §12.2 compatibility table, and requires no new walk-forward framework, no new data source, and no HMM — none of which exist yet and none of which this audit found any existing groundwork for.
- Explicitly out of scope for this Phase 1: any new API route, any new frontend surface, any change to `RiskEvaluationContext`/`ai_scaled_quantity`, any new classifier, and any of the three existing classifiers' consolidation (recommendation #2 above) — that ADR-level decision should be made deliberately and separately, not as a side effect of a scorecard extension.
