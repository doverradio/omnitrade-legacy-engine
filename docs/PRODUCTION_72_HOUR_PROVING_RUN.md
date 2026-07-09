# Production 72-Hour Proving Run (Paper Mode)

## Objective

Validate that the deployed system can run continuously for 72 hours in paper mode while producing durable evidence and useful dashboard metrics.

Scope is audit-first and no-drift:

- No live trading
- No production execution changes unless a paper-mode safety issue is identified
- No new architecture
- No AI expansion

## Audit Result Summary

This repository is suitable for a 72-hour paper proving run with one key caveat:

- Trading/evidence pipeline data is database-backed and durable across restart.
- Research campaign/laboratory/evolution memory is currently process-memory only and will reset on API restart.

No critical paper-mode safety code change is required for this audit.

## 1) Restart Durability Matrix

### Durable Across API/Orchestration Restart

- candles: durable
  - Evidence: SQLAlchemy model table `candles`.
- signals: durable
  - Evidence: SQLAlchemy model table `signals`.
- trades (paper): durable
  - Evidence: SQLAlchemy model table `trades`.
- risk events: durable
  - Evidence: SQLAlchemy model table `risk_events`.
- decision records: durable (append-only)
  - Evidence: SQLAlchemy model table `decision_records` with update/delete guard.
- decision packages: durable as reconstructible artifacts, not as a persisted package table
  - Evidence: package is built on demand from persisted decision tables by `DecisionPackageBuilder`.
- strategy metrics: durable-derived
  - Evidence: dashboard health/scoreboard/tournament metrics are computed from persisted `signals`, `trades`, `decision_records`, `strategies` on each request.

### In-Memory Only (Lost On API Process Restart)

- research memory: in-memory singleton (`ResearchMemory`) in registry
- laboratory runs/status: in-memory singleton (`ResearchLaboratory`) and memory callback storage
- campaigns: in-memory singleton (`ResearchCampaignEngine`)
- evolved candidates: persisted only into in-memory research memory; evolution engine also keeps in-memory descendants
- evolution analytics: recomputed from in-memory research memory providers

### Per Requested Item

- candles: survives restart
- signals: survives restart
- trades: survives restart
- risk events: survives restart
- decision records: survives restart
- decision packages: survives restart as reconstructible read model (not separately persisted package rows)
- strategy metrics: survives restart as durable-derived metrics from DB state
- research memory: does not survive restart
- laboratory runs: do not survive restart
- campaigns: do not survive restart
- evolved candidates: do not survive restart in current implementation
- evolution analytics: does not survive restart (depends on in-memory research memory)

## 2) In-Memory Only Inventory

Current transient stores:

- `get_research_memory()` singleton state
- `get_research_laboratory()` singleton status/last run
- `get_research_campaign_engine()` singleton campaign list/state
- `get_evolution_engine()` in-memory descendant cache
- OpenAI research adapter singleton status and generation cache behavior

Operational implication:

- A 72-hour run can still validate paper trading durability and dashboard stability for DB-backed panels.
- Research campaign/lab/evolution panels should be treated as transient telemetry unless persisted in a future phase.

## 3) Evidence Production For Both Enabled Strategies

Target strategies:

- MA Crossover (`ma_crossover`)
- RSI Mean Reversion (`rsi_mean_reversion`)

Code audit confirms both strategy implementations are registered and executable by orchestration worker when active.

Runtime verification status in this environment:

- Not directly verifiable here because local API/service runtime is not active in this session.

Required runtime checks on deployment host are included in the command set and checklist below.

## 4) Paper Trade Continuity Without Manual Intervention

Code-level continuity assessment:

- Orchestration worker runs continuous loop (`while True`) with configured poll interval.
- Cycle-level exceptions are logged and loop continues.
- Signals are persisted and then execution orchestration is attempted for actionable buy/sell signals.
- DB commit occurs per processed signal path.

Conclusion:

- If orchestration service process remains alive (or is correctly restarted by service manager), paper trading should continue without manual intervention.

Dependency:

- Correct host-level service restart policy for `omnitrade-orchestration` is required to survive process crashes/reboots.

## 5) Dashboard Durability Classification

### Durable/DB-Backed Panels (Recommended For Proving Metrics)

- Tournament
- Decision Intelligence
- Capital Allocation
- Strategy Health
- Replay/evaluation surfaces built from persisted decisions/signals/trades

These endpoints query persisted tables each request.

### Transient Panels (Research Memory Backed)

- Research Laboratory
- Research Memory
- Evolution
- Evolution Analytics
- Research Campaigns

These currently depend on in-memory research services and do not survive API restart.

## 6) systemd Restart Policies (Required Verification)

Direct verification could not be executed in this environment because systemd is not available in this session.

Run on deployment host:

```bash
systemctl is-active omnitrade-api omnitrade-orchestration
systemctl show omnitrade-api omnitrade-orchestration \
  -p Id -p ActiveState -p SubState -p UnitFileState \
  -p Restart -p RestartUSec -p StartLimitBurst -p StartLimitIntervalUSec
```

Expected for proving run:

- `ActiveState=active`
- `Restart=always` or `Restart=on-failure` (with acceptable limits)
- Non-zero restart delay (avoid tight crash loops)
- Units enabled for host reboot continuity

## 7) Recurring Error Audit

Direct `journalctl` inspection could not be executed in this environment because systemd is unavailable.

Run on deployment host:

```bash
journalctl -u omnitrade-api -n 300 --no-pager
journalctl -u omnitrade-orchestration -n 300 --no-pager
journalctl -u omnitrade-api --since '24 hours ago' --no-pager | grep -Ei 'error|exception|traceback|failed'
journalctl -u omnitrade-orchestration --since '24 hours ago' --no-pager | grep -Ei 'error|exception|traceback|failed'
```

Pass criterion:

- No repeating crash signatures
- No persistent DB connectivity failures
- No continuous risk/execution exceptions for normal paper flow

## 8) Daily Verification Command Set

Use this command set at start and every 12 hours.

### Git and Schema State

```bash
cd /home/eric/omnitrade-legacy-engine
git fetch --all --prune
git status -b --porcelain
cd apps/api
alembic current
alembic heads
```

### Service and API Availability

```bash
systemctl is-active omnitrade-api omnitrade-orchestration
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/paper/account
curl -fsS 'http://127.0.0.1:8000/paper/trades?account_id=<ACCOUNT_UUID>&limit=5'
curl -fsS http://127.0.0.1:8000/arena/strategy-health
curl -fsS http://127.0.0.1:8000/arena/tournament
```

### Durable Evidence Growth Checks (PostgreSQL)

```bash
psql "$DATABASE_URL" -c "select count(*) as candles from candles;"
psql "$DATABASE_URL" -c "select count(*) as signals from signals;"
psql "$DATABASE_URL" -c "select count(*) as trades from trades where is_paper = true;"
psql "$DATABASE_URL" -c "select count(*) as risk_events from risk_events;"
psql "$DATABASE_URL" -c "select count(*) as decision_records from decision_records;"
```

### Strategy-Specific Evidence Checks

```bash
psql "$DATABASE_URL" -c "
select st.name, count(*) as signals
from signals s
join strategies st on st.id = s.strategy_id
where st.slug in ('ma_crossover','rsi_mean_reversion')
group by st.name
order by st.name;
"

psql "$DATABASE_URL" -c "
select st.name, count(*) as paper_trades
from trades t
join signals s on s.id = t.signal_id
join strategies st on st.id = s.strategy_id
where t.is_paper = true
and st.slug in ('ma_crossover','rsi_mean_reversion')
group by st.name
order by st.name;
"
```

### Log Health Checks

```bash
journalctl -u omnitrade-api --since '12 hours ago' --no-pager | tail -n 200
journalctl -u omnitrade-orchestration --since '12 hours ago' --no-pager | tail -n 200
```

## 72-Hour Proving Run Checklist

### Before Start

- [ ] `git pull` / repo clean state verified
- [ ] migrations current (`alembic current`, `alembic heads`)
- [ ] services active (`omnitrade-api`, `omnitrade-orchestration`)
- [ ] dashboard loads
- [ ] paper endpoints return JSON

### Every 12 Hours

- [ ] candle count increasing
- [ ] signal count increasing
- [ ] decision record count increasing
- [ ] paper trade count checked
- [ ] strategy health checked
- [ ] logs checked for recurring errors

### At 72 Hours

- [ ] summarize total candles
- [ ] summarize signals by strategy (MA/RSI)
- [ ] summarize trades by strategy (MA/RSI)
- [ ] summarize paper PnL/equity
- [ ] summarize campaign/lab/evolution activity (noting transient scope)
- [ ] identify blockers before longer unattended run

## 72-Hour Final Summary Template

- Run window: `<start>` to `<end>`
- Service uptime notes: `<api/orchestration>`
- Total candles: `<count>`
- Signals by strategy:
  - MA Crossover: `<count>`
  - RSI Mean Reversion: `<count>`
- Paper trades by strategy:
  - MA Crossover: `<count>`
  - RSI Mean Reversion: `<count>`
- Paper equity/PnL: `<summary>`
- Recurring errors: `<none / list>`
- Research campaign/lab/evolution observations: `<summary + transient caveat>`
- Blockers before 30-day run: `<list>`

## No-Drift Notes

- This proving run remains paper-only.
- No live execution enablement is included.
- No promotion automation is included.
- Research-memory-backed campaign/lab/evolution data should not be used as durability proof until persisted in a future phase.
