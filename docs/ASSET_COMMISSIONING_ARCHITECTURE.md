# OmniTrade Asset Commissioning Architecture

Version: 0.1 Draft  
Date: 2026-07-25  
Status: Proposed; pending repository investigation and approval

## Purpose

Define a safe, repeatable, auditable way to add a new tradable asset to OmniTrade without manual database edits, fragile shell sequences, or handcrafted authorization payloads.

The capability must support crypto now and remain extensible to equities, ETFs, forex, options, futures, prediction markets, and other future asset classes.

## Problem Statement

Adding ETH-USD required separate manual operations for:

- Provider-product verification
- Canonical asset creation
- Candle backfill
- Campaign membership
- Mandate successor creation
- Mandate authorization and promotion
- Runtime selector update
- Service restart
- Runtime verification

This procedure is cumbersome, easy to mistype, difficult to resume after partial failure, and unsuitable for scaling to ten or more assets.

A further risk is that an asset may exist in the database and be authorized by campaign and mandate governance while still not being discovered by the continuous runtime.

## Architectural Principle

Asset commissioning is not a single database insert. It is a governed lifecycle.

An asset is not READY merely because it exists. READY requires verified runtime participation.

## Scope

The Asset Commissioning Service shall coordinate existing provider, market-data, campaign, mandate, runtime-selection, and audit services. It must reuse existing domain services rather than duplicate business logic or invoke shell scripts from the API.

## Core Components

### 1. Asset Commissioning Application Service

Owns the end-to-end commissioning workflow and stage transitions.

Responsibilities:

- Normalize canonical and provider product identities
- Produce a deterministic preview
- Execute an approved plan
- Preserve governance and risk constraints
- Track resumable stage status
- Return evidence and blockers

### 2. Provider Capability Adapter

Confirms that the requested provider supports the product and resolves provider-native identifiers.

Examples:

- Canonical: `SOL-USD`
- Provider: Kraken-specific pair identifier

### 3. Market Data Commissioning Adapter

Creates or reuses the canonical asset, backfills required history, verifies minimum candle count, and proves freshness.

### 4. Campaign Membership Adapter

Adds the asset to the selected campaign without removing existing assets or altering unrelated campaign settings.

### 5. Mandate Successor Adapter

Creates a successor mandate version that:

- Preserves every existing capital and risk constraint
- Preserves autonomy level
- Preserves already authorized products
- Adds only the requested product
- Is authorized and promoted through the existing mandate service

### 6. Runtime Discovery Adapter

Ensures the continuous worker can discover and process the newly commissioned asset.

The implementation should prefer dynamic discovery from authoritative campaign and asset records. Per-asset hard-coded triggers or environment variables should be eliminated where safely possible.

### 7. Readiness and Evidence Service

Determines whether each commissioning stage is proven and returns deterministic blockers and warnings.

### 8. Audit Record

Every preview and mutation must have:

- Commissioning ID
- Actor identity
- Environment
- Provider
- Product ID
- Campaign ID
- Correlation ID
- Idempotency key
- Stage timestamps
- Before and after evidence
- Failure reason, when applicable

## Proposed Lifecycle

1. REQUESTED
2. PREVIEWED
3. PROVIDER_VERIFIED
4. ASSET_REGISTERED
5. MARKET_DATA_READY
6. CAMPAIGN_AUTHORIZED
7. MANDATE_SUCCESSOR_CREATED
8. MANDATE_AUTHORIZED_AND_PROMOTED
9. RUNTIME_DISCOVERABLE
10. STRATEGY_EVALUATION_OBSERVED
11. READY

Failure states must be explicit and resumable. A partially completed asset must never silently become live-execution eligible.

## Proposed API Endpoints

### POST `/operator/assets/commission/preview`

Produces a no-mutation commissioning plan.

Request:

```json
{
  "provider": "kraken",
  "product_id": "SOL-USD",
  "campaign_id": "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
  "environment": "production"
}
```

Response should include:

- Canonical normalized product identity
- Provider support and mapping
- Existing asset state
- Candle count and freshness
- Required campaign mutation
- Required mandate successor
- Exact risk and capital constraints to preserve
- Runtime-discovery requirement
- Expected changes
- Blockers and warnings
- Deterministic execution plan

### POST `/operator/assets/commission`

Executes an approved commissioning plan.

Request:

```json
{
  "provider": "kraken",
  "product_id": "SOL-USD",
  "campaign_id": "e9a9e8e9-9574-498d-b49e-f011218c7f2b",
  "environment": "production",
  "activate": true,
  "idempotency_key": "commission-sol-usd-<operator-supplied-value>"
}
```

The endpoint must be idempotent, fail closed, resumable, and auditable.

### GET `/operator/assets/commission/{commissioning_id}`

Returns stage-by-stage status, evidence, timestamps, blockers, warnings, and the next permitted action.

### GET `/operator/assets/{product_id}/readiness`

Returns:

```json
{
  "product_id": "SOL-USD",
  "provider_supported": true,
  "asset_registered": true,
  "market_data_current": true,
  "candle_count": 100,
  "campaign_authorized": true,
  "mandate_authorized": true,
  "runtime_selected": true,
  "strategy_evaluation_observed": true,
  "live_execution_eligible": true,
  "blockers": [],
  "warnings": [],
  "overall_status": "READY"
}
```

## Readiness Rule

`overall_status=READY` requires all of the following:

- Provider support verified
- Canonical asset exists
- Required market history exists and is fresh
- Campaign permits the asset
- Governing mandate permits the asset
- Runtime can discover the asset
- A post-commission strategy-roster evaluation has been observed for the canonical asset ID
- No unresolved safety or governance blocker exists

Database presence alone is insufficient.

## Safety Invariants

- Adding an asset must never increase order size, campaign capital, daily-loss limit, drawdown limit, or autonomy level.
- Existing authorized products must not be removed.
- Commissioning must not force a BUY or SELL.
- Live execution remains governed by strategy, economics, risk, mandate, and execution gates.
- Every stage fails closed.
- Repeated requests with the same idempotency key must not duplicate assets, mandate versions, or authorizations.
- Runtime verification must distinguish configuration readiness from actual observed processing.

## Runtime Architecture Direction

The preferred long-term model is provider-neutral dynamic discovery:

```text
Heartbeat or market-data event
          ↓
Discover due work across commissioned campaign assets
          ↓
Refresh stale data as needed
          ↓
Run strategy roster
          ↓
Economics → Risk → Mandate → Execution
```

Event-driven candle-close processing should remain, but a deterministic heartbeat may later provide reconciliation and missed-event recovery. The heartbeat is complementary, not a replacement for market events.

## Initial Acceptance Asset

Use `SOL-USD` as the first acceptance-test asset after implementation because the production campaign has previously included SOL-USD.

Acceptance requires:

- Successful preview
- Successful idempotent execution
- Market-data readiness
- Campaign and mandate authorization
- Runtime discovery
- A strategy-roster run tied to the SOL canonical asset ID
- A readiness response of READY only after runtime proof

## Open Questions for Repository Investigation

1. How does the continuous worker choose assets today?
2. Is BTC identity hard-coded anywhere?
3. Is `kraken_btc_15m_candle_close` merely a trigger label or a BTC-only code path?
4. Does ingestion support concurrent Kraken products?
5. Why has ETH not yet been observed in strategy-roster logs?
6. Can future asset activation avoid systemd restarts?
7. Is a database migration required for commissioning records and stage evidence?
8. Which existing services and repositories can be reused directly?

## Rollback Principles

Rollback must not delete immutable audit evidence.

A failed or reversed commission should:

- Mark the commissioning record failed or revoked
- Remove live eligibility through a new governed configuration/version
- Preserve historical asset, campaign, mandate, and evidence records
- Restore the previously governing mandate version through an auditable successor or approved rollback mechanism
- Never rely on destructive database edits

