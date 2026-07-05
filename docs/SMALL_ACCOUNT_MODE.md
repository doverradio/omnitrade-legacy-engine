# SMALL_ACCOUNT_MODE.md

## OmniTrade Legacy Engine — Small Account Mode

### 1. Design Principle

**"Small Account Mode is not a toy mode. It is the default proving ground."**

OmniTrade Legacy Engine must work correctly, safely, and honestly for a paper account starting at $25 — not as a stripped-down demo experience, but as the baseline condition every strategy, every risk rule, and every UI surface is designed against. If a strategy or feature only "works" when starting capital is large, it is not considered validated. Small accounts expose fee drag, minimum order sizes, rounding error, and unrealistic position sizing far faster and more honestly than large ones — which makes Small Account Mode the system's most useful test bed, not a secondary concern bolted on later.

This is also a family-legacy-relevant decision (`PROJECT_VISION.md` §6): the platform should be genuinely useful for a family member starting with $25 of real intent to learn, not just for someone who already has significant capital.

### 2. Starting Balance Input

- Paper account creation (`/paper-trading`, `POST /paper/account`) and backtest configuration (`/backtests`, `POST /backtests/run`) both expose a **"Starting Balance"** input accepting any positive dollar amount, with quick-select presets: **$25, $50, $100, $250, $500, $1,000** — plus a free-text field for any other custom amount (e.g., $2,500, $17.50).
- Minimum allowed starting balance: **$25** (system-enforced floor — see §9). There is no upper limit in MVP beyond standard numeric sanity bounds.
- The presets are defaults for convenience, not hard-coded options — the input is always a real number field, not a locked dropdown.

### 3. Dollar and Percentage Outcomes — Always Both

- Every place the system reports a strategy or backtest result (Strategy Lab, Backtests, Paper Trading, AI Review) shows **both** the dollar figure and the percentage figure, side by side — never just one.
  - Example: `+$4.12 (+16.5%)` rather than only `+16.5%` or only `+$4.12`.
- This matters specifically because percentage-only reporting can make a strategy that made $4.12 on a $25 account look identical in tone/confidence to one that made $4,120 on a $25,000 account — the dollar figure keeps outcomes honest and proportionate to what a small-account user actually experiences.
- Metrics objects (`backtests.metrics`, trade P&L fields) store the raw numeric values; formatting both views is a frontend display responsibility using `lib/utils/formatting.ts` (see `FRONTEND_PAGE_SPECS.md` updates below).

### 4. Risk Engine: Preventing Over-Sizing on Small Accounts

- All risk engine position-size and dollar-amount rules (`RISK_ENGINE.md` §2.1) operate as **percentages of current equity**, never fixed dollar minimums — a $25 account and a $25,000 account both respect the same percentage caps, so a $25 account is never pushed into a trade that's disproportionately large relative to its size.
- The risk engine additionally enforces a **minimum viable position check**: if the calculated position size (equity × max_position_size_pct) would fall below an asset's minimum order size (e.g., a broker's minimum fractional share increment, or an exchange's minimum notional order value), the risk engine rejects the trade with a clear reason (`position_below_minimum_order_size`) rather than silently rounding up to a disproportionately large size to meet the minimum.
- This is a **new explicit rule** added to the risk engine's evaluation order (see `RISK_ENGINE.md` update below), sitting alongside the existing max position size check.

### 5. Fractional Crypto Position Sizing

- Crypto position sizing (`internal_sim.py`) always supports fractional quantities (e.g., 0.00038 BTC) — this is already natural for crypto and requires no special-casing beyond ensuring quantity fields use sufficient decimal precision (`numeric` with adequate scale, not truncated to whole units) throughout `DATABASE_SCHEMA.md`.
- Fractional quantity precision follows each asset's actual exchange-defined minimum quantity increment (`LOT_SIZE`-style step size on Binance/Binance.US) — the system rounds down to the nearest valid increment rather than up, to avoid ever sizing a position larger than the risk engine approved.

### 6. Fractional Shares for Stocks

- Stock paper trading is restricted to **assets Alpaca supports for fractional share trading**. The asset registry (`assets` table) carries a `supports_fractional` flag; any stock without fractional support is either excluded from Small Account Mode strategy eligibility or triggers a clear warning (see §12) if a user attempts to trade it on a small account where a single whole share would represent an outsized fraction of equity.
- Example: a $25 account should not be silently blocked from trading, but if a user selects a stock priced at $180/share with no fractional support, the system must surface that one share alone is ~7x the max recommended position size — not attempt the trade and fail confusingly at the execution layer.

### 7. Distinguishing Balance Types

The system must never let a user or the UI conflate three distinct concepts:

| Concept | Where it lives | Never confused with |
|---|---|---|
| **Paper balance** | `paper_accounts.current_cash_balance` / `.starting_balance` | Real money. Always labeled "Paper" in every UI surface per `SECURITY_AND_SAFETY.md` §3. |
| **Real account balance** | Not tracked by this system in MVP — no live brokerage/exchange balance is ever read or displayed, since no live trading exists (`RISK_ENGINE.md` §5, `SECURITY_AND_SAFETY.md` §1). | Paper balance and backtest starting capital. |
| **Backtest starting capital** | `backtests.initial_capital` | Paper balance. A backtest's starting capital is a hypothetical input for a single historical simulation and does not affect, draw from, or get confused with any live paper account's balance. |

- UI labeling requirement: every balance figure on screen is prefixed or tagged with its type — "Paper Balance: $25.00", "Backtest Starting Capital: $100.00" — never a bare "$25.00" with no type label, anywhere results are shown.
- Since MVP never tracks a real account balance, any UI copy or documentation that could imply the system is aware of or connected to real brokerage funds is disallowed (consistent with `SECURITY_AND_SAFETY.md` §3).

### 8. Backtests With Custom Starting Capital

- `POST /backtests/run`'s `initial_capital` field (already present in `API_CONTRACTS.md`) accepts any value ≥ $25, with the same presets as paper account creation offered in the UI for convenience.
- Backtest metrics (`total_return`, `max_drawdown`, etc.) are computed the same way regardless of starting capital size — no special-cased math for "small" vs. "large" runs — but the results display (§3) always renders dollar figures at the actual configured scale, so a $25 backtest run's dollar P&L is never misleadingly extrapolated to a different capital base without the user explicitly requesting that comparison.

### 9. No Strategy May Assume Large Account Size

- Every strategy module (`STRATEGY_ENGINE.md` §2) must be reviewed for size-dependent assumptions before being marked eligible for Small Account Mode:
  - No hard-coded dollar thresholds in strategy logic (e.g., "only trade if position would exceed $500") — all sizing decisions flow through the risk engine's percentage-based rules, never the strategy module itself.
  - No strategy may assume it can always achieve its target position size — every strategy must handle the risk engine returning a `position_below_minimum_order_size` rejection (§4) gracefully (log as a `hold`-equivalent outcome, not an error).
- Each strategy's backtest results going forward should include at least one validation run at the $25 floor as part of its promotion checklist (extending `MVP_BUILD_PLAN.md` Phase 3 activation criteria) — a strategy that has only ever been validated at $10,000+ starting capital is not considered Small-Account-Mode-validated.

### 10. Fee & Slippage Modeling for Small Accounts

- Fees and slippage (`backtests.fee_bps`, `.slippage_bps`) are proportionally far more damaging to small accounts — a $0.50 flat-ish cost structure that's negligible on a $10,000 account can represent 2%+ of a $25 account per trade.
- The backtesting engine and internal crypto simulator must model fees realistically at small scale — using actual basis-point/percentage fee structures (never a flat minimum fee that silently dominates a tiny trade) — and results panels must surface a **"fees as % of position"** or **"fee drag"** metric specifically so small-account users can see this effect clearly, not just an aggregate return number that obscures it.
- The AI post-trade review engine (`AI_LAYER.md` §2.5) should flag strategies where cumulative fee drag exceeds a meaningful threshold (e.g., >20% of gross gains consumed by fees/slippage over the backtest period) as a specific, explicit finding — this is exactly the kind of pattern Small Account Mode is meant to surface.

### 11. Unrealistic Strategy Warnings

- The system must warn — not silently allow — when a strategy is likely to behave poorly at small scale. Trigger conditions for a warning banner (Strategy Lab, Backtests results, and Paper Trading account setup):
  - Backtested fee drag exceeds the threshold in §10 for the account's actual starting balance.
  - The strategy's minimum viable trade size (per §4/§6) would consume more than a configurable percentage of account equity (e.g., >50%) — a signal that the strategy's typical position sizing doesn't fit this account size.
  - A stock strategy targets an asset without fractional share support (§6) on an account where a single share would be disproportionate.
  - Backtest trade frequency is high enough that modeled fees materially erode returns relative to the account's size, even if they wouldn't at a larger scale.
- Warnings are informational, not blocking — the user can proceed, but the system must never present a strategy as validated/ready without surfacing a known small-account mismatch. Warning text must be specific (e.g., "This strategy's fees consumed 34% of gross backtest gains at a $25 starting balance — consider a larger account or a lower-frequency strategy") rather than generic.

### 12. Summary of Required Downstream Doc Updates

This document is the source of truth for Small Account Mode; the following documents are updated to reflect it (see corresponding diffs/redlines applied alongside this doc):
- `UI_SPEC.md` — Starting Balance input, dollar+percentage display convention, balance-type labeling.
- `FRONTEND_PAGE_SPECS.md` — page-level detail for the above on Paper Trading, Strategy Lab, and Backtests pages, plus the warning banner component.
- `DATABASE_SCHEMA.md` — `supports_fractional` on `assets`, precision/scale confirmation on numeric quantity/balance columns, `initial_capital` minimum.
- `RISK_ENGINE.md` — the new `position_below_minimum_order_size` rule in the evaluation order.
- `API_CONTRACTS.md` — starting balance minimum/validation on account creation and backtest run endpoints.
- `COPILOT_PHASE_0_PROMPTS.md` / `COPILOT_PHASE_1_PROMPTS.md` — scaffolding and seed-data adjustments so Small Account Mode is exercised from the earliest phases, not retrofitted later.
