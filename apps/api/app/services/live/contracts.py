from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

LIVE_TRADING_LIFECYCLE_STATES = {
    "draft",
    "pending_approval",
    "approved",
    "enabled",
    "suspended",
}

LIVE_TRADING_OPERATING_MODES = {"paper", "live"}

LIVE_TRADING_APPROVAL_STATES = {
    "not_requested",
    "pending",
    "approved",
    "rejected",
    "revoked",
}

# Prompt 9.1 boundary: paper is the default mode, live is opt-in only.
LIVE_TRADING_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_approval"},
    "pending_approval": {"draft", "approved"},
    "approved": {"pending_approval", "enabled", "suspended"},
    "enabled": {"suspended"},
    "suspended": {"enabled"},
}


@dataclass(frozen=True)
class LiveTradingProvenanceContract:
    actor_id: str
    actor_type: str
    source: str
    request_id: str
    observed_at: datetime
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LiveTradingProfileContract:
    live_trading_profile_id: uuid.UUID
    paper_account_id: uuid.UUID
    operating_mode: str
    lifecycle_state: str
    approval_state: str
    live_opt_in: bool
    human_approval_recorded: bool
    paper_default_mode: bool
    governance_approved: bool
    risk_authority_model: str
    autonomous_capital_allocation: bool
    autonomous_strategy_evolution: bool
    automatic_promotion_enabled: bool
    provenance_metadata: dict[str, Any]


@dataclass(frozen=True)
class LiveTradingStateTransitionContract:
    live_trading_profile_id: uuid.UUID
    from_state: str
    to_state: str
    transition_reason: str
    requested_at: datetime


@dataclass(frozen=True)
class LiveReadinessEligibilityResult:
    eligible: bool
    rejection_reason: str | None


@dataclass(frozen=True)
class LiveAccountRegistrationRequest:
    paper_account_id: uuid.UUID
    requested_by: str
    registration_source: str
    live_opt_in: bool
    governance_approved: bool
    human_approval_recorded: bool
    provenance_metadata: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LiveAccountRegistrationResult:
    live_trading_profile_id: uuid.UUID
    readiness_state: str
    operating_mode: str
    accepted: bool
    rejection_reason: str | None
    created_event_id: uuid.UUID
    idempotency_key: str


@dataclass(frozen=True)
class LiveApprovalCheckpointRequest:
    live_trading_profile_id: uuid.UUID
    checkpoint_type: str
    approver_id: str
    approver_role: str
    rationale: str
    approval_scope: dict[str, Any]
    expires_at: datetime | None
    renewal_condition: str | None
    requested_by: str
    provenance_metadata: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LiveApprovalCheckpointResult:
    approval_event_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    checkpoint_type: str
    approval_state: str
    lifecycle_state: str
    operating_mode: str
    expires_at: datetime | None
    renewal_condition: str | None
    idempotency_key: str


@dataclass(frozen=True)
class LiveApprovalStateChangeRequest:
    live_trading_profile_id: uuid.UUID
    checkpoint_type: str
    approver_id: str
    approver_role: str
    rationale: str
    approval_scope: dict[str, Any]
    requested_by: str
    provenance_metadata: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LiveApprovalGateResult:
    allowed: bool
    reason: str | None
    matched_approval_event_id: uuid.UUID | None


@dataclass(frozen=True)
class LiveRiskVerificationResult:
    approved: bool
    reason: str | None


@dataclass(frozen=True)
class LiveExecutionOrchestrationRequest:
    live_trading_profile_id: uuid.UUID
    provider_name: str
    broker_account_ref: str
    adapter_request_id: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    limit_price: str | None
    stop_price: str | None
    time_in_force: str
    risk_decision_id: uuid.UUID
    approval_event_id: uuid.UUID
    audit_correlation_id: str
    requested_by: str
    provenance_metadata: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LiveExecutionOrchestrationResult:
    accepted: bool
    status: str
    reason: str | None
    provider_name: str
    live_trading_profile_id: uuid.UUID
    execution_event_id: uuid.UUID | None
    approval_event_id: uuid.UUID | None
    risk_decision_id: uuid.UUID | None
    audit_correlation_id: str
    adapter_request_id: str
    idempotency_key: str


@dataclass(frozen=True)
class LiveOrderReconciliationRequest:
    live_trading_profile_id: uuid.UUID
    source_execution_event_id: uuid.UUID
    provider_name: str
    provider_order_id: str
    client_order_id: str
    reconciliation_status: str
    requested_by: str
    provenance_metadata: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LiveFillReconciliationRequest:
    live_trading_profile_id: uuid.UUID
    source_execution_event_id: uuid.UUID
    provider_name: str
    provider_order_id: str
    provider_fill_id: str
    client_order_id: str
    symbol: str
    side: str
    fill_quantity: str
    cumulative_filled_quantity: str
    order_quantity: str
    fill_price: str
    fee_amount: str
    fee_currency: str
    requested_by: str
    provenance_metadata: dict[str, Any]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LiveReconciliationResult:
    accepted: bool
    status: str
    reason: str | None
    live_trading_profile_id: uuid.UUID
    source_execution_event_id: uuid.UUID
    reconciliation_event_id: uuid.UUID | None
    accounting_record_ids: tuple[uuid.UUID, ...]
    idempotency_key: str


@dataclass(frozen=True)
class LiveTradingImmutableEventContract:
    idempotency_key: str
    event_hash: str
    immutable_contract_version: str
    live_trading_profile_id: uuid.UUID
    sequence_number: int
    event_type: str
    from_state: str | None
    to_state: str
    operating_mode: str
    paper_default_mode: bool
    live_opt_in: bool
    governance_approved: bool
    risk_authority_model: str
    event_payload: dict[str, Any]
    provenance: LiveTradingProvenanceContract
    recorded_at: datetime


def is_valid_live_trading_transition(from_state: str, to_state: str) -> bool:
    allowed_targets = LIVE_TRADING_ALLOWED_TRANSITIONS.get(from_state)
    if not allowed_targets:
        return False
    return to_state in allowed_targets
