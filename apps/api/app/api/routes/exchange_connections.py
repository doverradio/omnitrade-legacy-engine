from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.exchange_connections import (
    ExchangeConnectionListResponse,
    ExchangeConnectionResponse,
    SaveExchangeConnectionRequest,
    TestExchangeConnectionRequest,
    TestExchangeConnectionResponse,
)
from app.services.exchange_connections import (
    create_exchange_connection,
    list_exchange_connections,
    refresh_exchange_account,
    refresh_exchange_balances,
    refresh_exchange_permissions,
    test_exchange_credentials,
)

router = APIRouter(prefix="/exchange-connections", tags=["exchange-connections"])


@router.get("", response_model=ExchangeConnectionListResponse)
async def get_exchange_connections(db: AsyncSession = Depends(get_db)) -> ExchangeConnectionListResponse:
    return await list_exchange_connections(db=db)


@router.post("/test", response_model=TestExchangeConnectionResponse)
async def test_connection(
    payload: TestExchangeConnectionRequest,
) -> TestExchangeConnectionResponse:
    return await test_exchange_credentials(payload=payload)


@router.post("", response_model=ExchangeConnectionResponse, status_code=201)
async def save_exchange_connection(
    payload: SaveExchangeConnectionRequest,
    db: AsyncSession = Depends(get_db),
) -> ExchangeConnectionResponse:
    return await create_exchange_connection(db=db, payload=payload)


@router.post("/{exchange_connection_id}/refresh/balances", response_model=ExchangeConnectionResponse)
async def refresh_connection_balances(
    exchange_connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ExchangeConnectionResponse:
    return await refresh_exchange_balances(db=db, exchange_connection_id=exchange_connection_id)


@router.post("/{exchange_connection_id}/refresh/account", response_model=ExchangeConnectionResponse)
async def refresh_connection_account(
    exchange_connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ExchangeConnectionResponse:
    return await refresh_exchange_account(db=db, exchange_connection_id=exchange_connection_id)


@router.post("/{exchange_connection_id}/refresh/permissions", response_model=ExchangeConnectionResponse)
async def refresh_connection_permissions(
    exchange_connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ExchangeConnectionResponse:
    return await refresh_exchange_permissions(db=db, exchange_connection_id=exchange_connection_id)
