"""Shared types and the pluggable Strategy protocol.

The engine (engine.py) owns the deterministic candle-replay loop, the
generic limit-order fill mechanics, and no-look-ahead enforcement. A
Strategy only decides entry prices and position-management rules, so
Strategy Version 2, Version 3, and future AI-generated strategies can plug
into the same simulation framework without touching engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Protocol, Sequence

from .candles import Candle


class ExitReason(str, Enum):
    TRAILING_FLOOR_BREACHED = "trailing_floor_breached"
    DECLINING_CLOSES = "declining_closes"


@dataclass(frozen=True)
class LimitOrder:
    """A resting virtual BUY LIMIT order.

    `placed_after_candle_index` is the index of the candle whose close set
    this price. Per the no-look-ahead policy, the order is only eligible to
    fill starting the NEXT candle -- never the same candle that set it.
    """
    price: Decimal
    placed_after_candle_index: int


@dataclass(frozen=True)
class FillResult:
    fill_price: Decimal
    candle_index: int


@dataclass
class PositionState:
    """Strategy-owned position bookkeeping. Opaque to the engine beyond
    being passed back into the Strategy's own hooks each candle."""
    fill_price: Decimal
    entry_candle_index: int
    initial_stop: Decimal
    profit_activation_price: Decimal
    trailing_distance_pct: Decimal
    trailing_active: bool = False
    highest_price_since_entry: Decimal = field(default=Decimal("0"))
    trailing_floor: Decimal = field(default=Decimal("0"))

    def __post_init__(self) -> None:
        if self.highest_price_since_entry == Decimal("0"):
            self.highest_price_since_entry = self.fill_price
        if self.trailing_floor == Decimal("0"):
            self.trailing_floor = self.initial_stop


@dataclass(frozen=True)
class ExitResult:
    exit_price: Decimal
    reason: ExitReason


@dataclass(frozen=True)
class TradeRecord:
    entry_candle_index: int
    entry_timestamp: datetime
    entry_order_price: Decimal
    raw_fill_price: Decimal
    effective_entry_price: Decimal
    entry_fee: Decimal
    entry_slippage_cost: Decimal
    exit_candle_index: int
    exit_timestamp: datetime
    raw_exit_price: Decimal
    effective_exit_price: Decimal
    exit_fee: Decimal
    exit_slippage_cost: Decimal
    exit_reason: ExitReason
    quantity: Decimal
    equity_before: Decimal
    equity_after: Decimal
    gross_return_pct: Decimal
    net_return_pct: Decimal
    highest_price_during_trade: Decimal
    lowest_price_during_trade: Decimal
    holding_candles: int


class Strategy(Protocol):
    """Pluggable strategy interface. `closed_candles` sequences passed to
    these hooks always end with the most recently completed candle -- never
    a candle that has not yet closed."""

    def propose_entry_price(self, closed_candles: Sequence[Candle]) -> Decimal:
        """Return the new resting BUY LIMIT price, computed only from
        already-completed candles (the last element is the candle that just
        closed)."""
        ...

    def open_position(self, fill: FillResult, closed_candles: Sequence[Candle]) -> PositionState:
        """Called once, the candle a BUY LIMIT fills, to initialize
        position-management state."""
        ...

    def check_exit(
        self,
        position: PositionState,
        candle: Candle,
        prior_closes: Sequence[Decimal],
    ) -> Optional[ExitResult]:
        """Evaluate whether `candle` triggers an exit. `position` reflects
        state as of the END of the previous candle (i.e. the protective
        floor has NOT yet been raised by `candle` itself) -- this is what
        prevents a single candle from both raising its own floor and being
        judged against that raised floor. `prior_closes` contains closes
        strictly before `candle` (append candle.close yourself if needed)."""
        ...

    def update_position_state(self, position: PositionState, candle: Candle) -> PositionState:
        """Called after a candle that did NOT trigger an exit, to roll
        forward highest-price/activation/trailing-floor state using that
        candle's high, for use starting the NEXT candle."""
        ...
