from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationResult


@dataclass(frozen=True, slots=True)
class RiskDecisionPersistenceRequest:
    paper_account_id: uuid.UUID | None
    signal_id: uuid.UUID | None
    actor: str
    evaluation_result: RiskEvaluationResult
    state_change_action: str | None = None
    state_change_entity_type: str | None = None
    state_change_entity_id: uuid.UUID | None = None
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RiskDecisionPersistenceResult:
    risk_event_action: str
    risk_event_type: str
    risk_event_reason_code: str | None
    audit_written: bool


def _serialize_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _map_risk_event_type(reason_code: str | None, action: RiskDecisionAction) -> str:
    if action == RiskDecisionAction.APPROVE:
        return "risk_approval"

    if reason_code is None:
        return "risk_decision"

    reason_to_type = {
        "global_kill_switch_engaged": "kill_switch",
        "global_kill_switch_state_unknown": "kill_switch",
        "global_kill_switch_rearm_state_unknown": "kill_switch",
        "global_kill_switch_requires_manual_rearm": "kill_switch",
        "account_kill_switch_engaged": "kill_switch",
        "account_kill_switch_state_unknown": "kill_switch",
        "account_kill_switch_rearm_state_unknown": "kill_switch",
        "account_kill_switch_requires_manual_rearm": "kill_switch",
        "account_trading_paused": "kill_switch",
        "asset_in_no_trade_zone": "no_trade_zone",
        "strategy_asset_cooldown_active": "cooldown",
        "max_daily_loss_breached": "daily_loss_limit",
        "max_drawdown_breached": "drawdown_limit",
        "position_resized_by_risk_engine": "position_limit",
        "position_below_minimum_order_size": "minimum_viable_order",
    }

    return reason_to_type.get(reason_code, "risk_decision")


def _map_risk_action_taken(action: RiskDecisionAction) -> str:
    if action == RiskDecisionAction.APPROVE:
        return "approved"
    if action == RiskDecisionAction.RESIZE:
        return "resized"
    return "blocked"


def _build_detail_payload(request: RiskDecisionPersistenceRequest) -> dict[str, Any]:
    result = request.evaluation_result
    return {
        "decision": result.action.value,
        "reason_code": result.reason_code,
        "approved_quantity": _serialize_decimal(result.approved_quantity),
        "steps": [
            {
                "step": step.step,
                "status": step.status,
                "reason_code": step.reason_code,
            }
            for step in result.steps
        ],
    }


async def persist_risk_decision(
    *,
    db: AsyncSession,
    request: RiskDecisionPersistenceRequest,
) -> RiskDecisionPersistenceResult:
    if db.in_transaction():
        return await _persist_risk_decision_without_begin(db=db, request=request)

    async with db.begin():
        return await _persist_risk_decision_without_begin(db=db, request=request)


async def _persist_risk_decision_without_begin(
    *,
    db: AsyncSession,
    request: RiskDecisionPersistenceRequest,
) -> RiskDecisionPersistenceResult:
    result = request.evaluation_result
    event_type = _map_risk_event_type(result.reason_code, result.action)
    action_taken = _map_risk_action_taken(result.action)

    risk_event = RiskEvent(
        paper_account_id=request.paper_account_id,
        related_signal_id=request.signal_id,
        event_type=event_type,
        action_taken=action_taken,
        detail=_build_detail_payload(request),
    )

    audit_written = False

    db.add(risk_event)

    if request.state_change_action is not None:
        audit = AuditLog(
            actor=request.actor,
            action=request.state_change_action,
            entity_type=request.state_change_entity_type or "risk",
            entity_id=request.state_change_entity_id,
            before_state=request.state_before,
            after_state=request.state_after,
        )
        db.add(audit)
        audit_written = True

    return RiskDecisionPersistenceResult(
        risk_event_action=action_taken,
        risk_event_type=event_type,
        risk_event_reason_code=result.reason_code,
        audit_written=audit_written,
    )