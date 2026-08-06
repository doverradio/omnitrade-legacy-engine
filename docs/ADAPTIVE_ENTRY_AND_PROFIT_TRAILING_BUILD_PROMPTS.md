# OmniTrade Adaptive Entry and Profit-Trailing Bot — Incremental Build Prompts

## How to Use This File

Use the prompts sequentially in Claude Code or Codex.

Do not paste all prompts at once.

After each prompt:

1. inspect the output and diff;
2. run the requested tests;
3. commit only when that phase is complete;
4. do not advance while the current phase remains unstable.

Governing architecture:

```text
ADAPTIVE_ENTRY_AND_PROFIT_TRAILING_ARCHITECTURE.md
```

Recommended standalone repository:

```text
~/omnitrade-adaptive-bracket-bot
```

The bot must remain isolated from the existing OmniTrade repository and production services.

---

# Prompt 1 — Repository Skeleton and State Machine

```text
Create a new standalone repository named omnitrade-adaptive-bracket-bot.

Read ADAPTIVE_ENTRY_AND_PROFIT_TRAILING_ARCHITECTURE.md in full and treat it as the governing specification.

Implement only the repository skeleton and deterministic state-machine domain model.

Required:
- Python 3.11
- pyproject.toml
- README.md
- .env.example
- src/adaptive_bot/
- tests/
- typed configuration
- Decimal for every price, quantity, fee, percentage, and P&L value
- enums for states, order types, sides, and provider order statuses
- domain models for BotCycle, OrderRecord, FillRecord, PositionState, and StateTransition
- legal transition validation
- live submission defaulting false
- no Kraken calls
- no database
- no OmniTrade imports

Implement states:
BOOT, RECONCILING, FLAT, ENTRY_PENDING, ENTRY_FILLED, INITIAL_PROTECTION, POSITION_MANAGED, PROFIT_TRAILING, EXIT_PENDING, CLOSED, UNKNOWN_PROVIDER_STATE, HALTED, MANUAL_REVIEW_REQUIRED.

Add tests proving:
- legal transitions succeed
- illegal transitions fail closed
- idempotent transition replay where appropriate
- floats are rejected for money and quantity fields
- live submission defaults disabled

Do not implement strategy math, persistence, provider integration, backtesting, or execution.
Do not commit.

Return files created, tests run, results, and exact Prompt 2 readiness.
```

---

# Prompt 2 — Strategy Mathematics

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement only deterministic strategy calculations from ADAPTIVE_ENTRY_AND_PROFIT_TRAILING_ARCHITECTURE.md.

Required pure functions:
- calculate_entry_limit
- calculate_true_break_even
- calculate_initial_stop
- calculate_profit_mode_activation
- calculate_trailing_floor
- update_protected_floor
- detect_declining_closes
- calculate_gross_pnl
- calculate_net_pnl
- calculate_slippage

Rules:
- Decimal only
- 0.01 means 1%
- tick-size rounding is explicit
- protected floor never decreases
- profit mode requires positive estimated net profit after remaining costs
- reversal uses completed candles only
- no network, database, or provider behavior

Test examples:
- reference 100 and 1% offset produces 99
- entry 100, high 120, trail 1% produces 118.80
- break-even includes entry fee, expected exit fee, slippage, and optional buffer
- floor cannot move down
- one declining close does not satisfy a two-close exit
- two declining closes do
- invalid inputs fail closed

Do not commit.
Return files changed, tests, results, and Prompt 3 readiness.
```

---

# Prompt 3 — Persistence and Restart Recovery

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement SQLite persistence and restart-safe internal recovery.

Required tables:
- bot_cycles
- orders
- fills
- state_transitions
- market_observations

Requirements:
- Alembic migrations
- transactions
- unique provider order ids
- unique provider trade ids
- one active cycle maximum
- one active entry maximum
- append-only transitions
- idempotent fill ingestion
- repository/service layer
- startup recovery that reconstructs internal state without contacting Kraken
- no live submission

Tests:
- migration upgrade and downgrade
- duplicate fill creates one record
- duplicate provider order id rejected
- restart reconstructs the active cycle
- zero or multiple active cycles handled explicitly and safely
- partial quantities remain exact
- transitions remain append-only

Do not commit.
Return files changed, migration commands, tests, results, and Prompt 4 readiness.
```

---

# Prompt 4 — Kraken Read-Only Adapter and Reconciliation

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement a provider-neutral read-only interface and Kraken spot adapter for reconciliation.

Required Kraken capabilities:
- current BTC-USD reference price
- best bid and ask
- product metadata
- tick/price precision
- quantity precision
- minimum order constraints
- open-order lookup
- closed-order lookup
- fill lookup
- balance lookup
- normalized provider states

Do not implement order submission or cancellation.

Implement boot reconciliation:
- no open order and no BTC → FLAT
- one matching BUY and no BTC → ENTRY_PENDING
- confirmed BTC from filled entry → ENTRY_FILLED or POSITION_MANAGED
- conflicts or ambiguity → MANUAL_REVIEW_REQUIRED

Requirements:
- official documented APIs
- signed authenticated reads where required
- credentials only from environment
- sanitized logs
- bounded retry/backoff
- ambiguity fails closed
- mock provider for tests

Do not use real credentials in tests.
Do not submit any order.
Do not commit.

Return files changed, tests, results, and Prompt 5 readiness.
```

---

# Prompt 5 — Shadow Adaptive Entry Loop

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement shadow mode for adaptive entry.

Behavior:
- when safely FLAT, immediately calculate an intended BUY LIMIT 1% below the current reference price
- persist the intended order without submitting it
- at each completed candle, replace the intended limit at 1% below the latest close
- allow movement upward and downward
- detect whether the shadow limit would have filled using deterministic, documented candle rules
- prevent look-ahead
- record every replacement and market observation

Operator events:
ENTRY_ORDER_PLANNED
ENTRY_ORDER_REPLACED
SHADOW_ENTRY_FILLED
SHADOW_ENTRY_UNFILLED

Tests:
- immediate action from FLAT
- replacement on each completed candle
- upward and downward movement
- no duplicate replacement
- deterministic fill handling
- intra-candle ambiguity logged rather than silently guessed

Do not implement real submission.
Do not commit.

Return files changed, tests, results, and Prompt 6 readiness.
```

---

# Prompt 6 — Historical Backtester and Parameter Study

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement a deterministic historical backtester for the complete simulated strategy.

Include:
- adaptive entry replacement
- partial-fill policy
- initial stop
- fee-adjusted break-even
- profit-mode activation
- monotonic trailing floor
- declining-candle exit
- optional distant profit limit
- fees
- spread
- configurable slippage
- explicit intra-candle event-ordering policy
- no look-ahead

Support parameter sweeps for:
- entry offset
- initial stop
- profit-mode activation
- trailing distance
- declining-candle count
- candle interval

Report:
- net return and net dollars
- cycle count
- win rate
- stop count
- trailing/reversal/distant-limit exits
- average holding time
- maximum drawdown
- maximum consecutive losses
- fee drag
- entry fill rate
- missed-move rate
- maximum favorable/adverse excursion
- percentage of maximum favorable excursion retained

Add a CLI for CSV candle input and JSON output.

Handcrafted deterministic tests:
- steady rise
- steady decline
- V reversal
- sudden spike
- sudden crash
- 20% rally followed by reversal
- stop and target touched in the same candle

Do not automatically optimize beyond enumerating the supplied grid.
Do not commit.

Return files changed, tests, example CLI, and Prompt 7 readiness.
```

---

# Prompt 7 — Live BUY LIMIT Capability Behind Disabled Flags

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement real Kraken BUY LIMIT submission and cancellation, but keep every live path disabled by default.

Required:
- BOT_MODE=shadow|live
- LIVE_SUBMISSION_ENABLED=false by default
- live startup requires both mode and flag
- provider validation/preview where available
- tick and quantity rounding
- minimum-order validation
- unique client order identity
- BUY LIMIT submission
- provider status polling independent of candles
- provider-confirmed cancellation before replacement
- no replacement during ambiguous state
- partial BUY handling: cancel remainder, confirm cancellation, manage only confirmed quantity
- persistence around every provider call
- restart recovery

Do not implement automated SELL behavior in this phase.

If any BUY fills during a manual test, halt and require manual review because exits are not automated yet.

Use mocks for write tests.
Do not enable live flags.
Do not use real funds.
Do not commit.

Return files changed, tests, exact flags, proof that defaults cannot submit, and Prompt 8 readiness.
```

---

# Prompt 8 — Protective Exit and Profit-Trailing

```text
Continue in omnitrade-adaptive-bracket-bot.

Implement the complete exit lifecycle.

Required:
- actual average BUY fill and entry fee
- exchange-resident initial protective stop
- true fee-adjusted break-even
- POSITION_MANAGED only after protection confirmed
- PROFIT_TRAILING only after configured threshold and positive estimated net profit
- highest-price and highest-close tracking
- monotonic protected floor
- declining-candle exit after profit mode
- optional distant SELL LIMIT
- verified native or safe software-managed OCO
- when one exit fills, cancel and confirm all others
- partial SELL handling
- CLOSED only after provider confirms zero remaining quantity
- realized net P&L from actual fills and fees
- after closure, return to FLAT and immediately begin the next entry cycle

Do not assume Kraken native OCO semantics. Verify them or implement safe coordination.

Tests:
- immediate protection
- protection failure halts
- activation threshold
- a 20% rally remains open while new highs continue
- floor never falls
- two declining closes trigger exit
- trailing stop fills first
- distant limit fills first
- exit race fails closed
- duplicate fills do not double-close
- restart from every exit state
- zero-position confirmation before CLOSED

Do not enable live submission.
Do not commit.

Return files changed, tests, results, and Prompt 9 readiness.
```

---

# Prompt 9 — VPS Shadow Deployment and Operations

```text
Continue in omnitrade-adaptive-bracket-bot.

Prepare VPS shadow deployment only.

Required:
- dedicated systemd service
- environment template
- structured logs and log rotation
- database backup command
- startup reconciliation
- safe shutdown
- health/status CLI
- operator commands: status, pause, resume, reconcile, current-cycle, recent-cycles, emergency-halt
- readiness showing mode, live flag, provider connectivity, market-data freshness, active cycle, pending orders, owned quantity, and protection state

Provide:
- local validation commands
- git commit/push commands
- VPS clone/pull commands
- environment setup
- systemd install/start commands
- shadow observation commands
- rollback commands

Do not submit real orders.
Do not enable live mode.
Do not modify OmniTrade services.
Use a unique service name.

End with an explicit shadow-readiness verdict, not a live-readiness claim.
```

---

# Prompt 10 — One Bounded $5 Live Proving Cycle

```text
Continue in omnitrade-adaptive-bracket-bot.

Do not modify code unless a concrete defect is found.

Review accumulated backtest and VPS shadow evidence and produce a go/no-go report for exactly one bounded live cycle:
- BTC-USD only
- $5 maximum notional
- one active cycle
- one position
- no leverage
- manual enablement
- manual kill switch available
- automatic stop protection
- profit-trailing enabled
- exact reconciliation

Required evidence:
- backtest windows and sample size
- shadow duration and sample size
- fill rate
- net expectancy
- fee drag
- modeled slippage
- maximum drawdown
- longest losing streak
- restart tests
- provider ambiguity tests
- protection tests
- unresolved risks

If GO:
- provide exact local and VPS commands
- require an explicit operator checkpoint
- run one cycle only
- collect order ids, fills, fees, and realized net result
- return live enablement to false after the proving cycle

If NO-GO:
- state exactly why
- do not weaken safeguards
- recommend the smallest evidence-driven correction

Do not promise profit.
Do not increase size or add assets.
Do not integrate into OmniTrade yet.
```

---

# Why Ten Prompts

Ten prompts separate distinct engineering and safety boundaries.

A capable coding agent may combine early non-live phases only when the current phase is fully tested and the resulting diff remains reviewable.

Never combine Prompts 7 through 10. They deliberately separate:

```text
live entry capability
→ exit safety
→ deployment
→ real-capital proving
```

OmniTrade integration is a later human decision made only after real evidence exists.
