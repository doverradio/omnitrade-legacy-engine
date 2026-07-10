from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExchangeAuthResult:
    reachable: bool
    authenticated: bool
    account_status: str | None
    permissions: list[str]
    heartbeat_at: datetime
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


class ExchangeProviderClient(Protocol):
    async def test_authentication(self, *, credentials: dict[str, str], environment: str) -> ExchangeAuthResult:
        ...

    async def fetch_balances(self, *, credentials: dict[str, str], environment: str) -> ExchangeBalanceSnapshot:
        ...

    async def fetch_account(self, *, credentials: dict[str, str], environment: str) -> ExchangeAccountSnapshot:
        ...

    async def fetch_permissions(self, *, credentials: dict[str, str], environment: str) -> ExchangePermissionSnapshot:
        ...
