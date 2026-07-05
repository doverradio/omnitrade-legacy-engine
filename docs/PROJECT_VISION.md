# PROJECT_VISION.md

## OmniTrade Legacy Engine — Project Vision

### 1. Mission Statement

OmniTrade Legacy Engine is a web-based, AI-assisted trading research and paper-trading platform. Its purpose is to help a family (and eventually a small circle of trusted users) research markets, design and validate trading strategies, and practice disciplined execution in a simulated environment — with a deliberate, staged path toward live trading only after rigorous validation.

This is **not** a signal-selling product, a "set and forget" bot, or a get-rich-quick scheme. It is infrastructure: a durable, auditable, extensible system meant to outlive any single strategy, market cycle, or contributor.

The platform is built around four permanent foundational engines: the **Market Intelligence Engine** (ingestion, indicators, regime detection), the **Strategy Evolution Engine** (strategy modules, backtesting, allocation), the **Decision Intelligence Engine** (the platform's permanent memory and reasoning system — see `DECISION_INTELLIGENCE_ENGINE.md`), and the **Portfolio Intelligence Engine** (account state, positions, performance). These are architectural constants, not features — every capability described in this doc set is understood to live within one of these four engines.

### 2. Philosophy

- **Process over prediction.** No model can reliably predict markets. The system's job is to enforce a repeatable process: hypothesize → backtest → paper trade → review → (maybe) go live, in that order, every time.
- **Boring is good.** Reliability, clear logs, and reproducibility matter more than clever features. A boring system that never silently loses money is worth more than an exciting one that occasionally does.
- **Everything is a hypothesis.** Every strategy, every AI output, every parameter set is treated as a falsifiable hypothesis with a paper trail, not as truth.
- **Transparency over black boxes.** Every AI-generated signal, weight, or recommendation must come with a human-readable explanation grounded in observable inputs — not just a score.
- **Memory over history.** The platform should not merely log what happened — it should preserve why, as structured, queryable Decision Records (see `DECISION_INTELLIGENCE_ENGINE.md`) that let it learn from both its trades and the trades it correctly or incorrectly avoided. Each such record is anchored by an immutable Decision Snapshot of the exact state that produced it, so the platform's memory can be replayed and analyzed exactly, not reconstructed approximately after the fact (`DECISION_INTELLIGENCE_ENGINE.md` §4a).
- **The best decision, not just the taken decision.** The platform should not only ask "did our trade make money?" — it should ask what actually would have been the best action available at that moment, including for the trades it didn't take. This is the governing question behind the Decision Intelligence Engine's Counterfactual Outcome Ledger (`DECISION_INTELLIGENCE_ENGINE.md` §8).
- **A good decision and a profitable decision are not the same thing.** A bad decision can accidentally make money; a good decision can occasionally lose money. The Decision Intelligence Engine's Decision Quality Engine (`DECISION_INTELLIGENCE_ENGINE.md` §8a) exists specifically to judge decisions on their reasoning, not just their outcome — so the platform never learns to reward luck or punish discipline.
- **Family legacy, not a startup pitch.** The system is built to be understood and maintained by non-specialists over years/decades: clear docs, simple deploys, no exotic infra dependencies.

### 3. Non-Goals

OmniTrade Legacy Engine explicitly does **not**:

- Execute live trades with real money in its MVP phase.
- Promise, imply, or guarantee any rate of return, win rate, or profitability.
- Act as a substitute for licensed financial, tax, or legal advice.
- Attempt high-frequency trading, market making, or latency-sensitive arbitrage.
- Trade on margin, use leverage, or use derivatives in the MVP.
- Automatically re-enable itself after a risk-engine kill switch trips (a human must review and re-arm).
- Rely on paid, exotic, or fragile data vendors as hard dependencies in the MVP.

### 4. Safety Principles

1. **Simulation before capital.** No strategy reaches live trading without passing backtesting *and* a minimum paper-trading observation period with pre-defined success criteria.
2. **Defense in depth.** Risk controls exist independently at the strategy level, the account level, and the platform level (see `RISK_ENGINE.md`). A failure in one layer should not cascade.
3. **Explainability is mandatory.** No trade, signal, or allocation decision may be taken by the AI layer without a logged explanation referencing the underlying data (see `AI_LAYER.md`).
4. **Auditability is mandatory.** Every state-changing action (parameter change, trade, signal, model decision) is written to an immutable audit log (see `DATABASE_SCHEMA.md`).
5. **Fail closed, not open.** On ambiguous errors, missing data, or system faults, the default behavior is "do nothing" / "flatten to cash," never "guess and trade."
6. **Human-in-the-loop by default.** The MVP does not permit fully autonomous live trading. A human approves any transition from paper to live capital, and can pause/kill the system at any time.

### 5. Long-Term Family Legacy Vision

OmniTrade Legacy Engine is intended to grow across several horizons:

- **Horizon 1 (MVP, 0–6 months):** Historical backtesting, crypto + stock paper trading, dashboard, first strategy modules, basic AI signal scoring, and full risk/audit infrastructure.
- **Horizon 2 (6–18 months):** Multi-strategy ensembles, richer AI regime classification, deeper post-trade review/learning loop, more asset classes, optional small-scale live trading with strict caps.
- **Horizon 3 (18+ months):** Multi-user/family access with role-based permissions, richer reporting for tax/record-keeping, potential mobile companion views, and a documented "playbook" so the system can be handed down and understood by the next generation of maintainers — not just used, but modified safely.

Over this same span, the Decision Intelligence Engine's accumulated Decision Record store (`DECISION_INTELLIGENCE_ENGINE.md`) is expected to become one of the platform's most valuable assets in its own right — not the strategies, not any single year's returns, but the structured record of what the platform believed, why, and what it learned, compounding across every horizon above.

Success is not measured by short-term returns. Success is measured by:
- Whether the system's decisions are always explainable after the fact.
- Whether risk controls have ever been tested and held.
- Whether a new maintainer, with no prior context, could read these docs and safely operate or extend the system.

### 6. Guiding Constraint

If a proposed feature increases returns but decreases auditability, explainability, or safety margins, **it does not ship** without an explicit, logged, human decision to accept that trade-off.
