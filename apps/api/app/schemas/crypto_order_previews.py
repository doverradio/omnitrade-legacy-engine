from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer

from app.schemas.exchange_connections import ExchangeEnvironment, ExchangeReadinessVerdict

CryptoOrderPreviewStatus = Literal[
    "DRAFT",
    "VALIDATING",
    "RISK_REJECTED",
    "CONNECTION_NOT_READY",
    "BALANCE_INSUFFICIENT",
    "PREVIEW_REQUESTED",
    "PREVIEW_READY",
    "PREVIEW_FAILED",
    "EXPIRED",
    "CANCELLED",
]
CryptoOrderPreviewSide = Literal["BUY", "SELL"]
CryptoOrderPreviewOrderType = Literal["MARKET"]
CryptoOrderPreviewAmountCurrency = Literal["USD", "BTC"]
CryptoOrderPreviewGeneratedBy = Literal["operator", "system_recommendation"]
CryptoOrderPreviewRiskVerdict = Literal["approved_for_preview", "rejected", "blocked", "needs_refresh"]


class CryptoOrderPreviewCreateRequest(BaseModel):
    exchange_connection_id: UUID
    environment: ExchangeEnvironment
    product_id: str = Field(default="BTC-USD", min_length=1)
    side: CryptoOrderPreviewSide
    order_type: CryptoOrderPreviewOrderType = "MARKET"
    quote_size: Decimal | None = None
    base_size: Decimal | None = None
    requested_amount_currency: CryptoOrderPreviewAmountCurrency = "USD"
    decision_record_id: UUID | None = None
    validation_run_id: UUID | None = None
    strategy_id: UUID | None = None
    strategy_name: str | None = None
    generated_by: CryptoOrderPreviewGeneratedBy = "operator"
    client_request_id: str | None = None


class CryptoOrderPreviewRefreshRequest(BaseModel):
    client_request_id: str | None = None


class CryptoOrderPreviewCancelRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class CryptoOrderPreviewReadinessResponse(BaseModel):
    ready: bool
    allowed_products: list[str]
    max_quote_size_usd: Decimal
    default_quote_size_usd: Decimal
    market_data_max_age_minutes: int
    expiration_minutes: int

    @field_serializer("max_quote_size_usd", "default_quote_size_usd", when_used="json")
    def serialize_decimal_fields(self, value: Decimal) -> str:
        return format(value, "f")


class CryptoOrderPreviewResponse(BaseModel):
    crypto_order_preview_id: UUID
    preview_version: int
    status: CryptoOrderPreviewStatus
    provider: str
    environment: ExchangeEnvironment
    product_id: str
    side: CryptoOrderPreviewSide
    order_type: CryptoOrderPreviewOrderType
    quote_size: Decimal | None
    base_size: Decimal | None
    requested_amount: Decimal
    requested_amount_currency: CryptoOrderPreviewAmountCurrency
    readiness_verdict: ExchangeReadinessVerdict | None
    risk_verdict: CryptoOrderPreviewRiskVerdict | None
    risk_explanation: str | None
    strategy_id: UUID | None
    strategy_name: str | None
    decision_record_id: UUID | None
    validation_run_id: UUID | None
    preview_id: str | None
    estimated_average_price: Decimal | None
    estimated_total_value: Decimal | None
    estimated_base_size: Decimal | None
    estimated_quote_size: Decimal | None
    estimated_fee: Decimal | None
    estimated_fee_currency: str | None
    estimated_slippage: Decimal | None
    estimated_commission_total: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    available_balance_before: Decimal | None
    estimated_balance_after: Decimal | None
    failure_reason: str | None
    warning_messages: list[str]
    exchange_response_summary: dict[str, object]
    expires_at: datetime
    generated_by: CryptoOrderPreviewGeneratedBy
    audit_correlation_id: UUID | None
    order_submitted: bool
    execution_available: bool
    created_at: datetime
    updated_at: datetime
    refreshed_from_preview_id: UUID | None = None

    @field_serializer(
        "quote_size",
        "base_size",
        "requested_amount",
        "estimated_average_price",
        "estimated_total_value",
        "estimated_base_size",
        "estimated_quote_size",
        "estimated_fee",
        "estimated_slippage",
        "estimated_commission_total",
        "best_bid",
        "best_ask",
        "available_balance_before",
        "estimated_balance_after",
        when_used="json",
    )
    def serialize_decimals(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class CryptoOrderPreviewListResponse(BaseModel):
    items: list[CryptoOrderPreviewResponse]


class CryptoOrderPreviewDetailResponse(CryptoOrderPreviewResponse):
    pass
