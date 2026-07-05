# STRATEGY_ENGINE.md

## OmniTrade Legacy Engine — Strategy Engine

### 1. Design Contract

Every strategy module implements a common interface so the backtester, paper trader, and live engine (future) can all call strategies identically:

```python
class Strategy(Protocol):
    slug: str
    default_params: dict

    def generate_signal(
        self,
        candles: pd.DataFrame,   # OHLCV, ascending by time, indexed by open_time
        params: dict,
        context: StrategyContext,  # account state, open positions, etc. (read-only)
    ) -> Signal:
        ...
```

```python
@dataclass
class Signal:
    action: Literal["buy", "sell", "hold"]
    strength: float           # 0.0 - 1.0, strategy's own confidence
    reason: str                # short human-readable rationale
    indicators: dict           # key indicator values used, for logging/explainability
```

Rules:
- Strategies are **pure functions of their inputs** — no hidden state, no direct DB or network access. This makes them trivially testable and reusable across backtest/paper/live.
- Every `Signal` must include a non-empty `reason` and the `indicators` that produced it — this feeds both the audit log and the AI explanation layer.
- Strategies never place orders directly; they only emit signals, which flow through the AI layer and risk engine (see `SYSTEM_ARCHITECTURE.md` §3).

### 2. MVP Strategy Modules

#### 2.1 Moving Average Crossover (`ma_crossover`)
- **Logic:** Buy when fast MA crosses above slow MA; sell when fast MA crosses below slow MA.
- **Default params:** `{fast_period: 10, slow_period: 50, ma_type: "sma"}`
- **Indicators logged:** fast_ma, slow_ma, prior_fast_ma, prior_slow_ma.
- **Notes:** Classic trend-following baseline; intentionally simple so it's easy to validate the whole pipeline end-to-end before adding complexity.

#### 2.2 RSI Mean Reversion (`rsi_mean_reversion`)
- **Logic:** Buy when RSI crosses below `oversold` threshold and turns back up; sell/exit when RSI crosses above `overbought` threshold and turns back down.
- **Default params:** `{rsi_period: 14, oversold: 30, overbought: 70}`
- **Indicators logged:** rsi_value, rsi_slope.
- **Notes:** Works best in range-bound regimes; the AI regime classifier (see `AI_LAYER.md`) is expected to downweight this strategy during strong trends.

#### 2.3 Breakout Strategy (`breakout`)
- **Logic:** Buy when close breaks above the highest high of the last `lookback` candles (with optional volume confirmation); sell/exit on break below the lowest low, or on a trailing-stop basis.
- **Default params:** `{lookback: 20, volume_confirmation: true, min_volume_multiple: 1.5}`
- **Indicators logged:** rolling_high, rolling_low, volume_ratio.
- **Notes:** Prone to false breakouts in choppy markets; paired with the volatility filter (below) to reduce noise trades.

#### 2.4 Volatility Filter (`volatility_filter`)
- **Type:** Not a standalone signal generator — a **filter module** other strategies/ensemble can call.
- **Logic:** Computes realized volatility (e.g., ATR or rolling stddev of returns) and flags whether current volatility is within an acceptable band. Strategies/ensemble can suppress trades when volatility is outside the configured band (too quiet = low edge; too wild = unreliable fills).
- **Default params:** `{atr_period: 14, min_atr_pct: 0.2, max_atr_pct: 5.0}`

#### 2.5 Trend Regime Filter (`trend_regime_filter`)
- **Type:** Filter module (not a standalone signal generator).
- **Logic:** Classifies the market as `trending_up`, `trending_down`, or `ranging` using something simple and explainable (e.g., ADX threshold + long-period MA slope). Used to gate which strategies are allowed to act (e.g., suppress `rsi_mean_reversion` during a strong trend).
- **Default params:** `{adx_period: 14, adx_trend_threshold: 25, ma_slope_period: 50}`
- **Note:** This is a simpler, rules-based cousin of the AI layer's regime classifier (`AI_LAYER.md`) — it exists so the system has a deterministic, explainable regime signal even before/without the AI layer, and the AI classifier's output can be validated against it.

#### 2.6 Ensemble Signal Scorer (`ensemble_scorer`)
- **Logic:** Combines outputs from multiple active strategies (weighted by strategy allocation, see `AI_LAYER.md` allocator) into a single blended signal per asset. Simple MVP approach: weighted average of `strength` per action, with the filter modules (volatility, trend regime) able to zero out a strategy's contribution when conditions are unfavorable for it.
- **Default params:** `{min_strategies_agreeing: 1, conflict_resolution: "net_strength"}`
- **Conflict resolution modes:**
  - `net_strength`: sum signed strengths (buy positive, sell negative); net sign determines action, magnitude determines strength.
  - `majority_vote`: action with the most strategies agreeing wins; ties resolve to `hold`.
- **Output:** A single `Signal` per asset per evaluation cycle, which is what actually proceeds to the AI layer and risk engine — individual strategy signals are still logged for transparency, but only the ensemble output is actionable.

### 3. Strategy Lifecycle

1. **Defined** — code + default params committed to repo, registered in `strategies` table.
2. **Backtested** — one or more `backtests` rows produced across different `parameter_sets`; must meet minimum criteria (see `MVP_BUILD_PLAN.md` Phase 3) before activation.
3. **Paper-active** — `strategies.is_active = true`; strategy participates in scheduled signal generation against paper accounts.
4. **Under review** — AI post-trade review engine (`AI_LAYER.md`) periodically evaluates live paper performance vs. backtest expectations; large divergence flags the strategy for human review.
5. **Retired** — set `is_active = false`; historical data remains for audit/research.

### 4. Parameter Adjustment from the Web UI

- The Strategy Lab UI page (`UI_SPEC.md`) allows creating new `parameter_sets` for a strategy and immediately queuing a backtest against them.
- Parameter changes to an **already-active** strategy do not apply instantly to live paper trading — they create a new `parameter_set` and require an explicit "promote to active" action, which is itself an audited event. This prevents accidental live parameter drift.

### 5. Adding a New Strategy (Developer Guide)

1. Create `backend/strategies/<slug>.py` implementing the `Strategy` protocol.
2. Register it in the strategy registry (`backend/strategies/__init__.py`).
3. Insert a row into `strategies` (via migration or admin action) with `module_version` matching the code.
4. Write unit tests covering: flat market, trending market, and a known historical window with expected signal count/timing.
5. Run at least one backtest before the strategy can be toggled active in any environment above `local`.
