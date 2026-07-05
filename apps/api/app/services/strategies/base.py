from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _freeze_mapping(value: dict[str, Any] | None) -> MappingProxyType[str, Any] | None:
    if value is None:
        return None
    return MappingProxyType(dict(value))


def _freeze_candles(candles: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> tuple[MappingProxyType[str, Any], ...]:
    return tuple(MappingProxyType(dict(candle)) for candle in candles)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    candles: tuple[MappingProxyType[str, Any], ...]
    asset_metadata: MappingProxyType[str, Any]
    interval: str
    current_position: MappingProxyType[str, Any] | None
    strategy_parameters: MappingProxyType[str, Any]

    def __init__(
        self,
        *,
        candles: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        asset_metadata: dict[str, Any],
        interval: str,
        current_position: dict[str, Any] | None,
        strategy_parameters: dict[str, Any],
    ) -> None:
        object.__setattr__(self, "candles", _freeze_candles(candles))
        object.__setattr__(self, "asset_metadata", MappingProxyType(dict(asset_metadata)))
        object.__setattr__(self, "interval", interval)
        object.__setattr__(self, "current_position", _freeze_mapping(current_position))
        object.__setattr__(self, "strategy_parameters", MappingProxyType(dict(strategy_parameters)))


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["buy", "sell", "hold"]
    strength: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    reason: str
    indicators: dict[str, Any]
    timestamp: datetime

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Signal reason must be a non-empty human-readable explanation.")
        return normalized

    @field_validator("indicators")
    @classmethod
    def validate_indicators(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value is None:
            raise ValueError("Signal indicators are required.")
        return value


@runtime_checkable
class Strategy(Protocol):
    slug: str
    default_params: dict[str, Any]

    def generate_signal(
        self,
        candles: Any,
        params: dict[str, Any],
        context: StrategyContext,
    ) -> Signal:
        ...