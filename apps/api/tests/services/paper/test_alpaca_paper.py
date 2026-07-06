from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import Settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.services.data.http_client import ExternalAPIError
from app.services.paper.alpaca_paper import get_alpaca_paper_order, submit_alpaca_paper_order


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeHTTPClient:
    def __init__(self, response: _FakeResponse | None = None, error: ExternalAPIError | None = None) -> None:
        self._response = response
        self._error = error

    async def request(self, method: str, url: str, **kwargs: object) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _settings() -> Settings:
    return Settings(
        alpaca_api_key_id="paper-key",
        alpaca_api_secret_key="paper-secret",
        alpaca_base_url="https://paper-api.alpaca.markets",
    )


@pytest.mark.asyncio
async def test_submit_alpaca_paper_order_success() -> None:
    payload = {
        "id": "broker-order-1",
        "status": "filled",
        "symbol": "AAPL",
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
        "qty": "0.5",
        "filled_qty": "0.5",
        "filled_avg_price": "210.10",
        "submitted_at": "2026-07-06T00:00:00Z",
        "filled_at": "2026-07-06T00:00:01Z",
    }

    result = await submit_alpaca_paper_order(
        settings=_settings(),
        client=_FakeHTTPClient(response=_FakeResponse(payload)),
        symbol="AAPL",
        side="buy",
        quantity=Decimal("0.5"),
    )

    assert result.broker_order_id == "broker-order-1"
    assert result.execution_venue == "alpaca_paper"
    assert result.is_paper is True
    assert result.qty == Decimal("0.5")
    assert result.filled_avg_price == Decimal("210.10")


@pytest.mark.asyncio
async def test_submit_alpaca_paper_order_rejects_non_paper_base_url() -> None:
    settings = Settings(
        alpaca_api_key_id="paper-key",
        alpaca_api_secret_key="paper-secret",
        alpaca_base_url="https://api.alpaca.markets",
    )

    with pytest.raises(InvalidRequestError) as exc_info:
        await submit_alpaca_paper_order(
            settings=settings,
            client=_FakeHTTPClient(response=_FakeResponse({"id": "unused"})),
            symbol="AAPL",
            side="buy",
            quantity=Decimal("1"),
        )

    assert "paper endpoint" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_alpaca_paper_order_maps_not_found() -> None:
    error = ExternalAPIError(
        message="External API request failed",
        endpoint="https://paper-api.alpaca.markets/v2/orders/does-not-exist",
        status_code=404,
        response_body="not found",
    )

    with pytest.raises(NotFoundError):
        await get_alpaca_paper_order(
            settings=_settings(),
            client=_FakeHTTPClient(error=error),
            broker_order_id="does-not-exist",
        )
