from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


CampaignStatus = Literal[
    "DRAFT",
    "READY",
    "ACTIVE",
    "PAUSED",
    "CAPITAL_EXHAUSTED",
    "COMPLETED",
    "CANCELED",
    "MANUAL_REVIEW_REQUIRED",
]

CampaignMode = Literal[
    "PROFIT_TARGET",
    "COMPOUND",
    "CAPITAL_PRESERVATION",
    "TIME_BOUND",
    "OPPORTUNITY_SEEKING",
]

AggressionMode = Literal[
    "CONSERVATIVE",
    "BALANCED",
    "AGGRESSIVE",
    "MAXIMUM_GOVERNED",
]

CompoundingPolicyType = Literal[
    "REINVEST_ALL_NET_PROFIT",
    "REINVEST_PERCENTAGE",
    "RETAIN_PRINCIPAL_DISTRIBUTE_PROFIT",
    "FIXED_CAPITAL",
    "STOP_AT_PROFIT_TARGET",
]

RiskPreviewVerdict = Literal["ALLOW", "VETO"]
EvidenceAuthorityClass = Literal["SIMULATED", "OPERATOR_SUPPLIED"]


class CampaignCompoundingPolicy(BaseModel):
    policy_type: CompoundingPolicyType
    reinvestment_percentage: Decimal = Decimal("0")
    profit_distribution_percentage: Decimal = Decimal("0")
    reserve_percentage: Decimal = Decimal("0")
    cumulative_profit_target: Decimal | None = None
    maximum_campaign_loss: Decimal | None = None
    campaign_end_date: datetime | None = None

    @field_serializer(
        "reinvestment_percentage",
        "profit_distribution_percentage",
        "reserve_percentage",
        "cumulative_profit_target",
        "maximum_campaign_loss",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CampaignProfitDistributionPolicy(BaseModel):
    reinvestment_percentage: Decimal = Decimal("0")
    profit_distribution_percentage: Decimal = Decimal("0")
    reserve_percentage: Decimal = Decimal("0")

    @field_serializer(
        "reinvestment_percentage",
        "profit_distribution_percentage",
        "reserve_percentage",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class CampaignAccountingState(BaseModel):
    initial_capital: Decimal = Decimal("0")
    allocated_capital: Decimal = Decimal("0")
    reserved_capital: Decimal = Decimal("0")
    deployed_capital: Decimal = Decimal("0")
    realized_gross_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    realized_net_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    distributable_profit: Decimal = Decimal("0")
    compounded_profit: Decimal = Decimal("0")
    withdrawn_profit: Decimal = Decimal("0")
    current_campaign_equity: Decimal = Decimal("0")
    maximum_drawdown: Decimal = Decimal("0")
    available_capital: Decimal = Decimal("0")

    @field_serializer(
        "initial_capital",
        "allocated_capital",
        "reserved_capital",
        "deployed_capital",
        "realized_gross_pnl",
        "fees",
        "realized_net_pnl",
        "unrealized_pnl",
        "distributable_profit",
        "compounded_profit",
        "withdrawn_profit",
        "current_campaign_equity",
        "maximum_drawdown",
        "available_capital",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class CapitalCampaignDraftCreateRequest(BaseModel):
    campaign_id: UUID | None = None
    name: str
    description: str | None = None
    owner_identity: str
    status: CampaignStatus = "DRAFT"
    capital_budget: Decimal
    remaining_unallocated_capital: Decimal | None = None
    base_currency: str = "USD"
    allowed_asset_classes: list[str] = Field(default_factory=list)
    allowed_venues: list[str] = Field(default_factory=list)
    allowed_instruments: list[str] = Field(default_factory=list)
    campaign_modes: list[CampaignMode] = Field(default_factory=list)
    maximum_open_positions: int
    maximum_position_size: Decimal
    minimum_position_size: Decimal
    maximum_total_exposure: Decimal
    profitability_policy_id: str
    profitability_policy_version: str
    risk_policy_id: str
    risk_policy_version: str
    compounding_policy: CampaignCompoundingPolicy
    profit_distribution_policy: CampaignProfitDistributionPolicy
    aggression_mode: AggressionMode = "BALANCED"
    activated_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    metadata_evidence: dict[str, Any] = Field(default_factory=dict)
    accounting_state: CampaignAccountingState | None = None
    non_live_only: bool = True


class CapitalCampaignDefinitionResponse(BaseModel):
    campaign_id: UUID
    version: int
    runtime_campaign_uuid: UUID
    runtime_definition_version: int
    name: str
    description: str | None = None
    owner_identity: str
    status: CampaignStatus
    capital_budget: Decimal
    remaining_unallocated_capital: Decimal
    base_currency: str
    allowed_asset_classes: list[str]
    allowed_venues: list[str]
    allowed_instruments: list[str]
    campaign_modes: list[CampaignMode]
    maximum_open_positions: int
    maximum_position_size: Decimal
    minimum_position_size: Decimal
    maximum_total_exposure: Decimal
    profitability_policy_id: str
    profitability_policy_version: str
    risk_policy_id: str
    risk_policy_version: str
    compounding_policy: CampaignCompoundingPolicy
    profit_distribution_policy: CampaignProfitDistributionPolicy
    aggression_mode: AggressionMode
    accounting_state: CampaignAccountingState
    created_at: datetime
    activated_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    metadata_evidence: dict[str, Any]

    @field_serializer(
        "capital_budget",
        "remaining_unallocated_capital",
        "maximum_position_size",
        "minimum_position_size",
        "maximum_total_exposure",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class CapitalCampaignDefinitionListResponse(BaseModel):
    items: list[CapitalCampaignDefinitionResponse]


class StrategyEvidenceInput(BaseModel):
    instrument: str
    authority_class: EvidenceAuthorityClass
    confidence: Decimal
    expected_gross_edge: Decimal
    expected_fees: Decimal
    expected_slippage: Decimal

    @field_serializer("confidence", "expected_gross_edge", "expected_fees", "expected_slippage", when_used="json")
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class LifecycleEvidenceInput(BaseModel):
    instrument: str
    authority_class: EvidenceAuthorityClass
    lifecycle_state: str
    recommendation: str
    market_data_stale: bool = False
    dust_indicator: bool = False
    closed_indicator: bool = False
    expected_net_realized_pnl_if_sold_now: Decimal | None = None

    @field_serializer("expected_net_realized_pnl_if_sold_now", when_used="json")
    def _serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class RiskPreviewInput(BaseModel):
    instrument: str
    authority_class: EvidenceAuthorityClass
    verdict: RiskPreviewVerdict
    reason: str | None = None
    max_allocation: Decimal | None = None

    @field_serializer("max_allocation", when_used="json")
    def _serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CapitalCampaignPreviewRequest(BaseModel):
    candidate_instruments: list[str] = Field(default_factory=list)
    strategy_evidence: list[StrategyEvidenceInput] = Field(default_factory=list)
    lifecycle_snapshots: list[LifecycleEvidenceInput] = Field(default_factory=list)
    risk_preview: list[RiskPreviewInput] = Field(default_factory=list)
    available_capital_override: Decimal | None = None

    @field_serializer("available_capital_override", when_used="json")
    def _serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CampaignPreviewOpportunity(BaseModel):
    instrument: str
    proposed_allocation: Decimal
    expected_fees: Decimal
    expected_slippage: Decimal
    expected_net_edge: Decimal
    confidence: Decimal
    reason: str

    @field_serializer(
        "proposed_allocation",
        "expected_fees",
        "expected_slippage",
        "expected_net_edge",
        "confidence",
        when_used="json",
    )
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class CampaignPreviewRejection(BaseModel):
    instrument: str
    reason: str


class CapitalCampaignPreviewResponse(BaseModel):
    campaign_id: UUID
    campaign_version: int
    aggression_mode: AggressionMode
    no_action: bool
    no_action_reason: str | None = None
    proposed_opportunities: list[CampaignPreviewOpportunity] = Field(default_factory=list)
    rejected_opportunities: list[CampaignPreviewRejection] = Field(default_factory=list)
    remaining_cash: Decimal
    expected_fees: Decimal
    expected_slippage: Decimal
    expected_net_edge: Decimal
    campaign_policy_checks: list[str] = Field(default_factory=list)
    risk_checks: list[str] = Field(default_factory=list)
    decision_evidence: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime

    @field_serializer("remaining_cash", "expected_fees", "expected_slippage", "expected_net_edge", when_used="json")
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")
