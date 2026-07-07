from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.services.live.broker_adapters.contracts import (
    NormalizedBrokerError,
    NormalizedBrokerOrderRequest,
    NormalizedFill,
    NormalizedOrderStatus,
    NormalizedRejection,
    ProviderBrokerRequestEnvelope,
    ProviderBrokerResponseEnvelope,
)


@runtime_checkable
class BrokerAdapterContract(Protocol):
    provider_name: str

    def build_provider_order_request(
        self,
        *,
        request: NormalizedBrokerOrderRequest,
    ) -> ProviderBrokerRequestEnvelope:
        ...

    def normalize_provider_order_status(
        self,
        *,
        response: ProviderBrokerResponseEnvelope,
        client_order_id: str,
    ) -> NormalizedOrderStatus:
        ...

    def normalize_provider_fill(
        self,
        *,
        response: ProviderBrokerResponseEnvelope,
        client_order_id: str,
    ) -> NormalizedFill:
        ...

    def normalize_provider_rejection(
        self,
        *,
        response: ProviderBrokerResponseEnvelope,
        client_order_id: str,
    ) -> NormalizedRejection:
        ...

    def normalize_provider_error(
        self,
        *,
        response: ProviderBrokerResponseEnvelope,
        category: str,
        error_code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> NormalizedBrokerError:
        ...