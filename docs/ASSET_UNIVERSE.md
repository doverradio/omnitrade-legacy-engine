# ASSET_UNIVERSE.md

Version: 1.0
Status: Constitutional Vision
Priority: Foundational Architecture
Scope: Asset Selection and Eligibility

---

# Purpose

The Asset Universe defines which canonical assets OmniTrade may monitor, evaluate, simulate, allocate capital toward, and trade.

The Asset Registry answers:

> What assets exist?

The Asset Universe answers:

> Which assets should OmniTrade currently consider?

This separation allows OmniTrade to support thousands of known assets while intentionally restricting operational scope according to campaign objectives, provider capabilities, portfolio rules, and risk policies.

---

# Guiding Principle

Knowing an asset exists does not mean OmniTrade should trade it.

Every operational asset must be intentionally admitted into an Asset Universe.

Asset Universes define the boundaries within which autonomous capital allocation occurs.

---

# Relationship to Other Documents

ASSET_REGISTRY.md defines canonical financial assets.

VENUE_INSTRUMENT_REGISTRY.md defines how those assets are represented on execution venues.

This document defines which canonical assets are eligible for observation and capital allocation.

Future documents such as CROSS_ASSET_OPPORTUNITY_SELECTION.md and EXECUTION_ROUTING.md determine how opportunities are evaluated once assets become eligible.

---

# Why Asset Universes Exist

Financial markets contain thousands of tradable assets.

Not every asset should be evaluated.

Not every evaluated asset should be traded.

Asset Universes allow OmniTrade to intentionally limit its scope according to mission, operational readiness, and risk tolerance.

---

# Universe Hierarchy

Assets progress through increasingly restrictive operational universes.

Each universe serves a different purpose.

---

# 1. Registry Universe

Definition:

Every canonical asset known to OmniTrade.

Examples:

- Bitcoin
- Ethereum
- Solana
- Apple
- Gold
- United States Dollar
- Treasury Bills

Registry inclusion does not imply operational use.

---

# 2. Discovered Universe

Definition:

Assets for which at least one supported provider exposes a tradable instrument.

Examples:

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

An asset may exist in the Registry while not yet existing in the Discovered Universe.

---

# 3. Monitored Universe

Definition:

Assets currently receiving market data.

Requirements may include:

- active provider support
- healthy market data
- sufficient historical data
- operational synchronization

Monitoring does not authorize trading.

---

# 4. Eligible Universe

Definition:

Assets satisfying operational requirements for strategy evaluation.

Examples of eligibility requirements may include:

- sufficient liquidity
- acceptable spreads
- acceptable volatility
- minimum historical data
- provider readiness
- supported order capabilities

Only Eligible assets participate in strategy generation.

---

# 5. Paper Universe

Definition:

Assets approved for simulated execution.

Paper execution validates:

- strategy behavior
- economics
- execution pipeline
- reconciliation
- portfolio interactions

Paper approval does not authorize live trading.

---

# 6. Live Universe

Definition:

Assets approved for autonomous live execution.

Live authorization requires explicit operational approval.

Live authorization should remain intentionally conservative.

---

# Campaign Asset Universes

Every campaign may define its own Asset Universe.

Example:

Campaign A

Purpose:

Crypto Growth

Assets:

Bitcoin

Ethereum

Solana

Campaign B

Purpose:

Dividend Income

Assets:

Apple

Microsoft

Johnson & Johnson

Campaigns should remain independent.

Adding an asset to one campaign should not automatically affect another.

---

# Dynamic Asset Membership

Assets may enter or leave operational universes.

Examples include:

- provider outages
- delistings
- insufficient liquidity
- excessive spreads
- risk controls
- campaign updates
- regulatory restrictions

Universe membership should remain dynamic while preserving deterministic auditability.

---

# Automatic Eligibility

Future versions of OmniTrade may automatically promote assets through operational universes.

Examples:

Discovered

↓

Historical Data Complete

↓

Monitoring Enabled

↓

Eligibility Verified

↓

Paper Approved

↓

Live Authorized

Promotion criteria should remain deterministic and auditable.

---

# Asset Suspension

Assets may be temporarily removed from operational universes.

Examples:

- exchange outage
- abnormal volatility
- insufficient liquidity
- repeated execution failures
- provider instability
- manual administrative suspension

Suspension affects operational participation.

It does not remove the asset from the Asset Registry.

---

# Scalability

The Asset Universe architecture should support:

- tens of assets
- hundreds of assets
- thousands of assets

without requiring architectural redesign.

Adding assets should primarily involve configuration rather than software development.

---

# Provider Neutrality

Asset Universes reference canonical assets.

They do not reference provider-specific instruments.

Provider selection belongs to execution-routing systems.

This separation allows OmniTrade to evaluate opportunities independently of where they may ultimately be executed.

---

# Risk Isolation

Different universes may exist simultaneously.

Examples:

Crypto Universe

Equity Universe

Commodity Universe

Foreign Exchange Universe

Prediction Markets Universe

Future portfolio policies may evaluate these independently while still allowing unified capital allocation.

---

# Operational Philosophy

Expanding an Asset Universe should increase opportunity.

It must never reduce decision quality.

Monitoring additional assets should not weaken:

- Economics
- Risk
- Portfolio Management
- Capital Allocation
- Auditability
- Explainability

Opportunity should expand.

Admission standards should not.

---

# Design Principles

Asset Universes should remain:

- Provider Neutral
- Configuration Driven
- Campaign Aware
- Dynamically Managed
- Deterministic
- Replayable
- Auditable
- Explainable
- Extensible

---

# Long-Term Vision

The Asset Universe enables OmniTrade to evolve from evaluating individual financial instruments toward continuously surveying global capital markets.

As additional asset classes, providers, and financial ecosystems are integrated, OmniTrade should remain capable of intentionally defining the scope within which autonomous financial intelligence operates.

Rather than asking:

"What assets exist?"

OmniTrade should continually answer:

"Among all eligible opportunities, where should capital be deployed next?"