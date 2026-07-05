# OmniTrade Legacy Engine — Project Status

Last Updated: 2026-07-05

---

# Current Status

Project Stage:
Pre-MVP

Current Phase:
Phase 3 — Backtesting

Current Prompt:
Pending Phase 3 prompt creation/approval

Overall Completion:
Approximately 20%

---

# Current Goal

Complete Phase 3 (Backtesting) according to the documented architecture.

Do not begin Phase 4 (Strategy Lab) until:

- Phase 3 implementation is complete.
- Phase 3 validation passes.
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

## 🟨 Phase 3 — Backtesting

Status:
IN PROGRESS

Current Focus:

- Backtesting engine
- Strategy execution framework
- Historical simulation
- Performance metrics
- Validation planning

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

Complete Phase 3 while maintaining architectural discipline.

Do not implement any Phase 4 or later systems until Phase 3 has been completed, validated, documented, and committed.

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

Known developer environment issue:

Docker may leave root-owned .next artifacts causing local EACCES errors during lint/build.
This is a local development environment issue and is NOT considered a Phase 2 functional blocker.

---

# Upcoming Roadmap

After Phase 2:

## Phase 3

Backtesting

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