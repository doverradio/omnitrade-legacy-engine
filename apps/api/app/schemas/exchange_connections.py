from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


ExchangeProvider = Literal["coinbase_advanced"]
ExchangeEnvironment = Literal["sandbox", "production"]
ExchangeConnectionStatus = Literal["connected", "disconnected", "error"]
ExchangeReadinessVerdict = Literal[
    "NOT_CONFIGURED",
    "AUTHENTICATION_FAILED",
    "PERMISSION_BLOCKED",
    "ACCOUNT_RESTRICTED",
    "BALANCE_UNAVAILABLE",
    "PRODUCT_UNAVAILABLE",
    "READY_FOR_PREVIEW",
    "READY_FOR_DRY_RUN",
    "READY_FOR_OPERATOR_REVIEW",
    "UNKNOWN",
]
ExchangeReadinessCheckStatus = Literal["pass", "warn", "fail"]


class ExchangeCredentialMaskResponse(BaseModel):
    api_key_name: str
    private_key: str
    passphrase: str | None


class ExchangeBalanceResponse(BaseModel):
    currency: Literal["USD", "BTC", "ETH"]
    available: Decimal
    reserved: Decimal
    total: Decimal

    @field_serializer("available", "reserved", "total", when_used="json")
    def serialize_decimals(self, value: Decimal) -> str:
        return format(value, "f")


class ExchangeReadinessCheckResponse(BaseModel):
    code: str
    label: str
    status: ExchangeReadinessCheckStatus
    explanation: str
    checked_at: datetime
    remediation: str


class ExchangeReadinessReportResponse(BaseModel):
    verdict: ExchangeReadinessVerdict
    checked_at: datetime
    checks: list[ExchangeReadinessCheckResponse]


class ExchangeConnectionResponse(BaseModel):
    exchange_connection_id: UUID
    provider: ExchangeProvider
    provider_label: str
    connection_name: str
    environment: ExchangeEnvironment
    status: ExchangeConnectionStatus
    credentials_valid: bool
    credential_mask: ExchangeCredentialMaskResponse
    api_permissions: list[str]
    account_status: str | None
    balances: list[ExchangeBalanceResponse]
    total_equity_usd: Decimal | None
    last_successful_sync_at: datetime | None
    last_heartbeat_at: datetime | None
    last_api_error: str | None
    readiness: ExchangeReadinessReportResponse
    updated_at: datetime

    @field_serializer("total_equity_usd", when_used="json")
    def serialize_total_equity(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value, "f")


class ExchangeConnectionListResponse(BaseModel):
    items: list[ExchangeConnectionResponse]


class SaveExchangeConnectionRequest(BaseModel):
    provider: ExchangeProvider
    connection_name: str = Field(min_length=1, max_length=120)
    environment: ExchangeEnvironment
    api_key_name: str = Field(min_length=1)
    private_key: str = Field(min_length=1)
    passphrase: str | None = None


class TestExchangeConnectionRequest(BaseModel):
    provider: ExchangeProvider
    environment: ExchangeEnvironment
    api_key_name: str = Field(min_length=1)
    private_key: str = Field(min_length=1)
    passphrase: str | None = None


class TestExchangeConnectionResponse(BaseModel):
    reachable: bool
    authenticated: bool
    account_status: str | None
    permissions: list[str]
    heartbeat_at: datetime
    error: str | None = None


class RotateExchangeCredentialsRequest(BaseModel):
    api_key_name: str = Field(min_length=1)
    private_key: str = Field(min_length=1)
    passphrase: str | None = None
    confirm_replace: bool = False


class DisconnectExchangeConnectionRequest(BaseModel):
    confirm_disconnect: bool = False


class DisconnectExchangeConnectionResponse(BaseModel):
    exchange_connection_id: UUID
    disconnected: bool
    message: str
