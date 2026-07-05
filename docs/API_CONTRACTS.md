# API_CONTRACTS.md

## OmniTrade Legacy Engine — Initial API Contracts

### Conventions

- Base URL (local): `http://localhost:8000`
- All responses are JSON. Successful responses return the resource directly or `{"items": [...], "next_cursor": "..."}` for paginated lists.
- All error responses use a shared envelope:
```json
{
  "error": {
    "code": "string_error_code",
    "message": "human-readable message",
    "details": {}
  }
}
```
- All monetary/quantity fields are strings in API responses (to preserve `numeric` precision across JSON) and are parsed to decimal on the frontend.
- Timestamps are ISO 8601 UTC strings.
- Auth: all endpoints except `/health` require a valid Supabase JWT in `Authorization: Bearer <token>` (enforced once auth is wired in Phase 0/1; see `SECURITY_AND_SAFETY.md`).
- **Small Account Mode:** every endpoint accepting a starting-capital or starting-balance value enforces a **$25 minimum** at the API layer (mirroring the database-level `CHECK` constraint in `DATABASE_SCHEMA.md`) — see `SMALL_ACCOUNT_MODE.md`. Return/P&L figures are reported as both a dollar (`_usd`) and percentage (`_pct`) field wherever they appear, rather than one or the other.

---

### `GET /health`
**Purpose:** Liveness/readiness check for uptime monitoring and local verification.
**Request:** none.
**Response 200:**
```json
{ "status": "ok", "db": "connected", "last_ingestion_at": "2026-07-02T10:00:00Z" }
```
**Errors:** `503` with `{"status": "degraded", "db": "disconnected", ...}` if DB unreachable. No auth required.

---

### `GET /markets/assets`
**Purpose:** List tracked assets for the Markets page asset picker.
**Request (query params):** `asset_class` (optional: `crypto` | `stock`), `is_active` (optional bool, default true).
**Response 200:**
```json
{
  "items": [
    { "id": "uuid", "symbol": "BTCUSDT", "asset_class": "crypto", "exchange": "binance_us", "is_active": true }
  ]
}
```
**Errors:** `400` invalid `asset_class` value.

---

### `GET /markets/candles`
**Purpose:** Fetch OHLCV candles for chart rendering.
**Request (query params, all required unless noted):** `asset_id` (uuid), `interval` (`1m|5m|15m|1h|1d`), `start_time` (ISO 8601), `end_time` (ISO 8601, optional — defaults to now).
**Response 200:**
```json
{
  "asset_id": "uuid",
  "interval": "15m",
  "items": [
    { "open_time": "2026-07-02T10:00:00Z", "open": "65000.10", "high": "65120.00", "low": "64950.50", "close": "65080.00", "volume": "12.4531" }
  ]
}
```
**Errors:** `404` unknown `asset_id`; `400` invalid interval or time range (`start_time >= end_time`); `422` missing required params.

---

### `POST /backtests/run`
**Purpose:** Queue and run a backtest for a strategy/parameter set/asset combination.
**Request body:**
```json
{
  "strategy_id": "uuid",
  "parameter_set_id": "uuid",
  "asset_id": "uuid",
  "interval": "1h",
  "start_time": "2025-01-01T00:00:00Z",
  "end_time": "2026-01-01T00:00:00Z",
  "initial_capital": "25",
  "fee_bps": "10",
  "slippage_bps": "5"
}
```
**Note:** `initial_capital` accepts any value **>= 25** (Small Account Mode floor, see `SMALL_ACCOUNT_MODE.md` §2/§8). The example above uses the $25 minimum deliberately — this is the platform's default proving ground, not a large-account value with small-account support bolted on.
**Response 202 (MVP runs synchronously but returns 202 to allow moving to async later):**
```json
{ "backtest_id": "uuid", "status": "running" }
```
**Errors:** `404` unknown strategy/parameter_set/asset; `400` invalid date range, insufficient candle history for the range, **or `initial_capital` below 25**; `409` if an identical backtest is already running.

---

### `GET /backtests/:id`
**Purpose:** Fetch backtest status and results.
**Request:** path param `id` (uuid).
**Response 200 (completed):**
```json
{
  "id": "uuid",
  "status": "completed",
  "strategy_id": "uuid",
  "parameter_set_id": "uuid",
  "asset_id": "uuid",
  "initial_capital": "25.00",
  "metrics": {
    "total_return_usd": "4.12",
    "total_return_pct": "0.165",
    "win_rate": "0.57",
    "max_drawdown": "0.092",
    "sharpe_like": "1.21",
    "trade_count": 42,
    "average_trade_usd": "0.098",
    "fee_drag_pct": "0.34"
  },
  "small_account_warning": {
    "type": "high_fee_drag",
    "detail": "Fees consumed 34% of gross backtest gains at this starting balance."
  },
  "trades": [
    { "side": "buy", "quantity": "0.00038", "price": "64200.00", "executed_at": "2025-02-11T14:00:00Z", "reason": "fast MA crossed above slow MA" }
  ]
}
```
**Note:** `metrics` reports both dollar (`_usd` suffix) and percentage (`_pct` suffix) figures for return-type values per `SMALL_ACCOUNT_MODE.md` §3, plus `fee_drag_pct` per §10. `small_account_warning` is `null` when no warning condition is triggered (see `SMALL_ACCOUNT_MODE.md` §11).
**Response 200 (still running):** `{"id": "uuid", "status": "running"}`
**Errors:** `404` unknown backtest id; `200` with `status: "failed"` and an `error_detail` field if the backtest run itself failed (not a 5xx, since the request to check status succeeded).

---

### `GET /strategies`
**Purpose:** List all strategies with active state, for Strategy Lab.
**Request (query params):** `is_active` (optional bool).
**Response 200:**
```json
{
  "items": [
    { "id": "uuid", "name": "MA Crossover", "slug": "ma_crossover", "is_active": false, "module_version": "1.0.0", "default_params": {"fast_period": 10, "slow_period": 50} }
  ]
}
```

---

### `POST /strategies`
**Purpose:** Register a new strategy (admin/dev use — most strategies are seeded, not created via UI in MVP).
**Request body:**
```json
{ "name": "MA Crossover", "slug": "ma_crossover", "description": "...", "module_version": "1.0.0" }
```
**Response 201:** the created strategy object (shape as in `GET /strategies` items).
**Errors:** `409` if `slug` already exists; `422` missing required fields.

---

### `PATCH /strategies/:id`
**Purpose:** Update strategy metadata **or** trigger the audited activate/deactivate flow.
**Request body (metadata update):**
```json
{ "description": "Updated description" }
```
**Request body (activation — preferred to use the dedicated endpoint below when available, but PATCH supports it for MVP):**
```json
{ "is_active": true, "active_parameter_set_id": "uuid" }
```
**Response 200:** updated strategy object.
**Errors:** `404` unknown strategy id; `400` if activating without a valid `active_parameter_set_id`; `409` if the parameter set hasn't passed a completed backtest yet (per `MVP_BUILD_PLAN.md` Phase 3 activation criteria).
**Note:** Every successful activation/deactivation writes an `audit_log` row per `STRATEGY_ENGINE.md` §4.

---

### `GET /signals`
**Purpose:** List generated signals for the Signals page feed.
**Request (query params):** `strategy_id` (optional), `asset_id` (optional), `action` (optional: `buy|sell|hold`), `status` (optional: `generated|risk_approved|risk_rejected|executed|expired`), `limit` (default 50), `cursor` (optional).
**Response 200:**
```json
{
  "items": [
    {
      "id": "uuid",
      "strategy_id": "uuid",
      "asset_id": "uuid",
      "signal_time": "2026-07-02T09:45:00Z",
      "action": "buy",
      "raw_strength": "0.62",
      "ai_confidence": "0.71",
      "regime_tag": "trending_up",
      "status": "executed"
    }
  ],
  "next_cursor": "opaque-string-or-null"
}
```

---

### `POST /paper/account`
**Purpose:** Create a new paper trading account. Referenced from `FRONTEND_PAGE_SPECS.md`'s Paper Trading page (`NewAccountModal`) — documented here alongside the rest of the `/paper` resource endpoints.
**Request body:**
```json
{ "name": "Family Crypto Paper", "asset_class": "crypto", "starting_balance": "25" }
```
**Note:** `starting_balance` accepts any value **>= 25** (Small Account Mode floor, see `SMALL_ACCOUNT_MODE.md` §2). The UI presents preset buttons ($25/$50/$100/$250/$500/$1,000) plus a free-text field — the API itself just validates the floor, it does not restrict to the preset list.
**Response 201:**
```json
{ "id": "uuid", "name": "Family Crypto Paper", "asset_class": "crypto", "starting_balance": "25.00", "current_cash_balance": "25.00", "is_active": true }
```
**Errors:** `400` if `starting_balance` is below 25 or not a valid positive number; `422` missing required fields.
**Note:** Writes an `audit_log` entry (`action: "paper_account_created"`).

---

### `GET /paper/account`
**Purpose:** Fetch the current (or specified) paper account's state for the dashboard/Paper Trading page.
**Request (query params):** `account_id` (optional — defaults to the user's primary/most-recent account).
**Response 200:**
```json
{
  "id": "uuid",
  "name": "Family Crypto Paper",
  "asset_class": "crypto",
  "starting_balance": "25.00",
  "current_cash_balance": "22.40",
  "equity": "25.75",
  "equity_return_usd": "0.75",
  "equity_return_pct": "0.030",
  "positions": [
    { "asset_id": "uuid", "symbol": "BTCUSDT", "quantity": "0.00038", "avg_entry_price": "64500.00", "unrealized_pnl_usd": "0.31", "unrealized_pnl_pct": "0.012" }
  ]
}
```
**Note:** `positions[].quantity` supports fractional crypto amounts at full precision (see `SMALL_ACCOUNT_MODE.md` §5). P&L and equity-return figures are reported as both dollar and percentage per `SMALL_ACCOUNT_MODE.md` §3.
**Errors:** `404` unknown or inaccessible `account_id`.

---

### `GET /paper/trades`
**Purpose:** Trade history for the Paper Trading page.
**Request (query params):** `account_id` (required), `strategy_id` (optional), `asset_id` (optional), `start_time`/`end_time` (optional), `limit`/`cursor`.
**Response 200:**
```json
{
  "items": [
    { "id": "uuid", "asset_id": "uuid", "side": "buy", "quantity": "0.02", "price": "65010.00", "fee": "0.65", "executed_at": "2026-07-01T15:20:00Z", "signal_id": "uuid" }
  ],
  "next_cursor": null
}
```

---

### `POST /paper/reset`
**Purpose:** Reset a paper account back to its starting balance and clear open positions — a dev/testing convenience, never available against anything resembling live trading.
**Request body:**
```json
{ "account_id": "uuid", "confirm": true }
```
**Response 200:**
```json
{ "account_id": "uuid", "current_cash_balance": "10000.00", "positions": [] }
```
**Errors:** `400` if `confirm` is not `true`; `404` unknown account; `403` if the account is not a paper account (defense-in-depth check, even though only paper accounts exist in MVP).
**Note:** Writes an `audit_log` entry (`action: "paper_account_reset"`) including the prior state, since this is a destructive action.

---

### Error Code Reference (used across endpoints)

| Code | HTTP Status | Meaning |
|---|---|---|
| `not_found` | 404 | Referenced resource does not exist |
| `validation_error` | 422 | Request body/params failed schema validation |
| `invalid_request` | 400 | Semantically invalid request (bad range, bad state transition) |
| `conflict` | 409 | Request conflicts with current resource state |
| `forbidden` | 403 | Authenticated but not permitted |
| `unauthorized` | 401 | Missing/invalid auth token |
| `internal_error` | 500 | Unexpected server error (always logged, never exposes internals to the client) |

> Note: risk-engine rejection reasons such as `position_below_minimum_order_size` (see `RISK_ENGINE.md` §2.1a, relevant to Small Account Mode) surface within signal/trade-related response bodies rather than as top-level HTTP error codes, since a risk rejection is a valid, expected outcome of signal evaluation — not a request-processing failure.
