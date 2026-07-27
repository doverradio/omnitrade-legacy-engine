# ASSET_REGISTRY.md

Version: 1.0
Status: Constitutional Vision
Priority: Foundational Architecture
Scope: Canonical Asset Identity

---

# Purpose

The Asset Registry defines the canonical representation of financial assets within OmniTrade.

Its purpose is to establish a permanent identity for every asset that OmniTrade may observe, analyze, simulate, allocate capital toward, or trade.

The Asset Registry is intentionally independent of any execution provider, exchange, broker, or financial institution.

Assets remain the same regardless of where they are traded.

---

# Guiding Principle

An asset is not defined by where it trades.

An asset is not defined by how a provider names it.

An asset exists independently of every venue.

OmniTrade should therefore maintain one canonical identity for every financial asset.

Providers merely expose tradable representations of those assets.

---

# Relationship to Other Documents

PROJECT_VISION.md answers:

> What is OmniTrade trying to become?

MATHEMATICAL_FOUNDATIONS.md answers:

> How should OmniTrade reason about financial systems?

This document answers:

> **What is an asset?**

Future provider-specific documents will answer:

> How is this asset represented on a particular venue?

---

# Canonical Asset Identity

Every asset shall possess one permanent OmniTrade identity.

This identity must never depend upon:

- exchange symbols
- provider naming conventions
- execution providers
- broker implementations
- account types
- trading venues

The canonical identity remains stable throughout the lifetime of the platform.

---

# Examples

Bitcoin is an asset.

The following are not separate assets.

Kraken:

BTC/USD

Coinbase:

BTC-USD

Robinhood:

BTC/USD

These are provider-specific trading instruments representing the same canonical asset.

Likewise:

Ethereum

remains Ethereum regardless of the venue through which it is traded.

---

# Asset Classes

The Asset Registry should support multiple financial asset classes.

Examples include:

- Cryptocurrency
- Equity
- ETF
- Mutual Fund
- Bond
- Treasury
- Commodity
- Foreign Exchange
- Stablecoin
- Index
- Option
- Future
- Prediction Market Contract
- Real Estate Investment Vehicle
- Cash Equivalent

The registry intentionally remains extensible.

Future asset classes should not require architectural redesign.

---

# Asset Metadata

Every canonical asset may contain descriptive metadata.

Examples include:

- canonical symbol
- display name
- asset class
- creation date
- status
- precision preferences
- primary currency
- issuer (when applicable)

Additional metadata may be incorporated as future capabilities require.

---

# Canonical Identifiers

Every asset should possess a permanent internal identifier.

This identifier should remain unchanged even if:

- provider symbols change
- exchanges rename instruments
- venues merge
- brokers are replaced
- routing logic evolves

Historical decisions, simulations, and audit records should always reference the canonical asset identifier rather than provider-specific symbols whenever practical.

---

# Provider Independence

The Asset Registry must remain completely independent of execution providers.

Examples of providers may include:

- Kraken
- Coinbase
- Robinhood
- Interactive Brokers
- Alpaca
- Binance
- Future providers

No provider-specific assumptions should exist within the Asset Registry.

Provider-specific implementations belong within separate architectural components.

---

# Asset Lifecycle

Assets may progress through operational states.

Examples include:

- Proposed
- Active
- Suspended
- Deprecated
- Archived

Lifecycle state affects operational availability.

It does not change canonical identity.

---

# Asset Relationships

Future versions of OmniTrade may model relationships between assets.

Examples include:

- Bitcoin and Wrapped Bitcoin
- Spot assets and derivative contracts
- Equity and related options
- Stablecoins pegged to USD
- ETFs tracking the same index

Relationships should enrich understanding without altering canonical identity.

---

# Separation of Concerns

The Asset Registry intentionally does not define:

- trading venues
- order routing
- execution providers
- market data feeds
- brokerage accounts
- trading fees
- spreads
- liquidity
- execution quality

Those concerns belong to separate architectural components.

The sole responsibility of the Asset Registry is to define the canonical identity of financial assets.

---

# Future Scalability

The Asset Registry should support thousands of assets without architectural modification.

Adding a new asset should require configuration rather than software redesign.

Future asset classes should integrate into the same registry using the same architectural principles.

---

# Design Principles

The Asset Registry should remain:

- Provider Independent
- Exchange Independent
- Deterministic
- Extensible
- Replayable
- Explainable
- Immutable where appropriate
- Backward Compatible

---

# Long-Term Vision

The Asset Registry establishes a permanent financial vocabulary for OmniTrade.

As execution providers, exchanges, brokers, and financial products evolve over time, OmniTrade should continue reasoning about the same canonical assets through stable internal identities.

This separation allows provider integrations to evolve independently while preserving historical records, decision intelligence, simulation results, auditability, and capital allocation logic across the lifetime of the platform.