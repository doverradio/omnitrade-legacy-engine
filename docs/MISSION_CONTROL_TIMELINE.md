# Mission Control Timeline

## Scope

Mission Control presents an operator-facing intelligence and profit cockpit.

Primary capital metric:
- Total Managed Capital (campaign-derived)

Managed-capital included statuses:
- READY
- RUNNING
- PAUSED
- TARGET_REACHED

Managed-capital excluded statuses:
- DRAFT
- COMPLETED
- ARCHIVED

## Current Timeline Sources

Current timeline evidence comes from:
- validation run events
- paper trade activity
- research cycle events
- operational alerts
- profit/equity series derived from durable paper trading evidence
- live-operations audit annotations derived from durable audit actions

## Live Operations Timeline Annotations

Mission Control now annotates timeline context using recent durable audit events.

Current tracked actions include:
- CONNECTION_VERIFIED
- PREVIEW_GENERATED
- DRY_RUN_READY
- DRY_RUN_BLOCKED
- CREDENTIAL_ROTATED
- CONNECTION_DISCONNECTED

These annotations are informational evidence only and do not authorize live submission.

## Profit Tab

Mission Control now treats profit as a first-class metric.

Visible operator metrics include:
- total managed capital
- net profit
- realized PnL
- unrealized PnL
- fees
- gross profit
- gross loss
- return percentage
- current equity
- maximum drawdown

## Tabs

Current primary tabs:
- Overall Intelligence
- Profit

Additional operator sections remain accessible through the existing accordion layout.

## Ranges

Supported ranges:
- 24h
- 72h
- 7d
- 30d
- 90d
- all

## Mode Separation

Supported modes:
- PAPER
- LIVE
- COMBINED

Default mode:
- PAPER

## Annotations

Current annotations include or can derive from:
- validation run lifecycle events
- paper trade fills
- paper execution rejections
- research cycle activity
- champion changes
- operational alerts

## Snapshots

System intelligence snapshots are persisted in `system_intelligence_snapshots`.

Current capture behavior:
- bounded 15-minute bucket capture
- worker-driven capture
- idempotent per logical bucket and schema version

## Limitations

The current implementation improves Mission Control truthfulness and durability, but it does not guarantee complete historical coverage until snapshots accumulate over time or a bounded backfill is run.

## No Guarantee

Mission Control is an operator evidence surface.
It does not authorize live trading and does not guarantee profit.
