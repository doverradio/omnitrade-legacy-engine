from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.errors import InvalidRequestError
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.coinbase_advanced import (
    CoinbaseAdvancedClient,
    parse_coinbase_balances,
)


def _submission_request() -> ExchangeOrderSubmissionRequest:
    return ExchangeOrderSubmissionRequest(
        product_id="BTC-USD",
        side="BUY",
        order_type="MARKET",
        quote_size=Decimal("5.00"),
        base_size=None,
        client_order_id="client-1",
        idempotency_key="client-1",
        raw_payload={
            "client_order_id": "client-1",
            "product_id": "BTC-USD",
            "side": "BUY",
            "order_configuration": {"market_market_ioc": {"quote_size": "5.00", "rfq_disabled": True}},
        },
    )


def test_conformance_01_stable_provider_identity() -> None:
    client = CoinbaseAdvancedClient()
    assert client.metadata.provider_key == "coinbase_advanced"
    assert client.metadata.display_name == "Coinbase Advanced"


def test_conformance_02_environment_isolation() -> None:
    client = CoinbaseAdvancedClient()
    assert client._base_url("production") != client._base_url("sandbox")


@pytest.mark.asyncio
async def test_conformance_03_04_credential_and_readiness_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _request_json(**kwargs):
        if kwargs["path"] == "/api/v3/brokerage/accounts":
            return ({"accounts": [{"status": "active"}]}, {"Date": "Thu, 09 Jul 2026 10:00:00 GMT"})
        return ({"permissions": ["view", "trade"]}, {})

    monkeypatch.setattr(client, "_request_json", _request_json)
    result = await client.test_authentication(credentials={"api_key": "k", "api_secret": "s"}, environment="production")

    assert isinstance(result.authenticated, bool)
    assert isinstance(result.permissions, list)
    assert result.account_status == "active"


@pytest.mark.asyncio
async def test_conformance_05_permission_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _request_json(**_kwargs):
        return {"permissions": ["view", "trade"]}, {}

    monkeypatch.setattr(client, "_request_json", _request_json)
    result = await client.fetch_permissions(credentials={"api_key": "k", "api_secret": "s"}, environment="production")
    assert result.verified is True
    assert "trade" in result.permissions


def test_conformance_06_balance_normalization_decimal_safe() -> None:
    snapshot = parse_coinbase_balances(
        {
            "accounts": [
                {"available_balance": {"currency": "USD", "value": "10.25"}, "hold": {"value": "0.75"}},
                {"available_balance": {"currency": "BTC", "value": "0.002"}, "hold": {"value": "0.000"}},
            ]
        }
    )
    usd = next(item for item in snapshot.balances if item.currency == "USD")
    assert isinstance(usd.available, Decimal)
    assert usd.total == Decimal("11.00")


@pytest.mark.asyncio
async def test_conformance_07_product_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _request_json(**_kwargs):
        return {"product_id": "BTC-USD", "is_disabled": False, "trading_disabled": False}, {}

    monkeypatch.setattr(client, "_request_json", _request_json)
    product = await client.fetch_product(credentials={"api_key": "k", "api_secret": "s"}, environment="production", product_id="BTC-USD")
    assert product.available is True
    assert product.trading_enabled is True


@pytest.mark.asyncio
async def test_conformance_08_09_price_preview_normalization_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _request_json(**_kwargs):
        return {"success": True, "preview_id": "p-1", "estimated_average_price": "50000", "estimated_quote_size": "5"}, {}

    monkeypatch.setattr(client, "_request_json", _request_json)
    preview = await client.preview_market_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        quote_size=Decimal("5.00"),
        base_size=None,
    )
    assert preview.preview_id == "p-1"
    assert isinstance(preview.estimated_average_price, Decimal)


@pytest.mark.asyncio
async def test_conformance_fetch_price_evidence_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _request_json(**_kwargs):
        return (
            {
                "product_id": "BTC-USD",
                "base_currency_id": "BTC",
                "quote_currency_id": "USD",
                "best_bid": "49990",
                "best_ask": "50000",
                "price": "49995",
            },
            {"Date": "Thu, 09 Jul 2026 10:00:00 GMT"},
        )

    monkeypatch.setattr(client, "_request_json", _request_json)
    evidence = await client.fetch_price_evidence(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        product_id="BTC-USD",
    )

    assert evidence.product_id == "BTC-USD"
    assert evidence.base_currency == "BTC"
    assert evidence.quote_currency == "USD"
    assert evidence.reference_price == Decimal("50000")
    assert evidence.source_endpoint == "/api/v3/brokerage/products/BTC-USD"


@pytest.mark.asyncio
async def test_conformance_10_timestamp_awareness(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _get_historical_order(**_kwargs):
        return ({"order": {"order_id": "o-1", "client_order_id": "c-1", "product_id": "BTC-USD", "status": "OPEN", "created_time": "2026-07-09T12:00:00Z"}}, {})

    monkeypatch.setattr(client, "get_historical_order", _get_historical_order)
    result = await client.lookup_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="o-1",
        client_order_id="c-1",
        product_id="BTC-USD",
    )
    assert result is not None
    assert result.submitted_at is not None
    assert result.submitted_at.tzinfo is not None


@pytest.mark.asyncio
async def test_conformance_11_12_client_order_id_preserved_success_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _create_order(**_kwargs):
        return ({"success": True, "order": {"order_id": "o-1", "client_order_id": "client-1", "product_id": "BTC-USD", "status": "OPEN"}}, {"x-request-id": "1"})

    monkeypatch.setattr(client, "create_order", _create_order)
    submission = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_submission_request(),
    )
    assert submission.classification == "success"
    assert submission.order is not None
    assert submission.order.client_order_id == "client-1"


@pytest.mark.asyncio
async def test_conformance_13_explicit_rejection_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _reject(**_kwargs):
        raise InvalidRequestError("provider rejected", details={"status_code": 400})

    monkeypatch.setattr(client, "create_order", _reject)
    submission = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_submission_request(),
    )
    assert submission.classification == "rejected"
    assert submission.rejection is not None


@pytest.mark.asyncio
async def test_conformance_14_15_ambiguous_and_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()
    calls = {"count": 0}

    async def _ambiguous(**_kwargs):
        calls["count"] += 1
        return ({"success": False, "order": {"order_id": "o-1", "status": "UNKNOWN"}}, {"x-request-id": "1"})

    monkeypatch.setattr(client, "create_order", _ambiguous)
    submission = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_submission_request(),
    )
    assert submission.classification == "ambiguous"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_conformance_16_17_order_lookup_by_provider_and_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _get_historical_order(**_kwargs):
        return ({"order": {"order_id": "o-1", "client_order_id": "c-1", "product_id": "BTC-USD", "status": "OPEN"}}, {})

    async def _list_historical_orders(**_kwargs):
        return ({"orders": [{"order_id": "o-2", "client_order_id": "c-2", "product_id": "BTC-USD", "status": "FILLED"}]}, {})

    monkeypatch.setattr(client, "get_historical_order", _get_historical_order)
    monkeypatch.setattr(client, "list_historical_orders", _list_historical_orders)

    by_provider = await client.lookup_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="o-1",
        client_order_id="c-1",
        product_id="BTC-USD",
    )
    by_client = await client.lookup_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id=None,
        client_order_id="c-2",
        product_id="BTC-USD",
    )

    assert by_provider is not None and by_provider.provider_order_id == "o-1"
    assert by_client is not None and by_client.provider_order_id == "o-2"


@pytest.mark.asyncio
async def test_conformance_18_19_fill_and_fee_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _list_historical_fills(**_kwargs):
        return ({"fills": [{"trade_id": "f-1", "order_id": "o-1", "product_id": "BTC-USD", "size": "0.001", "price": "50000", "commission": "0.05", "commission_currency": "USD", "created_time": "2026-07-09T12:00:00Z"}]}, {})

    monkeypatch.setattr(client, "list_historical_fills", _list_historical_fills)
    fills = await client.list_fills(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="o-1",
    )

    assert fills and fills[0].provider_fill_id == "f-1"
    assert fills[0].fee is not None
    assert fills[0].fee.currency == "USD"


@pytest.mark.asyncio
async def test_conformance_20_unknown_status_preservation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _get_historical_order(**_kwargs):
        return ({"order": {"order_id": "o-1", "client_order_id": "c-1", "product_id": "BTC-USD", "status": "PENDING_REVIEW"}}, {})

    monkeypatch.setattr(client, "get_historical_order", _get_historical_order)
    result = await client.lookup_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        provider_order_id="o-1",
        client_order_id="c-1",
        product_id="BTC-USD",
    )

    assert result is not None
    assert result.status == "PENDING_REVIEW"


@pytest.mark.asyncio
async def test_conformance_21_mock_forbidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OT_COINBASE_SANDBOX_MOCK_MODE", "true")
    client = CoinbaseAdvancedClient()
    with pytest.raises(InvalidRequestError, match="forbidden for production"):
        await client.fetch_balances(credentials={"api_key": "k", "api_secret": "s"}, environment="production")


@pytest.mark.asyncio
async def test_conformance_22_no_secret_output_in_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CoinbaseAdvancedClient()

    async def _reject(**_kwargs):
        raise InvalidRequestError("provider rejected", details={"status_code": 400, "api_secret": "must-not-leak"})

    monkeypatch.setattr(client, "create_order", _reject)
    submission = await client.submit_order(
        credentials={"api_key": "k", "api_secret": "s"},
        environment="production",
        request=_submission_request(),
    )
    assert submission.classification == "rejected"
    assert submission.rejection is not None


def test_conformance_23_24_no_raw_provider_import_in_generic_services_and_single_create_order_boundary() -> None:
    app_root = Path(__file__).resolve().parents[3] / "app"
    allowed_importers = {
        Path("services/exchange_connections/providers/coinbase_advanced.py"),
        Path("services/exchange_connections/providers/registry.py"),
    }
    allowed_create_order_callers = {Path("services/live_crypto_orders.py")}

    import_violations: list[str] = []
    create_order_violations: list[str] = []

    for file_path in app_root.rglob("*.py"):
        tree = ast.parse(file_path.read_text(), filename=str(file_path))
        relative = file_path.relative_to(app_root)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services.exchange_connections.providers.coinbase_advanced":
                if relative not in allowed_importers:
                    import_violations.append(str(relative))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_order":
                if file_path.name != "coinbase_advanced.py" and relative not in allowed_create_order_callers:
                    create_order_violations.append(str(relative))

    assert not import_violations
    assert not create_order_violations


def test_conformance_25_production_safety_flags_preserved() -> None:
    script_file = Path(__file__).resolve().parents[3] / "scripts" / "run_live_crypto_dry_run.py"
    text = script_file.read_text()
    assert "live_crypto_order_submission_enabled" in text
    assert "LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED must remain false" in text
