from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.decision_record import DecisionRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.risk_event import RiskEvent
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignState,
    CommissionedControlPlaneMutationRequest,
    CommissionedControlPlaneMutationResponse,
    CommissionedControlPlaneStatusResponse,
)

_COMMISSIONED_STATE_KEY = "commissioned_seed_campaign"
_CONTROL_ACTION_ALLOWED_SOURCE_STATES: dict[str, set[str]] = {
    "acknowledge": {"RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "ACTIVE_POSITION", "BUY_RECONCILIATION_PENDING", "SELL_RECONCILIATION_PENDING"},
    "pause": {"READY", "COMMISSIONED", "BUY_PENDING", "BUY_SUBMITTED", "BUY_RECONCILIATION_PENDING", "ACTIVE_POSITION", "SELL_EVALUATION"},
    "resume": {"READY", "COMMISSIONED", "BUY_PENDING", "BUY_SUBMITTED", "BUY_RECONCILIATION_PENDING", "ACTIVE_POSITION", "SELL_EVALUATION"},
    "cancel": {"READY", "COMMISSIONED", "BUY_PENDING", "BUY_SUBMITTED", "BUY_RECONCILIATION_PENDING", "ACTIVE_POSITION", "SELL_EVALUATION", "RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED"},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _read_commissioned_blob(*, definition: CapitalCampaignDefinition) -> dict[str, Any]:
    metadata = dict(definition.metadata_evidence or {})
    blob = metadata.get(_COMMISSIONED_STATE_KEY)
    if isinstance(blob, dict):
        return blob
    return {}


def _write_commissioned_blob(*, definition: CapitalCampaignDefinition, blob: dict[str, Any]) -> None:
    metadata = dict(definition.metadata_evidence or {})
    metadata[_COMMISSIONED_STATE_KEY] = blob
    definition.metadata_evidence = metadata
    definition.updated_at = _utcnow()


async def _load_definition_and_runtime_for_update(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int,
) -> tuple[CapitalCampaignDefinition, CapitalCampaign]:
    definition = await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == version)
        .with_for_update()
        .limit(1)
    )
    if definition is None:
        raise NotFoundError(
            message="Capital campaign definition not found",
            details={"campaign_id": str(campaign_id), "version": version},
        )

    runtime = await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == campaign_id)
        .with_for_update()
        .limit(1)
    )
    if runtime is None:
        raise NotFoundError(
            message="Runtime capital campaign not found",
            details={"campaign_id": str(campaign_id)},
        )

    return definition, runtime


async def _load_definition_and_runtime(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int,
) -> tuple[CapitalCampaignDefinition, CapitalCampaign]:
    definition = await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == version)
        .limit(1)
    )
    if definition is None:
        raise NotFoundError(
            message="Capital campaign definition not found",
            details={"campaign_id": str(campaign_id), "version": version},
        )

    runtime = await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == campaign_id)
        .limit(1)
    )
    if runtime is None:
        raise NotFoundError(
            message="Runtime capital campaign not found",
            details={"campaign_id": str(campaign_id)},
        )

    return definition, runtime


async def _load_decision_summary(*, db: AsyncSession, decision_id: str | None) -> dict[str, Any] | None:
    if not decision_id:
        return None
    decision = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.decision_id == decision_id)
        .limit(1)
    )
    if decision is None:
        return None
    return {
        "decision_id": str(decision.decision_id),
        "timestamp": decision.timestamp,
        "outcome": decision.outcome,
        "trade_accepted": bool(decision.trade_accepted),
        "execution_details": decision.execution_details if isinstance(decision.execution_details, dict) else {},
    }


async def _load_risk_summary(*, db: AsyncSession, risk_event_id: str | None) -> dict[str, Any] | None:
    if not risk_event_id:
        return None
    event = await db.scalar(
        select(RiskEvent)
        .where(RiskEvent.risk_event_id == risk_event_id)
        .limit(1)
    )
    if event is None:
        return None
    return {
        "risk_event_id": str(event.risk_event_id),
        "action_taken": event.action_taken,
        "reason": event.reason,
        "timestamp": event.timestamp,
        "risk_score": None if event.risk_score is None else str(event.risk_score),
    }


async def _load_audit_rows(*, db: AsyncSession, campaign_id: UUID, limit: int) -> list[AuditLog]:
    rows = await db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "capital_campaign")
        .where(AuditLog.entity_id == campaign_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    return list(rows)


def _normalize_state(blob: dict[str, Any]) -> CommissionedCampaignState:
    return str(blob.get("state") or "DRAFT")  # type: ignore[return-value]


def _pending_actions(*, state: str, lifecycle_recommendation: dict[str, Any], operator_control: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if state == "RECONCILIATION_REQUIRED":
        actions.append("acknowledge_reconciliation_required")
    if lifecycle_recommendation.get("recommendation_type") in {"SELL_NOW", "STOP_LOSS_EXIT", "MAX_HOLD_EXIT"}:
        actions.append("review_exit_recommendation")
    if not bool(operator_control.get("paused", False)):
        actions.append("pause")
    if bool(operator_control.get("paused", False)):
        actions.append("resume")
    if not bool(operator_control.get("cancelled", False)):
        actions.append("cancel")
    actions.append("acknowledge")
    deduped = []
    for item in actions:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _request_signature(*, request: CommissionedControlPlaneMutationRequest) -> dict[str, Any]:
    return {
        "campaign_id": str(request.campaign_id),
        "version": int(request.version),
        "action": str(request.action),
        "actor": str(request.actor),
        "reason": request.reason,
    }


def _normalize_seen_row(value: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(value, dict):
        return None, None
    request_signature = value.get("request_signature") if isinstance(value.get("request_signature"), dict) else None
    response = value.get("response") if isinstance(value.get("response"), dict) else None
    if response is not None:
        return request_signature, response
    return None, value


def _validate_action_source_state(*, action: str, state: str) -> None:
    allowed = _CONTROL_ACTION_ALLOWED_SOURCE_STATES.get(action)
    if not allowed:
        raise InvalidRequestError(message="Unsupported control-plane action", details={"action": action})
    if state not in allowed:
        raise InvalidRequestError(
            message="Control-plane mutation is not allowed for current source state",
            details={
                "action": action,
                "current_state": state,
                "allowed_source_states": sorted(allowed),
            },
        )


async def get_commissioned_control_plane_status(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int,
) -> CommissionedControlPlaneStatusResponse:
    definition, runtime = await _load_definition_and_runtime(db=db, campaign_id=campaign_id, version=version)
    blob = _read_commissioned_blob(definition=definition)
    state = _normalize_state(blob)

    commissioning = blob.get("commissioning") if isinstance(blob.get("commissioning"), dict) else {}
    entry_execution = blob.get("entry_execution") if isinstance(blob.get("entry_execution"), dict) else {}
    ownership = blob.get("ownership_reconciliation") if isinstance(blob.get("ownership_reconciliation"), dict) else {}
    exit_recommendation = blob.get("exit_recommendation") if isinstance(blob.get("exit_recommendation"), dict) else {}
    operator_control = blob.get("operator_control") if isinstance(blob.get("operator_control"), dict) else {}

    live_crypto_order_id = (ownership.get("correlation_ids") or {}).get("live_crypto_order_id")
    if live_crypto_order_id is None:
        live_crypto_order_id = entry_execution.get("live_crypto_order_id")

    live_order_summary: dict[str, Any] | None = None
    if live_crypto_order_id:
        live_order = await db.scalar(
            select(LiveCryptoOrder)
            .where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id)
            .limit(1)
        )
        if live_order is not None:
            live_order_summary = {
                "live_crypto_order_id": str(live_order.live_crypto_order_id),
                "status": live_order.status,
                "provider_order_id": live_order.provider_order_id,
                "provider_status": live_order.provider_status,
                "updated_at": live_order.updated_at,
            }

    entry_decision_summary = await _load_decision_summary(db=db, decision_id=entry_execution.get("decision_record_id"))
    exit_last = exit_recommendation.get("last_recommendation") if isinstance(exit_recommendation.get("last_recommendation"), dict) else {}
    exit_decision_summary = await _load_decision_summary(db=db, decision_id=exit_last.get("decision_record_id"))

    entry_risk_summary = await _load_risk_summary(db=db, risk_event_id=entry_execution.get("risk_event_id"))
    exit_risk_summary = await _load_risk_summary(db=db, risk_event_id=exit_last.get("risk_event_id"))

    audit_rows = await _load_audit_rows(db=db, campaign_id=campaign_id, limit=50)
    audit_summary = {
        "count": len(audit_rows),
        "latest": [
            {
                "timestamp": item.created_at,
                "action": item.action,
                "actor": item.actor,
            }
            for item in audit_rows[:10]
        ],
    }

    transition_history = blob.get("transition_history") if isinstance(blob.get("transition_history"), list) else []
    timeline = list(transition_history)
    for item in audit_rows:
        timeline.append(
            {
                "timestamp": item.created_at.isoformat() if item.created_at is not None else None,
                "action": item.action,
                "actor": item.actor,
                "kind": "audit",
            }
        )
    timeline.sort(key=lambda row: str(row.get("timestamp") or row.get("transitioned_at") or ""))

    pending_operator_actions = _pending_actions(
        state=state,
        lifecycle_recommendation=exit_last,
        operator_control=operator_control,
    )

    readiness_summary = {
        "available": False,
        "reason": "readiness request evidence is not persisted in commissioned metadata",
        "state": state,
    }
    preview_summary = {
        "available": False,
        "preview_identity_hash": commissioning.get("preview_identity_hash"),
        "preview_expires_at": commissioning.get("commissioned_until"),
        "reason": "preview payload is not persisted in commissioned metadata",
    }

    production_eligible = (
        state in {"READY", "COMMISSIONED", "BUY_RECONCILIATION_PENDING", "ACTIVE_POSITION"}
        and not bool(operator_control.get("paused", False))
        and not bool(operator_control.get("cancelled", False))
    )

    blockers: list[str] = []
    warnings: list[str] = []
    ownership_blockers = ownership.get("blockers") if isinstance(ownership.get("blockers"), list) else []
    blockers.extend(str(item) for item in ownership_blockers)
    if state in {"RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "FAILED_CLOSED"}:
        blockers.append(f"state:{state}")
    if not production_eligible:
        warnings.append("future_production_activation_not_eligible")
    if not bool(ownership.get("ownership_proven")) and state == "ACTIVE_POSITION":
        blockers.append("active_position_without_ownership_proof")
    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))

    return CommissionedControlPlaneStatusResponse(
        campaign_id=campaign_id,
        version=version,
        state=state,
        readiness=readiness_summary,
        preview=preview_summary,
        commissioning_status={
            "commissioning_identity": commissioning.get("commissioning_identity"),
            "commissioned_until": commissioning.get("commissioned_until"),
            "commissioned_by": commissioning.get("commissioned_by"),
            "authority_classification": commissioning.get("authority_classification"),
            "strategy_signal_classification": commissioning.get("strategy_signal_classification"),
        },
        lifecycle_recommendation=exit_last,
        active_position_summary={
            "ownership_proven": ownership.get("ownership_proven"),
            "position_identity": ownership.get("position_identity"),
            "provider_order_id": ownership.get("provider_order_id"),
            "provider_fill_ids": ownership.get("provider_fill_ids") or [],
            "executed_quantity": ownership.get("executed_quantity"),
            "average_entry_price": ownership.get("average_entry_price"),
            "total_buy_fees": ownership.get("total_buy_fees"),
        },
        reconciliation_status={
            "campaign_state": state,
            "ownership_blockers": ownership.get("blockers") or [],
            "buy_reconciliation": {
                "status": "reconciled" if bool(ownership.get("ownership_proven")) else "pending_or_blocked",
                "details": ownership,
            },
            "sell_reconciliation": {
                "status": "not_applicable_recommendation_only",
                "details": {},
            },
            "live_order": live_order_summary,
            "attributable_remaining_quantity": ownership.get("attributable_remaining_quantity"),
        },
        decision_record_summary={
            "entry": entry_decision_summary,
            "exit_latest": exit_decision_summary,
        },
        risk_engine_summary={
            "entry": entry_risk_summary,
            "exit_latest": exit_risk_summary,
        },
        audit_summary=audit_summary,
        pending_operator_actions=pending_operator_actions,
        campaign_timeline=timeline,
        campaign_history={
            "transition_history": transition_history,
            "operator_control_history": operator_control.get("history") or [],
            "exit_recommendation_seen_idempotency_keys": sorted((exit_recommendation.get("seen_idempotency_keys") or {}).keys()),
        },
        dry_run_status={
            "has_live_order": live_order_summary is not None,
            "is_dry_run": bool(live_order_summary and str(live_order_summary.get("status") or "").startswith("DRY_RUN")),
            "status": None if live_order_summary is None else live_order_summary.get("status"),
        },
        future_production_activation_eligibility={
            "eligible": production_eligible,
            "reason": (
                "eligible"
                if production_eligible
                else "blocked_by_pause_or_cancel_or_state"
            ),
        },
        blockers=blockers,
        warnings=warnings,
        read_only=True,
        no_execution=True,
        generated_at=_utcnow(),
    )


async def mutate_commissioned_control_plane(
    *,
    db: AsyncSession,
    request: CommissionedControlPlaneMutationRequest,
) -> CommissionedControlPlaneMutationResponse:
    definition, _runtime = await _load_definition_and_runtime_for_update(
        db=db,
        campaign_id=request.campaign_id,
        version=request.version,
    )
    blob = _read_commissioned_blob(definition=definition)
    state = _normalize_state(blob)

    operator_control = blob.get("operator_control") if isinstance(blob.get("operator_control"), dict) else {}
    seen = operator_control.get("seen_idempotency_keys") if isinstance(operator_control.get("seen_idempotency_keys"), dict) else {}
    if not str(request.actor or "").strip():
        raise InvalidRequestError(message="actor is required", details={"actor": request.actor})
    if not str(request.idempotency_key or "").strip():
        raise InvalidRequestError(message="idempotency_key is required", details={"idempotency_key": request.idempotency_key})

    request_signature = _request_signature(request=request)
    if request.idempotency_key in seen:
        stored_signature, replay = _normalize_seen_row(seen[request.idempotency_key])
        if stored_signature is not None and stored_signature != request_signature:
            raise InvalidRequestError(
                message="Changed-intent idempotency key reuse is not allowed",
                details={
                    "idempotency_key": request.idempotency_key,
                    "stored_request_signature": stored_signature,
                    "received_request_signature": request_signature,
                },
            )
        if replay is None:
            raise InvalidRequestError(
                message="Idempotency record is malformed; refusing replay",
                details={"idempotency_key": request.idempotency_key},
            )
        return CommissionedControlPlaneMutationResponse.model_validate(replay).model_copy(update={"replayed": True})

    paused = bool(operator_control.get("paused", False))
    cancelled = bool(operator_control.get("cancelled", False))
    blockers: list[str] = []
    accepted = True

    _validate_action_source_state(action=request.action, state=state)

    if request.action == "pause":
        paused = True
    elif request.action == "resume":
        if cancelled:
            accepted = False
            blockers.append("cannot_resume_cancelled_campaign")
        else:
            paused = False
    elif request.action == "cancel":
        cancelled = True
        paused = True
    elif request.action == "acknowledge":
        pass
    else:
        raise InvalidRequestError(message="Unsupported control-plane action", details={"action": request.action})

    operator_control_updated = {
        **operator_control,
        "paused": paused,
        "cancelled": cancelled,
        "acknowledged_at": _utcnow().isoformat() if request.action == "acknowledge" else operator_control.get("acknowledged_at"),
        "history": [
            *(operator_control.get("history") if isinstance(operator_control.get("history"), list) else []),
            {
                "action": request.action,
                "actor": request.actor,
                "reason": request.reason,
                "accepted": accepted,
                "timestamp": _utcnow().isoformat(),
            },
        ],
    }

    pending_operator_actions = _pending_actions(
        state=state,
        lifecycle_recommendation=(blob.get("exit_recommendation") or {}).get("last_recommendation")
        if isinstance(blob.get("exit_recommendation"), dict)
        else {},
        operator_control=operator_control_updated,
    )

    response = CommissionedControlPlaneMutationResponse(
        campaign_id=request.campaign_id,
        version=request.version,
        action=request.action,
        accepted=accepted,
        replayed=False,
        state=state,
        operator_control={
            "paused": paused,
            "cancelled": cancelled,
            "acknowledged_at": operator_control_updated.get("acknowledged_at"),
        },
        pending_operator_actions=pending_operator_actions,
        no_execution=True,
        updated_at=_utcnow(),
        blockers=blockers,
    )

    seen_updated = {
        **seen,
        request.idempotency_key: {
            "request_signature": request_signature,
            "response": response.model_dump(mode="json"),
        },
    }
    operator_control_updated["seen_idempotency_keys"] = seen_updated

    blob_updated = _read_commissioned_blob(definition=definition)
    blob_updated["operator_control"] = operator_control_updated
    _write_commissioned_blob(definition=definition, blob=blob_updated)

    db.add(
        AuditLog(
            actor=request.actor,
            action="commissioned_seed_campaign.control_plane_mutation",
            entity_type="capital_campaign",
            entity_id=request.campaign_id,
            before_state={"operator_control": operator_control},
            after_state={"operator_control": operator_control_updated, "action": request.action, "accepted": accepted},
        )
    )
    await db.flush()
    await db.commit()
    return response
