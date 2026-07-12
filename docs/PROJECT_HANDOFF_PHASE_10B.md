# OmniTrade Legacy Engine
# PROJECT HANDOFF — PHASE 10B
# NO DRIFT

## ROLE

You are continuing as Lead Architect of OmniTrade Legacy Engine.

Treat the current repository implementation as operational ground truth.

Never replace implemented architecture with older MVP documents.

Verify repository state before making architectural claims.

The Constitution remains the highest governing document.

---

# PROJECT MISSION

OmniTrade is NOT a trading bot.

It is becoming:

> An Autonomous Capital Management Platform whose accumulated investment knowledge compounds over decades while capital deployment remains governed by evidence, explainability, auditability, Risk Engine authority, and human oversight.

Execution is only one subsystem.

Research is the long-term competitive advantage.

---

# CURRENT ARCHITECTURAL PILLARS

The project currently consists of five permanent pillars.

## 1. Execution Platform

- Execution Provider Layer
- Coinbase provider
- Kraken provider
- Provider Registry
- Capital Ledger
- Accounting
- Reconciliation

Only Execution Providers may submit trades.

---

## 2. Governance Platform

- Risk Engine
- Human Approval
- Mission Control
- Audit
- Feature Flags

Risk Engine remains final authority.

Human approval remains mandatory.

No AI has execution authority.

---

## 3. Decision Intelligence

- Decision Records
- Explainability
- Evidence
- Decision Quality
- Research Persistence
- Laboratory
- Strategy Evolution

Decision Intelligence stores permanent investment memory.

---

## 4. AI Research Organization

This is now permanent architectural direction.

Researchers:

- generate hypotheses
- critique one another
- replay
- backtest
- paper validate
- recommend promotions

Researchers never:

- execute trades
- move capital
- bypass Risk
- bypass Human Approval
- rewrite history

Long-term subsystems include:

- Research Arena
- Laboratories
- Knowledge Graph
- Meta Research
- Promotion Pipeline
- Strategy Versioning
- Research Budget Engine

This is architectural direction only.

Do not implement until explicitly instructed.

---

## 5. Autonomous Capital Management

Future capital allocation platform including:

- Campaigns
- Capital Allocation
- Profit Policies
- Compounding
- Multi-provider execution

---

# CURRENT PRODUCTION STATUS

Current provider:

Kraken

Coinbase remains supported through the Execution Provider Layer but is no longer on the critical path.

Current production blocker:

Production Kraken authentication returns:

EAPI:Invalid signature

However...

A clean-room authentication implementation succeeds against Kraken using the exact same:

- VPS
- API key
- API secret
- IP restriction
- endpoint

Therefore:

Credentials are valid.

Kraken account is valid.

Permissions are valid.

The remaining defect exists inside the production authentication implementation.

This has been proven.

---

# IMPORTANT LESSON

The clean-room verifier is now the canonical authentication oracle.

Future authentication work must compare:

clean-room

↓

existing verifier

↓

production provider

Do NOT make speculative authentication changes.

Find the first concrete divergence.

---

# SHORT-TERM PRIORITY

1.
Fix production Kraken authentication using the clean-room implementation as reference.

2.
Complete production initialization.

3.
Deposit approximately $25 USD.

4.
Generate a $5 BTC preview.

5.
Human approval.

6.
Production-equivalent dry run.

7.
Review evidence.

8.
Enable submission.

9.
First live $5 BTC purchase.

10.
Fill reconciliation.

11.
Capital Ledger verification.

12.
Mission Control verification.

---

# AFTER FIRST LIVE TRADE

Immediately:

- remove temporary diagnostics
- protect Mission Control behind authentication
- complete first-live-trade evidence package

Only then begin implementation of the AI Research Organization.

---

# IMPLEMENTATION ORDER

Phase 10B

- production auth fix
- initialization
- funding
- preview
- approval
- dry run
- first trade

Phase 11

Research Governance

- immutable hypotheses
- experiment lineage
- strategy versioning
- recommendation lifecycle

Phase 12

Research Arena

Phase 13

Knowledge Graph

Phase 14

Meta Research

Phase 15

Research Budget Engine

---

# NO DRIFT RULES

Never:

- remove Risk Engine authority
- remove Human Approval
- allow AI to execute trades
- allow Research to execute trades
- bypass replay
- bypass backtesting
- bypass paper validation
- bypass promotion review
- rewrite history
- delete evidence

Evidence remains append-only.

Strategies remain immutable.

Knowledge compounds forever.

---

# CURRENT ENGINEERING PRIORITY

Do not broaden architecture.

Do not build new AI systems.

Finish the first authenticated production workflow.

The shortest safe path remains:

Fix production auth

↓

Initialize

↓

Fund Kraken

↓

Preview

↓

Approve

↓

Dry run

↓

First live trade

Only after this milestone should Research Arena implementation begin.

END OF HANDOFF