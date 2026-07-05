# ADR-0007: Decision Quality Engine

## Status
Accepted

## Context

The Counterfactual Outcome Ledger (ADR-0003) gives OmniTrade a way to know, in hindsight, which action (BUY/SELL/WAIT) would have been best at a given horizon. On its own, that comparison is tempting to use as a direct proxy for "how good was this decision" — but doing so would be a mistake: **a bad decision can accidentally make money, and a good decision can occasionally lose money.** A strategy that overrides its own risk sizing and gets lucky once is indistinguishable from a well-reasoned trade if the only measure is "did it agree with hindsight" or "did it make money." Left unaddressed, this would mean the platform's own learning mechanisms (COL pattern-mining, future retraining per `AI_LAYER.md` §6) could end up reinforcing lucky recklessness and penalizing disciplined caution — the opposite of the wisdom-accumulation goal in `PROJECT_VISION.md`.

## Decision

The Decision Intelligence Engine includes a core subsystem — the **Decision Quality Engine (DQE)** — that scores the quality of a decision independently from its raw profitability. Full design is in `DECISION_INTELLIGENCE_ENGINE.md` §8a. Key elements:

- The DQE is a subsystem **inside** the Decision Intelligence Engine, not a fifth core engine — it does not change the four-engine framing established in ADR-0001.
- It depends on the Counterfactual Outcome Ledger's (ADR-0003) resolved shadow outcomes and hindsight-best-action data — a Decision Quality Score is only computed once relevant counterfactual data exists, never at decision time and never as a placeholder/default value beforehand.
- The score considers multiple independent dimensions, not a single formula: hindsight-best-action agreement, confidence calibration, market regime accuracy, whether risk management improved or worsened the outcome, overreaction, hesitation, fee/slippage erosion of the apparent edge, and position sizing appropriateness.
- A WAIT can score *highly* even when a hindsight-better action existed, if the process (evidence, risk context) justified the caution. A high-confidence BUY that loses badly against a hindsight-correct SELL scores *low*, since that pattern is close to the clearest possible signal of a calibration or regime-classification failure.
- Like the rest of the DIE, the DQE is purely observational: it never overrides the risk engine, never automatically changes strategy behavior, and never replaces `AI_LAYER.md`'s real-time Signal Confidence Scorer — it is a retrospective, human-reviewed diagnostic.

## Alternatives Considered

- **Use the Counterfactual Outcome Ledger's hindsight-best-action agreement rate directly as the platform's quality metric**, without a separate scoring subsystem. Rejected because agreement-with-hindsight and quality-of-reasoning are genuinely different questions — collapsing them into one number would specifically reproduce the "bad decision that got lucky" failure mode this ADR exists to prevent. (This is also why Counterfactual Agreement Rate is kept as its own distinct dashboard metric, separate from Overall Decision Quality — see `DECISION_INTELLIGENCE_ENGINE.md` §8a.5.)
- **Propose the DQE as a fifth core engine**, given its distinct purpose from decision recording (Decision Records) and counterfactual tracking (COL). Rejected in favor of keeping it a subsystem of the DIE — its entire function is to evaluate Decision Records and COL output, which places it naturally within the DIE's existing boundary (ADR-0001, ADR-0002) rather than warranting a new architectural peer.
- **Score decisions in real time, at decision time, using only the information available then.** Rejected because a genuine quality judgment that accounts for "was BUY/SELL/WAIT actually best" requires counterfactual data that, by definition, isn't available until after the fact — a real-time version of this would just be a duplicate of the existing Signal Confidence Scorer (`AI_LAYER.md` §2.2), not a new capability.

## Consequences

- Future strategy and allocation review (`AI_LAYER.md` §2.5, `STRATEGY_ENGINE.md` §3) gains a second, independent lens beyond raw backtest/paper performance — a strategy can now be flagged for "wins a lot but decides poorly" or protected despite "loses sometimes but decides well," which is a meaningfully richer signal than P&L alone.
- The DQE introduces additional deferred implementation scope on top of the DIE and COL (a new scoring service, a new set of dashboard metrics) — like the rest of the DIE, this is explicitly a future, unscheduled phase (`MVP_BUILD_PLAN.md`), not an MVP deliverable.
- Because scoring depends on COL data, the DQE cannot exist or be validated before the COL is implemented (ADR-0003) — this is an explicit sequencing dependency for whenever DIE implementation work begins.
- The multi-dimensional nature of the score (§8a.3) means it cannot be reduced to a single simple formula without care; getting the weighting between dimensions right is nontrivial implementation-phase work this ADR deliberately does not pre-decide.
