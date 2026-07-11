# Copilot Prompt Pack — Phase 10: First Live Capital Readiness and Controlled Coinbase Validation

## How to Use This Prompt Pack

Use GitHub Copilot Chat in agent mode with full repository context.

Complete one prompt at a time, in order.

After each prompt:

1. Stop.
2. Summarize files changed.
3. Summarize tests added or changed.
4. Show exact validation commands run.
5. Report pass/fail results.
6. Identify any conflict with project documentation.
7. Wait for operator approval before continuing.

Do not combine prompts.

Do not implement unrelated features.

---

# Standing Instruction Block

Paste this before Prompt 10.1:

```text
You are implementing Phase 10 of OmniTrade Legacy Engine: First Live Capital Readiness and Controlled Coinbase Validation.

Before changing code, read these files in full:

1. docs/PROJECT_CONSTITUTION.md
2. docs/PROJECT_STATUS.md
3. docs/MASTER_PRODUCT_ROADMAP.md
4. docs/SYSTEM_ARCHITECTURE.md
5. docs/RISK_ENGINE.md
6. docs/SECURITY_AND_SAFETY.md
7. docs/DECISION_INTELLIGENCE_ENGINE.md
8. docs/AUTONOMOUS_CAPITAL_MANAGEMENT_VISION.md
9. docs/PHASE_10_FIRST_LIVE_CAPITAL_READINESS.md
10. all existing live trading, Coinbase, exchange readiness, Capital Ledger, campaign, profit policy, profit cycle, research/evolution, orchestration, audit, and reconciliation implementation files and tests

North Star:
OmniTrade is an Autonomous Capital Management Platform. Trading is the first proving mechanism, not the final product.

Current objective:
Complete the shortest safe, evidence-backed path to exactly one supervised Coinbase BTC-USD market buy capped at $5, followed by reconciliation and immediate disabling of live submission.

Hard constraints:
- Preserve the four permanent core engines.
- Mission Control is a control plane, not a fifth engine.
- Risk Engine remains final authority.
- Human approval remains mandatory.
- LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED remains false until the explicitly authorized live-validation prompt.
- No automatic enabling of live submission.
- No autonomous live order submission.
- No autonomous retries of ambiguous orders.
- No withdrawals.
- No compounding.
- No second live order.
- No new strategies, agents, asset classes, or unrelated features.
- No secrets in code, logs, tests, docs, or output.
- Fail closed on missing, stale, ambiguous, or inconsistent evidence.
- Every state-changing action must be audited.
- Every production-readiness claim must be backed by observable evidence.
- Targeted tests are not sufficient for phase exit; full regression is mandatory.
- If documentation and implementation conflict, stop and report the conflict rather than silently choosing.
- Before any schema change, determine whether a migration and/or ADR is required and report that before implementing it.

After reading the required files, confirm:
1. the current migration head,
2. the current live-order feature flags and defaults,
3. the current Coinbase submission call path,
4. the current reconciliation call path,
5. the current Research/Evolution failure path,
6. any conflicts or ambiguities.

Then wait for the first implementation prompt.
```

---

# Prompt 10.1 — Current-State Inventory and Readiness Ladder

```text
Perform a read-only Phase 10 current-state inventory.

Do not modify code, migrations, environment files, or documentation in this prompt.

Trace and report:

1. Research/Evolution persistence path:
   - laboratory run creation
   - activity creation
   - autoflush points
   - transaction boundaries
   - rollback behavior
   - retry/idempotency behavior

2. Live Coinbase execution path:
   - operator/API entrypoint
   - authorization and approval checks
   - readiness checks
   - Risk Engine call
   - kill-switch checks
   - order cap enforcement
   - preview/intent creation
   - idempotency handling
   - Coinbase client submission
   - provider acknowledgement persistence
   - reconciliation
   - Capital Ledger/campaign/profit-cycle updates
   - audit and Decision Intelligence evidence

3. Feature flags:
   - names
   - defaults
   - server-side enforcement points
   - tests
   - do not reveal values of secrets

4. Production readiness ladder:
   - mark each item PASS, PARTIAL, FAIL, or UNKNOWN
   - cite exact files, functions, tests, migrations, or production evidence supporting each result

5. Gap analysis:
   - blockers for first supervised $5 trade
   - production-readiness defects
   - non-blocking deferred work
   - documentation drift

Output:
- a structured audit report
- a proposed implementation order for Prompts 10.2 onward
- no code changes

Stop after the report.
```

---

# Prompt 10.2 — Fix Research Persistence Sequencing

```text
Fix the Research/Evolution foreign key sequencing defect involving research_laboratory_runs and research_agent_activity.

Requirements:

- Persist or flush the parent ResearchLaboratoryRun before child ResearchAgentActivity rows can flush.
- Prefer explicit ORM relationships and dependency ordering where consistent with the repository.
- Use session.no_autoflush only narrowly where premature autoflush is demonstrably possible.
- Do not globally disable autoflush.
- Define a clear transaction boundary for the laboratory run and its required child records.
- On failure, roll back cleanly.
- Ensure the session is usable after rollback.
- Preserve auditability.
- Preserve existing research behavior and outputs.
- Do not modify live execution behavior.
- Do not add a migration unless a database-level constraint is necessary.
- If a new uniqueness/idempotency constraint is needed, stop first and explain the required migration and why.

Add tests for:

1. parent-before-child persistence
2. multiple activities under one run
3. candidate queries without premature autoflush
4. child failure causing complete rollback
5. session reuse after rollback
6. successful retry after rollback
7. no duplicate completed run on retry, if cycle identity already exists
8. existing successful research-cycle behavior

Run targeted research persistence and orchestration tests.

Stop after implementation and targeted validation.
```

---

# Prompt 10.3 — Research Failure Containment and Execution Isolation

```text
Harden Research/Evolution isolation from continuous orchestration and live execution.

Requirements:

- A research persistence failure must not terminate the continuous orchestration worker.
- The failed unit of work must roll back fully.
- The next orchestration unit must receive a clean session/transaction.
- Failure status and reason must be operator-visible and auditable.
- Repeated failures must not create duplicate partial records.
- Research/Evolution must have no import path, service dependency, command path, or event path that can submit a live Coinbase order.
- Research recommendations remain advisory.
- Research may not enable live mode, approve an order, allocate live funds, or bypass Risk.
- If clean isolation is supported, add an explicit operational method to disable only the Research/Evolution schedule while leaving execution/readiness services healthy.
- Any disable/enable action must be explicit and audited.
- Do not add a broad global switch that hides failures silently.

Add tests for:

1. worker continues after injected research failure
2. clean transaction/session on subsequent work
3. failure appears in health/status evidence
4. no partial child records remain
5. research-disabled mode leaves execution path unaffected
6. architecture-level no-path-to-live-submission assertion

Update operational documentation only where needed.

Stop after implementation and targeted validation.
```

---

# Prompt 10.4 — Live Execution Call-Path Audit and Hardening

```text
Audit and harden the complete Coinbase live submission path.

The required sequence is:

Operator Approval
→ Fresh Readiness Evidence
→ Risk Engine Approval
→ Kill-Switch Check
→ Server-Side $5 Cap
→ Preview / Order Intent
→ Idempotency Reservation
→ Coinbase Submission
→ Provider Acknowledgement Persistence
→ Reconciliation Required/Complete State
→ Capital Ledger / Campaign Accounting
→ Audit and Decision Evidence

Tasks:

1. Identify every callable path to Coinbase order submission.
2. Prove all paths require the same shared guard service.
3. Consolidate guard logic if it is duplicated.
4. Ensure the Risk Engine cannot be bypassed.
5. Ensure global and relevant account/campaign kill switches are checked.
6. Ensure readiness evidence has a defined freshness threshold.
7. Ensure the $5 cap is server-side and Decimal-safe.
8. Ensure live flags default to disabled.
9. Ensure preview/order-intent details cannot be altered after operator approval without invalidating the approval.
10. Ensure research, scheduled orchestration, and background agents cannot call the submission path.
11. Ensure missing audit capability fails closed.

Add tests covering each guard and bypass attempt.

Do not enable live submission.

Stop after implementation and targeted validation.
```

---

# Prompt 10.5 — Live Order Idempotency and Ambiguous Response Safety

```text
Harden live order idempotency, duplicate prevention, and ambiguous Coinbase response handling.

Requirements:

- Every live order intent has a unique, persistent client-order/idempotency identity before network submission.
- Duplicate operator clicks cannot create duplicate provider orders.
- Process restart after intent creation cannot create a second order.
- Timeout before a provider acknowledgement must not trigger blind automatic resubmission.
- Timeout after possible provider acceptance must enter an explicit reconciliation-required or submission-unknown state.
- Automatic retry is allowed only for reads/reconciliation, not order creation, unless the provider contract proves the same idempotency key is safe and the repository explicitly models that behavior.
- Provider order ID and client order ID must be persisted once known.
- Terminal success cannot be reported without persisted provider evidence.
- Conflicting provider evidence must fail visibly.
- Reconciliation must search by the stable provider-supported identifier.
- Exact-one-order invariant must be queryable and tested.

If schema support is insufficient:
- stop,
- propose the minimum migration,
- explain constraints/indexes,
- explain rollback,
- wait for approval before creating it.

Add unit and integration tests for duplicate clicks, restart, pre-ack timeout, post-accept timeout, delayed acknowledgement, and conflicting reconciliation evidence.

Do not enable live submission.

Stop after implementation and targeted validation.
```

---

# Prompt 10.6 — Reconciliation and Capital Accounting Integrity

```text
Harden the Coinbase reconciliation and downstream accounting path.

Requirements:

- Reconcile provider order status, fills, fees, filled quantity, average price, and timestamps.
- Preserve raw provider evidence in a safe, redacted, immutable or append-only form consistent with repository conventions.
- Update internal order/trade state transactionally where practical.
- Capital Ledger entries must derive from reconciled provider evidence, not estimated preview values.
- Campaign and Profit Cycle state must remain consistent.
- Realized and unrealized amounts must not be conflated.
- Fees must be represented explicitly.
- Live and paper balances/trades must remain visibly and structurally distinct.
- Reconciliation must be restart-safe and idempotent.
- Repeated reconciliation must not duplicate fills, trades, ledger entries, audit records, or campaign mutations.
- Partial fill and canceled-partial-fill states must be handled honestly.
- Unknown state must remain visible and must not be coerced to success or failure.

Add tests for:

1. full fill
2. partial fill
3. canceled with no fill
4. canceled after partial fill
5. delayed fill
6. repeated reconciliation
7. restart during reconciliation
8. fee reconciliation
9. balance mismatch
10. duplicate provider event
11. Capital Ledger write failure
12. campaign/profit-cycle consistency

Do not enable live submission.

Stop after implementation and targeted validation.
```

---

# Prompt 10.7 — Production Dry-Run Endpoint and Operator Evidence

```text
Complete the production dry-run workflow while live submission remains disabled.

Required environment posture:

LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false
LIVE_CRYPTO_DRY_RUN_ENABLED=true
LIVE_CRYPTO_MAX_ORDER_USD=5

Requirements:

- Generate a fresh BTC-USD order preview/intention for a maximum of $5.
- Require explicit operator approval of the exact immutable intent.
- Run the complete shared guard path.
- Stop immediately before the provider create-order call.
- Persist evidence showing that submission was intentionally skipped because dry-run mode was active.
- Produce no live trade, fill, fee, balance mutation, Capital Ledger mutation, or campaign capital mutation.
- Record audit and Decision Intelligence evidence.
- Show the dry-run result clearly in Mission Control or the existing live operations UI.
- Show readiness age, risk result, kill-switch state, approved amount, and cap.
- Never display secrets.

Add tests proving the dry run uses the same guards and intent model as live submission and differs only at the final provider submission boundary.

Stop after implementation and targeted validation.
```

---

# Prompt 10.8 — Failure Drills and Operational Runbook

```text
Implement and document Phase 10 production failure drills.

Do not submit a real order.

Create a repeatable validation procedure for:

1. stale readiness evidence
2. global kill switch engaged
3. account/campaign pause if applicable
4. Risk Engine rejection
5. order above $5
6. duplicate operator submission
7. Coinbase timeout before acknowledgement
8. Coinbase timeout after possible acceptance
9. provider rejection
10. audit persistence failure
11. Capital Ledger persistence failure
12. reconciliation service restart
13. provider/internal balance mismatch
14. Research/Evolution failure while execution services remain healthy
15. disabling live submission during a pending reconciliation

For each drill define:

- setup
- action
- expected system state
- expected audit evidence
- expected operator-visible message
- recovery procedure
- pass/fail criterion

Add or update an operator runbook covering:

- enabling live submission
- disabling live submission
- kill-switch use
- ambiguous-order response
- reconciliation recovery
- service rollback
- credential rotation
- post-trade evidence collection

Run all automatable drills with live submission disabled.

Stop after implementation and validation.
```

---

# Prompt 10.9 — Full Pre-Live Validation and Go/No-Go Report

```text
Perform the complete pre-live validation.

Do not enable live submission and do not submit a real order.

Run:

- all backend tests
- all frontend tests
- frontend lint
- frontend production build
- migration status/head validation
- migration upgrade validation in a non-production-equivalent environment
- secret scan
- dependency/security checks already defined by the repository
- dry-run workflow
- failure drills
- exact-one-order invariant checks
- production health checks
- worker health checks
- Research/Evolution health or positive isolation checks

Produce a go/no-go report containing:

- commit SHA
- deployment identifier
- migration head
- test commands and results
- unresolved failures
- critical/high security findings
- live flag defaults
- server-side order cap evidence
- Risk Engine guard evidence
- kill-switch evidence
- idempotency evidence
- dry-run evidence
- reconciliation evidence
- rollback readiness
- exact checklist of manual operator actions required for the first live trade

Recommendation must be one of:

GO
CONDITIONAL GO
NO-GO

Do not soften failed mandatory criteria.

Stop after the report and wait for explicit operator authorization.
```

---

# Prompt 10.10 — Controlled First Live $5 Coinbase Buy

```text
This prompt is authorized only after the operator explicitly approves the Phase 10.9 GO report.

Prepare the repository and operator procedure for exactly one live Coinbase order.

Do not autonomously enable production environment flags or submit the order without the operator's explicit action through the approved interface.

Permitted order:

Venue: Coinbase
Pair: BTC-USD
Side: BUY
Type: MARKET
Maximum notional: $5.00
Maximum count: one order submission attempt

Requirements:

1. Verify deployed commit and migration head match the approved GO report.
2. Verify production health.
3. Verify Research/Evolution is healthy or positively isolated.
4. Verify live submission is currently disabled.
5. Generate fresh readiness evidence.
6. Verify Risk Engine approval.
7. Verify kill switches are clear.
8. Verify Coinbase permissions and available balance.
9. Create the immutable $5-or-less order intent.
10. Present the exact intent for explicit operator approval.
11. Provide the exact minimal environment/operational steps the operator must execute to enable submission.
12. After operator submission, immediately provide the exact steps to disable LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED again.
13. Do not permit a second submission.
14. Begin reconciliation.
15. Preserve all evidence.

If any evidence is stale, missing, ambiguous, or inconsistent, stop with NO-GO.

Stop after preparing the controlled operation and operator commands. Do not fabricate execution results.
```

---

# Prompt 10.11 — Post-Trade Reconciliation and Lockdown

```text
After the operator has performed the single authorized live-order action, reconcile and lock down the system.

Requirements:

- Confirm LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false.
- Confirm no second live order exists.
- Retrieve and persist Coinbase order status.
- Reconcile fills, fees, quantity, average price, and timestamps.
- Reconcile pre/post balances.
- Confirm Capital Ledger entries.
- Confirm campaign and Profit Cycle consistency.
- Confirm risk decision.
- Confirm audit trail.
- Confirm Decision Intelligence evidence.
- Confirm operator identity and approval evidence.
- Confirm no automatic retry or duplicate submission occurred.
- Confirm Research/Evolution did not participate in submission.
- Surface any mismatch as unresolved; do not paper over it.

Run the relevant reconciliation and accounting tests against the resulting state where safe.

Produce a post-trade evidence report.

Do not submit another order.

Stop after the report.
```

---

# Prompt 10.12 — Phase 10 Completion Report and Documentation Update

```text
Prepare the Phase 10 completion report and documentation updates.

Do not add new product features.

The report must include:

- scope completed
- scope deferred
- exact production result
- deployed commit
- migration head
- order identifiers
- status timeline
- fill details
- fee details
- balance reconciliation
- Capital Ledger evidence
- campaign/profit-cycle evidence
- risk evidence
- kill-switch evidence
- audit evidence
- Decision Intelligence evidence
- Research/Evolution resolution or isolation evidence
- all test and build results
- unresolved defects
- lessons learned
- recommendation for the next phase

Update:

- docs/PROJECT_STATUS.md
- docs/MASTER_PRODUCT_ROADMAP.md only if implementation reality requires it
- docs/POST_PHASE_9_ROADMAP.md or successor status
- docs/PHASE_10_FIRST_LIVE_CAPITAL_READINESS.md readiness ladder
- validation log/completion report files used by the repository

Preserve the North Star:

OmniTrade is an Autonomous Capital Management Platform. The successful first live trade proves one controlled capital-deployment mechanism; it does not authorize recurring or autonomous live trading.

Stop after documentation and full final validation.
```
