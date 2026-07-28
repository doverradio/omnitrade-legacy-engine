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

✓ Current production blocker (2026-07-28)

Production proving has advanced beyond package-progression diagnosis.

The terminal unresolved reconciliation scheduler defect has been
identified, repaired, deployed, and verified in production.

The scheduler now correctly rediscovers terminal FILLED orders whose
latest reconciliation event remains unresolved.

Production evidence confirms:

• historical terminal orders are rediscovered
• reconciliation executes
• Kraken returns authoritative FILLED status
• reconciliation completes

However, reconciliation still remains:

reconciliation_required

because:

balance_evidence_outcome=missing

The imported external trade does not contain the historical
pre-submit USD balance evidence required by the canonical accounting
reconciliation contract.

Current engineering work is therefore focused on determining whether
this is:

• an intentional reconciliation-policy requirement for externally
imported trades, or

• an unintended consequence of applying the canonical reconciliation
contract to externally executed manual trades.

No changes should weaken reconciliation, fabricate historical
evidence, or bypass existing production safety guarantees until the
intended repository behavior is conclusively established.

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