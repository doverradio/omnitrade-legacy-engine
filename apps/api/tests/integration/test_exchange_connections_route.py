from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.exchange_connections import (
    ExchangeBalanceResponse,
    ExchangeConnectionListResponse,
    ExchangeConnectionResponse,
    ExchangeCredentialMaskResponse,
    ExchangeReadinessCheckResponse,
)


def _connection_response() -> ExchangeConnectionResponse:
    return ExchangeConnectionResponse(
        exchange_connection_id=UUID("11111111-1111-1111-1111-111111111111"),
        provider="coinbase_advanced",
        provider_label="Coinbase Advanced",
        connection_name="Primary Coinbase",
        environment="sandbox",
        status="connected",
        credentials_valid=True,
        credential_mask=ExchangeCredentialMaskResponse(
            api_key="******1234",
            api_secret="********",
            passphrase="********",
        ),
        api_permissions=["view"],
        account_status="active",
        balances=[
            ExchangeBalanceResponse(currency="USD", available="100", reserved="0", total="100"),
            ExchangeBalanceResponse(currency="BTC", available="0", reserved="0", total="0"),
            ExchangeBalanceResponse(currency="ETH", available="0", reserved="0", total="0"),
        ],
        total_equity_usd="100",
        last_successful_sync_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
        last_heartbeat_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
        last_api_error=None,
        readiness_checks=[
            ExchangeReadinessCheckResponse(
                code="exchange_connected",
                label="Exchange Connected",
                ok=True,
                detail="Connected",
            )
        ],
        updated_at=datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc),
    )


def test_exchange_connections_route_shape(monkeypatch) -> None:
    app = create_app()

    async def _list_stub(*, db):
        _ = db
        return ExchangeConnectionListResponse(items=[_connection_response()])

    monkeypatch.setattr("app.api.routes.exchange_connections.list_exchange_connections", _list_stub)

    with TestClient(app) as client:
        response = client.get("/exchange-connections")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["provider"] == "coinbase_advanced"
    assert payload["items"][0]["credential_mask"]["api_secret"] == "********"
    assert payload["items"][0]["balances"][0]["currency"] == "USD"
