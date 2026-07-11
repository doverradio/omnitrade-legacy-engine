from __future__ import annotations

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import (
    ExchangeProviderCapabilityError,
    ExchangeProviderClient,
    ExchangeProviderMetadata,
    ProviderCapability,
    ProviderEnvironment,
)
from app.services.exchange_connections.providers.coinbase_advanced import CoinbaseAdvancedClient
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient


_PROVIDER_REGISTRY: dict[str, ExchangeProviderClient] = {
    "coinbase_advanced": CoinbaseAdvancedClient(),
    "kraken_spot": KrakenSpotClient(),
}


def get_exchange_provider(provider: str, *, environment: ProviderEnvironment | None = None) -> ExchangeProviderClient:
    normalized = provider.strip().lower()
    client = _PROVIDER_REGISTRY.get(normalized)
    if client is None:
        raise InvalidRequestError(message="Unsupported exchange provider", details={"provider": provider})
    if environment is not None and environment not in client.metadata.supported_environments:
        raise InvalidRequestError(
            message="Provider does not support requested environment",
            details={
                "provider": provider,
                "requested_environment": environment,
                "supported_environments": list(client.metadata.supported_environments),
            },
        )
    return client


def get_exchange_provider_metadata(provider: str) -> ExchangeProviderMetadata:
    return get_exchange_provider(provider).metadata


def list_registered_exchange_providers() -> tuple[ExchangeProviderMetadata, ...]:
    return tuple(client.metadata for client in _PROVIDER_REGISTRY.values())


def provider_mock_mode_enabled(provider: str) -> bool:
    client = get_exchange_provider(provider)
    marker = getattr(client, "mock_mode_enabled", None)
    if callable(marker):
        return bool(marker())
    return False


def require_provider_capabilities(
    *,
    provider: str,
    operation: str,
    required: tuple[ProviderCapability, ...],
    environment: ProviderEnvironment | None = None,
) -> None:
    client = get_exchange_provider(provider, environment=environment)
    missing = tuple(cap for cap in required if not client.supports_capability(cap))
    if not missing:
        return
    error = ExchangeProviderCapabilityError(operation=operation, missing_capabilities=missing)
    raise InvalidRequestError(
        message="Provider capability unavailable for requested operation",
        details={
            "provider": provider,
            "operation": error.operation,
            "missing_capabilities": list(error.missing_capabilities),
        },
    )
