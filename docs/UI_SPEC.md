# UI_SPEC.md

## OmniTrade Legacy Engine — Dashboard UI Specification

### 1. Global Layout

- Persistent left sidebar navigation with the 9 pages below.
- Top bar: active environment indicator (local/staging/production — always visibly labeled, and production always visibly labeled "PAPER TRADING" until live trading is ever enabled), current paper account selector, global kill-switch status indicator (green/amber/red).
- All monetary figures clearly labeled as simulated/paper where applicable, and always labeled with their balance type (paper balance / backtest starting capital) per `SMALL_ACCOUNT_MODE.md` §7 — never a bare dollar figure with no type context.
- **Small Account Mode is not a toy mode. It is the default proving ground.** Every page below is designed to work correctly and honestly at a $25 starting balance, not just at large account sizes — see `SMALL_ACCOUNT_MODE.md` for the full requirement set.

### 2. Pages

#### 2.1 Overview
- Summary cards: total paper equity, today's P&L, open positions count, active strategies count, current risk status.
- Equity curve chart (Recharts line chart) for the selected account, with drawdown shading.
- Recent activity feed: last N signals/trades/risk events, each with a one-line AI explanation snippet and a link to full detail.

#### 2.2 Markets
- Asset list (crypto + stocks) with current price, 24h change, and a data-source badge (e.g., "Binance.US", "Alpaca (IEX)").
- Selecting an asset opens a TradingView Lightweight Charts candlestick view with:
  - Overlay of relevant indicators (MA lines, RSI subplot, ATR subplot) toggleable.
  - Trade markers (buy/sell arrows) plotted at actual paper execution points, pulled from `trades`.
  - Interval selector (1m/5m/15m/1h/1d).

#### 2.3 Strategy Lab
- List of strategies with active/inactive toggle (toggle triggers the audited "promote to active" flow, not an instant change — see `STRATEGY_ENGINE.md` §4).
- Parameter editor per strategy: form generated from each strategy's param schema (numeric fields, dropdowns for enums like `ma_type`).
- "Save as new parameter set" + "Run backtest" action, which navigates to a pre-filled Backtests page run.
- Parameter set history/comparison view (table of past parameter sets with linked backtest results).

#### 2.4 Backtests
- Form: strategy, parameter set, asset, interval, date range, **Starting Balance** (simple dollar input with quick-select presets $25/$50/$100/$250/$500/$1,000 plus custom entry, minimum $25 — see `SMALL_ACCOUNT_MODE.md` §2/§8), fee/slippage assumptions → "Run Backtest".
- Results view per backtest: equity curve, drawdown chart, trade list (from `backtest_trades`), and a metrics panel: total return (shown as **both dollar amount and percentage**, e.g. "+$4.12 (+16.5%)" per `SMALL_ACCOUNT_MODE.md` §3), win rate, max drawdown, Sharpe-like ratio, average trade, trade count, and a **fee drag** metric (fees/slippage as % of gross gains).
- If the backtest indicates the strategy is a poor fit for its configured starting balance (per `SMALL_ACCOUNT_MODE.md` §11 trigger conditions), a specific, non-generic warning banner appears in the results view.
- Comparison mode: select 2+ backtests to overlay equity curves and compare metrics side-by-side.

#### 2.5 Paper Trading
- Per-account view: current holdings table (asset, quantity — supporting fractional amounts, avg entry, unrealized P&L shown as dollar + percentage), open orders (if any pending), and account-level equity/drawdown chart.
- "New Paper Account" creation flow (name, asset class, **Starting Balance** — same simple input/presets as Backtests, minimum $25, per `SMALL_ACCOUNT_MODE.md` §2).
- Trade history table with filters (strategy, asset, date range), each row expandable to show the full AI explanation and risk-engine decision trail.
- Warning banner surfaces if an active strategy is flagged as a poor fit for the account's current balance (`SMALL_ACCOUNT_MODE.md` §11).

#### 2.6 Signals
- Live/recent feed of all generated signals (not just executed trades) — including `hold` signals and `risk_rejected` ones, for full transparency.
- Filters: strategy, asset, action, status (generated/risk_approved/risk_rejected/executed/expired).
- Each row expandable to show: raw strategy signal, regime tag, AI confidence, allocator weight applied, risk engine decision, and final explanation text.

#### 2.7 Risk Monitor
- Real-time status per paper account: daily loss used (of limit), drawdown used (of limit), cooldown states active, no-trade zones currently in effect.
- Global kill switch control (with confirmation modal) and account-level pause/resume controls.
- Risk event log (from `risk_events`), filterable by event type and account.
- Risk parameter editor (per account and system defaults), with loosening changes visually flagged (see `RISK_ENGINE.md` §4).

#### 2.8 AI Review
- Regime classification history chart (regime label over time, overlaid on price).
- Strategy allocator weight history (stacked area chart of weights over time).
- Post-trade review feed: flagged divergences between backtest expectations and live paper performance, with the AI's recommended (not auto-applied) adjustments and an explicit human "approve/dismiss" action for each recommendation.

#### 2.9 Settings
- Account/profile management (Supabase Auth-backed).
- API/data source connection status (Binance/Binance.US, Alpaca) with masked key display and "test connection" actions — actual secrets are never rendered in the UI, only connection status (see `DATA_SOURCES.md`, secrets handling in `MVP_BUILD_PLAN.md`).
- Notification preferences (e.g., email/webhook on kill-switch trips, daily loss limit breaches).
- Audit log viewer (searchable/filterable table over `audit_log`) — read-only.

#### 2.10 Decision Intelligence Pages (Future Phase — Not Part of MVP)
The Decision Intelligence Engine (`DECISION_INTELLIGENCE_ENGINE.md`) anticipates a future page set — Decision Explorer, Decision Timeline, Decision Detail, Decision Compare, Decision Search, AI Reflection Viewer, Confidence Analytics, a **Counterfactual Viewer** (a Decision Detail sub-view showing shadow BUY/SELL/WAIT outcomes, hindsight-best action, and lesson tags per decision — see `DECISION_INTELLIGENCE_ENGINE.md` §8, §10), and a **Decision Quality Dashboard** (surfacing Overall Decision Quality, per-action correctness rates, False Positives, Missed Opportunities, Confidence Calibration, Market Regime Accuracy, Risk Override Success Rate, and Counterfactual Agreement Rate — see `DECISION_INTELLIGENCE_ENGINE.md` §8a) — not scheduled into the current 9-page MVP navigation. They're noted here so the Signals (§2.6) and AI Review (§2.8) pages above are built with an eye toward this later expansion (e.g., not assuming a signal's explanation data only ever needs a single inline drawer), without requiring any MVP work to build them now.

### 3. Design Constraints

- Every page that shows a trade, signal, or AI decision must have a path to that decision's full explanation — no orphaned numbers without a "why" available.
- Any destructive or state-changing action (kill switch, parameter promotion, risk loosening) requires a confirmation step and is immediately reflected in the audit log viewer.
- Charts must clearly indicate data source and any known limitations (e.g., "IEX feed, not full consolidated tape") per `DATA_SOURCES.md` §5.
- Every result surface showing a return, P&L, or performance figure shows both dollar and percentage values together, and every balance figure is labeled with its type (paper balance / backtest starting capital) — per `SMALL_ACCOUNT_MODE.md` §3 and §7.
- Explanation and evidence data surfaced on the Signals and AI Review pages (supporting/opposing evidence, confidence factors, risk adjustments) should be structured consistently with the Decision Record schema in `DECISION_INTELLIGENCE_ENGINE.md` §4, even though the DIE itself is a future-phase subsystem — this avoids rework when Decision Intelligence pages are eventually built on top of the same underlying data.
