from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageActivationRequest,
    CanonicalPreviewPackageDryRunRequest,
    CanonicalPreviewPackageMandateAuthorizeRequest,
    _validate_canonical_package_authority,
    activate_canonical_proving_campaign,
    authorize_canonical_preview_package_under_mandate,
    run_dry_run_for_canonical_preview_package,
)

logger = logging.getLogger(__name__)

_PROGRESSABLE_STATES = {"READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"}


@dataclass(frozen=True, slots=True)
class AutomaticPackageExecutionRequest:
    campaign_id: uuid.UUID
    campaign_version: int
    decision_record_id: uuid.UUID
    package_id: uuid.UUID | None = None
    software_build_version: str | None = None


@dataclass(frozen=True, slots=True)
class AutomaticPackageExecutionOutcome:
    package_id: uuid.UUID | None
    campaign_id: uuid.UUID
    campaign_version: int
    decision_record_id: uuid.UUID
    mandate_id: uuid.UUID | None
    authorization_state: str
    dry_run_state: str
    activation_state: str
    authority_source: str | None
    replayed: bool
    final_reason_code: str
    failed_closed: bool
    starting_state: str


def _phase_key(*, package_id: uuid.UUID, phase: str) -> str:
    return f"automatic-mandate-package:{package_id}:{phase}"


def _outcome(
    *,
    request: AutomaticPackageExecutionRequest,
    package: CanonicalPreviewPackage | None,
    reason: str,
    replayed: bool = False,
    failed_closed: bool = False,
    starting_state: str = "UNKNOWN",
) -> AutomaticPackageExecutionOutcome:
    state = "MISSING" if package is None else package.package_state
    return AutomaticPackageExecutionOutcome(
        package_id=None if package is None else package.package_id,
        campaign_id=request.campaign_id,
        campaign_version=request.campaign_version,
        decision_record_id=request.decision_record_id,
        mandate_id=None if package is None else package.mandate_id,
        authorization_state="AUTHORIZED" if state in {"AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED"} else state,
        dry_run_state="DRY_RUN_PASSED" if state in {"DRY_RUN_PASSED", "ACTIVATED"} else "NOT_RUN",
        activation_state="ACTIVATED" if state == "ACTIVATED" else "NOT_ACTIVATED",
        authority_source=None if package is None else package.authorization_source,
        replayed=replayed,
        final_reason_code=reason,
        failed_closed=failed_closed,
        starting_state=starting_state,
    )


async def execute_automatic_ready_package_through_activation(
    *,
    db: AsyncSession,
    request: AutomaticPackageExecutionRequest,
) -> AutomaticPackageExecutionOutcome:
    settings = get_settings()
    if not settings.automatic_mandate_package_activation_enabled:
        logger.info(
            "automatic_package_progression_skipped campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s reason=feature_disabled failed_closed=False",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id,
        )
        return _outcome(request=request, package=None, reason="automatic_mandate_package_activation_disabled")

    scope_values = {
        "campaign_id": getattr(settings, "automatic_mandate_package_activation_campaign_id", None),
        "campaign_version": getattr(settings, "automatic_mandate_package_activation_campaign_version", None),
        "mandate_id": getattr(settings, "automatic_mandate_package_activation_mandate_id", None),
        "mandate_version_id": getattr(settings, "automatic_mandate_package_activation_mandate_version_id", None),
    }
    configured_scope = [value is not None for value in scope_values.values()]
    if any(configured_scope) and not all(configured_scope):
        return _outcome(request=request, package=None, reason="automatic_activation_scope_incomplete", failed_closed=True)
    if all(configured_scope) and (
        request.campaign_id != scope_values["campaign_id"]
        or request.campaign_version != scope_values["campaign_version"]
    ):
        return _outcome(request=request, package=None, reason="automatic_activation_campaign_scope_mismatch", failed_closed=True)

    pinned_package_id = getattr(settings, "automatic_mandate_package_activation_package_id", None)
    if pinned_package_id is not None and request.package_id not in {None, pinned_package_id}:
        logger.warning(
            "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s pinned_package_id=%s reason=proof_package_pin_mismatch failed_closed=True",
            request.campaign_id, request.campaign_version, request.decision_record_id,
            request.package_id, pinned_package_id,
        )
        return _outcome(
            request=request,
            package=None,
            reason="proof_package_pin_mismatch",
            failed_closed=True,
        )

    statement = select(CanonicalPreviewPackage).where(
        CanonicalPreviewPackage.campaign_id == request.campaign_id,
        CanonicalPreviewPackage.campaign_version == request.campaign_version,
        CanonicalPreviewPackage.decision_record_id == request.decision_record_id,
        CanonicalPreviewPackage.package_state.in_(_PROGRESSABLE_STATES),
    )
    resolved_package_id = request.package_id or pinned_package_id
    if resolved_package_id is not None:
        statement = statement.where(CanonicalPreviewPackage.package_id == resolved_package_id)
    rows = list((await db.execute(statement.order_by(CanonicalPreviewPackage.generated_at.desc()).limit(2).with_for_update())).scalars().all())
    if len(rows) != 1:
        reason = "eligible_package_missing" if not rows else "ambiguous_eligible_packages"
        logger.warning(
            "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s reason=%s package_count=%s failed_closed=True",
            request.campaign_id, request.campaign_version, request.decision_record_id, request.package_id, reason, len(rows),
        )
        return _outcome(request=request, package=None, reason=reason, failed_closed=True)
    package = rows[0]
    starting_state = package.package_state

    if all(configured_scope) and (
        package.mandate_id != scope_values["mandate_id"]
        or package.mandate_version_id != scope_values["mandate_version_id"]
    ):
        return _outcome(
            request=request, package=package, reason="automatic_activation_mandate_scope_mismatch",
            failed_closed=True, starting_state=starting_state,
        )

    try:
        if (
            package.campaign_id != request.campaign_id
            or package.campaign_version != request.campaign_version
            or package.decision_record_id != request.decision_record_id
            or (resolved_package_id is not None and package.package_id != resolved_package_id)
        ):
            raise PermissionError("resolved package identity mismatch")
        if package.package_state == "ACTIVATED":
            if package.authorization_source != "MANDATE":
                raise PermissionError("activated package has conflicting authority source")
            await _validate_canonical_package_authority(db=db, package=package, requested_approval_event_id=None)
            logger.info(
                "automatic_package_activated campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s replayed=True",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, package.mandate_id,
            )
            return _outcome(request=request, package=package, reason="already_activated", replayed=True, starting_state=starting_state)

        if package.package_state == "READY":
            logger.info(
                "automatic_package_authorization_started campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id,
            )
            await authorize_canonical_preview_package_under_mandate(
                db=db,
                request=CanonicalPreviewPackageMandateAuthorizeRequest(
                    package_id=package.package_id,
                    idempotency_key=_phase_key(package_id=package.package_id, phase="authorize"),
                    software_build_version=request.software_build_version,
                ),
            )
            logger.info(
                "automatic_package_authorized_under_mandate campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, package.mandate_id,
            )

        if package.package_state == "AUTHORIZED":
            if package.authorization_source != "MANDATE":
                raise PermissionError("authorized package has conflicting authority source")
            await run_dry_run_for_canonical_preview_package(
                db=db,
                request=CanonicalPreviewPackageDryRunRequest(
                    package_id=package.package_id,
                    approval_event_id=None,
                    operator_identity=None,
                    idempotency_token=_phase_key(package_id=package.package_id, phase="dry-run"),
                ),
            )
            logger.info(
                "automatic_package_dry_run_passed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, package.mandate_id,
            )

        if package.package_state == "DRY_RUN_PASSED":
            if package.authorization_source != "MANDATE" or package.dry_run_live_crypto_order_id is None:
                raise PermissionError("dry-run package authority evidence is incomplete")
            await activate_canonical_proving_campaign(
                db=db,
                request=CanonicalPreviewPackageActivationRequest(
                    package_id=package.package_id,
                    approval_event_id=None,
                    dry_run_live_crypto_order_id=package.dry_run_live_crypto_order_id,
                    actor=None,
                    expires_at=None,
                    idempotency_key=_phase_key(package_id=package.package_id, phase="activate"),
                ),
            )
            logger.info(
                "automatic_package_activated campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s replayed=False",
                request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id, package.mandate_id,
            )

        if package.package_state != "ACTIVATED":
            raise PermissionError(f"automatic package progression stopped in unexpected state: {package.package_state}")
        return _outcome(request=request, package=package, reason="activated_under_mandate", starting_state=starting_state)
    except (LookupError, PermissionError, ValueError) as exc:
        logger.warning(
            "automatic_package_progression_failed_closed campaign_id=%s campaign_version=%s decision_record_id=%s package_id=%s mandate_id=%s state=%s reason=%s failed_closed=True",
            request.campaign_id, request.campaign_version, request.decision_record_id, package.package_id,
            package.mandate_id, package.package_state, str(exc),
        )
        return _outcome(request=request, package=package, reason=str(exc), failed_closed=True, starting_state=starting_state)
