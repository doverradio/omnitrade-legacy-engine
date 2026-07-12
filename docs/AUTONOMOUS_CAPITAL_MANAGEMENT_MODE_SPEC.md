# AUTONOMOUS_CAPITAL_MANAGEMENT_MODE_SPEC.md

## OmniTrade Legacy Engine — Autonomous Capital Management Mode

**Status:** Proposed architectural ground truth  
**Target:** First governed autonomous live-capital lifecycle on Kraken, then reusable multi-provider architecture  
**Primary objective:** Allow an account owner to pre-authorize a tightly bounded capital mandate so OmniTrade may autonomously produce and execute BUY, SELL, or HOLD decisions without per-order human approval, while preserving Risk Engine authority, fail-closed behavior, explainability, auditability, revocability, and capital limits.

---

# 1. Executive Summary

Autonomous Capital Management Mode (“ACMM”) is an opt-in live-capital operating mode.

It does **not** remove governance.

It changes governance from:

```text
human approval before every order
```

to:

```text
one explicit owner authorization
→ immutable mandate
→ autonomous decisions only inside that mandate
→ Risk Engine approval before every order
→ continuous audit, reconciliation, and revocation
```

The owner authorizes a bounded policy envelope in advance. OmniTrade may then autonomously evaluate opportunities and take BUY, SELL, or HOLD actions only when every mandate, risk, evidence, readiness, and execution invariant is satisfied.

The first production milestone is a complete, tiny, governed Kraken lifecycle:

```text
$25 funded Kraken account
→ autonomous BTC-USD decision
→ at most $5 BUY
→ filled position recorded
→ autonomous monitoring
→ governed SELL when exit policy triggers
→ fill reconciled
→ realized P/L recorded
→ full lifecycle visible in Decision Inspector
```

Profit is not guaranteed. The engineering milestone is a truthful, auditable, autonomous closed loop. Any public claim must describe only verified outcomes.

---

# 2. Permanent Principles

## 2.1 Risk Engine remains final authority

ACMM may remove per-order human approval inside a valid mandate. It may never bypass:

- Risk Engine rules
- kill switches
- freshness gates
- provider readiness
- balance checks
- position limits
- minimum-notional checks
- fee checks
- loss limits
- reconciliation
- audit requirements

A mandate grants permission to evaluate and act within limits. It does not grant permission to override safety.

## 2.2 Explicit owner opt-in

Autonomous mode is disabled by default.

It may be enabled only after the authenticated account owner explicitly accepts:

- provider
- account
- capital ceiling
- per-order ceiling
- asset universe
- strategies
- allowed order sides
- operating schedule
- loss limits
- exit policy
- mandate expiration
- acknowledgement that losses are possible

## 2.3 No hidden autonomy

Every autonomous action must identify:

- mandate ID
- mandate version
- decision ID
- evidence ID
- risk-event ID
- preview ID
- order ID, when submitted
- reconciliation ID
- actor as autonomous system
- deployed software version
- audit correlation ID

## 2.4 Fail closed

Missing, stale, contradictory, or unverifiable state means HOLD / NO ACTION.

## 2.5 Revocable at any time

The owner must be able to:

- pause new entries
- allow exits only
- revoke the mandate
- activate the global kill switch
- flatten eligible positions through a separately governed action
- inspect every autonomous decision

## 2.6 Truthful performance claims

The system must never imply guaranteed profitability.

Marketing or public reporting must distinguish:

- gross P/L
- fees
- net realized P/L
- unrealized P/L
- time period
- capital deployed
- number of completed trades
- whether trades were autonomous
- whether an operator intervened

---

# 3. Definitions

## Autonomous Capital Mandate

A versioned, owner-authorized policy object defining what OmniTrade may do without per-order human approval.

## Mandate Envelope

The complete set of limits and permissions attached to a mandate.

## Autonomous Campaign

A capital campaign operating under one mandate.

## Entry Decision

A governed BUY decision that opens or adds to an allowed position.

## Exit Decision

A governed SELL decision that reduces or closes an allowed position.

## HOLD Decision

A deliberate no-action outcome that is persisted as Decision Intelligence.

## Approval Exemption

A narrowly scoped exemption from per-order approval that is valid only when a decision and order remain inside a currently active mandate.

## Autonomous Lifecycle

The complete chain from mandate authorization through decision, execution, reconciliation, position monitoring, exit, and realized P/L.

---

# 4. Scope

## 4.1 MVP scope

The first ACMM release supports:

- one owner
- one Kraken production account
- spot crypto only
- BTC-USD only
- BUY, SELL, HOLD
- cash-only
- no margin
- no leverage
- no derivatives
- no transfers or withdrawals
- no short selling
- one active autonomous campaign
- maximum $25 authorized campaign capital
- maximum $5 per order
- one open BTC position at a time
- limit or market order only if already supported safely
- explicit stop conditions
- full Decision Intelligence and audit linkage

## 4.2 Out of scope for MVP

- multi-user pooled capital
- external customer funds
- discretionary withdrawals
- leverage
- margin
- options/futures
- high-frequency trading
- cross-exchange arbitrage
- automatic mandate expansion
- autonomous strategy promotion
- autonomous risk-limit changes
- guaranteed-profit logic
- marketing claims generated without verified ledger evidence

---

# 5. Required Architecture

```text
Account Owner
→ Creates Autonomous Capital Mandate
→ Mandate validation
→ Mandate activation
→ Autonomous scheduler/orchestrator
→ Strategy + market evaluation
→ Provider-native execution evidence
→ Risk Engine
→ Decision Record + Snapshot
→ Mandate eligibility gate
→ Autonomous preview
→ Approval exemption validation
→ Submission boundary
→ Provider order
→ Reconciliation
→ Position ledger
→ Exit evaluation
→ Governed SELL
→ Reconciliation
→ Realized P/L
→ Decision Inspector + lifecycle report
```

DecisionRecord remains the canonical decision root.

The Autonomous Capital Mandate is the canonical permission root.

No order is valid unless both roots are present and linked.

---

# 6. Autonomous Capital Mandate

## 6.1 Required fields

- `mandate_id`
- `mandate_version`
- `owner_actor_id`
- `status`
- `authorized_at`
- `activated_at`
- `expires_at`
- `revoked_at`
- `provider`
- `exchange_environment`
- `exchange_connection_id`
- `live_trading_profile_id`
- `paper_account_id`, when required for compatibility
- `capital_campaign_id`
- `base_currency`
- `authorized_capital_usd`
- `max_order_notional_usd`
- `max_open_exposure_usd`
- `max_daily_deployed_usd`
- `max_daily_realized_loss_usd`
- `max_campaign_drawdown_usd`
- `max_consecutive_losses`
- `allowed_assets`
- `allowed_products`
- `allowed_order_sides`
- `allowed_strategy_versions`
- `entry_policy`
- `exit_policy`
- `cooldown_policy`
- `operating_schedule`
- `price_evidence_max_age_seconds`
- `order_timeout_seconds`
- `max_slippage_bps`
- `max_fee_bps`
- `position_limit`
- `approval_policy`
- `reconciliation_policy`
- `kill_switch_policy`
- `owner_acknowledgements`
- `authorization_evidence`
- `created_at`
- `updated_at`
- `audit_correlation_id`

## 6.2 Statuses

- `DRAFT`
- `PENDING_AUTHORIZATION`
- `AUTHORIZED`
- `ACTIVE`
- `PAUSED`
- `EXIT_ONLY`
- `EXPIRED`
- `REVOKED`
- `KILLED`
- `COMPLETED`

## 6.3 Immutable authorization

Once authorized, mandate economic limits and permissions are immutable.

Any material change creates a new version and requires fresh owner authorization.

No autonomous process may increase:

- authorized capital
- per-order cap
- asset scope
- strategy scope
- loss limits
- operating hours
- provider permissions

---

# 7. Approval Model

## 7.1 Human approval remains the default platform policy

Existing human-approval flows remain unchanged.

## 7.2 Mandate-scoped approval exemption

ACMM introduces a new approval result:

```text
APPROVAL_SATISFIED_BY_ACTIVE_MANDATE
```

It is valid only if:

- mandate is ACTIVE
- mandate has not expired
- owner authorization is valid
- order stays inside every mandate field
- DecisionRecord exists
- DecisionSnapshot exists
- execution evidence is valid
- RiskEvent exists and allows action
- preview exists and is ready
- linkage integrity passes
- submission is enabled for ACMM
- no kill switch is active
- reconciliation is healthy
- no unresolved prior order exists
- no pending manual override exists

The exemption must be persisted and visible in the Inspector.

---

# 8. Autonomous Decision Cycle

## 8.1 Scheduler cadence

The scheduler may run no more frequently than justified by strategy and provider limits.

MVP recommendation: every 5–15 minutes.

## 8.2 Cycle stages

1. Load active mandate.
2. Validate mandate state.
3. Load exchange/account readiness.
4. Reconcile balances, orders, and positions.
5. Load provider-native price evidence.
6. Evaluate market/strategy.
7. Produce BUY, SELL, or HOLD.
8. Run Risk Engine.
9. Persist DecisionRecord and Snapshot.
10. Validate mandate eligibility.
11. For HOLD: persist and stop.
12. For BUY/SELL: create preview.
13. Validate approval exemption.
14. Submit only if every invariant passes.
15. Reconcile exchange result.
16. Update position and campaign ledgers.
17. Persist complete audit and linkage.
18. Notify owner of material events.

## 8.3 Idempotency

Every cycle and action must have deterministic idempotency keys.

Repeated workers must not duplicate:

- decisions
- previews
- approvals/exemptions
- submissions
- orders
- fills
- position updates
- P/L events

---

# 9. Entry Policy

The first autonomous BUY may occur only when:

- mandate is ACTIVE
- BTC-USD is allowed
- no BTC position is open
- strategy emits an actionable BUY
- signal confidence meets configured threshold
- exact provider-native Kraken evidence is fresh
- Risk Engine accepts
- $5 order passes Kraken minimums
- estimated fees/slippage pass limits
- sufficient settled USD exists
- no unresolved order exists
- linkage integrity guard passes
- daily/campaign loss limits are not breached
- global/provider/account kill switches are clear

The system must not manufacture a BUY merely to complete a demo.

---

# 10. Position Management

## 10.1 Canonical position record

The system must persist:

- position ID
- mandate ID
- campaign ID
- provider/account
- product
- quantity
- average cost
- fees
- opened timestamp
- current mark
- unrealized P/L
- exit policy
- state
- linked entry decision/order/fills
- linked exit decision/order/fills

## 10.2 Position states

- `OPENING`
- `OPEN`
- `EXIT_PENDING`
- `CLOSING`
- `CLOSED`
- `RECONCILIATION_REQUIRED`
- `UNKNOWN`

Unknown must fail closed.

---

# 11. Exit Policy

The MVP must support at least one deterministic exit policy and may combine several:

- take-profit threshold
- stop-loss threshold
- maximum holding period
- strategy reversal
- risk-triggered exit
- mandate expiration
- owner switches to EXIT_ONLY
- kill-switch flatten instruction, when explicitly configured

The exit decision must pass through:

```text
evidence
→ strategy/exit policy
→ Risk Engine
→ Decision Intelligence
→ preview
→ mandate approval exemption
→ submission
→ reconciliation
```

A profitable exit is not guaranteed.

---

# 12. P/L and Performance Ledger

## 12.1 Required calculations

- gross proceeds
- cost basis
- entry fees
- exit fees
- net realized P/L
- realized return percentage
- holding duration
- slippage
- execution-quality metrics

## 12.2 Claim evidence package

Any claim such as “OmniTrade made money while I slept” must be supported by:

- mandate active during the period
- no manual per-order approval
- no operator order submission
- entry decision/order/fill
- exit decision/order/fill
- timestamps
- realized net P/L after fees
- audit trail
- deployed software version
- explicit statement of capital deployed and risk

---

# 13. Safety Controls

Required controls:

- global kill switch
- mandate kill switch
- provider kill switch
- account kill switch
- strategy kill switch
- entry pause
- exit-only mode
- per-order cap
- total exposure cap
- daily loss cap
- campaign drawdown cap
- consecutive-loss pause
- stale evidence rejection
- price-deviation rejection
- duplicate-order prevention
- unresolved-order block
- reconciliation-health block
- balance-reservation check
- minimum-notional validation
- fee/slippage ceiling
- mandate expiration
- automatic pause on integrity violation

Any Decision Linkage Integrity Guard violation during an autonomous action must prevent submission or force immediate pause before the next action.

---

# 14. Security and Permissions

- Kraken API key must not allow withdrawals.
- Secrets remain encrypted and never appear in UI or logs.
- Owner authorization requires authenticated session and explicit confirmation.
- Mandate activation, pause, revoke, and kill actions are audited.
- Autonomous worker identity must be distinct from human operator identity.
- Every submission records deployed Git commit or build version.
- Rate limiting and replay protection are mandatory.

---

# 15. Reconciliation

Before every new autonomous order:

- fetch provider balances
- fetch open orders
- fetch recent fills
- compare local position
- compare reserved capital
- verify no unknown state

After every submission:

- poll or receive order state
- persist provider order ID
- persist fills
- calculate fees
- update position
- update campaign capital
- update Decision Intelligence
- emit alert on mismatch

No new entry while reconciliation is unresolved.

---

# 16. Decision Intelligence Requirements

Every autonomous cycle creates a DecisionRecord, including HOLD.

Every actionable cycle must link:

- mandate ID/version
- campaign ID
- strategy ID/version
- provider-native evidence ID
- RiskEvent ID
- preview ID
- approval exemption ID
- live order ID
- execution/fill IDs
- position ID
- audit correlation ID
- deployed build version

Inspector must clearly show:

- autonomous mode
- owner pre-authorization
- mandate envelope
- whether action stayed within mandate
- why action was taken
- why no manual approval was needed
- risk verdict
- order/fill outcome
- realized/unrealized P/L
- any intervention

---

# 17. Operator Experience

## 17.1 Mandate creation page

Must show:

- exact capital at risk
- exact per-order cap
- allowed assets
- strategy
- loss limits
- exit policy
- expiration
- warnings
- acknowledgement checkbox
- authorization action

## 17.2 Autonomous operations page

Must show:

- current mandate
- active/paused/exit-only state
- capital authorized/deployed/available
- open position
- latest cycle
- latest decision
- next scheduled evaluation
- cumulative realized P/L
- kill/pause controls
- reconciliation health
- linkage integrity health

## 17.3 Notifications

Notify on:

- mandate activated
- BUY submitted/filled
- SELL submitted/filled
- position closed
- realized loss/profit
- mandate paused/revoked/expired
- risk rejection
- integrity violation
- reconciliation mismatch
- kill switch

---

# 18. API Surface

Proposed read/write contracts:

- `POST /autonomous-capital/mandates`
- `POST /autonomous-capital/mandates/{id}/authorize`
- `POST /autonomous-capital/mandates/{id}/activate`
- `POST /autonomous-capital/mandates/{id}/pause`
- `POST /autonomous-capital/mandates/{id}/exit-only`
- `POST /autonomous-capital/mandates/{id}/revoke`
- `GET /autonomous-capital/mandates`
- `GET /autonomous-capital/mandates/{id}`
- `GET /autonomous-capital/status`
- `GET /autonomous-capital/cycles`
- `GET /autonomous-capital/positions`
- `GET /autonomous-capital/performance`
- internal worker cycle entrypoint
- internal mandate eligibility evaluator

All state-changing endpoints require audit evidence and idempotency.

---

# 19. Database and Migration Guidance

New persistence is expected for:

- autonomous capital mandates
- mandate authorization events
- mandate state events
- autonomous cycle runs
- approval exemption records
- autonomous position lifecycle, if existing models are insufficient
- performance ledger, if existing models are insufficient

Before creating new tables, inspect and reuse:

- capital campaigns
- live trading profiles
- live approval events
- crypto order previews
- live crypto orders
- execution events
- risk events
- Decision Records/Snapshots
- audit logs
- reconciliation records
- position/trade models

A migration must be created only for genuinely missing persistence.

---

# 20. Testing Requirements

## Unit tests

- mandate validation
- mandate version immutability
- eligibility gate
- approval exemption
- cap enforcement
- loss-limit enforcement
- expiration
- pause/revoke/kill
- entry/exit rules
- idempotency
- P/L calculation
- evidence freshness
- reconciliation blocks
- integrity-guard blocks

## Integration tests

- mandate → HOLD
- mandate → BUY preview
- mandate → BUY submit in mocked provider
- fill reconciliation
- position open
- SELL trigger
- SELL submit
- position close
- realized P/L
- provider failure
- stale evidence
- duplicate cycle
- kill switch
- restart recovery

## Production acceptance

- exact active mandate shown
- no withdrawal permission
- $5 cap enforced server-side
- no manual per-order approval
- autonomous actor shown
- full Inspector linkage
- order/fill visible at Kraken
- position and P/L reconcile
- no hidden/manual intervention

---

# 21. Rollout Phases

## Phase A — Architecture and mandate persistence

- repository discovery
- ADR
- mandate model
- authorization lifecycle
- APIs
- audit

## Phase B — Mandate eligibility and approval exemption

- deterministic gate
- Risk Engine integration
- approval exemption record
- fail-closed tests

## Phase C — Autonomous cycle in dry-run

- scheduler
- BUY/SELL/HOLD generation
- preview-only operation
- Decision Intelligence linkage
- no submission

## Phase D — Rehearsed autonomous lifecycle

- mocked/sandbox provider
- entry
- position
- exit
- P/L
- restart recovery

## Phase E — First governed live BUY

- Kraken production
- $5 max
- no manual order approval
- mandate active
- full readiness checks
- reconciliation

## Phase F — Autonomous monitoring and SELL

- exit evaluation
- SELL submission
- close position
- realized P/L

## Phase G — Truthful lifecycle report

- Inspector lifecycle
- autonomous badge
- mandate evidence
- P/L after fees
- exportable verification report

---

# 22. Acceptance Criteria

ACMM MVP is complete only when all are true:

1. Owner explicitly authorizes a bounded mandate.
2. Per-order approval is not required inside the active mandate.
3. Risk Engine remains mandatory.
4. The system autonomously produces BUY, SELL, or HOLD.
5. A live BUY can be submitted only within the $5 cap.
6. The BUY is reconciled and position persisted.
7. Exit policy evaluates autonomously.
8. A live SELL can close the position.
9. Realized net P/L is calculated after fees.
10. Every object is linked in Decision Intelligence.
11. Inspector reconstructs the complete lifecycle.
12. Owner can pause/revoke/kill.
13. No withdrawals, leverage, or unsupported products are possible.
14. Restart/retry cannot duplicate orders.
15. Production diagnostics pass.
16. Claims are generated only from verified ledger evidence.

---

# 23. Required ADR

Create an ADR titled:

**Autonomous Capital Management Through Pre-Authorized Mandates**

It must explain:

- why per-order approval is replaced only inside a bounded mandate
- why Risk Engine authority remains unchanged
- why mandate authorization is not equivalent to unrestricted autonomy
- safety tradeoffs
- rollback and revocation
- future provider support
- regulatory/product implications before external users are accepted

---

# 24. Final Guiding Statement

> Autonomous Capital Management Mode is not the absence of human governance. It is human governance expressed as a precise, revocable, auditable mandate that the system may execute without waking the owner for every compliant decision.
