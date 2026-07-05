# FRONTEND_PAGE_SPECS.md

## OmniTrade Legacy Engine — MVP Frontend Page Specs

> Conventions used below: "Loading state" = what renders while API calls are in flight; "Empty state" = what renders when the API succeeds but returns no data; "Error state" = what renders when the API call fails.

> **Small Account Mode is not a toy mode. It is the default proving ground.** Every page spec below assumes the platform is being used with a starting balance as low as $25 by default — components, empty states, and metric displays must all read correctly at that scale, not just at large account sizes. See `SMALL_ACCOUNT_MODE.md` for the full requirement set.

---

### `/dashboard`

**Layout:** `PageShell` with a 2-column grid on desktop (summary cards row spanning full width, then equity chart + activity feed side-by-side), single column on mobile.

**Components:**
- `SummaryCardRow`: total equity, today's P&L, open positions count, active strategies count, risk status badge.
- `EquityCurveChart`: line chart of selected paper account equity over time.
- `RecentActivityFeed`: last 10 signals/trades/risk events.

**Required API calls:** `GET /paper/account`, `GET /signals?limit=10`, (risk events feed reuses `GET /signals` filtered plus a future `GET /risk/events` — stub with signals/trades for MVP if risk endpoint isn't built yet).

**Loading state:** Skeleton cards (4 placeholder rectangles) + skeleton chart area + skeleton list rows.

**Empty state:** If no paper account exists yet: a centered `EmptyState` component with "No paper account yet" and a CTA button linking to `/paper-trading` to create one. If account exists but no activity: chart renders flat line at starting balance; activity feed shows "No signals or trades yet — activity will appear here once strategies are active."

**Error state:** `ErrorBanner` at top of page ("Couldn't load dashboard data — retry") with a retry button; cards that failed independently show an inline error chip rather than blocking the whole page (partial failure tolerance).

---

### `/markets`

**Layout:** Left panel (asset list, ~280px fixed width) + main panel (chart + controls), stacking vertically on mobile.

**Components:**
- `AssetList`: searchable/filterable list of assets with price + data-source badge.
- `IntervalSelector`: button group (1m/5m/15m/1h/1d).
- `CandleChart` (TradingView Lightweight Charts wrapper): candles + optional MA overlay + trade markers.
- `IndicatorToggles`: checkboxes for MA/RSI/ATR overlays.

**Required API calls:** `GET /markets/assets`, `GET /markets/candles` (re-fetched on asset or interval change).

**Loading state:** `AssetList` shows skeleton rows; chart area shows a centered spinner with "Loading candles..." on asset/interval change (not a full-page reload — keep the chart shell visible).

**Empty state:** If `GET /markets/assets` returns zero items: "No assets configured yet — run the seed script or check ingestion status" (dev-facing message, acceptable for MVP internal tool). If candles are empty for a valid asset/interval (e.g., not yet backfilled): "No candle data available for this range yet" inside the chart area, with the asset list still functional.

**Error state:** If asset list fails to load, page-level `ErrorBanner`. If only candles fail (asset list succeeded), chart area shows its own inline error with retry, asset list remains usable.

---

### `/strategy-lab`

**Layout:** Left panel: strategy list. Right panel: selected strategy's parameter form + parameter set history + backtest CTA.

**Components:**
- `StrategyList`: name, slug, active/inactive badge, toggle control (opens confirmation modal per `SECURITY_AND_SAFETY.md`/`UI_SPEC.md` audited-change requirement).
- `ParameterForm`: dynamically generated from `default_params` shape (number inputs, dropdowns for enum params like `ma_type`).
- `ParameterSetHistoryTable`: past parameter sets with linked backtest results (status + key metric, shown as `DollarAndPercent` where applicable per `SMALL_ACCOUNT_MODE.md` §3).
- `RunBacktestButton`: saves current form as a new parameter set, then navigates to `/backtests` pre-filled.
- `SmallAccountWarningBanner`: surfaces if the strategy's linked backtest history shows a small-account fit warning per `SMALL_ACCOUNT_MODE.md` §11, so the mismatch is visible before a user promotes the strategy to active.

**Required API calls:** `GET /strategies`, `PATCH /strategies/:id` (activation), (parameter set creation endpoint — implied by `API_CONTRACTS.md`, add `POST /strategies/:id/parameter-sets` when implemented), `POST /backtests/run`.

**Loading state:** Strategy list skeleton rows; parameter form area shows skeleton fields while the selected strategy's schema loads.

**Empty state:** If no strategies exist: "No strategies registered — run the seed script." If a strategy has no parameter set history yet: "No saved parameter sets yet — adjust values above and save your first one."

**Error state:** Activation toggle failure shows an inline toast ("Couldn't update strategy — try again") and reverts the toggle visually. Parameter save/backtest-run failure shows form-level error text without losing the user's entered values.

---

### `/backtests`

**Layout:** Top: run-configuration form (collapsible once a result exists). Below: results panel (single result) or comparison panel (2+ selected).

**Components:**
- `BacktestConfigForm`: strategy, parameter set, asset, interval, date range, **`StartingBalanceInput`** (preset buttons $25/$50/$100/$250/$500/$1,000 + custom numeric field, minimum $25 — per `SMALL_ACCOUNT_MODE.md` §2/§8; replaces a generic "capital" field), fee/slippage inputs.
- `BacktestResultPanel`: equity curve, drawdown chart, metrics summary table (every return/P&L figure rendered as `DollarAndPercent` — e.g. "+$4.12 (+16.5%)" — per `SMALL_ACCOUNT_MODE.md` §3, plus a `FeeDragIndicator` showing fees/slippage as % of gross gains per §10), trade list table.
- `BacktestHistoryList`: past runs, selectable for comparison, showing each run's starting balance alongside its results.
- `BacktestCompareView`: overlaid equity curves + side-by-side metrics table for 2+ selected runs — when comparing runs with different starting balances, the dollar figures are shown per-run (not normalized) and the percentage figures are the primary basis for comparison.
- `SmallAccountWarningBanner`: renders when the backtest result trips any `SMALL_ACCOUNT_MODE.md` §11 warning condition (e.g., "This strategy's fees consumed 34% of gross backtest gains at a $25 starting balance — consider a larger account or a lower-frequency strategy"). Informational, dismissible, never blocking.

**Required API calls:** `POST /backtests/run` (with `initial_capital` sourced from `StartingBalanceInput`), `GET /backtests/:id` (polled while `status: "running"`), `GET /backtests` (list — implied, add alongside `:id` route).

**Loading state:** While a backtest is `running`, show a progress indicator ("Running backtest over 2025-01-01 to 2026-01-01...") in place of the results panel; polling interval ~2s.

**Empty state:** No past backtests: history list shows "No backtests run yet — configure one above to get started."

**Error state:** If `status: "failed"`, results panel shows the `error_detail` message and a "Reconfigure and retry" shortcut back to the form pre-filled with the same inputs.

---

### `/signals`

**Layout:** Filter bar (strategy, asset, action, status) above a dense table, each row expandable.

**Components:**
- `SignalFilterBar`.
- `SignalTable`: signal_time, asset, action, strategy, ai_confidence, status — with color-coded status badges.
- `SignalDetailDrawer` (expand-on-click): raw strategy signal, regime tag, AI confidence breakdown, allocator weight, risk engine decision, final explanation text.

**Required API calls:** `GET /signals` (with filters + cursor pagination), detail drawer may need a `GET /signals/:id` (implied — add to `API_CONTRACTS.md` when implemented) if the list response doesn't include full explanation payloads.

**Loading state:** Table skeleton rows (8 placeholder rows); filter bar remains interactive during load.

**Empty state:** "No signals match these filters" when filtered results are empty vs. "No signals generated yet" when totally empty — these are distinct messages so users don't think filters are broken.

**Error state:** Table area shows inline error with retry; filters remain usable to attempt a different query.

---

### `/paper-trading`

**Layout:** Account selector/creation at top; below, tabs or stacked sections for Positions, Open Orders, Trade History, Account Equity Chart.

**Components:**
- `AccountSelector` + `NewAccountModal` (name, asset class, **`StartingBalanceInput`** — same preset/custom pattern as Backtests, minimum $25, per `SMALL_ACCOUNT_MODE.md` §2).
- `PositionsTable`: asset, quantity (fractional-aware formatting, per `SMALL_ACCOUNT_MODE.md` §5/§6), avg entry, unrealized P&L rendered as `DollarAndPercent`.
- `TradeHistoryTable`: filterable (strategy, asset, date range), expandable rows linking to the same explanation detail as `/signals`.
- `AccountEquityChart`, labeled explicitly "Paper Balance" per `SMALL_ACCOUNT_MODE.md` §7 balance-type labeling requirement.
- `ResetAccountButton` (dev/testing utility, confirmation-gated, calls `POST /paper/reset`).
- `SmallAccountWarningBanner`: renders when an active strategy is flagged as a poor fit for this account's current balance (`SMALL_ACCOUNT_MODE.md` §11).

**Required API calls:** `GET /paper/account`, `GET /paper/trades`, `POST /paper/reset`, (account creation — implied `POST /paper/account`, add to `API_CONTRACTS.md` when implemented).

**Loading state:** Skeleton positions/trade rows; equity chart skeleton.

**Empty state:** No accounts yet: full-page prompt to create the first paper account. Account exists but no positions/trades: "No open positions" / "No trades yet — trades will appear here once an active strategy generates and executes a signal."

**Error state:** Reset action failure shows a blocking modal error (since it's a destructive action, silent partial failure is unacceptable) requiring explicit dismissal.

---

### `/risk-monitor`

**Layout:** Top status strip (global kill switch state, account pause state) + grid of per-limit usage cards (daily loss, drawdown, cooldowns, no-trade zones) + risk event log below.

**Components:**
- `KillSwitchControl` (global + per-account), each with confirmation modal.
- `RiskLimitUsageCard` (one per limit type): current usage vs. configured limit, progress-bar style.
- `RiskEventLogTable`: filterable by event type/account.
- `RiskParameterEditor`: per-account and system-default limit editing, with loosening changes visually flagged (red/amber styling) per `RISK_ENGINE.md` §4.

**Required API calls:** (Risk-specific endpoints are not yet in `API_CONTRACTS.md`'s initial 12 — flag for addition: `GET /risk/status`, `POST /risk/kill-switch/global`, `POST /risk/kill-switch/account/:id`, `GET /risk/events`, `GET/PATCH /risk/parameters`. Note this gap explicitly in this page's implementation ticket.)

**Loading state:** Status strip shows a neutral/gray "checking..." badge instead of green/red until the first status fetch resolves — never default to a false "safe" green state while loading.

**Empty state:** Risk event log: "No risk events recorded yet."

**Error state:** If risk status cannot be fetched, the status strip must show an explicit "STATUS UNKNOWN" red/amber state (fail-visible, not fail-silent) rather than hiding the strip.

---

### `/settings`

**Layout:** Vertical sections: Profile, Data Connections, Notifications, Audit Log.

**Components:**
- `ProfileSection` (Supabase Auth-backed user info).
- `DataConnectionStatusCard` (Binance/Binance.US, Alpaca) — masked key display, "Test Connection" button, status badge.
- `NotificationPreferencesForm` (kill-switch trips, daily loss breaches).
- `AuditLogViewer`: searchable/filterable read-only table.

**Required API calls:** connection status (implied `GET /settings/connections`, add to `API_CONTRACTS.md`), `GET /audit-log` (implied, add to `API_CONTRACTS.md`), notification prefs (implied `GET/PATCH /settings/notifications`).

**Loading state:** Each section loads independently with its own skeleton — a slow audit log query should not block the profile section from rendering.

**Empty state:** Audit log: "No audit events yet." Data connections not yet configured: "Not connected — add credentials in your environment configuration" (MVP does not support entering API keys via the UI; see `SECURITY_AND_SAFETY.md`).

**Error state:** "Test Connection" failures show the specific failure reason returned by the backend (e.g., "401 from Alpaca — check API key") inline next to that connection's card.

---

### Cross-Page Requirements

- Every page uses the same `PageShell` (sidebar + top bar with environment/kill-switch indicator) per `UI_SPEC.md` §1.
- Every table with more than ~20 potential rows implements cursor-based pagination consistent with `API_CONTRACTS.md`'s `next_cursor` pattern.
- Every destructive or state-changing action (reset account, kill switch, strategy activation, risk parameter loosening) requires a confirmation modal — no exceptions in MVP.
- Every starting-balance input (Backtests, Paper Trading account creation) uses the shared `StartingBalanceInput` component (presets $25/$50/$100/$250/$500/$1,000 + custom, minimum $25) and every return/P&L display uses the shared `DollarAndPercent` component — per `SMALL_ACCOUNT_MODE.md` §2/§3. These are not page-specific one-offs; implement them once in `components/domain/` and reuse everywhere.
