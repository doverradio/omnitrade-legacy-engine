# Controlled Proof Activation Authority

## Why this exists

`AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED` is the global switch that lets
*any* automatically-generated canonical package progress unattended through
mandate authorization, dry-run, and activation. It is deliberately kept
`false` in production: flipping it on would let every future automatic
package activate and submit live orders unattended, for as long as the
switch stays on -- far broader than what a single, operator-issued
Controlled Proof needs.

A Controlled Proof (`RUN_CONTROLLED_PROOF`) is itself an explicit, audited,
operator-issued authorization to attempt exactly one BUY-to-SELL lifecycle
for one product, up to one notional cap, inside one time window. Rather than
widening the global switch to let that one proof through, the executor
(`app/services/orchestration/automatic_package_executor.py`,
`_resolve_controlled_proof_activation_scope`) recognizes the Controlled
Proof's own authority and grants a narrow, package-scoped override -- only
for the one package genuinely linked to that one proof, only while every
invariant below holds, re-verified fresh against the database on every call.

Ordinary automatic packages (no Controlled Proof linkage) are unaffected:
they remain governed solely by `AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED`,
exactly as before this change.

### Resolved activation scope (fixes `automatic_activation_scope_incomplete`)

The first version of this override still let the *downstream* scope
resolution read from the legacy global selector settings
(`AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID/_CAMPAIGN_VERSION/_MANDATE_ID/_MANDATE_VERSION_ID`)
unconditionally, even after granting a Controlled Proof override -- so a
partially-configured global selector (as production actually had:
`campaign_id`/`mandate_id` set, `campaign_version`/`mandate_version_id`
unset) still failed the whole request closed with
`automatic_activation_scope_incomplete`, before the already-resolved
package was ever used. `execute_automatic_ready_package_through_activation`
now resolves one `ResolvedAutomaticActivationScope` exactly once, from
exactly one source, before ever touching the global selector settings:

- **`CONTROLLED_PROOF_DERIVED_SCOPE`** -- whenever `_resolve_controlled_proof_activation_scope`
  grants an override, `package_id`, `campaign_id`, `campaign_version`,
  `mandate_id`, `mandate_version_id`, and `mandate_evaluation_id` are taken
  directly off that exact, already-validated `CanonicalPreviewPackage` row
  -- never off settings, never off a statically pinned package ID (every
  Controlled Proof creates a fresh package). The legacy global selector
  settings are not consulted at all in this mode, so a partially- or
  differently-configured global selector (including a pinned
  `automatic_mandate_package_activation_package_id` pointing at an unrelated
  package) can never redirect or block it.
- **`GLOBAL_CONFIGURED_SCOPE`** -- unchanged: ordinary automatic packages
  resolve scope from settings exactly as before (unrestricted when all four
  are unset, `automatic_activation_scope_incomplete` when only some are
  set, `automatic_activation_campaign_scope_mismatch`/
  `automatic_activation_mandate_scope_mismatch` when fully set but
  mismatched).

Both modes then feed the identical, unmodified package-fetch and
authorize/dry-run/activate state machine -- the state machine itself is not
duplicated.

## Fail-closed conditions

The override is granted only when **all** of the following hold; any single
failure blocks activation with a precise `controlled_proof_activation_override_blocked
reason=<code>` log line and leaves the package and proof untouched:

| Condition | Reason code |
|---|---|
| Package is linked to a proof (via the proof's `package_id` or `sell_package_id`) | `no_controlled_proof_linkage` |
| Proof status is one of `REQUESTED/CLAIMED/ENTRY_PROPOSED/PACKAGE_CREATED/POSITION_OPEN/WAITING_FOR_PROFITABLE_EXIT` | `controlled_proof_not_active` |
| Proof has not expired | `controlled_proof_expired` |
| Package product matches the proof's product | `controlled_proof_product_mismatch` |
| Package campaign identity (id + version) matches the proof's pinned campaign | `controlled_proof_campaign_scope_mismatch` |
| Package decision, Risk event, and mandate evaluation evidence are all present | `controlled_proof_evidence_incomplete` |
| Package's Risk-approved notional does not exceed the proof's maximum | `controlled_proof_notional_exceeds_maximum` |
| Package provider/environment match the proof's approved live proving scope | `controlled_proof_provider_environment_mismatch` |
| No prior live order/position exists for this proof on the matching side, unless this package is already `ACTIVATED` (idempotent replay) | `controlled_proof_live_capital_already_exists` |

A proof that is expired, cancelled, blocked, failed, or otherwise terminal
never satisfies the second condition, so it can never receive the override
-- this is the same `_ACTIVE_STATES` set the proof creation/replace-active
path and the database's `uq_controlled_proof_runs_single_active` partial
index are built on.

Once granted, the package still passes through the **unchanged** mandate
authorization, dry-run, and activation calls (`authorize_canonical_preview_package_under_mandate`,
`run_dry_run_for_canonical_preview_package`, `activate_canonical_proving_campaign`)
-- Risk Engine authority, mandate authority, and kill switches are not
bypassed by this override, only the global automatic-activation flag is.

## Activation scope vs. execution scope: two separate gates

Reaching `package_state="ACTIVATED"` is **not** sufficient for a package to
be claimed for execution -- `claim_activated_buy_package`
(`app/services/orchestration/autonomous_execution_claims.py`) independently
re-resolves its own scope before creating an `AutonomousExecutionClaim`, via
`_resolve_autonomous_execution_scope`. This mirrors the activation
resolver's two-mode design exactly, and for the identical reason: a
Controlled Proof's own persisted linkage is authoritative for its own
package, and a legacy global selector setting (`AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_*`)
must never gate or redirect it.

- If the package is linked to a `ControlledProofRun` (via that proof's own
  `package_id`), scope resolves through `_resolve_controlled_proof_execution_scope`
  -- exclusively from the package and proof's own persisted fields (campaign
  identity, product, provider, environment, mandate identity, dry-run/
  activation/decision/risk evidence, `side == "BUY"`, proof still active and
  unexpired). The legacy `AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_*` settings
  are never consulted for this package.
- Otherwise (ordinary automation), scope resolves through the original,
  byte-for-byte-unchanged configured-selector check (`configured_scope_mismatch`
  when any of the four settings is unset).

Both modes construct the same `ResolvedAutonomousExecutionScope` and feed
the identical, unmodified claim-insert / preparation / provider-submission
pipeline below -- not duplicated.

## Claim uniqueness: per-package, per-activation, and per-active-scope

`AutonomousExecutionClaim` enforces three independent invariants:

- `uq_autonomous_execution_claim_package` -- exactly one claim per package,
  ever (unchanged).
- `uq_autonomous_execution_claim_activation` -- exactly one claim per
  activation, ever (unchanged).
- `uq_aec_active_campaign_scope` -- a **partial** unique index on
  `(campaign_id, campaign_version)`, scoped to claim_status values whose
  provider-submission outcome is not yet resolved (`CLAIMED`,
  `EXECUTION_STARTED`, `SUBMISSION_PENDING`, `RECONCILIATION_REQUIRED`,
  `RECOVERY_REQUIRED` -- see `_CLAIM_SCOPE_NONTERMINAL_STATES` in
  `autonomous_execution_claims.py`). `BLOCKED` is deliberately a
  scope-*releasing* status (its name describes a permanent, non-recoverable
  pre-provider stop, the same shape as `FAILED_PRE_PROVIDER`, not an
  in-progress one). This **replaces** the original migration-20260724_0048
  plain `UNIQUE(campaign_id, campaign_version)`, which permitted at most one
  claim row *ever* per campaign version regardless of status -- since every
  Controlled Proof shares one pinned campaign/version, that made every
  second Controlled Proof's claim permanently fail with
  `claim_concurrency_conflict`, even after the first claim's package fully
  resolved (e.g. `SAFETY_DISABLED` because live submission was off, or
  `FAILED_PRE_PROVIDER`) with no provider call ever made. Migration
  `20260727_0053` fixes this; see that file's docstring for the preflight
  query and downgrade path.

`claim_activated_buy_package`'s INSERT distinguishes the two possible
rejection causes precisely: if the by-`package_id` lookup after the insert
attempt still finds nothing, it queries for a currently-nonterminal claim in
the same `(campaign_id, campaign_version)` scope. Finding one returns
`active_campaign_execution_claim_exists` (a real, distinct, active claim
already owns this scope); finding none returns the generic
`claim_concurrency_conflict` (a genuinely unexplained race). A same-package
replay is always resolved first, before any insert is attempted, and always
returns the existing claim (`autonomous_execution_claim_reused`,
`already_claimed`) -- never a second insert, never a second provider call.

A successful (or terminally failed) execution must also eventually
*release* its campaign scope, or a later, legitimate sequential Controlled
Proof could never claim again -- the same defect in a different shape.
`release_execution_claim_scope_if_order_resolved` (called from
`LiveCryptoOrderService.reconcile()`/`.cancel()` right after a live order's
status is authoritatively updated) maps `FILLED -> BUY_RECONCILED` and
`CANCELLED/REJECTED/EXPIRED -> CANCELLED`; it is a no-op for any
still-ambiguous status and never overwrites a claim that has already left
the nonterminal set. `advance_claimed_execution` itself is now a guaranteed
no-op for any claim already outside `_CLAIM_SCOPE_NONTERMINAL_STATES` --
without this, `continuous_pipeline_worker` calls it on *every* cycle for as
long as the package's own `package_state` stays `ACTIVATED` (nothing ever
advances that), which would otherwise re-drive `prepare_autonomous_claimed_buy`
against a claim that already finished, typically fail on the by-then-expired
activation window, and let `mark_pre_provider_blocked` silently overwrite a
genuinely successful `BUY_RECONCILED` claim's status back to
`FAILED_PRE_PROVIDER` -- corrupting the record of a real, profitable BUY.
`sweep_stale_autonomous_execution_claims` now also recovers claims stuck in
`EXECUTION_STARTED` (previously only `CLAIMED`), covering a crash between
`prepare_autonomous_claimed_buy`'s own state transition and the submission
call that follows it -- `SUBMISSION_PENDING`/`RECONCILIATION_REQUIRED` are
deliberately excluded from the sweep, since re-preparing either would be an
unsafe blind retry after a provider call may already have been made; the
correct recovery there is `reconcile()` against the real order.

## Automatic reconciliation (no operator action required)

**Reconciliation is now automatic.** `app/services/orchestration/reconciliation_scheduler.py`'s
`poll_unresolved_live_orders` runs once per orchestration cycle (see
`continuous_pipeline_worker.run_orchestration_cycle`, right before that
cycle's own orchestration attempt -- so a fill discovered here is already
visible to `should_propose_controlled_sell` within the same cycle), reusing
the identical `LiveCryptoOrderService.reconcile()` service the operator CLI
and `/reconcile` HTTP route already used -- no provider lookup, fill
accounting, fee calculation, or ledger logic is duplicated.

- **Candidate discovery**: every `LiveCryptoOrder` with `submitted_at IS NOT NULL`
  whose `status` is not in `{FILLED, CANCELLED, REJECTED, EXPIRED}`
  (exclude-based, not an allow-list -- an unrecognized status defaults to
  "needs reconciliation," never silently skipped), oldest first, bounded by
  `AUTOMATIC_LIVE_ORDER_RECONCILIATION_BATCH_LIMIT` (default 10) to cap
  provider requests per cycle. Row-locked with `SKIP LOCKED` so a
  concurrent poller never re-attempts an order another attempt already has
  in flight -- correctness itself is independently guaranteed regardless,
  by `LiveAccountingRecord`'s own `idempotency_key` and
  `(provider_order_id, provider_fill_id, record_type)` unique constraints.
- **Fail-closed per candidate**: each order is reconciled independently;
  one candidate's failure (provider outage, missing credentials, ambiguous
  response, accounting mismatch -- `reconcile_live_order_and_fills` already
  fails closed on all of these) never aborts the rest of the batch and
  never fabricates or forces an outcome.
- **Ordinary autonomous execution and Controlled Proof execution share this
  one path** -- candidate discovery has no notion of Controlled Proof at
  all, only `LiveCryptoOrder`'s own status.
- **Toggle**: `AUTOMATIC_LIVE_ORDER_RECONCILIATION_ENABLED` (default `true`
  -- unlike the live-capital-committing flags above, reconciliation only
  reads provider order state and records the resulting fills/fees; it never
  submits a new order or risks a duplicate BUY, so defaulting it off would
  leave every BUY permanently stuck at `SUBMISSION_PENDING` with no
  automatic path to a SELL).
- **Manual reconciliation remains available** as an operator recovery tool
  (CLI command, `/reconcile` route) -- unchanged, for out-of-band recovery
  or immediate manual intervention.

## Exactly-once provider submission

Activation only gets a package to `ACTIVATED`. Provider submission happens
downstream, in `claim_activated_buy_package` /
`app.services.orchestration.autonomous_execution_claims` and
`prepare_autonomous_claimed_buy` / `execute_activated_commissioned_entry`,
which are unmodified by this change beyond the claim-scope fix above: one
row-locked, unique `AutonomousExecutionClaim` per package
(`on_conflict_do_nothing` + reload-the-winner), one `LiveCryptoOrder` per
claim, and an identity-bound preview hash checked before every submission
so a retried or replayed call can never produce a second Kraken order.
`provider_submission_started` / `_succeeded` / `_ambiguous` are now logged
directly around the actual Kraken call in
`commissioned_entry_execution.py`.

## Required runtime configuration

No new environment variable is introduced by this change; no systemd or
`.env` change is required for a Controlled Proof to activate.
`AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_ENABLED` stays `false` in production.
The existing scope-pinning settings
(`AUTOMATIC_MANDATE_PACKAGE_ACTIVATION_CAMPAIGN_ID/_CAMPAIGN_VERSION/_MANDATE_ID/_MANDATE_VERSION_ID`)
must still be configured identically on both `omnitrade-api` and
`omnitrade-orchestration` -- they already are, since asset-commissioning
readiness (`automatic_package_identity_bundle`) depends on the same values
to declare a product `package_creation_eligible`.

A database migration **is** required for the `claim_concurrency_conflict`
fix: `alembic upgrade head` applies `20260727_0053`, replacing
`uq_autonomous_execution_claim_campaign_version` with the partial index
`uq_aec_active_campaign_scope`. Run the preflight query in that migration's
docstring first (expected empty on a healthy system).

## Operator commands

Launch a fresh, replace-active Controlled Proof:

```bash
curl -sS -X POST https://<api-host>/api/v1/operator/actions \
  -H "Authorization: Bearer <operator-token>" -H "Content-Type: application/json" \
  -d '{
    "action_type": "RUN_CONTROLLED_PROOF",
    "idempotency_key": "operator-2026-07-26-btc-proof",
    "parameters": {"product_id": "BTC-USD", "expires_in_minutes": 60, "replace_active": true}
  }'
```

Monitor only the lifecycle log sequence:

```bash
journalctl -u omnitrade-api -u omnitrade-orchestration -f \
  | grep -E "controlled_proof_|automatic_package_|automatic_ready_package_|autonomous_execution_|provider_submission_"
```

## Expected log sequence, acceptance through verified profit

```
controlled_proof_dispatch_started
controlled_proof_claimed
controlled_proof_risk_evaluation_started
controlled_proof_risk_allow
automatic_package_identity_bundle ... package_creation_eligible=True
automatic_ready_package_created
controlled_proof_activation_override_evaluated
controlled_proof_activation_override_allowed
automatic_activation_scope_resolved authority_mode=CONTROLLED_PROOF_DERIVED_SCOPE
automatic_package_authorization_started
automatic_package_authorized_under_mandate
automatic_package_dry_run_passed
automatic_package_activated
autonomous_execution_scope_resolution_started
autonomous_execution_scope_resolved authority_mode=CONTROLLED_PROOF_DERIVED_SCOPE
autonomous_execution_claim_resolution_started
autonomous_execution_claimed
autonomous_execution_failed_pre_provider  -- absent on the golden path
provider_submission_started
provider_submission_succeeded
live_order_reconciliation_poll_started
live_order_reconciliation_candidates_discovered
live_order_reconciliation_attempt_started
live_order_reconciliation_attempt_resolved status=FILLED
reconciliation_completed reconciliation_status=reconciled provider_fill_observed=True
autonomous_execution_claim_scope_released new_claim_status=BUY_RECONCILED
controlled_proof_position_opened
controlled_proof_exit_evaluation
controlled_proof_sell_submitted
controlled_proof_sell_filled
controlled_proof_reconciliation_completed
controlled_proof_terminal_verdict reason=LIFECYCLE_PROVEN_PROFIT
```

## BUY-to-SELL lifecycle, verified-profit terminal conditions

`should_propose_controlled_sell` only returns true once the proof's BUY has
a real filled `LiveAccountingRecord` and the resulting position is
genuinely open (`app.services.controlled_proof.service`). The SELL side
reuses the identical Risk evaluation
(`evaluate_controlled_proof_risk`), package creation, activation override,
and exactly-once claim/submission path as the BUY side -- there is no
separate, weaker SELL code path. The proof reaches `LIFECYCLE_PROVEN_PROFIT`
only when reconciliation (authoritative provider fills and fees) computes a
genuinely positive net P&L; `LIFECYCLE_PROVEN_LOSS` and
`LIFECYCLE_PROVEN_FLAT` are reported honestly and never relabeled as
profit.
