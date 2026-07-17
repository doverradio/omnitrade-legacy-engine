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

CommissionedCampaignState = Literal[
    "DRAFT",
    "READY",
    "COMMISSIONED",
    "BUY_PENDING",
    "BUY_SUBMITTED",
    "BUY_RECONCILIATION_PENDING",
    "ACTIVE_POSITION",
    "SELL_EVALUATION",
    "SELL_PENDING",
    "SELL_SUBMITTED",
    "SELL_RECONCILIATION_PENDING",
    "COMPLETED",
    "VETOED",
    "EXPIRED",
    "RECONCILIATION_REQUIRED",
    "MANUAL_REVIEW_REQUIRED",
    "FAILED_CLOSED",
    "CANCELLED",
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

RiskPreviewVerdict = Literal["ALLOW", "REDUCE", "VETO"]
EvidenceAuthorityClass = Literal["SIMULATED", "OPERATOR_SUPPLIED", "AUTHORITATIVE", "UNAVAILABLE", "STALE"]


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


class CommissionedCampaignAuthorityMetadata(BaseModel):
    campaign_type: str = "COMMISSIONED_AUTONOMOUS_SEED"
    entry_authority: str = "OPERATOR_COMMISSIONED"
    lifecycle_authority: str = "OMNITRADE_AUTONOMOUS"
    maximum_entry_notional: Decimal
    repeat_entry_allowed: bool = False
    commissioned_by: str
    commissioned_at: datetime | None = None

    @field_serializer("maximum_entry_notional", when_used="json")
    def _serialize_maximum_entry_notional(self, value: Decimal) -> str:
        return format(value, "f")


class CommissionedCampaignEvidenceMetadata(BaseModel):
    evidence_code: str
    source: str
    observed_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class CommissionedCampaignTransitionRequest(BaseModel):
    target_state: CommissionedCampaignState
    actor: str
    reason: str
    idempotency_key: str | None = None
    expected_current_state: CommissionedCampaignState | None = None
    authority_metadata: CommissionedCampaignAuthorityMetadata | None = None
    evidence_metadata: list[CommissionedCampaignEvidenceMetadata] = Field(default_factory=list)


class CommissionedCampaignTransitionResponse(BaseModel):
    campaign_id: UUID
    version: int
    previous_state: CommissionedCampaignState
    current_state: CommissionedCampaignState
    replayed: bool
    transition_count: int
    metadata_evidence: dict[str, Any]


class CommissionedReadinessRequest(BaseModel):
    campaign_id: UUID
    version: int
    provider: str
    environment: str
    instrument: str
    requested_quote_amount: Decimal
    quote_currency: str = "USD"
    idempotency_key: str | None = None
    live_trading_profile_id: UUID | None = None
    account_id: UUID | None = None
    mandate_id: UUID | None = None
    mandate_version_id: UUID | None = None
    expected_mandate_version_number: int | None = None
    expected_risk_policy_id: str | None = None
    expected_risk_policy_version: str | None = None
    approval_checkpoint_type: str = "first_live_enablement"
    authorization_expires_at: datetime | None = None
    provider_capability_evidence: dict[str, Any] = Field(default_factory=dict)
    connectivity_evidence: dict[str, Any] = Field(default_factory=dict)
    balance_evidence: dict[str, Any] = Field(default_factory=dict)
    market_data_evidence: dict[str, Any] = Field(default_factory=dict)
    price_evidence: dict[str, Any] = Field(default_factory=dict)
    minimum_order_evidence: dict[str, Any] = Field(default_factory=dict)
    fee_slippage_evidence: dict[str, Any] = Field(default_factory=dict)
    runtime_readiness_evidence: dict[str, Any] = Field(default_factory=dict)
    reconciliation_evidence: dict[str, Any] = Field(default_factory=dict)
    manual_review_evidence: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("requested_quote_amount", when_used="json")
    def _serialize_requested_quote_amount(self, value: Decimal) -> str:
        return format(value, "f")


class CommissionedReadinessResponse(BaseModel):
    campaign_id: UUID
    version: int
    readiness_verdict: Literal["READY", "BLOCKED"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    authority_classification: str
    strategy_signal_classification: str
    commissioned_state: CommissionedCampaignState | None = None
    expected_entry_quantity: Decimal | None = None
    applicable_capital_cap: Decimal | None = None
    estimated_entry_fee: Decimal | None = None
    estimated_future_exit_fee: Decimal | None = None
    estimated_slippage: Decimal | None = None
    evidence_timestamps: dict[str, datetime | None] = Field(default_factory=dict)
    evidence_provenance: dict[str, str] = Field(default_factory=dict)
    stale_after: datetime | None = None

    @field_serializer(
        "expected_entry_quantity",
        "applicable_capital_cap",
        "estimated_entry_fee",
        "estimated_future_exit_fee",
        "estimated_slippage",
        when_used="json",
    )
    def _serialize_optional_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CommissionedPreviewResponse(BaseModel):
    campaign_id: UUID
    version: int
    authority_classification: str
    strategy_signal_classification: str
    execution_venue: dict[str, str]
    instrument: str
    proposed_quote_amount: Decimal
    estimated_base_quantity: Decimal | None = None
    reference_price: Decimal | None = None
    reference_price_timestamp: datetime | None = None
    estimated_entry_fee: Decimal | None = None
    estimated_future_exit_fee: Decimal | None = None
    estimated_slippage: Decimal | None = None
    total_estimated_round_trip_costs: Decimal | None = None
    applicable_capital_cap: Decimal | None = None
    mandate_identity: dict[str, Any] = Field(default_factory=dict)
    risk_policy_identity: dict[str, Any] = Field(default_factory=dict)
    readiness_verdict: Literal["READY", "BLOCKED"]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_timestamps: dict[str, datetime | None] = Field(default_factory=dict)
    evidence_provenance: dict[str, str] = Field(default_factory=dict)
    preview_identity_hash: str
    stale_after: datetime | None = None
    no_database_writes: bool = True
    no_order_submission: bool = True
    no_position_creation: bool = True

    @field_serializer(
        "proposed_quote_amount",
        "estimated_base_quantity",
        "reference_price",
        "estimated_entry_fee",
        "estimated_future_exit_fee",
        "estimated_slippage",
        "total_estimated_round_trip_costs",
        "applicable_capital_cap",
        when_used="json",
    )
    def _serialize_preview_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CommissionedCampaignCommissionRequest(BaseModel):
    campaign_id: UUID
    version: int
    actor: str
    commissioning_reason: str
    preview_identity_hash: str
    requested_quote_amount: Decimal
    idempotency_key: str
    authorization_expires_at: datetime
    commissioned_until: datetime
    readiness_request: CommissionedReadinessRequest

    @field_serializer("requested_quote_amount", when_used="json")
    def _serialize_commission_requested_quote_amount(self, value: Decimal) -> str:
        return format(value, "f")


class CommissionedCampaignCommissionResponse(BaseModel):
    campaign_id: UUID
    version: int
    previous_state: CommissionedCampaignState
    current_state: CommissionedCampaignState
    replayed: bool
    commissioning_identity: str
    preview_identity_hash: str
    authority_classification: str
    strategy_signal_classification: str
    commissioned_until: datetime
    blockers: list[str] = Field(default_factory=list)


class CommissionedEntryExecutionRequest(BaseModel):
    campaign_id: UUID
    version: int
    actor: str
    idempotency_key: str
    readiness_request: CommissionedReadinessRequest
    expected_preview_identity_hash: str
    live_crypto_order_id: UUID
    confirmation_challenge_id: UUID
    confirmation_phrase: str
    submit_idempotency_token: str
    risk_signal_id: UUID
    paper_account_id: UUID
    asset_id: UUID
    requested_base_quantity: Decimal
    reference_price: Decimal
    account_equity: Decimal
    max_position_size_pct: Decimal
    min_order_notional: Decimal | None = None
    qty_step_size: Decimal | None = None
    supports_fractional: bool | None = None

    @field_serializer(
        "requested_base_quantity",
        "reference_price",
        "account_equity",
        "max_position_size_pct",
        "min_order_notional",
        "qty_step_size",
        when_used="json",
    )
    def _serialize_commissioned_entry_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CommissionedEntryExecutionResponse(BaseModel):
    campaign_id: UUID
    version: int
    previous_state: CommissionedCampaignState
    current_state: CommissionedCampaignState
    replayed: bool
    vetoed: bool
    risk_event_id: UUID | None = None
    risk_action: str
    decision_record_id: UUID | None = None
    live_crypto_order_id: UUID | None = None
    provider_order_id: str | None = None
    provider_submission_classification: str
    commissioning_identity: str
    economic_idempotency_key: str
    authority_classification: str
    strategy_signal_classification: str
    no_position_ownership_created: bool = True
    blockers: list[str] = Field(default_factory=list)


class CommissionedOwnershipReconciliationRequest(BaseModel):
    campaign_id: UUID
    version: int
    actor: str
    idempotency_key: str
    live_crypto_order_id: UUID | None = None


class CommissionedOwnershipReconciliationResponse(BaseModel):
    campaign_id: UUID
    version: int
    previous_state: CommissionedCampaignState
    current_state: CommissionedCampaignState
    replayed: bool
    ownership_proven: bool
    position_identity: str | None = None
    provider_order_id: str | None = None
    provider_fill_ids: list[str] = Field(default_factory=list)
    executed_quantity: Decimal | None = None
    average_entry_price: Decimal | None = None
    total_buy_fees: Decimal | None = None
    attributable_remaining_quantity: Decimal | None = None
    evidence_timestamps: dict[str, datetime | None] = Field(default_factory=dict)
    correlation_ids: dict[str, str | None] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)

    @field_serializer(
        "executed_quantity",
        "average_entry_price",
        "total_buy_fees",
        "attributable_remaining_quantity",
        when_used="json",
    )
    def _serialize_optional_ownership_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CommissionedExitRecommendationRequest(BaseModel):
    campaign_id: UUID
    version: int
    actor: str
    idempotency_key: str
    risk_signal_id: UUID
    paper_account_id: UUID
    asset_id: UUID
    account_equity: Decimal
    max_position_size_pct: Decimal
    min_order_notional: Decimal | None = None
    qty_step_size: Decimal | None = None
    supports_fractional: bool | None = None

    @field_serializer(
        "account_equity",
        "max_position_size_pct",
        "min_order_notional",
        "qty_step_size",
        when_used="json",
    )
    def _serialize_exit_request_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CommissionedExitRecommendationResponse(BaseModel):
    campaign_id: UUID
    version: int
    replayed: bool
    recommendation_type: Literal["HOLD", "SELL_NOW", "STOP_LOSS_EXIT", "MAX_HOLD_EXIT"]
    recommendation_reason: str
    policy_id: str | None = None
    policy_version: str | None = None
    lifecycle_state: str | None = None
    confidence: Decimal
    evidence: dict[str, Any] = Field(default_factory=dict)
    profitability_evidence: dict[str, Any] = Field(default_factory=dict)
    expected_fees: Decimal | None = None
    estimated_slippage: Decimal | None = None
    expected_net_result: Decimal | None = None
    risk_action: str
    risk_event_id: UUID | None = None
    decision_record_id: UUID | None = None
    timestamps: dict[str, datetime | None] = Field(default_factory=dict)
    correlation_identifiers: dict[str, str | None] = Field(default_factory=dict)
    no_sell_submitted: bool = True
    blockers: list[str] = Field(default_factory=list)

    @field_serializer(
        "confidence",
        "expected_fees",
        "estimated_slippage",
        "expected_net_result",
        when_used="json",
    )
    def _serialize_exit_response_decimal(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CommissionedControlPlaneStatusResponse(BaseModel):
    campaign_id: UUID
    version: int
    state: CommissionedCampaignState
    readiness: dict[str, Any] = Field(default_factory=dict)
    preview: dict[str, Any] = Field(default_factory=dict)
    commissioning_status: dict[str, Any] = Field(default_factory=dict)
    lifecycle_recommendation: dict[str, Any] = Field(default_factory=dict)
    active_position_summary: dict[str, Any] = Field(default_factory=dict)
    reconciliation_status: dict[str, Any] = Field(default_factory=dict)
    decision_record_summary: dict[str, Any] = Field(default_factory=dict)
    risk_engine_summary: dict[str, Any] = Field(default_factory=dict)
    audit_summary: dict[str, Any] = Field(default_factory=dict)
    pending_operator_actions: list[str] = Field(default_factory=list)
    campaign_timeline: list[dict[str, Any]] = Field(default_factory=list)
    campaign_history: dict[str, Any] = Field(default_factory=dict)
    dry_run_status: dict[str, Any] = Field(default_factory=dict)
    future_production_activation_eligibility: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    read_only: bool = True
    no_execution: bool = True
    generated_at: datetime


class CommissionedControlPlaneMutationRequest(BaseModel):
    campaign_id: UUID
    version: int
    actor: str
    action: Literal["acknowledge", "cancel", "pause", "resume"]
    idempotency_key: str
    reason: str | None = None


class CommissionedControlPlaneMutationResponse(BaseModel):
    campaign_id: UUID
    version: int
    action: Literal["acknowledge", "cancel", "pause", "resume"]
    accepted: bool
    replayed: bool
    state: CommissionedCampaignState
    operator_control: dict[str, Any] = Field(default_factory=dict)
    pending_operator_actions: list[str] = Field(default_factory=list)
    no_execution: bool = True
    updated_at: datetime
    blockers: list[str] = Field(default_factory=list)
