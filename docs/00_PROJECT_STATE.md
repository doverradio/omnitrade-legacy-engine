# OmniTrade Legacy Engine
# PROJECT STATE

Version:
2.0

Last Updated:
2026-07-25

Authority:
Highest

# Repository Facts

• Default Git branch: master
• Remote: origin
• Canonical repository: https://github.com/doverradio/omnitrade-legacy-engine
• Repository root: omnitrade-legacy-engine
---

# Purpose

This document is the authoritative snapshot of the OmniTrade project.

It records what has actually been proven, what remains unproven, the current engineering objective, and the immediate direction of development.

If this document conflicts with conversation history, this document is considered authoritative until intentionally updated.

---

# Project Vision

OmniTrade is **not** a cryptocurrency trading bot.

OmniTrade is being engineered as an Autonomous Capital Management Platform capable of intelligently allocating capital across multiple financial markets while continuously improving the quality of its own decisions.

The architecture is intentionally market-neutral.

Future asset classes include:

- Cryptocurrency
- Equities
- ETFs
- Options
- Futures
- Forex
- Prediction Markets
- Additional financial markets

No future expansion should require architectural redesign.

---

# Current Objective

Achieve the first fully autonomous profitable real-money trade while preserving:

- Explainability
- Auditability
- Deterministic behavior
- Capital preservation
- Risk governance

Every engineering decision should move the platform closer to this objective.

---

# Current Milestone

## FIRST AUTONOMOUS PROFIT

Definition of Done

One commissioned autonomous capital campaign performs:

Campaign Selection

↓

Strategy Selection

↓

Risk Approval

↓

Production BUY

↓

Autonomous Position Management

↓

Production SELL

↓

Reconciliation

↓

Accounting Completion

↓

Verified Positive Net Profit

without operator intervention during execution.

Success is waking up to more money than when the campaign began.

---

# Proven Capabilities

## Execution Layer

✅ Live Kraken authentication

✅ Live production BUY

✅ Live production SELL

✅ Live production reconciliation

✅ Terminal reconciliation recovery scheduler

✅ Controlled Proof Exit Recovery authorization

✅ Controlled Proof Exit Recovery claiming

✅ PACKAGE_ONLY SELL progression recovery


## Decision Layer

✅ Decision Records

✅ Replay architecture

✅ Decision Intelligence

✅ Risk Engine

✅ Position lifecycle

✅ Immutable audit evidence

## Capital Management

✅ Autonomous Capital Campaign architecture

✅ Campaign governance

✅ Campaign lifecycle

✅ Campaign identity persistence

## Platform

✅ Provider-neutral execution layer

✅ Exchange abstraction

✅ Production accounting framework

✅ Commissioned proving workflow

---

# Not Yet Proven

The following remain before the First Autonomous Profit milestone is complete.

□ One commissioned campaign executes a production BUY.

□ Campaign identity remains authoritative throughout reconciliation.

□ Accounting completes successfully.

□ Autonomous lifecycle manages the position.

□ Production SELL completes.

□ Net profit is verified.

---

# Engineering Philosophy

OmniTrade optimizes for:

Correctness before speed.

Evidence before assumptions.

Architecture before features.

Production proof before expansion.

Safety before automation.

Decision quality before profitability.

Long-term compounding over short-term gains.

---

# Current Development Philosophy

Development proceeds in small, bounded implementation tasks.

Large speculative implementation prompts are avoided.

Every completed task should be:

- testable
- reviewable
- deterministic
- independently valuable

---

# Current Priority

Before implementing any new feature, ask:

"Does this move OmniTrade closer to First Autonomous Profit?"

If the answer is no, the work should normally be postponed.

---

# Long-Term North Star

The long-term objective is not merely profitable trading.

The objective is a continuously improving autonomous capital management platform whose knowledge compounds alongside its capital.

Every decision becomes permanent knowledge.

The knowledge compounds.

The capital compounds.

Both improve together.


---

## Production Alembic Procedure

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

# Current Proven Runtime Behavior

✓ Autonomous worker cycles execute repeatedly.

✓ Multiple strategies generate BUY and SELL proposals.

✓ Decision Records continue to be generated.

✓ Risk Engine evaluates candidate trades.

✓ Regime-aware strategy weighting is production-validated.

✓ BUY candidates have been observed passing Strategy, Economics, and Risk
and reaching OPEN_POSITION_PROPOSED.

✓ Current production blocker (2026-07-30)

Controlled Proof #3 (`ef0ca4df-e520-4764-b6a5-71bcf165f43a`) exposed an Exit
Recovery authorization gap: its governed POST returned HTTP `400`,
`Controlled Proof is not eligible for exit recovery`, with
`details.status=BLOCKED`, despite a reconciled FILLED BUY and verified open
proof-owned exposure. The rejected request created no recovery. The correction
keeps `BLOCKED` terminal but permits separate SELL-only recovery authority only
when canonical proof quantity exactly equals scoped position and profile
custody quantities and every existing reconciliation/provider guard passes.
Implementation is local and awaiting review/deployment; no recovery has been
authorized under this correction.

The resulting production recovery (`bf2c040d-4ee6-4091-adeb-9dbb633d2b65`)
was authorized and claimed, passed SELL eligibility and Risk ALLOW, then became
BLOCKED with `controlled_proof_mandate_not_authorized` before package creation.
Root cause was ordinary mandate evaluation applying the expired entry mandate
without typed recovery context. The local correction recognizes only verified
unexpired recovery-owned SELL as continuing exit authority and makes terminal
recovery timestamps and dispatch outcome logs truthful. The existing BLOCKED
recovery is not resumable and must not be replayed; one replacement requires a
new post-deployment operator checkpoint. Proof #3 remains a hardening/recovery
proof and the clean qualification streak remains 0 of 5.
The row's historical `completed_at=null` is retained unchanged: it is a legacy
terminal anomaly, not an active state. New BLOCKED/EXPIRED transitions record
`completed_at`; replacement authorization is permitted because per-proof
uniqueness covers only AUTHORIZED/IN_PROGRESS recoveries.

Replacement recovery `31a927a6-f7ea-4ea2-9966-426bfe659b64` under deployed
commit `c8c93ab75b4048ceca5e2fc7c2eec6b3670b5dae` again passed ownership, SELL
eligibility, and Risk ALLOW, then blocked before package creation. Terminal
timestamps were truthful. The missed production predicate was the
CONTROLLED_PROOF open-exposure entry cap being applied to already-owned
exposure during SELL. The correction makes SELL incremental exposure zero;
BUY behavior is unchanged. Both recovery IDs remain terminal and Proof #3
remains outside the clean streak (0 of 5).

Third recovery `825c2010-cad9-4de7-af0c-019f16a8e617` successfully executed
the governed Kraken SELL (`ONJESU-CVFF3-TOIZEI`): package, claim, internal
order, provider fill, reconciliation, and claim completion all succeeded and
remaining position is zero. Terminal projection then failed with
`canonical_sell_package_match_count_invalid` because the projector counted
historical SELL-package attempts before distinguishing provider-executed
lineage. The local correction preserves every attempt but selects the sole
provider-identified SELL lineage, failing closed if more than one submitted
lineage exists. When the complete reconciled outcome is proven, the recovery's
current state becomes `COMPLETED`, its stale blocker is cleared, and the prior
BLOCKED state remains in immutable audit history. No further recovery or SELL
is authorized.

Production proving has advanced beyond the external reconciliation
investigation.

The external historical-order reconciliation policy has been
implemented, deployed, and verified.

The PACKAGE_ONLY SELL package progression defect has also been
identified, repaired, deployed, and verified in production.

The Controlled Proof authority-propagation defect (automatic package
activation losing Controlled Proof context and evaluating under
`GLOBAL_CONFIGURED_SCOPE controlled_proof_id=None`) is now
**production-confirmed fixed**: a fresh Exit Recovery dispatch reached
`automatic_activation_scope_resolved authority_mode=CONTROLLED_PROOF_DERIVED_SCOPE`
with the real `controlled_proof_id`.

That same production evidence surfaced a second, deeper defect one step
further into the same flow: activation then failed closed with
`controlled_proof_activation_override_blocked reason=controlled_proof_not_active`,
and the claimed recovery was left `IN_PROGRESS` indefinitely with no
recorded reason. Root cause: the exit-recovery eligibility check
required a SELL package's own creation-time
`controlled_proof_exit_recovery_id` stamp to equal the currently claimed
recovery's id — which is never true for the documented "resume a
pre-existing SELL package" case Exit Recovery exists to handle. Fixed
(see `docs/00_OPERATIONS_MAP.md`'s Controlled Proof Exit Recovery
section for the full root cause and change list): eligibility now rests
on the proof's own authoritative `sell_package_id` link plus a genuinely
claimed, unexpired recovery for that exact proof — no stamp match
required — and a claimed recovery that fails to activate now always
receives an explicit `BLOCKED` or retryable `failure_reason` instead of
sitting silently `IN_PROGRESS`.

The next production validation is a **fresh** Exit Recovery dispatch
(new authorization) confirming activation now succeeds end-to-end:
execution claim created, order submitted, reconciliation completed,
autonomous lifecycle continuing toward First Autonomous Profit.

No implementation should bypass governance, weaken mandate authority,
or reduce auditability.

✓ Bounded live multi-asset expansion foundation (2026-07-25): the
autonomous worker (continuous_pipeline_worker.py) can now evaluate a
configured roster of Kraken spot products (BTC-USD plus any of
ETH-USD/SOL-USD via AUTONOMOUS_CYCLE_ADDITIONAL_PRODUCTS) in the same
worker cycle, reusing the existing campaign-composition opportunity
ranking (authoritative.py's candidate_rows.sort + selected_decision) to
pick at most one winning instrument per cycle -- no parallel trading
architecture, no new execution provider, no weakened Risk/economics/
mandate gates. Default configuration (unset) is byte-identical to the
prior BTC-only behavior. NO asset beyond BTC-USD is authorized to trade
yet -- that requires an explicit campaign/mandate scope change the
operator must apply manually (see 02_DECISIONS.md, "Bounded Live
Multi-Asset Expansion").

✓ Parallel authorized lanes (2026-07-25): production proving-campaign
work (First Autonomous Profit) and expansion-foundation work
(Historical Intelligence Platform Phase 3: operating modes, evidence
contracts, isolated simulation persistence) are now both authorized to
proceed in parallel. Expansion-foundation work is production-isolated
by construction (separate `SimulationBase`, separate
`OT_SIMULATION_DATABASE_URL`, `IsolationGuard`) and must never delay or
alter the live proving campaign. See `02_DECISIONS.md` (2026-07 —
"Parallel Authorized Lanes").
