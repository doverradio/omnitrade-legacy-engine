"""Capital allocation policy: kept deliberately separate from strategy
signal generation (engine.py / strategies/).

A `TradeRecord` produced by the engine already carries a `net_return_pct`
and `gross_return_pct` that are scale-invariant -- fees and slippage are
percentages, so the same trade sequence has the same percentage returns no
matter how much money is behind it. That means the entire question of "how
much of my capital do I risk, and what do I do with realized profit" can be
answered as a pure post-processing pass over the trades, without touching
entry/exit logic at all. This module is that pass.

This directly serves one goal: capital allocation choices (how aggressively
to deploy, how much profit to compound vs. withdraw vs. reserve for taxes)
must never be allowed to obscure whether the underlying strategy itself has
positive expectancy. `raw_strategy_net_return_pct` in the result is always
computed by replaying the SAME trades under 100% deployment / 100%
compounding / 0% withdrawal / 0% tax reserve, regardless of which policy is
actually being reported, so the two numbers can always be compared side by
side.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Sequence

from .strategy import TradeRecord

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class CapitalPolicy:
    name: str
    trade_deployment_pct: Decimal  # % of current trading capital risked per trade
    profit_compound_pct: Decimal   # of REALIZED PROFIT (not principal): reinvested
    profit_withdrawal_pct: Decimal  # of realized profit: withdrawn
    profit_tax_reserve_pct: Decimal  # of realized profit: set aside for taxes

    def __post_init__(self) -> None:
        if not (Decimal("0") < self.trade_deployment_pct <= _HUNDRED):
            raise ValueError(
                f"trade_deployment_pct must be in (0, 100], got {self.trade_deployment_pct}"
            )
        for name, value in (
            ("profit_compound_pct", self.profit_compound_pct),
            ("profit_withdrawal_pct", self.profit_withdrawal_pct),
            ("profit_tax_reserve_pct", self.profit_tax_reserve_pct),
        ):
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        total = self.profit_compound_pct + self.profit_withdrawal_pct + self.profit_tax_reserve_pct
        if total != _HUNDRED:
            raise ValueError(
                "profit_compound_pct + profit_withdrawal_pct + profit_tax_reserve_pct "
                f"must sum to exactly 100, got {total} "
                f"(compound={self.profit_compound_pct}, withdrawal={self.profit_withdrawal_pct}, "
                f"tax_reserve={self.profit_tax_reserve_pct})"
            )


# First-run default policies, as specified.
FULL_COMPOUNDING = CapitalPolicy(
    name="full_compounding",
    trade_deployment_pct=Decimal("100"),
    profit_compound_pct=Decimal("100"),
    profit_withdrawal_pct=Decimal("0"),
    profit_tax_reserve_pct=Decimal("0"),
)

BALANCED = CapitalPolicy(
    name="balanced",
    trade_deployment_pct=Decimal("25"),
    profit_compound_pct=Decimal("60"),
    profit_withdrawal_pct=Decimal("20"),
    profit_tax_reserve_pct=Decimal("20"),
)


@dataclass(frozen=True)
class CapitalTradeRecord:
    trade: TradeRecord
    deployed_notional: Decimal
    trading_capital_before: Decimal
    realized_pnl: Decimal
    compounded_amount: Decimal
    withdrawn_amount: Decimal
    tax_reserved_amount: Decimal
    trading_capital_after: Decimal
    cumulative_withdrawn_after: Decimal
    cumulative_tax_reserve_after: Decimal
    total_economic_value_after: Decimal


@dataclass(frozen=True)
class CapitalSimulationResult:
    policy: CapitalPolicy
    initial_capital: Decimal
    records: List[CapitalTradeRecord]
    trading_capital_final: Decimal
    cumulative_withdrawn_final: Decimal
    cumulative_tax_reserve_final: Decimal
    total_economic_value_final: Decimal
    next_trade_deployed_notional: Decimal
    raw_strategy_net_return_pct: Decimal  # same trades under FULL_COMPOUNDING, for comparison


def apply_capital_policy(
    trades: Sequence[TradeRecord],
    initial_capital: Decimal,
    policy: CapitalPolicy,
) -> CapitalSimulationResult:
    records = _replay(trades, initial_capital, policy)

    trading_capital_final = records[-1].trading_capital_after if records else initial_capital
    cumulative_withdrawn_final = records[-1].cumulative_withdrawn_after if records else _ZERO
    cumulative_tax_reserve_final = records[-1].cumulative_tax_reserve_after if records else _ZERO
    total_economic_value_final = (
        records[-1].total_economic_value_after if records else initial_capital
    )
    next_trade_deployed_notional = trading_capital_final * policy.trade_deployment_pct / _HUNDRED

    raw_records = _replay(trades, initial_capital, FULL_COMPOUNDING)
    raw_final_value = raw_records[-1].total_economic_value_after if raw_records else initial_capital
    raw_strategy_net_return_pct = ((raw_final_value / initial_capital) - Decimal("1")) * _HUNDRED

    return CapitalSimulationResult(
        policy=policy,
        initial_capital=initial_capital,
        records=records,
        trading_capital_final=trading_capital_final,
        cumulative_withdrawn_final=cumulative_withdrawn_final,
        cumulative_tax_reserve_final=cumulative_tax_reserve_final,
        total_economic_value_final=total_economic_value_final,
        next_trade_deployed_notional=next_trade_deployed_notional,
        raw_strategy_net_return_pct=raw_strategy_net_return_pct,
    )


def _replay(
    trades: Sequence[TradeRecord],
    initial_capital: Decimal,
    policy: CapitalPolicy,
) -> List[CapitalTradeRecord]:
    trading_capital = initial_capital
    cumulative_withdrawn = _ZERO
    cumulative_tax_reserve = _ZERO
    records: List[CapitalTradeRecord] = []

    for trade in trades:
        trading_capital_before = trading_capital
        deployed_notional = trading_capital_before * policy.trade_deployment_pct / _HUNDRED
        realized_pnl = deployed_notional * trade.net_return_pct

        if realized_pnl > 0:
            compounded_amount = realized_pnl * policy.profit_compound_pct / _HUNDRED
            withdrawn_amount = realized_pnl * policy.profit_withdrawal_pct / _HUNDRED
            tax_reserved_amount = realized_pnl * policy.profit_tax_reserve_pct / _HUNDRED
        else:
            # Losses are not "profit" -- there is nothing to allocate. The
            # full loss reduces trading capital; nothing is withdrawn or
            # reserved for tax out of a loss.
            compounded_amount = realized_pnl
            withdrawn_amount = _ZERO
            tax_reserved_amount = _ZERO

        trading_capital_after = max(_ZERO, trading_capital_before + compounded_amount)
        cumulative_withdrawn += withdrawn_amount
        cumulative_tax_reserve += tax_reserved_amount
        total_economic_value_after = (
            trading_capital_after + cumulative_withdrawn + cumulative_tax_reserve
        )

        records.append(
            CapitalTradeRecord(
                trade=trade,
                deployed_notional=deployed_notional,
                trading_capital_before=trading_capital_before,
                realized_pnl=realized_pnl,
                compounded_amount=compounded_amount,
                withdrawn_amount=withdrawn_amount,
                tax_reserved_amount=tax_reserved_amount,
                trading_capital_after=trading_capital_after,
                cumulative_withdrawn_after=cumulative_withdrawn,
                cumulative_tax_reserve_after=cumulative_tax_reserve,
                total_economic_value_after=total_economic_value_after,
            )
        )
        trading_capital = trading_capital_after

    return records
