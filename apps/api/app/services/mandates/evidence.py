from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_evaluation import AutonomousCapitalMandateEvaluation
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.services.mandates.contracts import (
    MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
    MandateDomainModel,
    MandateEligibilityInput,
    MandateEvaluationRecord,
    MandateVersionModel,
)
from app.services.mandates.eligibility import evaluate_mandate_eligibility
from app.services.mandates.lifecycle import get_mandate

_ALLOWED_RISK_VERDICTS = {"ACCEPTED", "REJECTED", "RESIZED", "NOT_EVALUATED"}


@dataclass(frozen=True)
class MandateEvaluationWriteRequest:
    mandate_id: uuid.UUID
    actor: str
    strategy_version: str
    product: str
    side: str
    proposed_notional_usd: Decimal
    current_open_exposure_usd: Decimal
    daily_deployed_usd: Decimal
    daily_realized_loss_usd: Decimal
    campaign_drawdown_usd: Decimal
    consecutive_losses: int
    current_position_count: int
    risk_verdict: str
    evidence_age_seconds: int
    kill_switch_engaged: bool
    observed_at: datetime
    decision_id: uuid.UUID | None
    request_context: dict[str, object]
    idempotency_key: str | None
    audit_correlation_id: uuid.UUID | None
    software_build_version: str | None


async def evaluate_and_record_mandate(
    *,
    db: AsyncSession,
    request: MandateEvaluationWriteRequest,
) -> MandateEvaluationRecord:
    if request.risk_verdict not in _ALLOWED_RISK_VERDICTS:
        raise InvalidRequestError(
            message="Unsupported risk verdict",
            details={"risk_verdict": request.risk_verdict, "allowed": sorted(_ALLOWED_RISK_VERDICTS)},
        )

    if request.idempotency_key:
        existing = await db.scalar(
            select(AutonomousCapitalMandateEvaluation)
            .where(AutonomousCapitalMandateEvaluation.idempotency_key == request.idempotency_key)
            .limit(1)
        )
        if existing is not None:
            return _to_record(existing)

    mandate = await get_mandate(db=db, mandate_id=request.mandate_id)
    version, is_authorized = await _resolve_version_for_evaluation(db=db, mandate_id=mandate.mandate_id)

    domain_mandate = _to_mandate_domain(mandate)
    domain_version = _to_version_domain(version=version, mandate=mandate, is_authorized=is_authorized)

    decision = evaluate_mandate_eligibility(
        mandate=domain_mandate,
        version=domain_version,
        request=MandateEligibilityInput(
            owner_actor_id=mandate.owner_actor_id,
            provider=mandate.provider,
            exchange_environment=mandate.exchange_environment,
            exchange_connection_id=mandate.exchange_connection_id,
            live_trading_profile_id=mandate.live_trading_profile_id,
            paper_account_id=mandate.paper_account_id,
            capital_campaign_id=mandate.capital_campaign_id,
            strategy_version=request.strategy_version,
            product=request.product,
            side=request.side,
            proposed_notional_usd=request.proposed_notional_usd,
            current_open_exposure_usd=request.current_open_exposure_usd,
            daily_deployed_usd=request.daily_deployed_usd,
            daily_realized_loss_usd=request.daily_realized_loss_usd,
            campaign_drawdown_usd=request.campaign_drawdown_usd,
            consecutive_losses=request.consecutive_losses,
            current_position_count=request.current_position_count,
            risk_verdict=request.risk_verdict,
            evidence_age_seconds=request.evidence_age_seconds,
            kill_switch_engaged=request.kill_switch_engaged,
            observed_at=request.observed_at,
        ),
    )

    evaluation = AutonomousCapitalMandateEvaluation(
        mandate_id=mandate.mandate_id,
        mandate_version_id=version.mandate_version_id,
        mandate_version_number=version.version_number,
        decision_id=request.decision_id,
        autonomy_level=mandate.autonomy_level,
        proposed_action=request.side,
        authorization_result=decision.result,
        approval_result=decision.approval_result,
        risk_verdict=request.risk_verdict,
        risk_evaluated=request.risk_verdict != "NOT_EVALUATED",
        checks_passed=list(decision.passed_checks),
        checks_failed=list(decision.failed_checks),
        deterministic_explanation=list(decision.deterministic_explanation),
        reason_code=decision.reason_code,
        human_approval_required=decision.approval_result == MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
        active_mandate_exemption_eligible=decision.approval_result != MANDATE_APPROVAL_RESULT_REQUIRED_HUMAN,
        request_context=request.request_context,
        actor=request.actor,
        audit_correlation_id=request.audit_correlation_id or uuid.uuid4(),
        software_build_version=request.software_build_version,
        idempotency_key=request.idempotency_key or f"mandate-evaluation-{uuid.uuid4()}",
    )
    db.add(evaluation)
    await db.flush()

    db.add(
        AuditLog(
            actor=request.actor,
            action="MANDATE_EVALUATION_RECORDED",
            entity_type="autonomous_capital_mandate",
            entity_id=mandate.mandate_id,
            before_state={"status": mandate.status},
            after_state={
                "status": mandate.status,
                "evaluation_id": str(evaluation.evaluation_id),
                "decision_id": str(request.decision_id) if request.decision_id else None,
                "approval_result": evaluation.approval_result,
                "authorization_result": evaluation.authorization_result,
                "risk_verdict": evaluation.risk_verdict,
                "idempotency_key": evaluation.idempotency_key,
                "audit_correlation_id": str(evaluation.audit_correlation_id),
            },
        )
    )

    await db.commit()
    await db.refresh(evaluation)
    return _to_record(evaluation)


async def list_mandate_evaluations(
    *,
    db: AsyncSession,
    mandate_id: uuid.UUID,
) -> list[MandateEvaluationRecord]:
    await get_mandate(db=db, mandate_id=mandate_id)
    rows = list(
        await db.scalars(
            select(AutonomousCapitalMandateEvaluation)
            .where(AutonomousCapitalMandateEvaluation.mandate_id == mandate_id)
            .order_by(AutonomousCapitalMandateEvaluation.created_at.desc())
        )
    )
    return [_to_record(item) for item in rows]


async def _resolve_version_for_evaluation(
    *,
    db: AsyncSession,
    mandate_id: uuid.UUID,
) -> tuple[AutonomousCapitalMandateVersion, bool]:
    auth = await db.scalar(
        select(AutonomousCapitalMandateAuthorization)
        .where(
            AutonomousCapitalMandateAuthorization.mandate_id == mandate_id,
            AutonomousCapitalMandateAuthorization.authorization_state == "AUTHORIZED",
            AutonomousCapitalMandateAuthorization.revoked_at.is_(None),
        )
        .order_by(AutonomousCapitalMandateAuthorization.recorded_at.desc())
        .limit(1)
    )
    if auth is not None:
        version = await db.get(AutonomousCapitalMandateVersion, auth.mandate_version_id)
        if version is not None:
            return version, True

    latest = await db.scalar(
        select(AutonomousCapitalMandateVersion)
        .where(AutonomousCapitalMandateVersion.mandate_id == mandate_id)
        .order_by(AutonomousCapitalMandateVersion.version_number.desc())
        .limit(1)
    )
    if latest is None:
        raise InvalidRequestError(
            message="Mandate has no policy version to evaluate",
            details={"mandate_id": str(mandate_id)},
        )

    return latest, False


def _to_mandate_domain(mandate: AutonomousCapitalMandate) -> MandateDomainModel:
    return MandateDomainModel(
        mandate_id=mandate.mandate_id,
        owner_actor_id=mandate.owner_actor_id,
        status=mandate.status,
        autonomy_level=mandate.autonomy_level,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
        exchange_connection_id=mandate.exchange_connection_id,
        live_trading_profile_id=mandate.live_trading_profile_id,
        paper_account_id=mandate.paper_account_id,
        capital_campaign_id=mandate.capital_campaign_id,
        expires_at=mandate.expires_at,
        revoked_at=mandate.revoked_at,
    )


def _to_version_domain(
    *,
    version: AutonomousCapitalMandateVersion,
    mandate: AutonomousCapitalMandate,
    is_authorized: bool,
) -> MandateVersionModel:
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
        is_authorized=is_authorized,
        is_active=mandate.status in {"ACTIVE", "PAUSED", "EXIT_ONLY"},
    )


def _to_record(evaluation: AutonomousCapitalMandateEvaluation) -> MandateEvaluationRecord:
    return MandateEvaluationRecord(
        evaluation_id=evaluation.evaluation_id,
        mandate_id=evaluation.mandate_id,
        mandate_version_id=evaluation.mandate_version_id,
        mandate_version_number=evaluation.mandate_version_number,
        autonomy_level=evaluation.autonomy_level,
        proposed_action=evaluation.proposed_action,
        authorization_result=evaluation.authorization_result,
        approval_result=evaluation.approval_result,
        risk_verdict=evaluation.risk_verdict,
        risk_evaluated=evaluation.risk_evaluated,
        checks_passed=tuple(evaluation.checks_passed),
        checks_failed=tuple(evaluation.checks_failed),
        deterministic_explanation=tuple(evaluation.deterministic_explanation),
        reason_code=evaluation.reason_code,
        human_approval_required=evaluation.human_approval_required,
        active_mandate_exemption_eligible=evaluation.active_mandate_exemption_eligible,
        decision_id=evaluation.decision_id,
        actor=evaluation.actor,
        audit_correlation_id=evaluation.audit_correlation_id,
        software_build_version=evaluation.software_build_version,
        idempotency_key=evaluation.idempotency_key,
        created_at=evaluation.created_at,
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
