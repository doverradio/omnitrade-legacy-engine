from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class AssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    asset_class: str
    exchange: str
    is_active: bool
    supports_fractional: bool
    min_order_notional: Decimal | None
    qty_step_size: Decimal | None

    @field_serializer("min_order_notional", "qty_step_size", when_used="json")
    def serialize_numeric_fields(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class AssetListResponse(BaseModel):
    items: list[AssetResponse]