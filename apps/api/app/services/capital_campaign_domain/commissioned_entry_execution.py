from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError, ServiceUnavailableError
from app.models.audit_log import AuditLog
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.live_accounting_record import LiveAccountingRecord
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignCommissionRequest,
    CommissionedCampaignCommissionResponse,
    CommissionedExitRecommendationRequest,
    CommissionedExitRecommendationResponse,
    CommissionedCampaignEvidenceMetadata,
    CommissionedOwnershipReconciliationRequest,
    CommissionedOwnershipReconciliationResponse,
    CommissionedCampaignState,
    CommissionedCampaignTransitionRequest,
    CommissionedEntryExecutionRequest,
    CommissionedEntryExecutionResponse,
)
from app.schemas.live_crypto_orders import LiveCryptoOrderSubmitRequest
from app.schemas.live_crypto_orders import LiveCryptoOrderReconcileRequest
from app.services.capital_campaign_domain.commissioned_readiness_preview import (
    assess_commissioned_campaign_readiness,
    generate_commissioned_campaign_preview,
)
from app.services.capital_campaign_domain.commissioned_state_machine import transition_commissioned_campaign_state
from app.services.live_crypto_orders import LiveCryptoOrderService
from app.services.position_lifecycle.evaluator import evaluate_position_lifecycle
from app.services.position_lifecycle.policy_registry import resolve_lifecycle_policy
from app.services.position_lifecycle.source_adapter import load_position_snapshots
from app.services.profitability.engine import (
    RECOMMENDATION_HOLD_FOR_PROFIT,
    RECOMMENDATION_MAX_HOLD_EXIT,
    RECOMMENDATION_SELL_NOW,
    RECOMMENDATION_STOP_LOSS_EXIT,
)
from app.services.risk.risk_engine import (
    RiskDecisionAction,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
)
from app.services.risk.risk_persistence import RiskDecisionPersistenceRequest, persist_risk_decision


_COMMISSIONED_STATE_KEY = "commissioned_seed_campaign"
_AUTHORITY_CLASSIFICATION = "OPERATOR_COMMISSIONED"
_STRATEGY_CLASSIFICATION = "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY"
_DECISION_ENGINE_VERSION = "v1"
_ENTRY_ACTION = "ENTRY"
_EXIT_ACTION = "EXIT"

_ENTRY_LOCKS: dict[str, asyncio.Lock] = {}

_LIFECYCLE_TO_COMMISSIONED_RECOMMENDATION = {
    RECOMMENDATION_SELL_NOW: "SELL_NOW",
    RECOMMENDATION_STOP_LOSS_EXIT: "STOP_LOSS_EXIT",
    RECOMMENDATION_MAX_HOLD_EXIT: "MAX_HOLD_EXIT",
    RECOMMENDATION_HOLD_FOR_PROFIT: "HOLD",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lock_key(*, campaign_id: UUID, version: int) -> str:
    return f"{campaign_id}:{version}"


def _get_lock(*, campaign_id: UUID, version: int) -> asyncio.Lock:
    key = _lock_key(campaign_id=campaign_id, version=version)
    lock = _ENTRY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ENTRY_LOCKS[key] = lock
    return lock


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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


def _normalize_instrument(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _material_mismatch_blockers(*, request: CommissionedCampaignCommissionRequest, preview: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if str(preview.get("campaign_id")) != str(request.campaign_id):
        blockers.append("campaign_identity_mismatch")
    if int(preview.get("version", 0)) != request.version:
        blockers.append("campaign_version_mismatch")
    if str(preview.get("preview_identity_hash") or "") != request.preview_identity_hash:
        blockers.append("preview_identity_mismatch")

    preview_quote = Decimal(str(preview.get("proposed_quote_amount") or "0"))
    if preview_quote != request.requested_quote_amount:
        blockers.append("preview_quote_amount_mismatch")

    readiness_req = request.readiness_request
    if _normalize_instrument(str(preview.get("instrument") or "")) != _normalize_instrument(readiness_req.instrument):
        blockers.append("instrument_identity_mismatch")
    venue = preview.get("execution_venue") if isinstance(preview.get("execution_venue"), dict) else {}
    if str(venue.get("provider") or "") != readiness_req.provider:
        blockers.append("provider_identity_mismatch")
    if str(venue.get("environment") or "") != readiness_req.environment:
        blockers.append("environment_identity_mismatch")
    return blockers


def _build_commissioning_identity(*, request: CommissionedCampaignCommissionRequest) -> str:
    payload = {
        "campaign_id": str(request.campaign_id),
        "campaign_version": request.version,
        "preview_identity_hash": request.preview_identity_hash,
        "provider": request.readiness_request.provider,
        "environment": request.readiness_request.environment,
        "instrument": _normalize_instrument(request.readiness_request.instrument),
        "requested_quote_amount": format(request.requested_quote_amount, "f"),
        "authorization_expires_at": request.authorization_expires_at.isoformat(),
        "commissioned_until": request.commissioned_until.isoformat(),
        "idempotency_key": request.idempotency_key.strip(),
    }
    return _hash_payload(payload)


def _build_economic_idempotency_key(
    *,
    request: CommissionedEntryExecutionRequest,
    commissioning_identity: str,
) -> str:
    payload = {
        "campaign_id": str(request.campaign_id),
        "campaign_version": request.version,
        "commissioning_identity": commissioning_identity,
        "preview_identity_hash": request.expected_preview_identity_hash,
        "provider": request.readiness_request.provider,
        "environment": request.readiness_request.environment,
        "instrument": _normalize_instrument(request.readiness_request.instrument),
        "side": "BUY",
        "authorized_quote_amount": format(request.readiness_request.requested_quote_amount, "f"),
        "entry_action": _ENTRY_ACTION,
    }
    return _hash_payload(payload)


def _optional_uuid(value: object | None) -> UUID | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    try:
        return UUID(text)
    except Exception:
        return None


def _decimal(value: object | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _optional_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except Exception:
        return None


def _optional_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_decimal_divide(*, numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= Decimal("0"):
        return None
    return numerator / denominator


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


async def _persist_entry_audit(
    *,
    db: AsyncSession,
    actor: str,
    campaign_id: UUID,
    action: str,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="capital_campaign",
            entity_id=campaign_id,
            before_state=before_state,
            after_state=after_state,
        )
    )


async def _create_commissioned_decision_record(
    *,
    db: AsyncSession,
    request: CommissionedEntryExecutionRequest,
    economic_idempotency_key: str,
    risk_action: str,
    risk_event_id: UUID,
) -> UUID:
    decision_idempotency_key = f"commissioned-entry:{economic_idempotency_key}"
    existing = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.idempotency_key == decision_idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return existing.decision_id

    now = _utcnow()
    record = DecisionRecord(
        idempotency_key=decision_idempotency_key,
        source_lineage={
            "capital_campaigns": [str(request.campaign_id)],
            "risk_events": [str(risk_event_id)],
            "signals": [],
            "model_outputs": [],
            "trades": [],
        },
        field_provenance={
            "generated_signals": [{"entity_type": "capital_campaign", "entity_id": str(request.campaign_id)}],
            "risk_adjustments": [{"entity_type": "risk_events", "entity_id": str(risk_event_id)}],
        },
        version=_DECISION_ENGINE_VERSION,
        timestamp=now,
        asset={
            "campaign_id": str(request.campaign_id),
            "product_id": _normalize_instrument(request.readiness_request.instrument),
            "provider": request.readiness_request.provider,
            "environment": request.readiness_request.environment,
        },
        timeframe="commissioned_entry",
        market_regime={"state": "operator_commissioned", "source": "commissioned_seed_campaign"},
        indicators={
            "entry_authority": _AUTHORITY_CLASSIFICATION,
            "strategy_signal": _STRATEGY_CLASSIFICATION,
            "campaign_type": "COMMISSIONED_AUTONOMOUS_SEED",
            "economic_idempotency_key": economic_idempotency_key,
        },
        generated_signals=[
            {
                "decision_kind": "OPEN_POSITION_PROPOSED",
                "entry_authority": _AUTHORITY_CLASSIFICATION,
                "strategy_signal": _STRATEGY_CLASSIFICATION,
                "explanation": "Operator commissioned one bounded seed entry; no strategy-discovered BUY signal claimed.",
            }
        ],
        signal_strength=None,
        confidence=None,
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[
            {
                "risk_verdict": risk_action,
                "risk_event_id": str(risk_event_id),
            }
        ],
        expected_risk={"risk_event_id": str(risk_event_id), "risk_verdict": risk_action},
        expected_reward=None,
        position_size=request.requested_base_quantity,
        trade_accepted=risk_action in {"approve", "resize"},
        trade_rejected_reason=None if risk_action in {"approve", "resize"} else "risk_rejected",
        execution_details={
            "decision_kind": "OPEN_POSITION_PROPOSED",
            "entry_authority": _AUTHORITY_CLASSIFICATION,
            "strategy_signal": _STRATEGY_CLASSIFICATION,
            "campaign_version": request.version,
        },
        exit_details=None,
        pnl=None,
        duration=None,
        outcome="pending_submission",
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=["commissioned_seed_campaign", "operator_commissioned_entry"],
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )
    db.add(record)
    await db.flush()

    snapshot = DecisionSnapshot(
        decision_id=record.decision_id,
        timestamp=record.timestamp,
        asset=record.asset,
        exchange=request.readiness_request.provider,
        timeframe="commissioned_entry",
        ohlcv_context=[],
        indicators=record.indicators,
        generated_features={
            "decision_kind": "OPEN_POSITION_PROPOSED",
            "entry_authority": _AUTHORITY_CLASSIFICATION,
        },
        market_regime=record.market_regime,
        volatility={"state": "unknown"},
        spread_liquidity_context=None,
        strategy_inputs={
            "strategy_identity": None,
            "strategy_signal": "NOT_APPLICABLE",
            "entry_authority": _AUTHORITY_CLASSIFICATION,
        },
        risk_inputs={
            "risk_event_id": str(risk_event_id),
            "risk_action": risk_action,
        },
        current_position_state=None,
        open_trades=[],
        portfolio_exposure=None,
        parameter_set_version="commissioned_seed_campaign",
        strategy_version="operator_commissioned@1",
        ai_model_version=None,
        decision_engine_version=_DECISION_ENGINE_VERSION,
        configuration_version="commissioned_seed_campaign_task5",
    )
    db.add(snapshot)
    await db.flush()
    return record.decision_id


async def _create_commissioned_exit_decision_record(
    *,
    db: AsyncSession,
    request: CommissionedExitRecommendationRequest,
    recommendation_type: str,
    recommendation_reason: str,
    policy_id: str | None,
    policy_version: str | None,
    expected_net_result: Decimal | None,
    risk_action: str,
    risk_event_id: UUID | None,
    live_crypto_order_id: UUID | None,
) -> UUID:
    decision_idempotency_key = _hash_payload(
        {
            "kind": "commissioned-exit",
            "campaign_id": str(request.campaign_id),
            "version": request.version,
            "recommendation": recommendation_type,
            "idempotency_key": request.idempotency_key,
        }
    )

    existing = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.idempotency_key == decision_idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return existing.decision_id

    decision_kind = "CLOSE_POSITION_PROPOSED" if recommendation_type != "HOLD" else "HOLD"
    now = _utcnow()
    record = DecisionRecord(
        idempotency_key=decision_idempotency_key,
        source_lineage={
            "capital_campaigns": [str(request.campaign_id)],
            "risk_events": [] if risk_event_id is None else [str(risk_event_id)],
            "signals": [str(request.risk_signal_id)],
            "model_outputs": [],
            "trades": [],
        },
        field_provenance={
            "generated_signals": [{"entity_type": "capital_campaign", "entity_id": str(request.campaign_id)}],
            "risk_adjustments": []
            if risk_event_id is None
            else [{"entity_type": "risk_events", "entity_id": str(risk_event_id)}],
        },
        version=_DECISION_ENGINE_VERSION,
        timestamp=now,
        asset={
            "campaign_id": str(request.campaign_id),
            "entry_authority": _AUTHORITY_CLASSIFICATION,
            "live_crypto_order_id": None if live_crypto_order_id is None else str(live_crypto_order_id),
        },
        timeframe="commissioned_exit_evaluation",
        market_regime={"state": "operator_commissioned_active_position", "source": "commissioned_seed_campaign"},
        indicators={
            "decision_kind": decision_kind,
            "recommendation_type": recommendation_type,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "expected_net_result": None if expected_net_result is None else format(expected_net_result, "f"),
            "risk_action": risk_action,
        },
        generated_signals=[
            {
                "decision_kind": decision_kind,
                "recommendation_type": recommendation_type,
                "reason": recommendation_reason,
                "entry_authority": _AUTHORITY_CLASSIFICATION,
            }
        ],
        signal_strength=None,
        confidence=None,
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=[]
        if risk_event_id is None
        else [{"risk_verdict": risk_action, "risk_event_id": str(risk_event_id)}],
        expected_risk=None if risk_event_id is None else {"risk_event_id": str(risk_event_id), "risk_verdict": risk_action},
        expected_reward=None,
        position_size=None,
        trade_accepted=False,
        trade_rejected_reason=None,
        execution_details={
            "decision_kind": decision_kind,
            "recommendation_type": recommendation_type,
            "sell_submission_permitted": False,
        },
        exit_details={
            "recommendation_reason": recommendation_reason,
            "policy_id": policy_id,
            "policy_version": policy_version,
        },
        pnl=None,
        duration=None,
        outcome="advisory_only",
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=["commissioned_seed_campaign", "exit_recommendation_only"],
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )
    db.add(record)
    await db.flush()

    snapshot = DecisionSnapshot(
        decision_id=record.decision_id,
        timestamp=record.timestamp,
        asset=record.asset,
        exchange="commissioned_seed_campaign",
        timeframe="commissioned_exit_evaluation",
        ohlcv_context=[],
        indicators=record.indicators,
        generated_features={
            "decision_kind": decision_kind,
            "recommendation_type": recommendation_type,
        },
        market_regime=record.market_regime,
        volatility={"state": "unknown"},
        spread_liquidity_context=None,
        strategy_inputs={
            "strategy_identity": None,
            "strategy_signal": "NOT_APPLICABLE",
            "entry_authority": _AUTHORITY_CLASSIFICATION,
        },
        risk_inputs={
            "risk_event_id": None if risk_event_id is None else str(risk_event_id),
            "risk_action": risk_action,
        },
        current_position_state=None,
        open_trades=[],
        portfolio_exposure=None,
        parameter_set_version="commissioned_seed_campaign",
        strategy_version="operator_commissioned@1",
        ai_model_version=None,
        decision_engine_version=_DECISION_ENGINE_VERSION,
        configuration_version="commissioned_seed_campaign_task7",
    )
    db.add(snapshot)
    await db.flush()
    return record.decision_id


def _build_risk_request(request: CommissionedEntryExecutionRequest) -> RiskEvaluationRequest:
    return RiskEvaluationRequest(
        signal_id=request.risk_signal_id,
        paper_account_id=request.paper_account_id,
        asset_id=request.asset_id,
        side="buy",
        quantity=request.requested_base_quantity,
        account_equity=request.account_equity,
        max_position_size_pct=request.max_position_size_pct,
        min_order_notional=request.min_order_notional,
        campaign_authorized_notional=request.readiness_request.requested_quote_amount,
        qty_step_size=request.qty_step_size,
        supports_fractional=request.supports_fractional,
        actor=request.actor,
    )


async def commission_commissioned_campaign(
    *,
    db: AsyncSession,
    request: CommissionedCampaignCommissionRequest,
) -> CommissionedCampaignCommissionResponse:
    readiness = await assess_commissioned_campaign_readiness(db=db, request=request.readiness_request)
    preview = await generate_commissioned_campaign_preview(db=db, request=request.readiness_request)

    blockers = list(readiness.blockers)
    if readiness.readiness_verdict != "READY":
        blockers.append("readiness_not_ready")
    if preview.stale_after is None or preview.stale_after <= _utcnow():
        blockers.append("preview_expired_or_stale")
    if request.authorization_expires_at <= _utcnow():
        blockers.append("expired_operator_authorization")
    if request.commissioned_until <= _utcnow():
        blockers.append("commissioning_authority_expired")

    blockers.extend(
        _material_mismatch_blockers(
            request=request,
            preview=preview.model_dump(mode="json"),
        )
    )

    if blockers:
        deduped = sorted(set(blockers))
        raise InvalidRequestError(
            message="Commissioning blocked by fail-closed validation",
            details={"blockers": deduped},
        )

    definition, _runtime = await _load_definition_and_runtime_for_update(
        db=db,
        campaign_id=request.campaign_id,
        version=request.version,
    )
    blob = _read_commissioned_blob(definition=definition)
    current_state = str(blob.get("state") or "DRAFT")
    if current_state != "READY":
        raise InvalidRequestError(
            message="Commissioning requires READY state",
            details={"current_state": current_state, "required": "READY"},
        )

    commissioning_identity = _build_commissioning_identity(request=request)

    transition = await transition_commissioned_campaign_state(
        db=db,
        campaign_id=request.campaign_id,
        version=request.version,
        request=CommissionedCampaignTransitionRequest(
            target_state="COMMISSIONED",
            actor=request.actor,
            reason=request.commissioning_reason,
            idempotency_key=request.idempotency_key,
            expected_current_state="READY",
            evidence_metadata=[
                CommissionedCampaignEvidenceMetadata(
                    evidence_code="commissioned_preview_binding",
                    source="commissioned_seed_campaign_task5",
                    observed_at=_utcnow(),
                    payload={
                        "preview_identity_hash": request.preview_identity_hash,
                        "commissioning_identity": commissioning_identity,
                    },
                )
            ],
        ),
    )

    definition_after, _runtime_after = await _load_definition_and_runtime_for_update(
        db=db,
        campaign_id=request.campaign_id,
        version=request.version,
    )
    post_blob = _read_commissioned_blob(definition=definition_after)
    post_blob["commissioning"] = {
        "commissioning_identity": commissioning_identity,
        "preview_identity_hash": request.preview_identity_hash,
        "commissioned_by": request.actor,
        "commissioned_at": _utcnow().isoformat(),
        "commissioned_until": request.commissioned_until.isoformat(),
        "authorization_expires_at": request.authorization_expires_at.isoformat(),
        "campaign_definition_version": request.version,
        "mandate_id": str(request.readiness_request.mandate_id) if request.readiness_request.mandate_id else None,
        "mandate_version_id": str(request.readiness_request.mandate_version_id) if request.readiness_request.mandate_version_id else None,
        "expected_mandate_version_number": request.readiness_request.expected_mandate_version_number,
        "risk_policy_id": request.readiness_request.expected_risk_policy_id,
        "risk_policy_version": request.readiness_request.expected_risk_policy_version,
        "provider": request.readiness_request.provider,
        "environment": request.readiness_request.environment,
        "instrument": _normalize_instrument(request.readiness_request.instrument),
        "authorized_quote_amount_cap": format(readiness.applicable_capital_cap or Decimal("0"), "f"),
        "requested_quote_amount": format(request.requested_quote_amount, "f"),
        "idempotency_key": request.idempotency_key,
        "authority_classification": _AUTHORITY_CLASSIFICATION,
        "strategy_signal_classification": _STRATEGY_CLASSIFICATION,
    }
    _write_commissioned_blob(definition=definition_after, blob=post_blob)
    await _persist_entry_audit(
        db=db,
        actor=request.actor,
        campaign_id=request.campaign_id,
        action="commissioned_seed_campaign.commission",
        before_state={"state": transition.previous_state},
        after_state={
            "state": transition.current_state,
            "commissioning_identity": commissioning_identity,
            "preview_identity_hash": request.preview_identity_hash,
        },
    )
    await db.flush()
    await db.commit()

    return CommissionedCampaignCommissionResponse(
        campaign_id=request.campaign_id,
        version=request.version,
        previous_state=transition.previous_state,
        current_state=transition.current_state,
        replayed=transition.replayed,
        commissioning_identity=commissioning_identity,
        preview_identity_hash=request.preview_identity_hash,
        authority_classification=_AUTHORITY_CLASSIFICATION,
        strategy_signal_classification=_STRATEGY_CLASSIFICATION,
        commissioned_until=request.commissioned_until,
        blockers=[],
    )


async def execute_commissioned_entry(
    *,
    db: AsyncSession,
    request: CommissionedEntryExecutionRequest,
) -> CommissionedEntryExecutionResponse:
    lock = _get_lock(campaign_id=request.campaign_id, version=request.version)
    async with lock:
        definition, _runtime = await _load_definition_and_runtime_for_update(
            db=db,
            campaign_id=request.campaign_id,
            version=request.version,
        )
        blob = _read_commissioned_blob(definition=definition)
        commissioning = blob.get("commissioning") if isinstance(blob.get("commissioning"), dict) else {}
        commissioning_identity = str(commissioning.get("commissioning_identity") or "").strip()
        if not commissioning_identity:
            raise InvalidRequestError(
                message="Missing commissioning authority",
                details={"blocker": "missing_commissioned_authority"},
            )
        commissioned_until_raw = commissioning.get("commissioned_until")
        commissioned_until = datetime.fromisoformat(str(commissioned_until_raw)) if commissioned_until_raw else None
        if commissioned_until is None or commissioned_until <= _utcnow():
            raise InvalidRequestError(
                message="Commissioned authority expired",
                details={"blocker": "expired_commissioned_authority"},
            )

        bound_preview_hash = str(commissioning.get("preview_identity_hash") or "").strip()
        if bound_preview_hash != request.expected_preview_identity_hash:
            raise InvalidRequestError(
                message="Commissioned preview binding mismatch",
                details={
                    "bound_preview_identity_hash": bound_preview_hash,
                    "expected_preview_identity_hash": request.expected_preview_identity_hash,
                },
            )

        economic_idempotency_key = _build_economic_idempotency_key(
            request=request,
            commissioning_identity=commissioning_identity,
        )

        entry_execution = blob.get("entry_execution") if isinstance(blob.get("entry_execution"), dict) else {}
        existing_key = str(entry_execution.get("economic_idempotency_key") or "").strip()
        if existing_key and existing_key != economic_idempotency_key:
            raise InvalidRequestError(
                message="Idempotency key reuse must preserve economic intent",
                details={
                    "existing_economic_idempotency_key": existing_key,
                    "new_economic_idempotency_key": economic_idempotency_key,
                },
            )

        commissioned_state = str(blob.get("state") or "DRAFT")
        resume_from_buy_pending = (
            commissioned_state == "BUY_PENDING"
            and existing_key == economic_idempotency_key
            and _optional_uuid(entry_execution.get("live_crypto_order_id")) is not None
        )

        if not resume_from_buy_pending:
            readiness = await assess_commissioned_campaign_readiness(db=db, request=request.readiness_request)
            preview = await generate_commissioned_campaign_preview(db=db, request=request.readiness_request)

            if readiness.readiness_verdict != "READY":
                raise InvalidRequestError(
                    message="Commissioned entry requires READY readiness verdict",
                    details={"blockers": list(readiness.blockers)},
                )
            if preview.preview_identity_hash != request.expected_preview_identity_hash:
                raise InvalidRequestError(
                    message="Preview identity mismatch",
                    details={
                        "expected_preview_identity_hash": request.expected_preview_identity_hash,
                        "actual_preview_identity_hash": preview.preview_identity_hash,
                    },
                )
            if preview.stale_after is None or preview.stale_after <= _utcnow():
                raise InvalidRequestError(
                    message="Preview is expired or stale",
                    details={"stale_after": preview.stale_after},
                )

        if entry_execution.get("terminal") is True:
            return CommissionedEntryExecutionResponse(
                campaign_id=request.campaign_id,
                version=request.version,
                previous_state="BUY_RECONCILIATION_PENDING",
                current_state="BUY_RECONCILIATION_PENDING",
                replayed=True,
                vetoed=False,
                    risk_event_id=_optional_uuid(entry_execution.get("risk_event_id")),
                risk_action=str(entry_execution.get("risk_action") or "approve"),
                    decision_record_id=_optional_uuid(entry_execution.get("decision_record_id")),
                    live_crypto_order_id=_optional_uuid(entry_execution.get("live_crypto_order_id")),
                provider_order_id=None if entry_execution.get("provider_order_id") is None else str(entry_execution.get("provider_order_id")),
                provider_submission_classification=str(entry_execution.get("provider_submission_classification") or "replayed"),
                commissioning_identity=commissioning_identity,
                economic_idempotency_key=economic_idempotency_key,
                authority_classification=_AUTHORITY_CLASSIFICATION,
                strategy_signal_classification=_STRATEGY_CLASSIFICATION,
                no_position_ownership_created=True,
                blockers=[],
            )

        if commissioned_state != "COMMISSIONED" and not resume_from_buy_pending:
            raise InvalidRequestError(
                message="Entry execution requires COMMISSIONED state",
                details={"current_state": commissioned_state},
            )

        if resume_from_buy_pending:
            expected_live_crypto_order_id = _optional_uuid(entry_execution.get("live_crypto_order_id"))
            if expected_live_crypto_order_id != request.live_crypto_order_id:
                raise InvalidRequestError(
                    message="BUY_PENDING execution requires matching live order identity",
                    details={
                        "expected_live_crypto_order_id": None if expected_live_crypto_order_id is None else str(expected_live_crypto_order_id),
                        "received_live_crypto_order_id": str(request.live_crypto_order_id),
                    },
                )
            persisted_risk_event_id = _optional_uuid(entry_execution.get("risk_event_id"))
            decision_record_id = _optional_uuid(entry_execution.get("decision_record_id"))
            if persisted_risk_event_id is None or decision_record_id is None:
                raise InvalidRequestError(
                    message="BUY_PENDING execution missing persisted identities",
                    details={"entry_execution": entry_execution},
                )
            persisted_risk = type("_PersistedRisk", (), {"risk_event_id": persisted_risk_event_id})()
            risk_action_value = str(entry_execution.get("risk_action") or "approve")
            buy_pending_transition = type("_Transition", (), {"current_state": "BUY_PENDING"})()
        else:
            buy_pending_transition = await transition_commissioned_campaign_state(
                db=db,
                campaign_id=request.campaign_id,
                version=request.version,
                request=CommissionedCampaignTransitionRequest(
                    target_state="BUY_PENDING",
                    actor=request.actor,
                    reason="commissioned_entry_pre_submission",
                    idempotency_key=f"{request.idempotency_key}:buy_pending",
                    expected_current_state="COMMISSIONED",
                ),
            )

            risk_result = evaluate_signal_risk(
                request=_build_risk_request(request),
                reference_price=request.reference_price,
                context=RiskEvaluationContext(
                    global_kill_switch_engaged=False,
                    account_trading_paused=False,
                    asset_in_no_trade_zone=False,
                    pair_in_cooldown=False,
                    would_breach_daily_loss=False,
                    would_breach_drawdown=False,
                    has_computable_stop_loss=True,
                    bypass_sizing_rule=False,
                ),
            )
            persisted_risk = await persist_risk_decision(
                db=db,
                request=RiskDecisionPersistenceRequest(
                    paper_account_id=request.paper_account_id,
                    signal_id=request.risk_signal_id,
                    actor=request.actor,
                    evaluation_result=risk_result,
                ),
            )
            if risk_result.action == RiskDecisionAction.REJECT:
                await transition_commissioned_campaign_state(
                    db=db,
                    campaign_id=request.campaign_id,
                    version=request.version,
                    request=CommissionedCampaignTransitionRequest(
                        target_state="VETOED",
                        actor=request.actor,
                        reason="risk_engine_veto",
                        idempotency_key=f"{request.idempotency_key}:vetoed",
                        expected_current_state="BUY_PENDING",
                    ),
                )
                return CommissionedEntryExecutionResponse(
                    campaign_id=request.campaign_id,
                    version=request.version,
                    previous_state=buy_pending_transition.current_state,
                    current_state="VETOED",
                    replayed=False,
                    vetoed=True,
                    risk_event_id=persisted_risk.risk_event_id,
                    risk_action=risk_result.action.value,
                    decision_record_id=None,
                    live_crypto_order_id=None,
                    provider_order_id=None,
                    provider_submission_classification="vetoed",
                    commissioning_identity=commissioning_identity,
                    economic_idempotency_key=economic_idempotency_key,
                    authority_classification=_AUTHORITY_CLASSIFICATION,
                    strategy_signal_classification=_STRATEGY_CLASSIFICATION,
                    no_position_ownership_created=True,
                    blockers=["risk_engine_veto"],
                )

            decision_record_id = await _create_commissioned_decision_record(
                db=db,
                request=request,
                economic_idempotency_key=economic_idempotency_key,
                risk_action=risk_result.action.value,
                risk_event_id=persisted_risk.risk_event_id,
            )

            definition_after_pending, _runtime_after_pending = await _load_definition_and_runtime_for_update(
                db=db,
                campaign_id=request.campaign_id,
                version=request.version,
            )
            blob_after_pending = _read_commissioned_blob(definition=definition_after_pending)
            blob_after_pending["entry_execution"] = {
                "economic_idempotency_key": economic_idempotency_key,
                "risk_event_id": str(persisted_risk.risk_event_id),
                "risk_action": risk_result.action.value,
                "decision_record_id": str(decision_record_id),
                "pre_submission_recorded_at": _utcnow().isoformat(),
                "live_crypto_order_id": str(request.live_crypto_order_id),
                "terminal": False,
                "authority_classification": _AUTHORITY_CLASSIFICATION,
                "strategy_signal_classification": _STRATEGY_CLASSIFICATION,
            }
            _write_commissioned_blob(definition=definition_after_pending, blob=blob_after_pending)
            await _persist_entry_audit(
                db=db,
                actor=request.actor,
                campaign_id=request.campaign_id,
                action="commissioned_seed_campaign.entry_pre_submission",
                before_state={"state": "BUY_PENDING"},
                after_state={
                    "economic_idempotency_key": economic_idempotency_key,
                    "decision_record_id": str(decision_record_id),
                },
            )
            await db.flush()
            await db.commit()
            risk_action_value = risk_result.action.value

        live_service = LiveCryptoOrderService()
        submit_response = None
        submit_error: Exception | None = None
        try:
            submit_response = await live_service.submit(
                db=db,
                request=LiveCryptoOrderSubmitRequest(
                    live_crypto_order_id=request.live_crypto_order_id,
                    confirmation_challenge_id=request.confirmation_challenge_id,
                    confirmation_phrase=request.confirmation_phrase,
                    operator_identity=request.actor,
                    idempotency_token=request.submit_idempotency_token,
                ),
            )
        except (TimeoutError, ServiceUnavailableError) as exc:
            submit_error = exc

        final_classification = "submitted"
        final_state: CommissionedCampaignState = "BUY_RECONCILIATION_PENDING"
        provider_order_id: str | None = None

        if submit_error is not None:
            final_classification = "ambiguous_submission"
            final_state = "RECONCILIATION_REQUIRED"
        elif submit_response is not None:
            provider_order_id = submit_response.live_crypto_order.provider_order_id
            if (
                submit_response.live_crypto_order.status in {"RECONCILIATION_REQUIRED", "UNKNOWN"}
                or submit_response.provider_create_order_responded is False
            ):
                final_classification = "ambiguous_submission"
                final_state = "RECONCILIATION_REQUIRED"
            else:
                await transition_commissioned_campaign_state(
                    db=db,
                    campaign_id=request.campaign_id,
                    version=request.version,
                    request=CommissionedCampaignTransitionRequest(
                        target_state="BUY_SUBMITTED",
                        actor=request.actor,
                        reason="commissioned_entry_submitted",
                        idempotency_key=f"{request.idempotency_key}:buy_submitted",
                        expected_current_state="BUY_PENDING",
                    ),
                )
                final_state = "BUY_RECONCILIATION_PENDING"
                await transition_commissioned_campaign_state(
                    db=db,
                    campaign_id=request.campaign_id,
                    version=request.version,
                    request=CommissionedCampaignTransitionRequest(
                        target_state="BUY_RECONCILIATION_PENDING",
                        actor=request.actor,
                        reason="commissioned_entry_reconciliation_pending",
                        idempotency_key=f"{request.idempotency_key}:buy_reconciliation_pending",
                        expected_current_state="BUY_SUBMITTED",
                    ),
                )

        if final_state == "RECONCILIATION_REQUIRED":
            await transition_commissioned_campaign_state(
                db=db,
                campaign_id=request.campaign_id,
                version=request.version,
                request=CommissionedCampaignTransitionRequest(
                    target_state="RECONCILIATION_REQUIRED",
                    actor=request.actor,
                    reason="commissioned_entry_ambiguous_submission",
                    idempotency_key=f"{request.idempotency_key}:reconciliation_required",
                    expected_current_state="BUY_PENDING",
                ),
            )

        definition_final, _runtime_final = await _load_definition_and_runtime_for_update(
            db=db,
            campaign_id=request.campaign_id,
            version=request.version,
        )
        blob_final = _read_commissioned_blob(definition=definition_final)
        entry_final = blob_final.get("entry_execution") if isinstance(blob_final.get("entry_execution"), dict) else {}
        entry_final.update(
            {
                "economic_idempotency_key": economic_idempotency_key,
                "risk_event_id": str(persisted_risk.risk_event_id),
                "risk_action": risk_action_value,
                "decision_record_id": str(decision_record_id),
                "live_crypto_order_id": str(request.live_crypto_order_id),
                "provider_order_id": provider_order_id,
                "provider_submission_classification": final_classification,
                "terminal": final_state == "BUY_RECONCILIATION_PENDING",
                "updated_at": _utcnow().isoformat(),
            }
        )
        if submit_error is not None:
            entry_final["ambiguous_error"] = {
                "type": submit_error.__class__.__name__,
                "message": str(submit_error),
            }
        blob_final["entry_execution"] = entry_final
        _write_commissioned_blob(definition=definition_final, blob=blob_final)
        await _persist_entry_audit(
            db=db,
            actor=request.actor,
            campaign_id=request.campaign_id,
            action="commissioned_seed_campaign.entry_execution",
            before_state={"state": "BUY_PENDING"},
            after_state={
                "state": final_state,
                "provider_submission_classification": final_classification,
                "live_crypto_order_id": str(request.live_crypto_order_id),
                "provider_order_id": provider_order_id,
            },
        )
        await db.flush()
        await db.commit()

        return CommissionedEntryExecutionResponse(
            campaign_id=request.campaign_id,
            version=request.version,
            previous_state=buy_pending_transition.current_state,
            current_state=final_state,
            replayed=False,
            vetoed=False,
            risk_event_id=persisted_risk.risk_event_id,
            risk_action=risk_action_value,
            decision_record_id=decision_record_id,
            live_crypto_order_id=request.live_crypto_order_id,
            provider_order_id=provider_order_id,
            provider_submission_classification=final_classification,
            commissioning_identity=commissioning_identity,
            economic_idempotency_key=economic_idempotency_key,
            authority_classification=_AUTHORITY_CLASSIFICATION,
            strategy_signal_classification=_STRATEGY_CLASSIFICATION,
            no_position_ownership_created=True,
            blockers=[],
        )


async def _load_buy_fill_accounting_rows(
    *,
    db: AsyncSession,
    live_crypto_order_id: UUID,
) -> list[LiveAccountingRecord]:
    rows = await db.scalars(
        select(LiveAccountingRecord)
        .where(LiveAccountingRecord.live_crypto_order_id == live_crypto_order_id)
        .where(LiveAccountingRecord.side == "buy")
        .where(LiveAccountingRecord.record_type.in_(["fill_accounting", "partial_fill_accounting"]))
        .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.created_at.asc())
    )
    return list(rows)


async def reconcile_commissioned_buy_ownership(
    *,
    db: AsyncSession,
    request: CommissionedOwnershipReconciliationRequest,
) -> CommissionedOwnershipReconciliationResponse:
    lock = _get_lock(campaign_id=request.campaign_id, version=request.version)
    async with lock:
        definition, _runtime = await _load_definition_and_runtime_for_update(
            db=db,
            campaign_id=request.campaign_id,
            version=request.version,
        )
        blob = _read_commissioned_blob(definition=definition)
        current_state = str(blob.get("state") or "DRAFT")
        if current_state not in {"BUY_RECONCILIATION_PENDING", "RECONCILIATION_REQUIRED", "ACTIVE_POSITION"}:
            raise InvalidRequestError(
                message="Ownership reconciliation requires reconciliation-pending state",
                details={"current_state": current_state},
            )

        ownership = blob.get("ownership_reconciliation") if isinstance(blob.get("ownership_reconciliation"), dict) else {}
        seen_keys = ownership.get("seen_idempotency_keys") if isinstance(ownership.get("seen_idempotency_keys"), dict) else {}
        if str(seen_keys.get(request.idempotency_key) or "").strip() and current_state == "ACTIVE_POSITION":
            return CommissionedOwnershipReconciliationResponse(
                campaign_id=request.campaign_id,
                version=request.version,
                previous_state="ACTIVE_POSITION",
                current_state="ACTIVE_POSITION",
                replayed=True,
                ownership_proven=True,
                position_identity=str(ownership.get("position_identity") or None),
                provider_order_id=str(ownership.get("provider_order_id") or None),
                provider_fill_ids=list(ownership.get("provider_fill_ids") or []),
                executed_quantity=_optional_decimal(ownership.get("executed_quantity")),
                average_entry_price=_optional_decimal(ownership.get("average_entry_price")),
                total_buy_fees=_optional_decimal(ownership.get("total_buy_fees")),
                attributable_remaining_quantity=_optional_decimal(ownership.get("attributable_remaining_quantity")),
                evidence_timestamps={
                    "provider_observed_at": _optional_datetime((ownership.get("evidence_timestamps") or {}).get("provider_observed_at")),
                    "first_fill_recorded_at": _optional_datetime((ownership.get("evidence_timestamps") or {}).get("first_fill_recorded_at")),
                    "last_fill_recorded_at": _optional_datetime((ownership.get("evidence_timestamps") or {}).get("last_fill_recorded_at")),
                    "ownership_verified_at": _optional_datetime((ownership.get("evidence_timestamps") or {}).get("ownership_verified_at")),
                },
                correlation_ids={
                    "live_crypto_order_id": str((ownership.get("correlation_ids") or {}).get("live_crypto_order_id") or "") or None,
                    "decision_record_id": str((ownership.get("correlation_ids") or {}).get("decision_record_id") or "") or None,
                    "risk_event_id": str((ownership.get("correlation_ids") or {}).get("risk_event_id") or "") or None,
                    "audit_correlation_id": str((ownership.get("correlation_ids") or {}).get("audit_correlation_id") or "") or None,
                },
                blockers=[],
            )

        entry_execution = blob.get("entry_execution") if isinstance(blob.get("entry_execution"), dict) else {}
        entry_live_order_id = _optional_uuid(entry_execution.get("live_crypto_order_id"))
        requested_live_order_id = request.live_crypto_order_id or entry_live_order_id
        if requested_live_order_id is None:
            raise InvalidRequestError(
                message="Ownership reconciliation requires live crypto order identity",
                details={"blocker": "missing_live_crypto_order_id"},
            )
        if entry_live_order_id is not None and requested_live_order_id != entry_live_order_id:
            raise InvalidRequestError(
                message="Live order identity mismatch for ownership reconciliation",
                details={
                    "expected_live_crypto_order_id": str(entry_live_order_id),
                    "received_live_crypto_order_id": str(requested_live_order_id),
                },
            )

        reconcile_response = await LiveCryptoOrderService().reconcile(
            db=db,
            live_crypto_order_id=requested_live_order_id,
            request=LiveCryptoOrderReconcileRequest(operator_identity=request.actor),
        )

        accounting_rows = await _load_buy_fill_accounting_rows(
            db=db,
            live_crypto_order_id=requested_live_order_id,
        )
        provider_order_ids = {
            str(row.provider_order_id).strip()
            for row in accounting_rows
            if str(row.provider_order_id).strip()
        }
        if reconcile_response.provider_order_id:
            provider_order_ids.add(str(reconcile_response.provider_order_id).strip())

        fill_ids = sorted(
            {
                str(row.provider_fill_id).strip()
                for row in accounting_rows
                if row.provider_fill_id is not None and str(row.provider_fill_id).strip()
            }
        )
        executed_quantity = sum((_decimal(row.filled_quantity) for row in accounting_rows), Decimal("0"))
        total_quote_notional = sum((_decimal(row.gross_notional) for row in accounting_rows), Decimal("0"))
        total_buy_fees = sum((_decimal(row.fee_amount) for row in accounting_rows), Decimal("0"))
        average_entry_price = _safe_decimal_divide(numerator=total_quote_notional, denominator=executed_quantity)

        create_payload = reconcile_response.live_crypto_order.safe_provider_response.get("create_order_payload")
        declared_base_size = None
        if isinstance(create_payload, dict):
            declared_base_size = _optional_decimal(create_payload.get("size"))
        attributable_remaining_quantity = None
        if declared_base_size is not None:
            attributable_remaining_quantity = declared_base_size - executed_quantity
            if attributable_remaining_quantity < Decimal("0"):
                attributable_remaining_quantity = Decimal("0")

        evidence_timestamps = {
            "provider_observed_at": _optional_datetime(
                (reconcile_response.live_crypto_order.safe_provider_response.get("reconciliation") or {}).get("observed_at")
            ),
            "first_fill_recorded_at": None if not accounting_rows else accounting_rows[0].recorded_at,
            "last_fill_recorded_at": None if not accounting_rows else accounting_rows[-1].recorded_at,
            "ownership_verified_at": _utcnow(),
        }
        correlation_ids = {
            "live_crypto_order_id": str(requested_live_order_id),
            "decision_record_id": None if entry_execution.get("decision_record_id") is None else str(entry_execution.get("decision_record_id")),
            "risk_event_id": None if entry_execution.get("risk_event_id") is None else str(entry_execution.get("risk_event_id")),
            "audit_correlation_id": str(reconcile_response.live_crypto_order.audit_correlation_id),
        }

        blockers: list[str] = []
        if reconcile_response.reconciliation_status in {"RECONCILIATION_REQUIRED", "UNKNOWN", "REJECTED", "CANCELLED"}:
            blockers.append("reconciliation_not_final_or_confident")
        if reconcile_response.campaign_correlation_status != "verified":
            blockers.append("campaign_correlation_unverified")
        if reconcile_response.balance_mismatch_state in {"missing", "stale", "material_mismatch"}:
            blockers.append("balance_evidence_unresolved")
        if len(provider_order_ids) != 1:
            blockers.append("provider_order_id_not_authoritative")
        if not fill_ids:
            blockers.append("provider_fill_evidence_missing")
        if executed_quantity <= Decimal("0"):
            blockers.append("executed_quantity_zero")
        if average_entry_price is None or average_entry_price <= Decimal("0"):
            blockers.append("average_entry_price_missing")

        ownership_proven = len(blockers) == 0
        previous_state: CommissionedCampaignState = current_state  # type: ignore[assignment]
        target_state: CommissionedCampaignState = "ACTIVE_POSITION" if ownership_proven else "RECONCILIATION_REQUIRED"

        if target_state != current_state:
            await transition_commissioned_campaign_state(
                db=db,
                campaign_id=request.campaign_id,
                version=request.version,
                request=CommissionedCampaignTransitionRequest(
                    target_state=target_state,
                    actor=request.actor,
                    reason="commissioned_buy_ownership_reconciled" if ownership_proven else "commissioned_buy_ownership_reconciliation_blocked",
                    idempotency_key=f"{request.idempotency_key}:{target_state.lower()}",
                    expected_current_state=current_state,
                ),
            )

        provider_order_id = next(iter(provider_order_ids)) if len(provider_order_ids) == 1 else None
        position_identity = None
        if provider_order_id is not None:
            position_identity = _hash_payload(
                {
                    "campaign_id": str(request.campaign_id),
                    "version": request.version,
                    "provider_order_id": provider_order_id,
                    "fill_ids": fill_ids,
                }
            )

        ownership_blob = {
            "position_identity": position_identity,
            "provider_order_id": provider_order_id,
            "provider_fill_ids": fill_ids,
            "executed_quantity": format(executed_quantity, "f"),
            "average_entry_price": None if average_entry_price is None else format(average_entry_price, "f"),
            "total_buy_fees": format(total_buy_fees, "f"),
            "attributable_remaining_quantity": None
            if attributable_remaining_quantity is None
            else format(attributable_remaining_quantity, "f"),
            "evidence_timestamps": {
                key: None if value is None else value.isoformat()
                for key, value in evidence_timestamps.items()
            },
            "correlation_ids": correlation_ids,
            "ownership_proven": ownership_proven,
            "blockers": blockers,
            "last_reconciled_at": _utcnow().isoformat(),
            "seen_idempotency_keys": {
                **seen_keys,
                request.idempotency_key: _utcnow().isoformat(),
            },
        }

        blob_updated = _read_commissioned_blob(definition=definition)
        blob_updated["ownership_reconciliation"] = ownership_blob
        _write_commissioned_blob(definition=definition, blob=blob_updated)
        await _persist_entry_audit(
            db=db,
            actor=request.actor,
            campaign_id=request.campaign_id,
            action="commissioned_seed_campaign.buy_ownership_reconciliation",
            before_state={"state": previous_state},
            after_state={
                "state": target_state,
                "ownership_proven": ownership_proven,
                "provider_order_id": provider_order_id,
                "position_identity": position_identity,
                "blockers": blockers,
            },
        )
        await db.flush()
        await db.commit()

        return CommissionedOwnershipReconciliationResponse(
            campaign_id=request.campaign_id,
            version=request.version,
            previous_state=previous_state,
            current_state=target_state,
            replayed=False,
            ownership_proven=ownership_proven,
            position_identity=position_identity,
            provider_order_id=provider_order_id,
            provider_fill_ids=fill_ids,
            executed_quantity=executed_quantity,
            average_entry_price=average_entry_price,
            total_buy_fees=total_buy_fees,
            attributable_remaining_quantity=attributable_remaining_quantity,
            evidence_timestamps=evidence_timestamps,
            correlation_ids=correlation_ids,
            blockers=blockers,
        )


async def recommend_commissioned_exit(
    *,
    db: AsyncSession,
    request: CommissionedExitRecommendationRequest,
) -> CommissionedExitRecommendationResponse:
    lock = _get_lock(campaign_id=request.campaign_id, version=request.version)
    async with lock:
        definition, runtime = await _load_definition_and_runtime_for_update(
            db=db,
            campaign_id=request.campaign_id,
            version=request.version,
        )
        blob = _read_commissioned_blob(definition=definition)
        current_state = str(blob.get("state") or "DRAFT")
        if current_state != "ACTIVE_POSITION":
            raise InvalidRequestError(
                message="Exit recommendation requires ACTIVE_POSITION",
                details={"current_state": current_state},
            )

        exit_blob = blob.get("exit_recommendation") if isinstance(blob.get("exit_recommendation"), dict) else {}
        seen_idempotency_keys = exit_blob.get("seen_idempotency_keys") if isinstance(exit_blob.get("seen_idempotency_keys"), dict) else {}
        replay_payload = seen_idempotency_keys.get(request.idempotency_key)
        if isinstance(replay_payload, dict):
            replay_response = CommissionedExitRecommendationResponse.model_validate(replay_payload)
            return replay_response.model_copy(update={"replayed": True})

        ownership = blob.get("ownership_reconciliation") if isinstance(blob.get("ownership_reconciliation"), dict) else {}
        entry_execution = blob.get("entry_execution") if isinstance(blob.get("entry_execution"), dict) else {}
        live_crypto_order_id = _optional_uuid((ownership.get("correlation_ids") or {}).get("live_crypto_order_id"))
        if live_crypto_order_id is None:
            live_crypto_order_id = _optional_uuid(entry_execution.get("live_crypto_order_id"))

        snapshots = await load_position_snapshots(
            db=db,
            account_id=runtime.paper_account_id,
            campaign_id=runtime.id,
        )
        matching_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.position_size > Decimal("0")
            and (
                ownership.get("provider_order_id") is None
                or str(ownership.get("provider_order_id")) in set(snapshot.provider_order_ids)
            )
        ]

        now = _utcnow()
        recommendation_type = "HOLD"
        recommendation_reason = "Fail-closed hold: no actionable lifecycle recommendation is available yet."
        lifecycle_state = "UNKNOWN"
        confidence = Decimal("0.50")
        risk_action = "not_required_hold"
        risk_event_id: UUID | None = None
        policy_id: str | None = None
        policy_version: str | None = None
        expected_fees: Decimal | None = None
        estimated_slippage: Decimal | None = None
        expected_net_result: Decimal | None = None
        blockers: list[str] = []

        evidence: dict[str, Any] = {
            "campaign_state": current_state,
            "matching_snapshot_count": len(matching_snapshots),
            "evaluation_mode": "advisory_only",
        }
        profitability_evidence: dict[str, Any] = {}
        timestamps: dict[str, datetime | None] = {
            "evaluated_at": now,
            "market_data_timestamp": None,
            "opened_at": None,
        }
        correlation_identifiers: dict[str, str | None] = {
            "campaign_id": str(request.campaign_id),
            "live_crypto_order_id": None if live_crypto_order_id is None else str(live_crypto_order_id),
            "provider_order_id": None if ownership.get("provider_order_id") is None else str(ownership.get("provider_order_id")),
            "position_id": None,
        }

        if len(matching_snapshots) != 1:
            blockers.append("position_snapshot_unavailable")
            recommendation_reason = "Fail-closed hold: expected exactly one active position snapshot for this commissioned campaign."
        else:
            snapshot = matching_snapshots[0]
            correlation_identifiers["position_id"] = snapshot.position_id
            timestamps["market_data_timestamp"] = snapshot.market_data_timestamp
            timestamps["opened_at"] = snapshot.opened_at

            policy = resolve_lifecycle_policy(
                asset_class=snapshot.asset_class,
                symbol=snapshot.symbol,
                venue="venue-neutral",
                now=now,
            )
            if policy is None:
                blockers.append("policy_unavailable")
                recommendation_reason = "Fail-closed hold: no eligible lifecycle policy is available."
            else:
                policy_id = policy.policy_id
                policy_version = policy.policy_version
                lifecycle = evaluate_position_lifecycle(snapshot=snapshot, policy=policy, now=now)
                lifecycle_state = lifecycle.lifecycle_state
                recommendation_type = _LIFECYCLE_TO_COMMISSIONED_RECOMMENDATION.get(lifecycle.recommendation, "HOLD")
                recommendation_reason = lifecycle.reason
                expected_net_result = lifecycle.expected_net_realized_pnl_if_sold_now
                if lifecycle.current_market_value is not None:
                    expected_fees = lifecycle.current_market_value * policy.estimated_exit_fee_rate
                    estimated_slippage = lifecycle.current_market_value * policy.estimated_slippage_rate

                evidence.update(
                    {
                        "symbol": snapshot.symbol,
                        "asset_class": snapshot.asset_class,
                        "lifecycle_recommendation": lifecycle.recommendation,
                        "lifecycle_reason": lifecycle.reason,
                        "market_data_stale": lifecycle.market_data_stale,
                        "stale_indicator": lifecycle.stale_indicator,
                        "dust_indicator": lifecycle.dust_indicator,
                        "closed_indicator": lifecycle.closed_indicator,
                        "policy_stale_price_threshold_minutes": policy.stale_price_threshold_minutes,
                        "policy_max_hold_minutes": policy.max_hold_minutes,
                    }
                )
                profitability_evidence = {
                    "current_market_value": None if lifecycle.current_market_value is None else format(lifecycle.current_market_value, "f"),
                    "expected_net_result_if_sold_now": None
                    if lifecycle.expected_net_realized_pnl_if_sold_now is None
                    else format(lifecycle.expected_net_realized_pnl_if_sold_now, "f"),
                    "minimum_profitable_exit_price": None
                    if lifecycle.minimum_profitable_exit_price is None
                    else format(lifecycle.minimum_profitable_exit_price, "f"),
                    "break_even_price": None if lifecycle.break_even_price is None else format(lifecycle.break_even_price, "f"),
                    "expected_exit_fee": None if expected_fees is None else format(expected_fees, "f"),
                    "expected_slippage": None if estimated_slippage is None else format(estimated_slippage, "f"),
                }

                if lifecycle.market_data_stale or snapshot.current_price is None:
                    blockers.append("market_evidence_stale_or_missing")
                    recommendation_type = "HOLD"
                    recommendation_reason = "Fail-closed hold: market evidence is stale or missing."

                if recommendation_type != "HOLD" and expected_net_result is None:
                    blockers.append("profitability_uncertain")
                    recommendation_type = "HOLD"
                    recommendation_reason = "Fail-closed hold: profitability evidence is incomplete."

                if recommendation_type != "HOLD":
                    risk_result = evaluate_signal_risk(
                        request=RiskEvaluationRequest(
                            signal_id=request.risk_signal_id,
                            paper_account_id=request.paper_account_id,
                            asset_id=request.asset_id,
                            side="sell",
                            quantity=snapshot.position_size,
                            account_equity=request.account_equity,
                            max_position_size_pct=request.max_position_size_pct,
                            min_order_notional=request.min_order_notional,
                            qty_step_size=request.qty_step_size,
                            supports_fractional=request.supports_fractional,
                            actor=request.actor,
                        ),
                        reference_price=snapshot.current_price or snapshot.entry_price,
                        context=RiskEvaluationContext(
                            global_kill_switch_engaged=False,
                            account_trading_paused=False,
                            asset_in_no_trade_zone=False,
                            pair_in_cooldown=False,
                            would_breach_daily_loss=False,
                            would_breach_drawdown=False,
                            has_computable_stop_loss=True,
                            bypass_sizing_rule=False,
                        ),
                    )
                    risk_action = risk_result.action.value
                    persisted_risk = await persist_risk_decision(
                        db=db,
                        request=RiskDecisionPersistenceRequest(
                            paper_account_id=request.paper_account_id,
                            signal_id=request.risk_signal_id,
                            actor=request.actor,
                            evaluation_result=risk_result,
                        ),
                    )
                    risk_event_id = persisted_risk.risk_event_id
                    if risk_result.action == RiskDecisionAction.REJECT:
                        blockers.append("risk_engine_veto")
                        recommendation_type = "HOLD"
                        recommendation_reason = "Fail-closed hold: Risk Engine vetoed an exit recommendation."

                confidence = Decimal("0.60")
                if recommendation_type == "SELL_NOW":
                    confidence = Decimal("0.90")
                elif recommendation_type in {"STOP_LOSS_EXIT", "MAX_HOLD_EXIT"}:
                    confidence = Decimal("1.00")

        decision_record_id = await _create_commissioned_exit_decision_record(
            db=db,
            request=request,
            recommendation_type=recommendation_type,
            recommendation_reason=recommendation_reason,
            policy_id=policy_id,
            policy_version=policy_version,
            expected_net_result=expected_net_result,
            risk_action=risk_action,
            risk_event_id=risk_event_id,
            live_crypto_order_id=live_crypto_order_id,
        )

        response = CommissionedExitRecommendationResponse(
            campaign_id=request.campaign_id,
            version=request.version,
            replayed=False,
            recommendation_type=recommendation_type,
            recommendation_reason=recommendation_reason,
            policy_id=policy_id,
            policy_version=policy_version,
            lifecycle_state=lifecycle_state,
            confidence=confidence,
            evidence=evidence,
            profitability_evidence=profitability_evidence,
            expected_fees=expected_fees,
            estimated_slippage=estimated_slippage,
            expected_net_result=expected_net_result,
            risk_action=risk_action,
            risk_event_id=risk_event_id,
            decision_record_id=decision_record_id,
            timestamps=timestamps,
            correlation_identifiers=correlation_identifiers,
            no_sell_submitted=True,
            blockers=sorted(set(blockers)),
        )

        exit_blob_updated = {
            "last_recommendation": response.model_dump(mode="json"),
            "updated_at": _utcnow().isoformat(),
            "seen_idempotency_keys": {
                **seen_idempotency_keys,
                request.idempotency_key: response.model_dump(mode="json"),
            },
        }
        blob_updated = _read_commissioned_blob(definition=definition)
        blob_updated["exit_recommendation"] = exit_blob_updated
        _write_commissioned_blob(definition=definition, blob=blob_updated)
        await _persist_entry_audit(
            db=db,
            actor=request.actor,
            campaign_id=request.campaign_id,
            action="commissioned_seed_campaign.exit_recommendation",
            before_state={"state": current_state},
            after_state={
                "recommendation_type": recommendation_type,
                "risk_action": risk_action,
                "no_sell_submitted": True,
            },
        )
        await db.flush()
        await db.commit()
        return response
