from __future__ import annotations

import base64
import hashlib
import hmac
from decimal import Decimal
import urllib.parse

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.kraken_spot import (
    KrakenSpotClient,
    _encode_form_payload,
    build_kraken_signature,
    build_kraken_signature_from_encoded_payload,
)


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


def test_kraken_signature_independent_reference_matches_official_vector() -> None:
    payload = {
        "nonce": "1616492376594",
        "ordertype": "limit",
        "pair": "XBTUSD",
        "price": "37500",
        "type": "buy",
        "volume": "1.25",
    }
    secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    expected = "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="

    # Independent reference implementation (kept local to test).
    postdata = urllib.parse.urlencode(payload)
    preimage = (payload["nonce"] + postdata).encode("utf-8")
    digest = hashlib.sha256(preimage).digest()
    message = b"/0/private/AddOrder" + digest
    mac = hmac.new(base64.b64decode(secret), message, hashlib.sha512)
    reference_sig = base64.b64encode(mac.digest()).decode("utf-8")

    actual_sig = build_kraken_signature(url_path="/0/private/AddOrder", payload=payload, secret_b64=secret)
    assert reference_sig == expected
    assert actual_sig == expected


def test_kraken_encoded_payload_is_stable_and_excludes_empty_optional_values() -> None:
    body = _encode_form_payload({"nonce": "1700000000000", "ordertype": "limit", "pair": "XBTUSD"})
    assert body == "nonce=1700000000000&ordertype=limit&pair=XBTUSD"
    assert "&&" not in body
    assert "otp=" not in body


def test_kraken_signature_changes_with_nonce_body_and_path() -> None:
    secret = "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    body = "nonce=1000&pair=XBTUSD"
    sig_base = build_kraken_signature_from_encoded_payload(
        url_path="/0/private/Balance",
        nonce="1000",
        encoded_payload=body,
        secret_b64=secret,
    )
    sig_nonce = build_kraken_signature_from_encoded_payload(
        url_path="/0/private/Balance",
        nonce="1001",
        encoded_payload="nonce=1001&pair=XBTUSD",
        secret_b64=secret,
    )
    sig_body = build_kraken_signature_from_encoded_payload(
        url_path="/0/private/Balance",
        nonce="1000",
        encoded_payload="nonce=1000&pair=ETHUSD",
        secret_b64=secret,
    )
    sig_path = build_kraken_signature_from_encoded_payload(
        url_path="/0/private/OpenOrders",
        nonce="1000",
        encoded_payload=body,
        secret_b64=secret,
    )
    assert sig_base != sig_nonce
    assert sig_base != sig_body
    assert sig_base != sig_path


@pytest.mark.asyncio
async def test_kraken_private_request_signs_exact_transmitted_path_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    captured: dict[str, str] = {}

    class _FakeResponse:
        status_code = 200
        text = '{"error":[],"result":{"ZUSD":"1.00"}}'

        def json(self):
            return {"error": [], "result": {"ZUSD": "1.00"}}

    class _FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            captured["base_url"] = str(base_url)
            captured["timeout"] = str(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, path, content, headers):
            captured["path"] = str(path)
            captured["content"] = str(content)
            captured["content_type"] = str(headers.get("Content-Type"))
            captured["api_key"] = str(headers.get("API-Key"))
            captured["api_sign"] = str(headers.get("API-Sign"))
            return _FakeResponse()

    monkeypatch.setattr("app.services.exchange_connections.providers.kraken_spot.httpx.AsyncClient", _FakeAsyncClient)

    await client._private_request(
        path="/private/Balance",
        environment="production",
        credentials={"api_key": "public-key", "api_secret": "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==", "passphrase": ""},
        payload={},
    )

    assert captured["base_url"] == "https://api.kraken.com"
    assert captured["path"] == "/0/private/Balance"
    assert captured["content_type"] == "application/x-www-form-urlencoded"
    assert captured["content"].count("nonce=") == 1
    assert captured["content"].startswith("nonce=")
    nonce = captured["content"].split("=", 1)[1]
    expected_signature = build_kraken_signature_from_encoded_payload(
        url_path="/0/private/Balance",
        nonce=nonce,
        encoded_payload=captured["content"],
        secret_b64="kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==",
    )
    assert captured["api_sign"] == expected_signature


@pytest.mark.asyncio
async def test_kraken_public_request_uses_versioned_uri_path(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    captured: dict[str, str] = {}

    class _FakeResponse:
        status_code = 200
        text = '{"error":[],"result":{"unixtime":1700000000}}'

        def json(self):
            return {"error": [], "result": {"unixtime": 1700000000}}

    class _FakeAsyncClient:
        def __init__(self, *, base_url, timeout):
            captured["base_url"] = str(base_url)
            _ = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, path, params=None):
            captured["path"] = str(path)
            captured["params"] = "" if params is None else str(params)
            return _FakeResponse()

    monkeypatch.setattr("app.services.exchange_connections.providers.kraken_spot.httpx.AsyncClient", _FakeAsyncClient)

    await client._public_request(path="/public/Time", environment="production", params=None)
    assert captured["base_url"] == "https://api.kraken.com"
    assert captured["path"] == "/0/public/Time"
