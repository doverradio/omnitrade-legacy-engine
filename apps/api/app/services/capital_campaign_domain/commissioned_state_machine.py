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
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignState,
    CommissionedCampaignTransitionRequest,
    CommissionedCampaignTransitionResponse,
)


_COMMISSIONED_STATE_KEY = "commissioned_seed_campaign"
_TRANSITION_AUDIT_ACTION = "commissioned_seed_campaign.transition"

_COMMISSIONED_TERMINAL_STATES: set[CommissionedCampaignState] = {
    "COMPLETED",
    "EXPIRED",
    "MANUAL_REVIEW_REQUIRED",
    "FAILED_CLOSED",
    "CANCELLED",
}

_COMMISSIONED_TRANSITIONS: dict[CommissionedCampaignState, set[CommissionedCampaignState]] = {
    "DRAFT": {"READY", "CANCELLED"},
    "READY": {"COMMISSIONED", "EXPIRED", "CANCELLED"},
    "COMMISSIONED": {"BUY_PENDING", "EXPIRED", "CANCELLED"},
    "BUY_PENDING": {"BUY_SUBMITTED", "VETOED", "FAILED_CLOSED", "CANCELLED"},
    "BUY_SUBMITTED": {"BUY_RECONCILIATION_PENDING", "RECONCILIATION_REQUIRED", "FAILED_CLOSED"},
    "BUY_RECONCILIATION_PENDING": {"ACTIVE_POSITION", "RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "FAILED_CLOSED"},
    "ACTIVE_POSITION": {"SELL_EVALUATION", "RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "FAILED_CLOSED"},
    "SELL_EVALUATION": {"SELL_PENDING", "ACTIVE_POSITION", "MANUAL_REVIEW_REQUIRED", "FAILED_CLOSED"},
    "SELL_PENDING": {"SELL_SUBMITTED", "VETOED", "FAILED_CLOSED", "CANCELLED"},
    "SELL_SUBMITTED": {"SELL_RECONCILIATION_PENDING", "RECONCILIATION_REQUIRED", "FAILED_CLOSED"},
    "SELL_RECONCILIATION_PENDING": {"COMPLETED", "RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "FAILED_CLOSED"},
    "COMPLETED": set(),
    "VETOED": {"BUY_PENDING", "SELL_EVALUATION", "CANCELLED", "FAILED_CLOSED"},
    "EXPIRED": set(),
    "RECONCILIATION_REQUIRED": {"BUY_RECONCILIATION_PENDING", "SELL_RECONCILIATION_PENDING", "MANUAL_REVIEW_REQUIRED", "FAILED_CLOSED"},
    "MANUAL_REVIEW_REQUIRED": set(),
    "FAILED_CLOSED": set(),
    "CANCELLED": set(),
}

_DEFINITION_STATUS_BY_COMMISSIONED_STATE: dict[CommissionedCampaignState, str] = {
    "DRAFT": "DRAFT",
    "READY": "READY",
    "COMMISSIONED": "ACTIVE",
    "BUY_PENDING": "ACTIVE",
    "BUY_SUBMITTED": "ACTIVE",
    "BUY_RECONCILIATION_PENDING": "ACTIVE",
    "ACTIVE_POSITION": "ACTIVE",
    "SELL_EVALUATION": "ACTIVE",
    "SELL_PENDING": "ACTIVE",
    "SELL_SUBMITTED": "ACTIVE",
    "SELL_RECONCILIATION_PENDING": "ACTIVE",
    "COMPLETED": "COMPLETED",
    "VETOED": "MANUAL_REVIEW_REQUIRED",
    "EXPIRED": "CANCELED",
    "RECONCILIATION_REQUIRED": "MANUAL_REVIEW_REQUIRED",
    "MANUAL_REVIEW_REQUIRED": "MANUAL_REVIEW_REQUIRED",
    "FAILED_CLOSED": "MANUAL_REVIEW_REQUIRED",
    "CANCELLED": "CANCELED",
}

_RUNTIME_STATUS_BY_COMMISSIONED_STATE: dict[CommissionedCampaignState, str] = {
    "DRAFT": "DRAFT",
    "READY": "READY",
    "COMMISSIONED": "RUNNING",
    "BUY_PENDING": "RUNNING",
    "BUY_SUBMITTED": "RUNNING",
    "BUY_RECONCILIATION_PENDING": "RUNNING",
    "ACTIVE_POSITION": "RUNNING",
    "SELL_EVALUATION": "RUNNING",
    "SELL_PENDING": "RUNNING",
    "SELL_SUBMITTED": "RUNNING",
    "SELL_RECONCILIATION_PENDING": "RUNNING",
    "COMPLETED": "COMPLETED",
    "VETOED": "PAUSED",
    "EXPIRED": "ARCHIVED",
    "RECONCILIATION_REQUIRED": "PAUSED",
    "MANUAL_REVIEW_REQUIRED": "PAUSED",
    "FAILED_CLOSED": "PAUSED",
    "CANCELLED": "ARCHIVED",
}

_IDEMPOTENCY_INTENT_FIELDS = (
    "target_state",
    "expected_current_state",
    "actor",
    "reason",
    "authority_metadata",
    "evidence_metadata",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InvalidRequestError(message=f"{field_name} is required", details={"field": field_name})
    return normalized


def _read_commissioned_blob(*, definition: CapitalCampaignDefinition) -> dict[str, Any]:
    metadata = dict(definition.metadata_evidence or {})
    blob = metadata.get(_COMMISSIONED_STATE_KEY)
    if not isinstance(blob, dict):
        return {
            "state": "DRAFT",
            "authority_metadata": None,
            "evidence_metadata": [],
            "transition_history": [],
            "seen_idempotency_keys": {},
            "updated_at": None,
        }

    return {
        "state": str(blob.get("state") or "DRAFT"),
        "authority_metadata": blob.get("authority_metadata"),
        "evidence_metadata": list(blob.get("evidence_metadata") or []),
        "transition_history": list(blob.get("transition_history") or []),
        "seen_idempotency_keys": dict(blob.get("seen_idempotency_keys") or {}),
        "updated_at": blob.get("updated_at"),
    }


def _build_idempotency_intent(*, request: CommissionedCampaignTransitionRequest) -> dict[str, Any]:
    return {
        "target_state": request.target_state,
        "expected_current_state": request.expected_current_state,
        "actor": request.actor.strip(),
        "reason": request.reason.strip(),
        "authority_metadata": None if request.authority_metadata is None else request.authority_metadata.model_dump(mode="json"),
        "evidence_metadata": [item.model_dump(mode="json") for item in request.evidence_metadata],
    }


def _validate_runtime_mapping(
    *,
    definition: CapitalCampaignDefinition,
    runtime: CapitalCampaign | None,
    commissioned_state: CommissionedCampaignState,
) -> None:
    expected_definition_status = _DEFINITION_STATUS_BY_COMMISSIONED_STATE[commissioned_state]
    if definition.status != expected_definition_status:
        raise InvalidRequestError(
            message="Commissioned state metadata is inconsistent with definition status",
            details={
                "commissioned_state": commissioned_state,
                "definition_status": definition.status,
                "expected_definition_status": expected_definition_status,
            },
        )

    if runtime is not None:
        expected_runtime_status = _RUNTIME_STATUS_BY_COMMISSIONED_STATE[commissioned_state]
        if runtime.status != expected_runtime_status:
            raise InvalidRequestError(
                message="Commissioned state metadata is inconsistent with runtime status",
                details={
                    "commissioned_state": commissioned_state,
                    "runtime_status": runtime.status,
                    "expected_runtime_status": expected_runtime_status,
                },
            )


def _validate_transition(*, current_state: CommissionedCampaignState, target_state: CommissionedCampaignState) -> None:
    if current_state in _COMMISSIONED_TERMINAL_STATES and current_state != target_state:
        raise InvalidRequestError(
            message="Terminal commissioned campaign state is immutable",
            details={"current_state": current_state, "target_state": target_state},
        )

    allowed = _COMMISSIONED_TRANSITIONS.get(current_state)
    if allowed is None:
        raise InvalidRequestError(
            message="Unknown commissioned campaign state",
            details={"current_state": current_state},
        )

    if target_state not in allowed:
        raise InvalidRequestError(
            message="Invalid commissioned campaign state transition",
            details={"current_state": current_state, "target_state": target_state, "allowed": sorted(allowed)},
        )


async def _record_transition_audit(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    actor: str,
    previous_state: CommissionedCampaignState,
    current_state: CommissionedCampaignState,
    reason: str,
    idempotency_key: str | None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=_TRANSITION_AUDIT_ACTION,
            entity_type="capital_campaign",
            entity_id=campaign_id,
            before_state={"commissioned_state": previous_state},
            after_state={
                "commissioned_state": current_state,
                "reason": reason,
                "idempotency_key": idempotency_key,
            },
        )
    )


async def transition_commissioned_campaign_state(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int,
    request: CommissionedCampaignTransitionRequest,
) -> CommissionedCampaignTransitionResponse:
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

    actor = _normalize_text(request.actor, field_name="actor")
    reason = _normalize_text(request.reason, field_name="reason")

    blob = _read_commissioned_blob(definition=definition)
    current_state = blob["state"]
    target_state = request.target_state
    _validate_runtime_mapping(definition=definition, runtime=runtime, commissioned_state=current_state)

    if request.expected_current_state is not None and request.expected_current_state != current_state:
        raise InvalidRequestError(
            message="Expected commissioned campaign state mismatch",
            details={"expected_current_state": request.expected_current_state, "actual_current_state": current_state},
        )

    idempotency_key = None if request.idempotency_key is None else request.idempotency_key.strip()
    seen_idempotency_keys = dict(blob["seen_idempotency_keys"])
    request_intent = _build_idempotency_intent(request=request)

    if idempotency_key:
        replay_entry = seen_idempotency_keys.get(idempotency_key)
        if isinstance(replay_entry, dict):
            replay_intent = replay_entry.get("intent")
            if replay_intent != request_intent:
                raise InvalidRequestError(
                    message="Idempotency key reuse must match the original transition intent",
                    details={
                        "idempotency_key": idempotency_key,
                        "original_intent": replay_intent,
                        "new_intent": request_intent,
                        "required_fields": list(_IDEMPOTENCY_INTENT_FIELDS),
                    },
                )
            replay_previous = str(replay_entry.get("previous_state") or current_state)
            replay_current = str(replay_entry.get("current_state") or current_state)
            return CommissionedCampaignTransitionResponse(
                campaign_id=campaign_id,
                version=version,
                previous_state=replay_previous,
                current_state=replay_current,
                replayed=True,
                transition_count=len(blob["transition_history"]),
                metadata_evidence=dict(definition.metadata_evidence or {}),
            )

    if target_state == current_state:
        raise InvalidRequestError(
            message="Duplicate commissioned campaign transition rejected",
            details={"state": current_state},
        )

    _validate_transition(current_state=current_state, target_state=target_state)

    transition_timestamp = _utcnow().isoformat()
    transition_record = {
        "previous_state": current_state,
        "current_state": target_state,
        "actor": actor,
        "reason": reason,
        "idempotency_key": idempotency_key,
        "transitioned_at": transition_timestamp,
    }

    transition_history = list(blob["transition_history"])
    transition_history.append(transition_record)

    evidence_metadata = list(blob["evidence_metadata"])
    if request.evidence_metadata:
        evidence_metadata.extend([item.model_dump(mode="json") for item in request.evidence_metadata])

    authority_metadata = blob["authority_metadata"]
    if request.authority_metadata is not None:
        requested_authority_metadata = request.authority_metadata.model_dump(mode="json")
        if authority_metadata is not None and authority_metadata != requested_authority_metadata:
            raise InvalidRequestError(
                message="Commissioned authority metadata is immutable once recorded",
                details={
                    "current_authority_metadata": authority_metadata,
                    "requested_authority_metadata": requested_authority_metadata,
                },
            )
        authority_metadata = requested_authority_metadata

    if idempotency_key:
        seen_idempotency_keys[idempotency_key] = {
            "previous_state": current_state,
            "current_state": target_state,
            "transitioned_at": transition_timestamp,
            "intent": request_intent,
        }

    updated_blob = {
        "state": target_state,
        "authority_metadata": authority_metadata,
        "evidence_metadata": evidence_metadata,
        "transition_history": transition_history,
        "seen_idempotency_keys": seen_idempotency_keys,
        "updated_at": transition_timestamp,
    }

    metadata = dict(definition.metadata_evidence or {})
    metadata[_COMMISSIONED_STATE_KEY] = updated_blob
    definition.metadata_evidence = metadata
    definition.status = _DEFINITION_STATUS_BY_COMMISSIONED_STATE[target_state]
    definition.updated_at = _utcnow()

    if runtime is not None:
        runtime.status = _RUNTIME_STATUS_BY_COMMISSIONED_STATE[target_state]
        runtime.updated_at = _utcnow()

    await _record_transition_audit(
        db=db,
        campaign_id=campaign_id,
        actor=actor,
        previous_state=current_state,
        current_state=target_state,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    await db.flush()
    await db.commit()

    return CommissionedCampaignTransitionResponse(
        campaign_id=campaign_id,
        version=version,
        previous_state=current_state,
        current_state=target_state,
        replayed=False,
        transition_count=len(transition_history),
        metadata_evidence=metadata,
    )


def validate_commissioned_state_transition(
    *,
    current_state: CommissionedCampaignState,
    target_state: CommissionedCampaignState,
) -> None:
    _validate_transition(current_state=current_state, target_state=target_state)


def commissioned_state_expected_statuses(*, commissioned_state: CommissionedCampaignState) -> tuple[str, str]:
    return (
        _DEFINITION_STATUS_BY_COMMISSIONED_STATE[commissioned_state],
        _RUNTIME_STATUS_BY_COMMISSIONED_STATE[commissioned_state],
    )
