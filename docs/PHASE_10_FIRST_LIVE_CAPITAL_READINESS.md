# Phase 10 — First Live Capital Readiness and Controlled Coinbase Validation

**Status:** Proposed for approval  
**Primary objective:** Complete the shortest safe, evidence-backed path from the current production baseline to one supervised, tightly capped live Coinbase crypto trade, followed by reconciliation and immediate return to a disabled live-submission state.

---

## 1. North Star

OmniTrade is an **Autonomous Capital Management Platform**.

Its purpose is not merely to execute trades. Its purpose is to grow capital while preserving capital through explainable, governed, evidence-based decision systems.

Trading is the first capital-deployment mechanism. Future mechanisms may include equities, options, prediction markets, treasury products, lending, yield strategies, arbitrage, and other AI-discovered opportunities, but no such expansion is part of this phase.

This phase exists to prove that OmniTrade can safely move from simulated capital operation to a single controlled real-capital action without weakening:

- Risk Engine final authority
- human approval
- auditability
- reconciliation
- explainability
- capital accounting
- kill-switch behavior
- fail-closed behavior
- production observability

---

## 2. Current Production Baseline

The following are treated as already operational unless repository or production evidence disproves them:

- Mission Control
- continuous orchestration worker
- paper trading
- evidence-based profit metrics
- Capital Ledger
- Capital Campaigns
- Profit Policies
- Profit Cycles
- migration `20260710_0026`
- encrypted exchange credentials
- exchange readiness
- live crypto order submission feature flag
- `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`
- paper-first operating mode
- human approval gates
- Risk Engine final authority

Known defect:

- Research/Evolution persistence can violate the foreign key between `research_laboratory_runs` and `research_agent_activity` because child rows may flush before their parent laboratory run is persisted.

---

## 3. Governing Principles

### 3.1 Shortest Safe Path

Whenever there is a choice between adding a feature and completing the path to verified live-capital deployment, choose the smallest safe step that advances production readiness.

### 3.2 Evidence Over Assumption

Every material production claim must be backed by observable evidence.

A successful function call is not sufficient proof by itself. Readiness must be demonstrated through persisted records, audit evidence, reconciliation results, provider acknowledgements, health checks, and operator-visible state.

### 3.3 Human Approval Remains Mandatory

No automated process may:

- enable live submission
- approve the first live order
- increase the live order cap
- retry an ambiguous live order automatically
- initiate a second live order
- promote a research result directly into live capital
- move capital between campaigns without governed authorization

### 3.4 Risk Engine Remains Final Authority

No Coinbase connector, campaign service, research service, orchestration worker, operator UI, or administrative route may bypass the Risk Engine.

### 3.5 Fail Closed

On missing, stale, ambiguous, inconsistent, or unavailable evidence, the system must not submit an order.

---

## 4. Architectural Layer Model

This phase uses the following operational layer model while preserving the four permanent core engines:

```text
Mission Control / Operator Control Plane
────────────────────────────────────────
Decision Intelligence
Research / Evolution
Portfolio Intelligence / Capital Management
Execution and Reconciliation
Risk and Governance
Strategy
Market Intelligence
Infrastructure and Security
```

Mission Control is not a fifth engine. It is the operator-facing control plane spanning the existing architecture.

---

## 5. Production Readiness Ladder

This ladder is a living checklist. Every implementation prompt must identify the rung or rungs it advances.

### Foundation

- [x] Infrastructure deployed
- [x] Market data operational
- [x] Strategy framework operational
- [x] Paper trading operational
- [x] Risk Engine operational
- [x] Mission Control operational
- [x] Continuous orchestration operational

### Capital Management

- [x] Capital Ledger
- [x] Capital Campaigns
- [x] Profit Policies
- [x] Profit Cycles
- [x] Evidence-based profit metrics
- [x] Encrypted exchange credentials
- [x] Exchange readiness model

### Production Integrity

- [ ] Research persistence sequencing fixed
- [ ] Research failure containment verified
- [ ] Execution path dependency audit complete
- [ ] Coinbase connector contract and implementation validated
- [ ] Live-order idempotency and duplicate prevention verified
- [ ] Kill-switch behavior verified against live submission path
- [ ] Ambiguous provider-response handling verified
- [ ] Production rollback and disable procedure documented

### Controlled Live Validation

- [ ] Fresh Coinbase readiness evidence recorded
- [ ] Production dry run completed
- [ ] Operator approval evidence recorded
- [ ] One live BTC-USD market buy submitted at a maximum of $5
- [ ] Submission flag immediately disabled after the attempt
- [ ] Provider acknowledgement persisted
- [ ] Fill or terminal order state reconciled
- [ ] Fees and resulting balances reconciled
- [ ] Capital Ledger updated from provider evidence
- [ ] Campaign and Profit Cycle state remain internally consistent
- [ ] Audit and Decision Intelligence evidence complete
- [ ] Post-trade incident review completed
- [ ] Phase exit review approved

### Explicitly Deferred

- [ ] autonomous campaign launch
- [ ] automatic compounding
- [ ] automatic withdrawals
- [ ] multiple simultaneous live campaigns
- [ ] dynamic live capital allocation
- [ ] research-driven live promotion
- [ ] fully autonomous capital management

---

## 6. Phase Scope

### 6.1 In Scope

1. Repair or cleanly contain the Research/Evolution persistence defect.
2. Verify that Research/Evolution cannot reach live order submission.
3. Audit the complete live execution dependency path.
4. Validate Coinbase readiness, preview, submission, acknowledgement, and reconciliation boundaries.
5. Prove live-order idempotency and duplicate prevention.
6. Prove fail-closed behavior for stale readiness evidence, kill switches, risk rejection, reconciliation uncertainty, and ambiguous provider responses.
7. Complete a production dry run.
8. Submit exactly one operator-approved live BTC-USD market buy, capped at $5.
9. Immediately disable live submission following the attempt.
10. Reconcile the order, fill, fees, balances, Capital Ledger, campaign state, audit trail, and Decision Intelligence evidence.
11. Produce a Phase 10 completion report with explicit pass/fail evidence.

### 6.2 Out of Scope

- new strategies
- new research agents
- new asset classes
- options
- leverage or margin
- derivatives
- autonomous strategy promotion
- autonomous live enablement
- autonomous retries of uncertain orders
- automated withdrawals
- automated compounding
- multi-user custody
- pooled funds
- increasing the order cap above $5
- recurring live trading
- a second live trade
- feature work unrelated to the readiness ladder

---

## 7. Workstreams

## Workstream A — Documentation and Ground-Truth Reconciliation

Confirm the current repository and production implementation against this phase spec.

Required outputs:

- current implementation inventory
- current migration head
- current environment-flag inventory without revealing secret values
- exact live-order call graph
- exact reconciliation call graph
- identified gaps or contradictions
- updated readiness ladder

No code changes should occur until this inventory is complete.

---

## Workstream B — Research Persistence Integrity and Isolation

Repair the foreign key sequencing defect involving:

- `research_laboratory_runs`
- `research_agent_activity`

Required behavior:

- parent laboratory run persists before child activity rows
- transaction boundaries are explicit
- failures roll back cleanly
- the SQLAlchemy session is usable after rollback
- retries do not duplicate completed work
- worker continues safely after an isolated research failure
- research cannot invoke live execution

Acceptable implementation approaches may include:

- explicit parent `flush()`
- relationship-driven dependency ordering
- narrowly scoped `no_autoflush`
- database uniqueness constraints when required for idempotency

A blanket suppression of autoflush is not acceptable.

---

## Workstream C — Live Execution Safety Audit

Trace the full call path from operator action to Coinbase submission.

The audit must prove:

```text
Operator Approval
→ Fresh Readiness Evidence
→ Risk Engine Approval
→ Kill-Switch Check
→ Order Cap Check
→ Preview / Intent Creation
→ Idempotency Reservation
→ Coinbase Submission
→ Provider Acknowledgement
→ Persistence
→ Reconciliation
→ Capital Ledger / Campaign Accounting
→ Audit and Decision Evidence
```

Required proofs:

- no alternate call path bypasses Risk
- no research service can submit
- no worker can enable live mode
- no UI-only enforcement of the $5 cap
- duplicate clicks cannot create duplicate orders
- retries after timeouts do not blindly resubmit
- ambiguous response state enters reconciliation-required status
- missing audit persistence prevents completion
- missing provider acknowledgement cannot be represented as a successful order

---

## Workstream D — Dry Run and Failure Drills

With live submission disabled:

```env
LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false
LIVE_CRYPTO_DRY_RUN_ENABLED=true
LIVE_CRYPTO_MAX_ORDER_USD=5
```

Complete:

- end-to-end dry run
- stale readiness evidence rejection
- global kill-switch rejection
- account/campaign pause rejection if applicable
- Risk Engine rejection
- duplicate submission attempt
- provider timeout simulation
- ambiguous acknowledgement simulation
- reconciliation retry
- audit-write failure simulation
- Capital Ledger write failure simulation
- service restart during reconciliation
- rollback and operator recovery drill

All drills must produce operator-visible, auditable outcomes.

---

## Workstream E — Controlled First Live Trade

Preconditions:

- all mandatory readiness ladder items above the live-trade rung pass
- full regression suite passes
- production health is green
- no unresolved critical/high security issue exists
- Research/Evolution is either fixed and verified or positively disabled/isolated
- live order cap is $5 server-side
- global and relevant account/campaign kill switches are clear
- Coinbase credentials and permissions are verified
- balance and market evidence are fresh
- operator explicitly approves the exact order
- rollback procedure is available

Permitted order:

```text
Venue: Coinbase
Pair: BTC-USD
Side: BUY
Type: MARKET
Maximum notional: $5.00
Count: exactly one submission attempt
```

Immediately after the attempt:

1. Set `LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false`.
2. Persist the disable action and operator identity.
3. Reconcile provider order state.
4. Reconcile fills, fees, and balances.
5. Confirm no duplicate order exists.
6. Confirm Capital Ledger and campaign accounting.
7. Confirm full audit and Decision Intelligence evidence.
8. Do not submit another live order in this phase.

---

## Workstream F — Post-Trade Review and Exit Report

The phase completion report must include:

- exact commit and deployment identifiers
- migration head
- environment-mode evidence without secret values
- test results
- dry-run evidence
- operator approval record
- Coinbase client order identifier
- provider order identifier
- order status timeline
- fill details
- fee details
- pre/post balance reconciliation
- Capital Ledger entries
- campaign and Profit Cycle state
- risk decision
- kill-switch state
- audit entries
- Decision Record / evidence links
- duplicate-order query result
- unresolved issues
- final recommendation

---

## 8. Mandatory Test Matrix

### Backend

- unit tests for research persistence sequencing
- rollback and retry tests
- orchestration containment tests
- execution authorization tests
- Risk Engine bypass tests
- kill-switch tests
- order cap tests
- idempotency tests
- ambiguous provider response tests
- reconciliation tests
- Capital Ledger accounting tests
- audit transaction tests

### Frontend

- live mode visibly distinct from paper mode
- exact order summary before approval
- confirmation friction appropriate for real capital
- live submission unavailable when readiness is unknown
- duplicate click prevention
- pending/ambiguous/reconciliation-required states
- clear display that submission was disabled after the attempt

### Integration

- dry run end-to-end
- provider client mocked success
- provider client mocked rejection
- provider client timeout before acknowledgement
- provider client timeout after likely acknowledgement
- service restart during pending reconciliation
- exact-one-order invariant
- no research-to-execution path

### Full Regression

Run the complete backend, frontend, lint, build, migration, and deployment validation suites defined by the repository.

Targeted tests alone are insufficient for Phase 10 exit.

---

## 9. Security Requirements

- Never print or expose Coinbase secrets.
- Never commit credentials.
- Never log full signed requests.
- Redact provider payload fields that may contain sensitive information.
- Keep encryption-at-rest behavior intact.
- Confirm least-privilege Coinbase API permissions.
- Withdrawal permission must not be required for this phase.
- Automatic withdrawal remains disabled.
- No wallet private keys are stored.
- No bank credentials are stored.
- Live flags must default to disabled when absent or invalid.

---

## 10. Exit Criteria

Phase 10 is complete only when all of the following are true:

1. The Research/Evolution persistence issue is fixed or positively isolated with evidence.
2. The full live execution path has been audited and no Risk Engine bypass exists.
3. The $5 maximum is enforced server-side.
4. Idempotency prevents duplicate live orders.
5. Ambiguous provider responses fail closed into reconciliation.
6. Production dry run and failure drills pass.
7. Exactly one operator-approved live BTC-USD buy of no more than $5 has been attempted.
8. Live submission is disabled immediately afterward.
9. Provider order state, fill, fees, and balances are reconciled.
10. Capital Ledger, campaign state, audit trail, and Decision Intelligence evidence agree.
11. No second live order was submitted.
12. Full regression passes.
13. A completion report is reviewed and explicitly approved by the operator.

---

## 11. Phase Success Definition

Success is not defined as making a profit.

Success is:

> OmniTrade safely submitted, recorded, reconciled, explained, and accounted for exactly one tightly capped real-capital order under explicit human control, then returned itself to a disabled live-submission posture with complete evidence and no ambiguity about what occurred.
