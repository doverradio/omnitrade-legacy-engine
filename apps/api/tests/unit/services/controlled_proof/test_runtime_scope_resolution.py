from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.controlled_proof import service as controlled_proof_service
from tests.support.real_sqlite_session import real_sqlite_session

_ALLOWED_CAMPAIGN_ID = controlled_proof_service.ALLOWED_CAMPAIGN_ID
_ALLOWED_PROVIDER = controlled_proof_service.ALLOWED_PROVIDER
_ALLOWED_ENVIRONMENT = controlled_proof_service.ALLOWED_ENVIRONMENT

_ALL_TABLES = [
    CapitalCampaign.__table__,
    ExchangeConnection.__table__,
    LiveTradingProfile.__table__,
    PaperAccount.__table__,
]


@asynccontextmanager
async def _real_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_ALL_TABLES) as session:
        yield session


async def _seed_paper_account_and_campaign(session: AsyncSession) -> uuid.UUID:
    paper_account_id = uuid.uuid4()
    session.add(PaperAccount(
        id=paper_account_id, owner_user_id=uuid.uuid4(), name="controlled-proof-account",
        asset_class="crypto", starting_balance=Decimal("25"), current_cash_balance=Decimal("25"),
    ))
    session.add(CapitalCampaign(
        uuid=_ALLOWED_CAMPAIGN_ID, owner="test", name="controlled-proof-campaign", status="READY",
        campaign_type="TEST", exchange=_ALLOWED_PROVIDER, paper_account_id=paper_account_id,
        definition_campaign_id=_ALLOWED_CAMPAIGN_ID, definition_version=1,
        starting_capital=Decimal("25"), current_equity=Decimal("25"),
    ))
    await session.flush()
    return paper_account_id


def _add_connection(session: AsyncSession, *, status: str = "connected", credentials_valid: bool = True) -> uuid.UUID:
    connection_id = uuid.uuid4()
    session.add(ExchangeConnection(
        exchange_connection_id=connection_id, provider=_ALLOWED_PROVIDER, connection_name=f"kraken-{connection_id}",
        environment=_ALLOWED_ENVIRONMENT, status=status,
        credentials_encrypted="enc", api_key_masked="****", api_secret_masked="****",
        credentials_valid=credentials_valid, balances=[{"currency": "USD", "available": "25"}],
    ))
    return connection_id


@pytest.mark.asyncio
async def test_resolves_full_scope_when_infrastructure_is_correctly_provisioned() -> None:
    async with _real_session() as session:
        paper_account_id = await _seed_paper_account_and_campaign(session)
        profile_id = uuid.uuid4()
        session.add(LiveTradingProfile(id=profile_id, paper_account_id=paper_account_id, provenance_metadata={}))
        connection_id = _add_connection(session)
        await session.flush()

        scope = await controlled_proof_service.resolve_controlled_proof_runtime_scope(db=session)

    assert scope.paper_account_id == paper_account_id
    assert scope.live_trading_profile_id == profile_id
    assert scope.exchange_connection_id == connection_id


@pytest.mark.asyncio
async def test_raises_when_runtime_campaign_is_not_provisioned() -> None:
    async with _real_session() as session:
        with pytest.raises(InvalidRequestError, match="Controlled Proof runtime campaign"):
            await controlled_proof_service.resolve_controlled_proof_runtime_scope(db=session)


@pytest.mark.asyncio
async def test_raises_when_no_live_trading_profile_exists_for_the_paper_account() -> None:
    async with _real_session() as session:
        await _seed_paper_account_and_campaign(session)
        _add_connection(session)
        await session.flush()

        with pytest.raises(InvalidRequestError, match="no live trading profile found"):
            await controlled_proof_service.resolve_controlled_proof_runtime_scope(db=session)


@pytest.mark.asyncio
async def test_raises_when_zero_connected_exchange_connections_exist() -> None:
    async with _real_session() as session:
        paper_account_id = await _seed_paper_account_and_campaign(session)
        session.add(LiveTradingProfile(id=uuid.uuid4(), paper_account_id=paper_account_id, provenance_metadata={}))
        await session.flush()

        with pytest.raises(InvalidRequestError, match="0 connected"):
            await controlled_proof_service.resolve_controlled_proof_runtime_scope(db=session)


@pytest.mark.asyncio
async def test_raises_when_multiple_connected_exchange_connections_exist() -> None:
    async with _real_session() as session:
        paper_account_id = await _seed_paper_account_and_campaign(session)
        session.add(LiveTradingProfile(id=uuid.uuid4(), paper_account_id=paper_account_id, provenance_metadata={}))
        _add_connection(session)
        _add_connection(session)
        await session.flush()

        with pytest.raises(InvalidRequestError, match="2 connected"):
            await controlled_proof_service.resolve_controlled_proof_runtime_scope(db=session)


@pytest.mark.asyncio
async def test_raises_when_the_only_connection_is_not_connected_or_not_credentials_valid() -> None:
    async with _real_session() as session:
        paper_account_id = await _seed_paper_account_and_campaign(session)
        session.add(LiveTradingProfile(id=uuid.uuid4(), paper_account_id=paper_account_id, provenance_metadata={}))
        _add_connection(session, status="disconnected", credentials_valid=True)
        await session.flush()

        with pytest.raises(InvalidRequestError, match="0 connected"):
            await controlled_proof_service.resolve_controlled_proof_runtime_scope(db=session)
