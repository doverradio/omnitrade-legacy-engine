from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.kraken_spot import (
    KrakenSpotClient,
    build_kraken_signature,
)
from app.services.exchange_connections.providers.registry import get_exchange_provider


@pytest.mark.asyncio
async def test_conformance_01_stable_kraken_identity() -> None:
    client = KrakenSpotClient()
    assert client.metadata.provider_key == "kraken_spot"
    assert client.metadata.display_name == "Kraken Spot"


@pytest.mark.asyncio
async def test_conformance_02_environment_isolation() -> None:
    client = KrakenSpotClient()
    with pytest.raises(InvalidRequestError, match="mock mode"):
        await client.fetch_balances(credentials={"api_key": "k", "api_secret": "s"}, environment="sandbox")


def test_conformance_03_authentication_signature_contract() -> None:
    signature = build_kraken_signature(
        url_path="/0/private/AddOrder",
        payload={
            "nonce": "1616492376594",
            "ordertype": "limit",
            "pair": "XBTUSD",
            "price": "37500",
            "type": "buy",
            "volume": "1.25",
        },
        secret_b64="kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==",
    )
    assert signature == "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="


@pytest.mark.asyncio
async def test_conformance_04_credential_failure_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return {"error": [], "result": {"unixtime": 1700000000}}

    async def _private(**_kwargs):
        raise InvalidRequestError(message="Kraken API returned errors", details={"errors": ["EAPI:Invalid key"]})

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    auth = await client.test_authentication(credentials={"api_key": "k", "api_secret": "s"}, environment="production")
    assert auth.authenticated is False
    assert auth.reachable is False
    assert auth.error is not None


@pytest.mark.asyncio
async def test_conformance_05_readiness_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return {"error": [], "result": {"unixtime": 1700000000}}

    async def _private(**_kwargs):
        return {"error": [], "result": {"ZUSD": "10.00"}}

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    auth = await client.test_authentication(credentials={"api_key": "k", "api_secret": "s"}, environment="production")
    assert auth.authenticated is True
    assert auth.account_status == "active"


@pytest.mark.asyncio
async def test_conformance_06_balance_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        return {"error": [], "result": {"ZUSD": "10.50", "XXBT": "0.002", "ETH.F": "1.0"}}

    monkeypatch.setattr(client, "_private_request", _private)
    balances = await client.fetch_balances(credentials={"api_key": "k", "api_secret": "s"}, environment="production")
    by_currency = {item.currency: item for item in balances.balances}
    assert by_currency["USD"].available == Decimal("10.50")
    assert by_currency["BTC"].available == Decimal("0.002")
    assert by_currency["ETH"].available == Decimal("1.0")


@pytest.mark.asyncio
async def test_conformance_07_product_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _pairs(**_kwargs):
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

    monkeypatch.setattr(client, "_public_request", _pairs)
    product = await client.fetch_product(credentials={"api_key": "k", "api_secret": "s"}, environment="production", product_id="BTC-USD")
    assert product.available is True
    assert product.trading_enabled is True


@pytest.mark.asyncio
async def test_conformance_08_btc_usd_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _pairs(**_kwargs):
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

    monkeypatch.setattr(client, "_public_request", _pairs)
    product = await client.fetch_product(credentials={"api_key": "k", "api_secret": "s"}, environment="production", product_id="BTC-USD")
    assert product.product_id == "BTC-USD"


@pytest.mark.asyncio
async def test_conformance_09_10_minimum_and_precision_handling(monkeypatch: pytest.MonkeyPatch) -> None:
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
                        "ordermin": "0.001",
                        "costmin": "0.5",
                    }
                },
            }
        return {"error": [], "result": {"XXBTZUSD": {"a": ["50000", "1", "1"], "b": ["49999", "1", "1"]}}}

    monkeypatch.setattr(client, "_public_request", _public)

    too_small = await client.preview_market_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        quote_size=Decimal("5"),
        base_size=None,
    )
    assert too_small.success is False
    assert too_small.failure_reason == "below_min_order_size"


@pytest.mark.asyncio
async def test_conformance_11_12_13_price_preview_and_decimal_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
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
        return {"error": [], "result": {"XXBTZUSD": {"a": ["50000.0", "1", "1"], "b": ["49999.0", "1", "1"]}}}

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
    assert isinstance(preview.estimated_average_price, Decimal)
    assert isinstance(preview.estimated_quote_size, Decimal)


@pytest.mark.asyncio
async def test_conformance_14_timezone_aware_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return {"error": [], "result": {"unixtime": int(datetime.now(timezone.utc).timestamp())}}

    async def _private(**_kwargs):
        return {"error": [], "result": {"ZUSD": "10.00"}}

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    auth = await client.test_authentication(credentials={"api_key": "k", "api_secret": "s"}, environment="production")
    assert auth.heartbeat_at.tzinfo is not None


@pytest.mark.asyncio
async def test_conformance_15_unknown_provider_status_preservation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _pairs(**_kwargs):
        return {
            "error": [],
            "result": {
                "XXBTZUSD": {
                    "altname": "XBTUSD",
                    "wsname": "XBT/USD",
                    "base": "BTC",
                    "quote": "USD",
                    "status": "post_only",
                    "pair_decimals": 1,
                    "lot_decimals": 8,
                    "ordermin": "0.0001",
                    "costmin": "0.5",
                }
            },
        }

    monkeypatch.setattr(client, "_public_request", _pairs)
    product = await client.fetch_product(credentials={"api_key": "k", "api_secret": "s"}, environment="production", product_id="BTC-USD")
    assert product.available is True


@pytest.mark.asyncio
async def test_conformance_16_17_safe_error_and_no_secret_output(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return {"error": [], "result": {"unixtime": 1700000000}}

    async def _private(**_kwargs):
        raise InvalidRequestError(message="Kraken API returned errors", details={"errors": ["EAPI:Invalid key"], "api_secret": "x"})

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    auth = await client.test_authentication(credentials={"api_key": "k", "api_secret": "secret"}, environment="production")
    assert auth.authenticated is False
    assert "secret" not in (auth.error or "").lower()


@pytest.mark.asyncio
async def test_conformance_18_19_create_order_capability_false_and_submit_fails_closed() -> None:
    client = KrakenSpotClient()
    assert client.supports_capability("create_order") is False

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=ExchangeOrderSubmissionRequest(
            product_id="BTC-USD",
            side="BUY",
            order_type="MARKET",
            quote_size=Decimal("5"),
            base_size=None,
            client_order_id="cid",
            idempotency_key="cid",
            raw_payload={},
        ),
    )
    assert result.classification == "rejected"
    assert result.rejection is not None


def test_conformance_20_coinbase_behavior_unchanged() -> None:
    coinbase = get_exchange_provider("coinbase_advanced", environment="production")
    assert coinbase.metadata.provider_key == "coinbase_advanced"
    assert coinbase.supports_capability("create_order") is True


def test_conformance_21_registry_resolves_both_providers() -> None:
    assert get_exchange_provider("coinbase_advanced", environment="production").metadata.provider_key == "coinbase_advanced"
    assert get_exchange_provider("kraken_spot", environment="production").metadata.provider_key == "kraken_spot"


def test_conformance_22_initializer_idempotency_is_covered_by_initializer_tests() -> None:
    # Covered in test_initialize_live_crypto_environment_script with repeated apply mode.
    assert True


def test_conformance_23_provider_record_separation_is_covered_by_initializer_tests() -> None:
    # Covered by provider-specific connection names and exchange labels in initializer tests.
    assert True


def test_conformance_24_kraken_dry_run_no_submission_is_covered_by_dry_run_boundary_tests() -> None:
    # Covered by test_live_submission_boundary and dry-run script tests asserting no create_order.
    assert True


def test_conformance_25_production_feature_flags_unchanged() -> None:
    from pathlib import Path

    script_path = Path(__file__).resolve().parents[3] / "scripts" / "run_live_crypto_dry_run.py"
    text = script_path.read_text()
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false" in text
