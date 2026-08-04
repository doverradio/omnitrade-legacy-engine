"""Historical candle loading.

A Candle is the atomic unit of information the simulator is allowed to see.
Everything downstream must only use data available at, or before, a given
candle's close -- never a candle's own future or a later candle's data.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import List, Sequence, Union


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"candle at {self.timestamp}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"candle at {self.timestamp}: open {self.open} outside [{self.low}, {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"candle at {self.timestamp}: close {self.close} outside [{self.low}, {self.high}]")


def _parse_timestamp(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_candles_csv(path: Union[str, Path]) -> List[Candle]:
    """Load candles from a CSV with header: timestamp,open,high,low,close,volume

    Rows must be in strictly increasing timestamp order -- this is enforced
    to catch accidental shuffling that would otherwise introduce look-ahead
    bias (or hindsight bias) further down the pipeline.
    """
    candles: List[Candle] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"candle CSV missing required columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                candle = Candle(
                    timestamp=_parse_timestamp(row["timestamp"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                )
            except Exception as exc:  # noqa: BLE001 - re-raised with location context
                raise ValueError(f"candle CSV row {line_number} invalid: {exc}") from exc
            if candles and candle.timestamp <= candles[-1].timestamp:
                raise ValueError(
                    f"candle CSV row {line_number} out of order: "
                    f"{candle.timestamp} is not after {candles[-1].timestamp}"
                )
            candles.append(candle)
    return candles


def validate_no_duplicate_timestamps(candles: Sequence[Candle]) -> None:
    seen = set()
    for candle in candles:
        if candle.timestamp in seen:
            raise ValueError(f"duplicate candle timestamp: {candle.timestamp}")
        seen.add(candle.timestamp)
