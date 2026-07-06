from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.schemas.paper import (
    CreatePaperAccountRequest,
    CreatePaperAccountResponse,
    PaperAccountResponse,
    PositionResponse,
    ResetPaperAccountRequest,
    ResetPaperAccountResponse,
)
from app.services.paper.accounting import build_account_snapshot

router = APIRouter(prefix="/paper", tags=["paper"])

_DEFAULT_OWNER_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@router.get("/account", response_model=PaperAccountResponse)
async def get_paper_account(
    account_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaperAccountResponse:
    account = await _load_account(db=db, account_id=account_id)

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=account.id,
        starting_balance=account.starting_balance,
    )

    return PaperAccountResponse(
        id=account.id,
        name=account.name,
        asset_class=account.asset_class,
        starting_balance=account.starting_balance,
        current_cash_balance=snapshot.cash_balance,
        equity=snapshot.equity,
        equity_return_usd=snapshot.equity_return_usd,
        equity_return_pct=snapshot.equity_return_pct,
        positions=[
            PositionResponse(
                asset_id=position.asset_id,
                symbol=position.symbol,
                quantity=position.quantity,
                avg_entry_price=position.avg_entry_price,
                unrealized_pnl_usd=position.unrealized_pnl_usd,
                unrealized_pnl_pct=position.unrealized_pnl_pct,
            )
            for position in snapshot.positions
        ],
    )


@router.post("/account", response_model=CreatePaperAccountResponse, status_code=201)
async def create_paper_account(
    payload: CreatePaperAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> CreatePaperAccountResponse:
    if payload.asset_class not in {"crypto", "stock"}:
        raise InvalidRequestError(message="Invalid asset_class", details={"asset_class": payload.asset_class})

    if payload.starting_balance < Decimal("25"):
        raise InvalidRequestError(
            message="starting_balance must be at least 25",
            details={"starting_balance": format(payload.starting_balance, "f")},
        )

    account = PaperAccount(
        owner_user_id=_DEFAULT_OWNER_USER_ID,
        name=payload.name.strip(),
        asset_class=payload.asset_class,
        starting_balance=payload.starting_balance,
        current_cash_balance=payload.starting_balance,
        is_active=True,
    )

    db.add(account)
    await db.commit()
    await db.refresh(account)

    return CreatePaperAccountResponse(
        id=account.id,
        name=account.name,
        asset_class=account.asset_class,
        starting_balance=account.starting_balance,
        current_cash_balance=account.current_cash_balance,
        is_active=account.is_active,
    )


@router.post("/reset", response_model=ResetPaperAccountResponse)
async def reset_paper_account(
    payload: ResetPaperAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> ResetPaperAccountResponse:
    if payload.confirm is not True:
        raise InvalidRequestError(message="Reset requires confirm=true", details={"confirm": payload.confirm})

    account = await _load_account(db=db, account_id=payload.account_id)

    await db.execute(delete(Trade).where(Trade.paper_account_id == account.id))
    account.current_cash_balance = account.starting_balance
    await db.commit()

    return ResetPaperAccountResponse(
        account_id=account.id,
        current_cash_balance=account.current_cash_balance,
        positions=[],
    )


async def _load_account(*, db: AsyncSession, account_id: uuid.UUID | None) -> PaperAccount:
    if account_id is not None:
        account = await db.scalar(select(PaperAccount).where(PaperAccount.id == account_id))
        if account is None:
            raise NotFoundError(message="Paper account not found", details={"account_id": str(account_id)})
        return account

    account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.is_active.is_(True))
        .order_by(PaperAccount.created_at.desc())
        .limit(1)
    )
    if account is None:
        raise NotFoundError(message="Paper account not found", details={})

    return account
