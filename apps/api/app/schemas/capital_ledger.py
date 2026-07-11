from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_serializer


CapitalPoolStatus = Literal["active", "inactive", "completed", "cancelled"]
CapitalPoolType = Literal[
    "paper_account",
    "validation_run",
    "research_campaign",
    "strategy_allocation",
    "position",
    "compounding_recommendation",
    "withdrawal_recommendation",
    "profit_reserve",
    "policy_review",
]


class CapitalLedgerSummaryResponse(BaseModel):
    total_managed_capital: Decimal
    total_starting_capital: Decimal
    total_current_equity: Decimal
    total_allocated_capital: Decimal
    total_available_capital: Decimal
    total_reserved_capital: Decimal
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal
    active_capital_pools: int
    inactive_capital_pools: int
    active_positions: int
    total_trades: int
    utilization_percent: float
    data_completeness_percent: float
    unavailable_sources: list[str]
    generated_at: datetime

    @field_serializer(
        "total_managed_capital",
        "total_starting_capital",
        "total_current_equity",
        "total_allocated_capital",
        "total_available_capital",
        "total_reserved_capital",
        "total_realized_pnl",
        "total_unrealized_pnl",
        when_used="json",
    )
    def serialize_decimal_fields(self, value: Decimal) -> str:
        return format(value, "f")


class CapitalPoolResponse(BaseModel):
    capital_pool_id: str
    capital_pool_type: CapitalPoolType
    name: str
    status: CapitalPoolStatus
    starting_capital: Decimal | None
    current_equity: Decimal | None
    allocated_capital: Decimal | None
    available_capital: Decimal | None
    reserved_capital: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    pnl_percent: float | None
    started_at: datetime | None
    completed_at: datetime | None
    related_entity_type: str
    related_entity_id: str
    related_page_url: str
    capital_campaign_uuid: str | None = None
    capital_campaign_name: str | None = None
    capital_campaign_status: str | None = None
    parent_capital_pool_id: str | None = None
    child_allocations_count: int
    notes: str | None = None

    @field_serializer(
        "starting_capital",
        "current_equity",
        "allocated_capital",
        "available_capital",
        "reserved_capital",
        "realized_pnl",
        "unrealized_pnl",
        when_used="json",
    )
    def serialize_optional_decimal_fields(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CapitalLedgerResponse(BaseModel):
    summary: CapitalLedgerSummaryResponse
    capital_pools: list[CapitalPoolResponse]
    page: int
    page_size: int
    total: int
    has_more: bool
