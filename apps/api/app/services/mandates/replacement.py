from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.services.mandates.contracts import (
    MandateAuthorizationRequest,
    MandateAuthorizationModel,
    MandateVersionCreateRequest,
    MandateVersionModel,
    MandateVersionReplacementRequest,
    MandateVersionReplacementResult,
)
from app.services.mandates.lifecycle import (
    authorize_mandate_version,
    create_mandate_version,
    get_mandate,
    list_mandate_authorizations,
    list_mandate_versions,
)
from app.services.strategies.identity import build_strategy_identity


_LEGACY_REPLACEMENT_ALLOWLIST = ("1.0.0",)


@dataclass(frozen=True)
class ReplacementVersionSummary:
    mandate_version_id: uuid.UUID
    version_number: int
    allowed_strategy_versions: tuple[str, ...]
    is_authorized: bool
    is_active: bool
    policy_hash: str


@dataclass(frozen=True)
class ReplacementAuthorizationSummary:
    mandate_authorization_id: uuid.UUID
    mandate_version_id: uuid.UUID
    mandate_version_number: int | None
    authorization_state: str
    approval_result: str
    recorded_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class ReplacementDryRunReport:
    mandate_id: uuid.UUID
    mandate_status: str
    source_mandate_version_id: uuid.UUID
    source_mandate_authorization_id: uuid.UUID
    current_governing_version_id: uuid.UUID | None
    current_governing_strategy_identity: str | None
    source_version_number: int
    source_allowed_strategy_versions: tuple[str, ...]
    source_policy_hash: str
    proposed_replacement_strategy_versions: tuple[str, ...]
    proposed_policy_hash: str
    replacement_required: bool
    stop_reason: str | None
    versions_in_order: tuple[ReplacementVersionSummary, ...]
    exact_version_authorizations: tuple[ReplacementAuthorizationSummary, ...]


@dataclass(frozen=True)
class ReplacementExecutionReport:
    dry_run: ReplacementDryRunReport
    result: MandateVersionReplacementResult


async def dry_run_governing_version_replacement(
    *,
    db: AsyncSession,
    request: MandateVersionReplacementRequest,
) -> ReplacementDryRunReport:
    mandate = await get_mandate(db=db, mandate_id=request.mandate_id)
    versions = await list_mandate_versions(db=db, mandate_id=request.mandate_id)
    authorizations = await list_mandate_authorizations(db=db, mandate_id=request.mandate_id)
    if not versions:
        raise InvalidRequestError(message="Mandate has no versions to replace", details={"mandate_id": str(request.mandate_id)})

    if mandate.status != "ACTIVE":
        return ReplacementDryRunReport(
            mandate_id=mandate.mandate_id,
            mandate_status=mandate.status,
            source_mandate_version_id=request.source_mandate_version_id,
            source_mandate_authorization_id=request.source_mandate_authorization_id,
            current_governing_version_id=versions[0].mandate_version_id,
            current_governing_strategy_identity=_current_identity(versions[0]),
            source_version_number=0,
            source_allowed_strategy_versions=(),
            source_policy_hash="",
            proposed_replacement_strategy_versions=request.replacement_allowed_strategy_versions,
            proposed_policy_hash="",
            replacement_required=False,
            stop_reason=f"mandate_status_must_be_ACTIVE:{mandate.status}",
            versions_in_order=tuple(_summarize_version(item) for item in versions),
            exact_version_authorizations=tuple(_summarize_authorization(item) for item in authorizations),
        )

    current_governing_version = versions[0]
    source_version = next((item for item in versions if item.mandate_version_id == request.source_mandate_version_id), None)
    if source_version is None:
        raise InvalidRequestError(
            message="Source mandate version not found for mandate",
            details={"mandate_id": str(request.mandate_id), "source_mandate_version_id": str(request.source_mandate_version_id)},
        )

    source_authorization = next(
        (
            item
            for item in authorizations
            if item.mandate_authorization_id == request.source_mandate_authorization_id
            and item.mandate_version_id == request.source_mandate_version_id
        ),
        None,
    )
    if source_authorization is None:
        raise InvalidRequestError(
            message="Source authorization not found for source version",
            details={
                "mandate_id": str(request.mandate_id),
                "source_mandate_version_id": str(request.source_mandate_version_id),
                "source_mandate_authorization_id": str(request.source_mandate_authorization_id),
            },
        )

    versions_in_order = tuple(_summarize_version(item) for item in versions)
    exact_version_authorizations = tuple(_summarize_authorization(item) for item in authorizations)

    source_policy_hash = _policy_hash(source_version)
    proposed_policy_hash = _policy_hash(source_version, allowed_strategy_versions=request.replacement_allowed_strategy_versions)
    current_governing_identity = _current_identity(current_governing_version)

    stop_reason = _validate_dry_run(
        source_version=source_version,
        source_authorization=source_authorization,
        current_governing_version=current_governing_version,
        replacement_allowed_strategy_versions=request.replacement_allowed_strategy_versions,
        replacement_identity=request.replacement_allowed_strategy_versions[0] if request.replacement_allowed_strategy_versions else None,
    )

    replacement_required = stop_reason is None
    return ReplacementDryRunReport(
        mandate_id=mandate.mandate_id,
        mandate_status=mandate.status,
        source_mandate_version_id=source_version.mandate_version_id,
        source_mandate_authorization_id=source_authorization.mandate_authorization_id,
        current_governing_version_id=current_governing_version.mandate_version_id,
        current_governing_strategy_identity=current_governing_identity,
        source_version_number=source_version.version_number,
        source_allowed_strategy_versions=tuple(source_version.allowed_strategy_versions),
        source_policy_hash=source_policy_hash,
        proposed_replacement_strategy_versions=tuple(request.replacement_allowed_strategy_versions),
        proposed_policy_hash=proposed_policy_hash,
        replacement_required=replacement_required,
        stop_reason=stop_reason,
        versions_in_order=versions_in_order,
        exact_version_authorizations=exact_version_authorizations,
    )


async def replace_governing_mandate_version(
    *,
    db: AsyncSession,
    request: MandateVersionReplacementRequest,
) -> ReplacementExecutionReport:
    dry_run = await dry_run_governing_version_replacement(db=db, request=request)
    if dry_run.stop_reason not in {None, "already_replaced"}:
        raise InvalidRequestError(message="Replacement workflow blocked", details={"reason": dry_run.stop_reason})

    if dry_run.stop_reason == "already_replaced":
        current_version = await db.get(AutonomousCapitalMandateVersion, dry_run.current_governing_version_id)
        if current_version is None:
            raise InvalidRequestError(message="Current governing version not found", details={})
        current_authorization = next(
            (
                item
                for item in await list_mandate_authorizations(db=db, mandate_id=request.mandate_id)
                if item.mandate_version_id == current_version.mandate_version_id
                and item.authorization_state == "AUTHORIZED"
                and item.revoked_at is None
            ),
            None,
        )
        if current_authorization is None:
            raise InvalidRequestError(message="Current governing authorization not found", details={})

        return ReplacementExecutionReport(
            dry_run=dry_run,
            result=MandateVersionReplacementResult(
                mandate_id=request.mandate_id,
                source_mandate_version_id=request.source_mandate_version_id,
                replacement_mandate_version_id=current_version.mandate_version_id,
                authorization_id=current_authorization.mandate_authorization_id,
                mandate_status=(await get_mandate(db=db, mandate_id=request.mandate_id)).status,
                selected_mandate_version_id=current_version.mandate_version_id,
                selected_strategy_identity=_current_identity(current_version) or request.replacement_allowed_strategy_versions[0],
                created_replacement=False,
            ),
        )

    source_version = await db.get(AutonomousCapitalMandateVersion, request.source_mandate_version_id)
    if source_version is None:
        raise InvalidRequestError(
            message="Source mandate version not found",
            details={"source_mandate_version_id": str(request.source_mandate_version_id)},
        )

    source_authorization = await db.get(AutonomousCapitalMandateAuthorization, request.source_mandate_authorization_id)
    if source_authorization is None:
        raise InvalidRequestError(
            message="Source mandate authorization not found",
            details={"source_mandate_authorization_id": str(request.source_mandate_authorization_id)},
        )

    canonical_replacement_identity = request.replacement_allowed_strategy_versions[0]
    if len(request.replacement_allowed_strategy_versions) != 1 or not canonical_replacement_identity:
        raise InvalidRequestError(message="Replacement strategy identity must be singular and canonical", details={})

    if request.owner_acknowledgements is None or not request.owner_acknowledgements:
        raise InvalidRequestError(message="Owner acknowledgements are required", details={})
    if request.authorization_evidence is None or not request.authorization_evidence:
        raise InvalidRequestError(message="Authorization evidence is required", details={})
    if request.deterministic_explanation is None or not request.deterministic_explanation:
        raise InvalidRequestError(message="Deterministic explanation is required", details={})

    replacement_request = MandateVersionCreateRequest(
        mandate_id=request.mandate_id,
        actor=request.actor,
        base_currency=source_version.base_currency,
        authorized_capital_usd=Decimal(source_version.authorized_capital_usd),
        max_order_notional_usd=Decimal(source_version.max_order_notional_usd),
        max_open_exposure_usd=Decimal(source_version.max_open_exposure_usd),
        max_daily_deployed_usd=Decimal(source_version.max_daily_deployed_usd),
        max_daily_realized_loss_usd=Decimal(source_version.max_daily_realized_loss_usd),
        max_campaign_drawdown_usd=Decimal(source_version.max_campaign_drawdown_usd),
        max_consecutive_losses=source_version.max_consecutive_losses,
        position_limit=source_version.position_limit,
        price_evidence_max_age_seconds=source_version.price_evidence_max_age_seconds,
        max_slippage_bps=Decimal(source_version.max_slippage_bps),
        max_fee_bps=Decimal(source_version.max_fee_bps),
        allowed_products=tuple(source_version.allowed_products),
        allowed_order_sides=tuple(source_version.allowed_order_sides),
        allowed_strategy_versions=request.replacement_allowed_strategy_versions,
        entry_policy=dict(source_version.entry_policy),
        exit_policy=dict(source_version.exit_policy),
        cooldown_policy=dict(source_version.cooldown_policy),
        operating_schedule=dict(source_version.operating_schedule),
        approval_policy=source_version.approval_policy,
        reconciliation_policy=dict(source_version.reconciliation_policy),
        kill_switch_policy=dict(source_version.kill_switch_policy),
        owner_acknowledgements=request.owner_acknowledgements,
        authorization_evidence_summary={
            **request.authorization_evidence,
            "owner_actor": request.actor,
            "source_mandate_version_id": str(request.source_mandate_version_id),
            "source_mandate_authorization_id": str(request.source_mandate_authorization_id),
            "canonical_strategy_identity": canonical_replacement_identity,
            "replacement_reason": request.authorization_evidence.get("replacement_reason") or request.deterministic_explanation.get("reason"),
            "limits_unchanged": True,
            "preview_only_scope": True,
            "no_live_submission": True,
            "deployed_git_sha": request.deployed_git_sha,
            "audit_correlation_id": str(request.audit_correlation_id),
        },
        idempotency_key=request.idempotency_key,
        audit_correlation_id=request.audit_correlation_id,
    )
    try:
        replacement_version = await create_mandate_version(db=db, request=replacement_request, commit=False)

        authorization_timestamp = datetime.now(timezone.utc)
        enriched_authorization_evidence = {
            **request.authorization_evidence,
            "owner_actor": request.actor,
            "old_version_id": str(request.source_mandate_version_id),
            "new_version_id": str(replacement_version.mandate_version_id),
            "source_authorization_id": str(request.source_mandate_authorization_id),
            "canonical_strategy_identity": canonical_replacement_identity,
            "replacement_reason": request.authorization_evidence.get("replacement_reason") or request.deterministic_explanation.get("reason"),
            "limits_unchanged": True,
            "preview_only_scope": True,
            "no_live_submission": True,
            "deployed_git_sha": request.deployed_git_sha,
            "authorization_timestamp": authorization_timestamp.isoformat(),
            "audit_correlation_id": str(request.audit_correlation_id),
        }

        authorization_request = MandateAuthorizationRequest(
            mandate_id=request.mandate_id,
            mandate_version_id=replacement_version.mandate_version_id,
            actor=request.actor,
            authorization_method=request.authorization_method,
            owner_acknowledgements=request.owner_acknowledgements,
            authorization_evidence=enriched_authorization_evidence,
            deterministic_explanation=request.deterministic_explanation,
            expires_at=request.expires_at or source_authorization.expires_at,
            idempotency_key=request.idempotency_key,
            audit_correlation_id=request.audit_correlation_id,
        )
        authorization = await authorize_mandate_version(db=db, request=authorization_request, commit=False)

        await db.commit()
    except Exception:
        await db.rollback()
        raise

    result = MandateVersionReplacementResult(
        mandate_id=request.mandate_id,
        source_mandate_version_id=request.source_mandate_version_id,
        replacement_mandate_version_id=replacement_version.mandate_version_id,
        authorization_id=authorization.mandate_authorization_id,
        mandate_status=(await get_mandate(db=db, mandate_id=request.mandate_id)).status,
        selected_mandate_version_id=(await list_mandate_versions(db=db, mandate_id=request.mandate_id))[0].mandate_version_id,
        selected_strategy_identity=canonical_replacement_identity,
        created_replacement=True,
    )
    return ReplacementExecutionReport(dry_run=dry_run, result=result)


def _validate_dry_run(
    *,
    source_version: AutonomousCapitalMandateVersion,
    source_authorization: object,
    current_governing_version: AutonomousCapitalMandateVersion,
    replacement_allowed_strategy_versions: tuple[str, ...],
    replacement_identity: str | None,
) -> str | None:
    if len(source_version.allowed_strategy_versions) != 1:
        return "source_allowlist_must_be_singular"
    if tuple(source_version.allowed_strategy_versions) != _LEGACY_REPLACEMENT_ALLOWLIST:
        return "source_allowlist_must_equal_[\"1.0.0\"]"
    if replacement_allowed_strategy_versions != (build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),):
        return "replacement_identity_must_equal_ma_crossover@1.0.0"
    if source_authorization.authorization_state != "AUTHORIZED":
        return "source_authorization_not_authorized"
    if source_authorization.revoked_at is not None:
        return "source_authorization_revoked"
    if source_authorization.expires_at is not None and source_authorization.expires_at <= datetime.now(timezone.utc):
        return "source_authorization_expired"
    if current_governing_version.mandate_version_id != source_version.mandate_version_id:
        current_summary = getattr(current_governing_version, "authorization_evidence_summary", {}) or {}
        if (
            tuple(current_governing_version.allowed_strategy_versions) == (build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),)
            and current_summary.get("source_mandate_version_id") == str(source_version.mandate_version_id)
            and current_summary.get("source_mandate_authorization_id") == str(source_authorization.mandate_authorization_id)
        ):
            return "already_replaced"
        return "unexpected_later_version"
    if replacement_identity != build_strategy_identity(slug="ma_crossover", module_version="1.0.0"):
        return "replacement_identity_must_equal_ma_crossover@1.0.0"
    return None


def _policy_hash(version: AutonomousCapitalMandateVersion, *, allowed_strategy_versions: tuple[str, ...] | None = None) -> str:
    payload = _policy_payload(version, allowed_strategy_versions=allowed_strategy_versions)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _policy_payload(
    version: AutonomousCapitalMandateVersion,
    *,
    allowed_strategy_versions: tuple[str, ...] | None = None,
) -> dict[str, object]:
    return {
        "base_currency": version.base_currency,
        "authorized_capital_usd": str(version.authorized_capital_usd),
        "max_order_notional_usd": str(version.max_order_notional_usd),
        "max_open_exposure_usd": str(version.max_open_exposure_usd),
        "max_daily_deployed_usd": str(version.max_daily_deployed_usd),
        "max_daily_realized_loss_usd": str(version.max_daily_realized_loss_usd),
        "max_campaign_drawdown_usd": str(version.max_campaign_drawdown_usd),
        "max_consecutive_losses": version.max_consecutive_losses,
        "position_limit": version.position_limit,
        "price_evidence_max_age_seconds": version.price_evidence_max_age_seconds,
        "max_slippage_bps": str(version.max_slippage_bps),
        "max_fee_bps": str(version.max_fee_bps),
        "allowed_products": list(version.allowed_products),
        "allowed_order_sides": list(version.allowed_order_sides),
        "allowed_strategy_versions": list(allowed_strategy_versions if allowed_strategy_versions is not None else version.allowed_strategy_versions),
        "entry_policy": version.entry_policy,
        "exit_policy": version.exit_policy,
        "cooldown_policy": version.cooldown_policy,
        "operating_schedule": version.operating_schedule,
        "approval_policy": version.approval_policy,
        "reconciliation_policy": version.reconciliation_policy,
        "kill_switch_policy": version.kill_switch_policy,
        "owner_acknowledgements": version.owner_acknowledgements,
        "authorization_evidence_summary": version.authorization_evidence_summary,
    }


def _summarize_version(version: AutonomousCapitalMandateVersion) -> ReplacementVersionSummary:
    return ReplacementVersionSummary(
        mandate_version_id=version.mandate_version_id,
        version_number=version.version_number,
        allowed_strategy_versions=tuple(version.allowed_strategy_versions),
        is_authorized=bool(version.is_authorized),
        is_active=bool(version.is_active),
        policy_hash=_policy_hash(version),
    )


def _summarize_authorization(authorization: AutonomousCapitalMandateAuthorization) -> ReplacementAuthorizationSummary:
    return ReplacementAuthorizationSummary(
        mandate_authorization_id=authorization.mandate_authorization_id,
        mandate_version_id=authorization.mandate_version_id,
        mandate_version_number=_mandate_version_number(authorization),
        authorization_state=authorization.authorization_state,
        approval_result=authorization.approval_result,
        recorded_at=authorization.recorded_at,
        expires_at=authorization.expires_at,
        revoked_at=authorization.revoked_at,
    )


def _mandate_version_number(authorization: AutonomousCapitalMandateAuthorization) -> int | None:
    value = getattr(authorization, "mandate_version_number", None)
    return value if isinstance(value, int) else None


def _current_identity(version: AutonomousCapitalMandateVersion) -> str | None:
    if not version.allowed_strategy_versions:
        return None
    return version.allowed_strategy_versions[0]