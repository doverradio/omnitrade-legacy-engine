# MARKET_REGIME_CLASSIFICATION.md

Version: 1.0
Status: Constitutional Vision
Priority: Core Financial Intelligence
Scope: Market Environment Classification

---

# Purpose

The Market Regime Classification Engine continuously evaluates the current behavior of financial markets and classifies the environment in which OmniTrade is operating.

Financial markets do not behave the same way every day.

Strategies that perform exceptionally well in one environment may perform poorly in another.

The purpose of Market Regime Classification is to ensure that OmniTrade understands the environment before making financial decisions.

---

# Guiding Principle

Markets change.

Financial intelligence should recognize those changes rather than assume that one strategy fits every environment.

OmniTrade should adapt to the market it observes, not the market it wishes existed.

---

# Relationship to Other Documents

ASSET_UNIVERSE.md

Defines which assets are eligible.

CROSS_ASSET_OPPORTUNITY_SELECTION.md

Determines which opportunity deserves capital.

OPPORTUNITY_COST_MODEL.md

Compares competing opportunities.

This document provides environmental context that may influence strategy confidence, expected value, and capital allocation.

---

# Fundamental Question

The Market Regime Classification Engine continuously asks:

> What type of market are we currently operating in?

The answer provides context.

It does not directly authorize trades.

---

# Why Market Regimes Matter

The same trading signal can have different meanings under different market conditions.

Examples:

A moving average crossover during a strong trend may indicate continuation.

The same crossover during a highly volatile sideways market may produce repeated false signals.

Market Regime Classification helps OmniTrade interpret signals within their proper context.

---

# Market Regimes

Market regimes are classifications describing the current behavior of a market.

Examples include:

- Strong Bull Trend
- Moderate Bull Trend
- Strong Bear Trend
- Moderate Bear Trend
- Sideways Consolidation
- High Volatility
- Low Volatility
- Expansion
- Contraction
- Range Bound
- Breakout
- Recovery
- Capitulation

Additional regimes may be introduced as financial understanding evolves.

---

# Regimes Are Descriptive

Market regimes describe current conditions.

They do not predict the future.

The purpose of regime classification is to improve decision quality, not to forecast markets with certainty.

---

# Regime Independence

Every asset should maintain its own market regime.

Example

Bitcoin

Strong Bull Trend

Ethereum

Sideways

Gold

Recovery

Apple

Moderate Bear Trend

The regime of one asset should not automatically become the regime of another.

---

# Multi-Timeframe Awareness

Different timeframes may produce different regimes.

Example

Bitcoin

15 Minute

High Volatility

4 Hour

Strong Bull Trend

Daily

Sideways

Weekly

Recovery

Financial intelligence should understand that multiple regimes may exist simultaneously depending upon observation timeframe.

---

# Strategy Context

Strategies may respond differently under different market regimes.

Examples

Trend-following strategies

Prefer trending markets.

Mean-reversion strategies

Prefer range-bound markets.

Momentum strategies

Prefer expanding markets.

The Market Regime Classification Engine provides context.

Strategies remain responsible for their own decision logic.

---

# Confidence Adjustment

Future versions of OmniTrade may adjust confidence according to regime compatibility.

Example

Strategy

Moving Average Trend

Current Regime

Strong Bull Trend

Confidence

High

versus

Current Regime

Sideways

Confidence

Reduced

The engine should influence confidence rather than dictate decisions.

---

# Capital Allocation

Market regimes may influence:

- position sizing
- capital deployment
- opportunity ranking
- expected duration
- expected volatility
- risk tolerance

Capital Allocation remains responsible for final decisions.

Market Regime Classification supplies contextual intelligence.

---

# Portfolio Awareness

Portfolio Management may benefit from understanding multiple simultaneous regimes.

Examples

Crypto

Bear Market

Equities

Bull Market

Gold

Recovery

Treasuries

Low Volatility

Different market environments may produce different capital allocation opportunities.

---

# Opportunity Selection

Cross-Asset Opportunity Selection may incorporate regime awareness when comparing opportunities.

Example

Two opportunities possess identical expected edge.

One aligns strongly with the current market regime.

The other conflicts with it.

Future scoring models may prefer the more compatible opportunity.

---

# Opportunity Cost

Opportunity Cost may incorporate regime awareness.

Remaining invested in one market may become less attractive if another market enters a significantly more favorable regime.

Regime classification therefore contributes additional context to comparative opportunity evaluation.

---

# Risk Relationship

Risk remains independent.

Risk determines whether trading is acceptable.

Market Regime Classification describes the environment in which acceptable opportunities exist.

Risk always retains authority over safety decisions.

---

# Determinism

Given identical market data, Market Regime Classification should always produce identical classifications.

Replay should reproduce identical regime transitions.

Deterministic classification is essential for:

- auditing
- replay
- simulation
- AI review
- debugging

---

# Explainability

Every classification should be explainable.

Example

Bitcoin

Current Regime

Strong Bull Trend

Supporting Evidence

Higher highs

Higher lows

Increasing momentum

Stable volatility

Classification should never become an unexplained black box.

---

# Failure Behavior

If market conditions cannot be classified confidently:

Return:

Unknown

or

Uncertain

The engine should never fabricate confidence.

The absence of a reliable classification is itself valuable information.

---

# Future Enhancements

Future versions may incorporate:

- adaptive regime learning
- AI-assisted classification
- probabilistic regime confidence
- macroeconomic context
- intermarket relationships
- sentiment analysis
- on-chain analytics
- volatility clustering
- structural market shifts

These enhancements should improve classification quality while preserving deterministic principles.

---

# Design Principles

The Market Regime Classification Engine should remain:

- Deterministic
- Explainable
- Replayable
- Auditable
- Asset Aware
- Multi-Timeframe Aware
- Extensible
- Context Driven
- Provider Neutral

---

# Constitutional Principles

Markets continually evolve.

Financial intelligence should recognize changing environments.

Market regimes provide context rather than prediction.

Confidence should never exceed evidence.

Unknown is a valid classification.

Context should improve decision quality without replacing Economics, Risk, or Capital Allocation.

---

# Long-Term Vision

The Market Regime Classification Engine enables OmniTrade to understand not only individual opportunities, but the broader financial environments in which those opportunities exist.

Rather than asking:

"Did a trading signal occur?"

OmniTrade continually asks:

"What kind of market produced this signal, and how should that influence our confidence?"

As the platform expands across asset classes, providers, and global markets, regime awareness becomes an important layer of contextual intelligence, helping every financial decision remain adaptive, explainable, and grounded in observable market behavior.