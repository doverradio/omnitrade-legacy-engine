# Design Review — Adaptive Entry and Profit-Trailing Bot

**Reviewer role:** Lead principal trading-systems architect, final pre-implementation review
**Scope reviewed:** `ADAPTIVE_ENTRY_AND_PROFIT_TRAILING_ARCHITECTURE.md` + `ADAPTIVE_ENTRY_AND_PROFIT_TRAILING_BUILD_PROMPTS.md` (both read in full)
**Posture:** Adversarial. The goal was to break it, assuming real capital eventually rides on it.
**Verdict:** **APPROVE WITH CHANGES** — begin non-live phases (Prompts 1–6) now; three CRITICAL issues must be closed before Prompt 7 (first live capability).

Facts about Kraken below were verified against Kraken support/developer docs and current market data as of 2026-08-03 (BTC ≈ $62,500). Where a claim depends on live exchange behavior, it is tagged **[VERIFIED]**; where it is an engineering judgment, **[ASSESSMENT]**.

---

## The headline problem

The single most important finding is not a logic bug. It is that **the configured trade size cannot legally exist on Kraken.**

- **[VERIFIED]** Kraken's minimum BTC order volume is **0.0001 BTC**. At ~$62,500, that is **≈ $6.25**.
- The config sets `MAX_POSITION_NOTIONAL_USD=5`.
- **$5 is below the exchange minimum.** The bot's *maximum* allowed order is smaller than the smallest order Kraken will accept. In live mode it cannot place a single compliant order. It deadlocks on its first action.

This is the same class of failure noted in your history with OmniTrade — a minimum-order floor collapsing the viable order range on a small account. It reappears here, and it cascades into the partial-fill and exit logic (below). Everything else in this review is secondary to fixing the sizing model against the live, price-dependent minimum.

---

## 1. Overall architecture

**Internally consistent?** Mostly, at the level of intent. The spine is sound: isolation boundary, reconcile-before-act, provider-evidence-as-truth, protection-before-management, evidence-gated promotion. Those are the right instincts and they are not common in hobby bots. Credit where due.

**But the FSM diagram and the prose disagree.** Section 4 draws a single linear chain `BOOT → … → CLOSED → FLAT`. Section 5 then describes `RECONCILING` legally landing in `ENTRY_FILLED`, `POSITION_MANAGED`, or `PROFIT_TRAILING`, plus three safety states with no drawn edges in or out. So the actual transition graph is materially richer than the diagram, and the diagram is what an implementer will encode first. **The authoritative transition table is missing.** This is where restart holes hide. (Detail in §5.)

**Unnecessarily complicated?** In a few places it is quietly re-growing the thing you built this to escape. See §9.

**Missing?** The transition table; a crash-safe definition of the fill→protection window (§4 failure modes); an explicit dust/sub-minimum policy (§3); a single authoritative definition of "the trail" (§2).

**Duplicated?** Yes — there are effectively **three** exit mechanisms and **two** definitions of "high water." See §2 and §6.

**Impossible to implement cleanly?** The `$5` lane, as written, yes. The rest is implementable.

---

## 2. Trading logic

This is where I'd push hardest. The architecture is competent; the *strategy* is the weakest part, and it is worth being blunt because the entire project exists to find that out with evidence.

**Is continuously replacing the BUY LIMIT sensible? Can it chase forever?**
It chases, and the chase has an asymmetric, adverse structure:

- **In a rising market, it never fills.** The limit sits 1% below each close; price walks away from it upward. You watch the move you wanted and never board it. This is not a rare edge case — it is the entire behavior of the strategy during exactly the regimes a long-only dip-buyer wants to catch.
- **In a falling market, it fills readily — and badly.** Each new candle lowers the limit to `close × 0.99`; price trades down through it; you fill. **You are structurally selected to get filled precisely when price is moving down through your level.** You then sit in a fresh long with a 1% stop directly beneath you.

That combination — fills concentrated in downward momentum, no fills in upward momentum — is a textbook **adverse-selection** profile. **[ASSESSMENT]** My prior is that this has negative net expectancy on 15m BTC after costs, because the tight 1% stop gets chewed up in the down-moves you're selected into (death by a thousand cuts) while the up-moves that would pay for them are never entered. That is a *hypothesis*, and your backtester exists to confirm or refute it — which is the correct posture. But the design should not carry an implicit assumption that "buy 1% below, trail down" is neutral. It is not; it is a specific mean-reversion bet with a known failure mode.

**When would it perform well?** Choppy, range-bound, mean-reverting markets with frequent 1–3% dips that bounce — the limit fills on the dip and the bounce clears the 3% activation. It will do *worst* in trending markets in either direction (misses uptrends, gets sliced in downtrends).

**Cancel/replace gap.** Every candle you cancel, *confirm* cancellation, then submit. Between cancel-confirm and new-submit you hold **no resting order**. A fast down-tick in that window is either a missed good fill or a dodged bad one — non-deterministic, and it means "the order is always working" is false. Minor, but it belongs in the spec and the backtester's event ordering.

**Profit-trailing logic — the two-declining-candle exit is the soft underbelly.**
On a 15m chart, **two consecutive lower closes is noise, not signal.** It happens multiple times inside almost every genuine uptrend. So the "let the winner run" thesis is self-defeating: the runner is cut on the first ordinary two-bar pullback, typically far below the true swing high. The build prompt's own test — *"a 20% rally remains open while new highs continue"* — is misleading, because real 20% rallies are not monotonic at candle-close granularity; they breathe, and two-down-closes fires on the breath. Expect this rule to cap most winners near the activation threshold. Your `% of MFE retained` metric is exactly the right instrument to expose this — watch it closely; I'd predict it comes in low.

**Hidden candle-timing assumptions.** "At each completed candle" needs a hard definition of *completed*. Kraken's OHLC feed's most recent bucket is typically still forming; acting on it is a subtle look-ahead. Specify: act only on the last **fully closed** candle (index −2 when polling), never the in-progress one — in the bot *and* the backtester.

**Would professionals do it differently?** Yes, in three ways: (a) they would not run a fixed-percentage limit chase without a regime/trend filter to suppress dip-buying in downtrends; (b) they would not use a raw two-lower-closes exit — they'd use an ATR- or volatility-scaled trail so the exit distance breathes with the market instead of firing on fixed noise; (c) they would size the stop relative to volatility, not a flat 1%. None of that needs to be in v1 — but the design should acknowledge these are the known-better approaches it is deliberately deferring, so "1% / 3% / 1% / 2 candles" isn't mistaken for a considered edge.

---

## 3. Kraken execution

**[VERIFIED] Native OCO does not exist for cash spot.** Kraken's automatic one-cancels-the-other bracket is a **derivatives** feature. On spot you have **Conditional Close (OTO — one-*triggers*-other)**, which Kraken's own docs state **cannot** place a combined take-profit + stop-loss and **cannot** do OCO, and which is documented around margin positions. Consequence: the architecture's framing — *"Preferred: native Kraken conditional/OCO behavior … Fallback: software-managed"* — is inverted. **There is no native option to prefer.** The software-managed coordination is the *only* path and must be specified as primary, not hedged as a fallback. An implementer who reads "preferred: native" may reach for something that isn't there and hand-wave the coordination.

**[VERIFIED] Stop-loss and trailing-stop order types DO exist on spot** via the API (`stop-loss`, `stop-loss-limit`, `trailing-stop`, `trailing-stop-limit`). So exchange-resident protection is genuinely available — good, the design's requirement for a "supported stop type" is satisfiable. Two caveats: (a) a triggered stop-loss **executes as a market order and pays taker fees** plus whatever slippage the book gives — on a ~$6 position in a fast tick this is a meaningful fraction of the trade; (b) **[VERIFIED]** a resting stop is an **independent order not bound to the position** — "if you exit in an alternate way the stop loss must also be manually cancelled." That sentence is the whole ballgame for your race conditions (below).

**The real v1 race — even with the distant limit disabled.** You will simultaneously have a **resting exchange stop** and a **bot-initiated market sell** (the two-declining-candle exit). Both can fire at once. If the bot sells on declining candles and does not *confirm-cancel* the resting stop first, the stop can later trigger and try to **sell BTC you no longer own** → rejected order or, worse, a negative/short surprise; or both fill and you **double-sell**. Section 8 gestures at "only one full-position exit may complete," but the concrete coordination between an *exchange-resident stop* and a *software market exit* is not specified, and that is the race that will actually bite in v1. This must be written down before any SELL automation ships (Prompt 8).

**[VERIFIED] Conditional Close must be attached at submit time**, volume equals the primary, opposite side — you cannot bolt one on after a fill. So the "fill, then establish protection" sequence in `INITIAL_PROTECTION` is a *separate* order placed after the fact, which is exactly why the fill→protection window (§4) is unprotected and must be made crash-safe.

**Cancel/replace on entry** is fine against the API but see the no-order gap in §2.

---

## 4. Failure modes

**CRITICAL — the fill→protection window is not crash-safe (fail-open).**
Sequence: BUY fills → bot must *then* place the protective stop as a separate order → if the process dies (VPS restart, OOM, network partition) *between* those two steps, you hold a **naked long**. On restart, `RECONCILING` sees "owned BTC, no exit order." Section 5 maps that to *"`ENTRY_FILLED` or `POSITION_MANAGED`"* — and `POSITION_MANAGED` explicitly assumes *"the initial stop remains authoritative,"* i.e. it believes protection exists. So a restart can silently classify an **unprotected** position as a **managed, protected** one. That is a fail-open capital hole. **Fix:** reconciliation must treat *position present + no confirmed live protective order* as a forced path to (re)establish protection or emergency-flatten — never straight to `POSITION_MANAGED`. Make "protected" a fact read from the provider's open orders, not an inferred state.

**CRITICAL — cancelled-but-not-really-cancelled → the independent-stop trap.** Because the stop is not bound to the position, any exit-by-other-means that doesn't *provider-confirm* the stop's cancellation leaves a live sell order that can fire against a flat/empty balance later, in the *next* cycle. Every exit path must end in "provider confirms zero open exit orders AND zero owned quantity" before `CLOSED`. The doc says this for quantity; say it for *orders* too.

**Duplicate submit on partition.** If the bot submits and the connection drops before it sees the order id, it doesn't know whether the order exists. Mitigation is a **client order id / userref** as the idempotency key so reconciliation can recognize "my" order. This appears in Prompt 7 but **not in the architecture doc** — elevate it to the architecture as a first-class invariant, not a build-time detail.

**Stale candles / clock drift.** `MARKET_DATA_STALE_SECONDS=60` is good for ticks. Add the analogous guard for *candles*: refuse to act if the last closed candle is older than one interval + grace, and never act on the in-progress candle.

**Exchange outage / partial reconciliation.** Handled in spirit ("ambiguity → MANUAL_REVIEW_REQUIRED") but there is no defined **exit** from the safety states. How does the operator clear `HALTED` or `MANUAL_REVIEW_REQUIRED`? Manually, presumably — but the transition must exist and be logged, or the bot has dead-end states.

**Fail-open inventory (explicit):** (1) fill→protection gap on crash [CRITICAL]; (2) uncancelled independent stop surviving into next cycle [HIGH]; (3) reconciliation mapping naked position → managed [CRITICAL, same root as 1]; (4) acting on an unclosed candle [MEDIUM]. Everything else in the design fails *closed*, which is the correct default and is done well.

---

## 5. State machine

- **Missing authoritative transition table.** The diagram is linear; the real graph is not. Produce the full legal edge set (including every `RECONCILING → {mid-cycle state}` and every `{operational state} → {safety state}`), and make illegal transitions raise rather than pass. Prompt 1 asks for "legal transition validation" against a diagram that is incomplete — so the validator will encode an incomplete graph.
- **No `ENTRY_PENDING → FLAT` / halt path drawn.** Entry that never fills and is operator-cancelled has nowhere to go on the diagram.
- **Safety states are sources and sinks with no drawn edges.** Entry conditions and, critically, *exit* conditions are undefined (dead-end risk).
- **Restart re-entry into mid-cycle states is under-specified** beyond the one-line mapping — this is the §4 CRITICAL in state-machine clothing.
- **Infinite loop / tight re-entry:** `CLOSED → FLAT → immediate new BUY LIMIT` with no cooldown means a downtrend produces enter→stop→re-enter-lower→stop rapidly. `MAX_CONSECUTIVE_LOSSES=3` is the backstop and it works, but the absence of any re-entry cooldown or regime gate means the bot's default behavior in a downtrend is to bleed to the halt. Acceptable for v1 *only because* the consecutive-loss halt exists; flag a cooldown as a fast-follow.
- **Ownership:** who owns the truth of "position exists" — local state or provider? The doc says provider, but `POSITION_MANAGED` reads local "initial stop remains authoritative." Resolve to: provider owns existence and protection status; local owns intent and accounting.

---

## 6. Mathematical assumptions

`1% entry / 3% activation / 1% trail / 2 candles` are **arbitrary** — and that's fine *if* they are treated purely as configuration to be swept, which the `.env` already does. Two real issues:

- **Fixed percentages ignore volatility.** A 1% stop and a 1% trail mean completely different things when BTC realized vol is 20%/yr vs 80%/yr. Professionals scale these to ATR/realized vol. Keeping them fixed for v1 is a legitimate simplification, but the design should say so explicitly so the numbers aren't mistaken for tuned parameters.
- **The 1% stop is really a ~1.5%+ loss.** **[VERIFIED]** base-tier Kraken Pro spot fees run ≈ 0.16–0.25% maker / 0.26–0.40% taker. A stop exit is a **taker** market order. So a stopped trade loses ~1% (stop) + entry maker fee + exit taker fee + stop slippage ≈ **1.4–1.8%**, not 1%. Meanwhile the entry offset is 1% and activation is 3% gross. The *net* activation threshold after round-trip fees is closer to ~2.4–2.6% net — the design correctly requires "positive estimated net profit," so this is handled in logic, but the raw parameters oversell how much edge there is. The **loss-to-target asymmetry is worse than it looks on paper.**

**Remove anything?** Don't remove parameters — remove *redundant exit mechanisms* (§9): you currently have (a) exchange-resident stop, (b) monotonic software trailing floor computed off `highest_price`, and (c) two-declining-candle close rule. That's three overlapping ways to exit a winner, plus two different "high water" definitions (`current_price` in §7 vs `highest_close` in §5). For v1 you want to *test one clean idea*. Pick one trailing mechanism. My recommendation: **use Kraken's native `trailing-stop` as the single exchange-resident protective+trailing exit** (it survives bot downtime, which the software floor does not) and drop the software floor and the candle-count rule from v1, or keep exactly one of them and drop the native trail — but not all three. Three exit rules on one $6 position is the OmniTrade-complexity instinct in miniature.

---

## 7. Backtesting

The backtester is where a strategy this marginal will *lie to you*, and the biggest risk to the whole project is an over-optimistic backtest greenlighting live capital. Specific over-estimation vectors:

- **Optimistic limit fills.** A resting BUY LIMIT at `P` did **not** necessarily fill just because `candle.low ≤ P`. You have no queue position in OHLC data; a mere touch can leave you unfilled. If the backtester fills on touch, **fill rate and opportunity are overstated.** Gate fills more pessimistically (e.g. require `low < P` by a tick, or require trade-through with volume), and treat the result as an *upper bound*.
- **Same-candle stop-and-target** is listed as a test (good, you're aware) — resolve it pessimistically (assume the adverse leg first).
- **Stop fill price realism.** A stop at `P` in a down-candle does **not** fill at `P`; it fills at market below `P`. Model gap-through slippage on stops, not fill-at-trigger.
- **Fee side realism.** Entry = maker; **stop exit = taker.** If the backtester applies maker fees to both legs it understates cost. Wire the taker rate to the actual exit type.
- **Shadow mode inherits the same optimism** (Prompt 5 detects would-have-filled from candle rules). Shadow "fills" are an upper bound on real fills; the first live cycle must compare actual fill rate against shadow to calibrate the bias.
- **Parameter sweep = overfitting engine.** A 6-dimension grid on a limited BTC window will *always* surface a spuriously excellent combo. Section 19's "stable across nearby parameter values" instinct is exactly right — enforce it: require out-of-sample / walk-forward validation and reward robustness over peak return. Don't let the sweep's best cell become the live config.

If the backtester is built pessimistically on all five points and *still* shows positive net expectancy with a healthy MFE-retention, that is a real signal. If it needs optimistic fills to look good, it's noise.

---

## 8. Safety — is it safe enough for a $5 lane?

Not yet, for two reasons, one of which is that **the $5 lane cannot execute at all** (headline). The controls list in §11 is genuinely good — kill switch, live flag, reconcile-on-boot, consecutive-loss halt, halt-on-mismatch, halt-if-protection-fails. Those are the right minimum set. What's missing before live:

1. **Dynamic minimum-aware sizing (CRITICAL).** Size against `min_qty × live_price × buffer`, fetched from Kraken `AssetPairs`, not a hardcoded `$5`. If the resulting minimum exceeds your risk appetite, the honest conclusion is that BTC on Kraken is too expensive per unit for a `$5` lane and the proving size must rise (≈ `$8–$12` to clear `0.0001 BTC` with margin) or the instrument must change.
2. **Sub-minimum / dust policy (CRITICAL).** A partial fill can leave you holding **< 0.0001 BTC**, which you **cannot place a protective or exit SELL against** (below the sell minimum) — an unprotected, unsellable remainder, exitable only via Kraken's once-per-24h small-balance convert at a **3% fee**. On a ~$6 position a partial below the minimum is entirely possible. **Fix:** either require all-or-none fills, or size at **≥ 2× minimum** so any partial half still clears the minimum, or define an explicit dust-liquidation path. This is unglamorous and it is exactly the kind of edge that quietly strands capital.
3. **Independent-stop cancellation confirmation (HIGH)** before any alternate exit — see §3/§4.
4. **Exit transitions for safety states (MEDIUM)** — no way out of `HALTED`/`MANUAL_REVIEW_REQUIRED` is defined.

With 1–3 closed, a **properly-sized** single-cycle live proving run is reasonable. At literally `$5`, it is not runnable.

---

## 9. Simplicity — where OmniTrade's ghost reappears

The isolation discipline is excellent and the non-goals list is admirably ruthless. The complexity creep is subtler:

- **Three exit mechanisms + two high-water definitions** (§6). This is the clearest re-growth. Collapse to one.
- **`market_observations` persists every candle** (OHLC + floor + highest, per cycle) — an unbounded time-series in SQLite that you don't need to *store* to prove expectancy. Keep transitions, orders, fills. Recompute observations from source candles if needed. Persisting them is the "let's log everything" reflex that grows schemas.
- **6-dimension parameter-study framework** before a single fixed-parameter cycle has ever run. This is research infrastructure standing in front of the one result that matters. Defer it: prove *one* configured cycle end-to-end (backtest → shadow → one live) *first*, then build the sweep once you know the machine works. Right now it's negative expected value — it adds surface area before the core loop has produced any evidence.
- **12 operator events + 7 operator commands + systemd + log rotation + health CLI** (Prompt 9) — appropriate for unattended automation, but it's the exact ops-surface that ballooned last time. It's fine *if* it stays proportional to a one-position bot and doesn't acquire a dashboard, a metrics stack, and a config UI. Watch the boundary.
- **The §19 integration chain** (`execution → reconciliation → … → Decision Intelligence`) reintroduces, as an aspiration, the coupling the isolation boundary exists to prevent. Keep it as a *someday* note, not a roadmap the sidecar is built toward — the moment this bot starts importing OmniTrade concepts, you've rebuilt the thing you're escaping.

**Simplify to this v1:** fixed parameters (no sweep), one exit mechanism, one instrument, all-or-none or 2×-min sizing, transitions/orders/fills persisted only. Prove the loop. Add nothing until a real cycle has produced a real number.

---

## 10. Issue classification

### CRITICAL — must be resolved before Prompt 7 (first live capability)

1. **Sub-exchange-minimum sizing.** `MAX_POSITION_NOTIONAL_USD=5` < Kraken's `0.0001 BTC ≈ $6.25` minimum. The live lane cannot place an order. Replace the hardcoded cap with dynamic `AssetPairs`-driven sizing and raise the proving size above the live minimum with buffer.
2. **Partial-fill dust trap.** A partial can leave < minimum BTC, which cannot be protected or sold. Require all-or-none, or size ≥ 2× minimum, or define an explicit dust path.
3. **Fill→protection window is not crash-safe (fail-open).** A restart can classify a naked long as `POSITION_MANAGED` (believed protected). Reconciliation must read protection status from the provider and force protect-or-flatten when a position lacks a confirmed live protective order — never infer "protected."

### HIGH — resolve before or during Prompt 8 (SELL automation)

4. Independent exchange stop must be **provider-confirm-cancelled** before any alternate exit, and every `CLOSED` must confirm **zero open exit orders** as well as zero quantity, or a stale stop fires in the next cycle.
5. Native spot OCO does not exist; **software exit coordination is primary, not fallback** — specify it fully before enabling any two-exit configuration.
6. Backtester/shadow **optimistic-fill and maker-fee-on-taker-exit** biases will overstate profitability. Mandate pessimistic fills, taker fees on stops, and stop slippage.
7. **Strategy expectancy is structurally suspect** (adverse-selection entry; noise-triggered exit). Not an implementation blocker — it's the thing the bot exists to test — but the design must stop treating the parameters as neutral and must gate live on out-of-sample evidence, not a swept peak.

### MEDIUM

8. FSM diagram ≠ prose; **no authoritative transition table**; safety states have no defined exits.
9. Elevate **client-order-id idempotency** from build prompt to architecture invariant.
10. **Candle-completion guard** (act only on fully-closed candle; staleness guard for candles).
11. No **re-entry cooldown / regime gate** (mitigated by consecutive-loss halt for v1).
12. `MAX_DAILY_LOSS_USD=1` is barely reachable before `MAX_CONSECUTIVE_LOSSES=3` — harmless, but reconcile the two so the intended binding control is clear.

### LOW

13. Two "high water" definitions (`current_price` vs `highest_close`) — unify.
14. `market_observations` persistence is unnecessary for v1.
15. Parameter-study framework is premature; defer until the core loop produces evidence.
16. §19 integration chain risks reintroducing OmniTrade coupling; keep as a note, not a target.

---

## Verdict

**APPROVE WITH CHANGES.**

**Not a REJECT.** The foundation is sound and, frankly, more disciplined than most systems that reach this stage: hard isolation, reconcile-before-act, provider-evidence-as-truth, protection-before-management, and evidence-gated promotion are the right bones. None of the CRITICAL issues require a redesign; they are concrete holes in sizing, partial-fill handling, and restart reconciliation — all fixable in place.

**Why "with changes" and not clean approve.** Three CRITICAL issues each independently put capital at risk or make the stated `$5` deliverable impossible, and they all live at the live-execution boundary.

**Recommended sequencing:**
- **Begin now:** Prompts 1–6 (skeleton, math, persistence, read-only adapter, shadow, backtester). None touch the CRITICALs, and building the backtester *pessimistically* is the fastest way to learn whether the strategy is worth proving at all. Fix the FSM transition table (MEDIUM #8) during Prompt 1 since the validator encodes it.
- **Gate before Prompt 7:** close CRITICAL #1–#3. Do not write a live BUY path against a size the exchange will reject or a reconciliation that can believe a naked position is safe.
- **Gate before Prompt 8:** close HIGH #4–#5.
- **Gate before Prompt 10 (live):** HIGH #6–#7 satisfied — i.e. the pessimistic backtest and shadow show positive net expectancy *out-of-sample*, not just on a swept peak.

One honest closing note, because you asked me to assume real money: the architecture can be made safe, but "safe" and "profitable" are different verdicts. This review clears the *safety and soundness* bar with the changes above. It does **not** endorse the strategy's edge — my genuine expectation is that a correctly pessimistic backtester will show the buy-1%-below / exit-on-two-down-closes logic to be flat-to-negative after costs on 15m BTC. That is not a reason to stop. It is the entire reason to build the disproving machine correctly and let it return a real number before any capital moves.
