# Capital Campaigns Foundation (Phase 1 + Phase 2)

## Scope

Capital Campaigns Foundation introduces campaign-scoped capital domain models.

Phase 1 delivered campaign CRUD and lifecycle management.

Phase 2 adds profit policy configuration and profit-cycle accounting recommendations.

It does not:

- enable live automation
- enable automatic withdrawals
- execute withdrawals or transfers
- enable multi-user custody
- change existing trading behavior

## Core Model

Table: `capital_campaigns`

Fields:

- id
- uuid
- owner
- name
- description
- status
- campaign_type
- exchange
- paper_account_id
- validation_run_id
- strategy_id
- starting_capital
- current_equity
- realized_profit
- unrealized_profit
- fees
- roi
- created_at
- updated_at

## Statuses

Allowed campaign statuses:

- DRAFT
- READY
- RUNNING
- PAUSED
- TARGET_REACHED
- COMPLETED
- ARCHIVED

## Lifecycle Rules

Allowed transition paths:

- DRAFT -> READY
- READY -> RUNNING
- RUNNING -> PAUSED
- PAUSED -> RUNNING
- RUNNING -> TARGET_REACHED
- TARGET_REACHED -> COMPLETED
- COMPLETED -> ARCHIVED
- PAUSED -> ARCHIVED

Blocked examples:

- ARCHIVED -> RUNNING
- COMPLETED -> DRAFT
- TARGET_REACHED -> READY

## Relationships

Campaign links are modeled through nullable foreign keys:

- `paper_account_id -> paper_accounts.id`
- `validation_run_id -> validation_runs.validation_run_id`
- `strategy_id -> strategies.id`

This supports the relationship chain:

Capital Campaign -> Validation Runs -> Paper Accounts -> Trades -> Positions -> Decision Records -> Mission Control Timeline

## API Surface

- `GET /capital-campaigns`
- `POST /capital-campaigns`
- `GET /capital-campaigns/{campaign_uuid}`
- `PATCH /capital-campaigns/{campaign_uuid}`
- `DELETE /capital-campaigns/{campaign_uuid}`

Phase 2 API additions:

- `POST /capital-campaigns/{campaign_uuid}/profit-policy`
- `GET /capital-campaigns/{campaign_uuid}/profit-policy`
- `PATCH /capital-campaigns/{campaign_uuid}/profit-policy`
- `POST /capital-campaigns/{campaign_uuid}/profit-cycles/evaluate`
- `GET /capital-campaigns/{campaign_uuid}/profit-cycles`
- `GET /capital-campaigns/{campaign_uuid}/profit-cycles/{cycle_uuid}`
- `POST /capital-campaigns/{campaign_uuid}/profit-cycles/{cycle_uuid}/approve`
- `POST /capital-campaigns/{campaign_uuid}/profit-cycles/{cycle_uuid}/reject`

Delete behavior is non-destructive in Phase 1.

`DELETE` archives the campaign by setting status to `ARCHIVED`.

## Money and Validation Semantics

- Money inputs are Decimal-safe on API and service boundaries.
- `starting_capital` must be greater than zero.
- `current_equity` must be non-negative in Phase 1.
- ROI is derived from `(current_equity - starting_capital) / starting_capital * 100`.
- `owner` is immutable after campaign creation.

## Mission Control Integration

Mission Control now exposes `total_managed_capital` using active campaign starting capital.

Active statuses for this metric:

- READY
- RUNNING
- PAUSED
- TARGET_REACHED

Excluded statuses:

- DRAFT
- COMPLETED
- ARCHIVED

Duplicate `paper_account_id` references are deduplicated to prevent managed-capital double counting.

## Capital Ledger Integration

Capital Ledger pools include optional campaign linkage fields when a mapping is available:

- `capital_campaign_uuid`
- `capital_campaign_name`

Existing ledger semantics remain unchanged and backward compatible.

Phase 2 recommendation rows may also be linked to campaigns for cycle evidence:

- `compounding_recommendation`
- `withdrawal_recommendation`
- `profit_reserve`
- `policy_review`

These rows are accounting evidence only and do not change managed-capital totals.

## Profit Policy and Cycle Model (Phase 2)

Phase 2 adds durable policy and cycle tables:

- `capital_campaign_profit_policies`
- `capital_campaign_profit_cycles`

Policy configuration includes:

- policy type
- target amount or target percent
- compounding and withdrawal percentages
- reserve controls
- optional protected-principal and max-capital boundaries
- cooldown and approval requirements

Cycle evaluations include:

- eligibility calculations from durable realized profit/equity state
- recommendation split (compound/withdraw/reserve)
- target progress and reach state
- idempotent fingerprinting
- approval/rejection review trail

Cycle outputs are recommendations, not execution directives.

## Limitations

Current foundation does not include:

- automatic withdrawals
- automatic compounding execution
- autonomous live trading control
- multi-user custody
