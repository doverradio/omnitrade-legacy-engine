# AI_LAYER.md

## OmniTrade Legacy Engine — Proprietary AI Layer

### 1. Role of the AI Layer

The AI layer is an **advisory and explanatory** system, not an execution authority. It sits between the strategy engine's raw signals and the risk engine's final gate. It never places trades directly and never bypasses the risk engine.

Its four jobs:
1. Classify the current market regime.
2. Score the confidence of strategy-generated signals.
3. Recommend strategy allocation weights (for the ensemble scorer).
4. Generate a human-readable explanation for every signal and every trade, plus periodic post-trade reviews.

### 2. Components

#### 2.1 Regime Classifier
- **Input:** Recent OHLCV history, realized volatility, trend-strength indicators (e.g., ADX), and the rules-based `trend_regime_filter` output as a baseline signal.
- **Output:** A regime label (`trending_up`, `trending_down`, `ranging`, `high_volatility`, `low_liquidity`) with a confidence score, written to `signals.regime_tag` and `model_outputs`.
- **MVP implementation approach:** Start with an interpretable model (e.g., gradient-boosted trees or logistic regression over engineered features) rather than a deep/black-box model — interpretability is a hard requirement (see §4). A more complex model can be considered later, but only alongside a feature-attribution method (e.g., SHAP) so explanations remain grounded.
- **Validation:** Regime labels are back-tested against known historical regime periods and cross-checked against the deterministic `trend_regime_filter` to catch classifier drift.

#### 2.2 Signal Confidence Scorer
- **Input:** The raw `Signal` from a strategy (or ensemble), current regime classification, recent strategy performance (win rate, recent drawdown), and volatility context.
- **Output:** `ai_confidence` (0.0–1.0) written to `signals.ai_confidence`, plus the specific factors that drove the score (e.g., "regime favorable: +0.15, strategy recent win rate 62%: +0.05, high volatility: -0.10").
- **Purpose:** Downstream, the risk engine and position sizing can use `ai_confidence` to scale position size within pre-approved bounds — it can only ever scale **down** from a strategy's base sizing, never grant permission to exceed hard risk limits (see `RISK_ENGINE.md`).
- **Future calibration input:** Once the Decision Intelligence Engine's Counterfactual Outcome Ledger is implemented (`DECISION_INTELLIGENCE_ENGINE.md` §8), its per-horizon comparison between stated confidence and hindsight-best action (surfaced as `confidence_overestimated`/`confidence_underestimated` lesson tags) becomes a direct calibration signal for this scorer — reviewed and applied through the same versioned retraining process as any other model change (§6), never automatically.

#### 2.3 Strategy Allocator
- **Input:** Recent per-strategy performance metrics, current regime, and correlation between active strategies' recent signals.
- **Output:** Recommended weight per active strategy (summing to 1.0) for the ensemble scorer to use. Written to `model_outputs` with `model_name = 'allocator'`.
- **Constraints:** Allocator recommendations are bounded (e.g., no strategy can be weighted to zero or to more than a configurable cap like 60%, preventing the AI from silently "picking one horse" and removing diversification) and take effect only on the next scheduled rebalance, not instantly — every rebalance is logged and reviewable before it materially affects trading.

#### 2.4 Trade Explanation Generator
- **Input:** The final signal, its regime tag, confidence score, allocator weight, and the risk engine's decision.
- **Output:** A plain-language explanation stored alongside the trade (e.g., *"Bought 0.02 BTC: MA crossover signal (fast MA crossed above slow MA), regime classified as trending_up (78% confidence), AI confidence 0.71 based on favorable regime and strategy's 58% win rate over the last 30 trades, sized at 1.5% of paper account equity per risk engine limits."*).
- **Requirement:** Every trade and every non-`hold` signal has an explanation before it is considered "complete" in the audit trail. If explanation generation fails, the signal is logged as `status = 'risk_rejected'` with reason `explanation_unavailable` — the system fails closed rather than trading without a rationale.

#### 2.5 Post-Trade Review Engine
- **Trigger:** Runs on a schedule (e.g., nightly) and after each closed trade.
- **Input:** Trade outcome vs. the original signal's stated rationale and confidence.
- **Output:** A structured review noting whether the trade's outcome was consistent with its stated rationale, and flags patterns (e.g., "this strategy underperforms its backtest in `high_volatility` regime by X%") for human attention on the AI Review dashboard page.
- **Important boundary:** The review engine can **recommend** parameter or allocation changes for human approval; it cannot automatically apply them. This keeps a human in the loop for anything that changes future trading behavior (see §4).
- **Distinction from the Counterfactual Outcome Ledger:** This engine reviews the trade that was actually taken. The Decision Intelligence Engine's COL (`DECISION_INTELLIGENCE_ENGINE.md` §8) is a separate, complementary mechanism that additionally tracks what would have happened under the actions *not* taken — including for signals this engine never sees because no trade resulted. The two are expected to eventually share findings (e.g., a rejected-trade lesson tag from the COL informing this engine's pattern-flagging), but are architecturally distinct: this engine reviews realized trades, the COL evaluates all decisions, taken or not.

### 3. Data Flow

```
Strategy Engine → raw Signal
        │
        ▼
Regime Classifier → regime_tag + confidence
        │
        ▼
Signal Confidence Scorer → ai_confidence
        │
        ▼
Ensemble Scorer (uses Allocator weights) → blended Signal
        │
        ▼
Trade Explanation Generator → explanation text
        │
        ▼
Risk Engine (final gate) → approved/rejected/resized
        │
        ▼
Execution (paper) → Trade
        │
        ▼
Post-Trade Review Engine (async, after outcome known)
```

### 4. What the AI Should Do

- Classify, score, weight, and explain — always producing a traceable rationale grounded in specific, logged inputs.
- Scale position sizing **downward** within risk-engine-approved bounds when confidence is low.
- Flag anomalies and underperformance patterns for human review.
- Improve its own scoring/weighting over time only through a reviewed, versioned retraining process (new `model_version`, backtested before deployment) — not silent online learning.

### 5. What the AI Should NOT Do

- **Never place or size a trade above the strategy/risk engine's hard limits**, regardless of confidence.
- **Never trade without a logged explanation.**
- **Never auto-apply its own recommendations** (allocation changes, parameter changes) — these require explicit human promotion, exactly like strategy parameter changes (`STRATEGY_ENGINE.md` §4).
- **Never override or bypass the risk engine, kill switch, or cooldown rules.**
- **Never fabricate confidence or regime labels when input data is insufficient or stale** — in that case it must output a low/undefined confidence and let the risk engine's default-deny behavior take over.
- **Never be the sole reviewer of its own performance** — its post-trade reviews are inputs to the human-facing AI Review page, not self-executing verdicts.

### 6. Versioning & Reproducibility

- Every model has a `model_version`; all `model_outputs` rows are tagged with the exact version that produced them, so historical decisions can always be traced back to the exact model logic that made them, even after later retraining.
- Model changes go through the same rigor as strategy changes: backtest/validate → stage → human review → promote.
