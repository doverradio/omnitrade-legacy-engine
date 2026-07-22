# VISUAL DECISION INTELLIGENCE AND MULTIMODAL LEARNING ARCHITECTURE

## OmniTrade Legacy Engine

**Status:** Architecture proposal — implementation requires explicit approval  
**Purpose:** Turn every market decision, strategy vote, portfolio consequence, and visual pattern into versioned, replayable, testable learning material for humans and AI agents.  
**Constitutional alignment:** Explainability, Decision Intelligence, Continuous Learning, Decision Snapshot, Decision Quality, Evidence, Safety, Institutional Memory, and Capital Stewardship.

---

# 1. Executive Vision

OmniTrade should not merely display a chart after decisions are made. It should create a **versioned Decision Learning Environment** in which:

1. Market data is preserved as structured time-series truth.
2. Every strategy vote, confidence score, risk decision, execution event, and portfolio consequence is aligned to that same timeline.
3. Humans can turn overlays on and off in an interactive chart.
4. AI agents can consume the exact same replay through structured data, derived features, and optionally deterministic visual renderings.
5. Every completed or rejected decision becomes a labeled learning example.
6. Pattern-discovery systems can identify recurring market and decision regimes without being told what to look for.
7. Candidate improvements must defeat the incumbent through leakage-safe replay, walk-forward validation, and risk-adjusted evaluation before promotion.

The goal is not to teach an AI to admire a chart. The goal is to create a **shared, reproducible evidence surface** where humans and machines can inspect the same decision history from different perspectives.

---

# 2. Core Principle: The Chart Is a View, Not the Source of Truth

The chart is valuable because it makes relationships visible. It must never become the canonical data source.

Canonical sources remain structured and immutable:

- candles and market observations
- strategy proposals and votes
- aggregate decisions
- confidence and uncertainty
- risk evaluations and reason codes
- campaign authority
- orders, fills, fees, and slippage
- position lifecycle events
- portfolio equity and cash
- realized and unrealized P&L
- counterfactual outcomes
- model, feature, dataset, and policy versions

The visual rendering is a deterministic projection of those records.

This distinction prevents:

- image interpretation errors from corrupting financial truth
- loss of numerical precision
- untraceable chart changes
- training leakage caused by accidentally rendering future information
- dependence on one chart library or UI implementation

---

# 3. Proposed Capability Name

## Decision Arena Observatory

The **Decision Arena Observatory** is a capability spanning the four permanent engines rather than a fifth foundational engine.

It provides:

- synchronized market/portfolio/decision timelines
- interactive chart overlays
- deterministic replay frames
- machine-readable replay packages
- optional multimodal chart renderings
- supervised learning datasets
- unsupervised and self-supervised pattern discovery datasets
- champion/challenger evaluation
- explainable promotion evidence

It does not replace:

- Market Intelligence
- Strategy Evolution
- Decision Intelligence
- Portfolio Intelligence
- the Risk Engine
- Campaign authority
- Execution and reconciliation

---

# 4. Human UI Concept

## 4.1 Primary chart

The initial instrument view should support:

- candlesticks or close-price line
- selectable interval: 1m, 5m, 15m, 1h, 4h, 1d when data exists
- time-range selector
- live mode and historical replay mode
- crosshair with synchronized values
- zoom and pan
- event inspection drawer

## 4.2 Overlay checkboxes

Minimum overlay set:

- [ ] Asset price
- [ ] OmniTrade portfolio value
- [ ] Strategy aggregate action
- [ ] BUY / SELL / HOLD events
- [ ] AI confidence
- [ ] Strategy votes
- [ ] Position exposure
- [ ] Realized and unrealized P&L
- [ ] Risk rejections and resize events
- [ ] Fees and slippage
- [ ] Benchmark buy-and-hold value
- [ ] Counterfactual best-known outcome, clearly labeled as hindsight-only

## 4.3 Correct visual representation

Not every item should be drawn as a continuous line.

Recommended encoding:

- Price: candle or line on price axis
- Portfolio equity: line on normalized or secondary value axis
- Buy-and-hold benchmark: line on the same normalized-return axis as portfolio
- BUY: upward marker
- SELL: downward marker
- HOLD: subtle dot or optional hidden event band
- Confidence: bounded 0–100 line or translucent panel
- Strategy votes: stacked vote counts, compact heat strip, or expandable panel
- Risk events: warning markers with reason-code tooltip
- Position exposure: filled area or percentage line

This prevents misleading “three-line” simplification where categorical actions are treated as continuous values.

## 4.4 Normalization modes

Users should be able to switch among:

1. **Absolute mode** — actual BTC price and dollar portfolio value.
2. **Indexed mode** — both price and portfolio normalized to 100 at the selected start time.
3. **Return mode** — cumulative percentage return for OmniTrade versus buy-and-hold.
4. **Alpha mode** — OmniTrade return minus benchmark return.
5. **Drawdown mode** — portfolio and benchmark drawdowns from local peaks.

Indexed and alpha modes are essential because raw BTC price and a small portfolio balance cannot be meaningfully compared on one scale.

---

# 5. Machine Consumption Modes

AI agents should not be limited to screenshots. The Observatory should expose three synchronized representations.

## 5.1 Structured replay package — primary

A versioned machine-readable package containing:

- ordered candles
- derived indicators available at each timestamp
- market regime features
- strategy proposals and votes
- confidence and uncertainty
- campaign state
- position and portfolio state
- risk request and result
- action selected
- execution outcome
- future outcome labels, stored separately and unavailable during decision inference

This is the preferred representation for quantitative and language-model agents.

## 5.2 Feature tensor / tabular window — primary for ML

Fixed-length windows such as:

- previous 16, 32, 64, or 128 candles
- normalized OHLCV
- returns and volatility
- strategy vote vectors
- confidence trajectory
- portfolio exposure
- state transitions

This representation supports supervised, unsupervised, and self-supervised models.

## 5.3 Deterministic visual frame — supplementary

A server-rendered chart image produced from the exact replay package and rendering specification.

Uses:

- multimodal agent review
- visual anomaly detection
- human/AI shared inspection
- chart-pattern hypotheses
- UI regression evidence

The visual frame must include a manifest that identifies:

- rendering schema version
- replay package ID
- data cutoff timestamp
- visible overlays
- resolution and viewport
- chart library/version
- normalization mode
- whether outcome information is hidden or shown

Screenshots must never be the sole input to a capital decision.

---

# 6. Versioning Model

Versioning is mandatory. Without it, learning results cannot be reproduced.

## 6.1 Versioned artifacts

Every replay/training example must reference:

- `dataset_version`
- `feature_schema_version`
- `label_schema_version`
- `render_schema_version`
- `strategy_identity` and version
- `model_identity` and version
- `prompt_template_version`
- `aggregation_policy_version`
- `risk_policy_version`
- `campaign_policy_version`
- `execution_policy_version`
- `benchmark_definition_version`
- `code_commit_sha`

## 6.2 Immutable snapshot rule

A training example may not point only to “current” strategy or policy definitions. It must preserve exact identities used at decision time.

Corrections create a new version. They do not mutate historical examples.

## 6.3 Dataset manifests

Every dataset release should have an immutable manifest containing:

- included time range
- included instruments and venues
- inclusion/exclusion criteria
- feature versions
- label horizon definitions
- train/validation/test boundaries
- embargo periods
- known data-quality issues
- content hash
- creator and creation timestamp

---

# 7. Learning Modes

## 7.1 Supervised learning

Supervised systems learn from explicit labels derived after an outcome horizon closes.

Potential labels:

- net return after fees over 1, 2, 4, 8, and 16 candles
- maximum favorable excursion
- maximum adverse excursion
- whether a BUY/SELL/HOLD decision was directionally correct
- whether a decision beat buy-and-hold
- whether a trade achieved its policy objective
- decision-quality score
- execution-quality score
- avoidable-loss flag
- missed-opportunity value from the Counterfactual Outcome Ledger

Important: outcome labels are valid for training and evaluation only after the horizon closes. They must never appear in inference-time features.

## 7.2 Unsupervised learning

Unsupervised methods identify recurring structures without profit labels.

Possible discoveries:

- previously unknown volatility regimes
- distinct subtypes of “high confidence” setups
- recurring vote-conflict configurations
- clusters associated with late entries
- market states where a strategy becomes unreliable
- anomalous execution or portfolio behavior

Clustering does not prove profitability. Every discovered cluster must be evaluated out-of-sample before it influences a strategy.

## 7.3 Self-supervised learning

Self-supervised systems can learn representations from large quantities of unlabeled market history by tasks such as:

- predicting masked candles or features
- predicting the next regime transition
- distinguishing temporally adjacent from unrelated windows
- reconstructing corrupted feature windows
- learning embeddings for similar market/decision states

This may be more scalable than manually labeled supervised learning.

## 7.4 Reinforcement learning — later and tightly constrained

Reinforcement learning may eventually test sequential capital-allocation policies in simulation and replay.

It must not be allowed to learn directly with unrestricted real money.

Requirements before consideration:

- high-fidelity environment
- realistic fees, slippage, latency, liquidity, and rejected orders
- risk limits embedded into the environment and enforced externally
- offline evaluation
- paper/shadow proving
- champion/challenger governance
- bounded live authorization

---

# 8. AI Agent Roles in the Decision Arena

A useful arena should combine specialists rather than ask one model to do everything.

## 8.1 Quantitative Replay Analyst

Consumes structured time-series and calculates:

- return distributions
- conditional outcomes
- feature importance
- calibration
- signal lag
- regime-specific performance

## 8.2 Visual Pattern Analyst

Consumes deterministic chart frames and identifies hypotheses such as:

- entries visually lag reversals
- exits cluster after momentum decays
- confidence diverges from price action
- vote conflict precedes poor results

Its output is a hypothesis, not an executable order.

## 8.3 Decision Critic

Compares the preserved decision package with subsequent outcomes and asks:

- was the reasoning internally coherent?
- did the system obey available evidence?
- was the result luck or skill?
- what information was ignored?

## 8.4 Counterfactual Analyst

Evaluates alternative permitted actions at the same historical cutoff:

- BUY versus HOLD
- SELL now versus later
- smaller versus larger bounded allocation
- alternative stop or exit policy

## 8.5 Regime Discovery Agent

Uses embeddings or clustering to propose new regime definitions.

## 8.6 Strategy Evolution Agent

Translates validated findings into candidate strategy changes, feature additions, thresholds, or aggregation policies.

It cannot promote itself.

## 8.7 Governance Auditor

Verifies:

- no future leakage
- correct version references
- risk limits preserved
- evaluation windows valid
- benchmark comparison fair
- promotion evidence complete

---

# 9. The Learning Loop

```text
Market observations
        ↓
Versioned Decision Snapshot
        ↓
Strategy proposals and aggregate decision
        ↓
Risk and campaign governance
        ↓
Execution or explicit non-execution
        ↓
Outcome horizons close
        ↓
Labels and counterfactuals generated
        ↓
Structured replay + deterministic visual replay
        ↓
Supervised learning + pattern discovery + critique
        ↓
Candidate hypothesis or challenger produced
        ↓
Leakage-safe replay and walk-forward validation
        ↓
Decision Arena tournament
        ↓
Paper/shadow proving
        ↓
Governed promotion or rejection
        ↓
New immutable version
```

Every loop must preserve the incumbent and make rollback possible.

---

# 10. Evaluation: What “The Lines Converging” Actually Means

Visual convergence alone is not proof of intelligence.

The system should compute explicit measures:

- cumulative net return after all fees
- benchmark-relative return (alpha)
- Sharpe and Sortino ratios where statistically meaningful
- maximum drawdown
- downside capture
- hit rate
- payoff ratio
- profit factor
- turnover and fee burden
- calibration of confidence versus realized success
- decision latency
- entry/exit lag versus local extrema, labeled hindsight-only
- performance by regime
- performance by confidence bucket
- performance by strategy-vote configuration
- stability across time windows and instruments

The visual chart should be paired with these quantitative measures.

---

# 11. Non-Negotiable Anti-Leakage Controls

The greatest danger in an AI trading laboratory is accidentally letting the model see the future.

Required controls:

1. Every replay frame has an explicit cutoff timestamp.
2. Inference features contain only information available at or before that cutoff.
3. Future labels are stored in a separate namespace/table and joined only for training or retrospective evaluation.
4. Time-series splits, never random row splits, are the default.
5. Walk-forward validation is mandatory.
6. Purging and embargo windows are applied where labels overlap.
7. Charts shown to decision agents must hide future candles and outcome overlays.
8. Render manifests must prove which overlays were visible.
9. Hyperparameter selection may not use the final holdout period.
10. The final holdout remains sealed until a challenger is frozen.

---

# 12. Profit Claims and Statistical Reality

Applying supervised and unsupervised learning does not guarantee massive profits.

It can improve the probability of discovering useful patterns, but markets are:

- non-stationary
- competitive
- noisy
- fee-sensitive
- vulnerable to overfitting
- subject to regime change

OmniTrade should reject any candidate whose apparent advantage disappears after:

- fees and slippage
- out-of-sample testing
- walk-forward testing
- multiple-comparison correction
- regime segmentation
- paper/shadow operation

The correct goal is **demonstrable, risk-adjusted, reproducible improvement**, not visually impressive hindsight.

---

# 13. Proposed Data Model

Exact schema should follow repository conventions, but the architecture likely needs entities equivalent to:

## 13.1 `decision_replay_packages`

- id
- decision_record_id
- instrument_id
- venue_id
- cutoff_time
- window_start
- window_end
- dataset_version
- feature_schema_version
- payload/content location
- content hash
- created_at

## 13.2 `decision_replay_events`

Timeline-aligned events:

- replay_package_id
- event_time
- event_type
- source_identity
- payload
- sequence_number

## 13.3 `visual_replay_renders`

- id
- replay_package_id
- render_schema_version
- visible_overlays
- normalization_mode
- width
- height
- image/content location
- content hash
- created_at

## 13.4 `learning_labels`

- replay_package_id
- label_schema_version
- horizon
- label_name
- numeric_value / categorical_value
- available_at
- provenance

## 13.5 `dataset_manifests`

- id/version
- manifest JSON
- content hash
- immutable status
- created_at

## 13.6 `learning_experiments`

- experiment identity/version
- dataset manifest
- model/agent/prompt versions
- train/validation/test configuration
- metrics
- artifact references
- status

## 13.7 `arena_challengers`

- candidate identity/version
- parent/incumbent identity
- hypothesis
- evidence package
- replay metrics
- shadow metrics
- governance verdict
- promotion status

Existing Decision Records, Decision Packages, snapshots, counterfactual ledgers, strategy identities, and risk audit records should be reused rather than duplicated.

---

# 14. API Surface

Potential provider-neutral endpoints:

- `GET /observatory/instruments/{instrument_id}/timeline`
- `GET /observatory/decisions/{decision_id}/replay`
- `POST /observatory/replays/{replay_id}/render`
- `GET /observatory/replays/{replay_id}/render-manifest`
- `GET /observatory/replays/{replay_id}/events`
- `GET /observatory/datasets`
- `POST /observatory/datasets/build`
- `GET /observatory/experiments/{experiment_id}`
- `GET /observatory/challengers/{challenger_id}/evidence`

Machine-consumption endpoints must support deterministic pagination/order and explicit schema versions.

---

# 15. Frontend Pages and Components

Recommended initial locations:

## `/observatory`

Portfolio and market overview with indexed benchmark comparison.

## `/observatory/[instrument]`

Interactive timeline with overlays and event markers.

## `/decisions/[decisionId]/replay`

Candle-by-candle replay with the exact information available at each moment.

## `/arena/challengers/[challengerId]`

Incumbent versus challenger evidence, including quantitative metrics and representative replay frames.

Core components:

- `MarketDecisionChart`
- `OverlayControlPanel`
- `TimelineEventMarkers`
- `StrategyVoteHeatmap`
- `ConfidenceTrack`
- `PortfolioBenchmarkTrack`
- `DecisionInspectorDrawer`
- `ReplayTransportControls`
- `VersionManifestPanel`
- `LeakageBoundaryIndicator`

---

# 16. Phased Build Plan

## Phase VDI-0 — Audit and architecture confirmation

- map existing candles, Decision Records, packages, portfolio snapshots, strategy votes, and outcome ledgers
- identify what already exists
- produce ADR
- define canonical timeline schema
- no UI or model work

## Phase VDI-1 — Read-only Observatory MVP

- price/candle chart
- portfolio value overlay
- BUY/SELL/HOLD markers
- confidence overlay
- strategy-vote display
- indexed and alpha modes
- event inspector
- historical only, read-only

## Phase VDI-2 — Deterministic replay packages

- immutable replay package builder
- cutoff-time enforcement
- version manifests
- API endpoints
- replay UI
- exact event alignment

## Phase VDI-3 — Outcome labels and benchmark engine

- net outcome labels after fees
- multiple horizons
- counterfactual outcomes
- buy-and-hold benchmark
- alpha and drawdown metrics
- leakage-separated storage

## Phase VDI-4 — Dataset Registry and Experiment Ledger

- dataset manifests
- hashes
- experiment records
- model/prompt/version tracking
- reproducible train/validation/test splits

## Phase VDI-5 — Supervised Decision Quality models

- begin with interpretable models
- predict outcome quality or decision-quality dimensions
- calibration and feature importance
- advisory only

## Phase VDI-6 — Unsupervised regime and failure-pattern discovery

- embeddings/clustering
- cluster stability analysis
- retrospective naming/explanation
- no direct execution authority

## Phase VDI-7 — Multimodal Visual Analyst

- deterministic chart render service
- visual agent consumes frame plus manifest
- compare visual hypotheses against structured evidence
- visual agent remains advisory

## Phase VDI-8 — Champion/Challenger Arena

- challengers generated from validated findings
- walk-forward tournaments
- sealed holdout
- paper/shadow proving
- promotion governance

## Phase VDI-9 — Bounded live influence

Only after extensive evidence:

- advisory confidence adjustment or candidate selection
- explicit campaign authorization
- Risk Engine remains final authority
- automatic rollback thresholds
- no unconstrained self-modification

---

# 17. Safety and Governance Boundaries

The Observatory and learning system must never:

- bypass the Risk Engine
- bypass campaign authority
- submit orders from a visual analyst directly
- alter historical records
- train on future data accidentally
- promote a candidate solely because it earned more in one sample
- hide losing experiments
- optimize only for gross profit
- expose secrets in replay packages or images
- allow training jobs to mutate production strategy definitions
- permit an agent to approve its own promotion

Every learned recommendation must be traceable to:

- source dataset
- exact versions
- evaluation metrics
- risk impact
- governance decision

---

# 18. Success Criteria

The architecture succeeds when OmniTrade can demonstrate all of the following:

1. A human and an AI agent can inspect the same historical decision through synchronized views.
2. The exact state available at decision time can be replayed without future leakage.
3. Every render and dataset is reproducible from a versioned manifest.
4. Outcome labels include fees, slippage, and reconciliation truth.
5. Unsupervised discoveries can be translated into testable hypotheses.
6. Candidate improvements are evaluated against an incumbent using sealed, time-ordered evidence.
7. No learning component can bypass risk or campaign governance.
8. Promotion and rollback are explicit, auditable events.
9. Performance improvement is visible in both the chart and quantitative risk-adjusted metrics.
10. The system becomes more knowledgeable even when a proposed strategy is rejected.

---

# 19. Recommended Immediate Decision

Build the **read-only Observatory and deterministic replay foundation first**.

Do not begin by feeding arbitrary screenshots to autonomous trading agents. First create the versioned, leakage-safe, structured replay substrate. Once that substrate is trustworthy, add a visual analyst as one specialist in the Decision Arena and compare its hypotheses against structured quantitative agents.

This sequence turns the idea from an attractive chart into institutional learning infrastructure.
