# OmniTrade Legacy Engine — Project Status

Last Updated: 2026-07-05

---

# Current Status

Project Stage:
Pre-MVP

Current Phase:
Phase 2 — Chart UI

Current Prompt:
Pending Phase 2 prompt creation/approval

Overall Completion:
Approximately 15%

---

# Current Goal

Finish Phase 1 completely before beginning any work on:

- Strategy Engine
- AI Layer implementation
- Risk Engine implementation
- Decision Intelligence Engine implementation
- Paper Trading
- Deployment

The project philosophy is:

Build one layer.
Validate it.
Commit it.
Only then move to the next layer.

No skipping phases.

---

# Phase Progress

✅ Phase 0 — Repository Scaffold
Status: COMPLETE

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

VALIDATION_CHECKLIST.md

---

🟨 Phase 1 — Data Ingestion
Status: COMPLETE

Completed:

- Assets and Candles schema
- Binance ingestion
- Historical backfill
- Markets API
- Scheduled ingestion worker
- Markets UI
- Candlestick chart rendering with real market data
- Phase 1 validation completed

---

⬜ Phase 2

Current Phase

Chart UI

---

⬜ Phase 3

Not Started

Backtesting

---

⬜ Phase 4

Not Started

Strategy Lab

---

⬜ Phase 5

Not Started

Paper Trading

---

⬜ Phase 6

Not Started

AI Layer

---

⬜ Phase 7

Not Started

Risk Engine

---

⬜ Phase 8

Not Started

Deployment

---

⬜ Future Phase

Decision Intelligence Engine

Includes:

Decision Records

Decision Snapshot

Counterfactual Outcome Ledger

Decision Quality Engine

Decision Explorer

Decision Timeline

Decision Compare

Confidence Analytics

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

Begin Phase 2 while maintaining architectural discipline. Do not implement Phase 3 or later systems early.

---

# Known Future Work

After Phase 1:

Phase 2
Charts

Phase 3
Backtesting

Phase 4
Strategy Lab

Phase 5
Paper Trading

Phase 6
AI Layer

Phase 7
Risk Engine

Phase 8
Deployment

Future

Decision Intelligence Engine

---

# Definition of Done

A phase is only complete when:

- All prompts are complete
- Validation checklist passes
- Code is committed
- Documentation updated
- No known regressions

Only then may the next phase begin.

---

# Guiding Principle

Slow is smooth.

Smooth is fast.

Architecture first.

Quality over speed.

Decision quality over profits.