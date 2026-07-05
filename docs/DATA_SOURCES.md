# DATA_SOURCES.md

## OmniTrade Legacy Engine — Data Sources

### 1. Goals

- Start with free or low-cost data.
- Prefer sources with clear, permissive terms of service for research/paper trading.
- Avoid hard dependence on any single vendor.
- Treat all ingested data as potentially imperfect (gaps, delays, restatements) — the system must tolerate and log this, not assume perfection.

### 2. Candidate Data Sources

#### 2.1 Binance / Binance.US Public Market Data (Crypto) — **Primary crypto source**
- **Cost:** Free for public market data endpoints (klines/candles, order book snapshots, recent trades).
- **Coverage:** Wide range of crypto pairs, multiple intervals (1m up to 1M).
- **Rate limits:** Weight-based limits per IP (varies by endpoint; typically low hundreds to ~1200 weight/min on Binance.US-style limits). Must implement backoff and respect `Retry-After`/weight headers.
- **Latency:** Near real-time for recent candles; historical klines available via REST, no need for websockets in MVP (websockets are a Horizon 2 optimization).
- **Legal/ToS notes:** Binance.US has geographic and product-availability restrictions; confirm current terms before relying on it for anything beyond research/paper trading. Do not redistribute raw data commercially without checking ToS.
- **Use in system:** Primary source for crypto candles (ingestion workers + backtesting + paper trading fills).

#### 2.2 Alpaca Market Data + Paper Trading (Stocks) — **Primary stock source**
- **Cost:** Free tier available (IEX-based data feed) with a paid tier for SIP (full consolidated) data.
- **Coverage:** US equities and ETFs; supports paper trading accounts natively, which is a major advantage — no need to build a separate stock execution simulator.
- **Rate limits:** Generous for free tier but present; must batch/backoff.
- **Latency/quality caveat:** Free IEX feed is **not** the full consolidated tape — prices can differ slightly from SIP/consolidated data. This is acceptable for research and paper trading but must be disclosed in the UI (e.g., a small "data feed: IEX (delayed/partial)" label) and never described as "real-time NBBO."
- **Legal/ToS notes:** Standard brokerage-style ToS; paper trading is explicitly supported and intended for this kind of use. Confirm current Alpaca terms before any future live-trading integration.
- **Use in system:** Primary source for stock candles, and the execution venue for stock paper trading.

#### 2.3 yfinance — **Research/backfill only, never for live signals**
- **Cost:** Free (unofficial wrapper around Yahoo Finance endpoints).
- **Coverage:** Broad historical equity/ETF/index/some crypto data, long history.
- **Rate limits:** Unofficial/unpublished; prone to breaking changes since it's not an official API.
- **Legal/ToS notes:** Not an official, licensed data API — Yahoo's terms restrict commercial/redistribution use, and the endpoints yfinance relies on can change or be blocked without notice. Because of this fragility and ToS ambiguity, yfinance is designated **backfill/research-only**: useful for pulling long historical ranges to seed backtests, but must never be a dependency for live signal generation, live paper trading, or anything customer/user-facing beyond a labeled "historical research" context.
- **Use in system:** Optional one-off/scheduled backfill jobs for extending historical depth in backtests; results clearly tagged with `source = 'yfinance_backfill'` in the candles table.

#### 2.4 Other Options Considered (and why not primary for MVP)
| Source | Why not primary |
|---|---|
| Polygon.io | Good quality but paid tiers needed for meaningful history/rate limits; revisit for Horizon 2. |
| Alpha Vantage | Free tier very rate-limited (historically ~5 req/min, 500/day); too restrictive for active ingestion. |
| IEX Cloud | Service changes/deprecations have made long-term reliability uncertain; re-evaluate if needed. |
| CCXT (multi-exchange) | Useful abstraction layer if multi-exchange crypto support is added later; adds complexity not needed for MVP's single-exchange crypto scope. |

### 3. Recommended Starting Data Stack

- **Crypto candles (live + recent history):** Binance/Binance.US public REST klines endpoint.
- **Crypto historical backfill (deep history):** Binance klines endpoint (paginated), supplemented by yfinance only if Binance history doesn't reach far enough back for a given pair.
- **Stock candles + paper execution:** Alpaca (free/IEX tier to start).
- **Stock deep historical backfill:** Alpaca historical bars endpoint first; yfinance as a fallback for older ranges or symbols not well covered.

### 4. Rate Limit & Reliability Handling

- All ingestion calls go through a shared HTTP client wrapper that:
  - Implements exponential backoff with jitter on 429/5xx.
  - Tracks and logs current rate-limit budget where the API exposes it in headers.
  - Writes ingestion failures to `audit_log` with source, endpoint, and error detail — silent failures are not permitted.
- Ingestion jobs are idempotent: re-running a backfill for an already-covered range should not create duplicate candle rows (enforced via a unique constraint on `(asset_id, interval, open_time)`).

### 5. Delayed Data Concerns

- Alpaca's free tier (IEX) is **not** full consolidated market data — the UI must label the feed source and avoid implying institutional-grade real-time pricing.
- Binance public data is close to real-time but subject to normal network/API latency; not suitable for latency-sensitive strategies (which are explicitly out of scope — see `PROJECT_VISION.md` non-goals).
- All strategies and backtests must be data-source-aware: the AI layer's explanations and the risk engine's assumptions should account for the fact that this is not tick-level, co-located, or NBBO-guaranteed data.

### 6. Legal / Terms-of-Service Cautions

- This document is **not legal advice**. Before any live-trading use, or any redistribution/commercial use of vendor data, have the current ToS of each vendor reviewed.
- Do not scrape or use undocumented private endpoints from any exchange or broker — public/documented APIs only.
- yfinance in particular should be treated as legally and technically fragile; do not build critical-path features on top of it.
- Respect each vendor's rate limits as a contractual matter, not just a technical one — repeated violations can result in IP bans that break ingestion for the whole system.
