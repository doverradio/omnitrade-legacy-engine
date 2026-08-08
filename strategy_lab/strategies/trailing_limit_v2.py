"""Strategy #002: Strategy #001 with post-entry declining-close timing."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from ..candles import Candle
from ..strategy import ExitReason, ExitResult, PositionState
from .trailing_limit_v1 import TrailingLimitV1Strategy


class TrailingLimitV2Strategy(TrailingLimitV1Strategy):
    def check_exit(
        self,
        position: PositionState,
        candle: Candle,
        prior_closes: Sequence[Decimal],
    ) -> Optional[ExitResult]:
        if candle.low <= position.trailing_floor:
            exit_price = min(candle.open, position.trailing_floor)
            return ExitResult(exit_price=exit_price, reason=ExitReason.TRAILING_FLOOR_BREACHED)

        post_entry_closes = list(prior_closes[position.entry_candle_index :]) + [candle.close]
        n = self.config.required_declining_candles
        window = post_entry_closes[-(n + 1) :]
        if len(window) == n + 1 and all(window[i + 1] < window[i] for i in range(n)):
            return ExitResult(exit_price=candle.close, reason=ExitReason.DECLINING_CLOSES)

        return None