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

---

## 2026-08-03

### Entry Intelligence and Adaptive Limit-Order Decision Layer (Phases 1-5 only)

Decision

Added a strictly additive "entry intelligence" layer
(`app/services/entry_intelligence/{evidence,decision}.py`) that runs ONLY
after the existing net-edge gate (authoritative.py's
`compose_campaign_authoritative_cycle`) has already rejected a market-entry
BUY with `non_positive_net_edge`. It never changes that gate's own
accept/reject boundary or `historical_gross_return_pct` sourcing for the
market-entry decision. It answers a strictly additional question: does a
bounded, economically-derived LOWER entry price make the setup viable
instead? Produces one of BUY_LIMIT / WAIT / REJECT (BUY_NOW is reserved for
the case where a positive market edge is passed in, for API completeness;
the wired call site only ever invokes this after market-entry was already
rejected). Attached to the same `rejected_candidates` record
(`entry_intelligence_decision`, `entry_intelligence_reason`) and logged as
one explainable line (`entry_intelligence_decision_evaluated`), surfaced in
`tools/operator_console.py`.

Also added a narrower context-specific evidence hierarchy
(`resolve_context_specific_edge_evidence`): exact strategy + asset +
timeframe (matching the campaign's own candle interval) + regime, falling
back to exact strategy + asset + timeframe (any regime), falling back to
today's existing blended-aggregate figure (byte-identical to current
production behavior when neither narrower tier has enough samples), failing
closed ("unavailable") only when no tier has any evidence. Each of the two
new, narrower tiers requires its own minimum sample size
(`ENTRY_INTELLIGENCE_MIN_HORIZON_REGIME_SAMPLE_SIZE`=20,
`ENTRY_INTELLIGENCE_MIN_HORIZON_SAMPLE_SIZE`=10) before being trusted; the
selected tier's mean is then reduced by an uncertainty penalty
(`z * standard_error`, `z`=`ENTRY_INTELLIGENCE_UNCERTAINTY_PENALTY_Z`=1.0)
before being used as the "conservative_gross_edge" that BUY_LIMIT math is
based on. Added action-scoped sample standard deviation
(`buy/sell/hold_raw_return_stdev_pct`) and (horizon, regime)-conditioned
buckets (`StrategyScorecard.regime_conditioned_buckets`) to
`strategy_outcomes/service.py` to support this.

Maximum profitable entry price is derived, not arbitrary: solved directly
from the SAME expected-exit-price evidence already used for the
market-entry evaluation (`expected_exit_price = market_price * (1 +
conservative_gross_edge_pct/100)`), then
`max_price = expected_exit_price / (1 + total_cost_pct/100)`, rounded DOWN
to a configured price precision. A proposal is only made when the implied
discount from market is within a bounded safety cap
(`ENTRY_INTELLIGENCE_MAX_LIMIT_DISCOUNT_PCT`=1.0%) and the limit-priced
notional (using the SAME base quantity Risk already approved at market
price -- no second Risk evaluation was run this session) still clears
`asset.min_order_notional`.

Reason

Production evidence (repeated `buy_agreement_threshold_met` BUY candidates
immediately terminated with `non_positive_net_edge`, aggregate BUY scores
up to 3.33 alongside gross edges of roughly -0.02% to -0.18%) was traced
and found consistent with the net-edge gate correctly rejecting genuinely
non-positive expected edge AT MARKET PRICE, using the SAME evidence
pipeline already hardened across the "First Autonomous Profit" gate's prior
seven rounds of fixes (action-scoping, raw-vs-fee-adjusted sourcing,
double-fee-count correction — see `docs/00_PROJECT_STATE.md` project
memory). No further defect was found in that gate itself. The remaining
architectural gap was that only market-entry was ever evaluated: the
system had no way to express "this specific setup is not attractive right
now at market, but would be at a bounded lower price" — exactly the mission
of `docs/OMNITRADE_ENTRY_INTELLIGENCE_AND_LIMIT_ORDERS_PROMPT.md`.

Alternatives Considered

Change the existing market-entry gate itself to use the new, narrower
(regime/timeframe-conditioned, uncertainty-penalized) evidence directly,
potentially flipping some existing rejections to acceptances. Rejected
this session: doing so would retroactively change a live, already
multiply-hardened safety gate's outcome under evidence that, while
directionally sound, has not yet been shadow-validated (Phase 10 of the
governing prompt) against real forward outcomes. The chosen design gets
the same tighter evidence to the SAME final economic question (is there a
profitable entry for this setup) through a new, clearly-labeled, strictly
additive decision (BUY_LIMIT) instead of silently reinterpreting the old
one.

Wire BUY_LIMIT decisions through to live Kraken order submission this
session. Rejected: `app/services/exchange_connections/providers/kraken_spot.py::submit_order`
explicitly supports only MARKET orders in the current execution profile
(`request.order_type.upper() != "MARKET"` is rejected outright), and no
limit-order lifecycle/supervision worker exists yet. Building and
live-wiring full limit-order execution against a real exchange without
that adapter support, untested, in one session was judged higher-risk than
the value of shipping it half-built. This session delivers the fully
tested, audited decision layer and defers live submission explicitly (see
`06_NEXT_SESSION.md`).

Consequences

The existing net-edge gate's tests, log lines, and accept/reject outcomes
are provably unchanged (full existing unit + integration suites re-run
before and after this change with identical pre-existing failures only).
Every `non_positive_net_edge` rejection now additionally carries a
BUY_LIMIT/WAIT/REJECT decision with full provenance, visible in
`rejected_candidates`, one log line, and the operator console. No live
order is submitted for BUY_LIMIT decisions yet — Phases 6-11 of the
governing prompt (real limit-order construction/submission, lifecycle
state machine, continuous supervision worker, shadow counterfactual
validation, bounded live proving lane) remain unimplemented, tracked as
the explicit next task in `06_NEXT_SESSION.md`. Small-account/$5-notional
interaction proven and tested: a limit price below market, using the SAME
base quantity Risk approved at market, always implies a lower notional
than the original approved notional, so a campaign approved right at
`asset.min_order_notional` will correctly REJECT rather than propose an
undersized order — this is a real economic constraint, not a bug, and
must be considered before enabling BUY_LIMIT with production position
sizing.

---

## 2026-08-03 (continuation)

### Real Kraken LIMIT execution path, authoritative pre-execution attempt lifecycle, shadow validation

Decision

Completed the execution path the prior round of this same day deferred:
BUY_LIMIT is no longer diagnostic-only. `app/services/exchange_connections/providers/kraken_spot.py::submit_order`
now genuinely supports `order_type="LIMIT"` for both sides (real
`/private/AddOrder` payload with `ordertype=limit`, `price`, base-unit
`volume`, GTC/IOC `timeinforce`; no ticker fetch — the limit price is
caller-supplied, never derived from a market reference), plus a new
`cancel_order` method (`/private/CancelOrder`, distinguishing `success` /
`already_resolved` / `ambiguous` / `rejected`). Provider precision
(`pair_decimals`/`lot_decimals`/`ordermin`/`costmin`) is sourced from the
SAME `/public/AssetPairs` call the MARKET path already uses — no longer a
placeholder for the LIMIT path specifically, though the entry-intelligence
decision layer's `price_decimals` setting is still a placeholder at
proposal time (see prior entry) since the real precision is only fetched
at submission.

A new authoritative, persisted state machine (`AutonomousLimitEntryAttempt`,
migration `20260803_0065`) makes a BUY_LIMIT decision a genuine
pre-execution gate: `entry_intelligence_decision_evaluated` (unchanged)
→ `propose_and_risk_evaluate_limit_entry` creates a `PROPOSED` row and runs
a REAL Risk Engine evaluation (`evaluate_signal_risk`/`persist_risk_decision`,
same functions the market-entry path uses) at the proposed limit
price/quantity → `READY` or `REJECTED`. A new supervisor
(`app/services/orchestration/autonomous_limit_entry_worker.py`,
`advance_due_limit_entry_attempts`, wired into
`continuous_pipeline_worker.py`'s per-cycle loop the same way
`poll_unresolved_live_orders`/`advance_one_autonomous_proof_sell_stage`
already are) advances `READY → SUBMITTED → OPEN/PARTIALLY_FILLED → FILLED`,
handles expiration and invalidation by requesting cancellation
(`CANCEL_REQUESTED`), confirms cancellation via `lookup_order` re-verification
(never trusts the cancel response alone) before marking `CANCELLED`, and
creates at most one bounded `REPLACED` chain (new price capped at
`maximum_profitable_entry_price`, a DB `CHECK` constraint
(`ck_alea_never_chase_above_max`) makes "never chase above" a data
invariant, not just application logic).

Also added `replay_rejected_buy_candidate_counterfactual`
(`app/services/entry_intelligence/shadow_validation.py`, Phase 10): a
pure, read-only replay of a candidate against already-stored candle data
answering whether the proposed limit would have filled, time-to-fill,
MFE/MAE, and net P&L at 15m/30m/1h/2h/4h for both the market-entry and
limit-entry alternatives, plus missed-opportunity/avoided-loss framing
when the limit never fills. Reuses `strategy_outcomes/service.py`'s own
`_load_close_at_or_before`/`_load_window_candles` rather than a competing
replay implementation.

Reason

The prior round's own explicit "not yet built" list named exactly this:
Kraken adapter MARKET-only, no persisted lifecycle, no supervisor, no
shadow validation. This round closes each of those in the smallest way
that is genuinely real (actually calls the provider, actually evaluates
Risk, actually persists restart-safe state) rather than expanding the
diagnostic-only surface further.

Alternatives Considered

Build the LIMIT execution path on top of the existing canonical-package /
`AutonomousExecutionClaim` machinery (the same one market BUYs use), so
BUY_LIMIT and BUY_NOW share one execution lineage. Rejected for this
round: that machinery's `LiveCryptoOrder.execution_claim_id` column is
constrained (`ck_lco_reduce_only_lifecycle`) to SELL/reduce-only rows
only, and BUY claims link the other way (`AutonomousExecutionClaim.live_order_id`
points at the order) via a deeper chain
(`autonomous_order_preparation.py` → `commissioned_entry_execution.py` →
`LiveCryptoOrderService.submit`) that assumes a canonical package/dry-run
order already exists. Retrofitting LIMIT through that whole chain in this
session was judged materially riskier than a new, narrower, BUY-only lane
that reuses the same provider adapter, Risk Engine, and reconciliation
primitive but not the claim/package layer. This is why custody is not yet
established for a filled BUY_LIMIT — see Consequences.

Give the CHECK constraint added to `live_crypto_orders`
(`ck_lco_limit_price_matches_order_type`) an exact-case comparison
(`order_type = 'MARKET'`). Rejected after it broke `test_reconciliation_scheduler.py`
in the real (non-mocked) test run: several existing fixtures use lowercase
`"market"`, which was always valid since `order_type` was unconstrained
free text before this change. Used `UPPER(order_type) = 'MARKET'` instead
— same invariant, tolerant of pre-existing case variance.

Consequences

A real Kraken LIMIT order can now be proposed, Risk-evaluated, packaged
(the attempt row + a `CryptoOrderPreview` + a `LiveCryptoOrder` row),
submitted, observed OPEN, partially filled, filled, cancelled (with
provider re-verification), and replaced exactly once within the
economic bound — all with real unit test coverage (Kraken payload/
precision/cancel, Risk approval/rejection, submit/poll/partial-fill/
expire/cancel/replace/restart-recovery/unknown-provider-state,
shadow-fill/no-fill/avoided-loss). **Custody is explicitly NOT
established** for a filled BUY_LIMIT — `establish_buy_custody` is never
called from the new worker (asserted by a dedicated test); a fill is
reconciled and accounted for via the existing `reconcile_live_order_and_fills`,
but does not yet transition into `AutonomousPositionCustody`. Also not
yet wired: a live per-instrument reference price feed into the
supervisor's `current_reference_price` (so replacement, while implemented
and tested, will not fire automatically in production yet), and real
provider-precision (`pair_decimals`) informing the entry-intelligence
decision layer's `preferred_limit_price` before submission time (the
Kraken adapter now fetches real precision at SUBMISSION time and
requotes/rejects there, so no incorrect price is ever actually sent — the
gap is only that the proposal-time price shown in diagnostics may not yet
match final submission precision exactly). No live proving-lane
enablement, and no bounded live BUY_LIMIT capital deployment, has
occurred — this remains explicitly a code-and-test-only round. Full
existing unit (2805 passed) and integration (233 passed) suites re-run
before and after; the same 17 unit + 9 integration pre-existing failures
(confirmed via `git stash` against unmodified `master`) are unchanged,
no new failures.

---

## 2026-08-04 — Branch `feature/entry-intelligence-limit-orders` (continuation on top of commit `3580c3b`)

### FILLED BUY_LIMIT now reaches the exact same authoritative AutonomousPositionCustody as a FILLED market BUY

Decision

Closed the exact gap `3580c3b` documented as the reason this branch could
not merge: a provider-confirmed BUY_LIMIT fill was reconciled and
accounted for, but never converted into `AutonomousPositionCustody`. Traced
the full canonical lineage first (LiveCryptoOrder → provider reconciliation
→ accounting → `AutonomousExecutionClaim` → `AutonomousPositionCustody` →
exit authority → autonomous SELL construction/submission/reconciliation →
realized accounting) and confirmed both `AutonomousExecutionClaim` and
`AutonomousPositionCustody` are schema-hard-wired (NOT NULL FKs, plus
`ck_aec_reduce_only_custody_claim`) to a real `CanonicalPreviewPackage` +
`CanonicalProvingActivation` — there is no schema-legal way to reach
custody without them, confirming (not assuming) that "the smallest safe
correction" requires reusing that exact machinery, not building a parallel
one.

Added a fourth `commissioning_entry_mode` to `create_canonical_preview_package`
(`canonical_preview_package.py`) — `"autonomous_limit_entry"` — alongside
the three that already exist (`initial_proving_entry`, `controlled_proof`,
`autonomous_position_exit`). It reuses 100% of the existing package
creation, mandate-authorization, dry-run, and activation machinery
UNCHANGED; the mode only supplies its own already-computed `DecisionRecord`
+ `forced_action="OPEN_POSITION_PROPOSED"` in place of re-deriving a
(necessarily HOLD) decision from a fresh preview cycle — deliberately
distinct from `controlled_proof`, since `establish_buy_custody` explicitly
refuses any Controlled-Proof-linked package.

`autonomous_limit_entry_worker.py` gained `_establish_claim_lineage`
(idempotent: package → mandate authorization → dry run → activation →
`claim_activated_package`, called once per attempt before submission, with
package/activation/claim ids persisted on the attempt row for restart
safety) and `_resolve_claim_scope_and_custody` (calls the SAME, unmodified
`release_execution_claim_scope_if_order_resolved` — the one function that
already calls `establish_buy_custody` for a fully-reconciled FILLED BUY —
then observes and records the resulting `custody_id`, never establishing
custody itself).

Reason

`3580c3b`'s own report named this as the reason the branch could not be
merged or deployed. This round proves the full chain end-to-end (with
tests, not just code) rather than leaving it as a documented gap.

Alternatives Considered

Build a parallel, narrower custody concept scoped to this lane only.
Rejected outright — this is exactly the "competing custody architecture"
the task explicitly prohibited, and the schema itself (NOT NULL FKs on
both `AutonomousExecutionClaim` and `AutonomousPositionCustody`) makes it
structurally impossible to reach the real custody table any other way.

Weaken `establish_buy_custody`'s `reconciliation_status == "filled"`
exact-match requirement so a partial-fill-then-cancel scenario could
establish custody for the partial quantity. Rejected: that invariant is
shared, unmodified, with the market-BUY path; loosening it would change
behavior for BOTH lanes and was judged an unacceptable risk to accept
silently. Confirmed (via `accounting_reconciliation.py:820-837`) that this
exact scenario — cancel-with-partial-fill — has NO existing mechanism to
reach custody even for a market BUY (IOC market orders essentially never
partially fill then sit cancelled, so the gap was previously latent, never
exercised). The chosen behavior: `reconcile_live_order_and_fills` itself
already sets the order's authoritative status to `PARTIALLY_FILLED` (never
`CANCELLED`) whenever some quantity filled before cancellation;
`release_execution_claim_scope_if_order_resolved` has no resolution
mapping for that status, so this lane surfaces it explicitly as
`RECONCILIATION_REQUIRED` (preserving the exact reconciled fill quantity
for manual review) rather than either fabricating custody or silently
discarding the claim as a bare `CANCELLED` release.

Wire the reference-price/replacement safety requirement by reading candle
data. Rejected per the task's own explicit instruction ("never reprice
from stale candle data") — wired `app.services.execution_price_evidence.load_current_execution_price_evidence`
against `KrakenSpotClient.fetch_price_evidence` instead (a real, live
`/public/Ticker` call), with a bounded freshness check
(`AUTONOMOUS_LIMIT_ENTRY_REFERENCE_PRICE_MAX_AGE_MINUTES`, default 2
minutes) that fails closed (no replacement, not a stale price) on any
mismatch, staleness, or provider failure.

Consequences

The full chain — BUY_LIMIT proposed → Risk-approved → claimed (real
`AutonomousExecutionClaim` via the real, unmodified `claim_activated_package`)
→ packaged (real `CanonicalPreviewPackage`, `CanonicalProvingActivation`)
→ submitted → observed OPEN → partially filled → filled → reconciled →
accounted → converted into `AutonomousPositionCustody` → eligible for the
existing autonomous exit-management chain (`evaluate_due_custodies` /
`issue_exit_authority` / `revalidate_active_exit_authorities`, all
completely unmodified and already running every orchestration cycle) — is
now real and tested (37 new/updated tests across the worker, the migration,
and the canonical package mode; 125 pre-existing `canonical_preview_package.py`
tests re-verified unchanged). A new, dedicated, default-`False`
`AUTONOMOUS_LIMIT_ENTRY_SUBMISSION_ENABLED` flag gates only the live
provider-submission step — proposal, Risk evaluation, claim/package
construction, and shadow evaluation all continue to operate regardless
(diagnostics are never gated). No live capital has been deployed; no
migration has been applied to any real database; nothing was committed
beyond this session's own working tree. Full unit (2830 passed) and
integration (233 passed) suites re-run before and after this round's
changes; the same 17 unit + 9 integration pre-existing failures (confirmed
via `git stash` against unmodified `master`) are unchanged, no new
failures. Added `tools/shadow_validate_recent_rejections.py`
(read-only; journal-log-driven, since `non_positive_net_edge_rejection_explained`
is not persisted to any DB table) for running Phase 10 shadow validation
against real production history once deployed — not run against real
production data this round, since no local database or VPS access exists
in this environment; the script's own log-parsing logic is unit-tested.
