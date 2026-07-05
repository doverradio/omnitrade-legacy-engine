# OmniTrade Legacy Engine

Web-based, AI-assisted trading research and paper-trading platform.

## Documentation

- [Project Vision](docs/PROJECT_VISION.md)
- [System Architecture](docs/SYSTEM_ARCHITECTURE.md)
- [Repository Structure](docs/REPO_STRUCTURE.md)
- [Environment Setup](docs/ENVIRONMENT_SETUP.md)
- [API Contracts](docs/API_CONTRACTS.md)
- [Security and Safety Rules](docs/SECURITY_AND_SAFETY.md)
- [All docs](docs/)

## Quick Start

### 1) Backend Setup (`apps/api`)

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

# Run database migrations (after Supabase/Postgres is running — see step 3)
alembic upgrade head

# Seed reference data
python ../../scripts/seed_assets.py
python ../../scripts/seed_strategies.py
```

### 2) Frontend Setup (`apps/web`)

```bash
cd apps/web

# Install dependencies (from repo root if using workspaces, otherwise here)
pnpm install

# Copy environment template
cp ../../infra/env-templates/web.env.example .env.local
```

### 3) Supabase / Postgres Setup

Option A — Local Supabase (recommended for full parity):

```bash
supabase init          # only if not already initialized at repo root
supabase start          # spins up local Postgres, Auth, Studio via Docker
```

Option C — Plain Docker Postgres (no Supabase features needed yet):

```bash
docker compose -f infra/docker/docker-compose.yml up -d postgres
```

### 4) Environment Variables

Use the templates:

- `infra/env-templates/api.env.example` for `apps/api/.env`
- `infra/env-templates/web.env.example` for `apps/web/.env.local`

### 5) Running Locally

Full stack via Docker Compose:

```bash
docker compose -f infra/docker/docker-compose.yml up
```

Or run services individually:

Backend:

```bash
cd apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd apps/web
pnpm dev
```

Worker (once Phase 1+ ingestion jobs exist):

```bash
cd apps/api
source .venv/bin/activate
python -m app.services.data.worker_entrypoint
```

### 6) Phase 0 Validation

Run the Phase 0 checklist in [docs/VALIDATION_CHECKLIST.md](docs/VALIDATION_CHECKLIST.md).
