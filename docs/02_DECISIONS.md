# OmniTrade Legacy Engine
# ARCHITECTURAL DECISIONS

Authority:
Append Only

Never rewrite history.

Never remove previous decisions.

Append new decisions as the project evolves.

This document records **why** important architectural decisions were made.

It is not a changelog.

---

# Entry Format

## Date

Decision

Reason

Alternatives Considered

Consequences

Future Impact

---

## 2026-07

### Execution Provider Layer

Decision

Execution became provider-neutral.

Reason

No exchange should ever become a permanent dependency.

Provider onboarding delays demonstrated the need for interchangeable execution providers.

Alternatives Considered

Building directly around one exchange.

Rejected.

Consequences

Execution providers can be replaced independently of the remainder of the architecture.

Future Impact

Future providers require only provider implementations.

No architectural redesign.

---

## 2026-07

### Autonomous Capital Campaigns

Decision

Capital is managed through campaigns rather than isolated trades.

Reason

Investment objectives belong to campaigns, not individual orders.

Future Impact

Every future asset class inherits the same campaign architecture.

---

## 2026-07

### Decision Quality

Decision

Decision quality is more important than raw profitability.

Reason

A profitable decision can still be poor.

A losing decision can still be correct.

Future Impact

The AI layer evaluates reasoning before outcome.

---

## 2026-07

### Small Account Mode

Decision

The platform must succeed with very small balances.

Reason

If the system cannot intelligently compound $25, larger balances merely conceal weaknesses.

Future Impact

Every feature must function correctly for the smallest supported account.

---

## 2026-07

### Replay Architecture

Decision

Every production decision must be replayable.

Reason

Replay enables:

- debugging
- AI coaching
- deterministic audits
- research
- regression testing

Future Impact

Future AI systems learn from immutable historical evidence rather than reconstructed guesses.

---

## 2026-07

### Immutable Decision Records

Decision

Decision Records are immutable.

Reason

Historical decisions are evidence.

Evidence must never change.

Future Impact

Every AI evaluation is based on trustworthy historical facts.

---

## 2026-07

### Fail Closed

Decision

Every production safety boundary fails closed.

Reason

Unexpected behavior should stop execution rather than continue unpredictably.

Future Impact

Safety always overrides opportunity.

---

## 2026-07

### Provider-Neutral Governance

Decision

Governance must never depend upon any exchange.

Reason

Operational control belongs to OmniTrade.

Execution belongs to providers.

Those responsibilities remain separate.

Future Impact

Future providers inherit identical governance.

---

## 2026-07

### Campaign Identity

Decision

Campaign identity is authoritative throughout execution.

Reason

Every production order must remain attributable to the campaign that authorized it.

Future Impact

Reconciliation, accounting, AI analysis, and reporting all preserve campaign ownership.

---

## 2026-07

### Production Before Expansion

Decision

The first autonomous profitable trade takes precedence over new functionality.

Reason

An unfinished platform gains little from additional features.

Production proof creates confidence for every subsequent phase.

Future Impact

Development remains milestone-driven rather than feature-driven.

---

## 2026-07

### Small, Bounded Engineering Tasks

Decision

Large implementation prompts are avoided.

Reason

Smaller implementation tasks consistently produce higher quality code, simpler reviews, and fewer regressions.

Future Impact

Future AI-assisted development remains incremental, verifiable, and maintainable.

---

## 2026-07

### Runtime Evidence Before Expansion

Decision

Operational evidence takes precedence over new feature work.

Reason

The production runtime has reached the point where engineering effort
produces more value by explaining runtime behavior than by adding
additional capabilities.

Future Impact

Engineering remains focused on achieving the first autonomous profit
through evidence-based debugging instead of speculative feature growth.

---

## 2026-07

### Parallel Authorized Lanes

Decision

Production proving-campaign work toward First Autonomous Profit and
expansion-foundation work (Historical Intelligence Platform Phase 3:
operating modes, evidence contracts, isolated simulation persistence)
are now both authorized to proceed in parallel, rather than expansion
work waiting strictly behind First Autonomous Profit.

Reason

The live proving campaign continues operating independently while its
current blocker (mandate package authorization expiry / executor
failure at package progression, not a Risk Engine or strategy-threshold
defect) is diagnosed and fixed. Phase 3 foundation work is
production-isolated by construction: a separate `SimulationBase`
metadata root, a separate `OT_SIMULATION_DATABASE_URL` with no fallback
to the production database, and an `IsolationGuard` that fails closed
on any historical/counterfactual run bound to production. Because this
isolation is structural, not conventional, Phase 3 work carries no risk
to the live proving campaign and does not need to wait behind it.

Also recorded here: `IMPLEMENTATION_MASTER_PLAN.md` Phase 1's original
diagnosis (unwired Risk Engine inputs — `campaign_authorized_notional`,
stop-loss, loss-history) is superseded by runtime evidence. The actual
blocker observed in production is package-progression/mandate
authorization, downstream of a BUY candidate that already passed
Strategy, Economics, and Risk. Phase 1's original risk-input changes
were explicitly NOT implemented as part of this lane; they should be
re-evaluated only if future evidence again implicates the Risk Engine
inputs specifically.

Alternatives Considered

Hold all expansion-foundation work until First Autonomous Profit is
fully proven. Rejected: Phase 3's isolation guarantees make this
unnecessarily conservative — the isolation is enforced by
`IsolationGuard`/separate persistence, not by sequencing discipline
alone, so there is no real coupling to protect by waiting.

Fix Phase 1's originally-diagnosed risk inputs anyway, since the master
plan called for it. Rejected: doing so now would modify Risk Engine
behavior based on a diagnosis the current runtime evidence contradicts,
risking a change with no basis in the actual observed failure.

Consequences

Two lanes of engineering work can proceed without either blocking the
other, as long as expansion-foundation work never touches the live
autonomous crypto path, risk_engine.py decision math, campaign/mandate/
execution/reconciliation/strategy behavior, or production
decision_records/decision_snapshots schema.

Future Impact

Future engineering sessions should treat "production proving" and
"expansion foundation" as two named, independently-tracked lanes, and
should re-diagnose the production blocker from fresh runtime evidence
(package progression / mandate authorization) rather than reusing the
superseded Phase 1 risk-input theory.

---

## 2026-07

### Bounded Live Multi-Asset Expansion

Decision

The autonomous worker may evaluate a small, explicitly configured roster
of Kraken spot products per cycle (BTC-USD plus any of a bounded,
hand-maintained set -- ETH-USD, SOL-USD -- via
AUTONOMOUS_CYCLE_ADDITIONAL_PRODUCTS), instead of BTC-USD alone, reusing
every existing gate (strategy roster, economics, Risk Engine, mandate
authority, canonical package progression, execution provider,
reconciliation/accounting) unchanged. Campaign composition's existing
deterministic ranking (authoritative.py's candidate_rows.sort by
expected_net_dollars, then risk_adjusted_score, then instrument name)
selects at most one winning instrument per cycle; every other qualifying
candidate is recorded as deferred (why_not_other_assets), not rejected.

Reason

Waiting on BTC alone to produce a qualifying BUY was judged to be
needlessly narrowing the platform's own opportunity set given that
almost the entire pipeline downstream of candle ingestion was already
asset-parametrized (strategy roster, campaign composition's per-instrument
loop and ranking, Risk Engine, canonical package/claim/execution) --
the only genuinely single-asset code was the worker's own hardcoded
trigger/product constants and the mandate-evaluation correlation lookup,
both narrow and mechanical to generalize without touching Risk, economics,
or execution logic at all.

Alternatives Considered

Build a separate multi-asset scanning/execution path in parallel with the
existing one. Rejected: explicitly out of scope -- doubles the surface
area needing the same safety guarantees and risks the two paths drifting
apart, exactly the failure mode ADR-0012 (Operating Modes and Adapter
Boundaries) was written to prevent for a different context.

Hardcode a fixed multi-asset list without inspecting live Kraken
tradability. Rejected: this session had no live network access to confirm
current Kraken product status/minimums via KrakenSpotClient.fetch_product;
the recommended roster (ETH-USD, SOL-USD) is repository-evidenced
(matches the existing binance_us seed script's own historical choice of
secondary assets) and well-established spot pairs, but must be confirmed
live by the operator before enabling, not assumed.

Consequences

No asset beyond BTC-USD is authorized to trade: canonical_campaign_binding.py
still hard-asserts the canonical proving campaign's allowed_instruments/
allowed_venues equal exactly {"BTC-USD"}/{"kraken_spot"}, and no code in
this change mutates campaign or mandate authority automatically. Enabling
a second asset requires an explicit, manually-applied campaign version
and/or mandate version change (operator commands provided separately),
never an automatic one. The default (unset) configuration is byte-identical
in behavior to before this change, verified by the full existing test
suite passing unchanged.

Future Impact

Future asset additions to the live roster should extend
asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS (moved from
continuous_pipeline_worker.py into its own shared module in the ETH-USD
enablement round, see below) and the campaign/mandate scope together,
deliberately, one asset at a time -- never by broadening
AUTONOMOUS_CYCLE_ADDITIONAL_PRODUCTS' parsing to accept unknown products
by guessing their Kraken asset symbols.

---

## 2026-07

### Bounded ETH-USD Enablement: Generalized Canonical Campaign Binding

Decision

canonical_campaign_binding.py's campaign-status-transition readiness check
no longer hardcodes the literal string "BTC-USD" as the only instrument a
canonical campaign may ever bind. It now validates every instrument in a
campaign's `allowed_instruments` independently against: worker-roster
membership (asset_roster.resolve_autonomous_cycle_products), an active
Asset Registry entry, venue minimum-order feasibility at the proving cap,
and sufficient recent candle history -- with an explicit, non-empty
`allowed_instruments` set required (no wildcard authorization). The
roster/symbol logic itself was extracted into a new shared module,
app/services/orchestration/asset_roster.py, specifically to let
canonical_campaign_binding.py reuse it without importing
continuous_pipeline_worker.py (which would create an import cycle via
app.services.autonomous_cycle).

Reason

The prior single-product literal check was correct for a BTC-only
proving campaign but was never going to generalize on its own, and its
safety intent (no instrument may bind without concrete, current evidence
that it is authorized, supported, registered, and tradable) needed to be
preserved exactly, not weakened, while removing the "BTC-USD" literal.

Alternatives Considered

Add a campaign-level `mandate_version_id` cross-check (verifying every
campaign instrument is also present in the authorized mandate version's
`allowed_products`) in this same round. Deferred: the current
`CanonicalCampaignStatusTransitionRequest` dataclass carries no mandate
reference at all; adding one would ripple into every existing caller
(operator_cli, ~49 existing tests) under time pressure that this
generalization did not need to accept. Recorded as a known, explicit gap
-- not a silently dropped requirement.

Consequences

A BTC-only campaign continues to bind exactly as before (verified: the
full pre-existing binding test suite, 39 tests, passes unchanged). A
BTC+ETH campaign can now bind only when both instruments independently
satisfy every check; ETH-USD specifically cannot bind today because no
Asset Registry entry exists for it and it is outside the default worker
roster -- both are separate, explicit operator actions, not automatic
consequences of this code change.

Future Impact

The mandate cross-check gap noted above should be closed before any
canonical campaign is actually granted multi-instrument authority, not
merely before this generalization is committed -- it is a real,
outstanding safety-completeness gap, tracked here rather than assumed
closed.

---
## 2026-07
### Production Alembic Procedure

Production does not use apps/api/.venv.

Canonical production runtime:

- Python: /home/eric/miniconda3/envs/omnitrade311/bin/python3.11
- API working directory: /home/eric/omnitrade-legacy-engine/apps/api
- Environment file: /home/eric/omnitrade-legacy-engine/apps/api/.env

Read-only migration check:

cd /home/eric/omnitrade-legacy-engine/apps/api
PYTHON=/home/eric/miniconda3/envs/omnitrade311/bin/python3.11
set -a
source .env
set +a
PYTHONPATH=. "$PYTHON" -m alembic -c alembic.ini current

Apply migrations:

cd /home/eric/omnitrade-legacy-engine/apps/api
PYTHON=/home/eric/miniconda3/envs/omnitrade311/bin/python3.11
set -a
source .env
set +a
PYTHONPATH=. "$PYTHON" -m alembic -c alembic.ini upgrade head

Always verify the resulting revision after migration.
Do not assume a .venv path on production.
---

## Future Decisions

Append only.

Never rewrite previous entries.

Always explain:

- what changed
- why it changed
- alternatives rejected
- long-term consequences

The goal is to preserve engineering reasoning for every future contributor, human or AI.

---

## 2026-07 — External Historical-Order Reconciliation Finality

Decision

An order carrying the exact `EXTERNALLY_EXECUTED_MANUAL_TRADE` authority
classification may reach economic reconciliation finality without an
OmniTrade pre-submit balance snapshot. The snapshot is classified as
`not_applicable_external_provenance`, not fabricated, inferred, or reported as
observed. Terminal provider order identity, authoritative fills and fees,
canonical accounting and ownership projection, and durable audit evidence
remain required. OmniTrade-submitted orders retain the existing fresh pre/post
balance-causality requirement unchanged.

Reason

The production recovery bridge imports trades that were executed outside
OmniTrade. Such a trade cannot truthfully possess decision-time evidence from a
submission path it never traversed. Keeping it permanently unresolved conflates
an impossible provenance datum with genuine provider or accounting ambiguity,
leaving accurate accounting and ownership behind a gate no replay can clear.
Economic reconciliation does not retroactively grant Risk, Decision, Mandate,
Campaign, or Controlled Proof authority.

Alternatives Considered

Require the identical OmniTrade-submission evidence contract and leave every
external historical order permanently unresolved. Rejected because it makes the
operator recovery bridge non-convergent and makes `reconciliation_required`
describe known inapplicability rather than unresolved economic evidence.

Create a separate external-order reconciliation or accounting path. Rejected
because provider lookup, fills, fees, accounting, ownership, audit, and
idempotency must remain canonical and shared.

Consequences

The reconciliation scheduler can append a newer immutable terminal event for a
fully evidenced external order and clear latest-event gates. Missing or
conflicting provider identity, order state, fills, accounting, ownership, or
audit evidence continues to fail closed. The terminal event and accounting
evidence retain the external authority classification and never imply governed
OmniTrade execution lineage.

---

## 2026-07

### Controlled Proof Exit Recovery Self-Healing

Decision

Ordinary orchestration supervision is permitted to retry SELL package
progression when a Controlled Proof has already produced a READY SELL
package whose lineage remains PACKAGE_ONLY, instead of requiring
permanent operator intervention.

Reason

Production evidence demonstrated that a transient interruption after
SELL package creation could leave an otherwise valid READY package
orphaned indefinitely. The existing supervision logic detected the
existing SELL package but never attempted package progression again,
preventing autonomous recovery.

Alternatives Considered

Require every PACKAGE_ONLY occurrence to be recovered manually through
operator intervention.

Rejected because the condition represents an implementation gap rather
than an intentional governance boundary.

Consequences

PACKAGE_ONLY SELL packages now self-heal through ordinary supervision
while preserving all existing mandate evaluation, activation authority,
audit logging, idempotency, and fail-closed behavior.

Future Impact

Controlled Proof Exit Recovery remains an operator-authorized recovery
mechanism, but transient package progression failures no longer require
manual intervention solely because a SELL package already exists.

---

## 2026-07-30

### Exit Recovery Is Continuing SELL Authority, Not Renewed Entry Authority

A valid, unexpired, claimed Exit Recovery may satisfy only the expiry-derived
mandate checks needed to close verified proof-owned exposure. The mandate
evidence service independently resolves the typed recovery/proof identity and
enables this rule only for SELL under a CONTROLLED_PROOF-purpose mandate.
Revoked or killed mandates, unauthorized versions, scope mismatches, BUY,
disallowed products/sides, excessive notional, Risk denial, and kill switches
remain rejecting conditions.

Production recovery `bf2c040d-4ee6-4091-adeb-9dbb633d2b65` passed exposure,
SELL eligibility, and Risk but was blocked before package creation because its
recovery ID existed only in untyped context/idempotency text; ordinary mandate
evaluation therefore rejected the expired entry mandate. A BLOCKED or EXPIRED
recovery is terminal, carries `completed_at`, and cannot be claimed or resumed.
Dispatch completion means only an actual final outcome and is logged with that
outcome. The production recovery remains terminal; any later replacement must
use one new persisted key after deployment and read-only review.

The production row's pre-fix `completed_at=null` is preserved under treatment
A as immutable historical evidence. `status=BLOCKED` is authoritative for
claimability, and the database's per-proof unique index applies only to
AUTHORIZED/IN_PROGRESS rows. GET returns the newest recovery by
`authorized_at`; replay remains key-specific. Future terminal transitions set
`completed_at`, but the legacy row is neither rewritten nor reactivated.

Production replacement recovery `31a927a6-f7ea-4ea2-9966-426bfe659b64`
established that continuing-exit identity alone was insufficient: the
CONTROLLED_PROOF-specific open-exposure check still rejected a SELL when the
existing fill-value exposure was slightly above its $5 entry cap. A SELL does
not deploy or increment exposure, so that predicate evaluates zero for SELL;
BUY remains `existing controlled-proof exposure + proposed BUY notional`.
This is not an exposure-limit waiver for entry. Both failed recoveries remain
immutable terminal evidence and Proof #3 remains a recovery/hardening proof.
The SELL comparison is explicitly incremental (`0 <= cap`), while canonical
preview sizing separately binds `base_size` to the verified proof-owned
quantity. It is therefore neither `existing + 0` nor an unbounded SELL.

### Completed Recovery Outcomes Select Provider-Executed SELL Lineage

Immutable package history is not itself execution ambiguity. Post-fill
recovery projection considers package-only, unclaimed, and unsubmitted SELL
attempts historical; the authoritative candidate is the unique package whose
claim links to a provider-identified SELL order. More than one such submitted
lineage remains ambiguous and fails closed. A selected lineage must still have
a completed claim, FILLED order, FILLED reconciliation, zero ownership, and
complete BUY/SELL accounting before terminal P&L is published.
On successful publication, `ControlledProofExitRecovery.status` becomes
`COMPLETED`, `completed_at` records projection completion, and active
`blocked_reason`/`failure_reason` are cleared. The audit `before_state`
preserves the earlier BLOCKED classification and explanation.

### BLOCKED Proofs May Receive Exposure-Conditional SELL-Only Recovery Authority

Decision

`BLOCKED` remains a terminal Controlled Proof status and never regains BUY
authority. A separate Exit Recovery may nevertheless be authorized when, and
only when, the proof has a canonical reconciled FILLED BUY, no SELL execution,
and the positive quantity derived from its package/claim/order/accounting
lineage exactly equals both its scoped position projection and profile custody
quantity. All existing open-order, reconciliation, mandate, Risk, audit,
idempotency, and provider-boundary gates remain authoritative.

Reason

Proof `ef0ca4df-e520-4764-b6a5-71bcf165f43a` crossed the live-capital boundary
and was later terminalized `BLOCKED`. Exit Recovery's original status set,
unchanged since `c296baf`, contained only `EXPIRED` and `FAILED`, so its POST
returned HTTP `400` (`Controlled Proof is not eligible for exit recovery`,
`details.status=BLOCKED`) before evaluating the verified exposure. No recovery
or authorization audit was persisted. Treating every BLOCKED proof as eligible
would weaken terminal semantics; conditioning the separate recovery authority
on exact, independently scoped exposure evidence closes only the stranded-live-
capital gap and fails closed on any disagreement.

Consequences

An ordinary BLOCKED proof with no exposure remains ineligible. A qualifying
proof stays BLOCKED while the existing recovery lifecycle creates or resumes
exactly one governed SELL package. A rejected request's idempotency key is not
consumed because no recovery row exists before successful validation, so the
same persisted key may be replayed after deployment once read-only production
checks reconfirm eligibility and absence of a recovery.

### Exit Recovery Activation Eligibility Is Not Bound to a Package's Creation-Time Recovery Stamp

Decision

A claimed (`IN_PROGRESS`), unexpired Controlled Proof Exit Recovery
authorizes automatic activation of its proof's linked SELL package
(`proof.sell_package_id == package.package_id`) regardless of which
recovery, if any, was active when that package was originally created.
A package's own `market_evidence_identity.controlled_proof_exit_recovery_id`
stamp is no longer part of the eligibility check. A claimed recovery
that fails to reach activation on a given dispatch is now always
terminalized with an explicit reason: `BLOCKED` (with the executor's
`final_reason_code`) when the outcome was a definitive, config/scope-
level `failed_closed=True` stop, otherwise a retryable `failure_reason`
recorded via the existing waiting mechanism, bounded by the recovery's
own expiry.

Reason

Production evidence (proof `345fc153-3db1-4514-8d0e-c7e0fe77790e`,
recovery `819754a6-1566-4523-a2d4-b8447ab6868c`) showed an authorized,
claimed Exit Recovery failing closed on
`controlled_proof_activation_override_blocked reason=controlled_proof_not_active`
on every dispatch, with the claimed recovery left `IN_PROGRESS`
indefinitely and no recorded reason. Root cause: the eligibility check
(added in commit `0a167eb`, "Reissue expired exit recovery sell
authority") additionally required the package's stamp to equal the
currently claimed recovery's id. That is only ever true for a package
freshly created (or reissued) under that exact recovery — it can never
be true for `authorize_controlled_proof_exit_recovery`'s own documented
`allow_existing_sell_package` contract (present since the first Exit
Recovery commit, `c296baf`), which explicitly authorizes resuming a
SELL package that predates the recovery entirely, or that was stamped
under an earlier, now-terminal recovery attempt for the same proof
("the later authority may resume only that package" —
`docs/CONTROLLED_PROOF_ACTIVATION.md`). The stamp check was a
regression against the original, correct, tested design, not a
deliberate tightening: `proof.sell_package_id` is already the sole,
authoritative, exclusively-owned binding between a proof and its one
governed SELL package (set once, cleared only by
`supersede_stale_exit_recovery_sell_package`), so it alone is
sufficient; the stamp match added no real protection while silently
foreclosing a contract the same commit's own authorization code still
advertised as supported.

Alternatives Considered

Keep the stamp match but exempt only a `None` stamp (a package that
never touched any recovery). Rejected: it would still incorrectly
reject the "resume across two recovery attempts for the same proof"
case, which `authorize_controlled_proof_exit_recovery` and
`docs/CONTROLLED_PROOF_ACTIVATION.md` both explicitly document as
supported, and which is the more likely production shape (a prior
recovery attempt already existed and went terminal before this one was
authorized).

Immediately transition a claimed recovery to `BLOCKED` on any
non-`ACTIVATED` dispatch outcome, regardless of cause. Rejected: it
would foreclose legitimate multi-cycle retry within the recovery's own
authorized 1–180 minute window for transient, self-resolving causes,
turning every soft stall into a forced re-authorization. Used the
executor's existing `failed_closed` distinction instead — already the
codebase's own vocabulary for "definitive, not retryable" versus
"no clean outcome this cycle, may still resolve."

Consequences

Exit Recovery's documented "resume a pre-existing package" behavior is
restored and covered by regression tests exercising: a package with no
recovery stamp, a package stamped under an earlier terminal recovery for
the same proof, an unclaimed (`AUTHORIZED`-only) recovery failing
closed, an expired recovery failing closed, and a recovery belonging to
an unrelated proof failing closed. A claimed recovery can no longer sit
`IN_PROGRESS` indefinitely with no recorded reason. Mandate governance,
campaign/package/risk/reconciliation invariants, idempotency, and audit
correlation are unchanged — only the exit-recovery-specific eligibility
binding and terminalization were corrected. Not yet production-deployed
or production-validated as of this writing; see
`docs/00_OPERATIONS_MAP.md`'s Controlled Proof Exit Recovery section for
the next validation step.
