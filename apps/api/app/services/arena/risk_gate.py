from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.arena_cycle_proposal import ArenaCycleProposal
from app.models.arena_risk_gate_decision import ArenaRiskGateDecision
from app.services.arena.contracts import ArenaRiskEvaluationRequest, ArenaRiskEvaluationResult
from app.services.risk.risk_engine import (
    RiskDecisionAction,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
)
from app.services.risk.risk_persistence import RiskDecisionPersistenceRequest, persist_risk_decision


def _stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _build_risk_gate_idempotency_key(request: ArenaRiskEvaluationRequest) -> str:
    payload = {
        "kind": "arena_risk_gate_decision",
        "cycle_id": str(request.cycle_id),
        "proposal_id": str(request.proposal_id),
        "competition_id": str(request.competition_id),
        "tournament_id": str(request.tournament_id),
        "agent_id": str(request.agent_id),
        "action": request.action,
        "symbol": request.symbol,
        "requested_quantity": format(request.requested_quantity, "f"),
        "reference_price": format(request.reference_price, "f"),
        "min_order_notional": format(request.min_order_notional, "f"),
        "qty_step_size": format(request.qty_step_size, "f"),
        "supports_fractional": request.supports_fractional,
        "stop_loss_computable": request.stop_loss_computable,
        "risk_context": {
            "account_equity": format(request.risk_context.account_equity, "f"),
            "start_of_day_equity": format(request.risk_context.start_of_day_equity, "f"),
            "current_equity": format(request.risk_context.current_equity, "f"),
            "max_position_size_pct": format(request.risk_context.max_position_size_pct, "f"),
            "max_daily_loss_pct": format(request.risk_context.max_daily_loss_pct, "f"),
            "high_water_mark_equity": format(request.risk_context.high_water_mark_equity, "f"),
            "max_drawdown_pct": format(request.risk_context.max_drawdown_pct, "f"),
            "consecutive_losses_on_pair": request.risk_context.consecutive_losses_on_pair,
            "cooldown_after_losses": request.risk_context.cooldown_after_losses,
            "last_loss_at": request.risk_context.last_loss_at.isoformat() if request.risk_context.last_loss_at else None,
            "cooldown_duration_minutes": format(request.risk_context.cooldown_duration_minutes, "f"),
            "evaluation_time": request.risk_context.evaluation_time.isoformat(),
            "data_is_stale": request.risk_context.data_is_stale,
            "data_has_gaps": request.risk_context.data_has_gaps,
            "global_kill_switch_engaged_state": request.risk_context.global_kill_switch_engaged_state,
            "global_kill_switch_rearm_required": request.risk_context.global_kill_switch_rearm_required,
            "account_kill_switch_engaged_state": request.risk_context.account_kill_switch_engaged_state,
            "account_kill_switch_rearm_required": request.risk_context.account_kill_switch_rearm_required,
            "global_kill_switch_state_observed": request.risk_context.global_kill_switch_state_observed,
            "account_kill_switch_state_observed": request.risk_context.account_kill_switch_state_observed,
        },
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _map_decision_action(action: RiskDecisionAction) -> str:
    if action == RiskDecisionAction.APPROVE:
        return "approve"
    if action == RiskDecisionAction.RESIZE:
        return "resize"
    return "reject"


def _validate_request(request: ArenaRiskEvaluationRequest) -> None:
    if request.action not in {"buy", "sell", "wait"}:
        raise InvalidRequestError("Arena proposal action must be one of buy, sell, wait")
    if request.requested_quantity <= Decimal("0"):
        raise InvalidRequestError("Arena proposal quantity must be positive")
    if request.reference_price <= Decimal("0"):
        raise InvalidRequestError("Arena proposal reference price must be positive")
    if request.qty_step_size <= Decimal("0"):
        raise InvalidRequestError("Arena proposal qty step size must be positive")
    if request.min_order_notional <= Decimal("0"):
        raise InvalidRequestError("Arena proposal minimum order notional must be positive")
    if not request.risk_context.global_kill_switch_state_observed:
        raise InvalidRequestError("Arena risk gate requires observed global kill switch state")
    if not request.risk_context.account_kill_switch_state_observed:
        raise InvalidRequestError("Arena risk gate requires observed account kill switch state")


def _build_risk_request(request: ArenaRiskEvaluationRequest) -> RiskEvaluationRequest:
    context = request.risk_context
    deterministic_signal_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"arena-risk:{request.cycle_id}:{request.proposal_id}:{request.agent_id}",
    )
    deterministic_asset_id = uuid.uuid5(uuid.NAMESPACE_URL, f"arena-symbol:{request.symbol}")
    deterministic_paper_account_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"arena-competition:{request.competition_id}",
    )

    return RiskEvaluationRequest(
        signal_id=deterministic_signal_id,
        paper_account_id=deterministic_paper_account_id,
        asset_id=deterministic_asset_id,
        side="buy" if request.action == "buy" else "sell",
        quantity=request.requested_quantity,
        min_order_notional=request.min_order_notional,
        qty_step_size=request.qty_step_size,
        supports_fractional=request.supports_fractional,
        account_equity=context.account_equity,
        start_of_day_equity=context.start_of_day_equity,
        current_equity=context.current_equity,
        max_position_size_pct=context.max_position_size_pct,
        max_daily_loss_pct=context.max_daily_loss_pct,
        high_water_mark_equity=context.high_water_mark_equity,
        max_drawdown_pct=context.max_drawdown_pct,
        consecutive_losses_on_pair=context.consecutive_losses_on_pair,
        cooldown_after_losses=context.cooldown_after_losses,
        last_loss_at=context.last_loss_at,
        cooldown_duration_minutes=context.cooldown_duration_minutes,
        evaluation_time=context.evaluation_time,
        data_is_stale=context.data_is_stale,
        data_has_gaps=context.data_has_gaps,
        global_kill_switch_engaged_state=context.global_kill_switch_engaged_state,
        global_kill_switch_rearm_required=context.global_kill_switch_rearm_required,
        global_kill_switch_rearmed_by_human=None,
        account_kill_switch_engaged_state=context.account_kill_switch_engaged_state,
        account_kill_switch_rearm_required=context.account_kill_switch_rearm_required,
        account_kill_switch_rearmed_by_human=None,
        global_kill_switch_state_observed=context.global_kill_switch_state_observed,
        account_kill_switch_state_observed=context.account_kill_switch_state_observed,
    )


async def evaluate_arena_candidate_action(
    *,
    db: AsyncSession,
    request: ArenaRiskEvaluationRequest,
) -> ArenaRiskEvaluationResult:
    _validate_request(request)

    proposal = await db.scalar(
        select(ArenaCycleProposal)
        .where(
            ArenaCycleProposal.id == request.proposal_id,
            ArenaCycleProposal.cycle_id == request.cycle_id,
            ArenaCycleProposal.competition_id == request.competition_id,
            ArenaCycleProposal.tournament_id == request.tournament_id,
            ArenaCycleProposal.agent_id == request.agent_id,
        )
        .limit(1)
    )
    if proposal is None:
        raise InvalidRequestError("Arena risk gate proposal not found in cycle scope")

    idempotency_key = _build_risk_gate_idempotency_key(request)
    existing = await db.scalar(
        select(ArenaRiskGateDecision)
        .where(ArenaRiskGateDecision.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        risk_event = existing.provenance.get("persisted_risk_event", {})
        return ArenaRiskEvaluationResult(
            risk_gate_decision_id=existing.id,
            cycle_id=existing.cycle_id,
            proposal_id=existing.proposal_id,
            competition_id=existing.competition_id,
            tournament_id=existing.tournament_id,
            agent_id=existing.agent_id,
            action=existing.decision_action,
            approved_quantity=Decimal(existing.approved_quantity),
            reason_code=existing.reason_code,
            persisted_risk_event_type=str(risk_event.get("type", "risk_decision")),
            persisted_risk_event_action=str(risk_event.get("action", existing.decision_action)),
            persisted_risk_event_reason_code=risk_event.get("reason_code"),
            provenance=existing.provenance,
            decision_steps=list(existing.risk_steps),
        )

    risk_result = evaluate_signal_risk(
        request=_build_risk_request(request),
        reference_price=request.reference_price,
        context=RiskEvaluationContext(has_computable_stop_loss=request.stop_loss_computable),
    )
    decision_action = _map_decision_action(risk_result.action)
    decision_steps = [
        {
            "step": step.step,
            "status": step.status,
            "reason_code": step.reason_code,
        }
        for step in risk_result.steps
    ]
    persisted = await persist_risk_decision(
        db=db,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=None,
            signal_id=None,
            actor=request.actor,
            evaluation_result=risk_result,
            state_change_action="arena_risk_gate_evaluated",
            state_change_entity_type="arena_cycle_proposal",
            state_change_entity_id=request.proposal_id,
            state_before={
                "proposal_action": request.action,
                "requested_quantity": format(request.requested_quantity, "f"),
            },
            state_after={
                "decision_action": decision_action,
                "approved_quantity": format(risk_result.approved_quantity, "f"),
                "reason_code": risk_result.reason_code,
            },
        ),
    )

    provenance = {
        **request.provenance,
        "risk_engine": {
            "reason_code": risk_result.reason_code,
            "steps": decision_steps,
            "source": "evaluate_signal_risk",
        },
        "persisted_risk_event": {
            "type": persisted.risk_event_type,
            "action": persisted.risk_event_action,
            "reason_code": persisted.risk_event_reason_code,
        },
    }

    async with db.begin():
        decision = ArenaRiskGateDecision(
            idempotency_key=idempotency_key,
            cycle_id=request.cycle_id,
            proposal_id=request.proposal_id,
            competition_id=request.competition_id,
            tournament_id=request.tournament_id,
            agent_id=request.agent_id,
            decision_action=decision_action,
            reason_code=risk_result.reason_code,
            approved_quantity=risk_result.approved_quantity,
            risk_steps=decision_steps,
            provenance=provenance,
            created_at=request.risk_context.evaluation_time,
        )
        db.add(decision)
        await db.flush()

    return ArenaRiskEvaluationResult(
        risk_gate_decision_id=decision.id,
        cycle_id=request.cycle_id,
        proposal_id=request.proposal_id,
        competition_id=request.competition_id,
        tournament_id=request.tournament_id,
        agent_id=request.agent_id,
        action=decision_action,
        approved_quantity=risk_result.approved_quantity,
        reason_code=risk_result.reason_code,
        persisted_risk_event_type=persisted.risk_event_type,
        persisted_risk_event_action=persisted.risk_event_action,
        persisted_risk_event_reason_code=persisted.risk_event_reason_code,
        provenance=provenance,
        decision_steps=decision_steps,
    )