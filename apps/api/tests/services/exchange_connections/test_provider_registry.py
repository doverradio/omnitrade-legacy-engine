from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.registry import (
    get_exchange_provider,
    get_exchange_provider_metadata,
    list_registered_exchange_providers,
    provider_mock_mode_enabled,
    require_provider_capabilities,
)


def test_registry_returns_coinbase_provider() -> None:
    provider = get_exchange_provider("coinbase_advanced", environment="production")
    assert provider.metadata.provider_key == "coinbase_advanced"


def test_registry_rejects_unknown_provider() -> None:
    with pytest.raises(InvalidRequestError, match="Unsupported exchange provider"):
        get_exchange_provider("unknown_provider")


def test_registry_exposes_metadata_and_capabilities() -> None:
    metadata = get_exchange_provider_metadata("coinbase_advanced")
    assert metadata.provider_key == "coinbase_advanced"
    assert "create_order" in metadata.capabilities
    assert "sandbox" in metadata.supported_environments


def test_registry_lists_registered_providers() -> None:
    providers = list_registered_exchange_providers()
    keys = {item.provider_key for item in providers}
    assert "coinbase_advanced" in keys


def test_registry_requires_capabilities_fail_closed() -> None:
    with pytest.raises(InvalidRequestError, match="capability unavailable"):
        require_provider_capabilities(
            provider="coinbase_advanced",
            operation="unsupported_operation",
            required=("latency_observability",),  # type: ignore[arg-type]
            environment="production",
        )


def test_registry_enforces_environment_support() -> None:
    with pytest.raises(InvalidRequestError, match="requested environment"):
        get_exchange_provider("coinbase_advanced", environment="paper")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_registry_mock_forbidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_COINBASE_SANDBOX_MOCK_MODE", "true")
    provider = get_exchange_provider("coinbase_advanced", environment="production")

    assert provider_mock_mode_enabled("coinbase_advanced") is True
    with pytest.raises(InvalidRequestError, match="forbidden for production"):
        await provider.fetch_balances(
            credentials={"api_key": "k", "api_secret": "s"},
            environment="production",
        )


@pytest.mark.asyncio
async def test_registry_provider_health_shape() -> None:
    provider = get_exchange_provider("coinbase_advanced", environment="sandbox")
    health = await provider.current_health(environment="sandbox")
    assert health.provider_key == "coinbase_advanced"
    assert health.environment == "sandbox"
    assert "create_order" in health.capability_status
