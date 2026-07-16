from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.audit_log import AuditLog
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_profile import LiveTradingProfile
from app.models.parameter_set import ParameterSet
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.strategy import Strategy
from app.services.live.approval import record_live_approval_checkpoint
from app.services.live.contracts import LiveApprovalCheckpointRequest

_PACKAGE_STATES = {
    "CREATED",
    "READY",
    "AUTHORIZED",
    "DRY_RUN_PASSED",
    "ACTIVATED",
    "EXPIRED",
    "INVALIDATED",
    "SUPERSEDED",
    "COMPLETED",
    "FAILED_CLOSED",
}

_ACTIVATION_STATES = {"ACTIVE", "PAUSED", "REVOKED", "EXPIRED", "INVALIDATED", "COMPLETED"}


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackageCreateRequest:
    campaign_id: uuid.UUID
    campaign_version: int
    paper_account_id: uuid.UUID
    live_trading_profile_id: uuid.UUID
    provider: str
    environment: str
    product: str
    max_proposed_order_amount: Decimal
    actor: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackageAuthorizeRequest:
    package_id: uuid.UUID
    actor: str
    approver_role: str
    rationale: str
    expires_at: datetime
    max_order_usd: Decimal
    max_total_deployed_campaign_capital_usd: Decimal
    no_leverage: bool
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackageDryRunRequest:
    package_id: uuid.UUID
    approval_event_id: uuid.UUID
    operator_identity: str
    idempotency_token: str


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackageActivationRequest:
    package_id: uuid.UUID
    approval_event_id: uuid.UUID
    dry_run_live_crypto_order_id: uuid.UUID
    actor: str
    expires_at: datetime
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackagePauseRequest:
    package_id: uuid.UUID
    actor: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackageRevokeRequest:
    package_id: uuid.UUID
    actor: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CanonicalPreviewPackageReadinessResult:
    ready: bool
    blockers: list[str]
    checks: list[dict[str, Any]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _serialize_decimal(value: Decimal) -> str:
    return format(value, "f")


def _serialize_uuid(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _input_fingerprint(request: CanonicalPreviewPackageCreateRequest) -> str:
    payload = json.dumps(
        {
            "campaign_id": str(request.campaign_id),
            "campaign_version": request.campaign_version,
            "paper_account_id": str(request.paper_account_id),
            "live_trading_profile_id": str(request.live_trading_profile_id),
            "provider": request.provider,
            "environment": request.environment,
            "product": request.product,
            "max_proposed_order_amount": _serialize_decimal(request.max_proposed_order_amount),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _load_package(*, db: AsyncSession, package_id: uuid.UUID) -> CanonicalPreviewPackage | None:
    return await db.scalar(select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == package_id).limit(1))


async def _find_package_by_id(*, db: AsyncSession, package_id: uuid.UUID) -> CanonicalPreviewPackage | None:
    return await _load_package(db=db, package_id=package_id)


async def _load_package_by_idempotency(*, db: AsyncSession, idempotency_key: str) -> CanonicalPreviewPackage | None:
    return await db.scalar(
        select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.idempotency_key == idempotency_key).limit(1)
    )


async def _load_activation(*, db: AsyncSession, package_id: uuid.UUID) -> CanonicalProvingActivation | None:
    return await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package_id).limit(1)
    )


async def _load_campaign_definition(*, db: AsyncSession, campaign_id: uuid.UUID, campaign_version: int) -> CapitalCampaignDefinition | None:
    return await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == campaign_version)
        .limit(1)
    )


async def _load_runtime_campaign(*, db: AsyncSession, campaign_id: uuid.UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == campaign_id).limit(1))


async def _load_profile(*, db: AsyncSession, live_trading_profile_id: uuid.UUID) -> LiveTradingProfile | None:
    return await db.scalar(
        select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1)
    )


async def _load_preview_for_package(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageCreateRequest,
) -> CryptoOrderPreview | None:
    result = await db.execute(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.provider == request.provider)
        .where(CryptoOrderPreview.environment == request.environment)
        .where(CryptoOrderPreview.product_id == request.product)
        .where(CryptoOrderPreview.requested_amount <= request.max_proposed_order_amount)
        .order_by(CryptoOrderPreview.created_at.desc(), CryptoOrderPreview.crypto_order_preview_id.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _load_decision_record(*, db: AsyncSession, decision_record_id: uuid.UUID) -> DecisionRecord | None:
    return await db.scalar(select(DecisionRecord).where(DecisionRecord.decision_id == decision_record_id).limit(1))


async def _load_risk_event(*, db: AsyncSession, risk_event_id: uuid.UUID) -> RiskEvent | None:
    return await db.scalar(select(RiskEvent).where(RiskEvent.id == risk_event_id).limit(1))


def _record_audit_entry(*, actor: str, action: str, entity_id: uuid.UUID, after_state: dict[str, Any]) -> AuditLog:
    return AuditLog(
        actor=actor,
        action=action,
        entity_type="canonical_proving_activation",
        entity_id=entity_id,
        before_state=None,
        after_state=after_state,
    )


def _package_payload(package: CanonicalPreviewPackage) -> dict[str, Any]:
    return {
        "package_id": str(package.package_id),
        "campaign_id": str(package.campaign_id),
        "campaign_version": package.campaign_version,
        "runtime_campaign_id": str(package.runtime_campaign_id),
        "paper_account_id": str(package.paper_account_id),
        "live_trading_profile_id": str(package.live_trading_profile_id),
        "provider": package.provider,
        "environment": package.environment,
        "product": package.product,
        "side": package.side,
        "proposed_order_amount": _serialize_decimal(_decimal(package.proposed_order_amount)),
        "risk_approved_amount": _serialize_decimal(_decimal(package.risk_approved_amount)),
        "strategy_id": str(package.strategy_id),
        "strategy_version": package.strategy_version,
        "parameter_set_id": str(package.parameter_set_id),
        "parameter_set_version": package.parameter_set_version,
        "decision_record_id": str(package.decision_record_id),
        "risk_event_id": str(package.risk_event_id),
        "crypto_order_preview_id": str(package.crypto_order_preview_id),
        "market_evidence_identity": package.market_evidence_identity,
        "market_evidence_observed_at": package.market_evidence_observed_at.isoformat() if package.market_evidence_observed_at else None,
        "preview_expires_at": package.preview_expires_at.isoformat(),
        "package_state": package.package_state,
        "generated_at": package.generated_at.isoformat(),
        "idempotency_key": package.idempotency_key,
        "input_fingerprint": package.input_fingerprint,
        "approval_event_id": _serialize_uuid(package.approval_event_id),
        "dry_run_live_crypto_order_id": _serialize_uuid(package.dry_run_live_crypto_order_id),
        "superseded_at": package.superseded_at.isoformat() if package.superseded_at else None,
        "invalidated_reason": package.invalidated_reason,
    }


def _activation_payload(activation: CanonicalProvingActivation) -> dict[str, Any]:
    return {
        "activation_id": str(activation.activation_id),
        "package_id": str(activation.package_id),
        "approval_event_id": str(activation.approval_event_id),
        "dry_run_live_crypto_order_id": str(activation.dry_run_live_crypto_order_id),
        "campaign_id": str(activation.campaign_id),
        "campaign_version": activation.campaign_version,
        "paper_account_id": str(activation.paper_account_id),
        "live_trading_profile_id": str(activation.live_trading_profile_id),
        "provider": activation.provider,
        "environment": activation.environment,
        "product": activation.product,
        "max_order_amount": _serialize_decimal(_decimal(activation.max_order_amount)),
        "max_deployed_capital": _serialize_decimal(_decimal(activation.max_deployed_capital)),
        "no_leverage": activation.no_leverage,
        "activated_at": activation.activated_at.isoformat(),
        "expires_at": activation.expires_at.isoformat(),
        "activation_state": activation.activation_state,
        "revoked_at": activation.revoked_at.isoformat() if activation.revoked_at else None,
        "paused_at": activation.paused_at.isoformat() if activation.paused_at else None,
        "invalidated_reason": activation.invalidated_reason,
    }


def _package_readiness(package: CanonicalPreviewPackage) -> dict[str, Any]:
    ready = package.package_state in {"READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}
    reason = None if ready else package.invalidated_reason or "package_not_ready"
    return {
        "ready": ready,
        "reason": reason,
        "package_state": package.package_state,
        "expires_at": package.preview_expires_at.isoformat(),
    }


async def inspect_canonical_preview_package_readiness(
    *,
    db: AsyncSession,
    package_id: uuid.UUID,
) -> CanonicalPreviewPackageReadinessResult:
    package = await _load_package(db=db, package_id=package_id)
    if package is None:
        return CanonicalPreviewPackageReadinessResult(ready=False, blockers=["package_not_found"], checks=[])
    readiness = _package_readiness(package)
    blockers = [] if readiness["ready"] else [str(readiness["reason"])]
    checks = [
        {
            "code": "package_state",
            "status": "pass" if readiness["ready"] else "fail",
            "detail": readiness["package_state"],
        }
    ]
    return CanonicalPreviewPackageReadinessResult(ready=bool(readiness["ready"]), blockers=blockers, checks=checks)


async def _latest_package_audits_for_campaign(*, db: AsyncSession, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(
                select(CanonicalPreviewPackage)
                .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                .order_by(CanonicalPreviewPackage.generated_at.desc())
            )
        ).scalars().all()
    )
    return [_package_payload(item) for item in rows]


async def create_canonical_preview_package(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageCreateRequest,
) -> dict[str, Any]:
    if request.max_proposed_order_amount > Decimal("5"):
        raise ValueError("max proposed order amount exceeds canonical bound")

    existing = await _load_package_by_idempotency(db=db, idempotency_key=request.idempotency_key)
    if existing is not None:
        if existing.input_fingerprint != _input_fingerprint(request):
            raise ValueError("idempotency key replay with different package input")
        return {"idempotent": True, "package": _package_payload(existing), "readiness": _package_readiness(existing)}

    profile = await _load_profile(db=db, live_trading_profile_id=request.live_trading_profile_id)
    if profile is None:
        raise LookupError("live trading profile not found")
    if profile.paper_account_id != request.paper_account_id:
        raise PermissionError("live trading profile paper account mismatch")

    runtime_campaign = await _load_runtime_campaign(db=db, campaign_id=request.campaign_id)
    if runtime_campaign is None:
        raise LookupError("capital campaign not found")

    definition = await _load_campaign_definition(db=db, campaign_id=request.campaign_id, campaign_version=request.campaign_version)
    if definition is None:
        raise LookupError("capital campaign definition not found")

    preview = await _load_preview_for_package(db=db, request=request)
    if preview is None:
        raise LookupError("crypto order preview not found for package scope")

    if preview.decision_record_id is None or preview.risk_event_id is None or preview.strategy_id is None:
        raise LookupError("preview evidence incomplete")

    decision = await _load_decision_record(db=db, decision_record_id=preview.decision_record_id)
    if decision is None:
        raise LookupError("decision record not found")

    risk_event = await _load_risk_event(db=db, risk_event_id=preview.risk_event_id)
    if risk_event is None:
        raise LookupError("risk event not found")

    strategy = await db.scalar(select(Strategy).where(Strategy.id == preview.strategy_id).limit(1))
    parameter_set = await db.scalar(select(ParameterSet).where(ParameterSet.id == preview.parameter_set_id).limit(1)) if preview.parameter_set_id is not None else None
    if strategy is None or parameter_set is None:
        raise LookupError("strategy or parameter set not found")

    package = CanonicalPreviewPackage(
        campaign_id=definition.campaign_id,
        campaign_version=definition.version,
        runtime_campaign_id=runtime_campaign.uuid,
        paper_account_id=profile.paper_account_id,
        live_trading_profile_id=profile.id,
        provider=request.provider,
        environment=request.environment,
        product=request.product,
        side=preview.side,
        proposed_order_amount=_decimal(preview.requested_amount),
        risk_approved_amount=_decimal(preview.requested_amount),
        strategy_id=strategy.id,
        strategy_version=getattr(strategy, "module_version", "unknown"),
        parameter_set_id=parameter_set.id,
        parameter_set_version=getattr(parameter_set, "label", "unknown"),
        decision_record_id=decision.decision_id,
        risk_event_id=risk_event.id,
        crypto_order_preview_id=preview.crypto_order_preview_id,
        market_evidence_identity={
            "provider": preview.provider,
            "environment": preview.environment,
            "product": preview.product_id,
            "exchange_connection_id": str(preview.exchange_connection_id),
        },
        market_evidence_observed_at=preview.created_at,
        preview_expires_at=preview.expires_at,
        package_state="READY",
        generated_at=_utcnow(),
        idempotency_key=request.idempotency_key,
        input_fingerprint=_input_fingerprint(request),
    )

    db.add(package)
    await db.flush()

    return {"idempotent": False, "package": _package_payload(package), "readiness": _package_readiness(package)}


async def get_canonical_preview_package(*, db: AsyncSession, package_id: uuid.UUID) -> dict[str, Any]:
    package = await _load_package(db=db, package_id=package_id)
    if package is None:
        raise LookupError("canonical preview package not found")
    return {"package": _package_payload(package), "readiness": _package_readiness(package)}


async def list_canonical_preview_package_history(
    *,
    db: AsyncSession,
    campaign_id: uuid.UUID,
    campaign_version: int | None,
    limit: int,
) -> dict[str, Any]:
    statement = select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.campaign_id == campaign_id)
    if campaign_version is not None:
        statement = statement.where(CanonicalPreviewPackage.campaign_version == campaign_version)
    statement = statement.order_by(CanonicalPreviewPackage.generated_at.desc()).limit(limit)
    rows = list((await db.execute(statement)).scalars().all())
    return {"items": [_package_payload(item) for item in rows], "count": len(rows)}


async def authorize_canonical_preview_package(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageAuthorizeRequest,
) -> dict[str, Any]:
    package = await _load_package(db=db, package_id=request.package_id)
    if package is None:
        raise LookupError("canonical preview package not found")
    if package.package_state in {"INVALIDATED", "SUPERSEDED", "COMPLETED", "FAILED_CLOSED"}:
        raise PermissionError("package is not eligible for authorization")
    if request.max_order_usd > Decimal("5") or request.max_total_deployed_campaign_capital_usd > Decimal("5"):
        raise PermissionError("bounded proving amount exceeds canonical cap")

    approval_scope = {
        "canonical_preview_package_id": str(package.package_id),
        "campaign_id": str(package.campaign_id),
        "campaign_version": str(package.campaign_version),
        "paper_account_id": str(package.paper_account_id),
        "live_trading_profile_id": str(package.live_trading_profile_id),
        "provider": package.provider,
        "environment": package.environment,
        "product": package.product,
        "side": package.side,
        "crypto_order_preview_id": str(package.crypto_order_preview_id),
        "strategy_version": package.strategy_version,
        "parameter_set_version": package.parameter_set_version,
        "max_order_usd": _serialize_decimal(request.max_order_usd),
        "max_total_deployed_campaign_capital_usd": _serialize_decimal(request.max_total_deployed_campaign_capital_usd),
        "no_leverage": bool(request.no_leverage),
    }

    checkpoint = await record_live_approval_checkpoint(
        db=db,
        request=LiveApprovalCheckpointRequest(
            live_trading_profile_id=package.live_trading_profile_id,
            checkpoint_type="bounded_proving_entry",
            approver_id=request.actor,
            approver_role=request.approver_role,
            rationale=request.rationale,
            approval_scope=approval_scope,
            expires_at=request.expires_at,
            renewal_condition="Renew bounded proving approval before activation",
            requested_by=request.actor,
            provenance_metadata={"canonical_preview_package_id": str(package.package_id)},
            idempotency_key=request.idempotency_key,
        ),
    )

    package.package_state = "AUTHORIZED"
    package.approval_event_id = checkpoint.approval_event_id
    await db.flush()

    payload = _package_payload(package)
    payload["approval_event_id"] = str(checkpoint.approval_event_id)
    payload["readiness"] = _package_readiness(package)
    payload["approval_scope"] = approval_scope
    payload["checkpoint_type"] = checkpoint.checkpoint_type
    return payload


async def run_dry_run_for_canonical_preview_package(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageDryRunRequest,
) -> dict[str, Any]:
    package = await _load_package(db=db, package_id=request.package_id)
    if package is None:
        raise LookupError("canonical preview package not found")
    if package.approval_event_id is None or package.approval_event_id != request.approval_event_id:
        raise PermissionError("approval event mismatch")
    if _decimal(package.risk_approved_amount) > Decimal("5"):
        raise PermissionError("bounded proving amount exceeds canonical cap")

    approval_event = await db.scalar(select(LiveApprovalEvent).where(LiveApprovalEvent.id == request.approval_event_id).limit(1))
    if approval_event is None:
        raise LookupError("approval event not found")
    if approval_event.approval_state != "approved":
        raise PermissionError("approval is not active")
    if approval_event.checkpoint_type != "bounded_proving_entry":
        raise PermissionError("approval checkpoint boundary violated")
    if approval_event.approval_scope.get("canonical_preview_package_id") != str(package.package_id):
        raise PermissionError("approval scope package mismatch")
    if approval_event.expires_at is not None and approval_event.expires_at <= _utcnow():
        raise PermissionError("approval expired")
    profile = await _load_profile(db=db, live_trading_profile_id=package.live_trading_profile_id)
    if profile is None:
        raise LookupError("live trading profile not found")

    dry_run_order = LiveCryptoOrder(
        crypto_order_preview_id=package.crypto_order_preview_id,
        exchange_connection_id=uuid.UUID(str(package.market_evidence_identity.get("exchange_connection_id"))) if package.market_evidence_identity.get("exchange_connection_id") else uuid.uuid4(),
        provider=package.provider,
        environment=package.environment,
        product_id=package.product,
        side=package.side,
        order_type="MARKET",
        requested_quote_size=_decimal(package.risk_approved_amount),
        client_order_id=f"cpp-{package.package_id}",
        status="DRY_RUN_READY",
        risk_event_id=package.risk_event_id,
        decision_record_id=package.decision_record_id,
        validation_run_id=None,
        provider_order_id=None,
        provider_status=None,
        submitted_at=None,
        acknowledged_at=None,
        filled_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={"submission_skipped": True, "dry_run": True},
        audit_correlation_id=uuid.uuid4(),
        operator_confirmation_id=None,
    )
    db.add(dry_run_order)
    await db.flush()

    package.package_state = "DRY_RUN_PASSED"
    package.dry_run_live_crypto_order_id = dry_run_order.live_crypto_order_id
    await db.flush()

    package_payload = _package_payload(package)
    package_payload["readiness"] = _package_readiness(package)
    return {
        "package": package_payload,
        "package_id": str(package.package_id),
        "dry_run_status": "DRY_RUN_READY",
        "dry_run_message": "dry run recorded against authoritative bounded proving package",
        "safe_request_summary": {
            "package_id": str(package.package_id),
            "approval_event_id": str(request.approval_event_id),
            "operator_identity": request.operator_identity,
        },
        "provider_create_order_called": False,
        "order_submitted": False,
        "submission_skipped": True,
        "submission_skip_reason": "bounded proving dry run only",
    }


async def activate_canonical_proving_campaign(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageActivationRequest,
) -> dict[str, Any]:
    package = await _load_package(db=db, package_id=request.package_id)
    if package is None:
        raise LookupError("canonical preview package not found")
    if package.approval_event_id is None or package.approval_event_id != request.approval_event_id:
        raise PermissionError("approval event mismatch")
    if package.dry_run_live_crypto_order_id is None or package.dry_run_live_crypto_order_id != request.dry_run_live_crypto_order_id:
        raise PermissionError("dry run order mismatch")
    if _decimal(package.risk_approved_amount) > Decimal("5"):
        raise PermissionError("bounded proving amount exceeds canonical cap")

    approval_event = await db.scalar(select(LiveApprovalEvent).where(LiveApprovalEvent.id == request.approval_event_id).limit(1))
    if approval_event is None:
        raise LookupError("approval event not found")
    if approval_event.approval_state != "approved":
        raise PermissionError("approval is not active")
    if approval_event.checkpoint_type != "bounded_proving_entry":
        raise PermissionError("approval checkpoint boundary violated")

    dry_run_order = await db.scalar(
        select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == request.dry_run_live_crypto_order_id).limit(1)
    )
    if dry_run_order is None:
        raise LookupError("dry run live crypto order not found")
    if dry_run_order.status != "DRY_RUN_READY":
        raise PermissionError("dry run submission boundary violated")

    existing = await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package.package_id).limit(1)
    )
    if existing is not None:
        if package.package_state != "ACTIVATED":
            package.package_state = "ACTIVATED"
            await db.flush()
        return {"activation": _activation_payload(existing), "package": _package_payload(package)}

    activation_id = uuid.uuid4()
    activation = CanonicalProvingActivation(
        activation_id=activation_id,
        package_id=package.package_id,
        approval_event_id=request.approval_event_id,
        dry_run_live_crypto_order_id=request.dry_run_live_crypto_order_id,
        campaign_id=package.campaign_id,
        campaign_version=package.campaign_version,
        paper_account_id=package.paper_account_id,
        live_trading_profile_id=package.live_trading_profile_id,
        provider=package.provider,
        environment=package.environment,
        product=package.product,
        max_order_amount=_decimal(package.risk_approved_amount),
        max_deployed_capital=_decimal(package.risk_approved_amount),
        no_leverage=True,
        activated_at=_utcnow(),
        expires_at=request.expires_at,
        activation_state="ACTIVE",
        revoked_at=None,
        paused_at=None,
        invalidated_reason=None,
    )
    db.add(activation)
    db.add(
        _record_audit_entry(
            actor=request.actor,
            action="canonical_proving_activation_created",
            entity_id=activation_id,
            after_state={"package_id": str(package.package_id), "activation_state": "ACTIVE"},
        )
    )
    await db.flush()

    package.package_state = "ACTIVATED"
    package.dry_run_live_crypto_order_id = request.dry_run_live_crypto_order_id
    await db.flush()

    return {"activation": _activation_payload(activation), "package": _package_payload(package)}


async def pause_canonical_proving_activation(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackagePauseRequest,
) -> dict[str, Any]:
    package = await _load_package(db=db, package_id=request.package_id)
    if package is None:
        raise LookupError("canonical preview package not found")
    activation = await _load_activation(db=db, package_id=request.package_id)
    if activation is None:
        raise LookupError("canonical proving activation not found")
    if activation.activation_state == "PAUSED":
        return {"activation": _activation_payload(activation), "package": _package_payload(package), "idempotent": True}
    if activation.activation_state not in {"ACTIVE", "PAUSED"}:
        raise PermissionError("canonical proving activation is not pausable")
    activation.activation_state = "PAUSED"
    activation.paused_at = _utcnow()
    activation.invalidated_reason = request.reason
    db.add(
        _record_audit_entry(
            actor=request.actor,
            action="canonical_proving_activation_paused",
            entity_id=activation.activation_id,
            after_state={"package_id": str(package.package_id), "reason": request.reason, "activation_state": "PAUSED"},
        )
    )
    await db.flush()
    return {"activation": _activation_payload(activation), "package": _package_payload(package), "idempotent": False}


async def revoke_canonical_proving_activation(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageRevokeRequest,
) -> dict[str, Any]:
    package = await _load_package(db=db, package_id=request.package_id)
    if package is None:
        raise LookupError("canonical preview package not found")
    activation = await _load_activation(db=db, package_id=request.package_id)
    if activation is None:
        raise LookupError("canonical proving activation not found")
    if activation.activation_state == "REVOKED":
        return {"activation": _activation_payload(activation), "package": _package_payload(package), "idempotent": True}
    if activation.activation_state not in {"ACTIVE", "PAUSED", "REVOKED"}:
        raise PermissionError("canonical proving activation is not revocable")
    activation.activation_state = "REVOKED"
    activation.revoked_at = _utcnow()
    activation.invalidated_reason = request.reason
    db.add(
        _record_audit_entry(
            actor=request.actor,
            action="canonical_proving_activation_revoked",
            entity_id=activation.activation_id,
            after_state={"package_id": str(package.package_id), "reason": request.reason, "activation_state": "REVOKED"},
        )
    )
    await db.flush()
    return {"activation": _activation_payload(activation), "package": _package_payload(package), "idempotent": False}


async def get_canonical_proving_activation_status(*, db: AsyncSession, package_id: uuid.UUID) -> dict[str, Any]:
    activation = await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package_id).limit(1)
    )
    if activation is None:
        return {"package_id": str(package_id), "activated": False, "activation": None}
    return {"package_id": str(package_id), "activated": activation.activation_state == "ACTIVE", "activation": _activation_payload(activation)}
