# Capital Ledger v1

## Purpose

Capital Ledger v1 is a read-only accounting surface for OmniTrade paper capital. It provides one authoritative view of where capital is funded, where it is assigned, and how each pool is performing, without changing execution, strategy, or allocation behavior.

## Capital Terminology

- **Starting Capital**: The initial funded amount for a top-level pool at creation.
- **Current Equity**: Mark-to-market value for a pool at read time.
- **Managed Capital**: Sum of **distinct top-level funded capital pools** represented in the system.
- **Allocated Capital**: Capital currently committed to active pool usage.
- **Available Capital**: Uncommitted cash/equity inside the pool.
- **Reserved Capital**: Capital currently reserved in active commitments.
- **Realized PnL**: Closed PnL where durable source data exists.
- **Unrealized PnL**: Mark-to-market PnL for open allocations/positions.
- **Active Capital Pool**: Pool with status `active`.
- **Inactive Capital Pool**: Pool with status `inactive`.
- **Archived Capital Pool**: Pool with terminal status (`completed` or `cancelled`) kept visible read-only.

## Capital-Pool Ownership Model

Top-level pools (counted in Managed Capital):

1. Paper accounts (`paper_account`)
2. Independently funded validation runs (`validation_run`)

Child allocations (not counted in Managed Capital):

1. Strategy allocations (`strategy_allocation`)
2. Open positions (`position`)
3. Trades and execution records

A child allocation references `parent_capital_pool_id` and is valuation detail only.

## Double-Counting Prevention Rules

1. Managed Capital sums only top-level pools (`parent_capital_pool_id = null`).
2. Child rows (positions, strategy allocations, trades) are excluded from Managed Capital.
3. Validation run trades/positions are observational children of the run pool.
4. Position value is not added on top of the same parent pool funding.
5. Trade count affects activity metrics only, never capital principal.

## Managed Capital Calculation

Managed Capital formula:

`Managed Capital = Σ(starting_capital of distinct top-level pools)`

- Example: Two independent funded runs at `$25` each -> Managed Capital = `$50`.
- If one run has open positions, those positions remain child allocations and are not added as principal.

## Active vs Inactive Definitions

- `active`: currently running/active pool.
- `inactive`: non-running but non-terminal pool.
- `completed`: terminal pool completed.
- `cancelled`: terminal pool cancelled.

## Data Sources

Capital Ledger v1 reads existing durable sources only:

- `validation_runs`
- `validation_run_metrics` (latest snapshot per run)
- `paper_accounts`
- `trades`
- computed paper accounting snapshot (derived from durable trades + candles)
- `research_campaigns` presence only (funded campaign allocations are currently unavailable)

No second accounting database is introduced.

## Read-Only Scope

Capital Ledger v1 performs no writes:

- No funding edits
- No allocation moves
- No live orders
- No broker API calls
- No strategy/AI behavior changes

## Partial Data and Completeness

If a source is unavailable or not durably tracked, the ledger returns:

- `data_completeness_percent`
- `unavailable_sources[]`
- nullable fields for unavailable pool-level values

This avoids fabricated values and preserves operator trust.

## Future Live-Capital Evolution

v1 is intentionally paper-only and read-only. Future evolution can extend the same ownership model to:

- exchange account pools
- sub-account balances
- live reserved margin/collateral
- funding transfer audit trails

without changing v1 double-counting safeguards.
