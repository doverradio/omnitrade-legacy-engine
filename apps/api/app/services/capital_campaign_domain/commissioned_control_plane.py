from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
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
_METADATA_BACKFILL_AUDIT_ACTION = "commissioned_seed_campaign.metadata_backfill"
_LEGACY_COMMISSIONED_METADATA_KEYS = {
    "state",
    "authority_metadata",
    "evidence_metadata",
    "transition_history",
    "seen_idempotency_keys",
    "commissioning",
    "entry_execution",
    "ownership_reconciliation",
    "exit_recommendation",
    "operator_control",
}
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
    # Backward compatibility for legacy commissioned metadata written at top-level.
    if any(key in metadata for key in _LEGACY_COMMISSIONED_METADATA_KEYS):
        return {key: metadata.get(key) for key in _LEGACY_COMMISSIONED_METADATA_KEYS if key in metadata}
    return {}


def _write_commissioned_blob(*, definition: CapitalCampaignDefinition, blob: dict[str, Any]) -> None:
    metadata = dict(definition.metadata_evidence or {})
    metadata[_COMMISSIONED_STATE_KEY] = blob
    definition.metadata_evidence = metadata
    definition.updated_at = _utcnow()


def _backfill_signature(
    *,
    campaign_id: UUID,
    version: int,
    actor: str,
    commissioning_identity: str,
    commissioned_by: str,
    authority_classification: str,
    strategy_signal_classification: str,
    provider: str | None,
    environment: str | None,
    instrument: str | None,
    paper_account_id: str | None,
    asset_id: str | None,
    capital_budget: str | None,
    maximum_position_size: str | None,
    maximum_total_exposure: str | None,
    commissioned_until: str | None,
) -> dict[str, Any]:
    return {
        "campaign_id": str(campaign_id),
        "version": int(version),
        "actor": actor,
        "commissioning_identity": commissioning_identity,
        "commissioned_by": commissioned_by,
        "authority_classification": authority_classification,
        "strategy_signal_classification": strategy_signal_classification,
        "provider": provider,
        "environment": environment,
        "instrument": instrument,
        "paper_account_id": paper_account_id,
        "asset_id": asset_id,
        "capital_budget": capital_budget,
        "maximum_position_size": maximum_position_size,
        "maximum_total_exposure": maximum_total_exposure,
        "commissioned_until": commissioned_until,
    }


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

    commissioning_identity = str(commissioning.get("commissioning_identity") or "").strip()
    production_eligible = (
        state in {"COMMISSIONED", "BUY_RECONCILIATION_PENDING", "ACTIVE_POSITION"}
        and bool(commissioning_identity)
        and not bool(operator_control.get("paused", False))
        and not bool(operator_control.get("cancelled", False))
    )

    blockers: list[str] = []
    warnings: list[str] = []
    has_commissioned_metadata = bool(blob)
    ownership_blockers = ownership.get("blockers") if isinstance(ownership.get("blockers"), list) else []
    blockers.extend(str(item) for item in ownership_blockers)
    if not has_commissioned_metadata and str(definition.status or "").upper() == "READY" and str(runtime.status or "").upper() == "READY":
        blockers.append("commissioned_state_metadata_missing_for_ready_campaign")
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
                else "blocked_by_missing_commissioning_or_pause_or_cancel_or_state"
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


async def backfill_commissioned_ready_metadata(
    *,
    db: AsyncSession,
    campaign_id: UUID,
    version: int,
    actor: str,
    idempotency_key: str,
    commissioning_identity: str,
    commissioned_by: str | None = None,
    authority_classification: str = "OPERATOR_COMMISSIONED",
    strategy_signal_classification: str = "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY",
    provider: str | None = None,
    environment: str | None = None,
    instrument: str | None = None,
    paper_account_id: UUID | None = None,
    asset_id: UUID | None = None,
    capital_budget: Decimal | None = None,
    maximum_position_size: Decimal | None = None,
    maximum_total_exposure: Decimal | None = None,
    commissioned_until: datetime | None = None,
) -> dict[str, Any]:
    actor_value = str(actor or "").strip()
    if not actor_value:
        raise InvalidRequestError(message="actor is required", details={"actor": actor})
    idempotency_value = str(idempotency_key or "").strip()
    if not idempotency_value:
        raise InvalidRequestError(message="idempotency_key is required", details={"idempotency_key": idempotency_key})
    commissioning_identity_value = str(commissioning_identity or "").strip()
    if not commissioning_identity_value:
        raise InvalidRequestError(
            message="commissioning_identity is required",
            details={"commissioning_identity": commissioning_identity},
        )

    definition, runtime = await _load_definition_and_runtime_for_update(
        db=db,
        campaign_id=campaign_id,
        version=version,
    )
    definition_status = str(definition.status or "").upper()
    runtime_status = str(runtime.status or "").upper()
    if definition_status != "READY" or runtime_status != "READY":
        raise InvalidRequestError(
            message="Governed commissioned metadata backfill requires READY runtime and definition",
            details={
                "definition_status": definition.status,
                "runtime_status": runtime.status,
            },
        )

    blob = _read_commissioned_blob(definition=definition)
    current_state = str(blob.get("state") or "DRAFT")
    if current_state not in {"DRAFT", "READY"}:
        raise InvalidRequestError(
            message="Governed commissioned metadata backfill requires DRAFT or READY commissioned state",
            details={"current_state": current_state},
        )

    commissioning_actor = str(commissioned_by or actor_value).strip()
    signature = _backfill_signature(
        campaign_id=campaign_id,
        version=version,
        actor=actor_value,
        commissioning_identity=commissioning_identity_value,
        commissioned_by=commissioning_actor,
        authority_classification=str(authority_classification),
        strategy_signal_classification=str(strategy_signal_classification),
        provider=None if provider is None else str(provider),
        environment=None if environment is None else str(environment),
        instrument=None if instrument is None else str(instrument),
        paper_account_id=None if paper_account_id is None else str(paper_account_id),
        asset_id=None if asset_id is None else str(asset_id),
        capital_budget=None if capital_budget is None else format(capital_budget, "f"),
        maximum_position_size=None if maximum_position_size is None else format(maximum_position_size, "f"),
        maximum_total_exposure=None if maximum_total_exposure is None else format(maximum_total_exposure, "f"),
        commissioned_until=None if commissioned_until is None else commissioned_until.isoformat(),
    )

    backfill_store = blob.get("metadata_backfill") if isinstance(blob.get("metadata_backfill"), dict) else {}
    seen = backfill_store.get("seen_idempotency_keys") if isinstance(backfill_store.get("seen_idempotency_keys"), dict) else {}
    replay = seen.get(idempotency_value) if isinstance(seen.get(idempotency_value), dict) else None
    if replay is not None:
        replay_signature = replay.get("request_signature") if isinstance(replay.get("request_signature"), dict) else None
        if replay_signature != signature:
            raise InvalidRequestError(
                message="Changed-intent idempotency key reuse is not allowed",
                details={
                    "idempotency_key": idempotency_value,
                    "stored_request_signature": replay_signature,
                    "received_request_signature": signature,
                },
            )
        replay_response = replay.get("response") if isinstance(replay.get("response"), dict) else {}
        replay_payload = dict(replay_response)
        replay_payload["replayed"] = True
        return replay_payload

    now = _utcnow()
    transition_history = blob.get("transition_history") if isinstance(blob.get("transition_history"), list) else []
    if current_state == "DRAFT":
        transition_history = [
            *transition_history,
            {
                "previous_state": "DRAFT",
                "current_state": "READY",
                "actor": actor_value,
                "reason": "governed_ready_runtime_definition_backfill",
                "idempotency_key": idempotency_value,
                "transitioned_at": now.isoformat(),
                "synthetic": True,
            },
        ]

    authority_metadata = blob.get("authority_metadata") if isinstance(blob.get("authority_metadata"), dict) else {}
    if not authority_metadata:
        max_notional = maximum_position_size
        if max_notional is None:
            try:
                max_notional = Decimal(str(getattr(definition, "maximum_position_size", "0")))
            except Exception:
                max_notional = Decimal("0")
        authority_metadata = {
            "campaign_type": "COMMISSIONED_AUTONOMOUS_SEED",
            "entry_authority": "OPERATOR_COMMISSIONED",
            "lifecycle_authority": "OMNITRADE_AUTONOMOUS",
            "maximum_entry_notional": format(max_notional, "f"),
            "repeat_entry_allowed": False,
            "commissioned_by": commissioning_actor,
            "commissioned_at": now.isoformat(),
        }

    commissioning = blob.get("commissioning") if isinstance(blob.get("commissioning"), dict) else {}
    commissioning_updates: dict[str, Any] = {
        "commissioning_identity": commissioning_identity_value,
        "commissioned_by": commissioning_actor,
        "authority_classification": str(authority_classification),
        "strategy_signal_classification": str(strategy_signal_classification),
        "campaign_definition_version": version,
        "idempotency_key": idempotency_value,
        "backfill_source": "governed_ready_runtime_definition_backfill",
        "backfill_recorded_at": now.isoformat(),
    }
    if commissioned_until is not None:
        commissioning_updates["commissioned_until"] = commissioned_until.isoformat()
    if provider is not None:
        commissioning_updates["provider"] = str(provider)
    if environment is not None:
        commissioning_updates["environment"] = str(environment)
    if instrument is not None:
        commissioning_updates["instrument"] = str(instrument)
    if paper_account_id is not None:
        commissioning_updates["paper_account_id"] = str(paper_account_id)
    if asset_id is not None:
        commissioning_updates["asset_id"] = str(asset_id)
    if capital_budget is not None:
        commissioning_updates["capital_budget"] = format(capital_budget, "f")
    if maximum_position_size is not None:
        commissioning_updates["maximum_position_size"] = format(maximum_position_size, "f")
    if maximum_total_exposure is not None:
        commissioning_updates["maximum_total_exposure"] = format(maximum_total_exposure, "f")

    commissioning = {**commissioning, **commissioning_updates}
    state_value = "READY"

    seen_updated = {
        **seen,
        idempotency_value: {
            "request_signature": signature,
        },
    }

    existing_metadata = dict(definition.metadata_evidence or {})
    preserved_keys = sorted([key for key in existing_metadata.keys() if key != _COMMISSIONED_STATE_KEY])

    updated_blob = {
        **blob,
        "state": state_value,
        "authority_metadata": authority_metadata,
        "transition_history": transition_history,
        "commissioning": commissioning,
        "updated_at": now.isoformat(),
        "metadata_backfill": {
            **backfill_store,
            "seen_idempotency_keys": seen_updated,
            "last_idempotency_key": idempotency_value,
            "last_actor": actor_value,
            "last_updated_at": now.isoformat(),
        },
    }

    _write_commissioned_blob(definition=definition, blob=updated_blob)
    response_payload = {
        "campaign_id": str(campaign_id),
        "version": version,
        "state": state_value,
        "replayed": False,
        "metadata_written": {
            "commissioned_seed_campaign.state": state_value,
            "commissioning_identity": commissioning_identity_value,
            "commissioned_by": commissioning_actor,
            "authority_classification": str(authority_classification),
            "strategy_signal_classification": str(strategy_signal_classification),
            "commissioned_until": None if commissioned_until is None else commissioned_until.isoformat(),
        },
        "preserved_metadata_keys": preserved_keys,
        "audit_action": _METADATA_BACKFILL_AUDIT_ACTION,
    }
    seen_updated[idempotency_value]["response"] = response_payload
    updated_blob["metadata_backfill"]["seen_idempotency_keys"] = seen_updated
    _write_commissioned_blob(definition=definition, blob=updated_blob)

    db.add(
        AuditLog(
            actor=actor_value,
            action=_METADATA_BACKFILL_AUDIT_ACTION,
            entity_type="capital_campaign",
            entity_id=campaign_id,
            before_state={
                "commissioned_seed_campaign": blob,
            },
            after_state={
                "commissioned_seed_campaign": updated_blob,
                "governed_backfill": True,
            },
        )
    )
    await db.flush()
    await db.commit()
    return response_payload
