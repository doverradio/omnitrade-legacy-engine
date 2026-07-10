# Profit Intelligence

## Definitions

Paper, live, and combined profit are reported separately.

Paper Profit:
- derived from durable paper trades and paper account mark-to-market positions

Live Profit:
- derived from durable live accounting records
- remains zero until real live fills exist

Combined Profit:
- visible sum of paper and live components
- must never hide the paper/live split

## Terms

Gross Profit:
- sum of positive realized trade outcomes before losses are netted

Gross Loss:
- absolute sum of negative realized trade outcomes

Realized PnL:
- closed-position outcomes only

Unrealized PnL:
- mark-to-market open-position outcomes only

Fees:
- attributed trade fees from durable trade or accounting records

Net Profit:
- realized PnL after attributed fees

Total Economic PnL:
- realized PnL plus unrealized PnL

## Range Accounting

Supported ranges:
- 24h
- 72h
- 7d
- 30d
- 90d
- all

Period calculations use:
- deterministic opening equity
- deterministic ending equity
- trades/fills executed within the selected range
- durable market prices at or before the bucket end time

Capital inflows are not treated as profit.
Capital outflows are not treated as loss by default.

## Data Sources

Current profit calculations use durable schema sources:
- paper accounts
- trades
- signals for strategy association
- assets
- candles for mark-to-market pricing
- live accounting records

## Separation

Mission Control defaults to PAPER mode.

LIVE mode remains visible but truthfully empty until live fills exist.

## No Guarantee

Paper profit does not imply future live profitability.
No profit metric in Mission Control is a guarantee of future performance.
