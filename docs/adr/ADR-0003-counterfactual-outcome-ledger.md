# ADR-0003: Counterfactual Outcome Ledger

## Status
Accepted

## Context

The Decision Intelligence Engine (ADR-0002), as originally scoped, records and learns from the decisions the platform actually made — the trade taken, the signal rejected, the position held. But a platform that only studies its own actions can never learn whether its *inaction* was wise: if OmniTrade waits through a breakout that would have paid off, or correctly waits through a false breakout, neither event leaves any trace in `signals`/`trades`/Decision Records as originally scoped. This is a real gap for a platform whose stated goal (`PROJECT_VISION.md`) is to accumulate wisdom from its own history — half of the platform's decisions (every WAIT, every rejected signal) were at risk of being invisible to that learning process.

## Decision

The Decision Intelligence Engine includes a core subsystem — the **Counterfactual Outcome Ledger (COL)** — that creates **shadow outcomes** for all three possible actions (BUY, SELL, WAIT) every time the platform evaluates a market and arrives at a real recommendation, regardless of which action was actually taken. Full design is in `DECISION_INTELLIGENCE_ENGINE.md` §8. Key elements:

- Shadow actions are never executed with real or paper capital — they exist purely as computed, labeled data.
- A background job revisits each decision's shadow outcomes at fixed horizons (V1: 15 minutes, 1 hour, 24 hours; full future set: 5 minutes, 15 minutes, 1 hour, 4 hours, 24 hours) and computes the hindsight-best action and structured lesson tags (e.g., `missed_breakout`, `wait_was_correct`).
- The COL is explicitly scoped as a **lightweight, continuously-running companion process**, not a second backtesting engine — it evaluates only decisions the live/paper platform actually made, using a small fixed feature snapshot, never replaying arbitrary historical windows or alternate parameter sets.
- Version 1 is deliberately narrow: BTC only, evaluated once per minute, three horizons, no heavy compute. Expansion to more assets/horizons/frequency is explicitly deferred to later versions.
- The COL is a subsystem *within* the DIE (per ADR-0001's four-engine framing), not a separate engine.

## Alternatives Considered

- **Rely solely on the existing backtesting engine (`STRATEGY_ENGINE.md`) for "what if" analysis.** Rejected because backtesting answers a different question — "how would this strategy perform over a historical window under different parameters" — not "was this specific real-time decision, taken or not, actually the best one available." Extending backtesting to also do this would risk conflating two distinct tools with two distinct performance/complexity profiles.
- **Only track shadow outcomes for rejected/WAIT decisions**, since executed trades already have a real outcome. Rejected because tracking all three shadow actions uniformly (including the one that matches the real action) keeps the evaluation methodology consistent and directly comparable across every decision, rather than having executed and non-executed decisions analyzed by different mechanisms.
- **Build the full 5-horizon, multi-asset version immediately.** Rejected in favor of an explicitly narrow V1 (BTC-only, 3 horizons, once per minute) to keep the mechanism lightweight and prove it out before expanding — consistent with the "not a giant backtesting engine" scope discipline this ADR's decision embeds.

## Consequences

- The platform gains a mechanism to learn from roughly twice the decision surface it previously could (actions taken *and* actions not taken), which is expected to meaningfully improve future confidence calibration and risk-rule validation once implemented.
- A new, explicit scope boundary now exists and must be actively maintained: any proposal to have the COL replay historical windows, simulate alternate parameters, or otherwise resemble backtesting is a signal that the proposal belongs in `STRATEGY_ENGINE.md`'s backtesting engine instead, not an extension of the COL.
- V1's narrow scope (BTC-only, 3 horizons) means most of the platform's assets and the full horizon set will not benefit from counterfactual learning until a deliberate V2 decision is made and implemented.
- Like the rest of the DIE, implementation is deferred to a future, unscheduled phase (`MVP_BUILD_PLAN.md`) — this ADR records the commitment, not an implementation deadline.
