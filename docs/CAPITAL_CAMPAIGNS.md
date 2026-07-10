# Capital Campaigns Foundation (Phase 1)

## Scope

Capital Campaigns Foundation introduces a new campaign-scoped capital domain model.

This phase is data-model and CRUD foundation only.

It does not:

- enable live automation
- enable automatic withdrawals
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

## Phase 1 Limitations

Phase 1 does not include:

- automatic withdrawals
- automatic compounding
- autonomous live trading control
- multi-user custody
