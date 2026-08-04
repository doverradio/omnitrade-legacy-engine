# 03 — Rule Discovery Engine

## Purpose

The Rule Discovery Engine turns research findings into explicit, testable, versioned strategy changes.

```text
Observation
→ Hypothesis
→ Machine-testable rule
→ Strategy branch
→ Replay
→ Validation
→ Accept or reject
```

It is the human-guided backpropagation layer of the Strategy Laboratory.

## Core Principle

No observation directly changes a strategy.

Every proposed change becomes a **Candidate Rule**.

## Candidate Rule

```json
{
  "candidate_rule_id": "CR-000021",
  "name": "Volatility contraction breakout entry",
  "status": "DRAFT",
  "source_analysis_id": "analysis_001",
  "strategy_parent_version": "002",
  "conditions": [],
  "action": {},
  "risk_controls": {},
  "created_by": "human_with_copilot"
}
```

## States

```text
DRAFT
READY_TO_TEST
TRAINING_PASSED
VALIDATION_PASSED
FINAL_TEST_PASSED
REJECTED
PROMOTABLE
ARCHIVED
```

## Structured Rule Builder

### Candle Conditions
- close above prior high
- close below prior low
- N higher or lower closes
- higher lows
- lower highs

### Range and Volatility
- range within X% for N candles
- contraction by X%
- expansion above percentile
- breakout from rolling range

### Volume
- above rolling median
- expansion by X%
- confirmation
- divergence

### Strategy State
- flat
- entry pending
- position open
- profit mode active
- capital below baseline
- capital above prior high
- stop not yet moved

### Actions
- permit entry
- block entry
- place limit order
- change entry offset
- exit
- activate trailing
- change trailing distance
- change position size
- wait for confirmation

## Serialized Rule Format

```json
{
  "when": {
    "all": [
      {
        "feature": "range_width_pct",
        "operator": "<=",
        "value": 0.30,
        "lookback": 3
      },
      {
        "feature": "close",
        "operator": ">",
        "reference": "previous_high"
      }
    ]
  },
  "then": {
    "action": "allow_long_entry"
  }
}
```

## Strategy Branching

```text
Strategy #002
+ Candidate Rule CR-000021
= Strategy #003-draft
```

The parent strategy remains immutable.

## Replay Actions

Every candidate rule supports:

- Replay Current Window
- Replay Training
- Replay Validation
- Replay Final Test
- Replay Entire Dataset
- Compare Against Parent
- Compare Against Buy & Hold

## Evaluation Output

Report:

- trade count
- win rate
- gross and net return
- drawdown
- profit factor
- fees and slippage
- average hold
- MFE and MAE
- capital curve
- parent-strategy delta
- buy-and-hold delta
- partition stability

## Candidate Rule Certificate

Generate an immutable certificate containing:

```text
Candidate Rule
Parent Strategy
Dataset
Training Result
Validation Result
Final Test Result
Cost Model
Sample Size
Robustness
Verdict
Promotion Eligibility
```

## Promotion Policy

A rule may become PROMOTABLE only when:

- deterministic replay succeeds
- sample size is sufficient
- Validation does not collapse
- Final Test was not used for tuning
- costs are explicit
- risk remains within configured bounds

PROMOTABLE does not mean automatically live.

## Export

Support:

- Copy Rule JSON
- Download Rule JSON
- Copy Strategy Package
- Generate Strategy Certificate
- Promote to Paper Trading Candidate

## Restrictions

The AI may propose rule structures.

It may not:

- activate them silently
- hide failed tests
- rewrite the parent strategy
- promote directly to live capital

## Definition of Done

A user can pause replay, select candles, accept or edit an evidence-backed Candidate Rule, create a strategy branch, replay it across all partitions, compare it with the parent strategy, and export a versioned strategy package for the next deployment stage.
