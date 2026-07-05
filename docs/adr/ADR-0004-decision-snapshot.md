# ADR-0004: Decision Snapshot

## Status
Accepted

## Context

A Decision Record (ADR-0002, `DECISION_INTELLIGENCE_ENGINE.md` §4) captures the reasoning, evidence, and outcome of a decision — but its fields (`indicators`, `market_regime`, and so on) are summaries. Summaries are enough for everyday display and querying, but not enough for rigorous future work: reproducing exactly why a decision was made, replaying it, or building a trustworthy supervised training set all require the *exact* state that produced the decision, not a best-effort reconstruction pieced together from tables that may have since been recalculated, corrected, or extended with new columns. Without an explicit, immutable capture of that state, "what did the model actually see" becomes an increasingly unreliable question to answer as the system evolves.

## Decision

Every Decision Record is paired, one-to-one, with an immutable **Decision Snapshot** — a point-in-time capture, by value, of the exact state that produced the decision. Full field list and design rules are in `DECISION_INTELLIGENCE_ENGINE.md` §4a. Key elements:

- Captures identity/timing (timestamp, asset, exchange, timeframe), market context (OHLCV, indicators, generated features, regime, volatility, spread/liquidity where available), decision inputs (strategy inputs, risk inputs), portfolio state (current position, open trades, portfolio exposure), and five mandatory version-pin fields (parameter set, strategy, AI/model, decision engine, and configuration versions).
- Written once, at decision time, and **never modified afterward** — even if source data is later found to be wrong or is recalculated under a new methodology.
- Values are captured by value, not as live references to current indicator/candle tables, so a later change to how an indicator is computed can never retroactively alter what a Decision Snapshot says the indicator value was at the time.
- Purely observational, like the rest of the DIE — never read by, or fed back into, any real-time decision path.

## Alternatives Considered

- **Rely on the Decision Record's existing summary fields (`indicators`, `market_regime`) as sufficient context.** Rejected because these are intentionally lightweight for everyday use, and because they don't capture portfolio state, version pins, or spread/liquidity context — all of which are necessary for true reproducibility.
- **Store live references (foreign keys) to the relevant candle/indicator rows instead of capturing values.** Rejected because underlying data can be corrected or recalculated over time (e.g., a candle backfill fixing a gap, an indicator methodology changing), which would silently and retroactively change what a historical decision "looked like" — undermining the entire reproducibility goal.
- **Treat version pins (parameter set, strategy, AI/model, decision engine, configuration versions) as optional/best-effort fields.** Rejected — a Decision Snapshot missing any version pin is explicitly treated as incomplete (`DECISION_INTELLIGENCE_ENGINE.md` §4a.3), since without them, "replay this decision" or "compare against the current model version" become unanswerable questions.

## Consequences

- Every Decision Record now implies a corresponding write to an immutable snapshot store, which is a meaningful additional storage/write responsibility for the eventual DIE implementation (`app/services/decisions/snapshot.py`, per `BACKEND_MODULE_SPECS.md`) — this cost was accepted as necessary for reproducibility rather than optional polish.
- Future replay, comparison, and training-set construction (`DECISION_INTELLIGENCE_ENGINE.md` §6, §7) can now be trusted to reflect the exact original context, not an approximation — this is a prerequisite for any serious future AI training work built on Decision Records.
- Because snapshots are immutable and captured by value, storage grows monotonically with decision volume and cannot be compacted by re-deriving values later — this is an accepted trade-off, consistent with the same append-only philosophy already used for `audit_log`.
- Like the rest of the DIE, this is an architectural commitment for a future implementation phase, not an MVP deliverable.
