"""Deterministic fee and slippage model.

Kept intentionally simple: slippage is a fixed percentage adverse price
adjustment applied at fill time (not volume- or volatility-dependent); fees
are a fixed percentage of executed notional. This is a documented
simplifying assumption for V1, not a claim of real-world precision.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .config import SimulationConfig

_ONE = Decimal("1")


@dataclass(frozen=True)
class CostModel:
    fee_pct: Decimal
    slippage_pct: Decimal

    @classmethod
    def from_config(cls, config: SimulationConfig) -> "CostModel":
        return cls(fee_pct=config.fee_pct, slippage_pct=config.slippage_pct)

    def effective_buy_price(self, raw_price: Decimal) -> Decimal:
        """Price actually paid per unit, after adverse slippage. A buyer
        pays more than the raw/quoted price."""
        return raw_price * (_ONE + self.slippage_pct)

    def effective_sell_price(self, raw_price: Decimal) -> Decimal:
        """Price actually received per unit, after adverse slippage. A
        seller receives less than the raw/quoted price."""
        return raw_price * (_ONE - self.slippage_pct)
