# Pipeline Resilience

## Scope

This document describes the paper-only resilience controls added for orchestration, execution rejection handling, and database recovery.

## SELL Rejection Handling

Expected paper execution business rejections are handled as structured outcomes, not pipeline-fatal exceptions.

Outcome classes:
- `EXECUTED`
- `SKIPPED`
- `REJECTED`
- `FAILED`

Current structured rejection reasons include:
- `INSUFFICIENT_POSITION_QUANTITY`
- `INSUFFICIENT_PAPER_CASH`

A structured rejection:
- does not create a trade
- does not create a fill
- does not reduce position below zero
- does not reduce paper cash for an unfilled SELL rejection
- persists a blocked risk/execution evidence record
- writes audit evidence
- emits `PAPER_EXECUTION_REJECTED` into active Validation Run timelines
- allows the orchestration worker to continue processing remaining candidates in the same cycle

## Candidate Failure Boundary

Normal paper trading business-state failures are `REJECTED`.
True infrastructure or unexpected code faults are `FAILED`.
A candidate `FAILED` outcome must not abort the remaining cycle.

## Database Recovery

SQLAlchemy async engine defaults now use:
- `pool_pre_ping=True`
- bounded `pool_recycle`
- bounded `pool_size`
- bounded `max_overflow`
- bounded `pool_timeout`

## Read Retry Boundary

Automatic retry applies only to safe read paths.

The retry helper:
- detects stale/closed pooled-connection failures
- invalidates the broken connection
- rolls back the failed session
- disposes the engine pool once
- retries exactly one time with a fresh session
- returns a safe `503 service_unavailable` if the retryable read still fails

Writes are not automatically retried.

## Worker Recovery

Workers already create fresh sessions per cycle.
Additional protections now include:
- bounded reconnect backoff for retryable DB disconnects
- engine disposal before the next retryable cycle
- no automatic replay of partially committed write cycles
- cycle-local failure logging without forcing a full process exit

## Freshness Evidence

Operator freshness evidence is available through `GET /operations/freshness`.

The helper uses real schema columns:
- candles: `close_time`
- signals: `signal_time`
- decision records: `timestamp`
- trades: `executed_at`
- risk events: `created_at`

## Live Trading Isolation

These resilience changes do not enable live trading.

The live policy remains:
- `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`
- no real Coinbase order submission
- research remains paper-only
- resilience changes do not weaken existing live safety gates
