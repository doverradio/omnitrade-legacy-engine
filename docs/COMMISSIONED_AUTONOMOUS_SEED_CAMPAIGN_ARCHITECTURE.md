# OmniTrade Legacy Engine
# Commissioned Autonomous Seed Campaign Architecture

Status: Implemented production handoff specification
Immediate milestone: First Autonomous Profit
Target venue: Kraken production
Initial notional: USD 5.00, or the venue minimum if higher

---

## 1. Purpose

This capability allows an authenticated operator to commission one tightly bounded real-money entry while OmniTrade retains ownership of the complete trading lifecycle.

The operator authorizes capital and scope. OmniTrade must create the decision evidence, submit and reconcile the BUY, manage the resulting position, decide when an eligible exit exists, submit and reconcile the SELL, and calculate final net profit or loss.

This is not a manual Kraken purchase imported into OmniTrade. The order must be created by OmniTrade through its governed production execution path.

---

## 2. Why This Exists

The canonical proving campaign has repeatedly evaluated fresh BTC-USD market evidence but the active `ma_crossover@1.0.0` strategy has continued to emit HOLD. Waiting for a naturally occurring crossover does not provide additional proof of the execution and position-management pipeline.

The commissioned seed campaign separates two questions:

1. Can OmniTrade discover an entry autonomously?
2. Can OmniTrade safely own and complete an autonomous position lifecycle?

This specification proves the second question now without falsely claiming the first.

---

## 3. Milestone Classification

Formal capability name:

`Commissioned Autonomous Seed Campaign`

Entry authority:

`OPERATOR_COMMISSIONED`

Lifecycle authority after entry:

`OMNITRADE_AUTONOMOUS`

This campaign may contribute to the First Autonomous Profit milestone only if all post-authorization actions are performed by OmniTrade and the full outcome is reconciled. Its evidence must remain distinguishable from an autonomous-discovery entry.

---

## 4. Non-Negotiable Invariants

1. The operator authorizes the campaign, not an exchange-side manual trade.
2. OmniTrade submits the production BUY.
3. The Risk Engine remains authoritative and may veto the BUY or SELL.
4. The campaign is single-entry by default.
5. Duplicate entry is impossible under retries, worker restarts, or repeated operator commands.
6. The BUY must be reconciled before a managed position becomes authoritative.
7. The system may not sell more than the reconciled owned quantity.
8. Exit decisions must be deterministic, explainable, versioned, and auditable.
9. A SELL must account for fees and expected net dollars before submission unless an emergency risk exit applies.
10. Every state transition must be persisted and replay-safe.
11. No component may claim autonomous opportunity discovery for this entry.
12. Failure must be fail-closed. Ambiguous state requires reconciliation or manual review, never another order.

---

## 5. Campaign Definition

Minimum campaign fields:

```text
campaign_id
campaign_version
campaign_type = COMMISSIONED_AUTONOMOUS_SEED
venue = KRAKEN
product = BTC-USD
quote_currency = USD
maximum_entry_notional
entry_authority = OPERATOR_COMMISSIONED
repeat_entry_allowed = false
position_management = OMNITRADE_AUTONOMOUS
exit_policy_id
exit_policy_version
risk_policy_id
risk_policy_version
status
commissioned_by
commissioned_at
expires_at
```

The implementation should reuse existing capital-campaign, mandate, authorization, decision, execution, position, reconciliation, and audit models where possible. New persistence should be added only when the current schema cannot express a required invariant.

---

## 6. Campaign State Machine

Canonical states:

```text
DRAFT
  -> READY
  -> COMMISSIONED
  -> ENTRY_PROPOSED
  -> ENTRY_RISK_APPROVED
  -> ENTRY_SUBMITTED
  -> ENTRY_RECONCILING
  -> POSITION_OPEN
  -> POSITION_MANAGING
  -> EXIT_PROPOSED
  -> EXIT_RISK_APPROVED
  -> EXIT_SUBMITTED
  -> EXIT_RECONCILING
  -> CLOSED_PROFIT
  -> CLOSED_LOSS
  -> CLOSED_FLAT
```

Exceptional terminal or intervention states:

```text
EXPIRED
CANCELLED
VETOED
RECONCILIATION_REQUIRED
MANUAL_REVIEW_REQUIRED
FAILED_CLOSED
```

Every transition must define:

- allowed source states;
- required evidence;
- idempotency key;
- audit event;
- prohibited side effects on retry.

---

## 7. Commissioning Workflow

### 7.1 Readiness

A read-only readiness command must verify at least:

- canonical campaign identity is coherent;
- production environment and Kraken provider are selected;
- product mapping is valid;
- authenticated balance evidence is fresh;
- sufficient available USD exists;
- venue minimum notional and precision are known;
- maximum campaign notional is within server-side and Risk Engine limits;
- no open or ambiguous campaign position exists;
- no prior entry has been submitted for this campaign version;
- worker and reconciliation paths are healthy;
- exit policy is present and versioned;
- production authorization has not expired.

### 7.2 Preview

A no-write preview must return the exact proposed campaign identity, notional, product, venue, entry authority, expected fee estimate, risk inputs, and intended idempotency key.

Preview must explicitly state:

```text
no_database_writes = true
no_order_submission = true
no_position_creation = true
```

### 7.3 Commission

Commissioning creates or activates a one-time governed mandate. It does not submit the order by itself unless the existing operator contract explicitly combines commission and execute.

The commission event must record the operator, timestamp, campaign version, maximum notional, expiration, and acknowledgement that the entry is operator-commissioned rather than strategy-discovered.

### 7.4 Execute Entry

The execute command must:

1. Re-run all readiness checks.
2. Acquire a campaign-level idempotency lock.
3. Create an immutable Decision Record with `decision_kind = OPEN_POSITION_PROPOSED`.
4. Record `entry_authority = OPERATOR_COMMISSIONED` and `strategy_signal = NOT_REQUIRED_FOR_COMMISSIONED_ENTRY`.
5. Run the Risk Engine.
6. Submit no more than one Kraken BUY.
7. Persist the provider order identity and idempotency evidence.
8. Enter reconciliation state.
9. Refuse any retry that could create a second economic order.

---

## 8. Decision Intelligence Requirements

The entry Decision Record must clearly distinguish authority from strategy:

```text
decision_kind = OPEN_POSITION_PROPOSED
entry_authority = OPERATOR_COMMISSIONED
strategy_identity = null or dedicated non-strategy authority identity
strategy_signal = NOT_APPLICABLE
campaign_type = COMMISSIONED_AUTONOMOUS_SEED
maximum_notional = governed campaign value
repeat_entry_allowed = false
```

Required human-readable explanation:

> The operator commissioned one bounded seed entry so OmniTrade could prove autonomous position management. No market-discovery BUY signal was claimed. OmniTrade independently validated readiness, obtained Risk Engine approval, submitted the order, reconciled the fill, and assumed lifecycle responsibility.

The exit Decision Record must contain the actual exit policy evidence, expected net dollars, fee assumptions, current position quantity, and Risk Engine verdict.

---

## 9. Position Ownership and Reconciliation

A campaign position becomes authoritative only after provider reconciliation confirms executed quantity, average fill price, fees, and provider order identity.

Required position provenance:

```text
position_origin = COMMISSIONED_AUTONOMOUS_SEED
entry_campaign_id
entry_decision_record_id
entry_execution_id
venue
product
reconciled_quantity
reconciled_cost_basis
entry_fees
opened_at
```

If Kraken reports an uncertain, partial, duplicated, or inconsistent state, the campaign enters `RECONCILIATION_REQUIRED` and no further economic action occurs until resolved.

---

## 10. Autonomous Exit Policy

The implementation must reuse an existing governed profit/exit policy if one already satisfies these requirements. Otherwise, introduce the smallest deterministic policy necessary for the proving campaign.

The policy must define:

- profit target or approved exit condition;
- maximum tolerated loss or emergency stop condition;
- maximum holding duration, if any;
- fee-aware expected net profit calculation;
- behavior when expected net dollars are non-positive;
- behavior during provider/data failure;
- emergency Risk Engine exit precedence;
- quantity and precision handling.

A normal profit-taking SELL must not be submitted unless expected net dollars are positive after estimated entry and exit fees. Emergency capital-preservation exits may override this condition but must be labeled as risk exits and may close at a loss.

No hidden discretionary logic is permitted.

---

## 11. Idempotency and Failure Containment

At minimum, enforce unique economic intent for:

```text
(campaign_id, campaign_version, ENTRY)
(campaign_id, campaign_version, EXIT, position_id)
```

Retries must resolve one of three outcomes:

1. No provider submission occurred: safe to resume.
2. A provider submission occurred and is identified: reconcile it.
3. Submission state is ambiguous: fail closed and require reconciliation.

Never infer that an order failed merely because a request timed out.

Worker exceptions must be contained so one campaign failure does not stop unrelated runtime cycles.

---

## 12. Operator Interface

The final command names should follow existing repository conventions and must be discovered during implementation rather than invented blindly.

Required operator capabilities:

```text
commissioned-seed-readiness --json
commissioned-seed-preview --json
commissioned-seed-commission --json
commissioned-seed-execute --json
commissioned-seed-status --json
commissioned-seed-reconcile --json
```

Equivalent names are acceptable if they align with current `./operator` patterns.

Every write command must print:

- campaign identity and version;
- whether database writes occurred;
- whether an order was submitted;
- provider order identifier when applicable;
- resulting state;
- next safe operator action.

---

## 13. Testing Requirements

Minimum automated coverage:

1. Readiness success.
2. Readiness failure for insufficient funds.
3. Readiness failure for stale evidence.
4. Preview performs no writes or submission.
5. Commission is idempotent.
6. First execute submits one BUY.
7. Repeated execute submits no duplicate BUY.
8. Timeout after provider acceptance reconciles rather than resubmits.
9. Partial fill handling.
10. Entry reconciliation creates one authoritative position.
11. Position manager evaluates the commissioned position.
12. Profit exit requires positive expected net dollars.
13. Emergency risk exit can close at a loss with explicit classification.
14. SELL quantity never exceeds reconciled quantity.
15. Repeated exit execution submits no duplicate SELL.
16. Closed campaign records realized gross P&L, all fees, and net P&L.
17. Decision Records identify entry as operator-commissioned.
18. Existing autonomous-discovery campaigns remain unchanged.
19. Existing Kraken real BUY and SELL tests continue to pass.
20. Worker failure containment and restart recovery.

---

## 14. Production Acceptance Sequence

No production order may be attempted until all prior steps pass.

```text
1. Repository audit
2. Architecture mapping
3. Implementation
4. Focused tests
5. Full relevant test suite
6. Local readiness
7. Local preview
8. Deploy
9. VPS effective configuration validation
10. VPS readiness
11. VPS preview
12. Explicit operator commission
13. One bounded production BUY
14. Reconciliation proof
15. Autonomous position-management proof
16. Autonomous SELL
17. Reconciliation and net P&L proof
18. Runtime observation and audit review
```

---

## 15. Definition of Done

The commissioned proving campaign is complete only when:

- OmniTrade submitted exactly one governed production BUY;
- the BUY was reconciled into one authoritative position;
- OmniTrade monitored that position without human exchange-side intervention;
- OmniTrade generated and Risk-approved an eligible exit;
- OmniTrade submitted exactly one production SELL;
- the SELL was reconciled;
- gross proceeds, entry fees, exit fees, and net P&L were persisted;
- the campaign reached a terminal closed state;
- Decision Records clearly show operator-commissioned entry and autonomous lifecycle management;
- the audit trail is complete and replay-safe;
- no duplicate economic order occurred.

A profitable close advances the First Autonomous Profit milestone. A loss or flat close proves the lifecycle but does not satisfy the profit milestone.

---

## 16. Explicit Non-Goals

This implementation does not:

- claim autonomous opportunity discovery;
- replace the Strategy Arena;
- declare MA crossover invalid;
- bypass the Risk Engine;
- permit repeated autonomous entries;
- scale capital beyond the commissioned maximum;
- redesign the provider layer;
- introduce opaque AI trading authority;
- optimize the exit policy for maximum profit.

---

## 17. Follow-On Order

After the full lifecycle is proven:

1. Historical Strategy Scorecard.
2. Shadow evaluation of multiple strategies on identical observations.
3. Strategy Arena ranking by regime, fees, risk, and outcome quality.
4. Standardized Learning Process.
5. Autonomous-discovery proving lane on each venue.

The commissioned seed campaign is a proving mechanism, not the permanent source of trading edge.

---

## 18. Current Implementation Status

Implemented and validated:

- commissioned campaign domain/state-machine support;
- commissioned readiness and preview flows;
- governed entry execution and reconciliation support;
- autonomous lifecycle and exit support;
- shared commissioned control-plane status and mutation service;
- REST and CLI control-plane wrappers;
- startup observability repair for the orchestration worker entrypoint;
- documentation, handoff, proving-window, and rollback package.

Not yet performed:

- production proving-window evidence collection;
- explicit operator-approved commissioning/activation;
- live commissioned BUY;
- live commissioned SELL.

Task tracker:

```text
Task 1  Repository and architecture audit                     COMPLETE
Task 2  Final implementation plan and invariant map          COMPLETE
Task 3  Campaign domain/state-machine implementation         COMPLETE
Task 4  Readiness and preview                                COMPLETE
Task 5  Commissioning and governed entry execution           COMPLETE
Task 6  Reconciliation and position ownership                COMPLETE
Task 7  Autonomous exit lifecycle                            COMPLETE
Task 8  Operator commands and status                         COMPLETE
Task 9  Regression, resilience, and security validation      COMPLETE
Task 10 Documentation, deployment handoff, and go/no-go prep COMPLETE
```

## 19. Canonical Production Services And Entrypoint

Canonical production services:

```text
omnitrade-api.service
omnitrade-orchestration.service
```

Canonical orchestration worker entrypoint:

```text
python -m app.services.orchestration.continuous_pipeline_worker
```

Never reference or operate an `omnitrade-worker.service` unit.

## 20. Exact Deployment Sequence

Use this sequence for future scoped commissioned-campaign or orchestration-runtime deployments:

```bash
git fetch origin
git checkout master
git pull --ff-only origin master
git rev-parse --short HEAD
git diff --check
python -m pytest apps/api/tests/unit/services/capital_campaign_domain/test_commissioned_*.py apps/api/tests/api/test_capital_campaign_domain_routes.py apps/api/tests/unit/operator_cli/test_main.py -q
python -m pytest apps/api/tests/integration/test_continuous_pipeline_worker.py -k "startup or run_forever" apps/api/tests/unit/test_database_bootstrap_entrypoints.py -q
git push origin master
ssh <vps>
cd ~/omnitrade-legacy-engine
git fetch origin
git checkout master
git pull --ff-only origin master
source apps/api/.venv/bin/activate
python -m pytest apps/api/tests/unit/services/capital_campaign_domain/test_commissioned_*.py apps/api/tests/api/test_capital_campaign_domain_routes.py apps/api/tests/unit/operator_cli/test_main.py apps/api/tests/integration/test_continuous_pipeline_worker.py -k "not slow" -q
sudo systemctl restart omnitrade-api.service
sudo systemctl restart omnitrade-orchestration.service
systemctl is-active omnitrade-api.service
systemctl is-active omnitrade-orchestration.service
systemctl show omnitrade-api.service -p NRestarts --value
systemctl show omnitrade-orchestration.service -p NRestarts --value
journalctl -u omnitrade-orchestration.service -n 200 --no-pager
```

This sequence deploys code only. It does not activate a commissioned campaign.

## 21. Exact Rollback Sequence

Fail closed if any commissioned campaign is in an ambiguous or unsafe condition.

```bash
# 1. Inspect current state before any rollback action.
./operator commissioned-control-plane-status --campaign-id <campaign_uuid> --version <version> --json
curl -sS "http://127.0.0.1:8000/capital-campaigns/domain/<campaign_uuid>/commissioned/control-plane/status?version=<version>"

# 2. If valid and explicitly approved, pause the campaign before code rollback.
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action pause --idempotency-key rollback-pause-<ts> --reason "rollback safety pause" --json

# 3. Cancel only when the current commissioned state allows cancel and the operator explicitly approves it.
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action cancel --idempotency-key rollback-cancel-<ts> --reason "rollback cancel" --json

# 4. Preserve evidence before service restart.
./operator campaign-orchestration-history --campaign-id <campaign_uuid> --version <version> --limit 100 --json
./operator commissioned-control-plane-status --campaign-id <campaign_uuid> --version <version> --json
journalctl -u omnitrade-api.service -n 200 --no-pager
journalctl -u omnitrade-orchestration.service -n 200 --no-pager

# 5. Roll application commit back on VPS.
git log --oneline -5
git checkout <previous_safe_commit>

# 6. Restart canonical services.
sudo systemctl restart omnitrade-api.service
sudo systemctl restart omnitrade-orchestration.service

# 7. Verify service health after rollback.
systemctl is-active omnitrade-api.service
systemctl is-active omnitrade-orchestration.service
systemctl show omnitrade-api.service -p NRestarts --value
systemctl show omnitrade-orchestration.service -p NRestarts --value

# 8. Re-check provider state and campaign state before any retry.
./operator commissioned-control-plane-status --campaign-id <campaign_uuid> --version <version> --json
./operator campaign-orchestration-status --campaign-id <campaign_uuid> --version <version> --json
```

Rollback invariants:

- preserve Decision Records;
- preserve audit evidence;
- preserve reconciliation evidence;
- never assume a failed request means no provider order exists;
- check provider state before any retry;
- avoid duplicate BUY after rollback;
- avoid duplicate SELL after rollback.

## 22. Pre-Deployment Checklist

- HEAD includes `eec36b9`, `cf25023`, and `9be1ead`.
- `git diff --check` is clean.
- Mandatory commissioned/control-plane gate passes.
- Focused orchestration startup tests pass.
- Documentation references `omnitrade-api.service` and `omnitrade-orchestration.service` only.
- Documentation references the worker entrypoint exactly as `python -m app.services.orchestration.continuous_pipeline_worker`.
- No migration files are added.
- No secrets, keys, account identifiers, or private provider data are present in staged docs.

## 23. Post-Deployment Checklist

- `omnitrade-api.service` is active/running.
- `omnitrade-orchestration.service` is active/running.
- `NRestarts` for both services is stable.
- A fresh orchestration worker process starts without traceback.
- No `_STARTED_AT` or `_RUN_ID` error is present.
- No runpy/sys.modules preload warning is present.
- Candle ingestion resumes.
- Autonomous cycle logging resumes.
- Commissioned control-plane status is queryable.

## 24. Runtime Readiness Checklist

- Worker PID is stable across the proving observation window.
- No fresh startup traceback exists in either service log.
- No unauthorized provider submission evidence exists.
- Decision Records remain queryable.
- Risk Engine evidence remains queryable.
- Reconciliation evidence is coherent.
- Control mutations are auditable.
- No secret is exposed in CLI, REST, or logs.

## 25. Campaign Commissioning Checklist

Read-only preconditions:

- funded Kraken account selected;
- available balance confirmed;
- campaign parameters confirmed;
- maximum capital-at-risk confirmed;
- live-provider readiness confirmed;
- Risk Engine readiness confirmed;
- Decision Record readiness confirmed;
- readiness preview inspected;
- no authoritative position already exists;
- idempotency key chosen and recorded.

Mutating or live-capital steps require separate explicit operator approval.

## 26. Operator Control-Plane CLI Reference

Read-only:

```bash
./operator commissioned-control-plane-status --campaign-id <campaign_uuid> --version <version> --json
./operator campaign-orchestration-status --campaign-id <campaign_uuid> --version <version> --json
./operator campaign-orchestration-history --campaign-id <campaign_uuid> --version <version> --limit 50 --json
```

Mutating, no-execution governance actions:

```bash
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action acknowledge --idempotency-key <key> --reason "acknowledge reconciliation or lifecycle review" --json
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action pause --idempotency-key <key> --reason "operator pause" --json
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action resume --idempotency-key <key> --reason "operator resume" --json
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action cancel --idempotency-key <key> --reason "operator cancel" --json
```

These commands mutate operator control metadata only.

They do not place a BUY or SELL.

## 27. REST Control-Plane Endpoint Reference

Read-only status:

```text
GET /capital-campaigns/domain/{campaign_id}/commissioned/control-plane/status?version=<version>
```

Mutation endpoint:

```text
POST /capital-campaigns/domain/{campaign_id}/commissioned/control-plane/actions
```

Mutation request body fields:

```json
{
  "campaign_id": "<uuid>",
  "version": 1,
  "actor": "operator:human",
  "action": "pause",
  "idempotency_key": "example-key",
  "reason": "operator initiated pause"
}
```

Contract guarantees:

- path/body campaign mismatch fails closed;
- actor is required;
- idempotency key is required;
- response returns `no_execution=true`.

## 28. Campaign State-Machine Reference

Implemented commissioned states:

```text
DRAFT
READY
COMMISSIONED
BUY_PENDING
BUY_SUBMITTED
BUY_RECONCILIATION_PENDING
ACTIVE_POSITION
SELL_EVALUATION
SELL_PENDING
SELL_SUBMITTED
SELL_RECONCILIATION_PENDING
COMPLETED
VETOED
EXPIRED
RECONCILIATION_REQUIRED
MANUAL_REVIEW_REQUIRED
FAILED_CLOSED
CANCELLED
```

Valid control-plane source-state transitions:

```text
ACKNOWLEDGE: RECONCILIATION_REQUIRED, MANUAL_REVIEW_REQUIRED, ACTIVE_POSITION, BUY_RECONCILIATION_PENDING, SELL_RECONCILIATION_PENDING
PAUSE: READY, COMMISSIONED, BUY_PENDING, BUY_SUBMITTED, BUY_RECONCILIATION_PENDING, ACTIVE_POSITION, SELL_EVALUATION
RESUME: READY, COMMISSIONED, BUY_PENDING, BUY_SUBMITTED, BUY_RECONCILIATION_PENDING, ACTIVE_POSITION, SELL_EVALUATION
CANCEL: READY, COMMISSIONED, BUY_PENDING, BUY_SUBMITTED, BUY_RECONCILIATION_PENDING, ACTIVE_POSITION, SELL_EVALUATION, RECONCILIATION_REQUIRED, MANUAL_REVIEW_REQUIRED
```

Invalid transition behavior:

- invalid source state fails closed with `InvalidRequestError`;
- cancelled campaigns cannot be resumed;
- unsupported actions fail closed;
- malformed idempotency replay records fail closed;
- ambiguous state requires reconciliation or manual review, never another economic order.

## 29. Idempotency-Key And Changed-Intent Rules

- Every control-plane mutation requires a non-empty idempotency key.
- Reuse of the same key with the same request signature replays the original response.
- Reuse of the same key with changed intent is rejected fail closed.
- Economic idempotency boundaries remain campaign-level for BUY and position-level for SELL.

## 30. Risk Engine, Decision Record, Audit, And Execution Boundaries

Risk Engine veto behavior:

- Risk Engine remains authoritative for BUY and SELL decisions.
- A veto prevents economic action and must remain visible in persisted evidence.

Decision Record requirements:

- commissioned entry must be recorded as operator-commissioned rather than strategy-discovered;
- exit recommendations must remain deterministic, explainable, and versioned;
- later lifecycle actions must be attributable to Decision Records.

Audit-record requirements:

- commissioned control-plane mutations must create audit rows;
- startup observability events must record worker start metadata;
- transition history and audit history together form the operator-facing evidence package.

BUY submission boundary:

- control-plane status and action surfaces must not submit BUY orders;
- BUY submission remains in the governed orchestration/execution path only.

Recommendation-versus-execution boundary:

- lifecycle recommendation visibility is allowed in status;
- execution remains separate and risk-governed.

## 31. Reconciliation, Ownership, Partial Fill, And Ambiguous State Rules

- Provider reconciliation is required before a position becomes authoritative.
- Authoritative ownership requires reconciled quantity, price, fees, and provider order identity.
- Partial fills must be reconciled deterministically and may not justify over-selling.
- Ambiguous or conflicting provider evidence moves the campaign into `RECONCILIATION_REQUIRED` or `MANUAL_REVIEW_REQUIRED`.
- No replacement order may be submitted merely because provider state is delayed or unclear.
- Position lifecycle behavior remains autonomous only after ownership is proven.

Profitability and exit recommendation behavior:

- normal profit-taking SELL requires positive expected net dollars after fees;
- emergency risk exits may close at a loss but remain explicitly classified;
- profitability recommendations are advisory until they pass the execution boundary.

## 32. Crash, Restart Recovery, Duplicate Prevention, And Provider Neutrality

- Worker restart must not duplicate economic action.
- Startup persistence failures must not arise from undefined worker metadata.
- Startup entry through `python -m app.services.orchestration.continuous_pipeline_worker` must not preload the worker module through package import.
- Retries must reconcile known provider submissions rather than resubmit.
- No duplicate BUY may occur after crash or rollback.
- No duplicate SELL may occur after crash or rollback.
- Commissioned control-plane logic is provider-neutral and does not call a provider adapter directly.

## 33. Incident Response Procedure

If commissioned runtime evidence is abnormal:

1. Freeze operator mutation intent and gather status/audit/history evidence.
2. Pause the campaign when pause is valid and explicitly approved.
3. Check authoritative position and reconciliation state before any retry.
4. Preserve logs, Decision Records, Risk Engine evidence, and audit output.
5. Escalate if state is ambiguous, evidence conflicts, or an unauthorized submission is suspected.

## 34. Production Proving Window Requirements

Objective:

Collect evidence that production runtime is stable and the commissioned campaign can be observed safely without activating live execution.

Exact read-only evidence commands:

```bash
systemctl is-active omnitrade-api.service
systemctl is-active omnitrade-orchestration.service
systemctl show omnitrade-api.service -p NRestarts --value
systemctl show omnitrade-orchestration.service -p NRestarts --value
systemctl show omnitrade-orchestration.service -p MainPID --value
journalctl -u omnitrade-api.service -n 200 --no-pager
journalctl -u omnitrade-orchestration.service -n 400 --no-pager
ps -fp "$(systemctl show omnitrade-orchestration.service -p MainPID --value)"
./operator commissioned-control-plane-status --campaign-id <campaign_uuid> --version <version> --json
./operator campaign-orchestration-status --campaign-id <campaign_uuid> --version <version> --json
./operator campaign-orchestration-history --campaign-id <campaign_uuid> --version <version> --limit 50 --json
curl -sS "http://127.0.0.1:8000/operations/status"
curl -sS "http://127.0.0.1:8000/capital-campaigns/domain/<campaign_uuid>/commissioned/control-plane/status?version=<version>"
```

Required proof set:

- `omnitrade-api.service` is active/running;
- `omnitrade-orchestration.service` is active/running;
- `NRestarts` remains stable;
- current worker PID remains stable;
- no fresh startup traceback exists;
- no `_STARTED_AT` or `_RUN_ID` error exists;
- no runpy/sys.modules preload warning exists;
- candle ingestion remains current;
- autonomous-cycle idempotency behaves correctly;
- duplicate candles are safely deduplicated;
- no duplicate economic action occurs;
- campaign status is queryable;
- Risk Engine evidence is available;
- Decision Records are available;
- reconciliation is coherent;
- control mutations are auditable;
- no secret is leaked;
- no unauthorized provider submission occurs.

PASS criteria:

- both services stay active throughout the observation window;
- restart counts remain unchanged;
- the worker PID remains stable unless there is a documented planned restart;
- logs contain no fresh startup traceback and no startup-metadata warning;
- status/history endpoints return coherent commissioned and orchestration state;
- evidence shows no duplicate BUY, no duplicate SELL, and no unauthorized provider submission.

FAIL criteria:

- either service is inactive or crash-looping;
- restart count increments unexpectedly;
- worker PID churn indicates instability;
- startup traceback, `_STARTED_AT`, `_RUN_ID`, or runpy preload warning reappears;
- reconciliation, Decision Records, Risk Engine, or audit evidence is missing or contradictory;
- any sign of duplicate economic action appears.

ABORT criteria:

- ambiguous provider state;
- evidence of unauthorized provider submission;
- missing authoritative ownership evidence while state implies live position;
- secret leakage in logs or outputs;
- operator cannot determine whether a prior submission already exists.

Escalation criteria:

- ABORT triggered;
- FAIL repeats after one bounded restart or evidence refresh;
- any discrepancy between provider evidence and internal reconciliation;
- any evidence suggesting a second execution path outside governed orchestration.

## 35. First Commissioned Campaign Checklist

This sequence is documented only.

Do not execute any mutating or live-capital step without separate explicit operator approval.

1. Select the funded Kraken account.
2. Confirm available balance.
3. Confirm campaign parameters.
4. Confirm maximum capital-at-risk.
5. Confirm live-provider readiness.
6. Confirm Risk Engine readiness.
7. Confirm Decision Record readiness.
8. Generate or inspect readiness preview.
9. Confirm no existing authoritative position.
10. Confirm the idempotency key to be used for the live action.
11. Obtain explicit commissioning/activation approval.
12. Observe order-submission evidence after the approved action.
13. Observe provider acceptance.
14. Reconcile fill or partial fill.
15. Establish authoritative ownership.
16. Monitor lifecycle recommendation.
17. Monitor exit recommendation.
18. Reconcile any eventual exit.
19. Calculate realized profit or loss.
20. Preserve the complete audit package.

Steps requiring explicit operator approval before execution:

- commissioning;
- any live BUY submission;
- any operator mutation intended to change runtime control state;
- any SELL-related live execution path.

Suggested later operator sequence:

```bash
# Read-only preparation
./operator commissioned-control-plane-status --campaign-id <campaign_uuid> --version <version> --json
./operator campaign-orchestration-status --campaign-id <campaign_uuid> --version <version> --json

# Approval-gated governance mutation only if explicitly approved
./operator commissioned-control-plane-action --campaign-id <campaign_uuid> --version <version> --actor operator:human --action resume --idempotency-key approved-resume-<ts> --reason "approved commissioned run" --json
```

Live execution is intentionally not documented as an automatic command sequence here.

It requires a separate explicit operator approval package after the proving window passes.

## 36. Final Go/No-Go Checklist

- production services healthy under canonical names;
- worker entrypoint confirmed and stable;
- no startup-metadata defect remains;
- no runpy preload warning remains;
- commissioned control-plane gate passes locally and on deployed code;
- focused orchestration startup gate passes locally and on deployed code;
- control plane remains non-executing;
- no direct provider adapter call exists in REST or CLI control-plane layers;
- readiness, Decision Record, Risk Engine, audit, and reconciliation evidence are queryable;
- proving-window evidence package is complete;
- no duplicate economic action evidence exists;
- no secret leakage exists;
- explicit operator approval has not yet been granted unless a separate activation decision package is signed off.

Go only if every item passes.

Otherwise remain in no-go and continue with fail-closed observation or remediation.

## 37. Known Technical Debt Carried Forward

Remaining non-commissioned debt:

- arena Risk Gate fake-session fixture drift;
- signal-orchestrator fake-result fixture drift;
- validation-run status fixture drift;
- research and analytics test-state contamination;
- paper realized-PnL expectation drift;
- async cancellation and event-loop teardown warning noise;
- FastAPI startup-event deprecation warnings.

Resolved production defects now recorded:

- missing commissioned campaign dependency files in the original scoped commit;
- undefined worker startup metadata;
- eager orchestration-package worker preload.
