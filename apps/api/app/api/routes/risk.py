from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.risk import (
    AccountRiskStatusResponse,
    KillSwitchRequest,
    KillSwitchResponse,
    KillSwitchStateResponse,
    RiskRulesPatchRequest,
    RiskRulesResponse,
    RiskRulesValues,
    RiskStatusResponse,
    RiskUsageResponse,
)
from app.services.risk.risk_monitor import (
    disable_kill_switch,
    enable_kill_switch,
    get_risk_rules,
    get_risk_status,
    patch_risk_rules,
)

router = APIRouter(prefix="/risk", tags=["risk"])


def _as_decimal(value: Decimal | int) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@router.get("/status", response_model=RiskStatusResponse)
async def risk_status(account_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> RiskStatusResponse:
    status = await get_risk_status(db=db, account_id=account_id)

    return RiskStatusResponse(
        global_kill_switch=KillSwitchStateResponse(
            engaged=status.global_engaged,
            engaged_at=status.global_engaged_at,
            engaged_by=status.global_engaged_by,
            reason=status.global_reason,
        ),
        account=AccountRiskStatusResponse(
            account_id=status.account_id,
            trading_paused=status.account_engaged,
            paused_reason=status.account_reason,
            daily_loss=RiskUsageResponse(
                used=status.daily_loss.used,
                limit=status.daily_loss.limit,
                pct_used=status.daily_loss.pct_used,
            ),
            drawdown=RiskUsageResponse(
                used=status.drawdown.used,
                limit=status.drawdown.limit,
                pct_used=status.drawdown.pct_used,
            ),
            active_cooldowns=[],
            active_no_trade_zones=[],
        ),
    )


@router.post("/kill-switch/enable", response_model=KillSwitchResponse)
async def enable_kill_switch_route(
    payload: KillSwitchRequest,
    db: AsyncSession = Depends(get_db),
) -> KillSwitchResponse:
    result = await enable_kill_switch(
        db=db,
        scope=payload.scope,
        account_id=payload.account_id,
        reason=payload.reason,
        confirm=payload.confirm,
        actor=payload.actor,
    )

    return KillSwitchResponse(
        scope=result.scope,
        account_id=result.account_id,
        engaged=True,
        engaged_at=result.changed_at,
        engaged_by=result.actor,
    )


@router.post("/kill-switch/disable", response_model=KillSwitchResponse)
async def disable_kill_switch_route(
    payload: KillSwitchRequest,
    db: AsyncSession = Depends(get_db),
) -> KillSwitchResponse:
    result = await disable_kill_switch(
        db=db,
        scope=payload.scope,
        account_id=payload.account_id,
        reason=payload.reason,
        confirm=payload.confirm,
        actor=payload.actor,
    )

    return KillSwitchResponse(
        scope=result.scope,
        account_id=result.account_id,
        engaged=False,
        disengaged_at=result.changed_at,
        disengaged_by=result.actor,
    )


@router.get("/rules", response_model=RiskRulesResponse)
async def risk_rules(account_id: uuid.UUID | None = None, db: AsyncSession = Depends(get_db)) -> RiskRulesResponse:
    result = await get_risk_rules(db=db, account_id=account_id)

    return RiskRulesResponse(
        account_id=result.account_id,
        is_override=result.is_override,
        rules=RiskRulesValues(
            max_position_size_pct=_as_decimal(result.rules["max_position_size_pct"]),
            max_daily_loss_pct=_as_decimal(result.rules["max_daily_loss_pct"]),
            max_drawdown_pct=_as_decimal(result.rules["max_drawdown_pct"]),
            default_stop_loss_pct=_as_decimal(result.rules["default_stop_loss_pct"]),
            cooldown_after_losses=int(result.rules["cooldown_after_losses"]),
            cooldown_duration_hours=int(result.rules["cooldown_duration_hours"]),
        ),
        system_defaults=RiskRulesValues(
            max_position_size_pct=_as_decimal(result.system_defaults["max_position_size_pct"]),
            max_daily_loss_pct=_as_decimal(result.system_defaults["max_daily_loss_pct"]),
            max_drawdown_pct=_as_decimal(result.system_defaults["max_drawdown_pct"]),
            default_stop_loss_pct=_as_decimal(result.system_defaults["default_stop_loss_pct"]),
            cooldown_after_losses=int(result.system_defaults["cooldown_after_losses"]),
            cooldown_duration_hours=int(result.system_defaults["cooldown_duration_hours"]),
        ),
    )


@router.patch("/rules", response_model=RiskRulesResponse)
async def patch_rules(payload: RiskRulesPatchRequest, db: AsyncSession = Depends(get_db)) -> RiskRulesResponse:
    result = await patch_risk_rules(
        db=db,
        account_id=payload.account_id,
        rules_patch=payload.rules.model_dump(),
        confirm_loosening=payload.confirm_loosening,
        actor=payload.actor,
    )

    return RiskRulesResponse(
        account_id=result.account_id,
        is_override=result.is_override,
        rules=RiskRulesValues(
            max_position_size_pct=_as_decimal(result.rules["max_position_size_pct"]),
            max_daily_loss_pct=_as_decimal(result.rules["max_daily_loss_pct"]),
            max_drawdown_pct=_as_decimal(result.rules["max_drawdown_pct"]),
            default_stop_loss_pct=_as_decimal(result.rules["default_stop_loss_pct"]),
            cooldown_after_losses=int(result.rules["cooldown_after_losses"]),
            cooldown_duration_hours=int(result.rules["cooldown_duration_hours"]),
        ),
        system_defaults=RiskRulesValues(
            max_position_size_pct=_as_decimal(result.system_defaults["max_position_size_pct"]),
            max_daily_loss_pct=_as_decimal(result.system_defaults["max_daily_loss_pct"]),
            max_drawdown_pct=_as_decimal(result.system_defaults["max_drawdown_pct"]),
            default_stop_loss_pct=_as_decimal(result.system_defaults["default_stop_loss_pct"]),
            cooldown_after_losses=int(result.system_defaults["cooldown_after_losses"]),
            cooldown_duration_hours=int(result.system_defaults["cooldown_duration_hours"]),
        ),
    )