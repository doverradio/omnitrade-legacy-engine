from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


ProfitPolicyType = Literal[
    "HOLD_PROFIT",
    "FULL_COMPOUND",
    "PARTIAL_COMPOUND",
    "WITHDRAW_PROFIT",
    "WITHDRAW_AND_COMPOUND",
    "PROTECTED_PRINCIPAL",
    "MANUAL_REVIEW",
]

ProfitCycleStatus = Literal[
    "CALCULATING",
    "BELOW_TARGET",
    "TARGET_REACHED",
    "REVIEW_REQUIRED",
    "APPROVED",
    "COMPOUNDING_RECOMMENDED",
    "WITHDRAWAL_RECOMMENDED",
    "COMPLETED",
    "CANCELLED",
    "ERROR",
]

SettlementState = Literal["SETTLED", "SETTLEMENT_UNKNOWN"]


class CapitalCampaignProfitPolicyUpsertRequest(BaseModel):
    policy_type: ProfitPolicyType
    profit_target_amount: Decimal | None = None
    profit_target_percent: Decimal | None = None
    compound_percent: Decimal = Decimal("0")
    withdraw_percent: Decimal = Decimal("0")
    protected_principal_amount: Decimal | None = None
    minimum_realized_profit: Decimal = Decimal("0")
    maximum_campaign_capital: Decimal | None = None
    minimum_cash_reserve: Decimal = Decimal("0")
    fee_reserve_percent: Decimal = Decimal("0")
    tax_reserve_percent: Decimal = Decimal("0")
    cooldown_hours: int = 0
    require_operator_approval: bool = True
    is_active: bool = True


class CapitalCampaignProfitPolicyResponse(BaseModel):
    policy_id: int
    policy_uuid: UUID
    capital_campaign_id: int
    policy_type: ProfitPolicyType
    profit_target_amount: Decimal | None
    profit_target_percent: Decimal | None
    compound_percent: Decimal
    withdraw_percent: Decimal
    protected_principal_amount: Decimal | None
    minimum_realized_profit: Decimal
    maximum_campaign_capital: Decimal | None
    minimum_cash_reserve: Decimal
    fee_reserve_percent: Decimal
    tax_reserve_percent: Decimal
    cooldown_hours: int
    require_operator_approval: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "profit_target_amount",
        "profit_target_percent",
        "compound_percent",
        "withdraw_percent",
        "protected_principal_amount",
        "minimum_realized_profit",
        "maximum_campaign_capital",
        "minimum_cash_reserve",
        "fee_reserve_percent",
        "tax_reserve_percent",
        when_used="json",
    )
    def _serialize_decimals(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CapitalCampaignProfitCycleEvaluateRequest(BaseModel):
    force_new_cycle: bool = False
    actor: str = "system"


class CapitalCampaignProfitCycleResponse(BaseModel):
    cycle_id: int
    cycle_uuid: UUID
    capital_campaign_id: int
    profit_policy_id: int
    cycle_number: int
    opening_capital: Decimal
    opening_equity: Decimal
    realized_profit: Decimal
    unrealized_profit: Decimal
    fees: Decimal
    eligible_profit: Decimal
    compound_amount: Decimal
    withdrawal_amount: Decimal
    reserve_amount: Decimal
    closing_campaign_capital: Decimal
    target_reached: bool
    status: ProfitCycleStatus
    settlement_state: SettlementState
    calculation_snapshot: dict[str, Any]
    calculated_at: datetime
    approved_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer(
        "opening_capital",
        "opening_equity",
        "realized_profit",
        "unrealized_profit",
        "fees",
        "eligible_profit",
        "compound_amount",
        "withdrawal_amount",
        "reserve_amount",
        "closing_campaign_capital",
        when_used="json",
    )
    def _serialize_decimals(self, value: Decimal) -> str:
        return format(value, "f")


class CapitalCampaignProfitCycleListResponse(BaseModel):
    items: list[CapitalCampaignProfitCycleResponse]


class CapitalCampaignProfitCycleDecisionRequest(BaseModel):
    actor: str = Field(default="operator")
    reason: str | None = None
