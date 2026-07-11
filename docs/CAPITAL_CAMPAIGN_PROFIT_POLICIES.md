# Capital Campaign Profit Policies (Phase 2)

## Purpose

Phase 2 introduces durable, campaign-scoped profit-handling policy configuration.

This foundation defines how profit recommendations are calculated and reviewed.

It does not execute transfers, withdrawals, or live orders.

## Safety Boundary

Required boundary:

- `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`

Phase 2 behavior is recommendation-only.

Allowed actions:

- calculate policy-based recommendations
- persist recommendation evidence
- allow operator approval or rejection of recommendation cycles

Disallowed actions:

- move funds
- submit live orders
- trigger broker transfers

## Durable Data Model

Policy table:

- `capital_campaign_profit_policies`

Cycle table:

- `capital_campaign_profit_cycles`

Policy stores:

- policy type
- target amount or target percent
- compounding and withdrawal percentages
- reserve percentages
- minimum cash reserve and minimum realized profit requirements
- protected principal floor (optional)
- maximum campaign capital ceiling (optional)
- cooldown window
- approval requirement
- active flag

Cycle stores:

- cycle number and campaign/policy references
- opening and closing accounting values
- realized/unrealized profit and fees at evaluation time
- eligible profit and recommendation split
- target reached status
- recommendation status and settlement state
- calculation snapshot for explainability
- deterministic calculation fingerprint for idempotency
- approval and completion timestamps

## Policy Types

Current policy types:

- `FULL_COMPOUND`
- `PARTIAL_COMPOUND`
- `WITHDRAW_PROFIT`
- `WITHDRAW_AND_COMPOUND`
- `PROTECTED_PRINCIPAL`
- `HOLD_PROFIT`

## API Surface

- `POST /capital-campaigns/{campaign_uuid}/profit-policy`
- `GET /capital-campaigns/{campaign_uuid}/profit-policy`
- `PATCH /capital-campaigns/{campaign_uuid}/profit-policy`
- `POST /capital-campaigns/{campaign_uuid}/profit-cycles/evaluate`
- `GET /capital-campaigns/{campaign_uuid}/profit-cycles`
- `GET /capital-campaigns/{campaign_uuid}/profit-cycles/{cycle_uuid}`
- `POST /capital-campaigns/{campaign_uuid}/profit-cycles/{cycle_uuid}/approve`
- `POST /capital-campaigns/{campaign_uuid}/profit-cycles/{cycle_uuid}/reject`

## Evaluation Semantics

Profit-cycle evaluation uses durable campaign accounting state and policy constraints.

At a high level:

1. Determine eligible profit after fees/reserves/constraints.
2. Determine target state (amount or percent).
3. Compute compounding and withdrawal recommendations by policy type.
4. Enforce protected-principal and max-capital boundaries.
5. Persist cycle with deterministic fingerprint and evidence snapshot.

If target is not reached, recommendations are set to zero.

## Idempotency and Audit

Idempotency:

- fingerprinted cycle calculations return existing cycles for the same evidence payload unless a forced new cycle is requested

Audit actions include:

- `PROFIT_POLICY_CREATED`
- `PROFIT_POLICY_UPDATED`
- `PROFIT_TARGET_REACHED`
- `PROFIT_CYCLE_EVALUATED`
- `COMPOUNDING_RECOMMENDED`
- `WITHDRAWAL_RECOMMENDED`
- `PROFIT_CYCLE_APPROVED`
- `PROFIT_CYCLE_REJECTED`

## Operator Statement

All UI and API-facing recommendation artifacts carry the core statement:

- This is an accounting recommendation only. No funds will move.
