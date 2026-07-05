# OmniTrade Legacy Engine — Project Status

Last Updated: 2026-07-05

---

# Current Status

Project Stage:
Pre-MVP

Current Phase:
Phase 4 — Strategy Lab

Current Prompt:
Pending Phase 4 prompt creation

Overall Completion:
Approximately 30%

---

# Current Goal

Prepare Phase 4 (Strategy Lab) prompt and implementation plan after Phase 3 completion.

Do not begin Phase 4 (Strategy Lab) until:

- Phase 3 implementation is complete. (Complete)
- Phase 3 validation passes. (Complete)
- Documentation is updated.
- PROJECT_STATUS.md is updated.
- Code is committed.
- No known regressions remain.

Project philosophy:

Build one layer.

Validate it.

Commit it.

Only then move to the next layer.

Never skip phases.

---

# Phase Progress

## ✅ Phase 0 — Repository Scaffold

Status:
COMPLETE

Completed:

- Monorepo scaffold
- FastAPI backend
- Next.js frontend
- Shared package
- Docker
- Alembic
- Health endpoint
- CI
- Initial validation

Validated against:

- VALIDATION_CHECKLIST.md

---

## ✅ Phase 1 — Data Ingestion

Status:
COMPLETE

Completed:

- Assets and Candles schema
- Binance ingestion
- Historical backfill
- Markets API
- Scheduled ingestion worker
- Markets UI
- Candlestick chart rendering with real market data
- Phase 1 validation completed

Validated against:

- VALIDATION_CHECKLIST.md

---

## ✅ Phase 2 — Chart UI

Status:
COMPLETE

Completed:

- Markets page completed
- Asset selector
- Interval selector
- TradingView Lightweight Charts integration
- Responsive candlestick chart
- Crosshair
- Zoom and pan
- SMA overlay
- In-memory candle caching
- Loading state
- Empty state
- Error state
- Responsive resizing
- Frontend validation completed

Validated against:

- VALIDATION_CHECKLIST.md

---

## ✅ Phase 3 — Backtesting

Status:
COMPLETE

Completed:

- Backtesting database models and migrations
- Strategy interface and registry
- MA Crossover strategy and remaining MVP strategy/filter modules
- Event-driven backtesting engine
- Fill simulation and metrics engine (including fee drag and small-account warning support)
- Backtest persistence service
- Backtest API endpoints (`/backtests/run`, `/backtests`, `/backtests/{id}`, `/backtests/{id}/trades`)
- Backtests page UI with running/completed/failed/empty states
- Metadata support endpoint for backtests UI (`GET /parameter-sets`) while preserving documented `GET /strategies` contract
- Documented `GET /strategies` endpoint implemented for end-to-end manual validation
- Phase 3 validation completed (backend tests, frontend tests, manual backtest/API/UI validation)

---

## ⬜ Phase 4 — Strategy Lab

Status:
Not Started

---

## ⬜ Phase 5 — Paper Trading

Status:
Not Started

---

## ⬜ Phase 6 — AI Layer

Status:
Not Started

---

## ⬜ Phase 7 — Risk Engine

Status:
Not Started

---

## ⬜ Phase 8 — Deployment

Status:
Not Started

---

## ⬜ Future Phase

Decision Intelligence Engine

Includes:

- Decision Records
- Decision Snapshot
- Counterfactual Outcome Ledger
- Decision Quality Engine
- Decision Explorer
- Decision Timeline
- Decision Compare
- Confidence Analytics

---

# Important Architectural Decisions

The following have already been decided.

Do not revisit them without creating an ADR.

- FastAPI backend
- Next.js frontend
- Supabase/Postgres
- Four Core Engine architecture
- Small Account Mode
- Decision Intelligence Engine
- Counterfactual Outcome Ledger
- Decision Snapshot
- Decision Quality Engine
- Paper trading only
- Explainability first
- Decision quality over profit

---

# Current Priority

Begin Phase 4 prompt creation while maintaining architectural discipline.

Do not implement any Phase 5 or later systems until Phase 4 has been completed, validated, documented, and committed.

---

# Phase Validation Summary

Phase 0

- Complete
- Validated

Phase 1

- Complete
- Backend tests passing
- Frontend tests passing
- Production build verified

Phase 2

- Complete
- Markets UI manually verified
- Candlestick rendering verified
- Interval switching verified
- SMA overlay verified
- Frontend tests passing
- Production build verified

Phase 3

- Complete
- Backend tests passing (`pytest`)
- Frontend tests passing (`pnpm test`)
- Manual MA Crossover backtest run completed against real BTCUSDT candles
- Backtest persistence verified (`backtests`, `backtest_trades`, populated metrics)
- API verification completed (`GET /backtests`, `GET /backtests/{id}`, `GET /backtests/{id}/trades`, `GET /strategies`)
- Backtests UI states manually verified (running/completed/failed/empty)

Known developer environment issue:

Docker may leave root-owned .next artifacts causing local EACCES errors during lint/build.
This is a local development environment issue and is NOT considered a Phase 3 functional blocker.

---

# Upcoming Roadmap

After Phase 3:

## Phase 4

Strategy Lab

## Phase 5

Paper Trading

## Phase 6

AI Layer

## Phase 7

Risk Engine

## Phase 8

Deployment

## Future

Decision Intelligence Engine

---

# Definition of Done

A phase is considered complete only when:

- All Copilot prompts for the phase are complete.
- Validation checklist passes.
- Code is committed.
- Documentation is updated.
- PROJECT_STATUS.md is updated.
- No known regressions remain.

Only then may work begin on the next phase.

---

# Guiding Principles

Slow is smooth.

Smooth is fast.

Architecture first.

Quality over speed.

Decision quality over profit.