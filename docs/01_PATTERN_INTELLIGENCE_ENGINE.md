# 01 — Pattern Intelligence Engine

## Purpose

The Pattern Intelligence Engine converts normalized candle history and deterministic replay state into measurable, explainable market observations.

It is not a language model, neural network, or live trading system. It makes no external API calls and sends no candle data outside OmniTrade.

## Pipeline

```text
INPUT
OHLCV candles
Replay events
Current strategy state
Selected candle range

PROCESS
Feature extraction
Pattern detection
Historical recurrence search
Forward-outcome measurement
Partition validation

OUTPUT
Evidence-backed pattern observations
```

## Inputs

Required normalized columns:

```text
timestamp
open
high
low
close
volume
```

Optional metadata:

```text
asset
exchange
interval
dataset_id
strategy_version
replay_events
selected_start
selected_end
```

## Detector Families

### Price Structure
- higher highs and higher lows
- lower highs and lower lows
- flat ranges and consolidation
- trend slope and acceleration
- range expansion and contraction
- repeated support and resistance
- failed support or resistance breaks

### Volatility
- rolling volatility
- rolling range
- ATR-like measures
- contraction and expansion
- volatility regime and percentiles

### Momentum
- multi-candle momentum
- acceleration and deceleration
- exhaustion and recovery
- drawdown from recent high
- rebound from recent low

### Volume
- expansion and contraction
- rolling percentile
- price-volume agreement
- price-volume divergence

### Breakout Structure
- bullish breakout
- bearish breakdown
- failed breakout
- failed breakdown
- retest
- range escape
- false-move recovery

### Strategy-Relative Findings
- missed entry
- late entry
- early exit
- unrealized profit left
- stop too close or too far
- exit before profit activation
- repeated order replacement
- narrowly missed order
- capital drawdown onset and recovery
- underperformance versus buy-and-hold

## Detector Contract

Every detector returns structured evidence:

```json
{
  "detector_id": "volatility_contraction_v1",
  "pattern_name": "Volatility Contraction",
  "start_index": 120,
  "end_index": 126,
  "observed": true,
  "measurements": {
    "range_contraction_pct": 42.6,
    "lookback_candles": 6
  },
  "evidence": [
    "rolling_range_current=0.0041",
    "rolling_range_baseline=0.0071"
  ],
  "engine_version": "1.0.0"
}
```

No detector may emit vague prose without measurements.

## Historical Recurrence

For each finding, optionally search similar historical occurrences and report:

- occurrence count
- counts by Training, Validation, and Final Test
- average and median forward return
- win rate after costs
- MFE and MAE
- target-before-stop probability
- confidence interval where practical
- sufficiency of evidence

Similarity rules must be explicit and deterministic.

## Forward Horizons

Support configurable outcome windows:

```text
1, 2, 4, 8, and 16 candles
```

Future data may be used only for post-analysis outcome measurement, never for replay decisions.

## Output Labels

Every finding must be labeled:

```text
OBSERVATION
STATISTICAL EVIDENCE
INSUFFICIENT EVIDENCE
CONTRADICTION
```

Recommendations belong to the Research Copilot, not this engine.

## Visual Annotation Contract

```json
{
  "annotation_id": "ann_001",
  "pattern_id": "volatility_contraction_v1",
  "start_time": "2026-07-08T01:00:00Z",
  "end_time": "2026-07-08T02:15:00Z",
  "label": "Volatility Contraction",
  "chart_region": "price",
  "details_ref": "finding_001"
}
```

The frontend renders findings but never infers them.

## Persistence

Store immutable analysis artifacts containing:

```text
analysis_id
dataset_id
asset
exchange
interval
selected_range
partition
detector_versions
configuration
findings
recurrence_statistics
created_at
content_hash
```

## Testing

Required tests:

- deterministic repeated output
- no look-ahead in replay decisions
- exact detector boundaries
- partition separation
- insufficient-sample handling
- invalid OHLCV rejection
- gap and duplicate awareness
- recurrence calculations
- annotation serialization

## Definition of Done

A user can select candles, click **Analyze Selection**, and receive reproducible local findings for structure, volatility, momentum, volume, breakout behavior, and strategy-relative failures, with every claim backed by measurable evidence.
