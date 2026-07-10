from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


LiveCryptoOrderStatus = Literal[
    "DRY_RUN_READY",
    "DRY_RUN_BLOCKED",
    "PENDING_CONFIRMATION",
    "CONFIRMATION_EXPIRED",
    "VALIDATING",
    "RISK_REJECTED",
    "SUBMISSION_PENDING",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "FILLED",
    "REJECTED",
    "CANCELLED",
    "RECONCILIATION_REQUIRED",
    "UNKNOWN",
]

LiveCryptoOrderProviderStatus = Literal[
    "PENDING",
    "OPEN",
    "FILLED",
    "CANCELLED",
    "EXPIRED",
    "FAILED",
    "QUEUED",
    "CANCEL_QUEUED",
    "EDIT_QUEUED",
    "UNKNOWN",
]

LiveCryptoOrderDryRunStatus = Literal["DRY_RUN_READY", "DRY_RUN_BLOCKED"]
LiveCryptoOrderReadinessVerdict = Literal[
    "NOT_CONFIGURED",
    "BLOCKED",
    "READY_FOR_DRY_RUN",
    "DRY_RUN_PASSED",
    "READY_FOR_OPERATOR_ENABLEMENT",
]


class LiveCryptoOrderPrepareRequest(BaseModel):
    live_trading_profile_id: UUID
    crypto_order_preview_id: UUID
    operator_identity: str = Field(min_length=1, max_length=120)
    idempotency_token: str | None = None


class LiveCryptoOrderDryRunRequest(BaseModel):
    live_trading_profile_id: UUID
    crypto_order_preview_id: UUID
    operator_identity: str = Field(min_length=1, max_length=120)
    idempotency_token: str | None = None


class LiveCryptoOrderSubmitRequest(BaseModel):
    live_crypto_order_id: UUID
    confirmation_challenge_id: UUID
    confirmation_phrase: str = Field(min_length=1, max_length=64)
    operator_identity: str = Field(min_length=1, max_length=120)
    idempotency_token: str = Field(min_length=1, max_length=120)


class LiveCryptoOrderCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
    operator_identity: str = Field(min_length=1, max_length=120)


class LiveCryptoOrderReconcileRequest(BaseModel):
    operator_identity: str = Field(min_length=1, max_length=120)


class LiveCryptoOrderReadinessCheckResponse(BaseModel):
    code: str
    label: str
    status: Literal["pass", "warn", "fail"]
    explanation: str
    checked_at: datetime
    remediation: str


class LiveCryptoOrderResponse(BaseModel):
    live_crypto_order_id: UUID
    crypto_order_preview_id: UUID
    exchange_connection_id: UUID
    provider: str
    environment: str
    product_id: str
    side: str
    order_type: str
    requested_quote_size: Decimal
    client_order_id: str
    status: LiveCryptoOrderStatus
    risk_event_id: UUID | None
    decision_record_id: UUID | None
    validation_run_id: UUID | None
    provider_order_id: str | None
    provider_status: LiveCryptoOrderProviderStatus | None
    submitted_at: datetime | None
    acknowledged_at: datetime | None
    filled_at: datetime | None
    cancelled_at: datetime | None
    failure_code: str | None
    failure_reason: str | None
    safe_provider_response: dict[str, Any]
    audit_correlation_id: UUID
    operator_confirmation_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("requested_quote_size", when_used="json")
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class LiveCryptoOrderListResponse(BaseModel):
    items: list[LiveCryptoOrderResponse]


class LiveCryptoOrderReadinessResponse(BaseModel):
    overall_verdict: LiveCryptoOrderReadinessVerdict
    live_mode_enabled: bool
    live_profile_ready: bool
    feature_flag_enabled: bool
    dry_run_enabled: bool
    max_order_usd: Decimal
    latest_preview_age_seconds: int | None
    latest_balance_age_seconds: int | None
    latest_readiness_age_seconds: int | None
    latest_price_age_seconds: int | None
    reason: str | None = None
    checks: list[LiveCryptoOrderReadinessCheckResponse] = Field(default_factory=list)

    @field_serializer("max_order_usd", when_used="json")
    def _serialize_max_order(self, value: Decimal) -> str:
        return format(value, "f")


class LiveCryptoOrderPrepareResponse(BaseModel):
    live_crypto_order: LiveCryptoOrderResponse
    confirmation_challenge_id: UUID
    confirmation_phrase_required: str
    confirmation_expires_at: datetime
    live_money_warning: str
    execution_risk_verdict: str
    preview_age_seconds: int
    estimated_usd_balance_after: Decimal | None
    usd_balance_before: Decimal | None


class LiveCryptoOrderDryRunResponse(BaseModel):
    live_crypto_order: LiveCryptoOrderResponse
    dry_run_status: LiveCryptoOrderDryRunStatus
    dry_run_message: str
    safe_request_summary: dict[str, Any]
    provider_create_order_called: bool
    order_submitted: bool


class LiveCryptoOrderSubmitResponse(BaseModel):
    live_crypto_order: LiveCryptoOrderResponse
    execution_risk_verdict: str
    provider_create_order_responded: bool
    provider_reconciliation_status: str | None
    safe_provider_response: dict[str, Any]
    order_submitted: bool


class LiveCryptoOrderReconcileResponse(BaseModel):
    live_crypto_order: LiveCryptoOrderResponse
    reconciliation_status: str
    provider_status: str | None
    provider_order_id: str | None
    provider_fill_observed: bool
    safe_provider_response: dict[str, Any]

