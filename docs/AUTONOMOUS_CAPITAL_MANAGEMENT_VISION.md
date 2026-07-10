# OmniTrade Autonomous Capital Management Vision

## Purpose

OmniTrade should evolve from a trading bot into an autonomous capital management platform.

The operator deposits capital, defines goals and policies, and the system manages research, strategy selection, risk, execution, accounting, profit handling, compounding, and withdrawals within strict safety and regulatory boundaries.

This document is product vision only. It does not authorize live trading, custody of customer funds, automatic withdrawals, or any activity that requires licensing or legal approval.

---

## 1. Core Product Model: Capital Campaigns

Every deposit or allocation should become a separate **Capital Campaign**.

A campaign should contain:

- campaign name
- owner
- starting capital
- funding source
- exchange or brokerage connection
- selected strategy or strategy pool
- risk profile
- profit target
- stop-loss or drawdown policy
- compounding policy
- withdrawal policy
- status
- current equity
- realized profit
- unrealized profit
- fees
- ROI
- associated trades
- associated positions
- associated decisions
- associated validation runs
- associated research cycles
- complete timeline and audit history

Example:

```text
BTC Campaign #1

Starting Capital: $25.00
Current Equity: $27.43
Realized Profit: $1.80
Unrealized Profit: $0.63
Profit Target: $5.00
Risk Profile: Conservative
Status: Running
```

---

## 2. Campaign Statuses

Suggested statuses:

- DRAFT
- FUNDING_PENDING
- FUNDED
- VALIDATING
- READY
- RUNNING
- PAUSED
- TARGET_REACHED
- WITHDRAWAL_PENDING
- COMPOUNDING
- COMPLETED
- STOPPED
- FAILED
- ARCHIVED

Status transitions must be auditable and deterministic.

---

## 3. Profit Policies

Each campaign should support a configurable **Profit Policy**.

Examples:

### Withdraw Fixed Profit

```text
Starting Capital: $25
Profit Target: $5

When target is reached:
- preserve the original $25
- make $5 available for withdrawal
- optionally restart the campaign
```

### Withdraw a Percentage

```text
When profit reaches 10%:
- withdraw 50% of realized profit
- keep 50% in the campaign
```

### Periodic Income

```text
Every Friday:
- withdraw all realized profit above the protected capital floor
```

### No Withdrawal

```text
- never withdraw automatically
- continue compounding
```

---

## 4. Compounding Policies

Compounding should be a first-class campaign option.

### Full Compounding

When the profit target is reached:

```text
new campaign capital = original capital + realized profit
```

Example:

```text
Cycle 1: $25.00 → $30.00
Cycle 2: $30.00 → $36.00
Cycle 3: $36.00 → $43.20
```

This is only illustrative. Actual returns are not guaranteed.

### Partial Compounding

Example:

```text
Profit earned: $10

- withdraw $5
- reinvest $5
```

### Protected Principal

Example:

```text
Original capital: $100

- never reinvest below $100
- compound only realized profit above $100
```

### Tiered Compounding

Example:

```text
Profit under $5:
- compound 100%

Profit from $5 to $25:
- compound 75%
- withdraw 25%

Profit above $25:
- compound 50%
- withdraw 50%
```

### Compounding Controls

Every compounding policy should include:

- minimum realized profit
- maximum reinvestment amount
- reinvestment percentage
- withdrawal percentage
- minimum cash reserve
- maximum campaign size
- review threshold
- maximum drawdown
- optional human approval
- cooldown period
- tax reserve percentage
- fee reserve percentage

Only realized and settled profit should be eligible for withdrawal or reinvestment.

Unrealized gains must never be treated as withdrawable cash.

---

## 5. Profit Buckets

Mission Control and Capital Ledger should distinguish:

- gross profit
- realized profit
- unrealized profit
- fees
- tax reserve
- withdrawable profit
- compounding profit
- protected principal
- available cash
- invested capital
- pending settlement
- pending withdrawal

Example:

```text
Paper Profit: +$12.43
Live Realized Profit: +$4.82
Withdrawable Profit: $3.10
Compounding Allocation: $1.22
Fee Reserve: $0.30
Tax Reserve: $0.20
```

---

## 6. Withdrawal Workflow

Profit withdrawal must be separated into distinct stages:

```text
Profit Target Reached
→ Close or reduce positions if required
→ Confirm realized profit
→ Confirm settlement
→ Confirm available cash
→ Apply reserves
→ Create withdrawal request
→ Require policy and authorization checks
→ Initiate transfer
→ Reconcile bank receipt
→ Mark campaign cycle complete
```

Suggested statuses:

- NOT_ELIGIBLE
- ELIGIBLE
- REVIEW_REQUIRED
- WITHDRAWAL_QUEUED
- TRANSFER_PENDING
- TRANSFER_CONFIRMED
- TRANSFER_FAILED
- RECONCILIATION_REQUIRED

Automatic withdrawals should remain disabled until legal, custody, banking, security, and operational requirements are fully satisfied.

---

## 7. Research and Capital Allocation

The Research/Evolution layer may recommend campaign strategy allocation.

It may:

- recommend strategy changes
- recommend increased or decreased paper allocation
- quarantine weak strategies
- promote research champions for paper use
- compare campaign performance
- recommend diversification

It must not:

- move live funds without authorization
- chase short-term winners blindly
- promote a strategy from inadequate evidence
- bypass risk limits
- bypass campaign policy
- bypass operator-defined capital limits

---

## 8. Mission Control

Mission Control should answer:

- How much capital is deployed?
- How much profit has been realized?
- How much profit is withdrawable?
- How much is being compounded?
- Which campaigns are active?
- Which campaigns reached target?
- Which campaigns are losing?
- Which strategies are improving?
- Which campaigns are paused or blocked?
- Which withdrawals are pending?
- Is the platform healthy?
- Is the system becoming more reliable?

Suggested top-level sections:

- Total Managed Capital
- Total Realized Profit
- Total Unrealized Profit
- Withdrawable Profit
- Compounding Capital
- Protected Principal
- Current Drawdown
- Campaigns
- Research Champion
- Risk Status
- Withdrawal Queue
- Audit Timeline

---

## 9. Funding Model

For the initial owner-operated version:

- funds remain at a regulated exchange or brokerage
- OmniTrade should not directly custody user money
- OmniTrade records allocations and policies
- deposits should go directly to the exchange or brokerage
- withdrawals should go through the exchange or brokerage
- the system should never store bank credentials or wallet private keys unnecessarily

Stripe should not be used as a substitute for a brokerage or exchange funding rail.

---

## 10. Multi-User Future

A future commercial version may support multiple users and campaigns, but only after legal and regulatory design.

Required future concerns include:

- investment-adviser rules
- broker-dealer rules
- money-transmitter rules
- custody requirements
- customer asset segregation
- KYC and AML
- sanctions screening
- gambling and contest laws
- tax reporting
- disclosures
- suitability
- recordkeeping
- cybersecurity
- incident response
- withdrawal controls
- user consent
- state-by-state licensing

No multi-user custody or pooled-investment design should be implemented without specialist legal counsel.

---

## 11. Competitive Game Concept — Required Redesign

The idea of players staking money in a skill game and secretly using the pooled player funds to launch OmniTrade campaigns must **not** be implemented.

The problems include:

- players would not know how their money is being used
- prize funds could be exposed to trading losses
- customer money could be mixed with company money
- the structure could implicate gambling, custody, securities, money-transmission, consumer-protection, and contest laws
- undisclosed use of funds would create a severe trust and disclosure problem

A compliant future concept would need to be transparent and opt-in.

Possible lawful design directions, subject to legal review:

### A. Segregated Prize Pool

- player stakes remain segregated
- prize money is never invested
- the company funds trading campaigns only from its own revenue or reserves
- users receive clear rules and disclosures

### B. Optional Investment Treasury

- users explicitly opt in
- investment funds are separate from game stakes
- users understand risk
- qualified custody and regulatory requirements are satisfied
- no concealed use of player funds

### C. Revenue-Funded Campaigns

- the platform earns disclosed fees
- company revenue, not player prize money, funds OmniTrade campaigns
- campaign gains and losses belong to the company
- prizes remain protected and segregated

### D. Promotional Treasury

- a fixed company-funded promotional budget launches campaigns
- campaign results may fund future promotions
- player entry funds remain untouched

The safe principle is:

> Player stakes, prize pools, operating capital, and investment capital must remain clearly disclosed, legally structured, and financially segregated.

---

## 12. Product Principles

1. No guaranteed profit claims.
2. No secret use of customer funds.
3. No mixing paper and live performance.
4. No mixing principal and profit.
5. No withdrawal of unrealized gains.
6. No autonomous movement of live money without policy controls.
7. No strategy promotion without adequate evidence.
8. No customer custody without legal approval.
9. No hidden fees.
10. Every capital movement must be auditable.
11. Every campaign must have a kill switch.
12. Every automated action must fail closed.
13. Real-money features must be disabled by default.
14. Campaign policies must be explainable.
15. Research may recommend; Risk remains final authority.

---

## 13. Proposed Build Sequence

### Phase A — Campaign Foundation

- Capital Campaign model
- campaign CRUD
- campaign lifecycle
- campaign detail page
- campaign timeline
- links to trades, decisions, validation runs, and research

### Phase B — Profit Policies

- fixed target
- percentage target
- periodic target
- protected principal
- profit eligibility
- realized vs unrealized rules

### Phase C — Compounding

- full compounding
- partial compounding
- tiered compounding
- reserve controls
- reinvestment cycle history

### Phase D — Withdrawal Queue

- target reached
- settlement verification
- withdrawal eligibility
- review queue
- manual approval
- reconciliation

### Phase E — Automated Withdrawal

Only after:

- legal review
- secure banking/exchange integration
- custody analysis
- authorization hardening
- fraud controls
- testing
- operational runbook
- operator approval

### Phase F — Multi-User Platform

Only after specialist legal and regulatory design.

---

## 14. Near-Term MVP

The next owner-operated MVP should support:

- one operator
- one Coinbase production account
- multiple campaigns
- campaign-specific starting capital
- paper/live separation
- profit targets
- manual compounding
- manual withdrawal queue
- no automatic bank transfer
- no pooled customer funds
- no game stakes
- no multi-user custody
- complete audit trail

---

## 15. Final Vision

OmniTrade should become:

> An autonomous capital management platform where an operator defines goals, risk limits, profit policies, compounding rules, and withdrawal preferences, while the system manages research, strategy selection, execution, accounting, and monitoring within strict safety and legal boundaries.

The system should make capital management simpler, more transparent, and more disciplined.

It must never create the illusion that profit is guaranteed.
