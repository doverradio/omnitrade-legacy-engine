from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.models.crypto_order_preview import CryptoOrderPreview
from app.schemas.capital_campaign_domain import CommissionedEntryExecutionRequest, CommissionedEntryExecutionResponse
from app.services.capital_campaign_domain.commissioned_entry_execution import execute_commissioned_entry


def _fail(blocker: str, **details: object) -> None:
    raise InvalidRequestError(message="Activated commissioned entry failed closed", details={"blocker": blocker, **details})


async def execute_activated_commissioned_entry(
    *,
    db: AsyncSession,
    package_id: UUID,
    request: CommissionedEntryExecutionRequest,
    now: datetime | None = None,
) -> CommissionedEntryExecutionResponse:
    """Consume only the caller-selected activated canonical package.

    Session ownership and transaction boundaries remain with the caller; this
    function neither discovers an alternative package nor creates authority.
    """
    observed_at = now or datetime.now(timezone.utc)
    package = await db.scalar(
        select(CanonicalPreviewPackage)
        .where(CanonicalPreviewPackage.package_id == package_id)
        .with_for_update()
        .limit(1)
    )
    if package is None:
        raise NotFoundError(message="Canonical preview package not found", details={"package_id": str(package_id)})
    if package.package_state != "ACTIVATED":
        _fail("package_not_activated", package_state=package.package_state)

    activation = await db.scalar(
        select(CanonicalProvingActivation)
        .where(CanonicalProvingActivation.package_id == package_id)
        .with_for_update()
        .limit(1)
    )
    if activation is None:
        _fail("activation_missing")
    if activation.package_id != package.package_id:
        _fail("activation_package_mismatch")
    if activation.activation_state != "ACTIVE" or activation.activated_at > observed_at or activation.expires_at <= observed_at:
        _fail("activation_not_effective", activation_state=activation.activation_state)

    readiness = request.readiness_request
    if package.campaign_id != request.campaign_id or activation.campaign_id != request.campaign_id:
        _fail("campaign_identity_mismatch")
    if package.campaign_version != request.version or activation.campaign_version != request.version:
        _fail("campaign_version_mismatch")
    if readiness.campaign_id != request.campaign_id or readiness.version != request.version:
        _fail("readiness_campaign_mismatch")
    if package.paper_account_id != request.paper_account_id or readiness.account_id != request.paper_account_id:
        _fail("account_identity_mismatch")
    if package.live_trading_profile_id != readiness.live_trading_profile_id or activation.live_trading_profile_id != package.live_trading_profile_id:
        _fail("live_profile_identity_mismatch")
    if (package.provider, package.environment, package.product) != (readiness.provider, readiness.environment, readiness.instrument):
        _fail("execution_scope_mismatch")
    if (activation.provider, activation.environment, activation.product) != (package.provider, package.environment, package.product):
        _fail("activation_scope_mismatch")
    preview = await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.crypto_order_preview_id == package.crypto_order_preview_id)
        .limit(1)
    )
    if preview is None or preview.crypto_order_preview_id != request.risk_signal_id:
        _fail("preview_identity_mismatch")
    if (preview.provider, preview.environment, preview.product_id) != (package.provider, package.environment, package.product):
        _fail("preview_scope_mismatch")

    if package.authorization_source == "MANDATE":
        if package.mandate_id != readiness.mandate_id:
            _fail("mandate_identity_mismatch")
        if package.mandate_version_id != readiness.mandate_version_id:
            _fail("mandate_version_mismatch")
        if package.mandate_evaluation_id is None or activation.mandate_evaluation_id != package.mandate_evaluation_id:
            _fail("mandate_evaluation_mismatch")
        evaluation = await db.scalar(
            select(AutonomousCapitalMandateEvaluation)
            .where(AutonomousCapitalMandateEvaluation.evaluation_id == package.mandate_evaluation_id)
            .limit(1)
        )
        if evaluation is None or evaluation.mandate_id != package.mandate_id or evaluation.mandate_version_id != package.mandate_version_id:
            _fail("mandate_evidence_inconsistent")
    elif package.authorization_source == "HUMAN":
        if activation.authority_source != "HUMAN" or activation.approval_event_id != package.approval_event_id:
            _fail("human_authority_evidence_inconsistent")
    else:
        _fail("authorization_source_invalid")

    return await execute_commissioned_entry(db=db, request=request)
