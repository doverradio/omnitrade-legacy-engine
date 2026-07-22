from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.autonomous_cycle_run import AutonomousCycleRun
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
from app.models.exchange_connection import ExchangeConnection
from app.services.capital_campaign_orchestration.service import run_campaign_orchestration_preview_for_candle
from app.services.crypto_order_previews.service import create_crypto_order_preview
from app.schemas.crypto_order_previews import CryptoOrderPreviewCreateRequest
from app.services.live.approval import record_live_approval_checkpoint
from app.services.live.contracts import LiveApprovalCheckpointRequest
from app.services.strategies.identity import parse_strategy_identity

logger = logging.getLogger(__name__)

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

_EXECUTABLE_ACTIONS = {"OPEN_POSITION_PROPOSED", "CLOSE_POSITION_PROPOSED"}
_FORCED_COMMISSIONING_MODE = "initial_proving_entry"
_TERMINAL_PACKAGE_STATES = {"EXPIRED", "INVALIDATED", "SUPERSEDED", "COMPLETED", "FAILED_CLOSED"}
_FORCED_REISSUE_RATIONALE = "expired_unused_initial_proving_entry_reissued"


def _diagnostic(*, code: str, stage: str, detail: str | None = None) -> dict[str, str]:
    payload = {"code": code, "stage": stage}
    if detail:
        payload["detail"] = detail
    return payload


def _preview_evidence_error(*, diagnostics: list[dict[str, str]]) -> LookupError:
    compact = ",".join(item["code"] for item in diagnostics)
    return LookupError(f"preview evidence incomplete: {compact}; diagnostics={json.dumps(diagnostics, sort_keys=True)}")


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
    commissioning_entry_mode: str | None = None


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


@dataclass(frozen=True, slots=True)
class ForcedSupersessionContext:
    reissued_from_package_id: uuid.UUID
    replacement_package_id: uuid.UUID
    audit_correlation_id: uuid.UUID
    rationale: str


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
            "commissioning_entry_mode": request.commissioning_entry_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _commissioned_blob(definition: CapitalCampaignDefinition | None) -> dict[str, Any]:
    if definition is None:
        return {}
    metadata = dict(getattr(definition, "metadata_evidence", {}) or {})
    blob = metadata.get("commissioned_seed_campaign")
    return blob if isinstance(blob, dict) else {}


def _is_forced_commissioning_mode(request: CanonicalPreviewPackageCreateRequest) -> bool:
    value = str(request.commissioning_entry_mode or "").strip().lower()
    return value == _FORCED_COMMISSIONING_MODE


def _forced_commissioning_guard_blocker(
    *,
    request: CanonicalPreviewPackageCreateRequest,
    definition: CapitalCampaignDefinition,
    runtime_campaign: CapitalCampaign,
    prior_packages: int,
) -> str | None:
    if not _is_forced_commissioning_mode(request):
        return None

    if str(definition.status or "").upper() != "READY" or str(runtime_campaign.status or "").upper() != "READY":
        return "commissioning_mode_requires_ready_definition_and_runtime"

    blob = _commissioned_blob(definition)
    state = str(blob.get("state") or "").strip().upper()
    if state != "READY":
        return "commissioning_mode_requires_commissioned_ready_state"

    commissioning = blob.get("commissioning") if isinstance(blob.get("commissioning"), dict) else {}
    authority = str(commissioning.get("authority_classification") or "").strip().upper()
    if authority and authority != "OPERATOR_COMMISSIONED":
        return "commissioning_mode_requires_operator_commissioned_authority"

    entry_execution = blob.get("entry_execution") if isinstance(blob.get("entry_execution"), dict) else {}
    if entry_execution:
        return "commissioning_mode_applies_only_to_initial_proving_entry"

    if prior_packages > 0:
        return "commissioning_mode_applies_only_to_first_canonical_package"

    return None


def _is_forced_initial_proving_package(package: CanonicalPreviewPackage) -> bool:
    identity = package.market_evidence_identity if isinstance(package.market_evidence_identity, dict) else {}
    return (
        str(identity.get("entry_authority") or "").strip().upper() == "OPERATOR_COMMISSIONED"
        and str(identity.get("entry_reason") or "").strip().upper() == "INITIAL_PROVING_ENTRY"
        and str(identity.get("strategy_override_scope") or "").strip().upper() == "COMMISSIONING_ENTRY_ONLY"
    )


def _is_terminal_package_state(package: CanonicalPreviewPackage) -> bool:
    return str(package.package_state or "").strip().upper() in _TERMINAL_PACKAGE_STATES


async def _maybe_supersede_forced_commissioning_package(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageCreateRequest,
    definition: CapitalCampaignDefinition,
    prior_packages: int,
) -> tuple[int, ForcedSupersessionContext | None]:
    if not _is_forced_commissioning_mode(request) or prior_packages <= 0:
        return prior_packages, None

    all_campaign_packages = await _load_campaign_packages(db=db, campaign_id=request.campaign_id)
    forced_packages = [item for item in all_campaign_packages if _is_forced_initial_proving_package(item)]
    if not forced_packages:
        return prior_packages, None

    if _decimal(definition.deployed_capital) > Decimal("0"):
        raise _preview_evidence_error(
            diagnostics=[
                _diagnostic(
                    code="commissioning_mode_reissue_blocked_by_deployed_capital",
                    stage="commissioning_mode",
                )
            ]
        )

    nonterminal = [item for item in forced_packages if not _is_terminal_package_state(item)]
    if len(nonterminal) > 1:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="commissioning_mode_multiple_nonterminal_packages", stage="commissioning_mode")]
        )
    if not nonterminal:
        return prior_packages, None

    prior = nonterminal[0]
    now = _utcnow()
    if prior.approval_event_id is not None:
        initial_approval = await _load_approval_event_by_id(db=db, approval_event_id=prior.approval_event_id)
        if initial_approval is None:
            raise _preview_evidence_error(
                diagnostics=[
                    _diagnostic(
                        code="commissioning_mode_reissue_blocked_by_unknown_approval_state",
                        stage="commissioning_mode",
                    )
                ]
            )

        latest_scoped_approval = await _load_latest_package_scoped_approval_event(
            db=db,
            live_trading_profile_id=prior.live_trading_profile_id,
            package_id=prior.package_id,
        )
        effective_approval = latest_scoped_approval or initial_approval

        approval_scope = (
            effective_approval.approval_scope if isinstance(effective_approval.approval_scope, dict) else {}
        )
        if str(approval_scope.get("canonical_preview_package_id") or "") != str(prior.package_id):
            raise _preview_evidence_error(
                diagnostics=[
                    _diagnostic(
                        code="commissioning_mode_reissue_blocked_by_approval_scope_mismatch",
                        stage="commissioning_mode",
                    )
                ]
            )

        approval_state = str(effective_approval.approval_state or "").strip().lower()
        approval_expired = (
            approval_state == "expired"
            or (
                approval_state == "approved"
                and effective_approval.expires_at is not None
                and effective_approval.expires_at <= now
            )
        )
        approval_revoked = approval_state == "revoked"

        if not (approval_expired or approval_revoked):
            raise _preview_evidence_error(
                diagnostics=[_diagnostic(code="commissioning_mode_reissue_blocked_by_approved_package", stage="commissioning_mode")]
            )
    if prior.dry_run_live_crypto_order_id is not None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="commissioning_mode_reissue_blocked_by_dry_run_package", stage="commissioning_mode")]
        )

    activation = await _load_activation(db=db, package_id=prior.package_id)
    if activation is not None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="commissioning_mode_reissue_blocked_by_activation", stage="commissioning_mode")]
        )

    linked_order = await _load_live_order_for_preview(db=db, preview_id=prior.crypto_order_preview_id)
    if linked_order is not None:
        if linked_order.filled_at is not None:
            code = "commissioning_mode_reissue_blocked_by_filled_order"
        elif linked_order.provider_order_id is not None:
            code = "commissioning_mode_reissue_blocked_by_provider_order_link"
        elif linked_order.submitted_at is not None:
            code = "commissioning_mode_reissue_blocked_by_submitted_order"
        else:
            code = "commissioning_mode_reissue_blocked_by_order_link"
        raise _preview_evidence_error(diagnostics=[_diagnostic(code=code, stage="commissioning_mode")])

    if prior.preview_expires_at > now:
        return prior_packages, None

    replacement_package_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    before_state = {
        "package_state": prior.package_state,
        "approval_event_id": _serialize_uuid(prior.approval_event_id),
        "dry_run_live_crypto_order_id": _serialize_uuid(prior.dry_run_live_crypto_order_id),
        "preview_expires_at": prior.preview_expires_at.isoformat(),
        "crypto_order_preview_id": str(prior.crypto_order_preview_id),
    }

    prior.package_state = "SUPERSEDED"
    prior.superseded_at = now
    prior.invalidated_reason = _FORCED_REISSUE_RATIONALE
    identity = dict(prior.market_evidence_identity or {})
    identity["replacement_package_id"] = str(replacement_package_id)
    identity["expired_approval_event_id"] = _serialize_uuid(prior.approval_event_id)
    identity["superseded_by_actor"] = request.actor
    identity["supersession_rationale"] = _FORCED_REISSUE_RATIONALE
    identity["supersession_audit_correlation_id"] = str(correlation_id)
    prior.market_evidence_identity = identity
    await db.flush()

    db.add(
        AuditLog(
            actor=request.actor,
            action="canonical_preview_package_superseded_for_reissue",
            entity_type="canonical_preview_package",
            entity_id=prior.package_id,
            before_state=before_state,
            after_state={
                "package_state": prior.package_state,
                "superseded_at": prior.superseded_at.isoformat(),
                "invalidated_reason": prior.invalidated_reason,
                "expired_approval_event_id": _serialize_uuid(prior.approval_event_id),
                "replacement_package_id": str(replacement_package_id),
                "actor": request.actor,
                "rationale": _FORCED_REISSUE_RATIONALE,
                "audit_correlation_id": str(correlation_id),
            },
        )
    )

    return 0, ForcedSupersessionContext(
        reissued_from_package_id=prior.package_id,
        replacement_package_id=replacement_package_id,
        audit_correlation_id=correlation_id,
        rationale=_FORCED_REISSUE_RATIONALE,
    )


async def _load_package(*, db: AsyncSession, package_id: uuid.UUID) -> CanonicalPreviewPackage | None:
    return await db.scalar(select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.package_id == package_id).limit(1))


async def _find_package_by_id(*, db: AsyncSession, package_id: uuid.UUID) -> CanonicalPreviewPackage | None:
    return await _load_package(db=db, package_id=package_id)


async def _load_package_by_idempotency(*, db: AsyncSession, idempotency_key: str) -> CanonicalPreviewPackage | None:
    return await db.scalar(
        select(CanonicalPreviewPackage).where(CanonicalPreviewPackage.idempotency_key == idempotency_key).limit(1)
    )


async def _load_campaign_packages(*, db: AsyncSession, campaign_id: uuid.UUID) -> list[CanonicalPreviewPackage]:
    return list(
        (
            await db.execute(
                select(CanonicalPreviewPackage)
                .where(CanonicalPreviewPackage.campaign_id == campaign_id)
                .order_by(CanonicalPreviewPackage.generated_at.desc(), CanonicalPreviewPackage.package_id.desc())
            )
        )
        .scalars()
        .all()
    )


async def _load_activation(*, db: AsyncSession, package_id: uuid.UUID) -> CanonicalProvingActivation | None:
    return await db.scalar(
        select(CanonicalProvingActivation).where(CanonicalProvingActivation.package_id == package_id).limit(1)
    )


async def _load_live_order_for_preview(*, db: AsyncSession, preview_id: uuid.UUID) -> LiveCryptoOrder | None:
    return await db.scalar(
        select(LiveCryptoOrder)
        .where(LiveCryptoOrder.crypto_order_preview_id == preview_id)
        .order_by(LiveCryptoOrder.created_at.desc(), LiveCryptoOrder.live_crypto_order_id.desc())
        .limit(1)
    )


async def _load_approval_event_by_id(*, db: AsyncSession, approval_event_id: uuid.UUID) -> LiveApprovalEvent | None:
    return await db.scalar(
        select(LiveApprovalEvent).where(LiveApprovalEvent.id == approval_event_id).limit(1)
    )


async def _load_latest_package_scoped_approval_event(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    package_id: uuid.UUID,
) -> LiveApprovalEvent | None:
    return await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.live_trading_profile_id == live_trading_profile_id)
        .where(LiveApprovalEvent.checkpoint_type == "bounded_proving_entry")
        .where(LiveApprovalEvent.approval_scope["canonical_preview_package_id"].astext == str(package_id))
        .order_by(LiveApprovalEvent.sequence_number.desc())
        .limit(1)
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
    observed_after: datetime | None = None,
) -> CryptoOrderPreview | None:
    statement = (
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.provider == request.provider)
        .where(CryptoOrderPreview.environment == request.environment)
        .where(CryptoOrderPreview.product_id == request.product)
        .where(CryptoOrderPreview.requested_amount <= request.max_proposed_order_amount)
        .order_by(CryptoOrderPreview.created_at.desc(), CryptoOrderPreview.crypto_order_preview_id.desc())
        .limit(1)
    )
    if observed_after is not None:
        statement = statement.where(CryptoOrderPreview.created_at >= observed_after)
    result = await db.execute(statement)
    return result.scalars().first()


async def _load_preview_by_id(*, db: AsyncSession, preview_id: uuid.UUID) -> CryptoOrderPreview | None:
    return await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.crypto_order_preview_id == preview_id)
        .limit(1)
    )


async def _load_exchange_connection_for_scope(
    *,
    db: AsyncSession,
    provider: str,
    environment: str,
) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == provider)
        .where(ExchangeConnection.environment == environment)
        .order_by(ExchangeConnection.created_at.desc(), ExchangeConnection.exchange_connection_id.desc())
        .limit(1)
    )


def _profile_provider_environment(profile: LiveTradingProfile) -> tuple[str | None, str | None]:
    provenance_raw = getattr(profile, "provenance_metadata", None)
    provenance = provenance_raw if isinstance(provenance_raw, dict) else {}
    provider = str(provenance.get("provider") or "").strip().lower() or None
    environment = str(provenance.get("exchange_environment") or provenance.get("environment") or "").strip().lower() or None
    return provider, environment


def _selected_decision_record_id(selected_decision: dict[str, Any]) -> uuid.UUID | None:
    direct = str(selected_decision.get("decision_record_id") or "").strip()
    if direct:
        try:
            return uuid.UUID(direct)
        except ValueError:
            return None
    source_identity = selected_decision.get("source_identity") if isinstance(selected_decision.get("source_identity"), dict) else {}
    nested = str(source_identity.get("decision_record_id") or "").strip()
    if not nested:
        return None
    try:
        return uuid.UUID(nested)
    except ValueError:
        return None


def _selected_strategy_identity(*, selected_decision: dict[str, Any], composition: dict[str, Any]) -> str | None:
    source_identity = selected_decision.get("source_identity") if isinstance(selected_decision.get("source_identity"), dict) else {}
    candidates = [
        selected_decision.get("strategy_identity"),
        source_identity.get("strategy_identity"),
        composition.get("strategy_identity"),
    ]
    for candidate in candidates:
        raw = str(candidate or "").strip()
        if raw and parse_strategy_identity(raw) is not None:
            return raw
    return None


async def _resolve_strategy_and_parameter_binding(
    *,
    db: AsyncSession,
    strategy_identity: str,
) -> tuple[Strategy | None, ParameterSet | None]:
    parsed = parse_strategy_identity(strategy_identity)
    if parsed is None:
        return None, None
    slug, module_version = parsed
    strategy = await db.scalar(
        select(Strategy)
        .where(Strategy.slug == slug)
        .where(Strategy.module_version == module_version)
        .where(Strategy.is_active.is_(True))
        .limit(1)
    )
    if strategy is None:
        return None, None
    parameter_set = await db.scalar(
        select(ParameterSet)
        .where(ParameterSet.strategy_id == strategy.id)
        .order_by(ParameterSet.created_at.desc())
        .limit(1)
    )
    return strategy, parameter_set


async def _create_crypto_order_preview_for_package(
    *,
    db: AsyncSession,
    request: CanonicalPreviewPackageCreateRequest,
    profile: LiveTradingProfile,
    composition: dict[str, Any],
    selected_decision: dict[str, Any],
) -> CryptoOrderPreview:
    profile_provider, profile_environment = _profile_provider_environment(profile)
    normalized_provider = request.provider.strip().lower()
    normalized_environment = request.environment.strip().lower()
    if profile_provider and profile_provider != normalized_provider:
        raise _preview_evidence_error(
            diagnostics=[
                _diagnostic(
                    code="canonical_profile_provider_mismatch",
                    stage="preview_resolution",
                    detail=f"profile_provider={profile_provider}",
                )
            ]
        )
    if profile_environment and profile_environment != normalized_environment:
        raise _preview_evidence_error(
            diagnostics=[
                _diagnostic(
                    code="canonical_profile_environment_mismatch",
                    stage="preview_resolution",
                    detail=f"profile_environment={profile_environment}",
                )
            ]
        )

    connection = await _load_exchange_connection_for_scope(
        db=db,
        provider=request.provider,
        environment=request.environment,
    )
    if connection is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_exchange_connection_missing", stage="preview_resolution")]
        )

    decision_record_id = _selected_decision_record_id(selected_decision)
    if decision_record_id is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_decision_record_id_missing", stage="preview_resolution")]
        )

    strategy_identity = _selected_strategy_identity(selected_decision=selected_decision, composition=composition)
    if not strategy_identity:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_strategy_version_missing", stage="preview_resolution")]
        )

    strategy, parameter_set = await _resolve_strategy_and_parameter_binding(
        db=db,
        strategy_identity=strategy_identity,
    )
    if strategy is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_strategy_id_missing", stage="preview_resolution")]
        )
    if parameter_set is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_parameter_set_id_missing", stage="preview_resolution")]
        )

    preview_response = await create_crypto_order_preview(
        db=db,
        request=CryptoOrderPreviewCreateRequest(
            exchange_connection_id=connection.exchange_connection_id,
            environment=request.environment,
            product_id=request.product,
            side="BUY",
            order_type="MARKET",
            quote_size=request.max_proposed_order_amount,
            requested_amount_currency="USD",
            decision_record_id=decision_record_id,
            strategy_id=strategy.id,
            strategy_name=strategy.slug,
            generated_by="system_recommendation",
            client_request_id=f"canonical-forced-preview:{request.idempotency_key}",
        ),
        actor=request.actor,
    )

    preview = await _load_preview_by_id(db=db, preview_id=preview_response.crypto_order_preview_id)
    if preview is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_crypto_order_preview_id_missing", stage="preview_resolution")]
        )
    if str(preview.status or "").upper() != "PREVIEW_READY":
        raise _preview_evidence_error(
            diagnostics=[
                _diagnostic(
                    code="canonical_crypto_order_preview_not_ready",
                    stage="preview_resolution",
                    detail=str(preview.status),
                )
            ]
        )
    if preview.parameter_set_id is None:
        preview.parameter_set_id = parameter_set.id
        await db.flush()

    return preview


async def _load_decision_record(*, db: AsyncSession, decision_record_id: uuid.UUID) -> DecisionRecord | None:
    return await db.scalar(select(DecisionRecord).where(DecisionRecord.decision_id == decision_record_id).limit(1))


async def _load_risk_event(*, db: AsyncSession, risk_event_id: uuid.UUID) -> RiskEvent | None:
    return await db.scalar(select(RiskEvent).where(RiskEvent.id == risk_event_id).limit(1))


async def _load_campaign_cycle(*, db: AsyncSession, cycle_id: uuid.UUID) -> AutonomousCycleRun | None:
    return await db.scalar(select(AutonomousCycleRun).where(AutonomousCycleRun.cycle_id == cycle_id).limit(1))


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
    market_identity = package.market_evidence_identity if isinstance(package.market_evidence_identity, dict) else {}
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
        "entry_authority": market_identity.get("entry_authority"),
        "entry_reason": market_identity.get("entry_reason"),
        "strategy_override_scope": market_identity.get("strategy_override_scope"),
        "requested_quote_size": market_identity.get("requested_quote_size"),
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
    if request.max_proposed_order_amount != Decimal("5"):
        raise ValueError("max proposed order amount must equal canonical bound of 5")
    mode_value = str(request.commissioning_entry_mode or "").strip().lower()
    if mode_value and mode_value != _FORCED_COMMISSIONING_MODE:
        raise ValueError(f"unsupported commissioning_entry_mode: {request.commissioning_entry_mode}")

    existing = await _load_package_by_idempotency(db=db, idempotency_key=request.idempotency_key)
    if existing is not None:
        if existing.input_fingerprint != _input_fingerprint(request):
            raise ValueError("idempotency key replay with different package input")
        return {"idempotent": True, "package": _package_payload(existing), "readiness": _package_readiness(existing)}

    profile = await _load_profile(db=db, live_trading_profile_id=request.live_trading_profile_id)
    if profile is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_live_trading_profile_missing", stage="profile_resolution")]
        )
    if profile.paper_account_id != request.paper_account_id:
        raise _preview_evidence_error(
            diagnostics=[
                _diagnostic(
                    code="canonical_paper_account_missing",
                    stage="profile_resolution",
                    detail="live_trading_profile_paper_account_mismatch",
                )
            ]
        )

    runtime_campaign = await _load_runtime_campaign(db=db, campaign_id=request.campaign_id)
    if runtime_campaign is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_runtime_campaign_missing", stage="campaign_resolution")]
        )

    definition = await _load_campaign_definition(db=db, campaign_id=request.campaign_id, campaign_version=request.campaign_version)
    if definition is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_campaign_definition_missing", stage="campaign_resolution")]
        )

    prior_packages = await db.scalar(
        select(func.count(CanonicalPreviewPackage.package_id)).where(CanonicalPreviewPackage.campaign_id == request.campaign_id)
    )
    effective_prior_packages, supersession_context = await _maybe_supersede_forced_commissioning_package(
        db=db,
        request=request,
        definition=definition,
        prior_packages=int(prior_packages or 0),
    )
    forced_blocker = _forced_commissioning_guard_blocker(
        request=request,
        definition=definition,
        runtime_campaign=runtime_campaign,
        prior_packages=effective_prior_packages,
    )
    if forced_blocker is not None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code=forced_blocker, stage="commissioning_mode")]
        )

    orchestration = await run_campaign_orchestration_preview_for_candle(
        db=db,
        campaign_id=request.campaign_id,
        version=request.campaign_version,
        allow_draft_preview=True,
    )
    cycles = orchestration.get("cycles") or []
    if not cycles:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_orchestration_cycle_missing", stage="canonical_orchestration")]
        )

    latest_cycle_summary = cycles[-1]
    cycle_id_raw = latest_cycle_summary.get("cycle_id")
    if cycle_id_raw is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_orchestration_cycle_missing", stage="canonical_orchestration", detail="cycle_id_missing")]
        )
    cycle = await _load_campaign_cycle(db=db, cycle_id=uuid.UUID(str(cycle_id_raw)))
    if cycle is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_orchestration_cycle_missing", stage="canonical_orchestration", detail="cycle_row_missing")]
        )

    cycle_context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    composition = cycle_context.get("authoritative_composition") if isinstance(cycle_context.get("authoritative_composition"), dict) else {}
    proposed_action = str(composition.get("proposed_action") or cycle.proposed_action or "").strip().upper()
    selected_decision = composition.get("selected_decision") if isinstance(composition.get("selected_decision"), dict) else {}
    decision_kind = str(selected_decision.get("decision_kind") or "").strip().upper()

    if cycle.failure_reason == "runtime_campaign_or_paper_account_unavailable":
        diagnostics = [
            _diagnostic(code="canonical_runtime_campaign_missing", stage="canonical_orchestration"),
            _diagnostic(code="canonical_paper_account_missing", stage="canonical_orchestration"),
        ]
        raise _preview_evidence_error(diagnostics=diagnostics)

    is_hold_cycle = (
        cycle.termination_stage in {"hold_terminal", "hold_no_package_created"}
        or proposed_action in {"NO_ACTION", "HOLD"}
        or decision_kind in {"NO_ACTION", "HOLD"}
    )
    if is_hold_cycle:
        hold_reason = str(selected_decision.get("reason") or cycle.failure_reason or "no_executable_opportunity")
        can_force_commissioning_entry = _is_forced_commissioning_mode(request) and hold_reason == "strategy_hold_signal"
        if not can_force_commissioning_entry:
            return {
                "idempotent": False,
                "outcome_code": "HOLD_NO_PACKAGE_CREATED",
                "reason_code": "canonical_action_hold",
                "reason_detail": hold_reason,
                "stage": "canonical_orchestration",
                "package": None,
                "campaign_cycle": {
                    "cycle_id": str(cycle.cycle_id),
                    "state": cycle.state,
                    "termination_stage": cycle.termination_stage,
                    "failure_reason": cycle.failure_reason,
                },
                "diagnostics": [
                    _diagnostic(code="canonical_action_hold", stage="canonical_orchestration", detail=hold_reason),
                ],
            }

        # Initial operator-commissioned proving entry can bypass strategy HOLD only.
        proposed_action = "OPEN_POSITION_PROPOSED"

    if proposed_action and proposed_action not in _EXECUTABLE_ACTIONS:
        raise _preview_evidence_error(
            diagnostics=[
                _diagnostic(
                    code="canonical_action_not_executable",
                    stage="canonical_orchestration",
                    detail=f"proposed_action={proposed_action}",
                )
            ]
        )

    # _load_preview_for_package only ever finds a CryptoOrderPreview row that
    # some OTHER flow already created (e.g. a manual/API preview, or a prior
    # canonical package's own preview). For the capital-campaign orchestration
    # path there is no such upstream creator for a normal, already-validated
    # executable decision (BUY/OPEN_POSITION_PROPOSED/CLOSE_POSITION_PROPOSED)
    # -- by this point proposed_action has already been confirmed executable
    # (line 967 above) and, for a hold cycle, confirmed to be a legitimate
    # forced-commissioning bypass (the early-return above exits for every
    # other hold case) -- so it is always safe to create the preview
    # ourselves here rather than only for the forced-commissioning bypass.
    # Confirmed production defect: a genuine, economically-accepted,
    # non-forced OPEN_POSITION_PROPOSED decision had no code path that ever
    # persisted its CryptoOrderPreview, so this lookup always returned None
    # and the cycle crashed with canonical_crypto_order_preview_id_missing
    # instead of completing.
    preview = await _load_preview_for_package(db=db, request=request, observed_after=cycle.started_at)
    if preview is None:
        preview = await _create_crypto_order_preview_for_package(
            db=db,
            request=request,
            profile=profile,
            composition=composition,
            selected_decision=selected_decision,
        )
    if preview is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_crypto_order_preview_id_missing", stage="preview_resolution")]
        )

    diagnostics: list[dict[str, str]] = []
    if preview.decision_record_id is None:
        diagnostics.append(_diagnostic(code="canonical_decision_record_id_missing", stage="preview_resolution"))
    if preview.risk_event_id is None:
        diagnostics.append(_diagnostic(code="canonical_risk_event_id_missing", stage="preview_resolution"))
    if preview.strategy_id is None:
        diagnostics.append(_diagnostic(code="canonical_strategy_id_missing", stage="preview_resolution"))
    if preview.parameter_set_id is None:
        diagnostics.append(_diagnostic(code="canonical_parameter_set_id_missing", stage="preview_resolution"))
    if preview.expires_at is None:
        diagnostics.append(_diagnostic(code="canonical_preview_expiration_missing", stage="preview_resolution"))
    elif preview.expires_at <= _utcnow():
        diagnostics.append(_diagnostic(code="canonical_price_evidence_stale", stage="preview_resolution"))
    if preview.created_at is None:
        diagnostics.append(_diagnostic(code="canonical_price_evidence_missing", stage="preview_resolution"))
    if preview.requested_amount is None:
        diagnostics.append(_diagnostic(code="canonical_risk_approved_amount_missing", stage="preview_resolution"))
    if not str(preview.provider or "").strip() or not str(preview.environment or "").strip():
        diagnostics.append(_diagnostic(code="canonical_provider_identity_missing", stage="preview_resolution"))
    if not str(preview.product_id or "").strip():
        diagnostics.append(_diagnostic(code="canonical_asset_identity_missing", stage="preview_resolution"))
    if diagnostics:
        raise _preview_evidence_error(diagnostics=diagnostics)

    decision = await _load_decision_record(db=db, decision_record_id=preview.decision_record_id)
    if decision is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_decision_record_id_missing", stage="decision_resolution")]
        )

    risk_event = await _load_risk_event(db=db, risk_event_id=preview.risk_event_id)
    if risk_event is None:
        raise _preview_evidence_error(
            diagnostics=[_diagnostic(code="canonical_risk_event_id_missing", stage="risk_resolution")]
        )

    strategy = await db.scalar(select(Strategy).where(Strategy.id == preview.strategy_id).limit(1))
    parameter_set = await db.scalar(select(ParameterSet).where(ParameterSet.id == preview.parameter_set_id).limit(1)) if preview.parameter_set_id is not None else None
    if strategy is None or parameter_set is None:
        diagnostics = []
        if strategy is None:
            diagnostics.append(_diagnostic(code="canonical_strategy_id_missing", stage="strategy_resolution"))
            diagnostics.append(_diagnostic(code="canonical_strategy_version_missing", stage="strategy_resolution"))
        if parameter_set is None:
            diagnostics.append(_diagnostic(code="canonical_parameter_set_id_missing", stage="strategy_resolution"))
            diagnostics.append(_diagnostic(code="canonical_parameter_set_version_missing", stage="strategy_resolution"))
        raise _preview_evidence_error(diagnostics=diagnostics)

    package_identity_kwargs: dict[str, Any] = {}
    if supersession_context is not None:
        package_identity_kwargs["package_id"] = supersession_context.replacement_package_id

    package = CanonicalPreviewPackage(
        **package_identity_kwargs,
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
            "entry_authority": "OPERATOR_COMMISSIONED" if _is_forced_commissioning_mode(request) else "AUTONOMOUS_STRATEGY",
            "entry_reason": "INITIAL_PROVING_ENTRY" if _is_forced_commissioning_mode(request) else "AUTONOMOUS_SELECTION",
            "strategy_override_scope": "COMMISSIONING_ENTRY_ONLY" if _is_forced_commissioning_mode(request) else "NONE",
            "requested_quote_size": _serialize_decimal(request.max_proposed_order_amount),
            "reissued_from_package_id": (
                str(supersession_context.reissued_from_package_id) if supersession_context is not None else None
            ),
            "supersession_audit_correlation_id": (
                str(supersession_context.audit_correlation_id) if supersession_context is not None else None
            ),
            "supersession_rationale": supersession_context.rationale if supersession_context is not None else None,
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
        "capital_campaign_id": str(package.runtime_campaign_id),
        "capital_campaign_version": str(package.campaign_version),
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

    logger.info(
        "automatic_ready_package_activation_started campaign_id=%s campaign_version=%s package_id=%s decision_record_id=%s approval_event_id=%s actor=%s",
        package.campaign_id, package.campaign_version, package.package_id, package.decision_record_id, checkpoint.approval_event_id, request.actor,
    )

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
        if existing.activation_state != "ACTIVE":
            raise PermissionError("canonical proving activation is not active and cannot be renewed")
        if existing.approval_event_id != request.approval_event_id:
            existing.approval_event_id = request.approval_event_id
            existing.expires_at = request.expires_at
            await db.flush()
        if package.package_state != "ACTIVATED":
            package.package_state = "ACTIVATED"
            await db.flush()
        logger.info(
            "automatic_ready_package_activated campaign_id=%s campaign_version=%s package_id=%s activation_id=%s reused=True",
            package.campaign_id, package.campaign_version, package.package_id, existing.activation_id,
        )
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

    logger.info(
        "automatic_ready_package_activated campaign_id=%s campaign_version=%s package_id=%s activation_id=%s reused=False",
        package.campaign_id, package.campaign_version, package.package_id, activation_id,
    )
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
