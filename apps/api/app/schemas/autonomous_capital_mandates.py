from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import uuid
from typing import Any

from pydantic import BaseModel, Field


class AutonomousCapitalMandateCreateRequest(BaseModel):
    owner_actor_id: str
    autonomy_level: str
    provider: str
    exchange_environment: str
    exchange_connection_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    paper_account_id: uuid.UUID | None = None
    capital_campaign_id: int | None = None
    expires_at: datetime | None = None
    idempotency_key: str | None = None
    reason: str | None = None


class AutonomousCapitalMandateResponse(BaseModel):
    mandate_id: uuid.UUID
    owner_actor_id: str
    status: str
    autonomy_level: str
    provider: str
    exchange_environment: str
    exchange_connection_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    paper_account_id: uuid.UUID | None
    capital_campaign_id: int | None
    authorized_at: datetime | None
    activated_at: datetime | None
    paused_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AutonomousCapitalMandateListResponse(BaseModel):
    items: list[AutonomousCapitalMandateResponse]


class AutonomousCapitalMandateVersionCreateRequest(BaseModel):
    base_currency: str
    authorized_capital_usd: Decimal
    max_order_notional_usd: Decimal
    max_open_exposure_usd: Decimal
    max_daily_deployed_usd: Decimal
    max_daily_realized_loss_usd: Decimal
    max_campaign_drawdown_usd: Decimal
    max_consecutive_losses: int
    position_limit: int
    price_evidence_max_age_seconds: int
    max_slippage_bps: Decimal
    max_fee_bps: Decimal
    allowed_products: list[str]
    allowed_order_sides: list[str]
    allowed_strategy_versions: list[str]
    entry_policy: dict[str, Any]
    exit_policy: dict[str, Any]
    cooldown_policy: dict[str, Any]
    operating_schedule: dict[str, Any]
    approval_policy: str
    reconciliation_policy: dict[str, Any]
    kill_switch_policy: dict[str, Any]
    owner_acknowledgements: dict[str, Any]
    authorization_evidence_summary: dict[str, Any]
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None


class AutonomousCapitalMandateVersionResponse(BaseModel):
    mandate_version_id: uuid.UUID
    mandate_id: uuid.UUID
    version_number: int
    version_hash: str
    base_currency: str
    authorized_capital_usd: Decimal
    max_order_notional_usd: Decimal
    max_open_exposure_usd: Decimal
    max_daily_deployed_usd: Decimal
    max_daily_realized_loss_usd: Decimal
    max_campaign_drawdown_usd: Decimal
    max_consecutive_losses: int
    position_limit: int
    price_evidence_max_age_seconds: int
    max_slippage_bps: Decimal
    max_fee_bps: Decimal
    allowed_products: list[str]
    allowed_order_sides: list[str]
    allowed_strategy_versions: list[str]
    approval_policy: str
    is_authorized: bool
    is_active: bool
    created_at: datetime
    authorized_at: datetime | None


class AutonomousCapitalMandateVersionListResponse(BaseModel):
    items: list[AutonomousCapitalMandateVersionResponse]


class AutonomousCapitalMandateAuthorizationCreateRequest(BaseModel):
    mandate_version_id: uuid.UUID
    authorization_method: str
    owner_acknowledgements: dict[str, Any]
    authorization_evidence: dict[str, Any]
    deterministic_explanation: dict[str, Any]
    expires_at: datetime | None = None
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None


class AutonomousCapitalMandateAuthorizationResponse(BaseModel):
    mandate_authorization_id: uuid.UUID
    mandate_id: uuid.UUID
    mandate_version_id: uuid.UUID
    mandate_version_number: int | None
    autonomy_level: str | None
    authorization_state: str
    approval_result: str
    authorized_by_actor_id: str | None
    audit_correlation_id: uuid.UUID | None
    recorded_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class AutonomousCapitalMandateAuthorizationListResponse(BaseModel):
    items: list[AutonomousCapitalMandateAuthorizationResponse]


class AutonomousCapitalMandateLifecycleActionRequest(BaseModel):
    action: str
    reason: str
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None
    software_build_version: str | None = None


class AutonomousCapitalMandateHistoryEventResponse(BaseModel):
    audit_id: int
    actor: str
    action: str
    created_at: datetime
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None


class AutonomousCapitalMandateHistoryResponse(BaseModel):
    items: list[AutonomousCapitalMandateHistoryEventResponse]


class AutonomousCapitalMandateEvaluationCreateRequest(BaseModel):
    strategy_version: str
    product: str
    side: str
    proposed_notional_usd: Decimal
    current_open_exposure_usd: Decimal
    daily_deployed_usd: Decimal
    daily_realized_loss_usd: Decimal
    campaign_drawdown_usd: Decimal
    consecutive_losses: int
    current_position_count: int
    risk_verdict: str
    evidence_age_seconds: int
    kill_switch_engaged: bool
    observed_at: datetime
    decision_id: uuid.UUID | None = None
    request_context: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None
    software_build_version: str | None = None


class AutonomousCapitalMandateEvaluationResponse(BaseModel):
    evaluation_id: uuid.UUID
    mandate_id: uuid.UUID
    mandate_version_id: uuid.UUID
    mandate_version_number: int
    autonomy_level: str
    proposed_action: str
    authorization_result: str
    approval_result: str
    risk_verdict: str
    risk_evaluated: bool
    checks_passed: list[str]
    checks_failed: list[str]
    deterministic_explanation: list[str]
    reason_code: str
    human_approval_required: bool
    active_mandate_exemption_eligible: bool
    decision_id: uuid.UUID | None
    actor: str
    audit_correlation_id: uuid.UUID
    software_build_version: str | None
    idempotency_key: str
    created_at: datetime


class AutonomousCapitalMandateEvaluationListResponse(BaseModel):
    items: list[AutonomousCapitalMandateEvaluationResponse]
