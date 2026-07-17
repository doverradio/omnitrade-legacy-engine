# OmniTrade Legacy Engine
# Commissioned Autonomous Seed Campaign — Copilot Build Prompts

Use these prompts one at a time. Do not give Copilot the entire build as one large implementation request.

Permanent operating rules:

- Keep a visible progress bar for this implementation.
- Complete and validate one task before moving to the next.
- Do not invent repository structure, command names, tables, or services.
- Inspect the current implementation first and reuse existing abstractions.
- Preserve Decision Intelligence, Risk Engine authority, provider neutrality, auditability, and fail-closed behavior.
- Do not submit any real order during implementation or testing.
- Do not create a migration unless the current schema truly cannot represent a required invariant.
- Report whether an Alembic migration was created. If none was created, explicitly state: `No Alembic upgrade is required.`
- Stop after each prompt and return changed files, tests run, results, risks, and the exact next recommended prompt.

Progress tracker:

```text
Task 1  Repository and architecture audit                     [          ] 0%
Task 2  Final implementation plan and invariant map            [          ] 0%
Task 3  Campaign domain/state-machine implementation           [          ] 0%
Task 4  Readiness and preview                                  [          ] 0%
Task 5  Commissioning and entry execution                      [          ] 0%
Task 6  Reconciliation and position ownership                  [          ] 0%
Task 7  Autonomous exit lifecycle                              [          ] 0%
Task 8  Operator commands and status                           [          ] 0%
Task 9  Regression, resilience, and security validation        [          ] 0%
Task 10 Documentation, deployment handoff, and final verdict    [##########] 100%

Task 10 completion note:

- Canonical production services confirmed as `omnitrade-api.service` and `omnitrade-orchestration.service`.
- Canonical worker entrypoint confirmed as `python -m app.services.orchestration.continuous_pipeline_worker`.
- Final handoff, proving-window, rollback, and go/no-go package recorded in `COMMISSIONED_AUTONOMOUS_SEED_CAMPAIGN_ARCHITECTURE.md`.
- Resolved production defects recorded: missing commissioned dependency files, undefined startup metadata, eager orchestration-package worker preload.
- Remaining non-commissioned debt carried forward unchanged.
```

---

## Prompt 1 — Repository and Architecture Audit

```text
You are implementing the Commissioned Autonomous Seed Campaign for OmniTrade Legacy Engine.

Read these project documents first:

- PROJECT_CONSTITUTION.md
- PROJECT_STATE.md
- DECISIONS.md
- SYSTEM_ARCHITECTURE.md
- STRATEGY_ENGINE.md
- RISK_ENGINE.md
- DECISION_INTELLIGENCE_ENGINE.md
- API_CONTRACTS.md
- RISK_AND_AUDIT_API_CONTRACTS.md
- BACKEND_MODULE_SPECS.md
- DATABASE_SCHEMA.md
- SECURITY_AND_SAFETY.md
- COMMISSIONED_AUTONOMOUS_SEED_CAMPAIGN_ARCHITECTURE.md

Do not modify code yet.

Audit the current repository and identify the exact existing implementation for:

1. capital campaigns and campaign versions;
2. mandates and production authorization;
3. autonomous cycle orchestration;
4. canonical proving campaign binding;
5. Decision Records and Decision Packages;
6. Risk Engine evaluation;
7. Kraken production BUY and SELL submission;
8. idempotency and duplicate-order protection;
9. order reconciliation;
10. position lifecycle and position closure;
11. profit policies and expected-net-dollar calculations;
12. operator CLI command registration;
13. worker scheduling, restart recovery, and failure containment;
14. tests that prove previous real BUY and SELL behavior.

Return:

- a file-by-file architecture map;
- reusable components;
- gaps against the architecture document;
- schema changes that may be needed, with a strong preference for none;
- the narrowest safe implementation plan;
- exact tests that should be added or extended;
- explicit confirmation that no production action was performed.

Do not implement anything. Stop after the audit.
```

---

## Prompt 2 — Final Implementation Plan and Invariant Map

```text
Using the completed repository audit, produce the final implementation plan for the Commissioned Autonomous Seed Campaign. Do not modify code yet.

For every required invariant, identify:

- owning module;
- existing model or service reused;
- required code change;
- required test;
- failure behavior;
- idempotency boundary;
- audit evidence produced.

Map the complete state machine from DRAFT through terminal closure, including exceptional states.

Resolve these design questions from actual repository evidence:

1. Whether this should be a new campaign type, mandate type, decision authority, or a combination.
2. How to represent `OPERATOR_COMMISSIONED` without pretending it is a strategy signal.
3. How to enforce a single economic entry across retries and restarts.
4. How an entry becomes an authoritative managed position only after reconciliation.
5. Which existing profit/exit policy should govern the proving position.
6. Whether expected-net-dollar checks already include both entry and exit fees.
7. How emergency Risk Engine exits differ from normal profit-taking exits.
8. Whether a database migration is truly necessary.

Return the proposed changed-file list in implementation order and a test plan.

Do not modify code. Stop for review.
```

---

## Prompt 3 — Campaign Domain and State Machine

```text
Implement only the Commissioned Autonomous Seed Campaign domain representation and state-machine rules approved in the prior plan.

Requirements:

- Reuse existing campaign/version/mandate models where possible.
- Add the minimum explicit representation for commissioned entry authority.
- Preserve autonomous-discovery semantics unchanged.
- Define allowed transitions and fail closed on invalid transitions.
- Add campaign-level single-entry invariants.
- Add immutable audit evidence for campaign type, entry authority, maximum notional, repeat-entry policy, and lifecycle authority.
- Do not implement order submission yet.
- Do not add operator write commands yet.
- Do not perform production actions.

Add focused unit tests for valid transitions, invalid transitions, version identity, and single-entry invariants.

Run the focused tests and relevant existing campaign tests.

Stop after this task. Return changed files, test results, migration status, unresolved concerns, and updated progress.
```

---

## Prompt 4 — Readiness and Preview

```text
Implement only read-only readiness and preview support for the Commissioned Autonomous Seed Campaign.

Readiness must verify:

- coherent campaign identity and version;
- production Kraken provider selection;
- valid BTC-USD product mapping;
- fresh authenticated balance evidence;
- sufficient USD balance;
- venue minimum notional and precision;
- campaign maximum notional within all existing caps;
- no open or ambiguous campaign position;
- no prior economic entry for this campaign version;
- healthy reconciliation prerequisites;
- present, versioned exit and risk policies;
- valid, unexpired production authorization.

Preview must expose the exact intended economic action and idempotency identity while guaranteeing:

- no database writes;
- no package creation unless existing preview architecture requires an explicitly disposable no-write representation;
- no order submission;
- no position creation.

Use existing operator command conventions. If command names differ from the architecture draft, explain why.

Add tests proving every major readiness failure and proving preview has no side effects.

Do not implement live entry execution yet. Stop after tests.
```

---

## Prompt 5 — Commissioning and Governed Entry Execution

```text
Implement the one-time commissioning action and governed entry execution path.

Requirements:

1. Commissioning records operator identity, campaign version, maximum notional, expiration, and acknowledgement that this is not strategy-discovered.
2. Execute re-runs readiness immediately before economic action.
3. Execute acquires the existing strongest appropriate idempotency/locking protection.
4. Create an immutable OPEN_POSITION_PROPOSED Decision Record.
5. Record entry authority as OPERATOR_COMMISSIONED and do not fabricate a BUY strategy signal.
6. Run the existing Risk Engine without bypass or special allow shortcut.
7. Route through the existing Kraken production execution provider.
8. Submit no more than one economic BUY for the campaign version.
9. Persist provider identity and enter reconciliation state.
10. On timeout or ambiguity, fail closed and reconcile rather than resubmit.

Implement with a fake or mocked provider only. No real order may be submitted.

Add tests for:

- commission idempotency;
- expired commission;
- risk veto;
- exactly-one BUY;
- repeated execute;
- concurrent execute attempts;
- provider timeout before acceptance;
- provider timeout after acceptance;
- malformed provider response;
- correct Decision Record explanation and authority classification.

Run focused and relevant provider/execution regression tests. Stop after this task.
```

---

## Prompt 6 — Reconciliation and Position Ownership

```text
Implement commissioned-entry reconciliation and authoritative position ownership.

Requirements:

- Reuse the existing production reconciliation service.
- A position must not become authoritative until fill quantity, average fill price, fees, provider order identity, and product are reconciled.
- Persist position provenance linking campaign, Decision Record, execution, and provider evidence.
- Handle partial fills deterministically.
- Never create more than one authoritative position for the economic entry.
- Ambiguous, inconsistent, or duplicate provider evidence must enter RECONCILIATION_REQUIRED or MANUAL_REVIEW_REQUIRED.
- Never submit a replacement order merely because reconciliation is incomplete.
- Make restart recovery idempotent.

Add tests for full fill, partial fill, delayed fill, duplicate reconciliation events, conflicting provider evidence, restart recovery, and position provenance.

Do not implement the SELL path in this task. Stop after tests.
```

---

## Prompt 7 — Autonomous Exit Lifecycle

```text
Implement autonomous management and exit for the reconciled commissioned position.

First, identify and reuse the existing governed exit/profit-policy implementation. Do not invent a second exit engine.

Requirements:

- The position enters the existing autonomous management cycle.
- Exit evaluation is deterministic, explainable, and versioned.
- Normal profit-taking requires fee-aware expected net dollars greater than zero.
- The calculation must use reconciled cost basis, entry fees, expected exit fees, and executable quantity.
- Emergency Risk Engine exits may close at a loss but must be explicitly classified and explained.
- Create an immutable CLOSE_POSITION_PROPOSED Decision Record.
- Risk Engine remains authoritative.
- Submit no more than one economic SELL for the position.
- SELL quantity may never exceed reconciled owned quantity.
- Reconcile the SELL and persist gross proceeds, entry fees, exit fees, and net P&L.
- End in CLOSED_PROFIT, CLOSED_LOSS, or CLOSED_FLAT.
- Retry and worker restart behavior must be idempotent and fail closed.

Use mocked/fake provider execution only.

Add tests for profitable exit, non-positive expected net dollars, emergency risk exit, duplicate SELL prevention, partial SELL fill, quantity precision, restart recovery, and terminal P&L classification.

Run focused and relevant position/profit/reconciliation tests. Stop after this task.
```

---

## Prompt 8 — Operator Commands and Status

```text
Complete the operator interface for the Commissioned Autonomous Seed Campaign using existing `./operator` conventions.

Required capabilities:

- readiness;
- preview;
- commission;
- execute entry;
- status;
- reconciliation/status recovery where existing architecture permits operator initiation.

Every write command must return structured JSON containing:

- campaign_id;
- campaign_version;
- campaign_type;
- entry_authority;
- state before and after;
- database_writes;
- order_submitted;
- provider_order_id when applicable;
- risk verdict;
- reconciliation status;
- authoritative position identity when applicable;
- next_safe_action.

Read-only commands must clearly assert their invariants.

Add CLI tests for success, validation errors, invalid state, repeated commands, and safe JSON output. Ensure secrets are never printed.

Do not run any production write command. Stop after tests.
```

---

## Prompt 9 — Regression, Resilience, and Security Validation

```text
Perform the full pre-production engineering validation for the Commissioned Autonomous Seed Campaign.

Do not make architectural enhancements. Fix only genuine defects revealed by tests.

Run and report:

1. all new commissioned-seed tests;
2. existing campaign and mandate tests;
3. Decision Intelligence tests;
4. Risk Engine tests;
5. Kraken provider parsing and production-order contract tests using mocks;
6. reconciliation tests;
7. position lifecycle and profit-policy tests;
8. operator CLI tests;
9. worker failure-containment and restart-recovery tests;
10. security tests for secret redaction and production authorization.

Add explicit regression tests proving:

- autonomous-discovery campaigns are unchanged;
- MA crossover HOLD behavior remains unchanged;
- no manual review or stale evidence can become an order;
- no retry path can create a duplicate BUY or SELL;
- ambiguous provider state always fails closed;
- no test can reach the real Kraken network.

Return a pass/fail matrix, remaining risks, migration status, and a production-readiness verdict.

Stop before deployment.
```

---

## Prompt 10 — Documentation and Deployment Handoff

```text
Prepare the Commissioned Autonomous Seed Campaign for operator-controlled deployment without submitting any live order.

Update only the appropriate canonical documentation:

- PROJECT_STATE.md;
- DECISIONS.md, append-only, if a major architectural decision was actually made;
- NEXT_SESSION.md;
- operator runbook or relevant production documentation;
- API/operator contracts if changed.

Document the exact production acceptance sequence:

1. deploy code;
2. apply migration only if one was created;
3. restart only required services;
4. verify effective systemd configuration;
5. verify worker and API health;
6. run read-only readiness;
7. run no-write preview;
8. inspect output;
9. obtain explicit operator approval before commission;
10. commission the one-time USD 5 campaign;
11. re-run readiness;
12. execute exactly one entry;
13. reconcile and verify the authoritative position;
14. monitor autonomous management;
15. verify autonomous exit and final net P&L.

Provide:

- changed-file summary;
- final tests and results;
- migration status;
- local commit commands;
- VPS deployment and validation commands;
- rollback procedure;
- exact first production command that is read-only;
- separate live write command, clearly labeled DO NOT RUN WITHOUT EXPLICIT OPERATOR APPROVAL.

Follow permanent command formatting: combine related commands into readable copy/paste blocks, use `&&` for fail-fast sequencing and `;` only when later commands must continue after failure, and include a trailing blank line in every command block.

Do not submit a live order. Stop with the final readiness verdict.
```

---

# Production Authorization Rule

No Copilot prompt grants authority to submit a real Kraken order.

A real BUY may occur only after:

- implementation is committed and deployed;
- tests pass;
- VPS effective configuration is verified;
- readiness passes;
- preview passes;
- the user reviews the exact proposed notional and campaign identity;
- the user explicitly authorizes the one-time production commission and execution.
