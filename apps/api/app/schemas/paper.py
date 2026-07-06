from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class PositionResponse(BaseModel):
    asset_id: uuid.UUID
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    unrealized_pnl_usd: Decimal
    unrealized_pnl_pct: Decimal

    @field_serializer(
        "quantity",
        "avg_entry_price",
        "unrealized_pnl_usd",
        "unrealized_pnl_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class PaperAccountResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    name: str
    asset_class: str
    starting_balance: Decimal
    current_cash_balance: Decimal
    equity: Decimal
    equity_return_usd: Decimal
    equity_return_pct: Decimal
    positions: list[PositionResponse]

    @field_serializer(
        "starting_balance",
        "current_cash_balance",
        "equity",
        "equity_return_usd",
        "equity_return_pct",
        when_used="json",
    )
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class CreatePaperAccountRequest(BaseModel):
    name: str
    asset_class: str
    starting_balance: Decimal


class CreatePaperAccountResponse(BaseModel):
    id: uuid.UUID
    name: str
    asset_class: str
    starting_balance: Decimal
    current_cash_balance: Decimal
    is_active: bool

    @field_serializer("starting_balance", "current_cash_balance", when_used="json")
    def serialize_numeric_fields(self, value: Decimal) -> str:
        return format(value, "f")


class ResetPaperAccountRequest(BaseModel):
    account_id: uuid.UUID
    confirm: bool


class ResetPaperAccountResponse(BaseModel):
    account_id: uuid.UUID
    current_cash_balance: Decimal
    positions: list[PositionResponse]

    @field_serializer("current_cash_balance", when_used="json")
    def serialize_numeric_field(self, value: Decimal) -> str:
        return format(value, "f")
