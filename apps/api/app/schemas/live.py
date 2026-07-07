from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class LiveOperatorWarningResponse(BaseModel):
    code: str
    message: str


class LiveRegistrationStatusResponse(BaseModel):
    live_trading_profile_id: uuid.UUID | None
    paper_account_id: uuid.UUID | None
    readiness_state: str
    operating_mode: str
    approval_state: str
    live_opt_in: bool | None
    human_approval_recorded: bool | None
    governance_approved: bool | None
    risk_authority_model: str | None
    paper_default_mode: bool | None
    status_state: str
    warnings: list[LiveOperatorWarningResponse]


class LiveApprovalCheckpointCreateRequest(BaseModel):
    live_trading_profile_id: uuid.UUID
    checkpoint_type: str
    approver_id: str
    approver_role: str
    rationale: str
    approval_scope: dict[str, Any]
    expires_at: datetime | None = None
    renewal_condition: str | None = None
    requested_by: str
    provenance_metadata: dict[str, Any] = {}
    idempotency_key: str | None = None


class LiveApprovalStateChangeCreateRequest(BaseModel):
    live_trading_profile_id: uuid.UUID
    checkpoint_type: str
    approver_id: str
    approver_role: str
    rationale: str
    approval_scope: dict[str, Any]
    requested_by: str
    provenance_metadata: dict[str, Any] = {}
    idempotency_key: str | None = None


class LiveApprovalEventResponse(BaseModel):
    approval_event_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    checkpoint_type: str
    approval_state: str
    lifecycle_state: str
    operating_mode: str
    expires_at: datetime | None
    renewal_condition: str | None
    idempotency_key: str


class LiveApprovalStatusReadModelResponse(BaseModel):
    live_trading_profile_id: uuid.UUID
    status_state: str
    total_events: int
    items: list[LiveApprovalEventResponse]
    warnings: list[LiveOperatorWarningResponse]


class LiveReconciliationSummaryResponse(BaseModel):
    live_trading_profile_id: uuid.UUID
    status_state: str
    total_events: int
    open_count: int
    partially_filled_count: int
    filled_count: int
    canceled_count: int
    rejected_count: int
    unresolved_count: int
    latest_event_type: str | None
    latest_reconciliation_status: str | None
    latest_provider_name: str | None
    latest_recorded_at: datetime | None
    warnings: list[LiveOperatorWarningResponse]


class LiveExecutionQualityReadModelItemResponse(BaseModel):
    quality_metric_id: uuid.UUID
    provider_name: str
    symbol: str
    side: str
    expected_price: str | None
    expected_price_state: str
    actual_fill_price: str | None
    actual_price_state: str
    slippage_abs: str | None
    slippage_bps: str | None
    slippage_state: str
    market_context: dict[str, Any]
    telemetry_context: dict[str, Any]
    recorded_at: datetime


class LiveExecutionQualityReadModelResponse(BaseModel):
    live_trading_profile_id: uuid.UUID
    status_state: str
    total_records: int
    available_slippage_records: int
    unknown_or_unavailable_records: int
    average_slippage_bps: str | None
    items: list[LiveExecutionQualityReadModelItemResponse]
    warnings: list[LiveOperatorWarningResponse]


class LiveComplianceEvidenceItemResponse(BaseModel):
    evidence_record_id: uuid.UUID
    event_type: str
    attributable_actor_id: str
    attributable_actor_role: str
    action_name: str
    action_source: str
    action_summary: str
    provenance_hash: str
    linked_records: dict[str, str]
    evidence_payload: dict[str, Any]
    provenance: dict[str, Any]
    recorded_at: datetime


class LiveComplianceEvidenceReadModelResponse(BaseModel):
    live_trading_profile_id: uuid.UUID
    status_state: str
    total_records: int
    items: list[LiveComplianceEvidenceItemResponse]
    warnings: list[LiveOperatorWarningResponse]


class LiveComplianceExportBundleResponse(BaseModel):
    live_trading_profile_id: uuid.UUID
    exported_by: str
    exported_at: datetime
    status_state: str
    total_records: int
    records: list[LiveComplianceEvidenceItemResponse]
    warnings: list[LiveOperatorWarningResponse]
