from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
import uuid

from pydantic import BaseModel, field_serializer


CapitalCampaignStatus = Literal[
    "DRAFT",
    "READY",
    "RUNNING",
    "PAUSED",
    "TARGET_REACHED",
    "COMPLETED",
    "ARCHIVED",
]


class CapitalCampaignCreateRequest(BaseModel):
    owner: str
    name: str
    description: str | None = None
    status: CapitalCampaignStatus = "DRAFT"
    campaign_type: str
    exchange: str | None = None
    paper_account_id: uuid.UUID | None = None
    validation_run_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None
    starting_capital: Decimal
    current_equity: Decimal | None = None
    realized_profit: Decimal = Decimal("0")
    unrealized_profit: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")


class CapitalCampaignUpdateRequest(BaseModel):
    owner: str | None = None
    name: str | None = None
    description: str | None = None
    status: CapitalCampaignStatus | None = None
    campaign_type: str | None = None
    exchange: str | None = None
    paper_account_id: uuid.UUID | None = None
    validation_run_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None
    starting_capital: Decimal | None = None
    current_equity: Decimal | None = None
    realized_profit: Decimal | None = None
    unrealized_profit: Decimal | None = None
    fees: Decimal | None = None


class CapitalCampaignResponse(BaseModel):
    id: int
    uuid: uuid.UUID
    owner: str
    name: str
    description: str | None
    status: CapitalCampaignStatus
    campaign_type: str
    exchange: str | None
    paper_account_id: uuid.UUID | None
    validation_run_id: uuid.UUID | None
    strategy_id: uuid.UUID | None
    starting_capital: Decimal
    current_equity: Decimal
    realized_profit: Decimal
    unrealized_profit: Decimal
    fees: Decimal
    roi: Decimal
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "starting_capital",
        "current_equity",
        "realized_profit",
        "unrealized_profit",
        "fees",
        "roi",
        when_used="json",
    )
    def serialize_decimal_fields(self, value: Decimal) -> str:
        return format(value, "f")


class CapitalCampaignListResponse(BaseModel):
    items: list[CapitalCampaignResponse]


class CapitalCampaignDeleteResponse(BaseModel):
    campaign_uuid: uuid.UUID
    deleted: bool
