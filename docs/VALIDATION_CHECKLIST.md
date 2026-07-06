# VALIDATION_CHECKLIST.md

## OmniTrade Legacy Engine — Phase Validation Checklists

> Do not begin the next phase's Copilot prompts until every item in the current phase's checklist passes. Record results (pass/fail + notes) in a `docs/validation-log.md` or PR description for traceability.

---

### Phase 0 — Repo Scaffold

**Commands to run:**
```bash
docker compose -f infra/docker/docker-compose.yml up -d
cd apps/api && source .venv/bin/activate && alembic upgrade head
cd apps/api && pytest
cd apps/web && pnpm lint && pnpm build
```

**Tests to pass:**
- [ ] `apps/api` test suite runs and passes (even if it's currently trivial/empty placeholder tests).
- [ ] `apps/web` lints with zero errors and builds successfully.

**Pages to open:**
- [ ] `http://localhost:3000/dashboard` — loads, shows PageShell with sidebar and 4 placeholder summary cards.
- [ ] `http://localhost:3000/markets`, `/strategy-lab`, `/backtests`, `/signals`, `/paper-trading`, `/risk-monitor`, `/settings` — each loads with a placeholder "coming soon" message and no console errors.

**API endpoints to test:**
- [ ] `GET http://localhost:8000/health` → `200` with `{"status": "ok", "db": "connected", ...}`.
- [ ] Stopping the Postgres container and re-checking `/health` → `503` with `"status": "degraded"` (confirms the check is real, not hardcoded).

**Expected results:**
- Full stack runs via `docker compose up` with no manual intervention.
- No secrets present in any committed file (`git grep` for suspicious values as a spot check).
- README Quick Start commands work verbatim on a clean checkout.

---

### Phase 1 — Data Ingestion

**Commands to run:**
```bash
python scripts/seed_assets.py
python scripts/backfill_historical.py --symbol BTCUSDT --interval 1d --start-date 2025-01-01 --end-date 2026-01-01
cd apps/api && pytest tests/unit/services/data -v
```

**Tests to pass:**
- [ ] `http_client.py` backoff unit tests pass (including the "gives up after max retries" case).
- [ ] `binance_client.py` unit tests pass (single-page, multi-page, and failure cases).
- [ ] `candle_writer.py` upsert test confirms no duplicates on re-run with overlapping data.
- [ ] `markets.py` route integration tests pass (asset list, candle fetch, 404, 400, 422 cases).

**Pages to open:**
- [ ] `/markets` — asset list shows BTCUSDT, ETHUSDT (and SOLUSDT if supported), AAPL.
- [ ] Select BTCUSDT, interval 1d — candlestick chart renders real historical data matching the backfilled range.
- [ ] Switch to an interval with no backfilled data (e.g., 1m) — correct empty state message appears, not an error.
- [ ] Select an asset with zero candles at all — correct empty state, asset list remains usable.

**API endpoints to test:**
- [ ] `GET /markets/assets` → returns seeded assets.
- [ ] `GET /markets/candles?asset_id=<BTCUSDT id>&interval=1d&start_time=2025-01-01T00:00:00Z` → returns ~365 rows.
- [ ] `GET /markets/candles?asset_id=<unknown-uuid>&interval=1d` → `404`.
- [ ] `GET /markets/candles?asset_id=<valid>&interval=bogus` → `400`.
- [ ] `GET /health` after the worker has run at least once → `last_ingestion_at` is a recent, non-null timestamp.

**Expected results — deliberate failure test:**
- [ ] Temporarily point `binance_client.py` at an invalid base URL (or block network access to it) and re-run the ingestion worker: confirm the worker logs the failure clearly, does **not** crash the whole worker process, and other assets (if using a mocked partial-failure scenario) continue to ingest. Revert the change afterward.
- [ ] Re-run `backfill_historical.py` for a range that partially overlaps already-backfilled data: confirm no duplicate rows and the script reports success without re-fetching unnecessarily (or re-fetches safely — either is acceptable as long as no duplicates result).

---

### Phase 3 — Backtesting (for reference once reached; full detail lives in `MVP_BUILD_PLAN.md`)

**Commands to run:**
```bash
cd apps/api && pytest tests/unit/services/strategies -v
cd apps/api && pytest tests/unit/services/backtesting -v
```

**Tests to pass:**
- [ ] All six strategy modules have unit tests against synthetic price series with known expected signals.
- [ ] Backtest engine produces metrics matching the `backtests.metrics` shape for at least one real historical run.

**Pages to open:**
- [ ] `/strategy-lab` — all six seeded strategies listed, parameter forms render correctly per strategy.
- [ ] `/backtests` — run a backtest end-to-end from the UI, see equity curve + metrics + trade list render.

**API endpoints to test:**
- [ ] `POST /backtests/run` → `202` with `backtest_id`.
- [ ] `GET /backtests/:id` → transitions from `running` to `completed` with full metrics.

**Expected results:**
- [ ] Each of the 6 strategies has at least one completed backtest with sane, non-null metrics.

---

### Phase 5 — Portfolio Intelligence + Paper Execution Foundation

**Commands to run:**
```bash
cd apps/api
pytest tests/api -v
pytest tests/unit -v
pytest tests/integration -v
pytest tests/services -v

cd ../web
pnpm test
pnpm lint
```

**Backend validation:**
- [ ] Paper account creation works with the documented $25 minimum floor and returns expected account state.
- [ ] Portfolio accounting updates correctly after paper execution events (cash, equity, positions).
- [ ] Position tracking remains consistent across account queries and trade history queries.
- [ ] Trade recording writes complete and queryable trade records for each executed paper fill.
- [ ] Fractional crypto quantities are persisted and returned without precision loss.
- [ ] Fractional stock support behavior is respected for supported symbols and handled safely for unsupported paths.
- [ ] Signal execution path runs end-to-end for paper mode without introducing live-trading paths.
- [ ] Duplicate execution prevention is verified (same signal is not executed twice).
- [ ] Restart recovery is verified (in-flight/pending states recover without duplicate fills or orphaned accounting state).
- [ ] Audit logging is verified for all state-changing actions in this phase.

**Frontend validation:**
- [ ] `/paper-trading` renders complete Paper Trading workflows with correct paper labels and no live-capital ambiguity.
- [ ] Portfolio dashboard surfaces account equity and performance consistently with backend state.
- [ ] Trade history view renders executed paper trades correctly and remains filterable.
- [ ] Portfolio timeline view renders chronological account/trade progression without missing states.
- [ ] Performance charts render with correct loading/empty/error behavior and no precision-format regressions.
- [ ] Signals display remains consistent with execution outcomes (`generated`, `risk_rejected`, `executed`, `expired` as applicable).
- [ ] Small Account labeling is explicit wherever balances or returns are shown.

**Accounting validation:**
- [ ] Cash balance reconciliation passes against trade history and current open positions.
- [ ] Position reconciliation passes between holdings view and executed trade ledger.
- [ ] Realized P&L is computed and displayed correctly.
- [ ] Unrealized P&L is computed and displayed correctly.
- [ ] Equity calculation matches cash + mark-to-market position value.
- [ ] Fee accounting is consistently included in execution and portfolio-level reporting.
- [ ] Dollar + percentage reporting appears together wherever required by Small Account Mode.

**Small Account Mode validation ($25 proving ground):**
- [ ] End-to-end operation is validated at a $25 starting balance.
- [ ] Fractional crypto execution and accounting are validated at small-account scale.
- [ ] Supported fractional stock behavior is validated at small-account scale.
- [ ] Fee drag visibility is present and understandable in results/performance surfaces.
- [ ] Percentage sizing assumptions are respected in portfolio and execution flows.
- [ ] Minimum order handling is validated (including safe rejection paths where minimums are not met).
- [ ] Paper labeling is consistently visible across all phase-relevant pages and outputs.

**Failure scenarios:**
- [ ] Duplicate execution attempt is safely rejected or deduplicated with no double fill.
- [ ] Service restart during execution recovers cleanly with no accounting drift.
- [ ] Missing market data path is handled safely with clear state and no unsafe execution.
- [ ] Rejected trade path is handled and recorded correctly.
- [ ] Invalid order size path is handled and recorded correctly.
- [ ] Internal simulator failure path is handled safely and visibly.
- [ ] Disconnected paper broker path is handled safely and visibly.
- [ ] Stale signal handling is validated.
- [ ] Expired signal handling is validated.

**Expected results (Phase 5 exit criteria):**
- [ ] At least one strategy runs unattended for 5+ consecutive trading days against a paper account.
- [ ] Zero duplicate executions are observed.
- [ ] Zero accounting mismatches are observed.
- [ ] Restart recovery is verified as successful.
- [ ] Realized P&L is verified.
- [ ] Unrealized P&L is verified.
- [ ] Portfolio reconciliation from trade history is verified.
- [ ] Trade records show no missed executions for approved paper signals during the validation window.
- [ ] Trade records show no duplicated executions during the validation window.
- [ ] Correct P&L accounting is verified for the validation run.
- [ ] $25 Small Account Mode is validated end-to-end.
- [ ] Audit logs are written for every state-changing action.
- [ ] No live-trading code path exists in the implemented Phase 5 scope.

> Checklists for Phases 4–8 should be appended to this file as those phases are reached, following the same format (commands / tests / pages / endpoints / expected results), referencing the exit criteria already defined per-phase in `MVP_BUILD_PLAN.md`.

---

### General Validation Principles (Apply to Every Phase)

1. **No phase is "done" based on code existing — it's done when its checklist passes against a real, running local environment.**
2. Every checklist item that references a UI page must be manually opened and visually inspected at least once per phase — automated tests alone are not sufficient sign-off for MVP phases.
3. Every checklist item involving a destructive or state-changing action (resets, kill switches, activations) must be tested at least once against a non-production environment before being considered validated.
4. Any checklist failure blocks progression to the next phase's Copilot prompts until resolved — do not "come back to it later" once further prompts have been run on top of unresolved failures.
