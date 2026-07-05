# ADR-0006: FastAPI Backend

## Status
Accepted

## Context

OmniTrade Legacy Engine needed a single backend to own the strategy engine, backtesting engine, risk engine, and AI layer orchestration, alongside a Next.js/TypeScript frontend (`SYSTEM_ARCHITECTURE.md`). Two realistic options existed for where backend logic would live: Next.js API routes (keeping the whole stack in one language), or a separate Python backend. This choice is foundational and costly to reverse once strategy code, backtesting logic, and AI models are built against it, so it needed to be settled early and explicitly, rather than accreted piecemeal as each new backend capability was added.

## Decision

The backend is **FastAPI (Python)**, not Next.js API routes. Reasoning, as originally recorded in `SYSTEM_ARCHITECTURE.md` §2.2:

- The backtesting engine, strategy math (pandas/numpy/ta-lib-style indicators), and the AI/ML layer are far more natural in Python than in a Node/TypeScript API route.
- Python's mature quant ecosystem (pandas, numpy, scipy, scikit-learn) would otherwise need to be reimplemented or shelled out to from Node, adding complexity and a second runtime boundary for no real benefit.
- Keeping one backend language avoids splitting business logic across two runtimes (Node for "simple" routes, Python for "heavy" jobs), which becomes an audit and maintenance liability for a system that prizes explainability (`PROJECT_VISION.md` §4) — every strategy/risk/AI code path should be traceable in one place, one language.
- FastAPI provides async I/O (useful for concurrent exchange API calls), automatic OpenAPI schema generation (useful for a typed frontend client), and Pydantic validation (useful for strict input validation on every trade/signal endpoint) — all directly useful to this platform's specific needs.

The backend owns all writes to Postgres; the frontend never writes directly to the database, and talks to the backend exclusively through this FastAPI layer's typed REST API.

## Alternatives Considered

- **Next.js API routes for the entire backend**, keeping the stack single-language (TypeScript). Rejected because it would require either reimplementing a quant/data-science ecosystem in the Node/TypeScript ecosystem (immature relative to Python's for this purpose) or introducing a second backend runtime anyway for the heavy computational pieces — the worst of both options.
- **A hybrid split** — Next.js API routes for simple CRUD-style endpoints, a separate Python service for strategy/backtesting/AI logic. Rejected because it splits business logic across two runtimes and two codebases for what is meant to be a single, auditable decision pipeline (strategy → AI → risk → execution) — this directly conflicts with the explainability principle in `PROJECT_VISION.md` §4, since tracing a decision would require crossing a language/service boundary.
- **A different Python framework** (e.g., Flask, Django). Rejected in favor of FastAPI specifically for its async support, automatic schema generation, and Pydantic-based validation, all of which map directly onto this platform's needs (concurrent exchange calls, a typed frontend client, strict endpoint validation).

## Consequences

- All strategy, risk, and AI logic lives in one language and one service, simplifying auditability and making the request-flow diagrams in `SYSTEM_ARCHITECTURE.md` accurate representations of the actual code structure, not an idealization.
- The frontend and backend are permanently split across two languages (TypeScript/Next.js and Python/FastAPI), requiring contributors to be comfortable in both — an accepted cost given the alternative would compromise the backend's suitability for its actual workload.
- `apps/api`'s module layout (`BACKEND_MODULE_SPECS.md`, `REPO_STRUCTURE.md`) is built around this choice; reversing this decision later would require restructuring the entire backend, not just swapping a library.
