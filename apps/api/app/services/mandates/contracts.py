from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import uuid
from typing import Any


AUTONOMY_LEVEL_0 = "LEVEL_0"
AUTONOMY_LEVEL_1 = "LEVEL_1"
AUTONOMY_LEVEL_2 = "LEVEL_2"
AUTONOMY_LEVEL_3 = "LEVEL_3"

AUTONOMY_LEVELS = {
    AUTONOMY_LEVEL_0,
    AUTONOMY_LEVEL_1,
    AUTONOMY_LEVEL_2,
    AUTONOMY_LEVEL_3,
}

MANDATE_STATUSES = {
    "DRAFT",
    "PENDING_AUTHORIZATION",
    "AUTHORIZED",
    "ACTIVE",
    "PAUSED",
    "EXIT_ONLY",
    "EXPIRED",
    "REVOKED",
    "KILLED",
    "COMPLETED",
}

MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN = "APPROVAL_REQUIRED_HUMAN"
MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE = "APPROVAL_SATISFIED_BY_ACTIVE_MANDATE"

MANDATE_APPROVAL_POLICY_HUMAN_REQUIRED = "HUMAN_REQUIRED"
MANDATE_APPROVAL_POLICY_MANDATE_ALLOWED = "MANDATE_ALLOWED"

MANDATE_AUTHORIZATION_ALLOWED = "AUTHORIZED"
MANDATE_AUTHORIZATION_REJECTED = "REJECTED"


@dataclass(frozen=True)
class MandateDomainModel:
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
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class MandateVersionModel:
    mandate_version_id: uuid.UUID
    mandate_id: uuid.UUID
    version_number: int
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
    allowed_products: tuple[str, ...]
    allowed_order_sides: tuple[str, ...]
    allowed_strategy_versions: tuple[str, ...]
    approval_policy: str
    is_authorized: bool
    is_active: bool


@dataclass(frozen=True)
class MandateAuthorizationModel:
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


@dataclass(frozen=True)
class MandateEligibilityInput:
    owner_actor_id: str
    provider: str
    exchange_environment: str
    exchange_connection_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    paper_account_id: uuid.UUID | None
    capital_campaign_id: int | None
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


@dataclass(frozen=True)
class MandateAuthorizationDecision:
    result: str
    approval_result: str
    reason_code: str
    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    deterministic_explanation: tuple[str, ...]


@dataclass(frozen=True)
class DecisionMandateReferenceContract:
    mandate_id: uuid.UUID | None
    mandate_version_id: uuid.UUID | None
    autonomy_level: str
    authorization_result: str


@dataclass(frozen=True)
class MandateLifecycleActionRequest:
    mandate_id: uuid.UUID
    actor: str
    action: str
    reason: str
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None
    software_build_version: str | None = None


@dataclass(frozen=True)
class MandateVersionCreateRequest:
    mandate_id: uuid.UUID
    actor: str
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
    allowed_products: tuple[str, ...]
    allowed_order_sides: tuple[str, ...]
    allowed_strategy_versions: tuple[str, ...]
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


@dataclass(frozen=True)
class MandateVersionReplacementRequest:
    mandate_id: uuid.UUID
    source_mandate_version_id: uuid.UUID
    source_mandate_authorization_id: uuid.UUID
    replacement_allowed_strategy_versions: tuple[str, ...]
    actor: str
    authorization_method: str
    owner_acknowledgements: dict[str, Any]
    authorization_evidence: dict[str, Any]
    deterministic_explanation: dict[str, Any]
    deployed_git_sha: str
    expires_at: datetime | None = None
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None
    software_build_version: str | None = None


@dataclass(frozen=True)
class MandateAuthorizationRequest:
    mandate_id: uuid.UUID
    mandate_version_id: uuid.UUID
    actor: str
    authorization_method: str
    owner_acknowledgements: dict[str, Any]
    authorization_evidence: dict[str, Any]
    deterministic_explanation: dict[str, Any]
    expires_at: datetime | None
    idempotency_key: str | None = None
    audit_correlation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class MandateEvaluationRecord:
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
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]
    deterministic_explanation: tuple[str, ...]
    reason_code: str
    human_approval_required: bool
    active_mandate_exemption_eligible: bool
    decision_id: uuid.UUID | None
    actor: str
    audit_correlation_id: uuid.UUID
    software_build_version: str | None
    idempotency_key: str
    created_at: datetime


@dataclass(frozen=True)
class MandateVersionReplacementResult:
    mandate_id: uuid.UUID
    source_mandate_version_id: uuid.UUID
    replacement_mandate_version_id: uuid.UUID
    authorization_id: uuid.UUID
    mandate_status: str
    selected_mandate_version_id: uuid.UUID
    selected_strategy_identity: str
    created_replacement: bool
