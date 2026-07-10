from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer


ExchangeProvider = Literal["coinbase_advanced"]
ExchangeEnvironment = Literal["sandbox", "production"]
ExchangeConnectionStatus = Literal["connected", "disconnected", "error"]


class ExchangeCredentialMaskResponse(BaseModel):
    api_key: str
    api_secret: str
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
    ok: bool
    detail: str


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
    readiness_checks: list[ExchangeReadinessCheckResponse]
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
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    passphrase: str | None = None


class TestExchangeConnectionRequest(BaseModel):
    provider: ExchangeProvider
    environment: ExchangeEnvironment
    api_key: str = Field(min_length=1)
    api_secret: str = Field(min_length=1)
    passphrase: str | None = None


class TestExchangeConnectionResponse(BaseModel):
    reachable: bool
    authenticated: bool
    account_status: str | None
    permissions: list[str]
    heartbeat_at: datetime
    error: str | None = None
