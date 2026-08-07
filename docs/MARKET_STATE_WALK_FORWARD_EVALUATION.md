# MARKET_STATE_WALK_FORWARD_EVALUATION.md

## OmniTrade Legacy Engine — Deterministic Walk-Forward Market State Evaluation Harness

**Status:** Implemented.
**Location:** `apps/api/app/services/market_state/` (`walk_forward_contracts.py`, `walk_forward_evaluator.py`).
**Tests:** `apps/api/tests/unit/services/market_state/test_walk_forward_evaluator.py` (40 tests).
**Scope boundary:** Research / replay layer only. **Not consumed by any production decision, risk, or execution path.**

---

## Purpose

`run_walk_forward_evaluation` measures whether the canonical deterministic Market State Classifier (`docs/MARKET_STATE_CLASSIFIER.md`, `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md`) has measurable research value on chronological historical OHLCV data. It answers five questions:

1. **How frequently does each market state occur?** — `MarketStateFrequency`.
2. **How persistent is each market state?** — `MarketStatePersistence`.
3. **How does each state transition into other states?** — `MarketStateTransition`.
4. **What future return distributions follow each state?** — `MarketStateForwardReturnSummary`.
5. **Does combining direction, volatility, and participation provide more useful information than direction alone?** — answered by exposing both single-axis (`direction`/`volatility`/`participation`) and `joint` forward-return summaries side by side, for a caller to compare. **The evaluator does not itself compute or assert an answer to this question** — it deliberately does not claim predictive value merely because a state shows a positive average return (see "Baseline Comparison" below).

It calls the existing canonical `classify_market_state` for every evaluation point rather than reimplementing any classification logic — verified by a black-box equivalence test and a call-count spy test in the test suite.

## No Future Leakage (Core Rule)

At evaluation point T (a bar index), the classification window passed to `classify_market_state` contains only bars at or before T — there is no code path through which a later bar could reach that call. Forward returns are computed strictly afterward, as a separate step, to score the already-frozen state, never to influence it. This is verified by a paired-timeline test: two datasets share identical history through T and diverge only afterward; the state recorded at T is identical between them, while their forward-return outcomes (measured after the state was frozen) are free to differ — and, in the test, do.

Chronological ordering is enforced explicitly: `run_walk_forward_evaluation` raises `ValueError` if the supplied bars' `open_time` is not strictly increasing. This is stricter than the canonical classifier's own `OHLCVBar` (which has no timestamp and trusts caller-supplied ordering) — the walk-forward evaluator's entire no-leakage guarantee depends on genuine chronological order, so it checks rather than assumes it.

## Input and Output Contracts

### Input: `WalkForwardBar`

An OHLCV bar plus an explicit `open_time`. Deliberately a new, separate type from the canonical classifier's `OHLCVBar` (which has no timestamp) — `WalkForwardBar.to_ohlcv_bar()` converts to `OHLCVBar` for the actual classification call, so the canonical classifier's own contracts remain untouched by this addition. Supplied explicitly by the caller; the evaluator never loads production data, accesses a database, or calls a network provider.

### Configuration: `WalkForwardEvaluationParams`

- `classification_window_bars` (default 20) — must be `>= classifier_params.min_bars`, checked upfront with a clear error.
- `evaluation_step_size_bars` (default 1) — how many bars to advance between evaluation points.
- `forward_horizons_bars` (default `(1, 4, 16)`) — expressed in bars, not a fixed time unit, so the evaluator makes no assumption about candle interval.
- `minimum_sample_count` (default 30) — the threshold below which a summary row is marked `INSUFFICIENT_EVIDENCE`.

### Output: `WalkForwardEvaluationResult`

Bundles `observations`, `frequencies`, `transitions`, `persistence`, `forward_return_summaries`, plus version/config metadata (`classifier_version`, `evaluator_version`, the exact `classifier_params`/`walk_forward_params` used, and `total_bars_supplied`) for reproducibility.

- **`WalkForwardStateObservation`** — one evaluation point: `evaluation_index` (the index, in the caller's original `bars`, of the last bar in the classification window), `evaluation_time`, the three classified states, `confidence`, `reason_codes`, and `forward_returns_by_horizon` (a mapping with one entry per configured horizon; `None` means that horizon's future bar was not available in the supplied data — never fabricated — not that the horizon wasn't requested; every configured horizon is always present as a key).
- **`MarketStateFrequency`** — count and percentage of each state value, per dimension.
- **`MarketStateTransition`** — one `from_state -> to_state` edge, per dimension, computed from **consecutive evaluated observations, never raw candles**. Always carries its own denominator (`total_outgoing_transitions_from_state`) alongside `conditional_probability` — a probability is never reported without its sample count.
- **`MarketStatePersistence`** — how often a state, once observed, recurred at the next evaluation point (`self_transition_count` / `total_outgoing_transitions` / `persistence_probability`).
- **`MarketStateForwardReturnSummary`** — mean/median/positive-percentage/min/max close-to-close percentage return for one (dimension, state, horizon) triple, or for the `BASELINE`/`unconditional` case. All fields are `None` when `sample_count == 0` — never a fabricated zero.

All four aggregate types carry an `EvidenceStatus` (`SUFFICIENT` / `INSUFFICIENT_EVIDENCE`) driven by `minimum_sample_count`. Low-sample rows are always still returned in full, with their real counts — never suppressed, per this module's explicit instruction.

Every dimension (`direction`, `volatility`, `participation`, `joint`) is computed separately. The `joint` state value is the stable, explicit key `f"{direction}|{volatility}|{participation}"`.

## Walk-Forward Chronology

For each evaluation index `t` in `range(window - 1, len(bars), step)`:

1. Select the trailing window `bars[t - window + 1 : t + 1]` (inclusive of bar `t`).
2. Call `classify_market_state` on that window (converted to `OHLCVBar`).
3. Freeze the resulting state into a `WalkForwardStateObservation`.
4. Compute forward returns for each configured horizon (`(close[t + horizon] - close[t]) / close[t] * 100`, quantized to 6 decimal places, matching the canonical classifier's own metric precision and percentage convention — e.g. `1.25` means `+1.25%`), using `None` for any horizon whose future bar doesn't exist in the supplied data.
5. Advance to the next evaluation index.

An evaluation point's *eligibility* depends only on having enough trailing history (`t >= window - 1`) — not on forward data being available. A bar near the end of the supplied series is still classified; only its forward-return horizons that exceed the data are marked unavailable.

## Return Calculation

Close-to-close percentage return: `(future_close - evaluation_close) / evaluation_close * 100`, using `Decimal` arithmetic throughout, quantized to 6 decimal places with `ROUND_HALF_EVEN` (matching `deterministic_classifier.py`'s own convention). If a requested horizon's future bar is beyond the supplied data, the result is `None` for that horizon — never fabricated, and excluded from every aggregate statistic that depends on it (mean, median, positive%, min, max, and the corresponding `sample_count`).

## Baseline Comparison

For every configured horizon, an unconditional (`state_dimension=BASELINE`, `state_value="unconditional"`) `MarketStateForwardReturnSummary` is computed across **all** eligible observations with an available return for that horizon — regardless of state. This exists specifically so a caller can honestly compare, e.g., the unconditional 4-bar positive-return percentage against the `trending_up` 4-bar positive-return percentage, with both summaries' `sample_count` visible. **This module does not itself judge whether a state "beats" the baseline** — it exposes both numbers and their sample sizes; drawing a conclusion from that comparison, especially with awareness of the sample sizes and the risk of data-snooping across many state/horizon combinations, is left to the researcher, consistent with `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §26.4's warning about this exact risk.

A baseline row is emitted for every configured horizon even when there are zero eligible observations (`sample_count=0`, `evidence_status=INSUFFICIENT_EVIDENCE`) — a deliberate choice, not an oversight, consistent with "do not suppress the data": an empty result for a configured horizon is itself a fact worth reporting explicitly rather than omitting.

## Minimum-Sample Treatment

`WalkForwardEvaluationParams.minimum_sample_count` (default 30) is compared against the relevant count for each aggregate row: `observation_count` for frequencies, `total_outgoing_transitions_from_state` for transitions, `total_outgoing_transitions` for persistence, and `sample_count` for forward-return summaries. Rows below the threshold are returned in full with their real counts, tagged `EvidenceStatus.INSUFFICIENT_EVIDENCE` rather than omitted or presented as reliable.

## Known Limitations

- **Overlapping windows.** With the default `evaluation_step_size_bars=1`, consecutive classification windows share `window - 1` bars — the resulting observations are not independent samples, and transition/persistence/frequency counts should not be read as counts of independent regime episodes. This mirrors the overlapping-window risk `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §8.4 names for the broader architecture; this harness does not attempt to estimate an effective independent sample size, and a large observation count is not, by itself, evidence of a large amount of independent information.
- **No data-snooping control.** Running this evaluator across many state/horizon/parameter combinations and reporting only the favorable-looking ones would be exactly the walk-forward-overfitting risk named in `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §26.4. This harness provides the measurement primitive; it does not provide (and this task did not authorize) a pre-registration, deflated-performance-criterion, or held-out-era mechanism to guard against that risk.
- **No secular base-rate adjustment.** Forward-return summaries are not measured as excess over a buy-and-hold base rate. A state's positive average return may simply reflect an asset's overall drift during the supplied window rather than any state-specific effect. `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §16.4/Weakness 6 names this same gap for the broader architecture.
- **Classifier thresholds unchanged.** This task discovered no defect in `classify_market_state`; its mathematical behavior is exactly as documented in `docs/MARKET_STATE_CLASSIFIER.md` and was not modified.
- **No persistence.** Results are in-memory only; nothing is written to a database. Any future need to persist walk-forward evidence is a separate, unauthorized-by-this-document decision.

## Relationship to Other Documents

- `docs/MARKET_STATE_CLASSIFIER.md` — the canonical classifier this harness evaluates, unchanged.
- `docs/adr/ADR-0018-canonical-deterministic-regime-classifier.md` — the decision this harness's classifier dependency fulfills.
- `docs/CANONICAL_ARCHITECTURE_MAP.md` — updated to name this evaluator the canonical research evaluator for deterministic market-state walk-forward evidence.
- `docs/MARKET_STATE_AND_REGIME_INTELLIGENCE_ARCHITECTURE.md` §15 (Walk-Forward Validation) and §16 (Baselines and Ablation Testing) — the broader, still-research-only architecture vision this harness takes a further small step toward, without authorizing any of its later phases (HMM research, multi-timeframe synthesis, live filtering).
