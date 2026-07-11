# Execution Provider Layer

## EP-1 Status

EP-1 completed a controlled architectural extraction for execution providers without changing live-order behavior.

What EP-1 does:

- keeps Coinbase fully operational
- introduces one authoritative provider contract and capability model
- hardens provider registry lookups and fail-closed capability gates
- routes generic submission and reconciliation orchestration through provider contract methods
- adds provider conformance and architecture-boundary test coverage

What EP-1 does not do:

- no Kraken implementation in this prompt
- no automatic provider selection
- no failover routing
- no live submission enablement
- no live create_order call in dry-run paths

## Vision

Execution providers are interchangeable implementations
of a common contract.

The Decision Engine never chooses Coinbase.

It requests an execution.

The Venue Intelligence Engine chooses the optimal provider.

In EP-1, provider choice remains explicit from configuration or exchange connection.
Venue Intelligence does not select providers yet.

---

## North Star

No single exchange or broker may become
a single point of failure.

---

## Provider Contract

Authoritative contract location:

- apps/api/app/services/exchange_connections/providers/base.py

Capability groups in contract:

- identity and metadata
- authentication and readiness
- account and balance access
- product lookup
- pricing and preview support
- submission contract with stable client-order-id support
- read-only order lookup
- fills and fees
- environment behavior for production and sandbox
- health and observability metadata

Optional capabilities:

- capabilities are explicit and provider-declared
- missing required capability fails closed with typed operator-safe error
- no implicit fallback to another provider in EP-1

---

## Provider Registry

Registry location:

- apps/api/app/services/exchange_connections/providers/registry.py

Registry behavior:

- maps stable provider keys to implementations
- supports environment-aware lookup
- exposes provider metadata and capability sets
- rejects unknown providers fail-closed
- supports capability gating before provider-backed operations
- exposes provider mock-mode state for sandbox/mock boundary checks

Current registered provider:

- coinbase_advanced

Kraken remains additive for later prompts.

---

## Provider Selection

EP-1 selection behavior:

- explicit provider only
- no automatic venue choice
- no fallback routing
- capability requirements validated before operation

---

## Provider Health

Availability

Latency

Spread

Fee Schedule

Historical Reliability

API Errors

Readiness

Authentication

---

## Mission Control

Provider Dashboard

Readiness

Health

Sandbox

Production

Alerts

Fallback Status

EP-1 scope:

- provider-neutral dry-run metadata and readiness distinctions
- sandbox/mock results remain separated from production readiness

---

## Testing Requirements

Provider conformance coverage:

- stable identity and capability contract checks
- environment isolation checks
- readiness and permission shape checks
- balance, product, preview, order, fill, and fee normalization checks
- ambiguous/rejected/success submission classification checks
- no implicit retry checks on submission boundary
- mock forbidden in production checks

Architecture boundary coverage:

- single sanctioned live create_order boundary
- no direct Coinbase import in generic services outside adapter/registry
- no create_order from Mission Control, dry-run, review, initializer, or venue-intelligence surfaces

---

## Long-Term Vision

The Execution Layer becomes
provider-agnostic.

Adding a new provider should require
implementing only the provider contract,
not modifying Decision Engine,
Risk Engine,
Capital Engine,
or Mission Control.

Kraken readiness after EP-1:

- next provider can be added by implementing contract + registry registration + provider-specific tests
- generic risk, approval, accounting, and capital-ledger logic remain reusable