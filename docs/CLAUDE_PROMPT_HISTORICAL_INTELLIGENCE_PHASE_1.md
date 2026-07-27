# Claude Implementation Prompt — Historical Intelligence Platform Phase 1

You are the implementation agent for the OmniTrade Legacy Engine repository.

## Mission

Begin implementation of the Historical Intelligence Platform through the smallest safe, production-isolated vertical slice.

The first objective is not broad asset coverage, machine learning, distributed workers, counterfactual branching, or historical news.

The first objective is to prove that the actual OmniTrade decision pipeline can run through historical time deterministically, without future leakage, without production writes, and with complete synthetic evidence provenance.

## Required source documents

Read all attached documents before proposing changes. Treat them as architectural constraints, not optional inspiration.

Highest-priority documents:

1. `PROJECT_CONSTITUTION.md`
2. `PROJECT_VISION.md`
3. `SYSTEM_ARCHITECTURE.md`
4. `HISTORICAL_INTELLIGENCE_PLATFORM.md`
5. `HISTORICAL_MARKET_REPLAY_ENGINE.md`
6. `DECISION_INTELLIGENCE_ENGINE.md`
7. `STRATEGY_ENGINE.md`
8. `RISK_ENGINE.md`
9. `DATA_SOURCES.md`
10. `DATABASE_SCHEMA.md`
11. `BACKEND_MODULE_SPECS.md`
12. `API_CONTRACTS.md`
13. `RISK_AND_AUDIT_API_CONTRACTS.md`
14. `SECURITY_AND_SAFETY.md`
15. `REPO_STRUCTURE.md`
16. `00_PROJECT_STATE.md`
17. `02_DECISIONS.md`
18. `06_NEXT_SESSION.md`

Also review these if available because the implementation must remain compatible with the future architecture:

- `ASSET_REGISTRY.md`
- `VENUE_INSTRUMENT_REGISTRY.md`
- `ASSET_UNIVERSE.md`
- `MULTI_ASSET_DATA_PIPELINE.md`
- `MARKET_REGIME_CLASSIFICATION.md`
- `ASSET_CORRELATION_MODEL.md`
- `CROSS_ASSET_OPPORTUNITY_SELECTION.md`
- `OPPORTUNITY_COST_MODEL.md`
- `DYNAMIC_CAPITAL_REALLOCATION.md`
- `PORTFOLIO_INTELLIGENCE.md`
- `CAPITAL_EFFICIENCY_MODEL.md`
- `MULTI_PROVIDER_CAPITAL_ROUTING.md`
- `EXECUTION_ROUTING.md`
- `EXECUTION_QUALITY.md`
- `REPLAY_AGENT_INTERFACE.md`

## Production safety constraints

- Do not submit real orders.
- Do not mutate production balances, positions, campaigns, mandates, schedules, or risk state.
- Do not write synthetic evidence into production evidence namespaces without explicit origin classification and isolation.
- Do not alter the current live autonomous proving campaign.
- Do not weaken fail-closed behavior.
- Do not create a second strategy implementation for replay.
- Reuse the actual canonical strategy, economics, risk, and decision logic wherever technically possible.
- Environment-specific behavior must be supplied through adapters.
- Do not implement any machine-learning or self-modifying behavior in this phase.
- Do not add broad abstractions that are not required by the first vertical slice.

## Required operating concept

The architecture must support, or move cleanly toward supporting, these canonical modes:

- `PRODUCTION`
- `FORWARD_PAPER`
- `HISTORICAL_REPLAY`
- `COUNTERFACTUAL_REPLAY`

For Phase 1, implement only what is required for `HISTORICAL_REPLAY`, but do not hard-code assumptions that would block the other modes.

## Phase 1 target

Implement one complete Golden Historical Path:

Historical candle data

→ deterministic replay clock

→ point-in-time candle visibility

→ existing canonical strategy evaluation

→ existing economics and risk evaluation

→ synthetic BUY, SELL, or HOLD decision

→ synthetic cash and position state transition

→ replay Decision Record with explicit provenance

→ ground-truth outcome after time advances

→ deterministic rerun producing identical results

## Initial scope

Keep the first slice intentionally narrow:

- one canonical asset
- one venue instrument
- one historical dataset already available in the repository or database
- candle data only
- one existing production strategy
- market-order simulation only
- deterministic fee and fill assumptions
- one synthetic portfolio
- no historical news
- no fundamentals
- no distributed workers
- no UI unless a minimal existing admin/research surface can expose status without widening scope

Prefer BTC/USD or the repository's best-supported canonical crypto instrument, but determine this from the codebase rather than assuming symbol conventions.

## Required capabilities

### 1. Deterministic replay clock

Create a clock abstraction or adapter that:

- exposes the current simulated timestamp
- advances only when directed by the replay orchestrator
- prevents reads beyond the current timestamp
- is independent of wall-clock execution speed
- is deterministic and testable

Do not replace production time globally. Inject the replay clock only through the historical runtime boundary.

### 2. Point-in-time historical candle source

Create a historical data adapter that:

- exposes only candles whose availability timestamp is at or before the replay knowledge cutoff
- uses a stable ordering rule
- detects duplicate or non-monotonic timestamps
- fails closed on temporal corruption
- records dataset identity and version where available

No strategy or feature code may query future candles directly.

### 3. Historical replay orchestrator

Create a bounded orchestrator that:

- initializes a replay run
- binds configuration and version metadata
- initializes synthetic portfolio state
- advances through a selected historical window
- invokes the actual canonical decision pipeline at defined decision points
- checkpoints or records progress in a production-isolated namespace
- produces a terminal summary

The orchestrator must be resumable only if this can be implemented safely within the bounded phase. Otherwise document resumption as deferred rather than adding a fragile partial implementation.

### 4. Synthetic portfolio and broker

Implement the minimum deterministic simulation required to model:

- starting cash
- one position at a time if that matches current platform policy
- BUY
- SELL
- HOLD
- fees
- quantity and cost basis
- realized and unrealized P&L
- available cash

Use explicit versioned execution assumptions.

Do not claim order-book realism. Label this first model as candle-based deterministic execution.

### 5. Synthetic Decision Records

Reuse the canonical Decision Record structure wherever possible.

Every synthetic record must include or reference:

- `record_origin = HISTORICAL_REPLAY`
- evidence class
- replay or simulation identifier
- simulated timestamp
- knowledge cutoff timestamp
- historical dataset identifier or version
- strategy version
- risk policy version
- engine or code version where available
- execution model version

Synthetic records must be unambiguously distinguishable from production records.

### 6. Ground-truth evaluation

After a decision is committed and replay time advances, calculate a minimal outcome record using only subsequently revealed data.

The first version may include:

- forward return over a configured horizon
- position outcome if a synthetic trade closes
- maximum adverse excursion if easy to calculate safely
- maximum favorable excursion if easy to calculate safely

Do not let these outcomes influence the original decision.

### 7. Determinism proof

Add tests proving that identical:

- input candles
- replay configuration
- starting cash
- strategy version
- risk policy
- execution assumptions

produce identical:

- decision sequence
- synthetic trades
- ending portfolio state
- replay Decision Records
- outcome records

### 8. Future-leakage tests

Add explicit regression tests proving that:

- the strategy cannot access candles after the simulated cutoff
- indicators use only visible history
- advancing the underlying dataset without advancing the replay clock does not change the current decision
- synthetic outcomes are unavailable before their horizon is reached

## Repository analysis required before coding

Before changing code:

1. Inspect the repository structure.
2. Identify the canonical production decision path from market evidence through strategy, economics, risk, Decision Record creation, and execution.
3. Identify existing paper/backtest/replay abstractions that should be reused or replaced.
4. Identify current database models and evidence namespaces.
5. Identify the safest seam for clock and data-source injection.
6. Identify whether a database migration is actually required.
7. Identify risks of accidentally invoking production providers or workers.

Do not begin with speculative implementation.

## Deliverable sequence

Respond first with an implementation plan containing:

1. Current architecture discovered in the repository.
2. Exact production path that will be reused.
3. Proposed Phase 1 boundaries.
4. Files to create or modify.
5. Database changes, if any.
6. Test plan.
7. Production-isolation proof.
8. Known deferrals.
9. Estimated implementation risk.

Then stop and wait for approval before editing code.

Do not implement until the plan is approved.

## Quality standard

The implementation must be:

- deterministic
- fail-closed
- point-in-time correct
- auditable
- replayable
- production isolated
- minimally invasive
- covered by focused tests
- compatible with future multi-asset and counterfactual expansion

## Non-goals for Phase 1

Do not implement:

- 100+ assets
- equities, forex, metals, or other asset classes
- historical news
- historical fundamentals
- distributed replay workers
- adaptive strategy selection
- automated self-improvement
- AI-generated strategy changes
- broad UI dashboards
- global capital intelligence
- knowledge graphs
- financial ontology

Those depend on proving the Golden Historical Path first.

## Success criteria

Phase 1 is successful only when OmniTrade can:

1. Start from a historical timestamp.
2. Advance through a bounded historical candle sequence.
3. Run the actual canonical decision pipeline without future leakage.
4. Generate synthetic Decision Records with complete provenance.
5. Maintain a coherent synthetic portfolio.
6. Reveal outcomes only after simulated time advances.
7. Reproduce the exact same result on an identical rerun.
8. Demonstrate that no production state or provider was touched.

Begin by analyzing the repository and returning the requested implementation plan only.
