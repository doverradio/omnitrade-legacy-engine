from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


InstantTradeLifecycleState = Literal[
    "VALIDATING",
    "SUBMITTING",
    "PENDING",
    "FILLED",
    "RECONCILIATION_REQUIRED",
    "REJECTED",
    "FAILED",
]


class InstantTradeBuyRequest(BaseModel):
    paper_account_id: UUID
    live_trading_profile_id: UUID
    provider: str = Field(min_length=1, max_length=64)
    environment: Literal["production", "sandbox"]
    product: str = Field(min_length=3, max_length=32)
    quote_amount: Decimal
    actor: str = Field(min_length=1, max_length=120)
    confirmation: bool
    idempotency_key: str = Field(min_length=1, max_length=160)

    @field_serializer("quote_amount", when_used="json")
    def _serialize_quote_amount(self, value: Decimal) -> str:
        return format(value, "f")


class InstantTradeAdoptRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)


class InstantTradeReceiptResponse(BaseModel):
    internal_order_id: UUID
    provider_order_id: str | None
    status: InstantTradeLifecycleState
    requested_amount: Decimal
    executed_quantity: str | None
    average_fill_price: str | None
    fees: dict[str, str]
    created_at: datetime
    submitted_at: datetime | None
    acknowledged_at: datetime | None
    filled_at: datetime | None
    updated_at: datetime
    reconciliation_state: str | None
    order: dict[str, Any]

    @field_serializer("requested_amount", when_used="json")
    def _serialize_requested_amount(self, value: Decimal) -> str:
        return format(value, "f")
