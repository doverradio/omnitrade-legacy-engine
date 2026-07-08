from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.services.strategies import strategy_registry


@dataclass(slots=True)
class StrategyActivationResult:
    strategy_id: uuid.UUID
    name: str
    active: bool
    previous_active_strategy_id: uuid.UUID | None = None
    previous_active_strategy_name: str | None = None


async def activate_strategy(
    db_session: AsyncSession,
    *,
    strategy_id: uuid.UUID,
    activated_by: str,
) -> StrategyActivationResult:
    strategy = await db_session.scalar(select(Strategy).where(Strategy.id == strategy_id).limit(1))
    if strategy is None:
        raise NotFoundError(message="Strategy not found", details={"strategy_id": str(strategy_id)})

    if not strategy_registry.has(strategy.slug):
        raise InvalidRequestError(
            message="Strategy registry does not recognize strategy slug",
            details={"strategy_id": str(strategy.id), "slug": strategy.slug},
        )

    parameter_set_exists = await db_session.scalar(
        select(ParameterSet.id).where(ParameterSet.strategy_id == strategy.id).limit(1)
    )
    if parameter_set_exists is None:
        raise InvalidRequestError(
            message="Strategy requires at least one parameter set before activation",
            details={"strategy_id": str(strategy.id), "slug": strategy.slug},
        )

    active_strategies = (
        await db_session.execute(
            select(Strategy)
            .where(Strategy.is_active.is_(True))
            .where(Strategy.id != strategy.id)
            .order_by(Strategy.created_at.asc())
        )
    ).scalars().all()

    previous_active_strategy = active_strategies[0] if active_strategies else None

    if active_strategies:
        active_strategy_ids = [active_strategy.id for active_strategy in active_strategies]
        await db_session.execute(
            update(Strategy)
            .where(Strategy.id.in_(active_strategy_ids))
            .values(is_active=False)
        )

    strategy.is_active = True

    audit_before_state: dict[str, object] | None = None
    if previous_active_strategy is not None:
        audit_before_state = {
            "previous_active_strategy": {
                "id": str(previous_active_strategy.id),
                "name": previous_active_strategy.name,
                "slug": previous_active_strategy.slug,
            },
            "deactivated_strategy_ids": [str(active_strategy.id) for active_strategy in active_strategies],
        }

    db_session.add(
        AuditLog(
            actor=activated_by,
            action="STRATEGY_ACTIVATED",
            entity_type="strategy",
            entity_id=strategy.id,
            before_state=audit_before_state,
            after_state={
                "strategy_id": str(strategy.id),
                "name": strategy.name,
                "slug": strategy.slug,
                "active": True,
                "activated_by": activated_by,
            },
        )
    )

    await db_session.commit()
    return StrategyActivationResult(
        strategy_id=strategy.id,
        name=strategy.name,
        active=True,
        previous_active_strategy_id=previous_active_strategy.id if previous_active_strategy is not None else None,
        previous_active_strategy_name=previous_active_strategy.name if previous_active_strategy is not None else None,
    )