# OmniTrade — Solve the Over-Conservative Economics Gate Completely

## Role

You are working inside the `omnitrade-legacy-engine` repository.

Your task is to **fully diagnose and correct the autonomous BUY economics path** that is repeatedly rejecting valid-looking BUY opportunities with:

- `final_reason_code=non_positive_net_edge`
- `edge_provenance=scorecard_historical_gross_return_pct`

The goal is **not** to force trades.

The goal is to make the economics gate:

1. use the correct evidence,
2. estimate expected gross and net edge accurately,
3. remain fail-closed,
4. preserve Risk Engine authority,
5. preserve campaign and mandate governance,
6. preserve immutable audit evidence,
7. allow genuinely positive-expectancy trades to proceed,
8. continue blocking genuinely negative-expectancy trades,
9. produce enough transparent evidence that an operator can verify why every BUY was accepted or rejected.

Do not weaken safety controls merely to produce activity.

---

# Current Production Evidence

The autonomous worker is functioning.

The strategy layer repeatedly reaches:

```text
action=BUY
reason=buy_agreement_threshold_met
```

Recent examples include BUY agreement values such as:

```text
buy=3.333269
buy=0.970000
buy=0.966667
buy=0.961667
buy=0.960000
```

However, each candidate is rejected by the economics layer.

Examples:

```text
expected_gross_edge_pct=-0.0233
expected_net_edge_pct=-0.0533
final_reason_code=non_positive_net_edge
edge_provenance=scorecard_historical_gross_return_pct
```

```text
expected_gross_edge_pct=-0.0877
expected_net_edge_pct=-0.1177
final_reason_code=non_positive_net_edge
edge_provenance=scorecard_historical_gross_return_pct
```

```text
expected_gross_edge_pct=-0.0888
expected_net_edge_pct=-0.1188
final_reason_code=non_positive_net_edge
edge_provenance=scorecard_historical_gross_return_pct
```

```text
expected_gross_edge_pct=-0.0920
expected_net_edge_pct=-0.1220
final_reason_code=non_positive_net_edge
edge_provenance=scorecard_historical_gross_return_pct
```

```text
expected_gross_edge_pct=-0.0948
expected_net_edge_pct=-0.1248
final_reason_code=non_positive_net_edge
edge_provenance=scorecard_historical_gross_return_pct
```

The requested and approved notional in these examples was:

```text
5.00 USD
```

The logs also show:

```text
entry_fee_pct=0.01
exit_fee_pct=0.01
slippage_pct=0.01
```

The system is therefore not failing because it cannot generate BUY candidates.

It is failing because the economics layer repeatedly receives or derives a negative historical gross-return estimate, then subtracts costs, guaranteeing rejection.

---

# Governing Principles

Preserve all of the following:

- Constitution
- Risk Engine final authority
- campaign identity
- mandate governance
- provider-neutral execution
- immutable decision records
- replayability
- explainability
- idempotency
- fail-closed behavior
- small-account mode
- production safety
- no fabricated evidence
- no direct database manipulation as a shortcut
- no bypass around economics or risk
- no silent defaults that can authorize capital

Do not redesign the entire architecture.

Use the smallest complete correction supported by evidence.

---

# Primary Mission

Determine exactly how `scorecard_historical_gross_return_pct` is:

1. calculated,
2. persisted,
3. selected,
4. segmented,
5. aged,
6. associated with an asset,
7. associated with a timeframe,
8. associated with a strategy,
9. associated with an aggregate strategy roster,
10. associated with a market regime,
11. used during autonomous campaign composition,
12. converted into `expected_gross_edge_pct`,
13. combined with fees, spread, slippage, and profit buffers,
14. converted into `expected_net_edge_pct`,
15. used to reject or approve a BUY.

Then determine whether the current use is:

- mathematically correct,
- using the correct unit scale,
- using the correct percentage convention,
- using an appropriate historical window,
- using an appropriate sample,
- using current data,
- correctly scoped to BTC-USD,
- correctly scoped to the active timeframe,
- correctly scoped to the selected strategy coalition,
- correctly scoped to the current market regime,
- robust to small sample sizes,
- free from stale or unrelated records,
- free from paper/shadow/live contamination,
- free from duplicated trades,
- free from sign inversion,
- free from fee double-counting,
- free from percentage-vs-decimal errors,
- free from gross-vs-net confusion,
- free from fallback behavior that silently injects negative values.

Do not assume the current value is wrong.

Prove whether it is right or wrong.

---

# Required Work Plan

## Phase 1 — Read Governing and Operational Documentation

Read the repository documentation relevant to:

- project constitution,
- project state,
- decisions,
- next session,
- operations map,
- strategy engine,
- risk engine,
- decision intelligence,
- autonomous campaign orchestration,
- performance analytics,
- scorecards,
- economics,
- small-account mode,
- live execution,
- replay,
- audit evidence.

Identify the authoritative implementation boundaries before modifying code.

Do not rely on conversation assumptions when repository evidence exists.

---

## Phase 2 — Trace the Full Economics Data Lineage

Produce a precise call-path and data-lineage map beginning from:

```text
strategy_aggregate_completed action=BUY
```

and ending at:

```text
non_positive_net_edge
```

The map must identify:

- exact files,
- exact functions,
- exact classes,
- exact ORM models,
- exact fields,
- exact queries,
- exact transformations,
- exact fallback branches,
- exact configuration values,
- exact logging points.

Include a compact text diagram such as:

```text
strategy proposals
→ aggregate scoring
→ scorecard lookup
→ historical gross-return selection
→ cost model
→ expected net edge
→ rejection decision
→ decision record
```

Do not implement anything until this map is complete.

---

## Phase 3 — Build Read-Only Diagnostics First

Before changing decision behavior, add a read-only diagnostic capability that explains every recent `non_positive_net_edge` rejection.

The diagnostic output must include:

### Candidate identity

- campaign id
- campaign version
- decision record id
- instrument
- venue
- side
- timeframe
- candle close
- reference price
- requested notional
- approved notional
- strategy aggregate identity
- contributing strategy identities
- strategy votes
- strategy strengths
- aggregate BUY score
- aggregate SELL score
- market regime
- regime confidence

### Gross-edge evidence

- gross-edge source type
- source record ids
- source strategy identity
- source asset
- source timeframe
- source regime
- source execution mode
- source date range
- source sample size
- source winning samples
- source losing samples
- gross return mean
- gross return median
- gross return standard deviation
- gross return lower confidence bound
- gross return upper confidence bound
- stale-data age
- fallback path used
- missing-input flags

### Cost evidence

- actual configured fee source
- entry fee
- exit fee
- spread source
- spread
- slippage source
- slippage
- profit buffer
- whether any costs are estimates
- whether any costs are duplicated
- percentage unit convention
- dollar conversion

### Final decision

- expected gross edge percent
- expected gross dollars
- expected total costs percent
- expected total costs dollars
- uncertainty penalty
- expected net edge percent
- expected net dollars
- threshold
- final reason code
- human-readable explanation

The diagnostic must be accessible through one of:

- a read-only operator CLI command,
- a read-only API endpoint,
- or a deterministic repository script.

Prefer the lowest-risk existing operational surface.

Do not add a write path unless required.

---

## Phase 4 — Validate the Current Model Against Historical Reality

Create a deterministic offline evaluation that replays prior rejected BUY candidates.

For each rejected BUY:

1. reconstruct the exact decision-time context,
2. preserve the original predicted gross edge,
3. preserve the original predicted net edge,
4. preserve the original costs,
5. simulate or calculate forward outcomes at fixed horizons,
6. calculate what would have happened under the existing exit logic,
7. compare predicted outcome to realized counterfactual outcome.

At minimum evaluate:

- 15 minutes,
- 30 minutes,
- 1 hour,
- 2 hours,
- 4 hours,
- strategy-native exit signal,
- stop-loss outcome,
- take-profit outcome,
- maximum favorable excursion,
- maximum adverse excursion,
- net result after fees and slippage.

The replay must be deterministic and isolated from production writes.

Use existing replay/counterfactual infrastructure where available.

Do not create a competing architecture if an existing subsystem already serves this purpose.

---

## Phase 5 — Classify the Root Cause

The implementation must explicitly classify the defect into one or more of these categories:

### Category A — Wrong source selection

Examples:

- aggregate scorecard using an unrelated strategy,
- wrong asset,
- wrong timeframe,
- wrong regime,
- paper/shadow data contaminating live economics,
- stale historical record selected over current evidence.

### Category B — Wrong mathematics

Examples:

- percentage represented as `0.01` in one layer and `1.0` in another,
- sign inversion,
- gross return interpreted as net return,
- fees applied twice,
- slippage applied twice,
- dollar/percent mismatch,
- annualized metric applied as per-trade metric.

### Category C — Insufficient evidence handling

Examples:

- tiny sample treated as authoritative,
- no confidence interval,
- noisy mean used without uncertainty penalty,
- missing data silently converted into a negative edge,
- stale data accepted without qualification.

### Category D — Strategy expectancy is genuinely negative

If this is the true result, do not weaken the gate.

Instead identify which strategies, regimes, or coalitions are producing negative expectancy and recommend bounded strategy corrections.

### Category E — Cost model is materially overstated

Verify against real Kraken fee schedules, actual provider fills, and observed slippage evidence already stored by the system.

Do not lower costs without evidence.

---

# Required Corrective Design

The final economics model must be context-specific.

A live BTC-USD aggregate BUY should not rely on a broad historical profitability number unless the system proves that number is relevant.

The preferred model should derive expected gross edge from comparable evidence using as many of these dimensions as are reliably available:

- instrument
- venue
- timeframe
- strategy identity
- strategy coalition
- market regime
- volatility regime
- direction
- signal-strength band
- recentness
- execution mode
- sample quality

The model must also handle sparse evidence safely.

A suitable hierarchy may look like:

```text
exact strategy coalition + asset + timeframe + regime
→ exact strategy + asset + timeframe + regime
→ exact strategy + asset + timeframe
→ strategy family + asset + timeframe
→ broader fallback with explicit uncertainty penalty
→ fail closed if no defensible estimate exists
```

Do not implement this hierarchy mechanically unless repository evidence supports it.

Use the smallest version that correctly solves the observed defect.

---

# Confidence and Uncertainty Requirements

Do not treat a raw historical mean as certain.

The corrected model should consider:

- sample size,
- variance,
- recency,
- outliers,
- regime similarity,
- execution-mode similarity,
- confidence bounds.

A trade should be approved only when a conservative estimate of net edge is positive.

For example:

```text
conservative_gross_edge
= estimated_gross_edge
- uncertainty_penalty
```

Then:

```text
expected_net_edge
= conservative_gross_edge
- fees
- spread
- slippage
- required_profit_buffer
```

The exact formula must be repository-appropriate, deterministic, explainable, and tested.

Do not add opaque machine learning.

---

# Small-Account Requirements

The model must work correctly for a $25 account and approximately $5 trade sizes.

Test explicitly for:

- fee drag at $5 notional,
- minimum order sizing,
- rounding,
- precision,
- dust,
- small-dollar expected profit,
- provider minimums,
- realistic slippage,
- profit thresholds.

Do not accidentally require institutional trade sizes for positive economics.

Do not lower thresholds merely because the account is small.

Instead ensure all calculations are correct at small scale.

---

# Tests Required

Add focused tests covering at least:

## Unit tests

- gross-return source selection
- correct asset scoping
- correct timeframe scoping
- correct strategy scoping
- correct regime scoping
- stale-data handling
- sample-size handling
- confidence interval or uncertainty penalty
- percentage convention
- sign convention
- gross-vs-net convention
- fee application
- spread application
- slippage application
- no double-counting
- $5 notional conversion
- zero-edge rejection
- positive-edge approval
- negative-edge rejection
- missing-evidence fail-closed behavior
- deterministic fallback behavior

## Integration tests

- strategy aggregate BUY reaches economics
- economics uses the correct scorecard evidence
- positive net edge continues to Risk Engine
- negative net edge remains rejected
- decision record contains full provenance
- replay can reproduce the economics decision
- operator diagnostic explains the rejection
- no production write from read-only diagnostics
- no bypass of Risk Engine
- no bypass of campaign or mandate authority

## Regression tests

Recreate at least one observed production-shaped rejection with values similar to:

```text
expected_gross_edge_pct=-0.0948
entry_fee_pct=0.01
exit_fee_pct=0.01
slippage_pct=0.01
expected_net_edge_pct=-0.1248
```

Prove exactly why it was rejected under the old model.

Then prove what the corrected model does with the same underlying evidence.

Do not merely change the expected assertion.

Explain the behavioral reason.

---

# Production Evidence Requirements

The task is not complete after unit tests.

Provide commands to:

1. validate locally,
2. inspect git diff,
3. commit,
4. push,
5. pull on VPS,
6. restart only required services,
7. verify service health,
8. run the diagnostic report against recent rejections,
9. observe future autonomous cycles,
10. capture whether a BUY candidate:
   - reached economics,
   - received valid context-specific edge evidence,
   - was rejected correctly,
   - or proceeded correctly to Risk.

Do not claim success merely because a BUY executes.

Success means the decision is economically justified and fully auditable.

---

# Definition of Done

This task is complete only when all of the following are true:

- the full source of `scorecard_historical_gross_return_pct` is known,
- its calculation is documented,
- its selection logic is documented,
- its percentage units are proven,
- its sample set is visible,
- its freshness is visible,
- its contextual relevance is visible,
- every economics cost is proven,
- no fee or slippage is double-counted,
- rejected BUYs can be replayed counterfactually,
- the current model’s calibration is measured,
- the root cause is conclusively identified,
- the smallest safe correction is implemented,
- positive-expectancy candidates can proceed,
- negative-expectancy candidates remain blocked,
- missing or weak evidence fails closed,
- Risk Engine authority remains unchanged,
- campaign and mandate governance remain unchanged,
- audit evidence remains immutable,
- tests pass,
- no unrelated architecture is redesigned,
- production diagnostics prove the deployed behavior,
- documentation is updated,
- operator commands are provided.

---

# Required Documentation Updates

Update only the documents that genuinely changed.

Likely candidates:

- `00_PROJECT_STATE.md`
- `00_OPERATIONS_MAP.md`
- `02_DECISIONS.md`
- `06_NEXT_SESSION.md`

Append decisions.

Never rewrite history.

Document:

- observed defect,
- proven root cause,
- correction,
- safety boundaries,
- tests,
- production evidence,
- remaining uncertainty.

---

# Output Format

Return your work in this exact order:

## 1. Executive diagnosis

State:

- what is wrong,
- why it is wrong,
- what evidence proves it,
- whether the gate itself or its inputs are defective.

## 2. Full data lineage

List exact files, functions, models, fields, and transformations.

## 3. Root-cause classification

Map the defect to Categories A–E above.

## 4. Corrective design

Explain the smallest safe design.

## 5. Files changed

List each file and purpose.

## 6. Tests added and results

Include exact commands and outcomes.

## 7. Safety review

Explicitly confirm preservation of:

- Risk Engine authority,
- mandate governance,
- campaign identity,
- fail-closed behavior,
- immutable audit,
- replayability,
- idempotency.

## 8. Local commands

Provide one copyable command block.

Use `&&` for fail-fast behavior where practical.

End the command block with one blank line.

## 9. VPS commands

Provide one copyable command block.

Use `&&` for fail-fast behavior where practical.

End the command block with one blank line.

## 10. Production validation checklist

Give exact evidence strings to look for.

## 11. Final verdict

Do not say “100% solved” unless all Definition of Done items are proven.

If anything remains unproven, identify it precisely.

---

# Constraints

Do not:

- bypass economics,
- bypass Risk Engine,
- hardcode positive edge,
- introduce a blanket override,
- permit negative expected-edge trades,
- change live submission flags just to force activity,
- fabricate historical performance,
- seed fake profitable scorecards,
- alter production data manually,
- suppress rejection logs,
- remove uncertainty,
- weaken audit evidence,
- silently switch from net to gross profitability,
- treat shadow proposals as live fills,
- claim a BUY occurred without provider evidence,
- claim profitability without reconciliation and accounting evidence.

The desired result is not “more trades.”

The desired result is:

> A correctly calibrated, context-aware, explainable economics gate that permits genuinely positive-expectancy autonomous trades and rejects genuinely negative-expectancy trades.

Begin now by reading the governing documents and tracing the exact implementation path before changing code.
