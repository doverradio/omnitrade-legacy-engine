# Intelligence Scoring

## Scope

This document summarizes the current evidence sources used by Mission Control and validation intelligence surfaces.

## Current Evidence Sources

Current scoring and timelines rely on persisted or queryable evidence from:
- candles
- signals
- decision records
- trades
- risk events
- validation run events
- research campaigns
- research candidate evaluations
- research memory growth
- operational health status

## Confidence

Confidence is bounded and should fall when:
- validation evidence is sparse
- operational alerts are present
- paper trading activity is absent
- research activity is absent
- database or worker health is degraded

## Data Completeness

Data completeness should not be inferred from profit alone.
It depends on whether the underlying evidence families are present for the selected range.

## Validation Run Scorecards

Validation scorecards now prefer current observed evidence over stale “zero growth” wording when the system is active but the run-relative delta is flat.

Examples:
- candles may still be considered active even if run-relative candle growth is zero, as long as current operational evidence confirms active ingestion
- paper trading may still be considered active even if the current run delta is zero, as long as current paper trade evidence exists

## Mission Control Notes

Mission Control remains informational and must not be treated as a live-trading authorization source.

Intelligence views should remain consistent with:
- current operational health
- validation run evidence
- paper-only research activity
- strict live-trading isolation
