from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from app.config import Settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.services.data.http_client import AsyncHTTPClient, ExternalAPIError

PAPER_ALPACA_HOST = "paper-api.alpaca.markets"


@dataclass(frozen=True, slots=True)
class AlpacaPaperOrderResult:
    broker_order_id: str
    status: str
    symbol: str
    side: str
    type: str
    time_in_force: str
    qty: Decimal
    filled_qty: Decimal
    filled_avg_price: Decimal | None
    submitted_at: str | None
    filled_at: str | None
    execution_venue: str = "alpaca_paper"
    is_paper: bool = True


def _ensure_paper_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.hostname != PAPER_ALPACA_HOST:
        raise InvalidRequestError(
            message="Alpaca base URL must be the paper endpoint",
            details={"alpaca_base_url": base_url},
        )


def _format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _parse_order_payload(payload: dict[str, Any]) -> AlpacaPaperOrderResult:
    return AlpacaPaperOrderResult(
        broker_order_id=str(payload["id"]),
        status=str(payload.get("status", "unknown")),
        symbol=str(payload.get("symbol", "")),
        side=str(payload.get("side", "")),
        type=str(payload.get("type", "market")),
        time_in_force=str(payload.get("time_in_force", "day")),
        qty=Decimal(str(payload.get("qty", "0"))),
        filled_qty=Decimal(str(payload.get("filled_qty", "0"))),
        filled_avg_price=(
            Decimal(str(payload["filled_avg_price"]))
            if payload.get("filled_avg_price") not in {None, ""}
            else None
        ),
        submitted_at=payload.get("submitted_at"),
        filled_at=payload.get("filled_at"),
    )


def _alpaca_headers(settings: Settings) -> dict[str, str]:
    if settings.alpaca_api_key_id is None or settings.alpaca_api_secret_key is None:
        raise InvalidRequestError(message="Alpaca paper API credentials are not configured", details={})

    return {
        "APCA-API-KEY-ID": settings.alpaca_api_key_id.get_secret_value(),
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret_key.get_secret_value(),
        "Content-Type": "application/json",
    }


def _map_external_error(exc: ExternalAPIError, *, action: str) -> InvalidRequestError | NotFoundError:
    if exc.status_code == 404:
        return NotFoundError(message=f"Alpaca paper {action} not found", details={"endpoint": exc.endpoint})
    if exc.status_code in {401, 403}:
        return InvalidRequestError(message="Alpaca paper authentication failed", details={"status_code": exc.status_code})
    return InvalidRequestError(
        message=f"Alpaca paper {action} request failed",
        details={"status_code": exc.status_code, "endpoint": exc.endpoint},
    )


async def submit_alpaca_paper_order(
    *,
    settings: Settings,
    client: AsyncHTTPClient,
    symbol: str,
    side: str,
    quantity: Decimal,
    client_order_id: str | None = None,
) -> AlpacaPaperOrderResult:
    _ensure_paper_base_url(settings.alpaca_base_url)

    if side not in {"buy", "sell"}:
        raise InvalidRequestError(message="Invalid side", details={"side": side})
    if quantity <= 0:
        raise InvalidRequestError(message="Quantity must be positive", details={"quantity": _format_decimal(quantity)})

    payload: dict[str, str] = {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "qty": _format_decimal(quantity) or "0",
    }
    if client_order_id is not None:
        payload["client_order_id"] = client_order_id

    try:
        response = await client.request(
            "POST",
            f"{settings.alpaca_base_url.rstrip('/')}/v2/orders",
            headers=_alpaca_headers(settings),
            json=payload,
        )
    except ExternalAPIError as exc:
        raise _map_external_error(exc, action="order submission") from exc

    return _parse_order_payload(response.json())


async def get_alpaca_paper_order(
    *,
    settings: Settings,
    client: AsyncHTTPClient,
    broker_order_id: str,
) -> AlpacaPaperOrderResult:
    _ensure_paper_base_url(settings.alpaca_base_url)

    try:
        response = await client.request(
            "GET",
            f"{settings.alpaca_base_url.rstrip('/')}/v2/orders/{broker_order_id}",
            headers=_alpaca_headers(settings),
        )
    except ExternalAPIError as exc:
        raise _map_external_error(exc, action="order") from exc

    return _parse_order_payload(response.json())