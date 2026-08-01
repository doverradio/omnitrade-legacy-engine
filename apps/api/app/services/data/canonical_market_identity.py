"""Pure identities for provider-independent economic instruments and candle slots.

This module is intentionally unused by production. It does not assign canonical
event identity, perform admission, or establish provider-market provenance.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

from app.services.pipeline_contracts.identifiers import InstrumentId


ECONOMIC_SPOT_INSTRUMENT_KEY_VERSION = "economic-spot-instrument-key/v1"
ECONOMIC_CANDLE_SLOT_KEY_VERSION = "economic-candle-slot-key/v1"

# UUIDv5(NAMESPACE_URL, "urn:omnitrade:identity:economic-instrument:v1")
ECONOMIC_INSTRUMENT_NAMESPACE = UUID("5ed07d4d-b4d2-521f-9994-a3b87b4696cc")
# UUIDv5(NAMESPACE_URL, "urn:omnitrade:identity:economic-candle-slot:v1")
ECONOMIC_CANDLE_SLOT_NAMESPACE = UUID("2c9e254d-4719-5b72-b25e-183c7e80d838")

CanonicalAssetCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9]{1,15}$")]
CanonicalInterval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def _canonical_json_bytes(value: dict[str, str]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("candle-slot open_time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class CandleSlotId(BaseModel):
    """Strong identity for one provider-independent economic candle interval."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    value: UUID


class EconomicSpotInstrumentKeyV1(BaseModel):
    """Versioned key for an unleveraged provider-independent crypto spot pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_version: Literal[ECONOMIC_SPOT_INSTRUMENT_KEY_VERSION]
    asset_class: Literal["crypto"]
    base_asset: CanonicalAssetCode
    quote_asset: CanonicalAssetCode
    instrument_kind: Literal["spot"]

    @field_validator("base_asset", "quote_asset", mode="before")
    @classmethod
    def reject_noncanonical_asset_codes(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("canonical asset codes must be strings")
        if value != value.strip() or value != value.upper():
            raise ValueError("canonical asset codes must be uppercase without surrounding whitespace")
        if value in {"XBT", "XXBT"}:
            raise ValueError("provider aliases are not canonical asset codes")
        return value

    @model_validator(mode="after")
    def reject_same_asset_pair(self) -> "EconomicSpotInstrumentKeyV1":
        if self.base_asset == self.quote_asset:
            raise ValueError("spot instrument base and quote assets must differ")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(
            {
                "asset_class": self.asset_class,
                "base_asset": self.base_asset,
                "instrument_kind": self.instrument_kind,
                "key_version": self.key_version,
                "quote_asset": self.quote_asset,
            }
        )


class EconomicCandleSlotKeyV1(BaseModel):
    """Versioned key for a provider-independent economic candle interval."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key_version: Literal[ECONOMIC_CANDLE_SLOT_KEY_VERSION]
    instrument_id: InstrumentId
    interval: CanonicalInterval
    open_time: datetime

    @field_validator("open_time")
    @classmethod
    def require_aware_open_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candle-slot open_time must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("instrument_id")
    @classmethod
    def require_deterministic_instrument_id(cls, value: InstrumentId) -> InstrumentId:
        if value.value.version != 5:
            raise ValueError("candle-slot instrument identity must be a deterministic UUIDv5")
        return value

    def canonical_bytes(self) -> bytes:
        return _canonical_json_bytes(
            {
                "instrument_id": str(self.instrument_id.value),
                "interval": self.interval,
                "key_version": self.key_version,
                "open_time": _utc_text(self.open_time),
            }
        )


def derive_instrument_id(key: EconomicSpotInstrumentKeyV1) -> InstrumentId:
    if not isinstance(key, EconomicSpotInstrumentKeyV1):
        raise TypeError("instrument identity requires EconomicSpotInstrumentKeyV1")
    return InstrumentId(value=uuid5(ECONOMIC_INSTRUMENT_NAMESPACE, key.canonical_bytes().decode("utf-8")))


def derive_candle_slot_id(key: EconomicCandleSlotKeyV1) -> CandleSlotId:
    if not isinstance(key, EconomicCandleSlotKeyV1):
        raise TypeError("candle-slot identity requires EconomicCandleSlotKeyV1")
    return CandleSlotId(value=uuid5(ECONOMIC_CANDLE_SLOT_NAMESPACE, key.canonical_bytes().decode("utf-8")))
