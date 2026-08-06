# CANONICAL_ARCHITECTURE_MAP.md

## OmniTrade Legacy Engine — One Canonical Implementation Per Responsibility

**Purpose:** For every major architectural responsibility, this document names exactly one canonical implementation — the module a future contributor should extend — and, where more than one implementation currently exists, names the others as deprecated/non-authoritative candidates with the reason they are not canonical. This document does not merge, delete, or rename anything; it records a judgment for future work to act on.

**Companion documents:** `DOCUMENTATION_DRIFT_REPORT.md` (the evidence this map is built from), `docs/adr/ADR-0018` through `ADR-0020` (formal decisions for the three responsibilities where duplication is significant enough to warrant one), `REPOSITORY_RECONCILIATION_SUMMARY.md`.

**How to read a row:** *Canonical* is the implementation new work should build on. *Non-canonical / deprecated candidates* are implementations that exist, may still be in use for a narrower purpose than the canonical one, and should not be extended to take on more responsibility without an explicit decision to consolidate. *Status* records whether the responsibility is live in production, built-but-dormant, or not yet built at all.

---

## 1. Market State / Regime Classification

| | |
|---|---|
| **Canonical** | `apps/api/app/services/strategy_outcomes/service.py::classify_regime_labels` (lines 153-193) — deterministic trend/volatility/range classification, live in production, consumed by `strategy_roster/decision_aggregator.py::_strategy_weight`. |
| **Non-canonical candidates** | `apps/api/app/services/strategies/trend_regime_filter.py` (ADX-proxy + MA slope; registered but excluded from the live strategy roster — research/backtest-only). `strategy_lab/pattern_intelligence/detectors/volatility.py` (percentile-band volatility "findings"; scoped to the offline research tool, feeds `research_copilot`, never the live path). |
| **Status** | Live/production for the canonical implementation. The two non-canonical candidates are real, tested code, just not authoritative for production decisions. |
| **Governing decision** | `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md` (new, this session). |
| **Documented-but-nonexistent** | `AI_LAYER.md` §2.1's AI/ML regime classifier — no code anywhere; see `DOCUMENTATION_DRIFT_REPORT.md` §2.3. Not listed as a "candidate" above because it does not exist to be one. |

---

## 2. Strategy Engine

| | |
|---|---|
| **Canonical** | `apps/api/app/services/strategies/` — `base.py` (the `Strategy` Protocol and `Signal`/`StrategyContext` types) plus `registry.py` (slug → implementation). Roster-eligible subset governed separately by `strategy_roster/registry.py::ENABLED_PHASE1_ROSTER`. |
| **Non-canonical candidates** | `strategy_lab/strategies/` (`trailing_limit_v1.py`, `trailing_limit_v2.py`) — a separate, experimental strategy space used only by the offline `strategy_lab` research tool; not competing with the production registry, simply out of scope for it. |
| **Status** | Live/production. 12 strategy modules exist under `apps/api/app/services/strategies/`; only 7 are enabled in the production roster. |
| **Documentation gap** | `STRATEGY_ENGINE.md`'s Protocol signature is stale relative to the real code — see `DOCUMENTATION_DRIFT_REPORT.md` §2.4. No architectural duplication; a documentation-only fix. |

---

## 3. Risk Engine

| | |
|---|---|
| **Canonical** | `apps/api/app/services/risk/risk_engine.py::evaluate_signal_risk` — the single, deterministic, 12-step evaluation gate called by every order-producing pathway. |
| **Non-canonical candidates** | None. This is the one responsibility in the entire audit with zero duplication — every live call site (`arena/risk_gate.py`, `instant_trades.py`, `live_crypto_orders.py`, `authoritative.py`, `autonomous_limit_entry_worker.py`, `controlled_proof/service.py`, `commissioned_entry_execution.py`) converges on this single function. |
| **Status** | Live/production, uncontested, singular. |
| **Notable dormant seam** | `RiskEvaluationContext.ai_scaled_quantity` (line 77) is a correctly-bounded (downward-only) confidence-scaling hook that exists in the canonical implementation but is never populated by any current call site — the right integration point for any future confidence/regime-based sizing work, not a second gate to build. |
| **Documentation gap** | `RISK_ENGINE.md`'s framing (paper-only, MVP) is stale; its control mechanics (§2/§3) are accurate. See `DOCUMENTATION_DRIFT_REPORT.md` §3.3. |

---

## 4. Decision Intelligence

| | |
|---|---|
| **Canonical** | `apps/api/app/services/decisions/` (`ingestion.py`, `explainability.py`, `counterfactuals.py`, `quality.py`, `replay_context.py`, `replay_candidates.py`, `package.py`) plus `apps/api/app/models/decision_record.py`, `decision_snapshot.py`, `decision_explainability_record.py`, `decision_counterfactual_result.py`, `decision_quality_score.py`, `decision_alternative_action.py` — the full, documented Decision Intelligence Engine (Decision Records, Decision Snapshot, Explainability, Counterfactual Outcome Ledger, Decision Quality Engine), matching `DECISION_INTELLIGENCE_ENGINE.md`'s described schema closely. |
| **Non-canonical candidate (naming collision, not duplication)** | `apps/api/app/services/decision_intelligence/` (singular) — a smaller, distinct subsystem that ranks strategies by deterministic replay-quality. It consumes the canonical DIE's output; it is not a competing implementation of the DIE itself, but its name makes that easy to assume incorrectly. |
| **Status** | Live/production, fully migrated (2026-07-06), consumed by the autonomous decision path. |
| **Documentation gap** | `DATABASE_SCHEMA.md` §3a and `BACKEND_MODULE_SPECS.md` both still describe this as future/unbuilt work — the highest-confidence, most-corroborated drift finding in this audit. See `DOCUMENTATION_DRIFT_REPORT.md` §2.1–2.2. |

---

## 5. Replay Engine

This responsibility has three genuinely distinct meanings in this codebase, none of which is a duplicate of another — they answer different questions — but the shared word "replay" across all three, plus four separate architecture documents describing a fourth, not-yet-built meaning, is a real risk. See `docs/adr/ADR-0020-replay-terminology-and-boundaries.md`.

| Meaning | Canonical implementation | Status |
|---|---|---|
| **Decision-package identity replay** ("did this already-made decision reconstruct deterministically from its own immutable evidence?") | `apps/api/app/services/replay/` (`default_agent.py::DefaultReplayAgent.replay`), exposed via `POST /arena/replay`. Determinism verified by `decisions/replay_candidates.py::certify_decision_package_readiness_v0`'s double-build content-hash check. | Live/production. |
| **Single-candidate counterfactual replay** ("would this specific rejected/proposed limit order have filled, and what would it have earned?") | `apps/api/app/services/entry_intelligence/shadow_validation.py::replay_rejected_buy_candidate_counterfactual`. | Built and tested; offline/manual invocation only, not yet run against real production rejection history. |
| **Chronological historical-market replay** ("run the whole production pipeline against history from the earliest trustworthy data forward, generating a synthetic decision corpus") | **Not yet built.** Described, with heavy mutual overlap and no cross-referencing, by four separate documents: `docs/HISTORICAL_INTELLIGENCE_PLATFORM.md`, `docs/HISTORICAL_LEARNING_INTELLIGENCE_ARCHITECTURE.md` (not tracked in git), `docs/HISTORICAL_MARKET_REPLAY_ENGINE.md`, and `PIPELINE_ARCHITECTURE.md` §15–20. The closest real scaffolding is `apps/api/app/services/historical_simulation/` (`SimulationBase`, `IsolationGuard`, `RunMode`/`EvidenceClass` per ADR-0012/0013/0014) — isolation primitives only, no orchestrator, no chronological loop. | Not built. Scaffolding exists (ADR-0012–0016) but is explicitly "ahead of consumers" per those ADRs' own text. |
| Non-canonical/experimental | `apps/api/app/services/backtesting/` (`engine.py`, `fills.py`) — a real, working, deterministic backtester, but one that does not run the live strategy/Risk/AI pipeline; a distinct tool for "what if we changed this parameter over history," not a replay of production decision logic. | Live, but intentionally out of scope for the "same pipeline for live and replay" principle `PIPELINE_ARCHITECTURE.md` §2.8 states as a governing rule — not yet converged, per that same document's own acknowledgment. |

---

## 6. Learning Pipeline

| | |
|---|---|
| **Canonical (for what exists today)** | `apps/api/app/services/pipeline_contracts/` (`envelope.py`, `context.py`, `identifiers.py`, `serialization.py`, `btc_kraken.py`, `btc_kraken_adapters.py`) — real, tested, committed Phase 1 scaffolding from `PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md`, currently consumed by exactly one module (`apps/api/app/services/data/canonical_market_identity.py`). |
| **Status** | Built-but-dormant. No Dataset Registry, Model Registry, or Shadow Learning runner exists anywhere (confirmed by grep across `apps/api/app/`). |
| **Documented-only** | `docs/LEARNING_INTELLIGENCE_ARCHITECTURE.md` (2088 lines, self-labeled "Draft"/"Vision," its own peer review recommends non-binding status) describes the full learning pipeline (Dataset Registry, Model Registry, Decision Arena for ML candidates, Shadow/Experimental/Production Learning) — none of it built. |
| **Naming collision** | `LEARNING_INTELLIGENCE_ARCHITECTURE.md` §19's "Decision Arena" (ML-model tournament: production vs. candidate models) is a different concept sharing a name with the already-shipped `apps/api/app/api/routes/arena.py` Strategy Arena (which tournaments deterministic strategies, not ML models). |
| **Recommendation** | No canonical-ownership ADR needed yet — nothing beyond the Phase 1 contract scaffolding exists to have competing ownership of. Revisit once Phase 2+ of `PIPELINE_AND_LEARNING_IMPLEMENTATION_PLAN.md` produces real code. |

---

## 7. Feature Extraction

| | |
|---|---|
| **Canonical** | No dedicated, standalone feature-extraction module exists. Features are computed inline: within each strategy's own `generate_signal` (the `indicators` dict on `Signal`), within `entry_intelligence/evidence.py` (edge/uncertainty features), and within `strategy_outcomes/service.py` (regime-classification features). The closest thing to a persisted "feature snapshot" concept is `DecisionSnapshot.generated_features` (`apps/api/app/models/decision_snapshot.py`), populated at decision time. |
| **Documented-only** | `PIPELINE_ARCHITECTURE.md` §9.2's "Feature Engineering" stage (input `CanonicalMarketContext` → output `CanonicalFeatureSnapshot`) is a proposed, not-yet-built, dedicated stage. |
| **Status** | No canonical single implementation exists today; this is a genuinely distributed responsibility, not a duplicated one. Flagged for awareness, not correction. |

---

## 8. Audit

| | |
|---|---|
| **Canonical** | `apps/api/app/models/audit_log.py` (`AuditLog`) — append-only at the application layer (no `PATCH`/`DELETE` route exists), written as a side effect of state-changing operations across the codebase per `docs/PHASE_0_ARCHITECTURAL_INVENTORY.md` §7's persistence inventory ("Mostly append-only, correlation/entity IDs; transaction follows owning business operation"). |
| **Adjacent, not competing** | `apps/api/app/models/live_audit_evidence_record.py` (`LiveAuditEvidenceRecord`) — live-execution-specific audit evidence, a narrower/richer sibling for the live-trading path specifically, not a duplicate of the general `audit_log`. |
| **Status** | Live/production, well-established, consistently used — the one other responsibility besides Risk Engine with no meaningful duplication finding. |

---

## 9. Evidence

| | |
|---|---|
| **Canonical (decision evidence)** | `apps/api/app/models/decision_explainability_record.py` + `apps/api/app/services/decisions/explainability.py` — role-tagged (`supporting`/`opposing`/`confidence_factor`/`risk_adjustment`) evidence for individual trading decisions, append-only, with explicit `availability_state` rather than fabricated evidence. |
| **Distinct, not competing** | `apps/api/app/services/pipeline_contracts/envelope.py`'s `CanonicalEnvelopeV1` — a generic, business-payload-free transport-metadata wrapper (event_id, occurred_at, available_at, quality_status, integrity_hash) intended for the future canonical pipeline, currently unconsumed outside `canonical_market_identity.py`. This is a different sense of "evidence" (transport provenance vs. decision rationale) than the canonical implementation above, and the two are not currently cross-referenced anywhere despite both being named "evidence" concepts. |
| **Governing document** | `docs/EVIDENCE_LAYER.md` — accurate in its terminology and boundary rules, but its "Implemented vs. Future" status table is stale (everything on the Future list shipped 2026-07-09, the same day the doc was written). See `DOCUMENTATION_DRIFT_REPORT.md` §2.9. |
| **Status** | Live/production for decision evidence; dormant scaffolding for pipeline envelope evidence. |

---

## 10. Portfolio

| | |
|---|---|
| **Canonical (accounting, not intelligence)** | `apps/api/app/models/live_accounting_record.py` + `apps/api/app/services/live/accounting_reconciliation.py` — the real, production-proven ledger of reconciled balances, fees, and P&L. |
| **Documented-only** | `docs/PORTFOLIO_INTELLIGENCE.md` ("Constitutional Vision") describes a holistic portfolio-health/diversification/concentration/idle-capital evaluation layer with zero corresponding code anywhere (confirmed by grep: zero hits for `portfolio_intelligence`, `portfolio_health`, `diversification_score`, `concentration_score`, `idle_capital`). ADR-0008 assigns this responsibility to "Portfolio Intelligence" as part of the four-core-engine model, but the evaluative layer itself has never been built. |
| **Status** | Accounting/reconciliation is live and production-proven; the "intelligence" (evaluative) half of the responsibility does not exist in any form. Not a duplication — a genuine, honestly-labeled gap. |

---

## 11. Capital Allocation / Capital Campaigns

This is the most significant duplication finding in the audit — one phrase ("Capital Allocation Engine") denoting three unrelated things, plus a real, undocumented split inside the thing that actually governs live capital. See `docs/adr/ADR-0019-canonical-capital-campaign-governance-layer.md`.

| Concept | Implementation | Status |
|---|---|---|
| **Canonical capital-governance layer (live capital)** | `apps/api/app/services/capital_campaign_domain/` + `apps/api/app/services/capital_campaign_orchestration/` (`authoritative.py`) — the "definition + runtime pin" system that actually drives commissioned live execution via `commissioned_entry_execution.py`, working alongside `apps/api/app/services/mandates/` (ADR-0011, Autonomous Capital Mandate Engine). | Live/production. |
| **Non-canonical, narrower (CRUD shell over the same table)** | `apps/api/app/services/capital_campaigns/` (plural) — the simpler CRUD/lifecycle service `docs/CAPITAL_CAMPAIGNS.md` actually documents. Real and in use, but only a subset of the responsibility. | Live/production, narrower scope than documented reader would assume. |
| **"Capital Allocation Engine" #1 — aspirational** | ADR-0008 — an unimplemented `Master Account → Paper Portfolios → Strategies → Future Agents` hierarchy. No `MasterAccount`/`PaperPortfolio`/portfolio-level `Agent` model exists anywhere. | Not built; superseded in practice by the campaign/mandate architecture, but never formally marked superseded. |
| **"Capital Allocation Engine" #2 — real, unrelated in scope** | `docs/CAPITAL_ALLOCATION_ENGINE.md` / `apps/api/app/services/capital_allocation/deterministic.py` — a small, real, deterministic tournament-ranking-driven *paper-capital recommendation* generator for the Strategy Arena UI (`GET /arena/capital-allocation`). No portfolios, no agents, no rebalancing — none of ADR-0008's described responsibilities. | Live/production, but answers a completely different question than ADR-0008 describes. |

---

## 12. Order Management

| | |
|---|---|
| **Canonical** | `apps/api/app/services/live_crypto_orders.py` (`LiveCryptoOrderService`) + `apps/api/app/services/orchestration/autonomous_execution_claims.py` (exclusive execution custody) — the live order lifecycle, submission, and claim-ownership machinery. |
| **Governed entry point** | `apps/api/app/services/capital_campaign_domain/commissioned_entry_execution.py` — the campaign/mandate-governed path into the canonical order service; not a competing implementation, the authorized front door to it. |
| **Distinct, intentionally separate operator paths (not duplicates)** | `apps/api/app/services/instant_trades.py::execute_instant_trade` and the manual `live_crypto_orders.py` API path both reach the provider directly after their own operator/Risk gates, by design, per `docs/PHASE_0_ARCHITECTURAL_INVENTORY.md` §5/§14 ("CONDITIONALLY ACTIVE... bypasses autonomous package/claim Governance by design; classify ACTIVE operator path"). These are not architectural drift — they are deliberately separate, governed, human-operator-triggered paths, and should not be consolidated into the autonomous path without a dedicated decision. |
| **Status** | Live/production, well-inventoried by `docs/PHASE_0_ARCHITECTURAL_INVENTORY.md` — no new duplication found beyond what that document already classifies precisely. |

---

## 13. Scheduling

| | |
|---|---|
| **Canonical** | `apps/api/app/services/orchestration/continuous_pipeline_worker.py::run_forever()` — a single, in-process async poll loop; not a distinct "scheduler service" and not built on any external scheduler library (no APScheduler/cron abstraction exists, despite `BACKEND_MODULE_SPECS.md`'s original MVP-era description implying a more generic ingestion cron). |
| **Status** | Live/production, singular. No duplication found. |

---

## 14. Data Sources

| | |
|---|---|
| **Canonical (execution + primary market data)** | `apps/api/app/services/exchange_connections/providers/kraken_spot.py` (primary) and `coinbase_advanced.py` (secondary), registered in `providers/registry.py`; `apps/api/app/services/data/kraken_client.py` for market data. |
| **Narrower, still-real roles** | `apps/api/app/services/data/binance_client.py` — market-data-only, no execution integration. `apps/api/app/services/paper/alpaca_paper.py` — paper-trading-only. |
| **Status** | Live/production for Kraken/Coinbase. `DATA_SOURCES.md` documents neither and instead centers Binance/Alpaca/yfinance, which are present but not primary. See `DOCUMENTATION_DRIFT_REPORT.md` §2.7. |

---

## 15. AI Layer

| | |
|---|---|
| **Canonical** | No single module fulfills `AI_LAYER.md`'s described responsibility (regime classifier, confidence scorer, allocator, explanation generator, post-trade review) under one name. The real, scattered set that together covers most of the same ground: `apps/api/app/services/entry_intelligence/` (evidence/uncertainty/decisioning), `apps/api/app/services/ai_coach/` (template-based explanation generation for replay quality), `apps/api/app/services/decision_quality/` (post-hoc quality scoring, a different axis than pre-decision confidence), `apps/api/app/services/decision_intelligence/` (singular — replay-quality ranking). |
| **Documented-only** | `AI_LAYER.md` §2.1's regime classifier — see §1 above and `DOCUMENTATION_DRIFT_REPORT.md` §2.3. `BACKEND_MODULE_SPECS.md`'s `app/services/ai/` module (`regime_classifier.py`, `signal_scorer.py`, `allocator.py`, `explainer.py`, `post_trade_review.py`) — none of these five files exist anywhere. |
| **Status** | The responsibility exists, distributed; the module `AI_LAYER.md`/`BACKEND_MODULE_SPECS.md` describe as its home does not. This is a documentation-naming gap, not unimplemented functionality — the underlying capability (confidence estimation, explanation, evidence weighting) is real, just organized differently than documented. |

---

## 16. Execution

| | |
|---|---|
| **Canonical (provider abstraction)** | `apps/api/app/services/exchange_connections/providers/base.py` (contract) + `registry.py` (capability-gated lookup) — genuine, working provider-neutral execution, matching `VENUE_ABSTRACTION.md`'s mechanical claims reasonably well. |
| **Canonical (governed execution)** | `apps/api/app/services/capital_campaign_domain/commissioned_entry_execution.py` — see §11/§12 above. |
| **Documented-only (routing/quality "intelligence")** | `VENUE_INTELLIGENCE_ENGINE.md`, `EXECUTION_ROUTING.md`'s "Provider Ranking"/"Smart Order Routing" sections, and `EXECUTION_QUALITY.md`'s "Provider Comparison" section describe the same unbuilt provider-scoring/auto-routing concept three times, with mutual cross-references creating an illusion of independently-corroborated architecture. No `ExecutionRouting`, `route_order`, `select_provider`, or execution-scoring class exists anywhere. |
| **Partially real** | `apps/api/app/models/live_execution_quality_metric.py` + `apps/api/app/services/live/execution_quality.py` — real, append-only capture of expected-vs-realized price and slippage per fill. A genuine subset of `EXECUTION_QUALITY.md`'s vision, not the full "continuous improvement"/provider-ranking system it describes. |
| **Status** | Provider execution is live/production and singular. Routing "intelligence" is triplicated in documentation and built nowhere. |

---

## 17. State Management (Operating Mode / Evidence Provenance)

| | |
|---|---|
| **Canonical** | `RunMode` and `EvidenceClass` enums (ADR-0012/ADR-0013) + `apps/api/app/services/historical_simulation/` (`SimulationBase`, `IsolationGuard`, per ADR-0014). |
| **Status** | Built, real, but explicitly "scaffolding ahead of consumers" per the ADRs' own text — no production orchestrator binds to `RunMode` yet; the production path implicitly runs as `PRODUCTION_LIVE` without ever naming it that way. Not a duplication — a single, singular, honestly-scoped-as-incomplete implementation. |

---

## 18. Configuration

| | |
|---|---|
| **Canonical** | `apps/api/app/config.py` (`Settings`, `pydantic-settings`, `get_settings()`) — still accurate per `BACKEND_MODULE_SPECS.md`'s original description; the one MVP-era module description that has not drifted. |
| **Known, already-documented complication** | `00_OPERATIONS_MAP.md`'s "Environment and Configuration Architecture" section already candidly documents that production configuration is split across three sources with unresolved precedence (`apps/api/.env`, `/etc/omnitrade/activation-only/current.env`, a systemd drop-in) — this is a real operational gap, but it is already tracked accurately in the one document responsible for tracking it, so no correction is needed here. |
| **Status** | Live/production, canonical at the code level; operationally messy at the deployment level, already self-documented as such. |

---

## Summary Table

| Responsibility | Canonical Implementation | Duplication Severity | New ADR |
|---|---|---|---|
| Market State / Regime | `strategy_outcomes/service.py::classify_regime_labels` | Medium (3 classifiers) | ADR-0018 |
| Strategy Engine | `services/strategies/` | None | — |
| Risk Engine | `services/risk/risk_engine.py` | None | — |
| Decision Intelligence | `services/decisions/` + `models/decision_*.py` | Low (naming collision only) | — |
| Replay Engine | Three distinct meanings, each singular within its meaning | Low (terminology, not implementation) | ADR-0020 |
| Learning Pipeline | `services/pipeline_contracts/` (Phase 1 only) | None yet | — |
| Feature Extraction | Distributed, no single home | N/A (not duplicated, just distributed) | — |
| Audit | `models/audit_log.py` | None | — |
| Evidence | `models/decision_explainability_record.py` | Low (distinct senses of "evidence") | — |
| Portfolio | `models/live_accounting_record.py` (accounting only) | None (intelligence half unbuilt) | — |
| Capital Allocation / Campaigns | `services/capital_campaign_domain/` + `capital_campaign_orchestration/` | **High** (3 meanings of "Capital Allocation Engine" + undocumented split) | ADR-0019 |
| Order Management | `services/live_crypto_orders.py` + `autonomous_execution_claims.py` | None (other paths are intentional, not duplicative) | — |
| Scheduling | `orchestration/continuous_pipeline_worker.py::run_forever` | None | — |
| Data Sources | `exchange_connections/providers/kraken_spot.py` (+ `coinbase_advanced.py`) | None (documentation gap only) | — |
| AI Layer | Distributed (`entry_intelligence/`, `ai_coach/`, `decision_quality/`) | Low (documentation-naming gap) | — |
| Execution (provider) | `exchange_connections/providers/` | None | — |
| Execution (routing/quality "intelligence") | Not built | Medium (triplicated in docs, zero code) | — |
| State Management | `RunMode`/`EvidenceClass`/`historical_simulation/` | None | — |
| Configuration | `app/config.py` | None | — |
