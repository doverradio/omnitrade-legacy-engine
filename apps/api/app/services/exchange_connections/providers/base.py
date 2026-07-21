from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
import uuid


ProviderEnvironment = Literal["production", "sandbox"]
ProviderCapability = Literal[
    "authentication",
    "permissions",
    "account_readiness",
    "balance_read",
    "product_lookup",
    "price_evidence",
    "preview_market_order",
    "create_order",
    "stable_client_order_id",
    "order_lookup_provider_id",
    "order_lookup_client_id",
    "order_lookup_history",
    "fill_lookup",
    "fee_reporting",
    "sandbox",
    "controlled_mock",
    "health_observability",
]


@dataclass(frozen=True, slots=True)
class ExchangeProviderMetadata:
    provider_key: str
    display_name: str
    supported_environments: tuple[ProviderEnvironment, ...]
    supported_asset_classes: tuple[str, ...]
    capabilities: tuple[ProviderCapability, ...]


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
class ExchangeProductSnapshot:
    product_id: str
    available: bool
    trading_enabled: bool
    # Common, provider-agnostic venue-execution-constraint metadata. Every
    # provider adapter may populate these from its own real, authoritative
    # product/pair data (e.g. Kraken's AssetPairs ordermin/costmin, Coinbase's
    # base_min_size/quote_increment, Binance's LOT_SIZE/MIN_NOTIONAL filters).
    # None means "this provider has not supplied a value" -- callers must
    # never guess a number in its place; a missing minimum is a missing
    # minimum, not a licence to assume zero or fabricate one.
    min_order_notional: Decimal | None = None
    min_order_quantity: Decimal | None = None
    quantity_increment: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ExchangePriceEvidence:
    evidence_id: uuid.UUID
    provider: str
    venue: str
    product_id: str
    symbol: str
    quote_currency: str
    base_currency: str
    bid: Decimal | None
    ask: Decimal | None
    midpoint: Decimal | None
    last_trade: Decimal | None
    reference_price: Decimal | None
    observed_at: datetime | None
    retrieved_at: datetime
    latency_ms: int | None
    freshness_seconds: int | None
    source_endpoint: str
    retrieval_method: str
    confidence: Decimal | None
    audit_metadata: dict[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class ExchangeProviderRejection:
    code: str
    message: str
    retryable: bool = False
    provider_status: str | None = None
    safe_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeProviderAmbiguousResponse:
    reason: str
    safe_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeOrderSubmissionRequest:
    product_id: str
    side: str
    order_type: str
    quote_size: Decimal | None
    base_size: Decimal | None
    client_order_id: str
    idempotency_key: str
    raw_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExchangeProviderOrder:
    provider_order_id: str | None
    client_order_id: str | None
    product_id: str | None
    side: str | None
    status: str | None
    submitted_at: datetime | None
    acknowledged_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeProviderFee:
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class ExchangeProviderFill:
    provider_fill_id: str | None
    provider_order_id: str | None
    product_id: str | None
    size: Decimal
    price: Decimal
    fee: ExchangeProviderFee | None
    occurred_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeOrderSubmissionResult:
    classification: Literal["success", "rejected", "ambiguous"]
    order: ExchangeProviderOrder | None
    rejection: ExchangeProviderRejection | None
    ambiguous: ExchangeProviderAmbiguousResponse | None
    raw_response: dict[str, Any] = field(default_factory=dict)
    safe_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeProviderHealth:
    provider_key: str
    environment: ProviderEnvironment
    last_successful_call_at: datetime | None
    last_error_classification: str | None
    last_error_message: str | None
    supports_latency: bool
    capability_status: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExchangeProviderCapabilityError:
    operation: str
    missing_capabilities: tuple[ProviderCapability, ...]


def provider_supports_capability(*, metadata: ExchangeProviderMetadata, capability: ProviderCapability) -> bool:
    return capability in metadata.capabilities


class ExchangeProviderClient(Protocol):
    @property
    def metadata(self) -> ExchangeProviderMetadata:
        ...

    def supports_capability(self, capability: ProviderCapability) -> bool:
        ...

    async def current_health(self, *, environment: ProviderEnvironment) -> ExchangeProviderHealth:
        ...

    async def test_authentication(self, *, credentials: dict[str, str], environment: str) -> ExchangeAuthResult:
        ...

    async def fetch_balances(self, *, credentials: dict[str, str], environment: str) -> ExchangeBalanceSnapshot:
        ...

    async def fetch_account(self, *, credentials: dict[str, str], environment: str) -> ExchangeAccountSnapshot:
        ...

    async def fetch_permissions(self, *, credentials: dict[str, str], environment: str) -> ExchangePermissionSnapshot:
        ...

    async def fetch_product(self, *, credentials: dict[str, str], environment: str, product_id: str) -> ExchangeProductSnapshot:
        ...

    async def fetch_price_evidence(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        product_id: str,
    ) -> ExchangePriceEvidence:
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

    async def submit_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        request: ExchangeOrderSubmissionRequest,
    ) -> ExchangeOrderSubmissionResult:
        ...

    async def lookup_order(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        provider_order_id: str | None,
        client_order_id: str | None,
        product_id: str | None,
    ) -> ExchangeProviderOrder | None:
        ...

    async def list_fills(
        self,
        *,
        credentials: dict[str, str],
        environment: str,
        provider_order_id: str,
    ) -> list[ExchangeProviderFill]:
        ...
