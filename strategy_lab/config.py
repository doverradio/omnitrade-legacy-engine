"""All tunable simulation parameters in one place.

Every knob mentioned in the Strategy Laboratory V1 spec (Entry Offset,
Initial Stop, Profit Activation, Trailing Distance, Required Declining
Candles, Fee Model, Slippage Model, Candle Interval) lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SimulationConfig:
    # ENTRY MODE: BUY LIMIT = latest completed candle close * (1 - entry_offset_pct)
    entry_offset_pct: Decimal = Decimal("0.01")

    # POSITION MODE
    initial_stop_pct: Decimal = Decimal("0.01")       # initial stop = fill * (1 - initial_stop_pct)
    profit_activation_pct: Decimal = Decimal("0.03")  # activation = fill * (1 + profit_activation_pct)
    trailing_distance_pct: Decimal = Decimal("0.01")  # floor = highest_since_entry * (1 - trailing_distance_pct)
    required_declining_candles: int = 2               # consecutive strictly-declining closes to exit

    # Cost model
    fee_pct: Decimal = Decimal("0.001")       # charged on notional at entry and exit
    slippage_pct: Decimal = Decimal("0.0005")  # adverse price adjustment applied at fill

    # Reporting / compounding
    initial_capital: Decimal = Decimal("100")
    candle_interval: str = "1h"

    # Intra-candle ambiguity: when a candle's OHLC range makes it impossible
    # to know which of two mutually-exclusive events (e.g. a fill and a stop
    # breach, or a favorable trailing update and an adverse breach) happened
    # first, this policy decides how the engine resolves it. "pessimistic"
    # (the default, and the only policy used for the authoritative Strategy
    # #001 report) always assumes the adverse outcome. "optimistic" is the
    # original V1 behavior: never evaluate an exit on the same candle an
    # entry filled, and let a candle's high raise the trailing floor before
    # that same candle's low is checked against it. See engine.py's module
    # docstring for the exact mechanics.
    intra_candle_ambiguity_policy: str = "pessimistic"

    def __post_init__(self) -> None:
        if self.intra_candle_ambiguity_policy not in ("pessimistic", "optimistic"):
            raise ValueError(
                "intra_candle_ambiguity_policy must be 'pessimistic' or 'optimistic', "
                f"got {self.intra_candle_ambiguity_policy!r}"
            )
        non_negative = {
            "entry_offset_pct": self.entry_offset_pct,
            "initial_stop_pct": self.initial_stop_pct,
            "profit_activation_pct": self.profit_activation_pct,
            "trailing_distance_pct": self.trailing_distance_pct,
            "fee_pct": self.fee_pct,
            "slippage_pct": self.slippage_pct,
        }
        for name, value in non_negative.items():
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if self.entry_offset_pct >= 1:
            raise ValueError("entry_offset_pct must be < 1 (it is a discount off close)")
        if self.required_declining_candles < 2:
            raise ValueError("required_declining_candles must be >= 2")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be > 0")
