from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ExchangeAuthResult:
    reachable: bool
    authenticated: bool
    account_status: str | None
    permissions: list[str]
    heartbeat_at: datetime
    clock_skew_seconds: int | None = None
    withdrawals_permission_granted: bool = False
    trade_permission_present: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ExchangeBalanceItem:
    currency: str
    available: Decimal
    reserved: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class ExchangeBalanceSnapshot:
    balances: list[ExchangeBalanceItem]
    total_equity_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class ExchangeAccountSnapshot:
    account_status: str | None


@dataclass(frozen=True, slots=True)
class ExchangePermissionSnapshot:
    permissions: list[str]
    verified: bool


@dataclass(frozen=True, slots=True)
class ExchangePreviewResult:
    preview_id: str | None
    success: bool
    failure_reason: str | None
    warning_messages: list[str]
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
    exchange_response_summary: dict[str, Any] = field(default_factory=dict)


class ExchangeProviderClient(Protocol):
    async def test_authentication(self, *, credentials: dict[str, str], environment: str) -> ExchangeAuthResult:
        ...

    async def fetch_balances(self, *, credentials: dict[str, str], environment: str) -> ExchangeBalanceSnapshot:
        ...

    async def fetch_account(self, *, credentials: dict[str, str], environment: str) -> ExchangeAccountSnapshot:
        ...

    async def fetch_permissions(self, *, credentials: dict[str, str], environment: str) -> ExchangePermissionSnapshot:
        ...

    async def preview_market_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        product_id: str,
        side: str,
        quote_size: Decimal | None,
        base_size: Decimal | None,
        client_order_id: str | None = None,
    ) -> ExchangePreviewResult:
        ...
