# MARKET_STATE_CLASSIFIER.md

## OmniTrade Legacy Engine — Canonical Deterministic Market State Classifier (Research/Replay Layer)

**Status:** Implemented, Phase 1 of `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md`'s research track.
**Location:** `apps/api/app/services/market_state/` (`contracts.py`, `deterministic_classifier.py`).
**Tests:** `apps/api/tests/unit/services/market_state/test_deterministic_classifier.py` (44 tests).
**Scope boundary:** Research / replay layer only. **Not consumed by any production decision, risk, or execution path.**

---

## Purpose

Per `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`, this platform's production-authoritative regime classifier is `app.services.strategy_outcomes.service.classify_regime_labels`, and any future regime-classification work must either extend that function or be built as "a clearly-labeled research-only alternative to it — not a fourth parallel implementation." This module is that alternative: a single, canonical, deterministic Market State definition for the **research and replay layer**, intended as the fixed baseline against which future work (walk-forward threshold research, Hidden Markov Models, Bayesian models, neural regime models — none of which are built or authorized by this document) will eventually be evaluated, per `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §16's "baselines and ablation testing" principle.

It exists alongside, not in place of, `classify_regime_labels` — the two serve different responsibilities and are not in conflict. See `docs/CANONICAL_ARCHITECTURE_MAP.md` §1 for how they relate.

## What It Is

`classify_market_state(candles, *, params=DEFAULT_PARAMS) -> MarketState` is a pure function:

- **Inputs:** OHLCV bars only (`OHLCVBar`: open/high/low/close/volume). No order book, funding rate, ETF flow, on-chain data, news, macro data, or any other evidence family. No network or database access — the function never performs I/O.
- **Output:** A single, immutable `MarketState` object with three independent axes (`direction_state`, `volatility_state`, `participation_state`), a blended `confidence` in `[0, 1]`, human-readable `reason_codes` explaining each axis's classification, a `metrics` mapping exposing the exact computed numbers behind each decision, a `classifier_version` string, and the `evaluated_bar_count`.
- **Deterministic and replay-safe by construction:** no randomness, no module-level mutable state, no wall-clock reads, and no access to any bar other than the ones passed in `candles`. Given identical inputs, it always returns a field-for-field identical result. See the module docstring in `deterministic_classifier.py` for the full no-lookahead argument and the "Two-bar test" pattern used to verify it (§ Testing below).

### Direction State

`trending_up` / `trending_down` / `ranging`, from the net percentage return between the first and last bar's close, compared against a configurable threshold (default 0.60%, matching `classify_regime_labels`' own convention for continuity, though the two implementations are independent).

### Volatility State

`low` / `normal` / `high`, from the population standard deviation of bar-to-bar close returns over the window, compared against two configurable thresholds (defaults 0.15% / 0.40%).

### Participation State

`volume_contracting` / `volume_normal` / `volume_expanding`, from the ratio of the window's second-half average volume to its first-half average volume, compared against two configurable thresholds (defaults 0.80× / 1.20×). This axis has no analogue in `classify_regime_labels` today.

### Confidence and Explainability

Each axis computes its own sub-confidence (how far its metric sits from the nearest decision boundary, saturating toward 1.0 as the evidence gets stronger), and the overall `confidence` is their average, rounded to four decimal places. `reason_codes` is always exactly three strings — one per axis — each naming the exact computed metric and the threshold(s) it was compared against, so every classification is traceable to specific numbers, never an opaque score.

## What It Explicitly Is Not

- **Not a Hidden Markov Model, Bayesian model, or neural network.** Purely deterministic arithmetic over OHLCV bars.
- **Not consumed by live trading.** No production module — `strategy_roster`, `autonomous_cycle`, `capital_campaign_orchestration`, `risk`, or any execution path — imports this package. Confirmed by repository-wide search at implementation time; this must remain true.
- **Not a database-backed model.** No migration, no table, no persisted state. `MarketState` is an in-memory value object; any future persistence of its output (e.g., for replay evidence) is a separate, unauthorized-by-this-document decision.
- **Not a change to the Risk Engine, Strategy Engine, or any production behavior.** No existing file was modified to build this.

## Thresholds Are Hypotheses, Not Conclusions

Per `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §7.2 ("No Arbitrary Threshold Is Permanent"), every default threshold in `MarketStateClassifierParams` is an initial, unvalidated hypothesis, versioned and overridable via the `params` argument. Any future change to the defaults must be a new, explicitly versioned `MarketStateClassifierParams` (and a new `classifier_version` if the semantics of a state change), never a silent in-place edit — this is what lets future replay evidence compare classifier versions meaningfully instead of silently rewriting history.

## Testing

`apps/api/tests/unit/services/market_state/test_deterministic_classifier.py` covers, per axis and in combination: a strong uptrend, a strong downtrend, a sideways/ranging market, high volatility, low volatility, normal/moderate volatility, expanding volume, contracting volume, stable volume; edge cases (insufficient bars, non-positive close, negative volume, all-zero volume, a zero-valued direction threshold, a single repeated flat bar); exact-boundary conditions for all three axes (a metric landing exactly on a threshold resolves toward the more extreme state, deterministically); invalid-parameter validation (nine parametrized cases); replay determinism (identical input produces identical output, repeated calls don't leak state); no-lookahead verification (classifying a window depends only on the bars in that window, demonstrated by feeding it two timelines that diverge only after the window and confirming identical results, paired with a contrasting test showing the result *does* change if future bars are mistakenly included — clarifying that the guarantee is about what the function is given, not a magical property); and immutability (every output/input dataclass is frozen).

All 44 tests pass. The full existing `apps/api` unit test suite (2874 passing, 17 pre-existing failures unrelated to this change and unchanged by it — see `docs/02_DECISIONS.md`) was re-run after this addition with identical results.

## Relationship to Other Documents

- `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md` — the decision this module fulfills.
- `docs/CANONICAL_ARCHITECTURE_MAP.md` §1 — updated to list this module as the canonical research/replay baseline, alongside `classify_regime_labels` as the canonical production classifier.
- `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` — the broader, demoted-to-research-only architecture vision this module takes its first, smallest step toward (§7 Deterministic State Classifier, §16 Baselines and Ablation Testing). This module does not authorize, and should not be read as authorizing, any of that document's later phases (HMM research, multi-timeframe synthesis, live filtering, new data sources).
- `docs/MARKET_STATE_AND_REGIME_IMPLEMENTATION_AUDIT.md` — the Phase 0 repository audit that preceded this work; see its own updated Phase 1 note for how this implementation relates to that audit's originally-proposed (different, smaller) Phase 1 scope.
