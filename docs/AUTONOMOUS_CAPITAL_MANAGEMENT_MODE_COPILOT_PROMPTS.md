# AUTONOMOUS_CAPITAL_MANAGEMENT_MODE_COPILOT_PROMPTS.md

## OmniTrade Legacy Engine — Sequential Copilot Build Prompts

These prompts must be executed in order.

Do not skip phases.

Do not combine prompts unless the prior phase has been reviewed, committed, deployed, and validated.

Each prompt assumes `docs/AUTONOMOUS_CAPITAL_MANAGEMENT_MODE_SPEC.md` is architectural ground truth.

---

# Prompt 1 of 10 — Architecture Discovery and Gap Analysis

```text
Continue OmniTrade as Lead Architect and Lead Implementer.

Read and obey:

1. docs/PROJECT_CONSTITUTION.md
2. docs/PROJECT_VISION.md
3. docs/AUTONOMOUS_CAPITAL_MANAGEMENT_MODE_SPEC.md
4. docs/SYSTEM_ARCHITECTURE.md
5. docs/RISK_ENGINE.md
6. docs/DECISION_INTELLIGENCE_ENGINE.md
7. docs/API_CONTRACTS.md
8. docs/DATABASE_SCHEMA.md
9. docs/SECURITY_AND_SAFETY.md
10. docs/PROJECT_STATUS.md
11. all relevant ADRs
12. current repository implementation
13. permanent Operator Command Format / Standard VPS Operator Workflow

Objective:

Perform a complete architecture and repository gap analysis for Autonomous Capital Management Mode.

Do not implement code.

Map existing reusable components for:

- capital campaigns
- live trading profiles
- approvals
- previews
- live orders
- provider-native price evidence
- Risk Engine
- reconciliation
- positions
- execution events
- Decision Intelligence
- audit
- kill switches
- workers/schedulers
- notifications

Determine:

- which existing models/services can be reused;
- which persistence is missing;
- whether human approval is hard-coded or policy-driven;
- exact live submission boundary;
- exact places where mandate-scoped approval exemption must be evaluated;
- exact places where autonomous actor/build provenance must be recorded;
- exact migration requirements;
- required ADRs;
- safest phased implementation plan.

Do not weaken existing approval behavior.

Human approval remains default unless a future active mandate explicitly satisfies an exemption.

Return:

1. full call graph;
2. data-model map;
3. reuse map;
4. missing components;
5. migration plan;
6. API plan;
7. worker plan;
8. safety risks;
9. recommended phase sequence;
10. exact Phase 1 implementation scope.

No code changes.
Stop after reporting.
```

---

# Prompt 2 of 10 — ADR, Mandate Model, and Authorization Lifecycle

```text
Continue OmniTrade as Lead Architect and Lead Implementer.

Read and obey all governing docs, especially:

- docs/AUTONOMOUS_CAPITAL_MANAGEMENT_MODE_SPEC.md
- the approved output from Prompt 1
- Project Constitution
- Risk Engine
- Decision Intelligence
- Security and Safety

Objective:

Implement the Autonomous Capital Mandate foundation.

Required:

- ADR: Autonomous Capital Management Through Pre-Authorized Mandates;
- mandate persistence;
- mandate versioning;
- owner authorization evidence;
- immutable authorized economic envelope;
- statuses:
  DRAFT, PENDING_AUTHORIZATION, AUTHORIZED, ACTIVE, PAUSED,
  EXIT_ONLY, EXPIRED, REVOKED, KILLED, COMPLETED;
- create/read/authorize/activate/pause/exit-only/revoke APIs;
- idempotency;
- audit events;
- secret-safe output;
- authorization acknowledgements;
- owner identity;
- provider/account/profile/campaign linkage;
- expiration;
- no withdrawals;
- no leverage;
- no autonomous limit expansion.

Do not implement autonomous order submission.

Do not implement approval exemption yet.

Add migrations only if required.

Add focused backend tests and minimal read-only frontend/operator surface only if the existing product architecture requires it for authorization review.

Return:

- files changed;
- schema changes;
- tests;
- migration command only if migration exists;
- commit recommendation;
- local command;
- backend-only VPS command;
- Vercel smoke checklist.
```

---

# Prompt 3 of 10 — Mandate Eligibility Gate and Approval Exemption

```text
Continue OmniTrade as Lead Architect, Risk Engineer, and Lead Implementer.

Objective:

Implement the deterministic mandate eligibility evaluator and mandate-scoped approval exemption.

The existing human approval flow must remain the default.

Introduce approval result:

APPROVAL_SATISFIED_BY_ACTIVE_MANDATE

This result is valid only when all ACMM spec conditions pass.

Required checks:

- mandate ACTIVE;
- owner authorization valid;
- not expired/revoked/paused/killed;
- provider/environment/account/profile/campaign match;
- product and side allowed;
- strategy/version allowed;
- requested and approved notional within caps;
- total exposure within cap;
- daily deployment/loss/drawdown within limits;
- fresh provider-native evidence;
- Risk Event exists and permits action;
- DecisionRecord and Snapshot exist;
- preview ready;
- audit correlation exists;
- linkage integrity healthy;
- reconciliation healthy;
- no unresolved order;
- kill switches clear;
- submission setting explicitly permits ACMM;
- no withdrawal capability;
- no unsupported leverage/margin.

Persist an immutable approval-exemption record.

Do not submit live orders.

Add focused tests proving every failure fails closed and existing manual approval behavior is unchanged.

Stop after reporting.
```

---

# Prompt 4 of 10 — Autonomous Cycle Orchestrator in Preview-Only Mode

```text
Continue OmniTrade as Lead Architect and Lead Implementer.

Objective:

Implement the autonomous capital cycle orchestrator in preview-only mode.

No live submission.

Cycle:

1. load active mandate;
2. validate mandate;
3. reconcile;
4. load exact provider-native evidence;
5. evaluate approved strategy/exit policy;
6. produce BUY, SELL, or HOLD;
7. run Risk Engine;
8. persist DecisionRecord and Snapshot;
9. validate mandate eligibility;
10. HOLD: stop with complete evidence;
11. BUY/SELL: create preview;
12. validate approval exemption;
13. stop before submission;
14. persist cycle result and audit.

Required:

- cycle-run persistence;
- deterministic idempotency key;
- autonomous actor identity;
- deployed build/Git version;
- restart-safe operation;
- one active cycle per mandate;
- no overlapping orders;
- no duplicate decisions/previews;
- Decision Inspector linkage;
- linkage-integrity enforcement;
- operator diagnostics.

Add CLI or worker entrypoint for one cycle and scheduled operation.

Keep submission disabled.

Test BUY, SELL, HOLD, stale evidence, risk rejection, paused mandate, expired mandate, reconciliation unknown, and duplicate cycle.

Stop after reporting.
```

---

# Prompt 5 of 10 — Position Ledger and Deterministic Exit Policy

```text
Continue OmniTrade as Lead Architect, Portfolio Engineer, and Lead Implementer.

Objective:

Implement or extend the canonical autonomous position lifecycle and deterministic exit policy.

Reuse existing position/trade/execution models where sufficient.

Required:

- position states:
  OPENING, OPEN, EXIT_PENDING, CLOSING, CLOSED,
  RECONCILIATION_REQUIRED, UNKNOWN;
- mandate/campaign/provider/product linkage;
- entry decision/order/fill linkage;
- quantity;
- average cost;
- fees;
- current mark;
- unrealized P/L;
- exit decision/order/fill linkage;
- realized P/L;
- restart recovery;
- reconciliation ownership.

MVP exit policies:

- take-profit;
- stop-loss;
- maximum holding period;
- strategy reversal;
- risk-triggered exit;
- mandate expiration;
- EXIT_ONLY mode.

No live submission yet.

Add deterministic P/L tests and full simulated entry-to-exit lifecycle tests.

Stop after reporting.
```

---

# Prompt 6 of 10 — Full Rehearsal With Mocked/Sandbox Execution

```text
Continue OmniTrade as Lead Reliability Engineer and Lead Implementer.

Objective:

Prove the complete autonomous lifecycle without real capital.

Execute in mocked provider or supported sandbox:

mandate
→ autonomous BUY decision
→ evidence
→ Risk
→ preview
→ mandate exemption
→ submission boundary
→ mocked/sandbox fill
→ position OPEN
→ autonomous exit trigger
→ SELL
→ fill
→ position CLOSED
→ realized P/L
→ Inspector reconstruction

Required:

- no manual per-order approval;
- one-time mandate authorization;
- no duplicate orders after restart;
- idempotent reconciliation;
- provider timeout handling;
- partial fill handling if supported;
- fee accounting;
- linkage integrity;
- audit correlation;
- kill-switch tests;
- mandate pause/revoke tests;
- truthful lifecycle report.

Do not enable Kraken production submission.

Return all blockers before first governed live BUY.

Stop after reporting.
```

---

# Prompt 7 of 10 — Production Readiness for First Autonomous Kraken BUY

```text
Continue OmniTrade as Lead Architect, Risk Engineer, Security Engineer, and Production Readiness Reviewer.

Objective:

Determine whether the system is ready for one autonomous Kraken BTC-USD BUY of at most $5 under an active owner-authorized mandate.

This prompt is readiness review first.

Do not submit an order unless every required invariant is proven and the prompt explicitly reaches the approved execution stage.

Verify:

- active mandate;
- $25 or lower authorized campaign capital;
- $5 maximum order cap;
- BTC-USD only;
- BUY/SELL/HOLD allowed as configured;
- one open position maximum;
- Kraken key cannot withdraw;
- exact production connection;
- balances;
- provider-native evidence;
- Risk policy;
- kill switches;
- reconciliation;
- Decision linkage;
- mandate exemption;
- submission boundary;
- service/build provenance;
- notifications;
- order idempotency;
- restart recovery;
- operator emergency controls.

Produce a go/no-go report.

If NO-GO: stop and identify exact blockers.

If GO: produce one exact combined VPS command that performs one autonomous cycle with submission still disabled first, followed by a separate explicitly labeled live command that may be run only after human review of the readiness output.

No per-order approval object may be created; mandate exemption must be used.

Do not claim profit.

Stop after reporting.
```

---

# Prompt 8 of 10 — First Governed Autonomous Live BUY

```text
Continue OmniTrade as Production Execution Reviewer.

Objective:

Execute exactly one governed autonomous Kraken BTC-USD BUY under the active mandate, with notional no greater than $5.

Preconditions must already be proven by Prompt 7.

Required:

- no manual per-order approval;
- active mandate exemption;
- Risk Engine acceptance;
- provider-native evidence;
- exact $5-or-less cap;
- submission enabled only for the bounded cycle;
- no other products;
- no second order;
- full audit and Decision Intelligence;
- reconciliation to Kraken;
- position OPEN;
- owner notification.

Immediately disable or return submission posture to the intended safe autonomous configuration after the bounded action, without deactivating the mandate unless policy requires it.

Return:

- decision ID;
- evidence ID;
- RiskEvent ID;
- preview ID;
- mandate exemption ID;
- provider order ID;
- fill ID;
- quantity;
- price;
- fees;
- position ID;
- exact balance changes;
- Inspector URL;
- integrity verifier result.

If any uncertainty exists, fail closed and do not submit.

Stop after reporting.
```

---

# Prompt 9 of 10 — Autonomous Monitoring and Governed SELL

```text
Continue OmniTrade as Lead Portfolio Engineer and Production Execution Reviewer.

Objective:

Operate the existing autonomous position until a deterministic exit policy produces SELL, then close the position without per-order human approval.

Do not force a profitable exit.

Do not alter thresholds merely to create a sale.

Required:

- continuous provider reconciliation;
- fresh mark;
- deterministic exit policy;
- Risk Engine review;
- DecisionRecord/Snapshot;
- mandate eligibility;
- approval exemption;
- SELL preview;
- exact position quantity validation;
- submission;
- fill reconciliation;
- position CLOSED;
- realized net P/L after all fees;
- audit/linkage integrity;
- owner notification;
- Inspector lifecycle.

If no exit condition is met, persist HOLD and report status.

Do not guarantee completion time or profit.

Stop after reporting.
```

---

# Prompt 10 of 10 — Verified Autonomous Lifecycle Report and Product Acceptance

```text
Continue OmniTrade as Lead Architect, Auditor, Product Engineer, and Production Acceptance Reviewer.

Objective:

Produce the final verified acceptance package for Autonomous Capital Management Mode.

Review the complete lifecycle:

owner authorization
→ active mandate
→ autonomous entry decision
→ Risk
→ mandate approval exemption
→ live BUY
→ fill
→ position monitoring
→ autonomous exit decision
→ live SELL
→ fill
→ realized P/L
→ Decision Intelligence
→ audit
→ integrity verification

Required acceptance report:

- exact mandate and version;
- whether any per-order human approval occurred;
- whether any manual order submission occurred;
- capital authorized;
- capital deployed;
- entry/exit timestamps;
- gross P/L;
- all fees;
- net realized P/L;
- return percentage;
- maximum drawdown;
- all IDs and Inspector URLs;
- software build versions;
- integrity verifier outputs;
- reconciliation proof;
- intervention history;
- truthful public claim wording.

Generate claim wording only from facts.

Examples:

If profitable:
“Under a pre-authorized $25 mandate, OmniTrade autonomously bought and sold BTC on Kraken while no per-order human approval was provided, realizing $X net after fees.”

If unprofitable:
“OmniTrade completed its first fully autonomous governed BTC lifecycle on Kraken, with a verified net result of -$X after fees.”

Do not conceal losses.

Determine whether ACMM MVP is production-accepted.

List all remaining work before allowing any external user or customer capital.

Stop after reporting.
```

---

# Permanent Prompt Rules

Every prompt must preserve:

- Risk Engine final authority
- fail-closed behavior
- owner opt-in
- mandate immutability
- revocability
- no withdrawals
- no leverage
- no unsupported assets
- exact capital caps
- Decision Intelligence linkage
- auditability
- reconciliation
- idempotency
- truthful claims

Operator commands must:

- use combined blocks where practical;
- use `&&` for fail-fast operations;
- use `;` only when later diagnostics must continue;
- end with a trailing blank line;
- exclude frontend commands from VPS blocks;
- include Alembic upgrade only when an approved migration exists;
- never include `exit`.
