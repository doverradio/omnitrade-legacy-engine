# OmniTrade Legacy Engine - Glossary

This glossary explains common trading and backtesting terms in plain English.

## Backtest
A backtest is a simulation that asks, "How would this strategy have behaved in the past if we had run it with these settings?"

Example: Run MA Crossover on BTCUSDT from January to June with $25 starting capital and review the results.

## Strategy
A strategy is a rule set that decides when to buy, sell, or hold based on market data.

Example: "Buy when fast moving average crosses above slow moving average; sell when it crosses below."

## Parameter Set
A parameter set is a saved group of strategy settings.

Example: One MA Crossover parameter set uses fast=10 and slow=50, while another uses fast=20 and slow=100.

## Starting Capital
Starting capital is the amount of money a backtest starts with.

Example: If a backtest starts at $25, all gains and losses are measured from that $25 baseline.

## Ending Equity
Ending equity is the total account value at the end of a run.
It usually includes available cash plus any position value at the end.

Example: Start at $25 and finish at $29.12 means ending equity is $29.12.

## Net Profit / Loss
Net profit or loss is how much money was gained or lost overall.

Simple formula: Ending Equity - Starting Capital

Example: $29.12 ending equity minus $25.00 starting capital equals +$4.12 net profit.

## Total Return
Total return is performance shown as both dollars and percentage.

Simple formula for percent form: (Net Profit or Loss / Starting Capital)

Example: +$4.12 on $25 is +16.48% (commonly rounded to +16.5%).

## Win Rate
Win rate is the share of completed trades that were profitable.

Example: If 6 out of 10 completed trades made money, win rate is 60%.

## Max Drawdown
Max drawdown is the largest drop from a peak equity value to a later low during a run.
It helps show downside pain, not just final return.

Example: Equity rises to $30, later falls to $27 before recovering. Drawdown at that point is 10%.

## Sharpe-like Ratio
Sharpe-like ratio is a simplified risk-adjusted return metric.
Higher values generally mean better return relative to volatility.

Example: A strategy with steady gains may have a higher Sharpe-like score than one with the same return but bigger swings.

## Fee Drag
Fee drag is the performance reduction caused by fees and execution costs.
It shows how much gains are eaten by costs.

Example: If a strategy made gross gains of $10 but fees/slippage consumed $3.40, fee drag is high.

## Slippage
Slippage is the difference between expected price and actual fill price.
In backtests, it is a modelled execution cost.

Example: Expected buy at $100.00, modelled fill at $100.10 means $0.10 adverse slippage.

## Basis Points (bps)
A basis point is one-hundredth of one percent.

Conversion:
- 1 bps = 0.01%
- 10 bps = 0.10%
- 100 bps = 1.00%

Example: A 10 bps fee means a 0.10% cost.

## Equity Curve
An equity curve is a time series chart of account value through a run.
It helps users see path and volatility, not only the final result.

Example: Two strategies can end at $30, but one has a smoother equity curve and lower drawdowns.

## Trade
A trade is one execution event, such as a buy fill or a sell fill.

Example: A buy of 0.00038 BTC at $64,200 is one trade event.

## Round Trip
A round trip is a full cycle: entry and exit for the same position.
Usually this means one buy and one matching sell.

Example: Buy BTC, then later sell BTC to close the position. That pair is one round trip.

## Small Account Mode
Small Account Mode means the platform is intentionally designed to work at low starting balances (minimum $25), not only larger balances.
It emphasizes realistic costs, fractional sizing, and clear dollar impact.

Example: A strategy that looks fine at $10,000 but fails at $25 due to fee drag is flagged as a poor small-account fit.

## Paper Trading
Paper trading is simulated trading with no real money at risk.
It uses live-like flows for testing behavior safely.

Example: A strategy places simulated orders in a paper account to validate execution logic before any real-money discussion.

## Risk Engine
The risk engine is the rule system that decides whether a signal is allowed, resized, or rejected based on safety limits.

Example: If a position is too large for account equity or a kill switch is active, the risk engine can reject the trade.

## Strategy Lab
Strategy Lab is the UI area where users select strategies, edit parameters, save parameter sets, run backtests, and compare outcomes.

Example: Change MA periods, save a new preset, run a backtest, and compare results to a prior preset.

## Trend Following
Trend following means trying to trade in the same direction as the current market trend.

Example: If price is steadily rising, a trend-following strategy prefers buy signals over sell signals.

## Mean Reversion
Mean reversion means expecting price to move back toward its typical average after moving too far away.

Example: If price drops quickly and appears oversold, a mean-reversion strategy may look for a bounce.

## Breakout
A breakout is when price moves beyond a recent high or low range, which can signal a new move beginning.

Example: If price closes above the highest level of the last 20 candles, that may be treated as an upside breakout.

## Volatility (Strategy Context)
Volatility is how much and how quickly price moves.
Higher volatility means bigger swings; lower volatility means calmer movement.

Example: A volatility-focused filter may block trades when the market is too noisy or too quiet.

## Typical Trade Frequency
Typical trade frequency describes how often a strategy usually opens and closes trades.

Example: A high-frequency setup might trigger many trades per week, while a slower trend setup might trigger only a few.
