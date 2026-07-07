from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

BROKER_ORDER_SIDES = {"buy", "sell"}
BROKER_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}
BROKER_TIME_IN_FORCE = {"day", "gtc", "ioc", "fok"}

NORMALIZED_ORDER_STATUSES = {
    "created",
    "accepted",
    "working",
    "partially_filled",
    "filled",
    "canceled",
    "rejected",
    "expired",
    "unknown",
}

NORMALIZED_REJECTION_CATEGORIES = {
    "risk_rejected",
    "approval_missing",
    "validation_error",
    "insufficient_funds",
    "broker_rejected",
    "rate_limited",
    "service_unavailable",
    "unknown",
}


@dataclass(frozen=True)
class RequiredOrchestrationIdentifiers:
    risk_decision_id: uuid.UUID
    approval_event_id: uuid.UUID
    audit_correlation_id: str

    def __post_init__(self) -> None:
        if not self.audit_correlation_id or not self.audit_correlation_id.strip():
            raise ValueError("audit_correlation_id is required")


@dataclass(frozen=True)
class BrokerIdempotencyContract:
    idempotency_key: str
    idempotency_group: str

    def __post_init__(self) -> None:
        if not self.idempotency_key or not self.idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not self.idempotency_group or not self.idempotency_group.strip():
            raise ValueError("idempotency_group is required")


@dataclass(frozen=True)
class NormalizedBrokerOrderRequest:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    broker_account_ref: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: str
    requested_at: datetime
    metadata: dict[str, Any]

    def __post_init__(self) -> None:
        if self.side not in BROKER_ORDER_SIDES:
            raise ValueError("unsupported order side")
        if self.order_type not in BROKER_ORDER_TYPES:
            raise ValueError("unsupported order type")
        if self.time_in_force not in BROKER_TIME_IN_FORCE:
            raise ValueError("unsupported time_in_force")
        if self.quantity <= Decimal("0"):
            raise ValueError("quantity must be positive")
        if not self.adapter_request_id or not self.adapter_request_id.strip():
            raise ValueError("adapter_request_id is required")
        if not self.broker_account_ref or not self.broker_account_ref.strip():
            raise ValueError("broker_account_ref is required")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol is required")


@dataclass(frozen=True)
class ProviderBrokerRequestEnvelope:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    provider_name: str
    endpoint_operation: str
    payload: dict[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.provider_name or not self.provider_name.strip():
            raise ValueError("provider_name is required")
        if not self.endpoint_operation or not self.endpoint_operation.strip():
            raise ValueError("endpoint_operation is required")


@dataclass(frozen=True)
class ProviderBrokerResponseEnvelope:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    provider_name: str
    provider_status_code: int | None
    payload: dict[str, Any]
    received_at: datetime

    def __post_init__(self) -> None:
        if not self.provider_name or not self.provider_name.strip():
            raise ValueError("provider_name is required")


@dataclass(frozen=True)
class NormalizedOrderStatus:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    provider_order_id: str | None
    client_order_id: str
    status: str
    reason: str | None
    observed_at: datetime
    raw_payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.status not in NORMALIZED_ORDER_STATUSES:
            raise ValueError("unsupported normalized order status")
        if not self.client_order_id or not self.client_order_id.strip():
            raise ValueError("client_order_id is required")


@dataclass(frozen=True)
class NormalizedFill:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    provider_fill_id: str
    provider_order_id: str | None
    client_order_id: str
    symbol: str
    filled_quantity: Decimal
    fill_price: Decimal
    fee_amount: Decimal
    fee_currency: str
    liquidity: str | None
    observed_at: datetime
    raw_payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.filled_quantity <= Decimal("0"):
            raise ValueError("filled_quantity must be positive")
        if self.fill_price <= Decimal("0"):
            raise ValueError("fill_price must be positive")
        if self.fee_amount < Decimal("0"):
            raise ValueError("fee_amount cannot be negative")
        if not self.provider_fill_id or not self.provider_fill_id.strip():
            raise ValueError("provider_fill_id is required")
        if not self.client_order_id or not self.client_order_id.strip():
            raise ValueError("client_order_id is required")


@dataclass(frozen=True)
class NormalizedRejection:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    provider_order_id: str | None
    client_order_id: str
    category: str
    error_code: str
    message: str
    retriable: bool
    observed_at: datetime
    raw_payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.category not in NORMALIZED_REJECTION_CATEGORIES:
            raise ValueError("unsupported rejection category")
        if not self.error_code or not self.error_code.strip():
            raise ValueError("error_code is required")
        if not self.message or not self.message.strip():
            raise ValueError("message is required")
        if not self.client_order_id or not self.client_order_id.strip():
            raise ValueError("client_order_id is required")


@dataclass(frozen=True)
class NormalizedBrokerError:
    orchestration_ids: RequiredOrchestrationIdentifiers
    idempotency: BrokerIdempotencyContract
    adapter_request_id: str
    category: str
    error_code: str
    message: str
    details: dict[str, Any]
    retriable: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.category not in NORMALIZED_REJECTION_CATEGORIES:
            raise ValueError("unsupported error category")
        if not self.error_code or not self.error_code.strip():
            raise ValueError("error_code is required")
        if not self.message or not self.message.strip():
            raise ValueError("message is required")