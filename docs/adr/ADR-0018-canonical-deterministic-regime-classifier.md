# ADR-0018: Canonical Deterministic Regime Classifier

## Status
Accepted

## Context

A repository-wide documentation and architecture reconciliation pass (see `docs/DOCUMENTATION_DRIFT_REPORT.md` and `docs/MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md`) found three independent, non-reconciled implementations that each classify market trend/volatility state, under three different names, with no shared code or agreed authority:

1. `apps/api/app/services/strategy_outcomes/service.py::classify_regime_labels` — a deterministic three-axis classifier (`trend`: TRENDING/RANGING via net-return threshold; `volatility`: HIGH/LOW via population-stdev threshold; `range`: EXPANSION/COMPRESSION via first-half/second-half range comparison). This is the only one of the three actually consumed by the live production path: `strategy_roster/service.py::_classify_current_regime_trend` calls it to stamp `StrategyRosterRun.current_regime_trend`, which `strategy_roster/decision_aggregator.py::_strategy_weight` then uses to nudge live strategy ensemble weight ±0.1 (clamped to [0.5, 1.5]).
2. `apps/api/app/services/strategies/trend_regime_filter.py` — an ADX-proxy-plus-MA-slope classifier, implemented as a `Strategy`-protocol filter module. It is registered in the general strategy registry but explicitly excluded from the live production roster (`strategy_roster/registry.py::ENABLED_PHASE1_ROSTER`), and used only for standalone backtests.
3. `strategy_lab/pattern_intelligence/detectors/volatility.py` — a percentile-band volatility "finding" detector, scoped entirely to the offline `strategy_lab` research/pattern-mining tool that feeds `research_copilot`; never touches the live trading path.

None of the three share thresholds, constants, or code. `docs/AI_LAYER.md` §2.1 additionally documents a fourth classifier — an AI/ML regime classifier with a confidence score, cross-validated against `trend_regime_filter` — that does not exist anywhere in the codebase; this ADR does not need to reconcile it, only note that it is documentation, not a fourth implementation.

Without a named authority, a future contributor addressing regime-related work has no principled way to know which of the three real implementations to extend, and the proposed `MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` (already demoted to research-only status by its own prior review) would risk becoming a fourth, if built without first consulting this decision.

## Decision

`classify_regime_labels` (`apps/api/app/services/strategy_outcomes/service.py`) is the canonical, production-authoritative deterministic market-state/regime classifier for this platform. Any future work that needs "what is the current market regime" — whether for strategy weighting, risk sizing, entry-intelligence evidence, or a future market-state subsystem — must extend or consume this implementation rather than introduce a new, competing classifier.

`trend_regime_filter.py` and `strategy_lab/pattern_intelligence/detectors/volatility.py` remain in the repository, unmodified by this ADR, explicitly as non-authoritative:
- `trend_regime_filter.py` remains available as a `Strategy`-protocol filter module for backtesting and research use. It may continue to be used for those purposes. It must not be silently promoted into the live roster as a second, disagreeing source of regime truth without a future ADR that explicitly supersedes this one.
- `strategy_lab/pattern_intelligence/detectors/volatility.py` remains scoped to the offline research tool it already serves. It must not be wired into any live decision path.

Any future work expanding regime classification (additional axes, multi-timeframe synthesis, hidden-state/HMM research, etc.) must be built as an extension of, or a clearly-labeled research-only alternative to, `classify_regime_labels` — not a fourth parallel implementation. If a genuinely different regime model is later judged necessary for production (e.g., a learned/HMM-based classifier that outperforms the deterministic baseline on walk-forward evidence), promoting it to canonical status is itself an architectural decision requiring a new ADR that explicitly supersedes this one, per `docs/adr/README.md`'s numbering rule.

## Alternatives Considered

- **Consolidate all three into one module now.** Rejected for this ADR: `trend_regime_filter.py` and the `strategy_lab` detector serve genuinely different consumers (standalone backtesting, offline pattern research) with different math better suited to those contexts; forcibly merging them into the production classifier's shape would risk behavior changes in tools this ADR is not scoped to touch. This ADR names an authority, not a refactor.
- **Promote `trend_regime_filter.py` to canonical instead**, since it is the one named directly in `STRATEGY_ENGINE.md` and `AI_LAYER.md`. Rejected: it is not the implementation actually driving production behavior today, and promoting a dormant module over a live one would require behavior changes this reconciliation pass is explicitly prohibited from making (`docs/DOCUMENTATION_DRIFT_REPORT.md`'s scope is documentation-only).
- **Leave all three unranked, pending a future market-state architecture decision.** Rejected: this is the status quo, and it is exactly the condition that risks a fourth implementation being built by a future session (or the demoted `MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md`'s eventual Phase 1) without knowing three already exist.

## Consequences

Benefits:
- Future regime-related work (strategy weighting extensions, entry-intelligence evidence, any eventual market-state subsystem) has one unambiguous starting point.
- `AI_LAYER.md` and `STRATEGY_ENGINE.md` can now be corrected (per `docs/DOCUMENTATION_DRIFT_REPORT.md` §2.3) to describe the real, authoritative classifier instead of a nonexistent AI model or a dormant filter module.
- Closes the specific duplication risk `docs/MARKET_STATE_REGIME_INTELLIGENCE_REVIEW.md` flagged: a new `services/market_state/` tree would have been a fourth implementation; this ADR gives it a documented second (extend `classify_regime_labels`) instead.

Trade-offs:
- `classify_regime_labels`'s three fixed thresholds (0.60% net-return, 0.004 population-stdev, 0.10pp range-expansion) are not walk-forward-validated against alternatives — canonizing it records current authority, not a claim that its specific thresholds are optimal. Future evidence-based threshold research remains open and welcome; it should modify this classifier in place (versioned), not fork it.
- `trend_regime_filter.py` and the `strategy_lab` detector remain unreconciled with the canonical classifier's actual output — a strategy backtested against `trend_regime_filter`'s regime labels is being evaluated against different regime boundaries than the ones that would actually weight it in production. This pre-existing gap is not created by this ADR, only newly documented by it.
