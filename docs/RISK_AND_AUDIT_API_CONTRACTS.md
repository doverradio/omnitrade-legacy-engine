# RISK_AND_AUDIT_API_CONTRACTS.md

## OmniTrade Legacy Engine — Risk, Audit, Settings & AI Review API Contracts

This document fills the endpoint gaps flagged between `FRONTEND_PAGE_SPECS.md` (Risk Monitor, Settings, AI Review pages) and the original `API_CONTRACTS.md`. It follows the same conventions.

### Conventions (inherited from `API_CONTRACTS.md`)

- Base URL (local): `http://localhost:8000`
- Error envelope, auth requirement (Supabase JWT), numeric-fields-as-strings, ISO 8601 UTC timestamps — all identical to `API_CONTRACTS.md`'s conventions section. Refer there rather than duplicating.
- Every endpoint below is subject to `SECURITY_AND_SAFETY.md`: paper trading only, no live-trading code paths, audit logging on every state change.

---

### `GET /risk/status`

**Purpose:** Real-time risk status for the Risk Monitor page's top status strip and per-limit usage cards.
**Frontend page:** `/risk-monitor`
**MVP phase:** Required for MVP (Phase 7).

**Request (query params):** `account_id` (optional — if omitted, returns global status plus the user's primary/most-recent account's status).

**Response 200:**
```json
{
  "global_kill_switch": { "engaged": false, "engaged_at": null, "engaged_by": null, "reason": null },
  "account": {
    "account_id": "uuid",
    "trading_paused": false,
    "paused_reason": null,
    "daily_loss": { "used": "45.10", "limit": "300.00", "pct_used": "0.15" },
    "drawdown": { "used": "0.032", "limit": "0.15", "pct_used": "0.213" },
    "active_cooldowns": [
      { "strategy_id": "uuid", "asset_id": "uuid", "cooldown_until": "2026-07-04T08:00:00Z", "reason": "3 consecutive losses" }
    ],
    "active_no_trade_zones": [
      { "asset_id": "uuid", "reason": "stale_data", "since": "2026-07-03T14:02:00Z" }
    ]
  }
}
```

**Errors:**
- `404` — unknown `account_id`.
- `503` with `{"error": {"code": "risk_status_unavailable", ...}}` if the risk engine's status cannot be computed (e.g., DB read failure) — the frontend must render this as an explicit "STATUS UNKNOWN" state per `FRONTEND_PAGE_SPECS.md`, never fall back to a default "safe" display.

---

### `POST /risk/kill-switch/enable`

**Purpose:** Trip a kill switch — either global or a specific account — halting new signal approval.
**Frontend page:** `/risk-monitor` (`KillSwitchControl`)
**MVP phase:** Required for MVP (Phase 7).

**Request body:**
```json
{ "scope": "global", "account_id": null, "reason": "manual review requested by user", "confirm": true }
```
or
```json
{ "scope": "account", "account_id": "uuid", "reason": "unexpected drawdown pattern", "confirm": true }
```

**Response 200:**
```json
{ "scope": "global", "account_id": null, "engaged": true, "engaged_at": "2026-07-03T15:04:00Z", "engaged_by": "user:uuid" }
```

**Errors:**
- `400` — `confirm` not `true`, missing `reason`, or `scope: "account"` without `account_id`.
- `404` — unknown `account_id` when `scope: "account"`.
- `409` — kill switch already engaged for the requested scope.

**Audit requirement:** Every successful call writes an `audit_log` row (`action: "kill_switch_enabled"`, `entity_type: "global"` or `"paper_account"`, `after_state` including `reason` and `engaged_by`). This is mandatory and non-optional — the endpoint must not return `200` without a corresponding committed audit row (write both in the same DB transaction).

---

### `POST /risk/kill-switch/disable`

**Purpose:** Re-arm a previously tripped kill switch. Always a deliberate, explicit human action — never automatic.
**Frontend page:** `/risk-monitor`
**MVP phase:** Required for MVP (Phase 7).

**Request body:**
```json
{ "scope": "global", "account_id": null, "reason": "reviewed logs, no issue found", "confirm": true }
```

**Response 200:**
```json
{ "scope": "global", "account_id": null, "engaged": false, "disengaged_at": "2026-07-03T15:40:00Z", "disengaged_by": "user:uuid" }
```

**Errors:**
- `400` — `confirm` not `true` or missing `reason`.
- `404` — unknown `account_id` when `scope: "account"`.
- `409` — kill switch is not currently engaged for the requested scope (nothing to disable).

**Audit requirement:** Same as `/risk/kill-switch/enable` — writes `action: "kill_switch_disabled"` with `reason` and `disengaged_by`, same transaction guarantee. Per `RISK_ENGINE.md` §2.8, this endpoint must never be callable by any automated/scheduled process — only an authenticated human-initiated request.

---

### `GET /risk/rules`

**Purpose:** Fetch current risk parameter configuration (system defaults + per-account overrides) for the Risk Monitor page's `RiskParameterEditor`.
**Frontend page:** `/risk-monitor`
**MVP phase:** Required for MVP (Phase 7).

**Request (query params):** `account_id` (optional — if omitted, returns system defaults only).

**Response 200:**
```json
{
  "account_id": "uuid",
  "rules": {
    "max_position_size_pct": "0.05",
    "max_daily_loss_pct": "0.03",
    "max_drawdown_pct": "0.15",
    "default_stop_loss_pct": "0.05",
    "cooldown_after_losses": 3,
    "cooldown_duration_hours": 24
  },
  "is_override": true,
  "system_defaults": {
    "max_position_size_pct": "0.05",
    "max_daily_loss_pct": "0.03",
    "max_drawdown_pct": "0.15",
    "default_stop_loss_pct": "0.05",
    "cooldown_after_losses": 3,
    "cooldown_duration_hours": 24
  }
}
```

**Errors:** `404` unknown `account_id`.

---

### `PATCH /risk/rules`

**Purpose:** Update risk parameters (system default or per-account override).
**Frontend page:** `/risk-monitor`
**MVP phase:** Required for MVP (Phase 7).

**Request body:**
```json
{
  "account_id": "uuid",
  "rules": { "max_daily_loss_pct": "0.05" },
  "confirm_loosening": true
}
```

**Response 200:** updated rules object (same shape as `GET /risk/rules` response).

**Errors:**
- `404` — unknown `account_id`.
- `400` — a submitted rule value is outside a sane bound (e.g., negative, or `max_position_size_pct > 1.0`).
- `422` — `confirm_loosening` is missing/`false` **and** the change loosens any limit (i.e., increases a max-loss/drawdown/position-size type value, or decreases a cooldown duration) — this forces the explicit acknowledgment flow described in `RISK_ENGINE.md` §4 and `UI_SPEC.md` §2.7.

**Audit requirement:** Every successful call writes an `audit_log` row (`action: "risk_rules_updated"`, `before_state`, `after_state`, flagged `is_loosening: true/false` in `after_state`) in the same transaction as the update.

---

### `GET /audit-log`

**Purpose:** Read-only audit trail for the Settings page's `AuditLogViewer`.
**Frontend page:** `/settings`
**MVP phase:** Required for MVP (Phase 6/7, whenever the first audited actions exist — the endpoint itself should exist from Phase 0 since `audit_log` writes begin immediately).

**Request (query params):** `entity_type` (optional), `entity_id` (optional), `action` (optional), `actor` (optional), `start_time`/`end_time` (optional), `limit` (default 50), `cursor` (optional).

**Response 200:**
```json
{
  "items": [
    {
      "id": 10245,
      "actor": "user:uuid",
      "action": "kill_switch_enabled",
      "entity_type": "global",
      "entity_id": null,
      "before_state": null,
      "after_state": { "reason": "manual review requested by user", "engaged_by": "user:uuid" },
      "created_at": "2026-07-03T15:04:00Z"
    }
  ],
  "next_cursor": null
}
```

**Errors:** `400` invalid filter combination (e.g., `end_time` before `start_time`).

**Note:** No `POST`, `PATCH`, or `DELETE` route exists for this resource, by design (`SECURITY_AND_SAFETY.md` §7) — the audit log is append-only at the application layer, written exclusively as a side effect of other endpoints' transactions.

---

### `GET /settings`

**Purpose:** Fetch current user/account-level settings for the Settings page (data connection status, notification preferences).
**Frontend page:** `/settings`
**MVP phase:** Data connection status required for MVP (Phase 1, so users can verify ingestion health); notification preferences can land later (Phase 7/8) but the contract is defined here upfront so the schema doesn't need to change later.

**Request:** none (scoped to the authenticated user via JWT).

**Response 200:**
```json
{
  "data_connections": [
    { "provider": "binance_us", "status": "connected", "last_checked_at": "2026-07-03T15:00:00Z", "detail": null },
    { "provider": "alpaca", "status": "error", "last_checked_at": "2026-07-03T14:58:00Z", "detail": "401 unauthorized — check API key" }
  ],
  "notifications": {
    "email_on_kill_switch": true,
    "email_on_daily_loss_breach": true,
    "notification_email": "family@example.com"
  }
}
```

**Errors:** `503` if status cannot be determined for a provider (returned as `status: "unknown"` for that provider within a `200` response, not a top-level error — one provider's check failing shouldn't fail the whole request).

---

### `PATCH /settings`

**Purpose:** Update notification preferences. (Data connection credentials are **not** editable via this endpoint or any UI — see `SECURITY_AND_SAFETY.md` §6, credentials are environment-variable-only.)
**Frontend page:** `/settings`
**MVP phase:** Later phase (Phase 7/8) — contract defined now for forward compatibility.

**Request body:**
```json
{
  "notifications": { "email_on_kill_switch": true, "email_on_daily_loss_breach": false, "notification_email": "family@example.com" }
}
```

**Response 200:** updated settings object (same shape as `GET /settings`'s `notifications` field, echoed back).

**Errors:** `422` invalid email format; `400` if attempting to include a `data_connections` field in the request body (explicitly rejected — this endpoint never accepts credential data).

**Audit requirement:** Writes an `audit_log` row (`action: "settings_updated"`, `before_state`, `after_state`) — notification preference changes are low-risk but still logged for consistency and traceability.

---

### `GET /ai/review`

**Purpose:** Feed data for the AI Review page: regime classification history, allocator weight history, and post-trade review flags awaiting human approve/dismiss.
**Frontend page:** `/ai-review` (referred to as "AI Review" in `UI_SPEC.md` §2.8; not yet listed as a route in `FRONTEND_PAGE_SPECS.md`'s 8 pages — add `/ai-review` as a 9th route when implemented, matching `UI_SPEC.md`).
**MVP phase:** Later phase (Phase 6).

**Request (query params):** `account_id` (optional), `start_time`/`end_time` (optional, defaults to last 30 days).

**Response 200:**
```json
{
  "regime_history": [
    { "asset_id": "uuid", "regime_tag": "trending_up", "confidence": "0.78", "as_of": "2026-07-03T14:00:00Z" }
  ],
  "allocator_history": [
    { "as_of": "2026-07-03T00:00:00Z", "weights": { "ma_crossover": "0.35", "rsi_mean_reversion": "0.20", "breakout": "0.45" } }
  ],
  "pending_reviews": [
    {
      "id": "uuid",
      "strategy_id": "uuid",
      "summary": "ma_crossover underperforming backtest by 8% in high_volatility regime over last 14 days",
      "recommendation": { "type": "reduce_allocation_weight", "detail": { "target_weight": "0.15" } },
      "created_at": "2026-07-02T06:00:00Z",
      "status": "pending"
    }
  ]
}
```

**Errors:** `404` unknown `account_id`.

**Note:** `pending_reviews` entries require a separate approve/dismiss action (e.g., `POST /ai/review/:id/approve` and `POST /ai/review/:id/dismiss` — not detailed here as they weren't in the original 10-endpoint request; flag for a follow-up contract addition before Phase 6 implementation begins). Per `AI_LAYER.md` §5, recommendations must never auto-apply — these endpoints are the human-in-the-loop gate.

---

### `GET /ai/explanations/:signal_id`

**Purpose:** Fetch the full AI-generated explanation trail for a single signal, for the Signals page detail drawer and Paper Trading trade detail view.
**Frontend page:** `/signals`, `/paper-trading`
**MVP phase:** Required for MVP (Phase 6).

**Request:** path param `signal_id` (uuid).

**Response 200:**
```json
{
  "signal_id": "uuid",
  "regime": { "tag": "trending_up", "confidence": "0.78", "model_version": "1.0.0" },
  "confidence_score": {
    "value": "0.71",
    "factors": [
      { "label": "regime favorable", "contribution": "+0.15" },
      { "label": "strategy recent win rate 62%", "contribution": "+0.05" },
      { "label": "elevated volatility", "contribution": "-0.10" }
    ],
    "model_version": "1.0.0"
  },
  "allocator_weight": { "value": "0.35", "model_version": "1.0.0" },
  "explanation": "Bought 0.02 BTC: MA crossover signal (fast MA crossed above slow MA), regime classified as trending_up (78% confidence), AI confidence 0.71 based on favorable regime and strategy's 58% win rate over the last 30 trades, sized at 1.5% of paper account equity per risk engine limits.",
  "risk_decision": { "action": "approved", "resized": false, "reason": null }
}
```

**Errors:**
- `404` — unknown `signal_id`, **or** `signal_id` exists but has no associated `model_outputs` rows yet (e.g., still `status: "generated"` and not yet scored) — distinguish these two cases in the error `message` even though both return `404`, so the frontend can show "not yet processed" vs. "not found."
- **Validation constraint:** this endpoint must never return explanation data that isn't tied to a real, existing `signals.id` — every response's `signal_id` field is verified against the `signals` table before returning `200`; there is no code path that synthesizes an explanation without a backing signal record.

---

### Summary Table

| Endpoint | Frontend Page | MVP Phase |
|---|---|---|
| `GET /risk/status` | `/risk-monitor` | Required — Phase 7 |
| `POST /risk/kill-switch/enable` | `/risk-monitor` | Required — Phase 7 |
| `POST /risk/kill-switch/disable` | `/risk-monitor` | Required — Phase 7 |
| `GET /risk/rules` | `/risk-monitor` | Required — Phase 7 |
| `PATCH /risk/rules` | `/risk-monitor` | Required — Phase 7 |
| `GET /audit-log` | `/settings` | Required — endpoint from Phase 0, meaningful data from Phase 1+ |
| `GET /settings` | `/settings` | Data connections: Phase 1. Notifications: Phase 7/8. |
| `PATCH /settings` | `/settings` | Phase 7/8 |
| `GET /ai/review` | `/ai-review` | Phase 6 (approve/dismiss sub-endpoints to be specified separately) |
| `GET /ai/explanations/:signal_id` | `/signals`, `/paper-trading` | Required — Phase 6 |
