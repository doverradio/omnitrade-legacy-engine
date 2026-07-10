# Mission Control Timeline

## Scope

Mission Control presents an operator-facing intelligence and profit cockpit.

## Current Timeline Sources

Current timeline evidence comes from:
- validation run events
- paper trade activity
- research cycle events
- operational alerts
- profit/equity series derived from durable paper trading evidence

## Profit Tab

Mission Control now treats profit as a first-class metric.

Visible operator metrics include:
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
