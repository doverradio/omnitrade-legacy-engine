from __future__ import annotations

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import ExchangeProviderClient
from app.services.exchange_connections.providers.coinbase_advanced import CoinbaseAdvancedClient


_PROVIDER_REGISTRY: dict[str, ExchangeProviderClient] = {
    "coinbase_advanced": CoinbaseAdvancedClient(),
}


def get_exchange_provider(provider: str) -> ExchangeProviderClient:
    normalized = provider.strip().lower()
    client = _PROVIDER_REGISTRY.get(normalized)
    if client is None:
        raise InvalidRequestError(message="Unsupported exchange provider", details={"provider": provider})
    return client
