"""Tests for Kraken LIMIT order submission, precision handling, and
cancellation -- the execution-layer piece required to make a BUY_LIMIT
entry-intelligence decision (app.services.entry_intelligence) actually
submittable, not merely diagnostic. MARKET-order behavior is covered by
test_provider_conformance_kraken.py and is asserted unchanged here only
where the same code path is now shared (see _submit_add_order)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.kraken_spot import KrakenSpotClient

_PAIR_INFO_RESULT = {
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


def _request(**overrides) -> ExchangeOrderSubmissionRequest:
    kwargs = dict(
        product_id="BTC-USD",
        side="BUY",
        order_type="LIMIT",
        quote_size=None,
        base_size=Decimal("0.001"),
        client_order_id="cid",
        idempotency_key="cid",
        raw_payload={},
        limit_price=Decimal("64918.9"),
        time_in_force=None,
    )
    kwargs.update(overrides)
    return ExchangeOrderSubmissionRequest(**kwargs)


@pytest.mark.asyncio
async def test_limit_buy_payload_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    captured: dict[str, str] = {}
    public_calls: list[str] = []

    async def _public(*, path, **_kwargs):
        public_calls.append(path)
        return _PAIR_INFO_RESULT

    async def _private(*, path, payload, **_kwargs):
        if path == "/private/AddOrder":
            captured.update(payload)
            return {"error": [], "result": {"txid": ["O-LIMIT-1"]}}
        raise AssertionError(f"unexpected private path {path}")

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(),
    )

    assert result.classification == "success"
    assert result.order.provider_order_id == "O-LIMIT-1"
    assert captured["ordertype"] == "limit"
    assert captured["type"] == "buy"
    assert captured["pair"] == "XBTUSD"
    assert captured["price"] == "64918.9"
    assert captured["volume"] == "0.00100000"
    assert "oflags" not in captured  # fciq/viqc are MARKET-only, meaningless for LIMIT
    assert "timeinforce" not in captured  # GTC is Kraken's default, omitted rather than sent

    # No ticker fetch: the limit price is caller-supplied, never derived
    # from a market reference price.
    assert "/public/Ticker" not in public_calls
    assert "/public/AssetPairs" in public_calls


@pytest.mark.asyncio
async def test_limit_sell_payload_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    captured: dict[str, str] = {}

    async def _public(**_kwargs):
        return _PAIR_INFO_RESULT

    async def _private(*, path, payload, **_kwargs):
        captured.update(payload)
        return {"error": [], "result": {"txid": ["O-LIMIT-2"]}}

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(side="SELL", limit_price=Decimal("65500.25")),
    )
    assert result.classification == "success"
    assert captured["type"] == "sell"
    assert captured["price"] == "65500.3"  # quantized UP to 1 decimal for SELL (never accept less)


@pytest.mark.asyncio
async def test_limit_price_quantized_down_for_buy_is_accepted() -> None:
    # 64918.9 already matches pair_decimals=1 exactly -- sanity check via a
    # value that must round down (more decimals than the pair supports).
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return _PAIR_INFO_RESULT

    captured: dict[str, str] = {}

    async def _private(*, path, payload, **_kwargs):
        captured.update(payload)
        return {"error": [], "result": {"txid": ["O-3"]}}

    client._public_request = _public  # type: ignore[method-assign]
    client._private_request = _private  # type: ignore[method-assign]

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(limit_price=Decimal("64918.94")),
    )
    assert result.classification == "success"
    assert captured["price"] == "64918.9"


@pytest.mark.asyncio
async def test_limit_base_size_below_ordermin_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return _PAIR_INFO_RESULT

    monkeypatch.setattr(client, "_public_request", _public)

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(base_size=Decimal("0.00001")),
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "below_min_order_size"


@pytest.mark.asyncio
async def test_limit_notional_below_costmin_rejected_without_ticker_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    public_calls: list[str] = []

    async def _public(*, path, **_kwargs):
        public_calls.append(path)
        return _PAIR_INFO_RESULT

    monkeypatch.setattr(client, "_public_request", _public)

    # base_size * limit_price = 0.0001 * 100 = 0.01 < costmin (0.5)
    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(base_size=Decimal("0.0001"), limit_price=Decimal("100")),
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "below_min_order_cost"
    assert "/public/Ticker" not in public_calls


@pytest.mark.asyncio
async def test_limit_requires_positive_limit_price() -> None:
    client = KrakenSpotClient()
    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(limit_price=None),
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "invalid_limit_price"


@pytest.mark.asyncio
async def test_limit_requires_positive_base_size() -> None:
    client = KrakenSpotClient()
    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(base_size=None),
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "invalid_base_size"


@pytest.mark.asyncio
async def test_limit_ioc_time_in_force_included(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    captured: dict[str, str] = {}

    async def _public(**_kwargs):
        return _PAIR_INFO_RESULT

    async def _private(*, path, payload, **_kwargs):
        captured.update(payload)
        return {"error": [], "result": {"txid": ["O-4"]}}

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(time_in_force="IOC"),
    )
    assert result.classification == "success"
    assert captured["timeinforce"] == "IOC"


@pytest.mark.asyncio
async def test_limit_unsupported_time_in_force_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _public(**_kwargs):
        return _PAIR_INFO_RESULT

    monkeypatch.setattr(client, "_public_request", _public)

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_request(time_in_force="GTD"),
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "unsupported_time_in_force"


@pytest.mark.asyncio
async def test_market_order_unaffected_by_limit_refactor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: extracting _submit_add_order must not change
    MARKET-order payload construction or classification at all."""
    client = KrakenSpotClient()
    captured: dict[str, str] = {}

    async def _public(*, path, **_kwargs):
        if path == "/public/AssetPairs":
            return _PAIR_INFO_RESULT
        return {"error": [], "result": {"XXBTZUSD": {"a": ["50000.0", "1", "1"], "b": ["49999.0", "1", "1"]}}}

    async def _private(*, path, payload, **_kwargs):
        captured.update(payload)
        return {"error": [], "result": {"txid": ["O-MKT"]}}

    monkeypatch.setattr(client, "_public_request", _public)
    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=ExchangeOrderSubmissionRequest(
            product_id="BTC-USD", side="BUY", order_type="MARKET",
            quote_size=Decimal("5"), base_size=None,
            client_order_id="cid", idempotency_key="cid", raw_payload={},
        ),
    )
    assert result.classification == "success"
    assert captured["ordertype"] == "market"
    assert captured["oflags"] == "fciq,viqc"
    assert "price" not in captured


# --- cancel_order ---


@pytest.mark.asyncio
async def test_cancel_order_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()
    captured: dict[str, str] = {}

    async def _private(*, path, payload, **_kwargs):
        assert path == "/private/CancelOrder"
        captured.update(payload)
        return {"error": [], "result": {"count": 1}}

    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-1",
        client_order_id=None,
    )
    assert result.classification == "success"
    assert result.provider_status == "CANCELLED"
    assert captured["txid"] == "O-1"


@pytest.mark.asyncio
async def test_cancel_order_already_resolved_via_zero_count(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        return {"error": [], "result": {"count": 0}}

    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-1",
        client_order_id=None,
    )
    assert result.classification == "already_resolved"


@pytest.mark.asyncio
async def test_cancel_order_already_resolved_via_unknown_order_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        raise InvalidRequestError(
            message="Kraken API returned errors",
            details={"status_code": 200, "errors": ["EOrder:Unknown order"], "response_body": {}},
        )

    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-STALE",
        client_order_id=None,
    )
    assert result.classification == "already_resolved"


@pytest.mark.asyncio
async def test_cancel_order_pending_is_ambiguous_requires_reverification(monkeypatch: pytest.MonkeyPatch) -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        return {"error": [], "result": {"count": 1, "pending": True}}

    monkeypatch.setattr(client, "_private_request", _private)

    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-1",
        client_order_id=None,
    )
    assert result.classification == "ambiguous"
    assert result.provider_status == "CANCEL_QUEUED"


@pytest.mark.asyncio
async def test_cancel_order_provider_rejection_classified() -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        raise InvalidRequestError(
            message="Kraken API returned errors",
            details={"status_code": 200, "errors": ["EGeneral:Permission denied"], "response_body": {}},
        )

    client._private_request = _private  # type: ignore[method-assign]

    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-1",
        client_order_id=None,
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "provider_rejected"


@pytest.mark.asyncio
async def test_cancel_order_5xx_is_ambiguous() -> None:
    client = KrakenSpotClient()

    async def _private(**_kwargs):
        raise InvalidRequestError(
            message="Kraken API returned errors",
            details={"status_code": 503, "errors": [], "response_body": {}},
        )

    client._private_request = _private  # type: ignore[method-assign]

    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-1",
        client_order_id=None,
    )
    assert result.classification == "ambiguous"


@pytest.mark.asyncio
async def test_cancel_order_requires_an_identifier() -> None:
    client = KrakenSpotClient()
    result = await client.cancel_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id=None,
        client_order_id=None,
    )
    assert result.classification == "rejected"
    assert result.rejection.code == "missing_order_identifier"


# --- open-order lookup / unknown provider state fail-closed ---


@pytest.mark.asyncio
async def test_open_order_lookup_maps_resting_limit_order_to_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resting (unfilled) LIMIT order looks exactly like an open order to
    Kraken's QueryOrders -- proves the existing lookup_order machinery
    (unchanged by this session) already handles it correctly."""
    client = KrakenSpotClient()

    async def _private(*, path, payload, **_kwargs):
        if path == "/private/QueryOrders":
            return {
                "error": [],
                "result": {
                    "O-OPEN": {
                        "status": "open",
                        "cl_ord_id": "cid",
                        "vol": "0.00100000",
                        "vol_exec": "0.00000000",
                        "descr": {"pair": "XBT/USD", "type": "buy"},
                    }
                },
            }
        raise AssertionError(f"unexpected private path {path}")

    monkeypatch.setattr(client, "_private_request", _private)

    order = await client.lookup_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-OPEN",
        client_order_id="cid",
        product_id="BTC-USD",
    )
    assert order is not None
    assert order.status == "OPEN"


@pytest.mark.asyncio
async def test_unrecognized_provider_status_fails_closed_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely unrecognized Kraken order status must map to UNKNOWN,
    never be guessed as OPEN/FILLED/CANCELLED -- the supervisor's fail-closed
    behavior depends on this."""
    client = KrakenSpotClient()

    async def _private(*, path, **_kwargs):
        if path == "/private/QueryOrders":
            return {
                "error": [],
                "result": {
                    "O-WEIRD": {
                        "status": "some_new_kraken_status_not_yet_handled",
                        "cl_ord_id": "cid",
                        "vol": "0.001",
                        "vol_exec": "0",
                        "descr": {"pair": "XBT/USD", "type": "buy"},
                    }
                },
            }
        raise AssertionError(f"unexpected private path {path}")

    monkeypatch.setattr(client, "_private_request", _private)

    order = await client.lookup_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="O-WEIRD",
        client_order_id="cid",
        product_id="BTC-USD",
    )
    assert order is not None
    assert order.status == "UNKNOWN"
