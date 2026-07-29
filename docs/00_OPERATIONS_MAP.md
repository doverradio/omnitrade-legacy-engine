# 00_OPERATIONS_MAP.md — OmniTrade Operations Map (Version 1)

## Purpose and Usage

Operational index for OmniTrade: services, entry points, configuration sources, logs, external systems.

- **Consult this before broad repository searches.** A missing answer is a gap in this document, not a reason to skip it — file it under Known Gaps.
- Update this document in the same PR as any change to a critical operational component (see Maintenance Rule).
- This is a **map, not a manual** — it points to authoritative code/docs rather than re-explaining how a subsystem works internally.
- Every factual claim carries a verification tag. Do not add a claim without one, and do not state a **[PROPOSED]** or **[UNRESOLVED]** item as current fact.

---
## Git Repository

Default branch: master

All standard pull/push examples in this repository should use:

git pull origin master

git push origin master

Do not substitute `main` unless the repository default branch is intentionally changed.


---

## Verification Status Legend

| Tag | Meaning |
|---|---|
| **[REPO]** | Verified directly by reading files in this repository (code, scripts, docs, config templates). |
| **[PROD]** | Verified on the production host, either by direct inspection or explicitly confirmed by the operator. |
| **[INFERRED]** | Strongly implied by repository evidence (e.g. a Dockerfile `CMD`) but not independently confirmed against the running production process. |
| **[UNRESOLVED]** | Not established by repository or confirmed production inspection. Needs VPS access. Listed in Known Gaps. |
| **[PROPOSED]** | A discussed target/future state, **not approved or implemented**. |

Untagged prose is structural framing or an environment-independent fact (e.g. "the repository contains X").

---

## Repository Layout

All paths below are **[REPO]** — confirmed present in this checkout.

```text
omnitrade-legacy-engine/            # repository root
apps/api/                            # FastAPI application (primary backend)
apps/api/app/                        # application package
apps/api/app/main.py                 # FastAPI app factory / ASGI entry point
apps/api/app/config.py               # Settings (pydantic-settings) — the single Python-level config surface
apps/api/app/api/routes/             # HTTP route modules (asset_commissioning.py, capital_campaigns.py, autonomous_capital_mandates.py, live.py, risk.py, decisions.py, health.py, ...)
apps/api/app/services/               # domain/service layer (orchestration, canonical_campaign_binding.py, capital_campaign_domain/, asset_commissioning/, risk/, mandates/, decisions/, replay/, ai_coach/, exchange_connections/, data/)
apps/api/app/models/                 # SQLAlchemy ORM models
apps/api/app/schemas/                # Pydantic request/response schemas
apps/api/app/operator_cli/           # operator CLI (python -m app.operator_cli.main)
apps/api/app/db/migrations/          # Alembic migrations
apps/api/app/db/migrations/versions/ # individual migration files
apps/api/alembic.ini                 # Alembic configuration
apps/api/Dockerfile                  # containerized API build (dev-oriented; see Runtime Services)
apps/api/tests/                      # apps/api/tests/{unit,integration,api,services,support}/
apps/web/                            # Next.js frontend
apps/web/package.json                # frontend scripts (dev, build, start, lint, test)
docs/                                 # documentation (129 files at time of writing)
infra/docker/                        # Dockerfiles + docker-compose.yml (dev/local composition)
infra/env-templates/                 # api.env.example, web.env.example — variable-name templates, no secret values
scripts/                              # operational scripts (see Operational Commands)
scripts/activation_only_environment_selector.sh
scripts/activation_proof_watchdog.py
scripts/activation_proof_watchdog.sh
operator                              # thin wrapper: cd apps/api && PYTHONPATH=. exec python3 -m app.operator_cli.main "$@"
```

Notable service/domain modules referenced elsewhere in this document (all **[REPO]**):

```text
app/services/orchestration/continuous_pipeline_worker.py   # orchestration worker main loop
app/services/canonical_campaign_binding.py                  # canonical governing-campaign identity/transition logic
app/services/capital_campaign_domain/                        # capital campaign lifecycle (definitions, drafts, commissioned entry execution)
app/services/asset_commissioning/                             # asset commissioning pipeline (service.py)
app/services/mandates/                                        # autonomous capital mandate lifecycle
app/services/risk/risk_engine.py                              # risk evaluation
app/services/decisions/                                       # decision record + replay context/candidates
app/services/replay/                                           # replay engine
app/services/ai_coach/                                        # AI coach / decision review
app/services/exchange_connections/providers/                  # per-provider exchange connection clients (kraken_spot.py, coinbase_advanced.py)
app/services/data/kraken_client.py                             # Kraken market-data/spot client
app/services/data/binance_client.py                            # Binance US market-data client
app/services/paper/alpaca_paper.py                              # Alpaca paper-trading integration
```

---

## Runtime Services

### `omnitrade-orchestration.service`

| Field | Value | Status |
|---|---|---|
| Purpose | Continuous orchestration pipeline: strategy execution, canonical package progression (READY→AUTHORIZED→DRY_RUN_PASSED→ACTIVATED), autonomous claim/prepare/execute, provider interaction. | **[REPO]** |
| Entry point | `apps/api/app/services/orchestration/continuous_pipeline_worker.py` — `main()` → `run_forever()`. | **[REPO]** |
| Exact `ExecStart=` | Not established — no unit file checked into this repository. | **[UNRESOLVED]** |
| Config sources | `apps/api/.env`, `/etc/omnitrade/activation-only/current.env`, `.../omnitrade-orchestration.service.d/venue-commissioning.conf` — see Environment and Configuration Architecture. | **[PROD]** |
| Effective precedence | Not confirmed. | **[UNRESOLVED]** |
| Logs | `journalctl -u omnitrade-orchestration.service -f` | **[INFERRED]** |
| Restart | `sudo systemctl restart omnitrade-orchestration.service` | **[INFERRED]** |

### `omnitrade-api.service`

| Field | Value | Status |
|---|---|---|
| Purpose | Presumed to serve the FastAPI app (`app.main:app`) to the frontend and operator tooling. | **[INFERRED]** |
| Entry point | Only established ASGI target is `app.main:app`. `apps/api/Dockerfile` runs `uvicorn app.main:app --reload` (dev-oriented — `--reload` is not a production flag). Whether production uses this invocation, this Dockerfile, or runs outside a container is unconfirmed. | **[REPO]** Dockerfile fact; **[UNRESOLVED]** production use |
| Unit file / config sources | Not present in this repository. | **[UNRESOLVED]** |
| Logs | `journalctl -u omnitrade-api.service -f` (confirm unit name first) | **[UNRESOLVED]** |
| Restart | `sudo systemctl restart omnitrade-api.service` (same caveat) | **[UNRESOLVED]** |

Do not assume this mirrors the orchestration service's config sources — confirm with `systemctl cat omnitrade-api.service` before relying on it.

---

## Environment and Configuration Architecture

### Current State (Verified)

- **Local dev:** `apps/api/.env` (gitignored). Variable names templated in `infra/env-templates/api.env.example` / `web.env.example` — names only, no values. **[REPO]**
- **Pydantic fallback (applies everywhere, not just locally):** `app/config.py` sets `env_file=apps/api/.env` on the `Settings` class. Precedence: real process (OS) env wins over this file; the file wins over code defaults. Net effect — **any variable not set in the process's real environment silently falls back to whatever's on disk at `apps/api/.env`**, even with no systemd reference to it. **[REPO]**
- **Production** — `omnitrade-orchestration.service` currently loads three sources, in this order: **[PROD]**
  ```text
  apps/api/.env
  /etc/omnitrade/activation-only/current.env
  .../omnitrade-orchestration.service.d/venue-commissioning.conf
  ```
- Known overlap: `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED` and `LIVE_CRYPTO_PREPARATION_ENABLED` are each defined in more than one of the three sources. Do not assume duplicate definitions currently agree — verify with Operational Commands before trusting a value. **[PROD]**
- `scripts/activation_only_environment_selector.sh` owns `current.env`. Every state it can produce hardcodes `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`, and it auto-rolls-back if the live process ever disagrees — the one source here with a built-in, self-verifying safety guarantee. Companion tooling: `scripts/activation_proof_watchdog.{py,sh}`. **[REPO]**
- `venue-commissioning.conf` is named after `VENUE_COMMISSIONING_ENABLED` (`config.py`, default `false`), but unlike the activation-only file, no script or template in this repository produces it — its content is not repo-auditable. **[REPO]** for the field; **[UNRESOLVED]** for the drop-in's actual content
- `CONTROLLED_PROOF_MANDATE_ID` (`config.py`, default `None`) is a distinct, dedicated mandate identity Controlled Proof entry pins its BUY/SELL evaluations to — deliberately separate from `AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID` (ordinary production), so the two mandate purposes (`PRODUCTION` / `CONTROLLED_PROOF`, `autonomous_capital_mandates.purpose`) can never be conflated. Provisioned via `./operator controlled-proof-mandate-bootstrap` (`operator_cli/service.py::controlled_proof_mandate_bootstrap`), which resolves Controlled Proof's runtime scope automatically, runs the existing governed mandate lifecycle (`create_mandate` with `purpose="CONTROLLED_PROOF"` through `ACTIVATE`), and writes the resulting mandate id into the `.env` file `get_settings()` loads — no manual SQL, and a process restart (or the same process's next `get_settings()` call, since the write clears its cache) picks it up. Equivalently provisionable via `POST /autonomous-capital/mandates` with `purpose="CONTROLLED_PROOF"` directly. Readiness is checkable via `GET /api/v1/operator/controlled-proofs/mandate/readiness`. **[REPO]**

### Known Gaps (configuration-specific)

- Effective precedence across the three sources above — not confirmed on host (mechanics: see Systemd section).
- `venue-commissioning.conf`'s actual content, and whether a second, unlisted drop-in also exists (would change who wins).
- Full variable overlap and each key's winning value, verified against the live process — not assumed from file contents.

Full unresolved-items list: see Known Gaps section near the end of this document.

### Target Architecture (Proposed — not approved)

Two shapes discussed; neither is approved, and this document endorses neither:

- **Option A:** single `/etc/omnitrade/omnitrade.env`, one `EnvironmentFile=` per service.
- **Option B:** domain-separated `/etc/omnitrade/{api,orchestration,shared}.env`, one file per ownership boundary.

Either direction must: give every production-critical variable exactly one authoritative definition, stop treating repository `.env` as a live production fallback, and preserve (or deliberately replace) the activation-only selector's self-verifying guarantees. **[PROPOSED]** — do not implement without a separate, approved consolidation change.

---

## Single Sources of Truth

Do not claim a Single Source of Truth exists where it has not been proven — several rows below say so explicitly.

| Concern | Current authoritative location | Status | Notes / known conflicts |
|---|---|---|---|
| Production environment configuration | Not yet a single source — split across 3+ files, see above | **[PROD]** split confirmed; no SSOT yet | `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED`, `LIVE_CRYPTO_PREPARATION_ENABLED` overlap; precedence unresolved |
| Governing campaign identity | `capital_campaign_definitions` (immutable, versioned) + `capital_campaigns` (runtime pin) | **[REPO]** | Governing = runtime-pinned `definition_version` with status `READY`; resolved via `capital_campaign_domain.get_governing_campaign_definition` |
| Governing mandate identity | `autonomous_capital_mandate_versions`, resolved via `mandates.lifecycle.get_governing_authorized_mandate_version` | **[REPO]** | |
| Canonical campaign binding / transition | `app/services/canonical_campaign_binding.py` | **[REPO]** | Governs the DRAFT→READY transition and its readiness gate |
| Asset commissioning | `app/services/asset_commissioning/service.py` | **[REPO]** | 7-stage pipeline; see `docs/ASSET_COMMISSIONING_ARCHITECTURE.md` |
| Capital campaign domain | `app/services/capital_campaign_domain/` | **[REPO]** | Definitions, drafts, commissioned entry execution |
| API runtime | `omnitrade-api.service` | **[PROD]** name only; **[UNRESOLVED]** composition | |
| Worker runtime | `omnitrade-orchestration.service` | **[PROD]** name; **[UNRESOLVED]** exact `ExecStart=` | |
| Database schema | Alembic migrations, `apps/api/app/db/migrations/versions/` | **[REPO]** | |
| Database platform | `config.py` exposes Supabase-shaped fields alongside a generic `database_url` | **[INFERRED]** Supabase Postgres | Not independently confirmed for production |
| Decision Records | `app/models/decision_record.py`, `app/services/decisions/` | **[REPO]** | |
| Replay | `app/services/replay/`, `app/services/decisions/replay_context.py` / `replay_candidates.py` | **[REPO]** | |
| Provider execution (live order submission) | `app/services/capital_campaign_domain/commissioned_entry_execution.py` (`live_service.submit(...)`) | **[REPO]** | Reached only if `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED` is true at the gate in `continuous_pipeline_worker.py` |
| Commissioning execution scope | `app/services/asset_commissioning/service.py` + `app/services/orchestration/asset_roster.py` | **[REPO]** | Roster resolution for the worker |

---

## Systemd

```text
/etc/systemd/system/
```
Primary unit file location. **[UNRESOLVED]** full inventory beyond the two known services.

```text
/etc/systemd/system/*.service.d/
```
Drop-ins for the orchestration service:
```text
.../omnitrade-orchestration.service.d/venue-commissioning.conf          # [PROD] present, content unverified
.../omnitrade-orchestration.service.d/zz-activation-only-selector.conf  # [REPO] written by the selector script; presence on host unconfirmed
```

Ordering rules (systemd defaults, not specific to this host):

- Later `EnvironmentFile=`/drop-in wins per key; drop-ins apply after the main unit's own directives.
- Multiple drop-ins in one `.d/` apply in filename order — hence the `zz-` prefix convention (sorts, and wins, last).

Whether `venue-commissioning.conf` or an unlisted `zz-` drop-in has final precedence today is **[UNRESOLVED]** — confirm with `systemctl cat omnitrade-orchestration.service`.

---

## Operational Commands

Safe, read-only unless explicitly a restart. None of these dump full environments or print secret values.

Show effective unit composition (all drop-ins merged, in effective order):

```bash
systemctl cat omnitrade-orchestration.service
```

Show the ordered list of environment-file sources for a unit:

```bash
systemctl show omnitrade-orchestration.service --property=EnvironmentFiles
```

Check service status:

```bash
systemctl status omnitrade-orchestration.service
systemctl status omnitrade-api.service
```

Restart API (verify the unit name first — see Known Gaps):

```bash
sudo systemctl restart omnitrade-api.service
```

Restart orchestration worker:

```bash
sudo systemctl restart omnitrade-orchestration.service
```

View recent logs:

```bash
journalctl -u omnitrade-orchestration.service --since "-30m"
journalctl -u omnitrade-api.service --since "-30m"
```

Check selected non-secret runtime flags from the live process environment (never dump the whole `environ`):

```bash
PID="$(systemctl show omnitrade-orchestration.service --property=MainPID --value)"
sudo tr '\0' '\n' < /proc/${PID}/environ | grep -E '^(LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED|LIVE_CRYPTO_PREPARATION_ENABLED|AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED|AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID|AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_VERSION|AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_ID|AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_MANDATE_VERSION_ID|CONTROLLED_PROOF_MANDATE_ID|VENUE_COMMISSIONING_ENABLED|ASSET_DISCOVERY_MODE)='
```

Inspect activation-only selector state (self-verifying; the most trustworthy single check for submission/preparation/activation flags):

```bash
sudo ./scripts/activation_only_environment_selector.sh inspect
```

List variable **names** (never values) defined in a given env file, to compare sources without exposing secrets:

```bash
grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' /home/eric/omnitrade-legacy-engine/apps/api/.env | sed 's/=$//' | sort
```

Run the operator CLI (repo-verified entry point):

```bash
cd /home/eric/omnitrade-legacy-engine && ./operator automatic-mandate-activation-readiness --provider kraken_spot --environment production --product BTC-USD --json
```

Check a specific package's activation evidence:

```bash
cd /home/eric/omnitrade-legacy-engine && ./operator automatic-mandate-activation-proof --package-id <PACKAGE_UUID> --json
```

---

## Logs and Search Terms

| Term | Status | Where |
|---|---|---|
| `automatic_package_progression_result` | **[REPO]** | `continuous_pipeline_worker.py`, after every package-activation attempt. Its `live_submission_called`/`provider_submission_called` fields are hardcoded log literals, not real state — don't treat them as submission evidence. |
| `automatic_ready_package_created` | **[REPO]** | `continuous_pipeline_worker.py` — new canonical preview package created. |
| `automatic_ready_package_replayed` | **[REPO]** | `continuous_pipeline_worker.py` — idempotent replay of an existing package. |
| `provider_submission` | **[INFERRED]** | No single fixed log line; related terms (`provider_call_made`, `provider_order_id`) span `continuous_pipeline_worker.py` and `commissioned_entry_execution.py`. Search broadly. |
| `campaign_transition` | **[INFERRED]** | `canonical_campaign_binding.py` audit actions use names like `canonical_campaign_status_transition`; check `AuditLog.action` values. |
| `asset_commissioning` | **[REPO]** | Module/route prefix; internal log messages use stage-specific prefixes, not this literal string. |
| `reconciliation` | **[REPO]** | `app/models/live_reconciliation_event.py`; reconciliation-state fields on `AutonomousExecutionClaim`. |
| `risk_engine` | **[REPO]** | `app/services/risk/risk_engine.py`. |

**[INFERRED]** terms are starting search areas, not exact grep-able strings.

---

## External Systems

Do not invent URLs, account identifiers, or deployment specifics not established below.

| System | Known role | Status |
|---|---|---|
| VPS | Hosts `omnitrade-orchestration.service`, presumably `omnitrade-api.service`, and `/etc/omnitrade/` state. | **[PROD]** host + orchestration location confirmed; identity/access details not recorded here |
| GitHub | Canonical repository: `https://github.com/doverradio/omnitrade-legacy-engine` | **[REPO]** |
| Supabase | Auth/JWT, likely Postgres — `config.py` exposes `supabase_url`, `supabase_service_role_key`, `supabase_jwt_secret`; `SUPABASE_ANON_KEY` in local env. | **[REPO]** integration surface; **[INFERRED]** as the actual production database |
| Vercel | Presumed frontend hosting for `apps/web`. | **[UNRESOLVED]** — no `vercel.json` or deployment config in this repo |
| Cloudflare | Presumed DNS/edge routing. | **[UNRESOLVED]** — no repository evidence |
| Kraken | Live spot exchange integration. | **[REPO]** — `exchange_connections/providers/kraken_spot.py`, `data/kraken_client.py`; credentials via `KRAKEN_API_KEY`/`KRAKEN_API_SECRET`/`KRAKEN_OTP` |
| Coinbase | Exchange integration present in code. | **[REPO]** — `exchange_connections/providers/coinbase_advanced.py`; credentials via `OT_COINBASE_*` |
| Alpaca | Paper-trading integration only. | **[REPO]** — `services/paper/alpaca_paper.py`; no live exchange-connection provider found |
| Binance US | Market-data client only. | **[REPO]** — `services/data/binance_client.py`; no order-execution integration found |
| Interactive Brokers | No integration code found anywhere in the repository. | **[UNRESOLVED]** — future/aspirational only |

---

## Secrets

- Secret values must never appear in this document — only their source/origin.
- Known secret-bearing fields (names only; see `config.py` / `infra/env-templates/api.env.example`): `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `KRAKEN_OTP`, `EXCHANGE_CREDENTIALS_ENCRYPTION_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_ANON_KEY`, `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `OT_COINBASE_API_KEY_NAME`, `OT_COINBASE_PRIVATE_KEY`, `OT_COINBASE_PASSPHRASE`, `DATABASE_URL` (embeds DB credentials). **[REPO]**
- Plausible production origins: the environment files in this document, and/or a secrets manager or CI/CD store not documented here. **[UNRESOLVED]** which is authoritative for any given secret.
- Verify configuration with name-only commands (see Operational Commands) — never `cat` a full `.env` file or dump `/proc/<pid>/environ` in full into a shared channel, chat log, or this document.

---

## Known Gaps

Unresolved items requiring direct VPS inspection:

- [ ] Exact `omnitrade-api.service` unit composition (existence under that name, `EnvironmentFile=`/drop-ins, `ExecStart=`).
- [ ] Exact production API environment sources (entirely unresolved — see Runtime Services).
- [ ] `omnitrade-orchestration.service`'s actual `ExecStart=`; whether `omnitrade-api.service` really runs `uvicorn app.main:app`.
- [ ] Whether a second, unmentioned drop-in (e.g. `zz-activation-only-selector.conf`) currently coexists with `venue-commissioning.conf` — determines actual precedence.
- [ ] Complete effective environment overlap across all three orchestration config sources, verified against the live process.
- [ ] Content of `venue-commissioning.conf` (no repo-tracked script or template produces it).
- [ ] Approved target configuration architecture (Option A vs. B vs. other — currently undecided).
- [ ] Final fate of the production repository `.env`, given the Pydantic fallback behavior.
- [ ] Whether `apps/web` is actually deployed via Vercel, and whether Cloudflare sits in front of it.
- [ ] Whether the production database is actually Supabase-hosted Postgres, or Supabase is auth-only.

---

## Maintenance Rule

Any pull request that introduces or materially changes a production environment file, systemd unit or drop-in, infrastructure component, deployment dependency, operational entry point, or other critical operational artifact **must update this document in the same pull request.** A change that resolves a Known Gap or otherwise alters a claim here should update the relevant verification tag, not just the underlying fact.
