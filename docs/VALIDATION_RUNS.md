# Validation Runs v1

## Purpose

Validation Runs formalize paper-mode proving runs as controlled experiments.

This feature is experiment management only.

## Guardrails

- No live trading.
- No automatic promotion.
- No production strategy logic changes.
- No AI behavior changes.
- No OpenAI behavior changes.
- No autonomous live execution.

## Durable Data Model

Validation Runs persist through restart using:

- `validation_runs`
- `validation_run_events`
- `validation_run_metrics`
- `validation_run_scorecards`

### Validation Run Fields

- `validation_run_id`
- `name`
- `objective`
- `duration_hours`
- `status`
- `started_at`
- `expected_end_at`
- `completed_at`
- `paper_capital`
- `enabled_strategies`
- `enabled_research_agents`
- `enabled_research_features`
- `health_score`
- `result_status`

### Status Values

- `DRAFT`
- `RUNNING`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

### Result Status Values

- `PASS`
- `CONDITIONAL_PASS`
- `FAIL`
- `INCOMPLETE`

## API Surface

- `GET /validation-runs`
- `GET /validation-runs/{id}`
- `POST /validation-runs`
- `POST /validation-runs/{id}/start`
- `POST /validation-runs/{id}/cancel`
- `GET /validation-runs/{id}/events`
- `GET /validation-runs/{id}/metrics`

All endpoints are paper/research-only.

## Start Run Behavior

Starting a run:

- Marks run as `RUNNING`
- Sets `started_at` and `expected_end_at`
- Appends initial event: `Validation run started`
- Captures baseline metrics:
  - candles
  - signals
  - trades
  - decision records
  - paper equity
  - campaign count
  - research candidates
  - evolution count

Starting a run does not:

- Enable live trading
- Promote strategies
- Modify strategy logic

## Validation Metrics

Run metrics include:

- elapsed percentage
- time remaining
- candles processed during run
- signals generated during run
- trades executed during run
- decision records created during run
- paper PnL during run
- current equity
- current champion
- candidates generated
- candidates evaluated
- evolution descendants
- research memory growth
- alerts count

## Deterministic Scorecard

Categories:

- API Health
- Worker Health
- Database Health
- Data Ingestion
- Strategy Execution
- Paper Trading
- Research Agents
- Evolution Engine
- Campaign Engine
- Dashboard Data

Each category has:

- status
- score
- notes

Overall score is deterministic in range 0-100.

No AI involvement.

## Frontend: Validation Runs Page

Route:

- `/validation-runs`

Primary sections:

1. New Validation Run form
2. Active Validation Run panel
3. Scorecard panel
4. Validation Run History
5. Validation Run Detail (expandable)

The page reuses Mission Control, Paper Pipeline, Research Campaign, Research Memory, Evolution, and Tournament evidence.

## Test Scope

Backend:

- create validation run
- start validation run
- cancel validation run
- list validation runs
- metrics calculation
- scorecard calculation
- persistence across restart

Frontend:

- form render
- start run flow
- active run progress
- scorecard render
- history render
- empty state
