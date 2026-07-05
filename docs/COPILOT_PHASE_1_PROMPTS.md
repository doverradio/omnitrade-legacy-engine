# COPILOT_PHASE_1_PROMPTS.md

## OmniTrade Legacy Engine — GitHub Copilot Prompts: Phase 1 Only

**Scope:** Asset registry, Binance/Binance.US candle ingestion, candle storage schema, candle-fetch endpoint, basic market chart page, seed script for BTC/ETH/SOL, graceful handling of exchange failures. No strategies, no AI, no risk, no execution.

**Prepend this to your first Copilot Chat message in this phase:**
> "Read docs/DATABASE_SCHEMA.md sections 2.1-2.2, docs/DATA_SOURCES.md, docs/API_CONTRACTS.md (markets endpoints), docs/BACKEND_MODULE_SPECS.md (services/data section), and docs/SMALL_ACCOUNT_MODE.md sections 5-6 (fractional quantity precision requirements) before making changes. Phase 0 scaffolding already exists — extend it, don't recreate it. Do not implement strategies, AI, risk, or execution logic in this phase. Before starting each prompt below, check whether it would introduce or change an architectural decision (see docs/adr/README.md for the criteria) — if it would, stop and ask before writing code. None of the Phase 1 prompts below are expected to require a new ADR, but this check applies regardless."

Run these prompts **in order**, one at a time, reviewing/committing after each.

---

### Prompt 1.1 — Asset & Candle Models + Migration
```
Implement app/models/asset.py and app/models/candle.py in apps/api exactly per docs/DATABASE_SCHEMA.md sections 2.1 and 2.2 (columns, types, constraints, the unique index on candles). This includes the Small Account Mode columns on assets — supports_fractional, min_order_notional, qty_step_size — per docs/SMALL_ACCOUNT_MODE.md sections 5-6; these are needed from Phase 1 onward even though the risk engine that consumes them isn't built until a later phase.
Generate an Alembic migration creating both tables (alembic revision --autogenerate, then review the generated migration for correctness against the schema doc before applying).
Apply the migration locally and confirm both tables exist via psql or Supabase Studio.
```

### Prompt 1.2 — Asset & Candle Schemas
```
Implement app/schemas/asset.py and app/schemas/candle.py per docs/API_CONTRACTS.md's GET /markets/assets and GET /markets/candles response shapes exactly (field names, types — remember numeric fields are strings in API responses per the API_CONTRACTS.md conventions section). asset.py's response schema includes supports_fractional, min_order_notional, and qty_step_size per docs/SMALL_ACCOUNT_MODE.md sections 5-6, even though no endpoint currently exposes them in a documented example response — they're part of the asset model and should not be silently dropped from the schema.
```

### Prompt 1.3 — Shared HTTP Client with Backoff
```
Implement app/services/data/http_client.py: a shared async HTTP client wrapper (using httpx) that:
- Implements exponential backoff with jitter on 429 and 5xx responses (configurable max retries, base delay).
- Logs every retry attempt via app/core/logging.py, including the endpoint and status code.
- Raises a clear, typed exception (e.g., ExternalAPIError) after exhausting retries, including the last response status/body for debugging.
This will be used by both the Binance and Alpaca clients — do not duplicate retry logic in each client.
Write unit tests mocking httpx responses to verify backoff behavior triggers correctly on 429 and gives up after max retries.
```

### Prompt 1.4 — Binance/Binance.US Candle Ingestion Client
```
Implement app/services/data/binance_client.py per docs/DATA_SOURCES.md section 2.1:
- fetch_klines(symbol: str, interval: str, start_time: datetime, end_time: datetime | None) -> list[NormalizedCandle]: calls the Binance.US public klines REST endpoint, paginating as needed for ranges exceeding the API's per-request candle limit.
- Normalize raw kline arrays into the internal candle shape matching app/models/candle.py (open_time, close_time, open, high, low, close, volume, source="binance_us").
- Use app/services/data/http_client.py for all requests — do not implement separate retry logic here.
- On any unrecoverable failure (after retries exhausted), raise a typed exception and ensure the caller can catch it to log an audit_log-style failure record (audit_log model doesn't exist until a later prompt — for now, log via app/core/logging.py at ERROR level with full context, and add a TODO comment referencing audit_log for when that model exists).
Write unit tests with mocked HTTP responses covering: a successful single-page fetch, a multi-page paginated fetch, and a failure case that exhausts retries and raises cleanly.
```

### Prompt 1.5 — Candle Upsert Logic
```
Implement app/services/data/candle_writer.py:
- upsert_candles(db_session, asset_id: UUID, interval: str, candles: list[NormalizedCandle]) -> int: writes candles to the database using an upsert (ON CONFLICT on the (asset_id, interval, open_time) unique constraint from docs/DATABASE_SCHEMA.md) so re-running ingestion for an already-covered range never creates duplicates. Returns the count of rows inserted/updated.
Write a unit test (using a test DB or in-memory/sqlite-compatible approach if the project's test setup supports it, otherwise an integration test against a test Postgres instance) that calls upsert_candles twice with overlapping data and confirms no duplicate rows result.
```

### Prompt 1.6 — Candles API Endpoint
```
Implement app/api/routes/markets.py with:
- GET /markets/assets per docs/API_CONTRACTS.md (supports asset_class and is_active query params).
- GET /markets/candles per docs/API_CONTRACTS.md (required asset_id, interval; optional start_time/end_time; returns 404 for unknown asset_id, 400 for invalid interval or start_time >= end_time, 422 for missing required params).
Register this router in app/main.py.
Write integration tests (using FastAPI's TestClient) covering: successful asset list fetch, successful candle fetch with a seeded test asset/candles, 404 for unknown asset, 400 for invalid interval and bad time range.
```

### Prompt 1.7 — Seed Script for BTC/ETH/SOL (+ one stock)
```
Implement scripts/seed_assets.py:
- Inserts (idempotently — skip if symbol+exchange already exists) BTCUSDT, ETHUSDT, and SOLUSDT as crypto assets on exchange="binance_us" IF Binance.US supports SOLUSDT (verify against the live Binance.US exchangeInfo endpoint at script run time; if SOLUSDT is unavailable on Binance.US, log a clear warning and skip it gracefully rather than failing the whole script — this is the "graceful handling when an exchange endpoint fails or a symbol is unsupported" requirement).
- For each inserted crypto asset, populate supports_fractional=true, and pull min_order_notional (MIN_NOTIONAL filter) and qty_step_size (LOT_SIZE step) from the same Binance.US exchangeInfo response rather than hard-coding them — these values differ per symbol and change over time, and are required for the risk engine's minimum-viable-position check per docs/SMALL_ACCOUNT_MODE.md section 4/5. If a filter is missing from the exchange response for a symbol, insert the asset with that field null and log a warning rather than guessing a value.
- Also inserts AAPL as a stock asset on exchange="alpaca" for later Alpaca integration testing, with supports_fractional=true (Alpaca supports fractional AAPL shares) — min_order_notional and qty_step_size can be left null for stocks in Phase 1 (Alpaca's fractional trading minimums are addressed when the paper execution engine is built).
- Prints a summary of what was inserted vs. skipped (already existed) vs. skipped (unsupported).
Run this script locally and confirm the assets table is populated as expected, including the Small Account Mode fields.
```

### Prompt 1.8 — Historical Backfill Script
```
Implement scripts/backfill_historical.py as a CLI script:
- Args: --symbol, --interval, --start-date, --end-date.
- Looks up the asset by symbol, calls app/services/data/binance_client.py's fetch_klines for the given range, and writes results via app/services/data/candle_writer.py.
- Handles and clearly reports partial failures (e.g., Binance.US returns data for part of the range but errors on a later page) — report what was successfully backfilled and what was not, rather than failing silently or losing partial progress. Already-written candles from earlier successful pages must remain committed even if a later page fails.
Run this for BTCUSDT at 1d interval for a 1-year range and confirm ~365 candle rows exist afterward (accounting for any gaps in exchange data).
```

### Prompt 1.9 — Scheduled Recent-Candle Ingestion Worker
```
Implement a worker entrypoint at app/services/data/worker_entrypoint.py:
- Runs on a schedule (every 5 minutes is fine for MVP — use a simple asyncio loop with sleep, or APScheduler if already a dependency) pulling the most recent candles (last ~2 hours at 1m interval, or configurable) for every active crypto asset via binance_client.py, writing via candle_writer.py.
- On per-asset failure (e.g., one symbol's request fails), log the error and continue to the next asset — one failing symbol must not stop ingestion for the others.
- Expose a simple in-memory "last successful ingestion timestamp" that app/api/routes/health.py can report in the GET /health response's last_ingestion_at field per docs/API_CONTRACTS.md (wire this via a small shared state module, e.g., app/services/data/ingestion_status.py, rather than tight coupling between the worker process and the API process if they run separately — for local dev where they may share a process, a simple shared variable is fine; note in a comment that a separate deployment will need a DB-backed or cache-backed status instead).
```

### Prompt 1.10 — Basic Market Chart Page
```
In apps/web, implement the /markets page per docs/FRONTEND_PAGE_SPECS.md's Markets section:
- lib/api/markets.ts: typed fetch functions for GET /markets/assets and GET /markets/candles against NEXT_PUBLIC_API_BASE_URL.
- components/domain/AssetList.tsx: fetches and renders assets with a search/filter input; clicking an asset selects it (local state or route param via /markets/[symbol]).
- components/charts/CandleChart.tsx: wraps TradingView Lightweight Charts, accepts a candles array prop, renders a candlestick series.
- app/markets/page.tsx: composes AssetList + IntervalSelector (button group for 1m/5m/15m/1h/1d) + CandleChart, re-fetching candles on asset or interval change.
- Implement the loading state (skeleton rows / spinner over chart area, not full-page reload) and both empty states (no assets at all vs. no candles for a valid asset/interval) and error states exactly as specified in docs/FRONTEND_PAGE_SPECS.md's Markets page section.
Manually verify: selecting BTCUSDT after running the seed + backfill scripts renders a real candlestick chart; switching intervals re-fetches correctly; selecting an asset with no backfilled data shows the correct empty state instead of an error.
```

---

### Phase 1 Completion Check

After Prompt 1.10, run through `VALIDATION_CHECKLIST.md`'s Phase 1 section in full — including the deliberate exchange-failure test — before starting Phase 3 work (backtesting) from `COPILOT_PROMPT_PACK.md`.
