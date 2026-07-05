# ADR-0005: Small Account Mode

## Status
Accepted

## Context

OmniTrade Legacy Engine's original design implicitly assumed paper account starting balances large enough that percentage-based risk rules, position sizing, and fee/slippage assumptions would behave "normally" — the kind of account size where a 5% position and a $10 exchange minimum order are a rounding error. But the platform's actual intended first users (per `PROJECT_VISION.md`'s family-legacy framing) include people starting with as little as $25 of real intent to learn. At that scale, exchange minimum order sizes, fee percentages, and fractional-share support stop being edge cases and start being the dominant factor in whether a strategy can even execute as designed. Without an explicit design commitment, the platform risked working correctly only for accounts large enough to make its assumptions invisible — which is exactly the population least in need of a safe, low-stakes place to learn.

## Decision

OmniTrade Legacy Engine treats a **$25 minimum starting balance as the default proving ground, not a toy mode** — full requirements in `SMALL_ACCOUNT_MODE.md`. Key elements:

- Paper account and backtest starting balances support any amount ≥ $25, enforced at the database (`CHECK` constraint), API, and UI layers — not just a UI convenience.
- The risk engine's position-sizing rules are purely percentage-of-equity based, with a new explicit rule (`position_below_minimum_order_size`, `RISK_ENGINE.md` §2.1a) that **rejects** — never rounds up — a trade whose calculated size falls below an asset's exchange-defined minimum order size.
- Every result surface reports both dollar and percentage figures together, since percentage-only reporting can make a $4 gain on a $25 account look identical in tone to a $4,000 gain on a $25,000 account.
- Fee/slippage modeling must be realistic at small scale, with an explicit "fee drag" metric and warnings when a strategy's fee drag or minimum-trade-size mismatch makes it a poor fit for a given account size.
- No strategy may assume large account size; every strategy must handle a minimum-viable-position rejection gracefully.

## Alternatives Considered

- **Treat small accounts as an unsupported edge case**, with a recommended minimum starting balance well above $25 (e.g., $500+). Rejected because it directly contradicts the platform's stated purpose of being a genuinely useful proving ground for family members starting with limited capital, not just a tool for those who already have it.
- **Support small balances only in the UI (allow entering $25) without corresponding risk-engine and fee-modeling changes.** Rejected because this would silently produce misleading or broken behavior — a $25 account with position sizing rules tuned for $10,000 accounts would either never trade (rounded-up minimums) or take dangerously oversized positions (rounded-down minimums), neither of which is acceptable.
- **Build a separate, reduced-feature "starter mode" for small accounts.** Rejected — this is the specific framing the design principle explicitly rejects ("not a toy mode"); small accounts get the full platform, with the same rules that would apply to any account size.

## Consequences

- Every future strategy, risk rule, and UI surface must be validated at the $25 floor as part of its normal design/promotion process, not as an afterthought — this is now a standing constraint on all future work, not a one-time feature.
- Small accounts will legitimately be unable to execute some strategies or assets (e.g., a high-priced stock without fractional support) — this is treated as an honest, informative constraint to surface clearly, not a bug to engineer around.
- The platform's risk and fee-modeling code is more complex than it would be if it only had to support large accounts, since it must remain correct and non-misleading across a much wider range of starting balances.
