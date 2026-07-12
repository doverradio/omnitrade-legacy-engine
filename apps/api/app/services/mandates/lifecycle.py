from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.exchange_connection import ExchangeConnection
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.mandates.contracts import (
    AUTONOMY_LEVEL_2,
    MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
    MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
    MandateAuthorizationModel,
    MandateAuthorizationRequest,
    MandateLifecycleActionRequest,
    MandateVersionCreateRequest,
    MandateVersionModel,
)
from app.services.mandates.validation import validate_mandate_state_transition, validate_mandate_version


_LIFECYCLE_ACTION_TO_STATUS: dict[str, str] = {
    "SUBMIT_FOR_AUTHORIZATION": "PENDING_AUTHORIZATION",
    "ACTIVATE": "ACTIVE",
    "PAUSE": "PAUSED",
    "RESUME": "ACTIVE",
    "SET_EXIT_ONLY": "EXIT_ONLY",
    "EXPIRE": "EXPIRED",
    "REVOKE": "REVOKED",
    "KILL": "KILLED",
    "COMPLETE": "COMPLETED",
}


@dataclass(frozen=True)
class MandateHistoryEvent:
    audit_id: int
    actor: str
    action: str
    created_at: datetime
    before_state: dict[str, object] | None
    after_state: dict[str, object] | None


async def list_mandates(
    *,
    db: AsyncSession,
    owner_actor_id: str | None = None,
    status: str | None = None,
) -> list[AutonomousCapitalMandate]:
    stmt: Select[tuple[AutonomousCapitalMandate]] = select(AutonomousCapitalMandate).order_by(
        AutonomousCapitalMandate.created_at.desc()
    )
    if owner_actor_id is not None:
        stmt = stmt.where(AutonomousCapitalMandate.owner_actor_id == owner_actor_id)
    if status is not None:
        stmt = stmt.where(AutonomousCapitalMandate.status == status)

    return list(await db.scalars(stmt))


async def get_mandate(*, db: AsyncSession, mandate_id: uuid.UUID) -> AutonomousCapitalMandate:
    mandate = await db.get(AutonomousCapitalMandate, mandate_id)
    if mandate is None:
        raise NotFoundError(message="Mandate not found", details={"mandate_id": str(mandate_id)})
    return mandate


async def create_mandate(
    *,
    db: AsyncSession,
    owner_actor_id: str,
    autonomy_level: str,
    provider: str,
    exchange_environment: str,
    exchange_connection_id: uuid.UUID,
    live_trading_profile_id: uuid.UUID,
    paper_account_id: uuid.UUID | None,
    capital_campaign_id: int | None,
    expires_at: datetime | None,
    actor: str,
    idempotency_key: str | None,
    reason: str | None,
) -> AutonomousCapitalMandate:
    await _validate_relationships(
        db=db,
        exchange_connection_id=exchange_connection_id,
        live_trading_profile_id=live_trading_profile_id,
        paper_account_id=paper_account_id,
        capital_campaign_id=capital_campaign_id,
    )

    if idempotency_key:
        existing = await _find_audit_by_idempotency(
            db=db,
            entity_type="autonomous_capital_mandate",
            action="MANDATE_CREATED",
            idempotency_key=idempotency_key,
        )
        if existing and existing.entity_id is not None:
            existing_mandate = await db.get(AutonomousCapitalMandate, existing.entity_id)
            if existing_mandate is not None:
                return existing_mandate

    mandate = AutonomousCapitalMandate(
        owner_actor_id=owner_actor_id,
        autonomy_level=autonomy_level,
        provider=provider,
        exchange_environment=exchange_environment,
        exchange_connection_id=exchange_connection_id,
        live_trading_profile_id=live_trading_profile_id,
        paper_account_id=paper_account_id,
        capital_campaign_id=capital_campaign_id,
        expires_at=expires_at,
    )
    db.add(mandate)
    await db.flush()

    db.add(
        AuditLog(
            actor=actor,
            action="MANDATE_CREATED",
            entity_type="autonomous_capital_mandate",
            entity_id=mandate.mandate_id,
            before_state=None,
            after_state={
                "status": mandate.status,
                "autonomy_level": mandate.autonomy_level,
                "idempotency_key": idempotency_key,
                "reason": reason,
            },
        )
    )

    await db.commit()
    await db.refresh(mandate)
    return mandate


async def create_mandate_version(
    *,
    db: AsyncSession,
    request: MandateVersionCreateRequest,
    commit: bool = True,
) -> AutonomousCapitalMandateVersion:
    mandate = await get_mandate(db=db, mandate_id=request.mandate_id)

    if request.idempotency_key:
        existing = await _find_audit_by_idempotency(
            db=db,
            entity_type="autonomous_capital_mandate",
            action="MANDATE_VERSION_CREATED",
            idempotency_key=request.idempotency_key,
        )
        if existing and existing.after_state:
            version_id_raw = existing.after_state.get("mandate_version_id")
            if isinstance(version_id_raw, str):
                try:
                    existing_version = await db.get(AutonomousCapitalMandateVersion, uuid.UUID(version_id_raw))
                except ValueError:
                    existing_version = None
                if existing_version is not None:
                    return existing_version

    max_version = await db.scalar(
        select(func.max(AutonomousCapitalMandateVersion.version_number)).where(
            AutonomousCapitalMandateVersion.mandate_id == mandate.mandate_id
        )
    )
    version_number = int(max_version or 0) + 1

    version_hash = _build_version_hash(request=request, version_number=version_number)

    version = AutonomousCapitalMandateVersion(
        mandate_id=mandate.mandate_id,
        version_number=version_number,
        version_hash=version_hash,
        base_currency=request.base_currency,
        authorized_capital_usd=request.authorized_capital_usd,
        max_order_notional_usd=request.max_order_notional_usd,
        max_open_exposure_usd=request.max_open_exposure_usd,
        max_daily_deployed_usd=request.max_daily_deployed_usd,
        max_daily_realized_loss_usd=request.max_daily_realized_loss_usd,
        max_campaign_drawdown_usd=request.max_campaign_drawdown_usd,
        max_consecutive_losses=request.max_consecutive_losses,
        position_limit=request.position_limit,
        price_evidence_max_age_seconds=request.price_evidence_max_age_seconds,
        max_slippage_bps=request.max_slippage_bps,
        max_fee_bps=request.max_fee_bps,
        allowed_products=list(request.allowed_products),
        allowed_order_sides=list(request.allowed_order_sides),
        allowed_strategy_versions=list(request.allowed_strategy_versions),
        entry_policy=request.entry_policy,
        exit_policy=request.exit_policy,
        cooldown_policy=request.cooldown_policy,
        operating_schedule=request.operating_schedule,
        approval_policy=request.approval_policy,
        reconciliation_policy=request.reconciliation_policy,
        kill_switch_policy=request.kill_switch_policy,
        owner_acknowledgements=request.owner_acknowledgements,
        authorization_evidence_summary=request.authorization_evidence_summary,
        is_authorized=False,
        is_active=False,
    )

    validation = validate_mandate_version(_to_version_model(version))
    if not validation.valid:
        raise InvalidRequestError(message="Invalid mandate version envelope", details={"reason": validation.reason})

    db.add(version)
    await db.flush()

    db.add(
        AuditLog(
            actor=request.actor,
            action="MANDATE_VERSION_CREATED",
            entity_type="autonomous_capital_mandate",
            entity_id=mandate.mandate_id,
            before_state={"status": mandate.status},
            after_state={
                "status": mandate.status,
                "mandate_version_id": str(version.mandate_version_id),
                "mandate_version_number": version.version_number,
                "idempotency_key": request.idempotency_key,
                "audit_correlation_id": str(request.audit_correlation_id) if request.audit_correlation_id else None,
            },
        )
    )

    if commit:
        await db.commit()
    await db.refresh(version)
    return version


async def authorize_mandate_version(
    *,
    db: AsyncSession,
    request: MandateAuthorizationRequest,
    commit: bool = True,
) -> MandateAuthorizationModel:
    mandate = await get_mandate(db=db, mandate_id=request.mandate_id)
    version = await db.get(AutonomousCapitalMandateVersion, request.mandate_version_id)
    if version is None or version.mandate_id != mandate.mandate_id:
        raise NotFoundError(
            message="Mandate version not found",
            details={"mandate_version_id": str(request.mandate_version_id), "mandate_id": str(mandate.mandate_id)},
        )

    existing = await db.scalar(
        select(AutonomousCapitalMandateAuthorization)
        .where(AutonomousCapitalMandateAuthorization.idempotency_key == (request.idempotency_key or ""))
        .limit(1)
    ) if request.idempotency_key else None
    if existing is not None:
        return await _hydrate_authorization_model(db=db, authorization=existing)

    if mandate.status not in {"PENDING_AUTHORIZATION", "AUTHORIZED", "ACTIVE", "PAUSED", "EXIT_ONLY"}:
        raise InvalidRequestError(
            message="Mandate is not in an authorizable lifecycle state",
            details={"status": mandate.status},
        )

    approval_result = (
        MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE
        if mandate.autonomy_level == AUTONOMY_LEVEL_2
        else MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN
    )

    authorization = AutonomousCapitalMandateAuthorization(
        mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id,
        authorization_state="AUTHORIZED",
        approval_result=approval_result,
        authorized_by_actor_id=request.actor,
        authorization_method=request.authorization_method,
        owner_acknowledgements=request.owner_acknowledgements,
        authorization_evidence=request.authorization_evidence,
        deterministic_explanation=request.deterministic_explanation,
        idempotency_key=request.idempotency_key or f"mandate-auth-{uuid.uuid4()}",
        audit_correlation_id=request.audit_correlation_id,
        expires_at=request.expires_at,
    )
    db.add(authorization)

    previous_status = mandate.status
    if mandate.status == "PENDING_AUTHORIZATION":
        mandate.status = "AUTHORIZED"
        mandate.authorized_at = _utcnow()
        mandate.updated_at = _utcnow()

    db.add(
        AuditLog(
            actor=request.actor,
            action="MANDATE_VERSION_AUTHORIZED",
            entity_type="autonomous_capital_mandate",
            entity_id=mandate.mandate_id,
            before_state={"status": previous_status},
            after_state={
                "status": mandate.status,
                "mandate_version_id": str(version.mandate_version_id),
                "approval_result": approval_result,
                "idempotency_key": request.idempotency_key,
                "audit_correlation_id": str(request.audit_correlation_id) if request.audit_correlation_id else None,
            },
        )
    )

    if commit:
        await db.commit()
    await db.refresh(authorization)
    return await _hydrate_authorization_model(db=db, authorization=authorization)


async def apply_mandate_lifecycle_action(
    *,
    db: AsyncSession,
    request: MandateLifecycleActionRequest,
) -> AutonomousCapitalMandate:
    mandate = await get_mandate(db=db, mandate_id=request.mandate_id)

    target_status = _LIFECYCLE_ACTION_TO_STATUS.get(request.action)
    if target_status is None:
        raise InvalidRequestError(message="Unsupported lifecycle action", details={"action": request.action})

    if request.idempotency_key:
        existing = await _find_audit_by_idempotency(
            db=db,
            entity_type="autonomous_capital_mandate",
            action=f"MANDATE_{request.action}",
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            return mandate

    transition = validate_mandate_state_transition(from_status=mandate.status, to_status=target_status)
    if not transition.valid:
        raise ConflictError(
            message="Invalid mandate lifecycle transition",
            details={"from": mandate.status, "to": target_status, "reason": transition.reason},
        )

    if request.action in {"ACTIVATE", "RESUME", "SET_EXIT_ONLY"}:
        has_authorization = await _has_authorized_mandate_version(db=db, mandate_id=mandate.mandate_id)
        if not has_authorization:
            raise InvalidRequestError(
                message="Lifecycle action requires at least one authorized mandate version",
                details={"action": request.action},
            )

    previous_status = mandate.status
    mandate.status = target_status
    now = _utcnow()
    mandate.updated_at = now
    if target_status == "ACTIVE":
        mandate.activated_at = now
    if target_status == "PAUSED":
        mandate.paused_at = now
    if target_status == "EXPIRED":
        mandate.expires_at = now
    if target_status in {"REVOKED", "KILLED"}:
        mandate.revoked_at = now

    db.add(
        AuditLog(
            actor=request.actor,
            action=f"MANDATE_{request.action}",
            entity_type="autonomous_capital_mandate",
            entity_id=mandate.mandate_id,
            before_state={"status": previous_status},
            after_state={
                "status": mandate.status,
                "reason": request.reason,
                "idempotency_key": request.idempotency_key,
                "audit_correlation_id": str(request.audit_correlation_id) if request.audit_correlation_id else None,
                "software_build_version": request.software_build_version,
            },
        )
    )

    await db.commit()
    await db.refresh(mandate)
    return mandate


async def list_mandate_versions(
    *,
    db: AsyncSession,
    mandate_id: uuid.UUID,
) -> list[AutonomousCapitalMandateVersion]:
    await get_mandate(db=db, mandate_id=mandate_id)
    return list(
        await db.scalars(
            select(AutonomousCapitalMandateVersion)
            .where(AutonomousCapitalMandateVersion.mandate_id == mandate_id)
            .order_by(AutonomousCapitalMandateVersion.version_number.desc())
        )
    )


async def list_mandate_authorizations(
    *,
    db: AsyncSession,
    mandate_id: uuid.UUID,
) -> list[MandateAuthorizationModel]:
    await get_mandate(db=db, mandate_id=mandate_id)
    authorizations = list(
        await db.scalars(
            select(AutonomousCapitalMandateAuthorization)
            .where(AutonomousCapitalMandateAuthorization.mandate_id == mandate_id)
            .order_by(AutonomousCapitalMandateAuthorization.recorded_at.desc())
        )
    )

    models: list[MandateAuthorizationModel] = []
    for item in authorizations:
        models.append(await _hydrate_authorization_model(db=db, authorization=item))
    return models


async def read_mandate_history(
    *,
    db: AsyncSession,
    mandate_id: uuid.UUID,
) -> list[MandateHistoryEvent]:
    await get_mandate(db=db, mandate_id=mandate_id)
    records = list(
        await db.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "autonomous_capital_mandate",
                AuditLog.entity_id == mandate_id,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        )
    )

    return [
        MandateHistoryEvent(
            audit_id=item.id,
            actor=item.actor,
            action=item.action,
            created_at=item.created_at,
            before_state=item.before_state,
            after_state=item.after_state,
        )
        for item in records
    ]


async def _has_authorized_mandate_version(*, db: AsyncSession, mandate_id: uuid.UUID) -> bool:
    authorized = await db.scalar(
        select(AutonomousCapitalMandateAuthorization.mandate_authorization_id)
        .where(
            AutonomousCapitalMandateAuthorization.mandate_id == mandate_id,
            AutonomousCapitalMandateAuthorization.authorization_state == "AUTHORIZED",
            AutonomousCapitalMandateAuthorization.revoked_at.is_(None),
        )
        .limit(1)
    )
    return authorized is not None


async def _find_audit_by_idempotency(
    *,
    db: AsyncSession,
    entity_type: str,
    action: str,
    idempotency_key: str,
) -> AuditLog | None:
    records = list(
        await db.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == entity_type,
                AuditLog.action == action,
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(50)
        )
    )
    for record in records:
        after_state = record.after_state or {}
        if after_state.get("idempotency_key") == idempotency_key:
            return record
    return None


def _build_version_hash(*, request: MandateVersionCreateRequest, version_number: int) -> str:
    canonical_payload = {
        "mandate_id": str(request.mandate_id),
        "version_number": version_number,
        "base_currency": request.base_currency,
        "authorized_capital_usd": str(request.authorized_capital_usd),
        "max_order_notional_usd": str(request.max_order_notional_usd),
        "max_open_exposure_usd": str(request.max_open_exposure_usd),
        "max_daily_deployed_usd": str(request.max_daily_deployed_usd),
        "max_daily_realized_loss_usd": str(request.max_daily_realized_loss_usd),
        "max_campaign_drawdown_usd": str(request.max_campaign_drawdown_usd),
        "max_consecutive_losses": request.max_consecutive_losses,
        "position_limit": request.position_limit,
        "price_evidence_max_age_seconds": request.price_evidence_max_age_seconds,
        "max_slippage_bps": str(request.max_slippage_bps),
        "max_fee_bps": str(request.max_fee_bps),
        "allowed_products": list(request.allowed_products),
        "allowed_order_sides": list(request.allowed_order_sides),
        "allowed_strategy_versions": list(request.allowed_strategy_versions),
        "entry_policy": request.entry_policy,
        "exit_policy": request.exit_policy,
        "cooldown_policy": request.cooldown_policy,
        "operating_schedule": request.operating_schedule,
        "approval_policy": request.approval_policy,
        "reconciliation_policy": request.reconciliation_policy,
        "kill_switch_policy": request.kill_switch_policy,
        "owner_acknowledgements": request.owner_acknowledgements,
        "authorization_evidence_summary": request.authorization_evidence_summary,
    }
    encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _to_version_model(version: AutonomousCapitalMandateVersion) -> MandateVersionModel:
    return MandateVersionModel(
        mandate_version_id=version.mandate_version_id,
        mandate_id=version.mandate_id,
        version_number=version.version_number,
        base_currency=version.base_currency,
        authorized_capital_usd=Decimal(version.authorized_capital_usd),
        max_order_notional_usd=Decimal(version.max_order_notional_usd),
        max_open_exposure_usd=Decimal(version.max_open_exposure_usd),
        max_daily_deployed_usd=Decimal(version.max_daily_deployed_usd),
        max_daily_realized_loss_usd=Decimal(version.max_daily_realized_loss_usd),
        max_campaign_drawdown_usd=Decimal(version.max_campaign_drawdown_usd),
        max_consecutive_losses=version.max_consecutive_losses,
        position_limit=version.position_limit,
        price_evidence_max_age_seconds=version.price_evidence_max_age_seconds,
        max_slippage_bps=Decimal(version.max_slippage_bps),
        max_fee_bps=Decimal(version.max_fee_bps),
        allowed_products=tuple(version.allowed_products),
        allowed_order_sides=tuple(version.allowed_order_sides),
        allowed_strategy_versions=tuple(version.allowed_strategy_versions),
        approval_policy=version.approval_policy,
        is_authorized=bool(version.is_authorized),
        is_active=bool(version.is_active),
    )


async def _hydrate_authorization_model(
    *,
    db: AsyncSession,
    authorization: AutonomousCapitalMandateAuthorization,
) -> MandateAuthorizationModel:
    version = await db.get(AutonomousCapitalMandateVersion, authorization.mandate_version_id)
    mandate = await db.get(AutonomousCapitalMandate, authorization.mandate_id)
    return MandateAuthorizationModel(
        mandate_authorization_id=authorization.mandate_authorization_id,
        mandate_id=authorization.mandate_id,
        mandate_version_id=authorization.mandate_version_id,
        mandate_version_number=version.version_number if version is not None else None,
        autonomy_level=mandate.autonomy_level if mandate is not None else None,
        authorization_state=authorization.authorization_state,
        approval_result=authorization.approval_result,
        authorized_by_actor_id=authorization.authorized_by_actor_id,
        audit_correlation_id=authorization.audit_correlation_id,
        recorded_at=authorization.recorded_at,
        expires_at=authorization.expires_at,
        revoked_at=authorization.revoked_at,
    )


async def _validate_relationships(
    *,
    db: AsyncSession,
    exchange_connection_id: uuid.UUID,
    live_trading_profile_id: uuid.UUID,
    paper_account_id: uuid.UUID | None,
    capital_campaign_id: int | None,
) -> None:
    exchange_exists = await db.scalar(
        select(ExchangeConnection.exchange_connection_id)
        .where(ExchangeConnection.exchange_connection_id == exchange_connection_id)
        .limit(1)
    )
    if exchange_exists is None:
        raise InvalidRequestError(
            message="exchange_connection_id was not found",
            details={"exchange_connection_id": str(exchange_connection_id)},
        )

    profile_exists = await db.scalar(
        select(LiveTradingProfile.id).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )
    if profile_exists is None:
        raise InvalidRequestError(
            message="live_trading_profile_id was not found",
            details={"live_trading_profile_id": str(live_trading_profile_id)},
        )

    if paper_account_id is not None:
        paper_exists = await db.scalar(select(PaperAccount.id).where(PaperAccount.id == paper_account_id).limit(1))
        if paper_exists is None:
            raise InvalidRequestError(
                message="paper_account_id was not found",
                details={"paper_account_id": str(paper_account_id)},
            )

    if capital_campaign_id is not None:
        campaign_exists = await db.scalar(select(CapitalCampaign.id).where(CapitalCampaign.id == capital_campaign_id).limit(1))
        if campaign_exists is None:
            raise InvalidRequestError(
                message="capital_campaign_id was not found",
                details={"capital_campaign_id": capital_campaign_id},
            )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
