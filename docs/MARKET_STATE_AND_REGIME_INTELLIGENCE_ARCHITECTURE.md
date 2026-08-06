# OmniTrade Legacy Engine
# MARKET STATE AND REGIME INTELLIGENCE ARCHITECTURE

Version: 1.0

Status: Architecture and Implementation Planning

Last Updated: 2026-08-06

Authority: Tier-1 Architecture Specification

---

## 1. Purpose

This document defines the Market State and Regime Intelligence architecture for OmniTrade.

Its purpose is to insert a formal market-understanding layer between raw market evidence and trading decisions so that OmniTrade does not jump directly from candles or isolated indicators to BUY, SELL, or WAIT.

The architecture converts observable market evidence into:

- deterministic market-state classifications,
- probabilistic state-transition estimates,
- learned or hidden market regimes,
- multi-timeframe state alignment,
- strategy-regime compatibility evidence,
- calibrated confidence,
- risk-bounded position-size adjustments,
- and fully auditable explanations.

The subsystem is intended to answer:

> What kind of market environment exists now, how certain are we, what may come next, and which strategies are appropriate for that environment?

It does not promise prediction certainty. It produces structured, versioned evidence that informs decisions while preserving Risk Engine final authority.

---

## 2. Governing Principles

This architecture inherits and must preserve the permanent principles of OmniTrade:

1. **Explainability** — every state, regime, probability, filter, and confidence output must be understandable and traceable to recorded evidence.
2. **Risk Engine final authority** — no state or regime model may place a trade, bypass Risk, weaken a stop-loss, or enlarge a position beyond approved limits.
3. **Evidence before adoption** — models and data sources are promoted because walk-forward evidence demonstrates value, not because they are sophisticated or fashionable.
4. **Fail closed** — stale, missing, contradictory, or uncalibrated evidence results in low confidence, WAIT, or rejection rather than fabricated certainty.
5. **Versioned evolution** — state definitions, features, model parameters, transition matrices, and compatibility policies are immutable by version and promoted through explicit human review.
6. **No silent online self-modification** — models may be retrained through a governed process but may not rewrite or deploy themselves automatically.
7. **Production evidence remains authoritative** — research and replay outputs may recommend changes but may not rewrite production history.
8. **Decision quality over isolated profit** — a profitable result does not prove the state inference was correct, and a losing result does not automatically prove it was wrong.
9. **Multi-timeframe truth** — market state is always scoped to an asset, venue, and timeframe. There is no single universal state for an asset.
10. **Independent evidence, not duplicated noise** — agreement matters only when evidence sources add genuinely distinct information.

---

## 3. Architectural Position

Market State and Regime Intelligence is a subsystem of the permanent **Market Intelligence Engine** and supplies evidence to Strategy Evolution, Decision Intelligence, Portfolio Intelligence, and the Risk Engine.

It is not a fifth foundational engine.

```text
External and Internal Data Sources
        │
        ▼
Canonical Evidence Ingestion
        │
        ▼
Feature Extraction and Data Quality
        │
        ▼
Market State and Regime Intelligence
        │
        ├── Deterministic State Classifier
        ├── Transition Model
        ├── Hidden Regime Model
        ├── Multi-Timeframe Synthesizer
        ├── Strategy-Regime Compatibility
        └── Confidence Calibration
        │
        ▼
Strategy Engine / Ensemble
        │
        ▼
Signal Confidence Scorer
        │
        ▼
Risk Engine — Mandatory Final Gate
        │
        ▼
Execution / Position Management
        │
        ▼
Decision Records, Replay, Outcomes, Calibration
```

### 3.1 Boundary With Strategy Engine

The market-state subsystem describes the environment.

The Strategy Engine proposes an action within that environment.

A regime model may initially operate only as a filter or evidence source. Standalone trade generation by a regime model is a later research possibility and is not authorized by this document.

### 3.2 Boundary With Risk Engine

Market state and confidence may:

- suppress a trade,
- reduce confidence,
- reduce the proposed position size,
- recommend WAIT,
- or add cautionary evidence.

They may never:

- approve a trade rejected by Risk,
- increase exposure beyond Risk limits,
- omit a required stop-loss,
- bypass kill switches,
- override cooldowns,
- or re-arm trading automatically.

### 3.3 Boundary With Decision Intelligence

Market State and Regime Intelligence operates at decision time.

Decision Intelligence records the resulting state evidence, model outputs, disagreement, confidence, risk response, and eventual outcome for later analysis.

Decision Intelligence may diagnose past state-classification failures but must not feed hindsight information into the original decision record.

---

## 4. Core Concepts

### 4.1 Market State

A **market state** is an explainable description of observable behavior over a defined asset, venue, timeframe, and evaluation timestamp.

Examples:

- `trending_up`
- `trending_down`
- `ranging`
- `high_volatility`
- `low_volatility`
- `low_liquidity`
- `volume_expansion`
- `order_flow_buy_dominant`

A state is not permanent. It is a point-in-time classification.

### 4.2 Sideways or Ranging Market

A ranging market moves inside a bounded price area without a sustained directional advance or decline over the selected timeframe.

It may contain substantial short-term movement while producing little net progress.

A ranging classification must always identify:

- the timeframe,
- the observed range,
- trend-strength evidence,
- volatility conditions,
- and confidence.

### 4.3 Market Regime

A **market regime** is a broader, recurring market environment associated with characteristic behavior and strategy performance.

Examples may include:

- persistent trend,
- calm mean reversion,
- volatile mean reversion,
- liquidity stress,
- momentum expansion,
- panic liquidation,
- accumulation-like behavior,
- or risk-off correlation shock.

Regime names must not be assigned merely because they sound plausible. A learned regime is first identified by a neutral model ID and an empirical characteristic profile.

### 4.4 Hidden Regime

A hidden regime is a latent state that is not directly observed but is inferred from observable evidence such as returns, volatility, volume, spread, liquidity, and cross-asset behavior.

Example:

```text
Observed evidence:
- positive return bias
- increasing relative volume
- narrowing spread
- rising buy-initiated trade ratio
- moderate volatility

Model output:
- regime_id = hmm_regime_2
- probability = 0.76
```

Only after repeated analysis may humans attach a descriptive label such as `persistent_uptrend_high_participation`.

### 4.5 State Transition

A state transition is movement from one state or regime to another across evaluation periods.

For a three-state model:

```text
trending_up   → trending_up
trending_up   → ranging
trending_up   → trending_down
ranging       → trending_up
ranging       → ranging
ranging       → trending_down
trending_down → trending_up
trending_down → ranging
trending_down → trending_down
```

Transition probabilities estimate what historically followed the current state under a defined model version and training window. They are conditional estimates, not guarantees.

### 4.6 State Persistence or Stickiness

State persistence measures the tendency of a state to remain active across successive periods.

High persistence may support trend-following behavior.

Low persistence or rapid switching may indicate choppiness, unstable inference, inadequate features, or an inappropriate timeframe.

### 4.7 Regime Compatibility

Regime compatibility describes whether a strategy has demonstrated acceptable performance in a particular regime under walk-forward testing.

Examples:

- trend-following strategy + persistent uptrend = potentially compatible,
- mean-reversion strategy + stable range = potentially compatible,
- breakout strategy + low-volume choppy range = potentially incompatible.

Compatibility is empirical and versioned. It is never assumed solely from strategy theory.

---

## 5. Evidence Architecture

The subsystem must distinguish evidence families because each describes a different aspect of market reality.

### 5.1 Phase A — Native and Immediately Accessible Evidence

Initial implementation should use evidence already available or close to the existing data path:

- OHLCV candles,
- log returns,
- rolling returns,
- realized volatility,
- ATR,
- moving-average slope,
- ADX or comparable trend-strength measure,
- relative volume,
- volume acceleration,
- drawdown from recent high,
- distance from rolling range boundaries,
- spread when available,
- simple liquidity and candle-gap health,
- cross-asset returns and correlations.

### 5.2 Phase B — Market Microstructure and Derivatives Evidence

Later evidence may include:

- order-book snapshots,
- order-book imbalance,
- depth by price band,
- spread change,
- order-book replenishment and cancellation behavior,
- executed trade flow,
- buy-initiated versus sell-initiated trade ratio,
- large-trade activity,
- perpetual funding rates,
- futures basis,
- open interest,
- liquidation data,
- options-implied volatility and skew where available.

### 5.3 Phase C — Capital Flow, On-Chain, News, Macro, and Fundamentals

Later evidence may include:

- Bitcoin and crypto ETF creations, redemptions, or net flows,
- exchange inflows and outflows,
- large on-chain transfers,
- miner behavior,
- dormant coin movement,
- stablecoin issuance and exchange flows,
- news events and sentiment,
- macroeconomic releases,
- interest rates and yield curves,
- inflation and labor data,
- dollar and liquidity conditions,
- equity market breadth,
- sector breadth,
- company fundamentals,
- earnings and guidance,
- valuation metrics,
- insider and institutional disclosures.

### 5.4 Public Observability Does Not Equal Certainty

Evidence such as a large blockchain transfer does not prove a purchase or imminent sale.

Examples:

- an exchange withdrawal may be custody reshuffling,
- an exchange deposit may not result in a sale,
- a large executed trade may be hedged elsewhere,
- ETF flow data may be delayed,
- order-book orders may be canceled,
- news sentiment may misunderstand the event.

Every evidence source must preserve:

- source,
- observation time,
- effective time,
- ingestion time,
- confidence or data-quality status,
- transformation method,
- and known interpretation limitations.

---

## 6. Canonical Data Contracts

### 6.1 Market Evidence Observation

```text
MarketEvidenceObservation
- observation_id
- asset_id
- product
- venue
- timeframe
- evidence_family
- feature_name
- value
- unit
- observed_at
- effective_at
- ingested_at
- source
- source_version
- quality_status
- quality_reason
- transformation_version
```

### 6.2 Deterministic State Result

```text
DeterministicStateResult
- state_result_id
- asset_id
- product
- venue
- timeframe
- evaluated_at
- primary_state
- secondary_states
- confidence
- feature_snapshot
- rule_contributions
- classifier_version
- data_quality_status
- explanation
```

### 6.3 Hidden Regime Result

```text
HiddenRegimeResult
- regime_result_id
- asset_id
- product
- venue
- timeframe
- evaluated_at
- model_name
- model_version
- training_window_start
- training_window_end
- regime_probabilities
- selected_regime_id
- selected_probability
- emission_characteristics
- transition_probabilities
- data_quality_status
- explanation
```

### 6.4 Multi-Timeframe State Result

```text
MultiTimeframeStateResult
- result_id
- asset_id
- product
- venue
- evaluated_at
- timeframe_results
- alignment_score
- conflict_score
- dominant_context
- confidence_adjustment
- policy_version
- explanation
```

### 6.5 Strategy-Regime Compatibility Result

```text
StrategyRegimeCompatibilityResult
- compatibility_result_id
- strategy_id
- parameter_set_id
- asset_id
- timeframe
- evaluated_at
- state_or_regime_id
- compatibility
- expected_behavior
- supporting_sample_count
- walk_forward_metrics
- confidence
- policy_version
- explanation
```

### 6.6 Final Regime Evidence Package

```text
RegimeEvidencePackage
- package_id
- evaluated_at
- asset_id
- product
- venue
- strategy_id
- parameter_set_id
- deterministic_state
- hidden_regime
- multi_timeframe_context
- evidence_agreement
- evidence_conflict
- compatibility_result
- calibrated_confidence
- recommended_action_modifier
- recommended_size_multiplier
- data_quality_status
- model_and_policy_versions
- explanation
```

---

## 7. Deterministic State Classifier

The deterministic classifier is the first implementation baseline.

It must be:

- explainable,
- reproducible,
- pure with respect to its inputs,
- versioned,
- independently testable,
- and usable when learned models are unavailable.

### 7.1 Initial State Dimensions

The first classifier should produce separate dimensions rather than forcing all behavior into one label:

#### Direction

- `trending_up`
- `trending_down`
- `ranging`
- `direction_uncertain`

#### Volatility

- `low_volatility`
- `normal_volatility`
- `high_volatility`
- `volatility_uncertain`

#### Participation

- `volume_contracting`
- `normal_participation`
- `volume_expanding`
- `participation_uncertain`

#### Liquidity

- `healthy_liquidity`
- `thin_liquidity`
- `liquidity_stress`
- `liquidity_unknown`

### 7.2 No Arbitrary Threshold Is Permanent

Initial thresholds are hypotheses.

Every threshold must be:

- stored in a versioned parameter set,
- evaluated through walk-forward replay,
- compared with alternate thresholds,
- and reviewable through an explanation trail.

### 7.3 Ranging Detection

A ranging classifier should not rely on net return alone.

It should consider at least:

- low directional slope,
- low or declining trend strength,
- repeated reversals within a bounded range,
- price remaining inside rolling support and resistance bands,
- and lack of sustained higher highs or lower lows.

A market may be `ranging_high_volatility` or `ranging_low_volatility`; these are materially different environments.

---

## 8. Transition Probability Model

### 8.1 Purpose

The transition model estimates the probability distribution of the next state given the current state and the model's defined assumptions.

### 8.2 Transition Matrix

For a three-state directional model:

```text
P =
                 Next Up   Next Range   Next Down
Current Up         p11        p12          p13
Current Range      p21        p22          p23
Current Down       p31        p32          p33
```

Each row must sum to one.

### 8.3 Multi-Step Forecasts

Multi-step probabilities must use valid transition-matrix operations or an explicitly defined alternative model.

The system must not assume that a two-step probability is obtained by blindly squaring one one-step probability unless the full path and assumptions justify it.

### 8.4 Overlapping Window Risk

Feature windows and evaluation periods can share most of the same observations, producing dependence and inflated apparent sample size.

The architecture must record:

- feature lookback,
- prediction horizon,
- evaluation stride,
- overlap ratio,
- and effective independent sample estimate where feasible.

Research must compare overlapping and non-overlapping evaluation designs.

### 8.5 Transition Instability

Transition probabilities may change over time.

The model must monitor:

- rolling transition estimates,
- confidence intervals or uncertainty estimates,
- regime duration drift,
- and state-frequency drift.

A stale transition model must not be treated as current truth.

---

## 9. Hidden Markov Model Research Architecture

### 9.1 Role

A Hidden Markov Model is one optional learned regime detector.

It is not the sole source of market understanding and is not authorized as a direct execution authority.

### 9.2 Observations

Initial HMM research should use a small, explainable feature set such as:

- normalized return,
- realized volatility,
- relative volume,
- trend strength,
- drawdown,
- and spread or liquidity proxy if reliable.

Feature expansion must be justified through ablation evidence.

### 9.3 Hidden States

HMM states begin as neutral IDs:

- `hmm_state_0`
- `hmm_state_1`
- `hmm_state_2`
- `hmm_state_3`

Human-readable names may be added only after examining:

- feature distributions,
- average returns,
- volatility,
- duration,
- transition behavior,
- strategy performance,
- and stability across training windows.

### 9.4 Number of States

The number of states is a model-selection decision, not a storytelling decision.

Candidate state counts should be compared using:

- held-out likelihood,
- information criteria where appropriate,
- stability across walk-forward folds,
- interpretability,
- and incremental strategy value.

### 9.5 State Label Switching

HMM state numeric IDs may switch meaning across retraining runs.

The architecture must not assume `state_1` always represents the same environment.

Each model version must preserve its own emission profile, transition matrix, training data range, and optional human-reviewed semantic mapping.

### 9.6 HMM Failure Modes

Known risks include:

- excessive sensitivity to initialization,
- unstable states,
- state collapse,
- overfitting,
- insufficient samples,
- non-stationary behavior,
- misleading semantic labels,
- and probability outputs that are not calibrated for trading outcomes.

Any of these may disqualify a model from promotion.

---

## 10. Multi-Timeframe Regime Intelligence

### 10.1 Principle

The same asset can be:

- trending upward on the daily timeframe,
- ranging on the hourly timeframe,
- and declining on the five-minute timeframe.

All state and regime outputs must therefore include timeframe.

### 10.2 Initial Timeframes

Research may begin with:

- 15-minute,
- 1-hour,
- 4-hour,
- and 1-day.

The final set must follow data availability and strategy horizon.

### 10.3 Alignment

Alignment measures whether relevant timeframes support the same directional or environmental interpretation.

Examples:

- daily up + four-hour up + one-hour up = strong directional alignment,
- daily up + four-hour range + one-hour down = mixed context,
- daily high volatility + one-hour low liquidity = elevated execution risk.

### 10.4 No Universal Alignment Rule

More agreement is not automatically better.

A mean-reversion strategy may intentionally act against a short-term move within a stable longer-term range.

Alignment must be evaluated relative to the strategy's design and holding horizon.

---

## 11. Evidence Agreement and Conflict

### 11.1 Independent Specialists

The long-term system may use multiple specialist models:

- deterministic trend and volatility classifier,
- Hidden Markov regime model,
- order-flow model,
- on-chain model,
- ETF and capital-flow model,
- derivatives model,
- news and macro model,
- cross-asset model,
- fundamental model,
- statistical model,
- neural model.

### 11.2 Agreement Is Not a Vote Count

Five highly correlated price-derived indicators do not represent five independent confirmations.

The agreement engine must account for:

- evidence-family diversity,
- feature overlap,
- model correlation,
- data freshness,
- historical reliability,
- and regime-specific calibration.

### 11.3 Conflict Is Valuable Evidence

Conflict must be preserved rather than averaged away.

Example:

```text
Trend model: bullish
Order flow: bearish
ETF flow: positive but delayed
Funding: overheated
News: neutral
```

The system may reduce confidence or WAIT, but it must record the disagreement so Decision Intelligence can later determine which evidence was informative.

---

## 12. Strategy-Regime Compatibility Layer

### 12.1 Default Operating Mode: Filter

The first production-eligible use of regime intelligence should be to filter or modify strategy proposals, not to generate standalone trades.

```text
Strategy Proposal
        │
        ▼
Regime Compatibility Evaluation
        │
        ├── compatible → continue
        ├── uncertain  → reduce confidence or WAIT
        └── incompatible → suppress proposal
```

### 12.2 Compatibility Table

Compatibility must be learned from walk-forward evidence by:

- strategy,
- parameter-set version,
- asset,
- timeframe,
- venue where relevant,
- state or regime,
- and holding horizon.

### 12.3 Action Modifiers

Allowed outputs may include:

- `allow_unchanged`
- `allow_reduce_confidence`
- `allow_reduce_size`
- `wait_regime_uncertain`
- `suppress_regime_incompatible`
- `research_only_no_action`

### 12.4 No Circular Evaluation

A state classifier must not be judged solely by whether it agrees with the strategy it filters.

State quality and strategy value must be measured separately.

---

## 13. Confidence Architecture

### 13.1 Distinct Confidence Types

The system must not collapse all uncertainty into one unexplained percentage.

It should distinguish:

- **state confidence** — confidence in the deterministic classification,
- **regime posterior probability** — model probability assigned to a hidden state,
- **transition confidence** — uncertainty in estimated state transitions,
- **data-quality confidence** — reliability and freshness of inputs,
- **evidence-agreement score** — degree of independent support,
- **strategy-regime compatibility confidence** — strength of walk-forward evidence,
- **final decision confidence** — calibrated estimate used by the decision path.

### 13.2 Calibration

A score of 0.80 should be evaluated against outcomes across many comparable decisions.

If decisions assigned approximately 0.80 confidence succeed only 0.55 of the time under the defined target, the confidence system is miscalibrated.

Calibration must be evaluated by:

- probability bins,
- asset,
- strategy,
- regime,
- timeframe,
- horizon,
- and model version.

### 13.3 Confidence Is Not Certainty

Confidence must never be described as a guarantee.

It represents a model estimate under specific data, assumptions, and calibration history.

---

## 14. Position Sizing

### 14.1 Safety Rule

Regime and confidence outputs may only reduce a base size already permitted by strategy and Risk policy.

```text
risk_approved_base_size
× confidence_multiplier
× regime_multiplier
× liquidity_multiplier
= proposed_final_size
```

The Risk Engine then performs its own mandatory final evaluation.

### 14.2 Allowed Multipliers

All multipliers must be bounded between zero and one unless a future ADR explicitly approves a different design.

### 14.3 Minimum Viable Size

If scaling reduces the order below exchange minimums, the trade is rejected rather than rounded upward.

### 14.4 Kelly Criterion

Kelly-style sizing may be researched later but is not authorized for production by this document.

If researched, fractional Kelly and hard Risk caps are mandatory, and estimated edge uncertainty must be accounted for.

---

## 15. Walk-Forward Validation

### 15.1 Mandatory Principle

No state classifier, HMM, compatibility policy, transition model, or confidence model may be promoted based only on performance measured on the same data used to design or train it.

### 15.2 Walk-Forward Process

```text
Training Window
        │
        ▼
Fit state/regime model and policies
        │
        ▼
Freeze model version
        │
        ▼
Evaluate next unseen window
        │
        ▼
Record predictions and outcomes
        │
        ▼
Advance time
        │
        ▼
Retrain or update according to declared policy
```

### 15.3 No Future Leakage

At evaluation timestamp T, the system may use only evidence that would have been known by T, including realistic publication and ingestion delays.

Examples:

- final candle values are unavailable before candle close,
- revised macro data cannot be substituted for the first release,
- ETF flows published later cannot influence an earlier decision,
- future regime labels may not leak into current features.

### 15.4 Purging and Embargo

Where labels and feature windows overlap, research should use purging or embargo periods to reduce leakage between train and validation folds.

### 15.5 Replay Determinism

Each replay must pin:

- input dataset versions,
- feature versions,
- state classifier version,
- model version,
- parameter set,
- strategy version,
- risk policy version,
- fee and slippage assumptions,
- random seeds where applicable,
- and evaluation clock.

---

## 16. Baselines and Ablation Testing

Every complex model must compete against simpler alternatives.

### 16.1 Required Baselines

- strategy with no regime filter,
- deterministic trend-regime filter,
- volatility-only filter,
- simple moving-average regime baseline,
- naive persistence baseline,
- fixed-position-size strategy,
- and WAIT or buy-and-hold where relevant.

### 16.2 Required Comparisons

- strategy alone,
- strategy + deterministic state,
- strategy + HMM state,
- strategy + deterministic/HMM agreement,
- strategy + enhanced evidence,
- strategy + confidence sizing.

### 16.3 Ablation Questions

The system must be able to answer:

- Did HMM evidence improve results beyond deterministic state?
- Did order-book evidence add value after price and volume were known?
- Did multi-timeframe alignment improve decisions?
- Did confidence scaling reduce drawdown?
- Did a new source improve net performance after data cost and latency?
- Did added complexity merely fit noise?

### 16.4 Metrics

Evaluation should include:

- net return,
- return percentage,
- maximum drawdown,
- downside deviation,
- win rate,
- profit factor,
- expectancy,
- average win and loss,
- trade count,
- turnover,
- fee drag,
- slippage,
- time in market,
- state accuracy,
- transition calibration,
- probability calibration,
- regime stability,
- and decision-quality metrics.

Profit alone is insufficient.

---

## 17. Model Promotion Lifecycle

### 17.1 Lifecycle

1. `defined`
2. `research_only`
3. `walk_forward_validated`
4. `paper_shadow`
5. `paper_advisory`
6. `paper_filter_active`
7. `live_shadow`
8. `live_advisory`
9. `live_filter_candidate`
10. `retired`

No step automatically promotes to the next.

### 17.2 Shadow Mode

In shadow mode, the model records what it would have recommended but cannot alter execution.

### 17.3 Promotion Evidence

Promotion requires:

- minimum sample size,
- walk-forward success,
- baseline improvement,
- acceptable drawdown,
- calibration evidence,
- stable model behavior,
- explainability review,
- security and data-source review,
- and explicit human approval.

### 17.4 Rollback

Every promoted model must support immediate rollback to the prior version or deterministic baseline without requiring schema reconstruction.

---

## 18. Explainability and Audit Requirements

Every state-informed decision must preserve:

- canonical input evidence,
- source timestamps and quality,
- derived feature values,
- deterministic state and contributions,
- hidden regime probabilities,
- transition probabilities,
- multi-timeframe states,
- model agreement and conflict,
- compatibility result,
- confidence factors,
- size adjustments,
- strategy proposal,
- Risk Engine decision,
- execution outcome,
- and all version pins.

### 18.1 Explanation Example

```text
BTC-USD BUY proposal from strategy entry_limit_rebound_v1 was allowed with
reduced size.

Market context:
- 1h direction: ranging, confidence 0.72
- 4h direction: trending_up, confidence 0.66
- 1d direction: trending_up, confidence 0.81
- HMM regime: hmm_state_2, probability 0.74
- regime profile: positive return bias, moderate volatility, expanding volume

Compatibility:
- strategy historically acceptable in 1h range within 4h/1d uptrend
- walk-forward sample count: 286

Conflicts:
- funding elevated
- order-book evidence unavailable

Confidence adjustment:
- raw strategy strength 0.78
- final calibrated confidence 0.63
- proposed size multiplier 0.60

Risk decision:
- approved after independent Risk evaluation
```

### 18.2 No Fabricated Narratives

Explanations must be generated from structured evidence. The system may not invent causal statements such as “whales are accumulating” unless the underlying evidence supports that precise claim.

---

## 19. Data Quality and Failure Handling

### 19.1 Required Checks

- missing observations,
- stale data,
- duplicate data,
- out-of-order timestamps,
- provider disagreement,
- unit mismatch,
- publication delay,
- survivorship bias,
- corporate-action adjustment for stocks,
- exchange outages,
- and feature-computation failure.

### 19.2 Fail-Closed Outcomes

Possible outcomes include:

- `state_unknown`
- `regime_unavailable`
- `confidence_undefined`
- `data_stale`
- `evidence_conflict_excessive`
- `model_out_of_scope`
- `wait_insufficient_evidence`

### 19.3 Partial Evidence

The system may continue with partial evidence only under an explicit versioned policy defining:

- which evidence is mandatory,
- which is optional,
- how missing evidence reduces confidence,
- and when the result becomes non-actionable.

---

## 20. Repository Architecture

Target module layout:

```text
apps/api/app/services/market_state/
├── contracts.py
├── feature_builder.py
├── data_quality.py
├── deterministic/
│   ├── direction.py
│   ├── volatility.py
│   ├── participation.py
│   └── liquidity.py
├── transitions/
│   ├── matrix.py
│   ├── estimator.py
│   └── stability.py
├── hidden_regimes/
│   ├── interface.py
│   ├── hmm.py
│   ├── labeling.py
│   └── diagnostics.py
├── multi_timeframe/
│   ├── synthesizer.py
│   └── policy.py
├── compatibility/
│   ├── evaluator.py
│   └── registry.py
├── confidence/
│   ├── synthesizer.py
│   ├── calibration.py
│   └── diagnostics.py
├── explanations.py
└── registry.py

apps/api/app/services/replay/market_state/
├── walk_forward.py
├── purging.py
├── baselines.py
├── ablation.py
└── metrics.py
```

Exact paths must be reconciled with current repository reality before implementation. This document defines responsibility boundaries, not permission to create duplicate services.

### 20.1 Dependency Direction

```text
data services
      ↓
market_state feature and model services
      ↓
strategy/signal orchestration
      ↓
risk
      ↓
execution
```

Market-state modules must not import from execution or mutate accounts, orders, or positions.

---

## 21. Database and Persistence Impact

Architecture anticipates versioned persistence for:

- market evidence observations or references,
- feature snapshots,
- deterministic state results,
- hidden regime results,
- transition-model versions,
- calibration artifacts,
- strategy-regime compatibility results,
- and model-promotion records.

No database migration is authorized by this architecture document alone.

Before implementation, the repository must be audited to determine whether existing `model_outputs`, Decision Snapshots, replay evidence, or related tables can hold these records without duplication.

---

## 22. API and UI Surfaces

Potential read-only API surfaces:

```text
GET /market-state/current
GET /market-state/history
GET /market-state/:asset_id/multi-timeframe
GET /market-state/models
GET /market-state/models/:version/diagnostics
GET /market-state/strategy-compatibility
GET /market-state/replay/:run_id
```

Potential UI capabilities:

- current state by timeframe,
- regime probability history over price,
- transition matrix viewer,
- deterministic versus learned-state comparison,
- model disagreement display,
- confidence calibration chart,
- strategy-regime performance matrix,
- walk-forward fold results,
- ablation comparison,
- and model version/promotion status.

All return, P&L, and balance displays must preserve existing dollar-and-percentage and balance-type labeling conventions.

---

## 23. Immediate Implementation Plan

Implementation must proceed in bounded phases.

### Phase 0 — Repository Audit and Architecture Reconciliation

Objectives:

- locate all existing regime, trend-filter, confidence, replay, evidence, and model-output code,
- compare implemented behavior with governing documents,
- identify reusable contracts,
- identify documentation drift,
- and determine whether an ADR is required.

Deliverable:

- repository audit report and exact implementation plan,
- no runtime behavior change.

### Phase 1 — Deterministic State Baseline

Scope:

- OHLCV only,
- one asset initially,
- selected research timeframes,
- deterministic direction, volatility, and participation states,
- structured explanation,
- unit tests,
- replay output only.

No production filtering.

### Phase 2 — Walk-Forward and Baseline Framework

Scope:

- chronological training and evaluation windows,
- no-lookahead clock,
- baselines,
- overlap tracking,
- purge/embargo support where required,
- and reproducible metrics.

### Phase 3 — Transition Model

Scope:

- state-transition counts,
- transition matrix,
- persistence estimates,
- uncertainty and sample counts,
- stability diagnostics,
- research-only forecasts.

### Phase 4 — HMM Research Model

Scope:

- small feature set,
- neutral state IDs,
- multiple state-count candidates,
- deterministic seeds or recorded random seeds,
- walk-forward evaluation,
- stability and label-switching diagnostics,
- research-only output.

### Phase 5 — Strategy Compatibility Experiment

Compare:

- strategy alone,
- deterministic filter,
- HMM filter,
- agreement filter,
- and confidence-weighted reduction.

### Phase 6 — Shadow Mode

The selected model records recommendations beside live or paper decisions but cannot alter them.

### Phase 7 — Paper Advisory or Filter Candidate

Only after evidence review may a model influence paper decisions under explicit human-approved policy.

### Phase 8 — Enhanced Evidence

Add one evidence family at a time, beginning with the highest-value and most auditable source.

Recommended order:

1. relative volume and cross-asset confirmation,
2. spread, depth, and executed trade flow,
3. derivatives data,
4. ETF and on-chain flows,
5. news and macro,
6. fundamentals for stocks.

### Phase 9 — Live Shadow and Governed Promotion

No live behavior modification occurs without a separate implementation plan, validation report, governance approval, and explicit operator authorization.

---

## 24. Acceptance Criteria for Version 1

Version 1 is complete only when:

1. State outputs are asset-, venue-, timeframe-, timestamp-, and version-scoped.
2. Deterministic state classifications are fully explainable.
3. Replay prevents future leakage.
4. Walk-forward evaluation is reproducible.
5. Baselines exist and complex models are compared against them.
6. HMM states remain neutral until empirically characterized.
7. Transition probabilities include sample counts and uncertainty.
8. Confidence types are separated and calibrated.
9. Strategy-regime compatibility is based on walk-forward evidence.
10. Position sizing can only scale down before independent Risk evaluation.
11. Missing or stale evidence fails closed.
12. All outputs are versioned and auditable.
13. No model can place orders directly.
14. No autonomous promotion or self-modification exists.
15. The Risk Engine remains the mandatory final authority.

---

## 25. Explicit Non-Goals

This architecture does not authorize:

- guaranteed market prediction,
- a claim that HMMs are the universal hedge-fund method,
- immediate production deployment,
- direct regime-model order placement,
- autonomous live promotion,
- autonomous strategy rewriting,
- risk-limit expansion from confidence,
- leverage or derivatives trading,
- replacement of the Risk Engine,
- replacement of Decision Intelligence,
- indiscriminate ingestion of every available data source,
- causal claims unsupported by evidence,
- or acceptance of marketing claims as technical proof.

---

## 26. Risks

### 26.1 Overfitting

The system may discover states that describe the past but do not generalize.

### 26.2 Non-Stationarity

Market behavior and transition probabilities change.

### 26.3 False Precision

Probabilities may look exact while resting on small or biased samples.

### 26.4 Data Snooping

Repeatedly testing many states, features, and thresholds can produce accidental winners.

### 26.5 Correlated Evidence

Multiple indicators may repeat the same price information and create an illusion of confirmation.

### 26.6 Semantic Overreach

A statistical cluster may be incorrectly labeled “accumulation,” “whale buying,” or another causal story.

### 26.7 Complexity Without Value

Additional data, models, and providers may increase fragility without improving decisions.

### 26.8 Operational Risk

Provider latency, revisions, gaps, and unit mismatches may corrupt real-time state inference.

### 26.9 Regime Delay

A model may recognize a new regime only after much of the move has occurred.

### 26.10 Strategy Suppression Risk

An inaccurate regime filter may block profitable trades. Counterfactual tracking is required to measure this.

---

## 27. Research Questions

The architecture should support rigorous answers to:

1. Which deterministic state definitions remain stable across assets and eras?
2. Does an HMM improve decisions beyond a simple trend and volatility classifier?
3. How many hidden states are stable and useful?
4. Which timeframes matter for each strategy?
5. When does multi-timeframe agreement help or hurt?
6. Which evidence families add independent information?
7. How quickly do transition probabilities drift?
8. Are state probabilities calibrated?
9. Which strategies perform best in each regime?
10. Does confidence scaling reduce drawdown without eliminating edge?
11. Which rejected trades would have won?
12. Which allowed trades were filtered correctly or incorrectly?
13. Does order-flow evidence retain value after fees and latency?
14. Do ETF or on-chain flows improve decisions after publication delays?
15. Can the system explain state changes without fabricating causality?

---

## 28. Relationship to Existing OmniTrade Documents

This architecture refines rather than replaces existing documents.

- `PROJECT_CONSTITUTION.md` governs explainability, safety, evidence, decision quality, and stewardship.
- `SYSTEM_ARCHITECTURE.md` defines the permanent four-engine structure and service boundaries.
- `STRATEGY_ENGINE.md` already defines a deterministic trend-regime filter and regime-sensitive strategy behavior.
- `AI_LAYER.md` already defines a regime classifier, confidence scorer, allocator, explanation generator, and model versioning.
- `RISK_ENGINE.md` remains the mandatory final gate and already permits confidence only to scale size downward.
- `DECISION_INTELLIGENCE_ENGINE.md` preserves market regime, confidence, evidence, risk adjustments, outcomes, and calibration.
- `DATA_SOURCES.md` governs ingestion reliability, provider limitations, and data-source awareness.
- `DECISION_REPLAY_ENGINE.md` and related replay/evidence documents govern deterministic historical evaluation where present in the repository.

Any conflict must be resolved by the higher-authority governing documents or a new ADR. This file must not silently override them.

---

## 29. ADR Assessment

An ADR is recommended before production implementation because this architecture formalizes a cross-cutting subsystem spanning Market Intelligence, Strategy Evolution, AI, Replay, Decision Intelligence, Risk, persistence, and UI.

The ADR should decide:

- whether Market State and Regime Intelligence is formally recognized as a subsystem of Market Intelligence,
- the deterministic-baseline-first rule,
- the default filter-only operating mode,
- the separation of confidence types,
- walk-forward validation as a promotion prerequisite,
- downward-only confidence sizing,
- and the no-direct-execution boundary.

The ADR must not introduce a fifth foundational engine.

---

## 30. Final Architectural Position

The central lesson incorporated from the reviewed video series is not that a Hidden Markov Model is a secret formula for profit.

The durable architectural lesson is:

> OmniTrade should not jump directly from raw market data to a trade. It should first estimate the market environment, preserve uncertainty and disagreement, determine whether a strategy is compatible with that environment, validate the relationship without future leakage, and only then send a fully explained proposal to the Risk Engine.

The intended flow is therefore:

```text
Observe reality
      ↓
Measure and validate evidence
      ↓
Estimate deterministic states
      ↓
Infer learned regimes
      ↓
Measure transitions, persistence, and uncertainty
      ↓
Compare timeframes and independent evidence
      ↓
Evaluate strategy-regime compatibility
      ↓
Calibrate confidence
      ↓
Reduce or suppress exposure when warranted
      ↓
Risk Engine final authority
      ↓
Execute, record, replay, and learn
```

This architecture provides a disciplined path from OmniTrade's current candle-and-strategy foundation toward a richer, evidence-driven understanding of market conditions without sacrificing explainability, auditability, safety, or human governance.
