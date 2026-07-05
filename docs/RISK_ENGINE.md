# RISK_ENGINE.md

## OmniTrade Legacy Engine — Risk Engine

### 1. Purpose & Position in the Pipeline

The risk engine is the **mandatory final gate** for every signal before it can become a trade — rules-based strategy signals and AI-adjusted signals alike (see `SYSTEM_ARCHITECTURE.md` §3, `AI_LAYER.md` §3). It can approve, resize, delay, or reject any signal, and can trip account-level or global kill switches. No other component may bypass it.

Every risk decision is written to `risk_events` (see `DATABASE_SCHEMA.md` §2.11) and `audit_log`.

### 2. Controls

#### 2.1 Max Position Size
- Configurable per account and per asset, expressed as a percentage of account equity (e.g., max 5% of equity in any single position) and optionally as an absolute cap.
- Enforced at signal-approval time using current equity, not stale/cached equity.
- Always computed as a percentage of current equity, never a fixed dollar floor — this is what allows the same rule set to apply safely at a $25 account and a $25,000 account alike, per `SMALL_ACCOUNT_MODE.md` §4.

#### 2.1a Minimum Viable Position Size
- Before approving any position, the risk engine checks the calculated position size (equity × max_position_size_pct, further reduced by AI confidence scaling if applicable) against the asset's `min_order_notional` and `qty_step_size` (see `DATABASE_SCHEMA.md` §2.1).
- If the calculated size would fall below the asset's minimum order size, the signal is **rejected** with reason `position_below_minimum_order_size` — the risk engine never rounds a position up to meet a minimum, since that would silently exceed the account's approved risk sizing. This is the primary mechanism preventing over-sizing on small accounts per `SMALL_ACCOUNT_MODE.md` §4.
- This check applies identically regardless of account size; it simply triggers far more often on small accounts, which is expected and by design — a small account being unable to meet an asset's minimum order size is a real, informative constraint, not a bug to be worked around.

#### 2.2 Max Daily Loss
- A configurable percentage (e.g., 3% of starting-of-day equity). Once realized + unrealized losses for the day breach this, the account enters a **trading-paused** state for the remainder of the day — no new positions, existing positions may still be closed per stop-loss rules.

#### 2.3 Max Drawdown
- A configurable percentage from the account's high-water mark (e.g., 15%). Breaching this trips a longer-duration pause (configurable, e.g., until human review and manual re-arm) rather than just a daily reset — drawdown limits are treated as more serious than daily loss limits.

#### 2.4 Stop Loss
- Every position opened by the system must have an associated stop-loss level (percentage- or ATR-based, per strategy config). The risk engine rejects any signal that would open a position without a computable stop-loss.

#### 2.5 Take Profit
- Optional per-strategy take-profit target; when configured, enforced the same way as stop-loss. Strategies may also use trailing stops in place of a fixed take-profit — configurable per strategy.

#### 2.6 Cooldown Rules
- After a stop-loss is hit, or after N consecutive losing trades on a strategy/asset pair, that strategy/asset pair enters a cooldown period (configurable duration) during which new signals for it are auto-rejected. Prevents "revenge trading" patterns even in an automated system.

#### 2.7 No-Trade Zones
- Configurable time-based restrictions (e.g., no new positions in the first/last N minutes of the stock market session, around known high-impact economic releases if the user configures a calendar, or during detected abnormal data conditions like stale/missing candles).
- Also covers **data-quality no-trade zones**: if ingestion health checks detect gaps or stale data for an asset, that asset is automatically placed in a no-trade zone until data quality is restored.

#### 2.8 Kill Switch
- **Account-level kill switch:** immediately halts all new signal approval for one paper account; existing open positions are flagged for human review (not auto-liquidated by default, to avoid the kill switch itself causing forced-sale losses — configurable).
- **Global kill switch:** halts all trading activity across every account and strategy. Triggered automatically by: repeated risk engine internal errors, data feed failures beyond a threshold, or detection of an execution/reconciliation mismatch (e.g., recorded position doesn't match expected state). Can also be triggered manually from the Risk Monitor UI page.
- **Re-arming:** Both kill switch levels require an explicit human action to re-arm (an authenticated user action, logged to `audit_log`) — the system never silently resumes trading after a kill switch trip.

### 3. Evaluation Order

For each incoming signal, the risk engine evaluates in this order, short-circuiting on the first rejection:

1. Is the global kill switch engaged? → reject if yes.
2. Is the account-level kill switch / trading-paused state engaged? → reject if yes.
3. Is the asset in a no-trade zone (time-based or data-quality)? → reject if yes.
4. Is the strategy/asset pair in cooldown? → reject if yes.
5. Would this breach max daily loss (if opening/increasing exposure)? → reject if yes.
6. Would this breach max drawdown? → reject if yes.
7. Does the signal have a computable stop-loss? → reject if no.
8. Would this breach max position size? → **resize down** to the maximum allowed size rather than outright rejecting, if a valid smaller size still makes sense per strategy minimums; otherwise reject.
9. Is the (possibly resized) position size at or above the asset's minimum viable order size (`min_order_notional`/`qty_step_size`)? → reject with `position_below_minimum_order_size` if not — see §2.1a and `SMALL_ACCOUNT_MODE.md` §4. This check runs after resizing, since a resize can push a position below the minimum even when the pre-resize size was above it.
10. Apply AI confidence scaling (from `AI_LAYER.md` §2.2) — this can further reduce size, never increase it beyond the strategy/account's own configured maximum. Re-check step 9 after this scaling, since AI confidence scaling can also push a position below the minimum.
11. Approve → forward to execution engine.

### 4. Configuration & Defaults

- Risk parameters are stored per paper account (with system-wide defaults as a fallback) and are themselves versioned/audited like strategy parameters — a risk parameter loosening is a meaningfully "risky" audit event and is flagged distinctly (e.g., a UI banner) from a routine strategy parameter tweak.
- Suggested conservative MVP defaults:
  - Max position size: 5% of equity per asset
  - Max daily loss: 3%
  - Max drawdown: 15%
  - Default stop-loss: 2x ATR or 5% (configurable per strategy)
  - Cooldown after 3 consecutive losses: 24 hours
  - Global kill switch trigger: 3 consecutive data/reconciliation errors within 15 minutes
- These same percentage-based defaults apply at every account size, including the $25 floor — per `SMALL_ACCOUNT_MODE.md` §4, no separate "small account" rule set exists; the minimum-viable-position check (§2.1a) is what naturally makes the system behave differently (fewer/smaller approved trades) at small scale, without needing distinct configuration.

### 5. Relationship to Live Trading (Future)

- The risk engine's control set is designed to remain the same shape when live trading is eventually enabled — the MVP intentionally exercises the exact controls that would gate real capital, so that by the time live trading is considered, the controls are already battle-tested against months of paper trading data rather than newly built.
- Enabling live trading is explicitly out of MVP scope and requires, at minimum: a distinct "live" account type, explicit human opt-in per account, and a hard-coded, separately-configured (lower) starting position-size cap. This is a Horizon 2 decision, not an MVP deliverable (see `PROJECT_VISION.md` §5, `MVP_BUILD_PLAN.md`).
