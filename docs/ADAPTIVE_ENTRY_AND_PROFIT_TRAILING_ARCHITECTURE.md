# OmniTrade Sidecar Strategy Bot — Adaptive Entry and Profit-Trailing Architecture

## Status

Proposed standalone proving system, intentionally isolated from the existing OmniTrade production pipeline.

Its narrow purpose is to prove or disprove whether a simple adaptive limit-entry and profit-trailing strategy can produce positive net expectancy after fees, spread, slippage, missed entries, stop losses, and real execution behavior.

> The system begins every valid cash-only cycle by immediately placing a BUY LIMIT below the current market, follows the market until filled, then switches completely into capital-protection and profit-trailing mode.

This document does not claim or guarantee profitability.

---

# 1. Core Strategy Thesis

Version 1 trades one instrument: `BTC-USD`.

When safely flat:

```text
reference_price = authoritative current market price
entry_limit_price = reference_price × (1 - entry_offset_pct)
```

Initial default:

```text
entry_offset_pct = 1.00%
```

The bot submits the BUY LIMIT immediately.

At each completed candle:

```text
new_entry_limit = latest_close × (1 - entry_offset_pct)
```

If the entry has not filled, the bot cancels the old limit, confirms cancellation, and replaces it with the new one. The limit may move upward or downward.

When the BUY fills, the bot immediately switches from ENTRY mode to EXIT mode. It establishes downside protection, calculates true fee-adjusted break-even, and manages the position until it closes.

After a configured net-profit threshold is reached, the bot activates **profit-trailing mode**. It allows the winner to continue while progressively protecting more profit. It exits when protected profit is threatened, such as when the trailing floor is touched or a configured number of declining candles confirms a reversal.

---

# 2. Design Goals

## Primary goal

Answer one question with evidence:

> Does this strategy generate positive realized net returns over repeated cycles after all real costs?

## Required qualities

- Simple enough to understand completely.
- Deterministic and configurable.
- Restart-safe and idempotent.
- One active cycle, one pending entry, and one position maximum.
- Provider evidence determines every fill, cancellation, and closure.
- A filled position receives immediate exchange-resident protection.
- Every cycle records exact realized net P&L.
- No dependency on OmniTrade campaigns, mandates, scorecards, custody, or Decision Intelligence.

## Non-goals

Version 1 does not attempt:

- AI prediction
- strategy ensembles
- multi-asset allocation
- machine learning
- self-modification
- leverage or margin
- derivatives
- market making
- high-frequency trading
- multiple simultaneous positions
- guaranteed daily profit

---

# 3. Isolation Boundary

Create a standalone repository or service:

```text
omnitrade-adaptive-bracket-bot/
```

It must use:

- separate configuration
- separate database or schema
- separate logs
- separate systemd service
- separate feature flags
- no imports from OmniTrade business services

The existing OmniTrade feature branch remains untouched while this strategy is proved independently.

---

# 4. Finite State Machine

```text
BOOT
  ↓
RECONCILING
  ↓
FLAT
  ↓
ENTRY_PENDING
  ↓
ENTRY_FILLED
  ↓
INITIAL_PROTECTION
  ↓
POSITION_MANAGED
  ↓
PROFIT_TRAILING
  ↓
EXIT_PENDING
  ↓
CLOSED
  ↓
FLAT
```

Safety states:

```text
UNKNOWN_PROVIDER_STATE
HALTED
MANUAL_REVIEW_REQUIRED
```

---

# 5. State Behavior

## BOOT

Load persisted state, configuration version, active cycle, internal orders, provider ids, fills, and last known owned quantity. Do not submit an order.

Transition to `RECONCILING`.

## RECONCILING

Query Kraken for:

- open orders
- recent closed orders
- fills
- balances
- owned BTC quantity

Reconcile provider truth with local state.

```text
No open order and no owned BTC → FLAT
One matching pending BUY and no BTC → ENTRY_PENDING
Confirmed filled BUY quantity → ENTRY_FILLED or POSITION_MANAGED
Confirmed exit order and owned BTC → POSITION_MANAGED or PROFIT_TRAILING
Any conflict or ambiguity → MANUAL_REVIEW_REQUIRED
```

No new order may be placed until reconciliation succeeds.

## FLAT

The bot has no BTC position and no pending entry.

It immediately calculates and submits a BUY LIMIT at the configured percentage below the current reference price.

This is the defining invariant:

> Every safely FLAT cycle immediately attempts a discounted entry.

## ENTRY_PENDING

One BUY LIMIT is active.

The bot polls provider order status independently of candle completion. At every completed candle:

1. query provider state;
2. if filled, transition to `ENTRY_FILLED`;
3. if partially filled, apply the partial-fill policy;
4. if open, calculate the new limit;
5. cancel the old order;
6. confirm provider cancellation;
7. submit the replacement;
8. persist replacement lineage.

Version 1:

```text
replacement_limit = latest_close × 0.99
```

The order may trail upward or downward indefinitely until filled or manually halted.

## ENTRY_FILLED

Record provider-confirmed:

- requested quantity
- filled quantity
- average fill price
- entry fee
- fill timestamp
- provider trade ids
- entry slippage
- complete replacement lineage

Immediately calculate:

- true break-even
- initial protective stop
- profit-mode activation price
- optional distant profit-limit price

Transition to `INITIAL_PROTECTION`.

## INITIAL_PROTECTION

Establish an exchange-resident protective exit before ordinary position management.

Initial defaults:

```text
initial_stop_pct = 1.00%
profit_mode_activation_pct = 3.00%
```

The downside order must be a supported stop type, not a normal SELL LIMIT below market.

Only after protection is confirmed open may the bot enter `POSITION_MANAGED`.

If protection cannot be established, fail closed according to the emergency-exit policy.

## POSITION_MANAGED

Track:

- gross and estimated net P&L
- expected exit fee
- expected exit slippage
- true break-even
- highest observed price
- highest completed candle close
- stop distance
- profit-mode eligibility

Before profit mode, the initial stop remains authoritative.

An optional distant SELL LIMIT may exist only if it is coordinated safely with the protective stop.

## PROFIT_TRAILING

Activate only after both:

```text
configured gross threshold reached
and estimated net profit after remaining costs is positive
```

Maintain:

```text
highest_price_since_entry
highest_close_since_entry
protected_profit_floor
```

Simple Version 1 formula:

```text
candidate_floor = highest_price_since_entry × (1 - trailing_distance_pct)
protected_floor = max(previous_protected_floor, candidate_floor)
```

The protected floor may never decrease.

Exit triggers may include:

1. exchange-resident trailing/protective stop touched;
2. two consecutive declining completed candle closes;
3. optional distant SELL LIMIT filled;
4. emergency safety condition.

Whichever closes the position first cancels all remaining exit orders.

## EXIT_PENDING

Prevent duplicate SELL submission. Handle partial fills, cancel conflicting exits, reconcile remaining quantity, and continue until the position is fully closed or manual review is required.

## CLOSED

Confirm provider-owned BTC quantity for the cycle is zero.

Calculate and persist:

- gross proceeds
- acquisition cost
- entry fees
- exit fees
- actual average fills
- gross P&L
- realized net P&L
- net return percent
- holding duration
- maximum favorable excursion
- maximum adverse excursion
- exit reason

Then start the next cycle and return to `FLAT`.

---

# 6. Entry Algorithm

## Immediate initial order

```text
reference_price = current authoritative BTC-USD price
entry_limit = reference_price × (1 - entry_offset_pct)
```

Submit immediately when safely flat.

## Candle replacement

At every completed candle:

```text
desired_entry_limit = candle_close × (1 - entry_offset_pct)
```

Replace only when the desired price differs enough to satisfy tick size and any configured minimum-change threshold.

Cancellation must be provider-confirmed before replacement submission.

## Fill detection

Poll provider status independently of candles because fills can occur between candle closes.

---

# 7. Exit and Profit-Trailing Algorithm

## Fee-adjusted break-even

“Profit” means profit after costs.

Calculate the exit price required to recover:

- acquisition cost
- entry fee
- expected exit fee
- expected exit slippage
- optional safety buffer

## Initial stop

```text
initial_stop_trigger = average_fill_price × (1 - initial_stop_pct)
```

Use the supported Kraken stop type selected during implementation.

## Profit-mode activation

Version 1 may begin with a 3% gross rise, but activation also requires positive estimated net profit after the expected remaining costs.

## Trailing floor

```text
highest_price = max(highest_price, current_price)
new_floor = highest_price × (1 - trailing_distance_pct)
protected_floor = max(protected_floor, new_floor)
```

Never lower the floor.

## Two-declining-candle reversal

After profit mode activates:

```text
close[n] < close[n-1]
and
close[n-1] < close[n-2]
```

Initiate exit when the configured declining-candle count is satisfied.

## Optional distant SELL LIMIT

A distant target may capture a sudden spike. It is optional and never the only protection.

It must be coordinated with the stop through verified native behavior or software-managed OCO logic.

---

# 8. OCO and Exit Coordination

Only one full-position exit may complete.

Preferred:

- verified native Kraken conditional/OCO behavior, if suitable.

Fallback:

```text
Exit A fills
→ detect provider fill
→ cancel Exit B
→ confirm provider cancellation
→ reconcile remaining BTC quantity
```

Never assume cancellation succeeded. Race conditions fail closed.

---

# 9. Fees, Spread, and Slippage

## Fees

Use provider-reported actual fees for realized accounting. Use estimates only before execution and in simulation.

## Spread

```text
spread = best ask - best bid
```

Immediate buys generally cross toward the ask; immediate sells cross toward the bid.

## Slippage

Slippage is the difference between expected and actual execution price.

For a BUY:

```text
buy_slippage = actual_fill - expected_price
```

For a SELL:

```text
sell_slippage = expected_price - actual_fill
```

Limit orders constrain price but may not fill. Market and stop-market orders prioritize execution but may fill worse than expected.

## Realized net P&L

```text
realized_net_pnl
=
exit proceeds
- acquisition cost
- entry fees
- exit fees
- other provider charges
```

Actual fills control realized accounting.

---

# 10. Partial-Fill Policy

Recommended Version 1 BUY policy:

```text
If the BUY partially fills:
- stop replacing the entry;
- cancel the remaining quantity;
- confirm cancellation;
- manage only the confirmed filled quantity;
- immediately establish protection for that quantity.
```

Never assume the requested quantity filled.

Apply the same exactness to partial SELL fills.

---

# 11. Minimum Safety Controls

```text
BTC-USD only
one active cycle
one pending entry
one open position
no leverage
no margin
no averaging down
maximum notional
maximum daily realized loss
maximum consecutive losses
manual kill switch
live-submission feature flag
reconciliation on boot
halt on unknown provider state
halt on balance mismatch
halt if protection cannot be established
persist every transition
```

These are not unnecessary layers. They are the minimum controls required for unattended exchange automation.

---

# 12. Initial Configuration

```env
BOT_MODE=shadow
LIVE_SUBMISSION_ENABLED=false

INSTRUMENT=BTC-USD
CANDLE_INTERVAL_MINUTES=15

ENTRY_OFFSET_PCT=0.01
INITIAL_STOP_PCT=0.01
PROFIT_MODE_ACTIVATION_PCT=0.03
TRAILING_DISTANCE_PCT=0.01
DECLINING_CANDLES_TO_EXIT=2

ENABLE_DISTANT_PROFIT_LIMIT=false
DISTANT_PROFIT_LIMIT_PCT=0.25

MAX_POSITION_NOTIONAL_USD=5
MAX_OPEN_POSITIONS=1
MAX_DAILY_LOSS_USD=1
MAX_CONSECUTIVE_LOSSES=3

ORDER_POLL_SECONDS=5
MARKET_DATA_STALE_SECONDS=60
```

Percentage convention:

```text
0.01 = 1%
```

---

# 13. Persistence Model

SQLite is acceptable for a single-process local/VPS Version 1 if transactional durability and backups are configured.

Suggested tables:

## bot_cycles

```text
cycle_id
state
instrument
started_at
closed_at
config_version
reference_price
entry_offset_pct
exit_reason
gross_pnl
net_pnl
```

## orders

```text
internal_order_id
cycle_id
provider_order_id
parent_order_id
replacement_of_order_id
side
order_type
requested_quantity
filled_quantity
limit_price
stop_price
average_fill_price
status
submitted_at
updated_at
terminal_at
```

## fills

```text
fill_id
provider_trade_id
provider_order_id
side
quantity
price
fee
fee_currency
filled_at
```

## state_transitions

```text
transition_id
cycle_id
from_state
to_state
reason
evidence_json
created_at
```

## market_observations

```text
observation_id
cycle_id
candle_open_time
open
high
low
close
volume
active_entry_limit
highest_price
protected_floor
created_at
```

---

# 14. Operator Events

Emit clear structured events:

```text
ENTRY_ORDER_SUBMITTED
ENTRY_ORDER_REPLACED
ENTRY_PARTIALLY_FILLED
ENTRY_FILLED
PROTECTION_ESTABLISHED
PROFIT_MODE_ACTIVATED
TRAILING_FLOOR_RAISED
DECLINING_CANDLE_WARNING
EXIT_TRIGGERED
EXIT_FILLED
CYCLE_CLOSED
BOT_HALTED
```

Each includes cycle id, timestamp, market price, order price, quantity, gross/estimated net P&L, provider id, and reason.

---

# 15. Operating Modes

## Backtest

Historical candles with deterministic fill assumptions, fees, spread, slippage, replacement timing, and explicit intra-candle ambiguity handling. No look-ahead.

## Shadow

Live market data and intended orders, but no exchange writes.

## Live proving

Only after review of backtest and shadow evidence:

```text
BTC-USD
$5 maximum
one cycle
one position
deliberate manual enablement
```

---

# 16. Parameter Studies

Compare ranges such as:

```text
Entry offset: 0.25%, 0.50%, 0.75%, 1.00%, 1.50%
Initial stop: 0.50%, 0.75%, 1.00%, 1.50%, 2.00%
Profit-mode activation: 1%, 2%, 3%, 4%, 5%
Trailing distance: 0.50%, 0.75%, 1.00%, 1.50%, 2.00%
Declining candles: 1, 2, 3
Candle interval: 1m, 5m, 15m, 1h
```

Report:

- realized/modelled net return
- cycle count
- win rate
- stop count
- reversal exits
- distant-limit exits
- average holding time
- maximum drawdown
- maximum consecutive losses
- fee drag
- entry fill rate
- missed-move rate
- maximum favorable/adverse excursion
- percentage of maximum favorable excursion retained

---

# 17. Build Sequence

1. Repository and state-machine skeleton.
2. Deterministic strategy mathematics.
3. Persistence and restart recovery.
4. Kraken read-only adapter and reconciliation.
5. Shadow adaptive-entry loop.
6. Historical backtester and parameter study.
7. Live BUY LIMIT capability, disabled by default.
8. Protective exits and profit-trailing.
9. VPS shadow deployment and operational controls.
10. One bounded $5 live proving cycle.

---

# 18. Definition of Done

Version 1 is complete when:

- a safely FLAT bot immediately places a 1%-below-market BUY LIMIT;
- the pending entry is recalculated at each completed candle;
- cancellation is confirmed before replacement;
- fills are detected independently of candle timing;
- partial fills are handled safely;
- a filled position receives exchange-resident protection;
- fee-adjusted break-even is calculated;
- profit-trailing activates correctly;
- the trailing floor never decreases;
- declining candles can trigger a protected exit;
- an optional distant limit can coexist safely;
- remaining exits are cancelled after closure;
- restart reconciliation works;
- every cycle records exact realized net P&L;
- backtest and shadow modes work;
- live submission defaults disabled;
- the first deliberate $5 cycle can be run;
- no existing OmniTrade production code is modified.

---

# 19. Integration Policy

Do not integrate the sidecar merely because it places trades.

Require evidence answering:

- Is net expectancy positive?
- Does it remain positive after actual fees?
- How sensitive is it to slippage?
- Does it survive different market regimes?
- What is maximum drawdown?
- Is performance stable across nearby parameter values?
- Does it outperform simple baselines over identical periods?

If supported, integrate proven components one at a time:

```text
execution behavior
→ reconciliation
→ order lifecycle
→ profit-trailing
→ accounting
→ risk controls
→ audit
→ Decision Intelligence
```

The sidecar remains the proving ground until the strategy earns promotion through evidence.
