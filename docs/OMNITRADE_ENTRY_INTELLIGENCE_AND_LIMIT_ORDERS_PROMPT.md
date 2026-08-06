# OmniTrade — Entry Intelligence and Adaptive Limit Orders

## Mission

Implement the missing bridge between strategy recognition and autonomous capital deployment.

The current system repeatedly reaches:

```text
action=BUY
reason=buy_agreement_threshold_met
```

but every candidate is terminated by:

```text
edge_provenance=scorecard_historical_gross_return_pct
final_reason_code=non_positive_net_edge
termination_stage=hold_no_package_created
```

The system can detect possible opportunity but cannot commit capital.

Correct this without weakening the Risk Engine, bypassing economics, forcing trades, fabricating positive expectancy, or redesigning unrelated architecture.

Deliver both:

1. Context-specific entry intelligence.
2. Restart-safe adaptive limit-order execution.

Required target flow:

```text
pattern recognized
→ context-specific expected value
→ market entry evaluated
→ limit entry evaluated
→ BUY_NOW / BUY_LIMIT / WAIT / REJECT
→ Risk Engine
→ package and provider submission
→ continuous order supervision
→ fill / partial fill / cancel / expire / replace
→ reconciliation
→ custody
→ exit management
```

## Governing Constraints

Preserve:

- Project Constitution
- Risk Engine final authority
- mandate governance
- campaign identity
- provider-neutral execution
- immutable audit records
- replayability
- explainability
- fail-closed behavior
- idempotency
- restart safety
- small-account safety

Do not:

- disable or bypass economics
- bypass Risk
- hardcode positive edge
- force a BUY
- seed fake scorecards
- lower costs without evidence
- treat submission as fill
- treat cancellation request as cancellation
- edit production data manually
- allow indefinite limit orders
- chase price upward without an economic bound

## Current Production Evidence

The system generated at least ten BTC-USD BUY candidates with aggregate BUY scores including:

```text
3.333269
0.970000
0.966667
0.961667
0.960000
0.955833
0.955000
0.745000
0.744167
0.741667
```

Each passed `buy_agreement_threshold_met`.

Each then received negative gross edge from `scorecard_historical_gross_return_pct`, including approximately:

```text
-0.0233%
-0.0877%
-0.0888%
-0.0920%
-0.0948%
-0.1001%
-0.1031%
-0.1755%
-0.1755%
-0.1763%
```

No READY package was created and no live Kraken BUY was submitted.

## Phase 1 — Trace the Existing Economics Path

Before editing behavior, trace exactly how `scorecard_historical_gross_return_pct` is:

1. calculated
2. stored
3. selected
4. scoped to asset, timeframe, strategy, coalition, and regime
5. aged
6. transformed into expected gross edge
7. combined with fees, spread, slippage, and buffers
8. converted into expected net edge
9. used to terminate the campaign cycle

Produce an exact map of:

- files
- functions
- classes
- ORM models
- fields
- queries
- transformations
- fallback branches
- configuration
- logs

Prove whether the defect is:

- wrong source selection
- stale evidence
- inappropriate aggregation
- poor sample quality
- sign inversion
- percentage/decimal mismatch
- gross/net confusion
- fee or slippage double-counting
- paper/shadow/live contamination
- or genuinely negative expectancy

Do not assume the gate is wrong. Prove it.

## Phase 2 — Context-Specific Entry Intelligence

Estimate the expected value of the specific setup now forming using the most relevant available evidence:

- instrument and venue
- timeframe
- strategy identity
- strategy coalition
- signal strength
- market regime
- volatility regime
- trend regime
- expected holding period
- actual exit logic
- sample size
- variance
- recency
- execution mode

Conceptually:

```text
expected future exit value
− proposed entry price
− entry fees
− exit fees
− spread
− slippage
− uncertainty penalty
− required profit buffer
= expected net edge
```

The implementation must be deterministic, explainable, and auditable.

Use a defensible evidence hierarchy, such as:

```text
exact coalition + asset + timeframe + regime
→ exact strategy + asset + timeframe + regime
→ exact strategy + asset + timeframe
→ strategy family + asset + timeframe
→ broader fallback with explicit uncertainty penalty
→ fail closed if no defensible estimate exists
```

Implement only the smallest hierarchy supported by repository evidence.

Expose:

- source record ids
- source sample
- sample size
- mean and median
- variance
- confidence bounds
- recency
- fallback path
- uncertainty penalty
- missing-input flags

Missing evidence must not silently become negative edge. It must fail closed with an explicit reason.

## Phase 3 — Entry Decision Model

Support four authoritative outcomes:

```text
BUY_NOW
BUY_LIMIT
WAIT
REJECT
```

### BUY_NOW

Immediate execution has positive expected net edge and may proceed to Risk.

### BUY_LIMIT

Immediate entry is not attractive enough, but a bounded lower entry price creates positive expected net edge.

### WAIT

The setup is not invalid, but no economically justified order is available yet.

### REJECT

The setup has non-positive expectancy, stale or inadequate evidence, or failed governance.

Do not collapse all outcomes into generic HOLD without provenance.

## Phase 4 — Candidate Entry Object

Create or extend the authoritative candidate representation with:

```text
instrument
venue
side
signal_time
candle_close
timeframe
campaign_id
campaign_version
strategy_identity
strategy_coalition
contributing_strategies
signal_strength
market_regime
volatility_regime
expected_holding_period
expected_exit_price
market_entry_price
maximum_profitable_entry_price
preferred_limit_price
invalidation_price
expiration_time
expected_gross_edge_at_market
expected_net_edge_at_market
expected_gross_edge_at_limit
expected_net_edge_at_limit
confidence
uncertainty_penalty
evidence_provenance
decision
```

Reuse existing domain objects where possible.

## Phase 5 — Maximum Profitable Entry Price

Calculate the highest entry price at which expected net edge remains positive after:

- fees
- spread
- slippage
- uncertainty
- required profit buffer
- tick size
- minimum order size
- provider precision
- rounding
- fee currency
- dust
- $5 notional constraints

Do not use an arbitrary discount.

The preferred limit price must be derived from expected value and provider rules.

## Phase 6 — Adaptive Limit Entry

When market entry fails economics but a lower bounded entry price passes:

```text
decision=BUY_LIMIT
```

The proposed order must include:

- limit price
- quantity/notional
- expiration
- invalidation rule
- maximum age
- replacement policy
- maximum replacement count
- minimum repricing interval
- campaign identity
- idempotency key
- full economics provenance

The order must still pass Risk Engine and mandate governance.

## Phase 7 — Limit Order Lifecycle

Implement or extend:

```text
PROPOSED
→ READY
→ ACTIVATED
→ SUBMITTED
→ OPEN
→ PARTIALLY_FILLED
→ FILLED
→ EXPIRED
→ CANCEL_REQUESTED
→ CANCELLED
→ REPLACED
→ REJECTED
→ RECONCILIATION_REQUIRED
```

Requirements:

- restart-safe
- idempotent
- provider-reconciled
- immutable audit history
- explicit partial-fill handling
- fail-closed unknown provider state

## Phase 8 — Continuous Order Supervision

Add or extend a bounded worker that checks:

- provider status
- filled and remaining quantity
- average fill price
- bid/ask and reference price
- current expected exit
- current market and limit net edge
- signal validity
- regime
- expiration
- Risk state
- mandate state
- campaign state
- replacement eligibility

Use a safe polling cadence and respect provider limits.

## Phase 9 — Cancellation, Invalidation, and Replacement

Cancel a pending order when:

- signal expires
- regime changes materially
- strategy coalition reverses
- expected exit deteriorates
- net edge becomes non-positive
- invalidation price is crossed
- Risk or mandate state changes
- campaign is superseded
- maximum age is exceeded
- another authoritative position invalidates it
- provider state remains ambiguous beyond recovery policy

Replacement is allowed only when:

- economics still supports it
- new price remains at or below maximum profitable entry
- Risk approves
- campaign remains valid
- prior cancellation is provider-confirmed
- replacement count is below the configured maximum
- repricing interval has elapsed

For the initial live proving lane, default to one replacement maximum unless repository evidence supports another value.

## Phase 10 — Shadow Counterfactual Validation

Before live enablement, replay rejected BUY candidates and compare:

- market entry outcome
- proposed limit outcome
- whether the limit would fill
- time to fill
- maximum favorable excursion
- maximum adverse excursion
- strategy-native exit
- stop-loss
- take-profit
- gross P&L
- realistic fees and slippage
- net P&L
- missed-opportunity cost
- avoided-loss value

Evaluate at least:

- 15 minutes
- 30 minutes
- 1 hour
- 2 hours
- 4 hours
- strategy-native exit

Use existing replay infrastructure and do not mutate production state.

## Phase 11 — Small Live Proving Lane

After shadow validation passes, prepare a bounded lane:

```text
instrument: BTC-USD
maximum notional: $5
maximum pending entries: 1
maximum open positions: 1
maximum replacements: 1
explicit short expiration
existing stop-loss controls
existing daily loss controls
existing Risk Engine authority
existing campaign and mandate governance
```

Do not expand to more assets until BTC-USD is proven.

## Required Diagnostics

Add a read-only operator diagnostic showing:

### Strategy

- pattern
- contributing strategies
- strengths
- aggregate score
- regime

### Market economics

- current price
- expected exit
- gross edge
- costs
- uncertainty
- net edge
- decision

### Limit economics

- maximum profitable entry
- preferred limit price
- expected limit edge
- expiration
- invalidation
- replacement policy
- reason for BUY_LIMIT, WAIT, or REJECT

### Lifecycle

- internal and provider order ids
- state
- fill quantity
- remaining quantity
- last provider check
- cancellation/expiration/replacement reason

Prefer the existing operator console or operator API.

## Tests

Add focused unit, integration, and regression tests for:

- edge source selection
- asset/timeframe/strategy/coalition/regime scoping
- stale and sparse evidence
- uncertainty penalty
- sign and percentage conventions
- gross/net conversion
- fees, spread, and slippage
- no double-counting
- expected exit
- maximum profitable entry
- provider tick rounding
- $5 notional behavior
- BUY_NOW / BUY_LIMIT / WAIT / REJECT
- expiration and invalidation
- replacement bounds
- Risk rejection
- provider submission
- OPEN, PARTIALLY_FILLED, FILLED
- cancellation and expiration
- restart recovery
- unknown provider state
- immutable provenance
- no diagnostic writes
- no governance bypass

Recreate a production-shaped case such as:

```text
expected_gross_edge_pct=-0.0948
entry_fee_pct=0.01
exit_fee_pct=0.01
slippage_pct=0.01
expected_net_edge_pct=-0.1248
```

Prove:

1. why the current model rejects it
2. whether corrected evidence changes the estimate
3. whether a bounded limit price creates positive edge
4. whether the final result is BUY_LIMIT, WAIT, or REJECT
5. that no trade is forced

## Production Evidence

Provide exact commands to:

1. validate locally
2. inspect diff
3. commit and push
4. pull on VPS
5. run migrations if required
6. restart only necessary services
7. verify health
8. run diagnostics
9. observe live cycles
10. verify entry decision provenance
11. verify Risk evaluation
12. verify provider submission
13. verify OPEN status
14. verify fill, partial fill, cancellation, or expiration
15. verify reconciliation
16. verify accounting
17. verify custody only after confirmed fill

Do not claim success from order creation alone.

## Definition of Done

Complete only when:

- current edge provenance is fully traced
- root cause is proven
- context-specific expected value is implemented
- BUY_NOW, BUY_LIMIT, WAIT, and REJECT exist
- maximum profitable entry is correct
- preferred limit price is economically bounded
- lifecycle is authoritative
- continuous supervision works
- partial fills work
- expiration and invalidation work
- cancellation is reconciled
- replacement is bounded
- restart recovery works
- Risk authority is unchanged
- mandate and campaign governance are unchanged
- audit remains immutable
- missing evidence fails closed
- tests pass
- shadow evidence is produced
- bounded live lane is ready
- diagnostics explain every decision
- documentation is updated
- deployment commands are supplied

Do not say “100% solved” unless every item is proven.

## Documentation

Update only authoritative documents that changed, likely:

- `00_PROJECT_STATE.md`
- `00_OPERATIONS_MAP.md`
- `02_DECISIONS.md`
- `06_NEXT_SESSION.md`

Append decisions. Do not rewrite history.

## Required Response Format

Return:

1. Executive diagnosis
2. Exact current call path
3. Proven root cause
4. Corrective architecture
5. Entry decision model
6. Limit-order lifecycle
7. Files changed
8. Tests and results
9. Safety review
10. One local command block
11. One VPS command block
12. Production validation checklist
13. Final verdict with proven and unproven items

Begin immediately by reading the governing documents and tracing the current implementation before editing code.
