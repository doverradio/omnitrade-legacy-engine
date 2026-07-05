# COPILOT_PHASE_0_PROMPTS.md

## OmniTrade Legacy Engine — GitHub Copilot Prompts: Phase 0 Only

**Scope:** Repo scaffold, Next.js frontend init, FastAPI backend init, docs folder, health check, basic dashboard shell, Docker/dev setup, README. No ingestion, no strategies, no business logic.

**Prepend this to your first Copilot Chat message in this repo:**
> "Read docs/REPO_STRUCTURE.md, docs/ENVIRONMENT_SETUP.md, docs/BACKEND_MODULE_SPECS.md, and docs/SMALL_ACCOUNT_MODE.md before making changes. Follow the exact folder structure in REPO_STRUCTURE.md. Do not implement any strategy, AI, risk, or execution logic in this phase — scaffolding and the health check endpoint only. Note that Small Account Mode (starting balances as low as $25) is a core product requirement, not a later add-on — any placeholder/example data you create in this phase should reflect small-account-scale numbers, not large ones, so the team gets used to seeing the platform at its default scale from day one."

Run these prompts **in order**, one at a time, reviewing/committing after each.

---

### Prompt 0.1 — Root Monorepo Init
```
Initialize the root of this repo per docs/REPO_STRUCTURE.md section 1:
- Create /apps, /packages, /docs, /scripts, /infra top-level folders (docs already has architecture files — do not overwrite existing files in /docs).
- Create a root package.json configured as a pnpm workspace (or npm workspaces if pnpm is unavailable) referencing apps/web and packages/shared.
- Create a root .gitignore covering: node_modules, .venv, __pycache__, .env, .env.local, .DS_Store, dist/, build/, .next/, *.pyc.
- Create a root .env.example that lists every environment variable referenced in docs/ENVIRONMENT_SETUP.md section 6, with placeholder values only (no real keys).
- Create a root README.md with: project name/one-line description (from docs/PROJECT_VISION.md), links to the docs/ folder, and a "Quick Start" section that will be filled in fully after Prompt 0.6 (leave a TODO placeholder for now).
Do not create apps/web or apps/api contents yet — that's the next prompts.
```

### Prompt 0.2 — Next.js Frontend Init
```
Create apps/web as a Next.js 14+ project using the App Router, TypeScript, and Tailwind CSS, matching the folder structure in docs/REPO_STRUCTURE.md section 2 exactly:
- app/ with layout.tsx, page.tsx (redirect to /dashboard), and empty page.tsx files for dashboard, markets, markets/[symbol], strategy-lab, backtests, backtests/[id], signals, paper-trading, risk-monitor, settings — each with a minimal placeholder component that just renders the page title (e.g., "Dashboard — coming soon").
- components/ui, components/charts, components/layout as empty folders with a .gitkeep each (no components yet).
- components/domain: create placeholder (empty-shell, no logic) files for StartingBalanceInput.tsx and DollarAndPercent.tsx per docs/SMALL_ACCOUNT_MODE.md and docs/FRONTEND_PAGE_SPECS.md's cross-page requirements — these are shared components used across Backtests, Paper Trading, and Strategy Lab in later phases, so establish their file locations now even though they're not wired up yet.
- lib/api, lib/hooks, lib/types, lib/utils as empty folders with a .gitkeep each.
- A basic PageShell component in components/layout/PageShell.tsx with a left sidebar (links to all 8 pages) and a top bar placeholder — wire this into app/layout.tsx so every page renders inside it.
- Configure Tailwind with a simple dark-mode-friendly base theme (no elaborate design system yet — just working defaults).
- Add apps/web/package.json with dev/build/lint scripts.
Do not add any API calls, data fetching, or business components yet.
```

### Prompt 0.3 — FastAPI Backend Init
```
Create apps/api as a FastAPI project matching docs/REPO_STRUCTURE.md section 3 and docs/BACKEND_MODULE_SPECS.md exactly:
- app/main.py: FastAPI instance, CORS middleware allowing the frontend's local origin, router registration (only the health router exists so far).
- app/config.py: pydantic-settings based Settings class loading every variable listed in docs/ENVIRONMENT_SETUP.md section 6 for the API, with sensible defaults for local dev where safe (e.g., LOG_LEVEL=INFO) and required (no default) for secrets.
- app/db/session.py and app/db/base.py: async SQLAlchemy engine/session setup using DATABASE_URL from config.
- Empty (but present, with __init__.py) folders: app/models, app/schemas, app/services/data, app/services/strategies, app/services/backtesting, app/services/signals, app/services/risk, app/services/paper, app/services/ai.
- app/api/routes/health.py: implements GET /health per docs/API_CONTRACTS.md exactly, checking DB connectivity.
- app/core/logging.py, app/core/errors.py, app/core/security.py: per docs/BACKEND_MODULE_SPECS.md responsibilities. security.py can be a stub for now (function signature present, Supabase JWT verification implemented as a TODO with a clear comment) since no protected routes exist yet.
- apps/api/pyproject.toml (or requirements.txt) listing: fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic-settings, httpx, pytest, pytest-asyncio.
- apps/api/Dockerfile for the backend service.
Do not implement any models, schemas beyond health, or services beyond the DB connectivity check.
```

### Prompt 0.4 — Database Migration Baseline
```
Set up Alembic in apps/api per docs/REPO_STRUCTURE.md (app/db/migrations/):
- alembic.ini and env.py configured to read DATABASE_URL from app/config.py's Settings.
- Create an initial empty migration (no tables yet — this just proves the migration pipeline works end-to-end against a real Postgres/Supabase instance).
- Add a Makefile or npm/uv script shortcut (e.g., `make migrate` or documented command in README) to run `alembic upgrade head`.
Confirm this works against a local Postgres/Supabase instance per docs/ENVIRONMENT_SETUP.md section 5 before proceeding — do not add table models yet, that begins in Phase 1.
```

### Prompt 0.5 — Docker Compose for Local Dev
```
Create infra/docker/docker-compose.yml per docs/REPO_STRUCTURE.md section 7:
- postgres service (postgres:16, with a named volume, exposing 5432, default db/user/password matching docs/ENVIRONMENT_SETUP.md's local DATABASE_URL).
- api service building from apps/api using infra/docker/api.Dockerfile, depending on postgres, exposing 8000, mounting apps/api for hot reload in dev.
- web service building from apps/web using infra/docker/web.Dockerfile, exposing 3000, depending on api, mounting apps/web for hot reload in dev.
Create infra/docker/api.Dockerfile and infra/docker/web.Dockerfile if they don't already exist from prior prompts (reuse apps/api/Dockerfile if already created in Prompt 0.3 rather than duplicating).
Create infra/env-templates/api.env.example and infra/env-templates/web.env.example matching docs/ENVIRONMENT_SETUP.md section 6 exactly.
Verify `docker compose -f infra/docker/docker-compose.yml up` brings up all three services and GET http://localhost:8000/health returns 200.
```

### Prompt 0.6 — Basic Dashboard Shell + README Finalization
```
In apps/web, flesh out the /dashboard page only (per docs/FRONTEND_PAGE_SPECS.md's Dashboard layout section) with static/placeholder content — no live API calls yet:
- SummaryCardRow component with 4 placeholder cards using small-account-scale example numbers (e.g., "Paper Balance: $25.00", "Today's P&L: +$0.31 (+1.2%)", "Open Positions: 1", "Active Strategies: 1") clearly labeled "example data" — not large round numbers like $10,000, since Small Account Mode ($25 minimum) is the platform's default scale per docs/SMALL_ACCOUNT_MODE.md, and placeholder data should model that from the start.
- An empty-state EquityCurveChart placeholder (a components/charts/EquityCurveChart.tsx that accepts a `data` prop and renders "No data yet" when data is empty — call it with an empty array for now).
- An empty-state RecentActivityFeed placeholder ("No signals or trades yet — activity will appear here once strategies are active.").
Then finalize the root README.md "Quick Start" section with the exact commands from docs/ENVIRONMENT_SETUP.md sections 3-7, plus a link to docs/VALIDATION_CHECKLIST.md for Phase 0 verification steps.
```

---

### Phase 0 Completion Check

After Prompt 0.6, run through `VALIDATION_CHECKLIST.md`'s Phase 0 section before starting `COPILOT_PHASE_1_PROMPTS.md`. Do not begin Phase 1 prompts until every Phase 0 checklist item passes.
