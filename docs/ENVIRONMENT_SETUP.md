# ENVIRONMENT_SETUP.md

## OmniTrade Legacy Engine — Local Environment Setup (From Zero)

### 1. Required Tools

| Tool | Minimum Version | Purpose |
|---|---|---|
| Node.js | 20.x LTS | Frontend (Next.js), workspace tooling |
| npm or pnpm | npm 10+ / pnpm 9+ | Frontend package management (pnpm recommended for workspaces) |
| Python | 3.11+ | Backend (FastAPI), workers |
| uv or Poetry | latest | Python dependency + virtual environment management (uv recommended for speed) |
| Docker + Docker Compose | Docker 24+ | Local Postgres, optional full-stack local run |
| Git | 2.40+ | Version control |
| Supabase CLI | latest | Local Supabase project management, migrations |

Optional but recommended:
- **VS Code** with the Python, ESLint, Prettier, and Tailwind CSS IntelliSense extensions.
- **GitHub Copilot** (Chat + inline) — this project's implementation workflow assumes it.

### 2. Clone & Initial Layout

```bash
git clone <repo-url> omnitrade-legacy-engine
cd omnitrade-legacy-engine
```

Confirm the structure matches `REPO_STRUCTURE.md` before proceeding.

### 3. Backend Setup (`apps/api`)

```bash
cd apps/api

# Create and activate a virtual environment
uv venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows

# Install dependencies
uv pip install -r requirements.txt
# (or, if using Poetry: poetry install)

# Copy environment template
cp ../../infra/env-templates/api.env.example .env

# Run database migrations (after Supabase/Postgres is running — see step 5)
alembic upgrade head

# Seed reference data
python ../../scripts/seed_assets.py
python ../../scripts/seed_strategies.py
```

### 4. Frontend Setup (`apps/web`)

```bash
cd apps/web

# Install dependencies (from repo root if using workspaces, otherwise here)
pnpm install

# Copy environment template
cp ../../infra/env-templates/web.env.example .env.local
```

### 5. Supabase / Postgres Setup

**Option A — Local Supabase (recommended for full parity):**
```bash
supabase init          # only if not already initialized at repo root
supabase start          # spins up local Postgres, Auth, Studio via Docker
```
This prints local connection details (DB URL, anon key, service role key) — copy these into `apps/api/.env` and `apps/web/.env.local` per the variable names in section 6.

**Option B — Hosted Supabase dev project:**
- Create a free-tier project at supabase.com.
- Copy the project URL, anon key, and service role key from Project Settings → API into the env files.
- Run `alembic upgrade head` (or `supabase db push` if using Supabase-native migrations) against the hosted project.

**Option C — Plain Docker Postgres (no Supabase features needed yet):**
```bash
docker compose -f infra/docker/docker-compose.yml up -d postgres
```
Use this only if Auth/RLS aren't needed yet (early Phase 0/1 development); switch to Option A before implementing user-owned data (paper accounts) per `DATABASE_SCHEMA.md` §4.

### 6. Environment Variables

**`apps/api/.env`:**
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade
SUPABASE_URL=http://localhost:54321
SUPABASE_SERVICE_ROLE_KEY=<from supabase start output>
SUPABASE_JWT_SECRET=<from supabase start output>

BINANCE_US_API_BASE=https://api.binance.us
ALPACA_API_KEY_ID=<your Alpaca paper trading key id>
ALPACA_API_SECRET_KEY=<your Alpaca paper trading secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets

ENVIRONMENT=local
LOG_LEVEL=INFO
GLOBAL_KILL_SWITCH_DEFAULT=false
```

**`apps/web/.env.local`:**
```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=http://localhost:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=<from supabase start output>
NEXT_PUBLIC_ENVIRONMENT=local
```

> Every variable above must also exist (with placeholder values) in `infra/env-templates/*.env.example`. Never commit a populated `.env` or `.env.local` — see `SECURITY_AND_SAFETY.md`.

### 7. Running Locally

**Full stack via Docker Compose (simplest):**
```bash
docker compose -f infra/docker/docker-compose.yml up
```
This starts Postgres, the FastAPI backend (`localhost:8000`), the Next.js frontend (`localhost:3000`), and the worker service.

**Or run services individually (better for active development):**

Terminal 1 — backend:
```bash
cd apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — frontend:
```bash
cd apps/web
pnpm dev
```

Terminal 3 — worker (once Phase 1+ ingestion jobs exist):
```bash
cd apps/api
source .venv/bin/activate
python -m app.services.data.worker_entrypoint
```

### 8. Verifying the Setup

1. Visit `http://localhost:8000/health` → expect `{"status": "ok", "db": "connected"}`.
2. Visit `http://localhost:3000/dashboard` → page loads without console errors (data will be empty until Phase 1 ingestion runs).
3. Run backend tests: `cd apps/api && pytest`.
4. Run frontend lint/build: `cd apps/web && pnpm lint && pnpm build`.

If all four succeed, proceed to `VALIDATION_CHECKLIST.md` Phase 0 checklist before starting implementation work.

### 9. Common Setup Issues

| Symptom | Likely Cause | Fix |
|---|---|---|
| `alembic upgrade head` fails to connect | Postgres/Supabase not running yet | Run `supabase start` or `docker compose up postgres` first |
| Frontend can't reach backend (CORS error) | `NEXT_PUBLIC_API_BASE_URL` mismatch or CORS middleware not configured | Confirm `.env.local` matches backend port; check `app/main.py` CORS config |
| Alpaca client errors on startup | Missing/invalid paper trading API keys | Confirm keys are paper (not live) keys from the Alpaca dashboard |
| Binance.US requests failing/blocked | Regional restriction or rate limiting | Confirm network access; check `DATA_SOURCES.md` §2.1 rate limit notes |
