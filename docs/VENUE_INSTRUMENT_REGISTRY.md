# VENUE_INSTRUMENT_REGISTRY.md

Version: 1.0
Status: Constitutional Vision
Priority: Foundational Architecture
Scope: Provider-Specific Financial Instruments

---

# Purpose

The Venue Instrument Registry defines how canonical assets are represented on individual execution venues.

While the Asset Registry answers:

> **What is the asset?**

The Venue Instrument Registry answers:

> **How can that asset be traded on a particular venue?**

This document establishes the separation between financial assets and the provider-specific instruments used to execute transactions.

---

# Guiding Principle

An instrument is not an asset.

An instrument is a provider-specific representation of a canonical asset.

Different providers may represent the same asset differently.

OmniTrade should understand those differences while preserving a single canonical asset identity.

---

# Relationship to Other Documents

ASSET_REGISTRY.md defines:

> Canonical financial assets.

This document defines:

> Provider-specific tradable instruments.

Future documents such as EXECUTION_ROUTING.md and PROVIDER_CAPABILITIES.md determine how those instruments are selected and executed.

---

# Separation of Responsibilities

The Asset Registry owns financial identity.

The Venue Instrument Registry owns provider representation.

Example:

Canonical Asset:

Bitcoin

Provider Representations:

Kraken

BTC/USD

Coinbase

BTC-USD

Robinhood

BTC/USD

Future Exchange

XBTUSD

Each instrument represents the same underlying asset while preserving the provider's native conventions.

---

# Venue Independence

Every execution venue may define its own:

- symbol format
- naming convention
- precision
- minimum order size
- fee schedule
- trading rules
- supported order types
- quote currencies
- operational limitations

The Venue Instrument Registry records these differences without changing the underlying asset.

---

# Instrument Identity

Every venue instrument should possess its own internal identifier.

An instrument references:

- one execution venue
- one canonical asset
- one tradable representation

The instrument exists only within the context of its venue.

Removing or replacing a provider must never alter the canonical Asset Registry.

---

# Instrument Metadata

Each venue instrument may define metadata including:

- provider
- provider symbol
- base asset
- quote asset
- instrument status
- precision
- minimum quantity
- minimum notional value
- maximum quantity
- supported order types
- supported time-in-force policies
- trading permissions
- fee schedule reference
- liquidity classification

Additional metadata may be incorporated as providers evolve.

---

# Understanding Trading Instruments

A financial asset may have multiple tradable instruments.

Example:

Canonical Asset:

Bitcoin

Possible Instruments:

BTC/USD

BTC/USDC

BTC/USDT

BTC/EUR

BTC/GBP

Each instrument represents Bitcoin while using a different quote asset.

The choice of instrument may influence:

- execution cost
- liquidity
- spreads
- available capital
- provider capabilities

The canonical asset remains unchanged.

---

# Base Asset

The base asset is the asset being purchased or sold.

Example:

BTC/USD

Base Asset:

Bitcoin

ETH/USD

Base Asset:

Ethereum

---

# Quote Asset

The quote asset is the asset used to determine price.

Examples:

BTC/USD

Quote Asset:

United States Dollar

ETH/USDT

Quote Asset:

Tether

SOL/BTC

Quote Asset:

Bitcoin

Changing the quote asset creates a different trading instrument.

It does not create a different canonical asset.

---

# Instrument Lifecycle

Venue instruments may progress through operational states such as:

- Discovered
- Available
- Supported
- Monitoring Enabled
- Paper Enabled
- Live Enabled
- Suspended
- Delisted
- Archived

These states describe operational availability.

They never redefine the underlying asset.

---

# Provider Synchronization

Whenever possible, provider-specific metadata should be synchronized automatically.

Examples include:

- newly listed instruments
- delisted instruments
- precision updates
- minimum order changes
- supported order types
- trading status

Provider synchronization should minimize manual maintenance.

---

# Provider Isolation

Provider-specific details should not leak into provider-neutral strategy, portfolio, or capital-allocation logic.

Venue-specific instrument knowledge may be consumed by:

- market-data adapters
- execution adapters
- the Venue Intelligence Engine
- execution-routing systems
- reconciliation systems
- provider-readiness systems

These components should access venue-specific details through defined registry interfaces rather than embedding provider symbols, limits, or rules directly in core logic.

---

# Future Multi-Provider Support

A single canonical asset may simultaneously possess instruments across many providers.

Example:

Bitcoin

↓

Kraken

BTC/USD

↓

Coinbase

BTC-USD

↓

Robinhood

BTC/USD

↓

Interactive Brokers

Bitcoin Instrument

↓

Future Providers

Additional representations

OmniTrade should be capable of supporting all simultaneously.

---

# Instrument Selection

The Venue Instrument Registry does not determine which instrument should be used.

It only records available representations.

Future execution-routing systems may evaluate:

- execution cost
- liquidity
- spread
- slippage
- provider reliability
- latency
- account permissions
- available balances

before selecting an instrument.

---

# Scalability

The registry should support:

- thousands of assets
- tens of thousands of instruments
- hundreds of providers

without architectural redesign.

Adding a provider should primarily require implementing a provider adapter rather than modifying core financial intelligence.

---

# Design Principles

The Venue Instrument Registry should remain:

- Provider Specific
- Canonically Linked
- Extensible
- Deterministic
- Replayable
- Auditable
- Explainable
- Backward Compatible

---

# Long-Term Vision

The Venue Instrument Registry serves as the translation layer between OmniTrade's provider-neutral financial intelligence and the operational realities of individual execution venues.

As exchanges, brokers, APIs, and financial products evolve, OmniTrade should continue reasoning about stable canonical assets while dynamically adapting to new provider-specific instrument representations.

This separation enables the platform to scale across cryptocurrencies, equities, foreign exchange, commodities, options, futures, prediction markets, and future financial ecosystems without altering its core financial reasoning.