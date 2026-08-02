from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_serializer, field_validator

from app.schemas.live_crypto_orders import LiveCryptoOrderResponse


ExchangeProvider = Literal["coinbase_advanced", "kraken_spot"]
ExchangeEnvironment = Literal["sandbox", "production"]
ExchangeConnectionStatus = Literal["connected", "disconnected", "error"]
ExchangeReadinessVerdict = Literal[
    "NOT_CONFIGURED",
    "AUTHENTICATION_FAILED",
    "PERMISSION_BLOCKED",
    "ACCOUNT_RESTRICTED",
    "BALANCE_UNAVAILABLE",
    "INITIALIZED_BUT_UNFUNDED",
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


# Balance evidence must reflect whatever asset codes a provider legitimately
# reports an account holding -- it is not bounded to the currencies this
# system has product/trading support for (that authorization lives entirely
# in asset_roster.ADDITIONAL_PRODUCT_ASSET_SYMBOLS / campaign
# allowed_instruments / mandate allowed_products, none of which this schema
# touches). Confirmed production defect: a legitimate Kraken SOL balance was
# rejected outright because this field was a hard-coded
# Literal["USD", "BTC", "ETH"], failing the entire balance-refresh response
# even though the provider adapter, transaction commit, and persistence all
# already handled it correctly -- SOL was silently NOT the problem; the
# response schema was. A bounded, strictly-validated generic code (normalized
# the same way kraken_spot.py's own _canonical_asset normalizes: stripped,
# upper-cased, alphanumeric only) replaces the Literal so a legitimate new
# asset code never requires another schema edit to merely be *reported*.
_ASSET_CURRENCY_CODE_PATTERN = re.compile(r"^[A-Z0-9]{1,12}$")


class ExchangeBalanceResponse(BaseModel):
    currency: str
    available: Decimal
    reserved: Decimal
    total: Decimal

    @field_validator("currency", mode="before")
    @classmethod
    def _normalize_currency_code(cls, value: object) -> str:
        normalized = str(value if value is not None else "").strip().upper()
        if not _ASSET_CURRENCY_CODE_PATTERN.fullmatch(normalized):
            raise ValueError(f"invalid or unreasonable balance currency code: {value!r}")
        return normalized

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


class ReconcileExternalTradeRequest(BaseModel):
    provider_order_id: str = Field(min_length=1, max_length=120)


class ReconcileExternalTradeResponse(BaseModel):
    live_crypto_order: LiveCryptoOrderResponse
    provider_order_id: str
    product_id: str
    side: str
    live_trading_profile_id: UUID
    reconciliation_status: str
    accounting_completion_status: str | None
    provider_fill_observed: bool
