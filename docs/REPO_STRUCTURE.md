# REPO_STRUCTURE.md

## OmniTrade Legacy Engine — Monorepo Structure

### 1. Top-Level Layout

```
omnitrade-legacy-engine/
├── apps/
│   ├── web/                    # Next.js frontend
│   └── api/                    # FastAPI backend
├── packages/
│   └── shared/                 # Shared types/constants/schemas
├── docs/                       # Architecture + implementation docs (this doc set)
├── scripts/                    # Setup, seed, maintenance scripts
├── infra/                      # Deployment, docker, environment templates
├── .github/
│   └── workflows/              # CI (lint, test, build) — added Phase 0/8
├── .gitignore
├── .env.example
├── package.json                 # workspace root (npm/pnpm workspaces for apps/web + packages/shared)
├── pyproject.toml               # optional root-level Python tooling config (ruff/black/mypy shared config)
└── README.md
```

### 2. `/apps/web` — Next.js Frontend

```
apps/web/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                 # redirects to /dashboard
│   ├── dashboard/
│   │   └── page.tsx
│   ├── markets/
│   │   ├── page.tsx
│   │   └── [symbol]/page.tsx
│   ├── strategy-lab/
│   │   └── page.tsx
│   ├── backtests/
│   │   ├── page.tsx
│   │   └── [id]/page.tsx
│   ├── signals/
│   │   └── page.tsx
│   ├── paper-trading/
│   │   └── page.tsx
│   ├── risk-monitor/
│   │   └── page.tsx
│   └── settings/
│       └── page.tsx
├── components/
│   ├── ui/                      # shared primitives (Button, Card, Badge, Modal, Table...)
│   ├── charts/                  # LightweightCandleChart, EquityCurveChart, DrawdownChart
│   ├── layout/                  # Sidebar, TopBar, PageShell
│   └── domain/                  # StrategyParamForm, SignalRow, TradeRow, RiskStatusCard...
├── lib/
│   ├── api/                     # typed API client functions, one file per resource
│   ├── hooks/                   # useAssets, useCandles, useBacktest, useSignals...
│   ├── types/                   # re-exports from packages/shared where applicable
│   └── utils/                   # formatting (currency, %, dates), constants
├── public/
├── styles/
│   └── globals.css
├── next.config.js
├── tailwind.config.ts
├── tsconfig.json
└── package.json
```

### 3. `/apps/api` — FastAPI Backend

```
apps/api/
├── app/
│   ├── main.py                  # FastAPI app instance, router registration, middleware
│   ├── config.py                 # settings via pydantic-settings, env var loading
│   ├── db/
│   │   ├── session.py            # SQLAlchemy engine/session
│   │   ├── base.py               # declarative base
│   │   └── migrations/           # Alembic env + versions/
│   ├── models/                   # SQLAlchemy ORM models (mirrors DATABASE_SCHEMA.md)
│   │   ├── asset.py
│   │   ├── candle.py
│   │   ├── strategy.py
│   │   ├── parameter_set.py
│   │   ├── backtest.py
│   │   ├── signal.py
│   │   ├── paper_account.py
│   │   ├── trade.py
│   │   ├── model_output.py
│   │   ├── risk_event.py
│   │   └── audit_log.py
│   ├── schemas/                  # Pydantic request/response schemas (mirrors API_CONTRACTS.md)
│   │   ├── asset.py
│   │   ├── candle.py
│   │   ├── strategy.py
│   │   ├── backtest.py
│   │   ├── signal.py
│   │   ├── paper.py
│   │   └── common.py             # pagination, error envelope
│   ├── services/
│   │   ├── data/                 # ingestion clients (binance_client.py, alpaca_client.py, yfinance_backfill.py)
│   │   ├── strategies/           # strategy modules (ma_crossover.py, rsi_mean_reversion.py, ...)
│   │   ├── backtesting/          # engine.py, metrics.py
│   │   ├── signals/              # signal generation orchestration loop logic
│   │   ├── risk/                 # risk_engine.py, rules/
│   │   ├── paper/                # alpaca_paper.py, internal_sim.py, account.py
│   │   └── ai/                   # regime_classifier.py, signal_scorer.py, allocator.py, explainer.py, post_trade_review.py
│   ├── api/
│   │   └── routes/               # one router module per resource (health.py, markets.py, backtests.py, strategies.py, signals.py, paper.py)
│   └── core/
│       ├── logging.py
│       ├── errors.py             # shared exception types + handlers
│       └── security.py           # auth/JWT verification against Supabase
├── tests/
│   ├── unit/
│   └── integration/
├── alembic.ini
├── pyproject.toml                 # Poetry or uv/pip-tools managed dependencies
└── Dockerfile
```

### 4. `/packages/shared`

```
packages/shared/
├── src/
│   ├── types/                    # TS types mirroring backend Pydantic schemas (hand-synced or codegen'd)
│   ├── constants/                # shared enums: asset classes, signal actions, strategy slugs
│   └── schema-links/             # optional: OpenAPI-generated client types
├── package.json
└── tsconfig.json
```

> Note: `packages/shared` starts minimal in Phase 0 (a handful of enums/constants). Full OpenAPI-based type generation from FastAPI's schema is a nice-to-have introduced once `API_CONTRACTS.md`'s endpoints stabilize — not required for Phase 0.

### 5. `/docs`

```
docs/
├── PROJECT_VISION.md
├── SYSTEM_ARCHITECTURE.md
├── DATA_SOURCES.md
├── DATABASE_SCHEMA.md
├── STRATEGY_ENGINE.md
├── AI_LAYER.md
├── RISK_ENGINE.md
├── UI_SPEC.md
├── COPILOT_PROMPT_PACK.md
├── MVP_BUILD_PLAN.md
├── REPO_STRUCTURE.md
├── ENVIRONMENT_SETUP.md
├── API_CONTRACTS.md
├── FRONTEND_PAGE_SPECS.md
├── BACKEND_MODULE_SPECS.md
├── COPILOT_PHASE_0_PROMPTS.md
├── COPILOT_PHASE_1_PROMPTS.md
├── VALIDATION_CHECKLIST.md
├── SECURITY_AND_SAFETY.md
└── HANDOFF_TO_COPILOT.md
```

### 6. `/scripts`

```
scripts/
├── setup.sh                      # one-shot local environment bootstrap
├── seed_assets.py                # seeds assets table (BTC/ETH/SOL, AAPL, etc.)
├── seed_strategies.py            # seeds strategies table (6 MVP strategies, inactive)
├── backfill_historical.py        # CLI historical candle backfill
├── reset_paper_account.py        # dev utility mirroring POST /paper/reset
└── check_env.py                  # validates required env vars are present before running
```

### 7. `/infra`

```
infra/
├── docker/
│   ├── docker-compose.yml         # local: postgres, api, web, worker
│   ├── api.Dockerfile
│   └── web.Dockerfile
├── env-templates/
│   ├── web.env.example
│   ├── api.env.example
│   └── worker.env.example
└── deploy/
    ├── vercel.json                # frontend deploy config
    ├── railway.json (or fly.toml) # backend/worker deploy config — pick one per DEPLOYMENT.md
    └── supabase/
        └── migration-notes.md
```

### 8. Naming & Ownership Conventions

- Backend Python: `snake_case` files/functions, `PascalCase` classes, one SQLAlchemy model per file under `app/models/`.
- Frontend TypeScript: `PascalCase` components, `camelCase` functions/hooks, one component per file under `components/`.
- Every backend `services/` submodule is independently unit-testable and has no direct dependency on `api/routes/` (routes call services, never the reverse).
- Every new page under `apps/web/app/` must have a matching entry added to `FRONTEND_PAGE_SPECS.md`.
- Every new endpoint under `apps/api/app/api/routes/` must have a matching entry added to `API_CONTRACTS.md` before or in the same PR as its implementation.
