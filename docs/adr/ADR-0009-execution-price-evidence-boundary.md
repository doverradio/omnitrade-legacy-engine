# ADR-0009: Execution Price Evidence Boundary

## Status
Accepted

## Context
Preview generation for live-crypto governance used a candle-owned reference price path coupled to the Asset selected for execution intent. In production, Kraken execution readiness could be healthy while candle ownership was absent for the Kraken execution asset symbol, causing preview failure even though provider-native executable quotes were available.

This coupled two distinct domains:
- Research market data: candles for ingestion, backtesting, feature generation, and strategy analysis.
- Execution price evidence: venue-native, time-bounded quote evidence required to evaluate and govern executable intent.

The coupling risk was highlighted by a rejected heuristic fallback that searched cross-venue/cross-quote symbols and selected the freshest candle. That approach weakened quote identity guarantees and could silently substitute USD intent with USDT evidence.

## Decision
Execution preview and risk reference pricing move to a provider-native Execution Price Evidence boundary.

1. Providers expose normalized price evidence via a dedicated interface.
2. Preview uses this evidence for risk reference pricing and stale checks.
3. Preview persists evidence provenance metadata (including evidence UUID) in persisted preview evidence summaries.
4. Evidence validation is fail-closed for provider mismatch, product mismatch, quote/base currency mismatch, invalid timestamps, stale evidence, and unavailable executable reference prices.
5. Research candles remain unchanged and continue to serve research, backtesting, and strategy/AI workflows.

## Alternatives Considered
1. Continue candle-owned preview pricing.
Reason rejected: execution reliability becomes dependent on research ingestion ownership and symbol mapping artifacts.

2. Cross-symbol freshest-candle fallback.
Reason rejected: introduces silent quote/exchange substitution risk and weakens explainability and audit fidelity.

3. Provider-specific logic embedded directly in preview service.
Reason rejected: duplicates provider logic and degrades multi-provider scalability.

## Consequences
Benefits:
- Stronger auditability: preview evidence now carries provider-native provenance and a stable evidence UUID.
- Stronger explainability: decision review can tie preview and risk context to exact observed market evidence.
- Better multi-provider extensibility: preview logic asks for normalized evidence, not provider-specific quote APIs.
- Safer fail-closed guarantees: no silent USD/USDT or venue substitution.

Tradeoffs:
- Additional provider contract surface area must be maintained.
- Providers with weak quote fields may require conservative normalization and stricter failure behavior.
- Existing consumers that inspect preview summaries should treat new evidence metadata as additive.

Future provider implications:
- Coinbase, Kraken, and future venues (IBKR, Alpaca, Binance, others) can implement the same evidence interface.
- Preview service remains provider-agnostic as long as provider evidence contract is satisfied.

Decision Intelligence implications:
- Preview evidence now persists an explicit evidence UUID and provenance payload, enabling deterministic traceability from decision context to market evidence used for the preview recommendation.
