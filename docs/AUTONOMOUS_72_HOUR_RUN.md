# Autonomous 72-Hour Research Run

## Goal

Prepare the platform to execute an unattended 72-hour paper research campaign with complete operational observability.

This milestone is operational only.

## Scope and Guardrails

In scope:
- Run heartbeat and health status visibility
- Operational monitoring counters
- Mission Control read-only dashboard
- Alert surfacing for unattended run risk signals
- Tests for backend heartbeat/status behavior and frontend Mission Control states

Out of scope:
- Live trading
- Automatic promotion
- Strategy logic changes
- AI behavior changes
- Architecture redesign

## Backend Support

### Heartbeat Endpoint

- Route: `GET /operations/status`
- Behavior: read-only, no writes
- Returns:
  - overall operational health
  - autonomous run status
  - system component indicators
  - research status
  - monitoring counters
  - active alerts

### Run Status

Returned fields:
- `run_id`
- `started_at`
- `expected_end`
- `uptime`
- `current_phase`
- `health_status`

### Monitoring Counters

Returned counters:
- `candles_processed`
- `signals_generated`
- `paper_trades_executed`
- `decision_records_created`
- `replay_count`
- `candidate_count`
- `campaign_count`
- `laboratory_runs`
- `evolution_count`
- `current_champion`
- `paper_equity`

Additional operational counters for dashboard context:
- `signals_today`
- `trades_today`
- `research_memory_growth`

### Alerts (Read-Only)

The endpoint surfaces alert entries for:
- database unavailable
- worker stopped
- no new candles
- no new signals
- no new trades
- campaign stalled
- research agent unavailable

## Frontend Mission Control

### Route

- `app/mission-control/page.tsx`

### Displayed Areas

- System Health
- API
- Orchestrator
- Database
- Research Agent
- Current Campaign
- Current Champion
- Paper Equity
- Signals Today
- Trades Today
- Research Candidates
- Evolution Progress
- Research Memory Growth
- 72-Hour Countdown

All indicators use green/yellow/red visual states.

### Refresh Behavior

- Auto-refresh every 15 seconds
- Read-only rendering of backend status payload

## Operational Interpretation

### Health Summary

- `green`: no active warnings/errors
- `yellow`: warning-level drift/staleness detected
- `red`: critical readiness issue detected

### Run Phase Interpretation

- `bootstrapping`: no material data flow yet
- `data_collection`: candles flowing
- `paper_execution`: signals/trades active
- `researching`: campaign/lab/evolution activity detected
- `degraded`: critical dependency issue

## Test Coverage

### Backend

- status endpoint payload shape and required fields
- heartbeat route reachability
- uptime formatting and edge behavior

### Frontend

- Mission Control healthy state
- Mission Control empty state
- Mission Control degraded/alerts state
- indicator rendering and core metric visibility

## Readiness Checklist for Unattended Run

1. Confirm `GET /operations/status` returns `overall_health` not red.
2. Confirm orchestrator heartbeat is active.
3. Confirm monitoring counters increase over time.
4. Confirm alerts panel stays empty or only contains expected warnings.
5. Confirm Mission Control remains accessible and auto-refreshing.

## Acceptance Mapping

This operational support enables a user to leave the system running for 72 hours and return to Mission Control to verify:
- whether the system remained healthy,
- what research occurred,
- how strategies evolved,
- whether paper profitability improved,
- and whether the system appears ready for a longer proving run.
