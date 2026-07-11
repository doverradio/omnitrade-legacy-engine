from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient


@pytest.mark.asyncio
async def test_kraken_balance_parser_maps_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        return {"error": [], "result": {"ZUSD": "11.25", "XXBT": "0.001", "ETH.F": "2.0", "SOL": "1"}}

    monkeypatch.setattr(client, "_private_request", _private)
    snapshot = await client.fetch_balances(credentials={"api_key": "k", "api_secret": "s"}, environment="production")

    by_currency = {item.currency: item for item in snapshot.balances}
    assert by_currency["USD"].total == Decimal("11.25")
    assert by_currency["BTC"].total == Decimal("0.001")
    assert by_currency["ETH"].total == Decimal("2.0")


@pytest.mark.asyncio
async def test_kraken_product_lookup_supports_btc_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return {
            "error": [],
            "result": {
                "XXBTZUSD": {
                    "altname": "XBTUSD",
                    "wsname": "XBT/USD",
                    "base": "BTC",
                    "quote": "USD",
                    "status": "online",
                    "pair_decimals": 1,
                    "lot_decimals": 8,
                    "ordermin": "0.0001",
                    "costmin": "0.5",
                }
            },
        }

    monkeypatch.setattr(client, "_public_request", _public)
    product = await client.fetch_product(credentials={"api_key": "k", "api_secret": "s"}, environment="production", product_id="BTC-USD")
    assert product.available is True
    assert product.trading_enabled is True


@pytest.mark.asyncio
async def test_kraken_preview_uses_asset_pairs_and_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(*, path, **_kwargs):
        if path == "/public/AssetPairs":
            return {
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "altname": "XBTUSD",
                        "wsname": "XBT/USD",
                        "base": "BTC",
                        "quote": "USD",
                        "status": "online",
                        "pair_decimals": 1,
                        "lot_decimals": 8,
                        "ordermin": "0.0001",
                        "costmin": "0.5",
                    }
                },
            }
        return {"error": [], "result": {"XXBTZUSD": {"a": ["50000", "1", "1"], "b": ["49995", "1", "1"]}}}

    monkeypatch.setattr(client, "_public_request", _public)
    preview = await client.preview_market_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        quote_size=Decimal("5"),
        base_size=None,
    )

    assert preview.success is True
    assert preview.estimated_base_size is not None
    assert preview.exchange_response_summary["source"] == "kraken_public_assetpairs_ticker"


@pytest.mark.asyncio
async def test_kraken_sandbox_requires_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OT_KRAKEN_SANDBOX_MOCK_MODE", raising=False)
    client = KrakenSpotClient()

    with pytest.raises(InvalidRequestError, match="controlled mock mode"):
        await client.fetch_product(
            credentials={"api_key": "k", "api_secret": "s"},
            environment="sandbox",
            product_id="BTC-USD",
        )
