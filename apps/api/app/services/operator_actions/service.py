from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.operator_action import OperatorAction
from app.services.operator_actions.registry import get_action_handler, registered_action_types

MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 20

_TERMINAL_STATUSES = {"SUCCEEDED", "BLOCKED", "FAILED", "CANCELLED", "EXPIRED"}
_AUDIT_ACTION_BY_STATUS = {
    "SUCCEEDED": "operator_action.completed",
    "BLOCKED": "operator_action.blocked",
    "FAILED": "operator_action.failed",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _view(action: OperatorAction, *, status: str, result: dict[str, Any] | None, blocked_reason: str | None, failure_reason: str | None) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "action_type": action.action_type,
        "status": status,
        "actor": action.actor,
        "idempotency_key": action.idempotency_key,
        "parameters": action.parameters,
        "result": result,
        "linked_resource_type": action.linked_resource_type,
        "linked_resource_id": action.linked_resource_id,
        "blocked_reason": blocked_reason,
        "failure_reason": failure_reason,
        "requested_at": action.requested_at,
        "accepted_at": action.accepted_at,
        "started_at": action.started_at,
        "completed_at": action.completed_at,
        "expires_at": action.expires_at,
    }


async def _apply_projection(*, db: AsyncSession, action: OperatorAction) -> dict[str, Any]:
    """Derives the action's current status/result from its linked domain
    resource and persists ONLY when the status actually changed -- mirrors
    app.services.controlled_proof.service.get_controlled_proof_view's own
    "recompute fresh, write back only on change" pattern one layer up, so a
    GET never produces audit noise."""
    handler = get_action_handler(action.action_type)
    if handler is None or action.status in _TERMINAL_STATUSES:
        return _view(action, status=action.status, result=action.result, blocked_reason=action.blocked_reason, failure_reason=action.failure_reason)

    projection = await handler.project(db, action)
    if projection.status == action.status:
        return _view(action, status=projection.status, result=projection.result, blocked_reason=projection.blocked_reason, failure_reason=projection.failure_reason)

    before_status = action.status
    now = _utcnow()
    action.status = projection.status
    action.result = projection.result
    action.blocked_reason = projection.blocked_reason
    action.failure_reason = projection.failure_reason
    action.updated_at = now
    if projection.status == "IN_PROGRESS" and action.started_at is None:
        action.started_at = now
    if projection.status in _TERMINAL_STATUSES and action.completed_at is None:
        action.completed_at = now
    db.add(AuditLog(
        actor="system:operator_action_projection", action=_AUDIT_ACTION_BY_STATUS.get(projection.status, "operator_action.status_transitioned"),
        entity_type="operator_action", entity_id=action.action_id,
        before_state={"status": before_status},
        after_state={"status": projection.status, "result": projection.result},
    ))
    await db.commit()
    return _view(action, status=action.status, result=action.result, blocked_reason=action.blocked_reason, failure_reason=action.failure_reason)


async def submit_operator_action(
    *, db: AsyncSession, action_type: str, idempotency_key: str, parameters: dict[str, Any], actor: str,
) -> dict[str, Any]:
    action_type = str(action_type or "").strip()
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key:
        raise InvalidRequestError(message="idempotency_key is required", details={})

    existing = await db.scalar(select(OperatorAction).where(OperatorAction.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.action_type != action_type:
            raise ConflictError(
                message="idempotency_key already used for a different action_type",
                details={"idempotency_key": idempotency_key, "existing_action_type": existing.action_type},
            )
        return await _apply_projection(db=db, action=existing)

    handler = get_action_handler(action_type)
    if handler is None:
        raise InvalidRequestError(
            message="Unknown action_type", details={"action_type": action_type, "supported_action_types": list(registered_action_types())},
        )

    try:
        validated_parameters = handler.parameters_model(**parameters)
    except PydanticValidationError as exc:
        raise InvalidRequestError(
            message="Invalid or forbidden parameters for this action_type",
            details={"action_type": action_type, "errors": exc.errors(include_url=False, include_context=False)},
        ) from exc

    action = OperatorAction(
        action_type=action_type,
        status="REQUESTED",
        actor=actor,
        idempotency_key=idempotency_key,
        parameters=validated_parameters.model_dump(mode="json"),
        requested_at=_utcnow(),
    )
    db.add(action)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        replay = await db.scalar(select(OperatorAction).where(OperatorAction.idempotency_key == idempotency_key))
        if replay is not None:
            return await _apply_projection(db=db, action=replay)
        raise ConflictError(message="Operator action idempotency conflict", details={}) from exc

    db.add(AuditLog(
        actor=actor, action="operator_action.requested", entity_type="operator_action", entity_id=action.action_id,
        before_state=None, after_state={"action_type": action_type, "status": "REQUESTED"},
    ))
    await db.commit()

    try:
        submission = await handler.submit(db, action, validated_parameters)
    except Exception as exc:
        action.status = "FAILED"
        action.failure_reason = str(exc)
        action.completed_at = _utcnow()
        action.updated_at = _utcnow()
        db.add(AuditLog(
            actor=actor, action="operator_action.failed", entity_type="operator_action", entity_id=action.action_id,
            before_state={"status": "REQUESTED"}, after_state={"status": "FAILED", "failure_reason": action.failure_reason},
        ))
        await db.commit()
        raise

    now = _utcnow()
    action.status = "ACCEPTED"
    action.accepted_at = now
    action.updated_at = now
    action.linked_resource_type = submission.linked_resource_type
    action.linked_resource_id = submission.linked_resource_id
    db.add(AuditLog(
        actor=actor, action="operator_action.accepted", entity_type="operator_action", entity_id=action.action_id,
        before_state={"status": "REQUESTED"},
        after_state={"status": "ACCEPTED", "linked_resource_type": submission.linked_resource_type, "linked_resource_id": str(submission.linked_resource_id)},
    ))
    await db.commit()

    return await _apply_projection(db=db, action=action)


async def get_operator_action(*, db: AsyncSession, action_id: uuid.UUID) -> dict[str, Any]:
    action = await db.scalar(select(OperatorAction).where(OperatorAction.action_id == action_id))
    if action is None:
        raise NotFoundError(message="Operator action not found", details={"action_id": str(action_id)})
    return await _apply_projection(db=db, action=action)


async def list_operator_actions(
    *, db: AsyncSession, action_type: str | None = None, status: str | None = None, limit: int | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = min(max(int(limit or DEFAULT_LIST_LIMIT), 1), MAX_LIST_LIMIT)
    query = select(OperatorAction)
    if action_type:
        query = query.where(OperatorAction.action_type == action_type)
    if status:
        query = query.where(OperatorAction.status == status)
    query = query.order_by(OperatorAction.requested_at.desc()).limit(bounded_limit)
    rows = (await db.scalars(query)).all()
    return [await _apply_projection(db=db, action=row) for row in rows]
