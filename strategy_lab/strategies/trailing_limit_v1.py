"""Strategy Laboratory Version 1: continuously-replaced BUY LIMIT entry,
initial-stop + profit-activation + trailing-floor exit, plus a
declining-closes momentum exit.

ENTRY MODE: BUY LIMIT = latest completed candle close * (1 - entry_offset_pct),
recalculated and replaced every completed candle until filled.

POSITION MODE:
  - Initial Stop = fill_price * (1 - initial_stop_pct)
  - Profit Mode Activation = fill_price * (1 + profit_activation_pct)
  - Trailing disabled until Highest Price Since Entry reaches the
    activation price; once active, Trailing Floor tracks
    Highest Price Since Entry * (1 - trailing_distance_pct) and never
    decreases.
  - Exit if the floor is breached, OR if `required_declining_candles`
    consecutive completed candle closes are each lower than the one before.

See engine.py's module docstring for the exact intra-candle ordering used
to resolve ambiguity (this class only decides WHAT the conditions are, not
WHEN within a candle they get checked relative to each other).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence

from ..candles import Candle
from ..config import SimulationConfig
from ..strategy import ExitReason, ExitResult, FillResult, PositionState

_ONE = Decimal("1")


@dataclass(frozen=True)
class TrailingLimitV1Strategy:
    config: SimulationConfig

    def propose_entry_price(self, closed_candles: Sequence[Candle]) -> Decimal:
        latest_close = closed_candles[-1].close
        return latest_close * (_ONE - self.config.entry_offset_pct)

    def open_position(self, fill: FillResult, closed_candles: Sequence[Candle]) -> PositionState:
        fill_price = fill.fill_price
        initial_stop = fill_price * (_ONE - self.config.initial_stop_pct)
        profit_activation_price = fill_price * (_ONE + self.config.profit_activation_pct)
        return PositionState(
            fill_price=fill_price,
            entry_candle_index=fill.candle_index,
            initial_stop=initial_stop,
            profit_activation_price=profit_activation_price,
            trailing_distance_pct=self.config.trailing_distance_pct,
            trailing_active=False,
            highest_price_since_entry=fill_price,
            trailing_floor=initial_stop,
        )

    def check_exit(
        self,
        position: PositionState,
        candle: Candle,
        prior_closes: Sequence[Decimal],
    ) -> Optional[ExitResult]:
        if candle.low <= position.trailing_floor:
            exit_price = min(candle.open, position.trailing_floor)
            return ExitResult(exit_price=exit_price, reason=ExitReason.TRAILING_FLOOR_BREACHED)

        n = self.config.required_declining_candles
        window = list(prior_closes[-n:]) + [candle.close]
        if len(window) == n + 1 and all(window[i + 1] < window[i] for i in range(len(window) - 1)):
            return ExitResult(exit_price=candle.close, reason=ExitReason.DECLINING_CLOSES)

        return None

    def update_position_state(self, position: PositionState, candle: Candle) -> PositionState:
        highest = max(position.highest_price_since_entry, candle.high)
        trailing_active = position.trailing_active or highest >= position.profit_activation_price
        trailing_floor = position.trailing_floor
        if trailing_active:
            candidate_floor = highest * (_ONE - position.trailing_distance_pct)
            trailing_floor = max(trailing_floor, candidate_floor)
        return PositionState(
            fill_price=position.fill_price,
            entry_candle_index=position.entry_candle_index,
            initial_stop=position.initial_stop,
            profit_activation_price=position.profit_activation_price,
            trailing_distance_pct=position.trailing_distance_pct,
            trailing_active=trailing_active,
            highest_price_since_entry=highest,
            trailing_floor=trailing_floor,
        )
