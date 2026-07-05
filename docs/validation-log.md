# Validation Log

## Phase 0 Validation (2026-07-04)

### Commands Run

```bash
docker compose -f infra/docker/docker-compose.yml up -d
cd apps/api && source .venv/bin/activate && export SUPABASE_SERVICE_ROLE_KEY=dummy SUPABASE_JWT_SECRET=dummy ALPACA_API_KEY_ID=dummy ALPACA_API_SECRET_KEY=dummy DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/omnitrade' && alembic upgrade head
cd apps/api && source .venv/bin/activate && pytest
cd apps/web && pnpm lint && pnpm build
```

### Checklist Results

- PASS: `docker compose -f infra/docker/docker-compose.yml up -d` starts postgres/api/web.
- PASS: `alembic upgrade head` applies revision `20260704_0001`.
- FAIL: `apps/api` test suite currently has no tests (`collected 0 items`, pytest exits non-zero).
- PASS: `apps/web` lint passes with zero errors.
- PASS: `apps/web` build completes successfully.
- PASS: `http://localhost:3000/dashboard` loads with PageShell + 4 summary cards, `No data yet`, and recent activity empty state.
- PASS: `/markets`, `/strategy-lab`, `/backtests`, `/signals`, `/paper-trading`, `/risk-monitor`, `/settings` load and show "coming soon" placeholders.
- PASS: `GET http://localhost:8000/health` returns `200` with `{"status":"ok","db":"connected",...}`.
- PASS: stopping postgres and re-checking `/health` returns `503` with `{"status":"degraded","db":"disconnected",...}`.
- PASS: spot check for committed secrets found placeholder/template values only.
- PASS: root README Quick Start now includes commands from environment setup sections 3-7 and links to Phase 0 checklist.

### Notes

- Phase 0 is not fully complete due missing backend tests. Add at least one placeholder test in `apps/api/tests` so `pytest` can pass as required by the checklist.
- During verification, two scaffold defects were corrected:
  - `apps/web/tsconfig.json` `ignoreDeprecations` was changed from `6.0` to `5.0` so `next build` can run.
  - Compose/web cache state required cleanup of `.next` ownership when switching between container dev server and host build.
