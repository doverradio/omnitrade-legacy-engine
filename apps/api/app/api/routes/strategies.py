from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.core.security import get_current_user
from app.db.session import get_db
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.schemas.strategy import StrategyActivationResponse, StrategyListResponse, StrategyResponse
from app.services.strategies.promotion import activate_strategy

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=StrategyListResponse)
async def list_strategies(
    is_active: bool | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> StrategyListResponse:
    statement = select(Strategy).order_by(Strategy.created_at.asc())
    if is_active is not None:
        statement = statement.where(Strategy.is_active == is_active)

    strategies = (await db.execute(statement)).scalars().all()

    strategy_ids = [strategy.id for strategy in strategies]
    default_params_by_strategy_id: dict = {}
    if strategy_ids:
        parameter_sets = (
            await db.execute(
                select(ParameterSet)
                .where(ParameterSet.strategy_id.in_(strategy_ids))
                .order_by(ParameterSet.created_at.desc())
            )
        ).scalars().all()

        for parameter_set in parameter_sets:
            if parameter_set.strategy_id not in default_params_by_strategy_id:
                default_params_by_strategy_id[parameter_set.strategy_id] = parameter_set.params

    return StrategyListResponse(
        items=[
            StrategyResponse(
                id=strategy.id,
                name=strategy.name,
                slug=strategy.slug,
                is_active=strategy.is_active,
                module_version=strategy.module_version,
                default_params=default_params_by_strategy_id.get(strategy.id),
            )
            for strategy in strategies
        ]
    )


@router.post("/{strategy_id}/activate", response_model=StrategyActivationResponse)
async def activate_strategy_route(
    strategy_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: dict[str, str] | None = Depends(get_current_user),
) -> StrategyActivationResponse:
    if current_user is None:
        raise UnauthorizedError(message="Authentication required", details={})

    result = await activate_strategy(
        db,
        strategy_id=strategy_id,
        activated_by=current_user["id"],
    )
    return StrategyActivationResponse(
        status="activated",
        strategy_id=result.strategy_id,
        name=result.name,
        active=result.active,
    )
