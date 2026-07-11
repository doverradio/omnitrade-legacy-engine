from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.services.exchange_connections.providers.coinbase_advanced import (
    CoinbaseAdvancedClient,
    parse_coinbase_account_status,
    parse_coinbase_balances,
    parse_coinbase_permissions,
)


def test_balance_parsing_for_usd_btc_eth() -> None:
    payload = {
        "accounts": [
            {
                "available_balance": {"currency": "USD", "value": "100.50"},
                "hold": {"value": "10.25"},
                "status": "active",
            },
            {
                "available_balance": {"currency": "BTC", "value": "0.10"},
                "hold": {"value": "0.02"},
            },
            {
                "available_balance": {"currency": "ETH", "value": "1.50"},
                "hold": {"value": "0.25"},
            },
            {
                "available_balance": {"currency": "SOL", "value": "5"},
                "hold": {"value": "1"},
            },
        ]
    }

    snapshot = parse_coinbase_balances(payload)

    by_currency = {item.currency: item for item in snapshot.balances}

    assert by_currency["USD"].available == Decimal("100.50")
    assert by_currency["USD"].reserved == Decimal("10.25")
    assert by_currency["USD"].total == Decimal("110.75")
    assert by_currency["BTC"].total == Decimal("0.12")
    assert by_currency["ETH"].total == Decimal("1.75")
    assert snapshot.total_equity_usd == Decimal("110.75")


def test_permission_parsing() -> None:
    payload = {"permissions": ["view", "trade", "view"]}
    permissions = parse_coinbase_permissions(payload)

    assert permissions == ["trade", "view"]


def test_account_status_parsing() -> None:
    payload = {"accounts": [{"status": "active"}]}

    assert parse_coinbase_account_status(payload) == "active"


@pytest.mark.asyncio
async def test_historical_fills_request_uses_order_id_query_param() -> None:
    client = CoinbaseAdvancedClient()
    client._request_json = AsyncMock(return_value=({"fills": []}, {"x-request-id": "abc"}))

    await client.list_historical_fills(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        order_id="provider-order-1",
    )

    kwargs = client._request_json.call_args.kwargs
    assert kwargs["method"] == "GET"
    assert kwargs["path"] == "/api/v3/brokerage/orders/historical/fills"
    assert kwargs["query_params"] == {"order_id": "provider-order-1"}
