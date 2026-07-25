from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.operator_cli import service


class _FakeProduct:
    def __init__(self, *, available, trading_enabled, min_order_notional, min_order_quantity, quantity_increment):
        self.available = available
        self.trading_enabled = trading_enabled
        self.min_order_notional = min_order_notional
        self.min_order_quantity = min_order_quantity
        self.quantity_increment = quantity_increment


class _FakePriceEvidence:
    def __init__(self, reference_price):
        self.reference_price = reference_price
        self.observed_at = datetime.now(timezone.utc)


class _FakeKrakenClient:
    def __init__(self, product, price_evidence):
        self._product = product
        self._price_evidence = price_evidence

    async def fetch_product(self, *, credentials, environment, product_id):
        assert credentials == {}
        return self._product

    async def fetch_price_evidence(self, *, credentials, environment, product_id):
        assert credentials == {}
        return self._price_evidence


class _FakeSession:
    def __init__(self, connection):
        self._connection = connection

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, statement):
        return self._connection


def _connection(balances):
    return SimpleNamespace(exchange_connection_id=uuid4(), balances=balances)


@pytest.mark.asyncio
async def test_verify_product_readiness_feasible(monkeypatch: pytest.MonkeyPatch) -> None:
    product = _FakeProduct(
        available=True, trading_enabled=True,
        min_order_notional=Decimal("1"), min_order_quantity=Decimal("0.0001"), quantity_increment=Decimal("0.0001"),
    )
    price_evidence = _FakePriceEvidence(Decimal("3000"))
    connection = _connection([
        {"currency": "USD", "available": "25"},
        {"currency": "ETH", "available": "1"},
    ])

    monkeypatch.setattr(
        "app.services.exchange_connections.providers.kraken_spot.KrakenSpotClient",
        lambda: _FakeKrakenClient(product, price_evidence),
    )
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _FakeSession(connection))

    payload = await service.verify_kraken_product_readiness(product_id="ETH-USD", proving_notional_usd="5")

    assert payload["read_only"] is True
    assert payload["order_preview_or_submission_attempted"] is False
    assert payload["kraken_pair_available"] is True
    assert payload["trading_enabled"] is True
    assert payload["feasible_at_proving_notional"] is True
    assert payload["blockers"] == []
    assert payload["sufficient_usd_for_buy"] is True
    assert payload["sufficient_base_asset_for_future_sell"] is True


@pytest.mark.asyncio
async def test_verify_product_readiness_below_kraken_minimum_notional(monkeypatch: pytest.MonkeyPatch) -> None:
    product = _FakeProduct(
        available=True, trading_enabled=True,
        min_order_notional=Decimal("10"), min_order_quantity=None, quantity_increment=None,
    )
    price_evidence = _FakePriceEvidence(Decimal("3000"))
    connection = _connection([{"currency": "USD", "available": "25"}])

    monkeypatch.setattr(
        "app.services.exchange_connections.providers.kraken_spot.KrakenSpotClient",
        lambda: _FakeKrakenClient(product, price_evidence),
    )
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _FakeSession(connection))

    payload = await service.verify_kraken_product_readiness(product_id="ETH-USD", proving_notional_usd="5")

    assert payload["feasible_at_proving_notional"] is False
    assert any("proving_notional_below_kraken_minimum" in item for item in payload["blockers"])


@pytest.mark.asyncio
async def test_verify_product_readiness_trading_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    product = _FakeProduct(
        available=True, trading_enabled=False,
        min_order_notional=Decimal("1"), min_order_quantity=None, quantity_increment=None,
    )
    connection = _connection([{"currency": "USD", "available": "25"}])

    monkeypatch.setattr(
        "app.services.exchange_connections.providers.kraken_spot.KrakenSpotClient",
        lambda: _FakeKrakenClient(product, None),
    )
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _FakeSession(connection))

    payload = await service.verify_kraken_product_readiness(product_id="ETH-USD", proving_notional_usd="5")

    assert payload["feasible_at_proving_notional"] is False
    assert "product_trading_not_enabled" in payload["blockers"]


@pytest.mark.asyncio
async def test_verify_product_readiness_insufficient_usd_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    product = _FakeProduct(
        available=True, trading_enabled=True,
        min_order_notional=Decimal("1"), min_order_quantity=None, quantity_increment=None,
    )
    price_evidence = _FakePriceEvidence(Decimal("3000"))
    connection = _connection([{"currency": "USD", "available": "1"}])

    monkeypatch.setattr(
        "app.services.exchange_connections.providers.kraken_spot.KrakenSpotClient",
        lambda: _FakeKrakenClient(product, price_evidence),
    )
    monkeypatch.setattr(service, "AsyncSessionLocal", lambda: _FakeSession(connection))

    payload = await service.verify_kraken_product_readiness(product_id="ETH-USD", proving_notional_usd="5")

    assert payload["feasible_at_proving_notional"] is False
    assert any("insufficient_usd_balance" in item for item in payload["blockers"])
    assert payload["order_preview_or_submission_attempted"] is False
