from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.decision_record import DecisionRecord
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_trading_profile import LiveTradingProfile
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageCreateRequest,
    _create_crypto_order_preview_for_package,
    create_canonical_preview_package,
)
from app.services.decisions.linkage_integrity import guard_preview_linkage_integrity
from app.services.decisions.ingestion import DECISION_ENGINE_VERSION
from app.services.live.position_quantity import compute_signed_owned_quantity
from app.services.orchestration.autonomous_position_exit_authority import (
    NONTERMINAL_CUSTODY_STATES,
    RESERVATION_TTL,
    _digest,
    _evaluation,
)

PackageBuilder = Callable[..., Awaitable[dict[str, Any]]]
PreviewBuilder = Callable[..., Awaitable[CryptoOrderPreview]]
LinkageGuard = Callable[..., Awaitable[list[Any]]]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExitPaperworkResult:
    authority_id: uuid.UUID
    decision_id: uuid.UUID
    package_id: uuid.UUID
    quantity: Decimal
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ExitConstructionPollOutcome:
    discovered: int
    constructed: int
    failed: int


def _decision_key(authority: AutonomousPositionExitAuthority) -> str:
    return f"autonomous-position-exit:{authority.authority_id}:v{authority.authority_version}"


def _exit_decision(
    *, custody: AutonomousPositionCustody, authority: AutonomousPositionExitAuthority,
    evaluation: dict[str, Any], preview: CryptoOrderPreview, quantity: Decimal,
    construction_net_result: Decimal, now: datetime, strategy_identity: str,
) -> DecisionRecord:
    evaluation_economics = {
        key: evaluation.get(key) for key in (
            "price", "price_observed_at", "estimated_current_proceeds", "estimated_exit_fee",
            "estimated_slippage", "cost_basis", "paid_costs", "estimated_net_exit_result",
            "profitable_exit", "mandatory_safety_exit", "stop_loss_triggered",
            "maximum_hold_exceeded", "dust",
        )
    }
    construction_economics = {
        "authority": "crypto_order_preview",
        "crypto_order_preview_id": str(preview.crypto_order_preview_id),
        "risk_event_id": str(preview.risk_event_id),
        "price_observed_at": None if preview.created_at is None else preview.created_at.isoformat(),
        "estimated_average_price": format(Decimal(str(preview.estimated_average_price)), "f"),
        "estimated_gross_quote_proceeds": format(Decimal(str(preview.estimated_quote_size)), "f"),
        "estimated_fee": format(Decimal(str(preview.estimated_fee or 0)), "f"),
        "estimated_slippage_rate": format(Decimal(str(preview.estimated_slippage or 0)), "f"),
        "estimated_net_exit_result": format(construction_net_result, "f"),
    }
    authority_binding = {
        "custody_id": str(custody.custody_id),
        "evaluation_integrity_hash": authority.evaluation_integrity_hash,
        "exit_authority_id": str(authority.authority_id),
        "exit_authority_version": authority.authority_version,
        "authority_classification": authority.classification,
        "proof_eligible": authority.proof_eligible,
        "originating_buy_claim_id": str(authority.originating_buy_claim_id),
        "originating_reconciliation_event_id": str(authority.originating_reconciliation_event_id),
        "live_trading_profile_id": str(authority.live_trading_profile_id),
        "paper_account_id": str(authority.paper_account_id),
        "exchange_connection_id": str(authority.exchange_connection_id),
        "provider": authority.provider, "environment": authority.environment, "product": authority.product,
        "side": "SELL", "exposure_effect": "REDUCE_ONLY",
        "proposed_quantity": format(quantity, "f"),
        "authoritative_remaining_quantity": format(quantity, "f"),
        "maximum_authorized_quantity": format(authority.maximum_sell_quantity, "f"),
        "policy_evidence": authority.policy_evidence, "risk_evidence": authority.risk_evidence,
        "campaign_id": str(custody.campaign_id), "campaign_version": custody.campaign_version,
        "runtime_campaign_id": str(custody.runtime_campaign_id),
        "entry_mandate_id": str(custody.mandate_id),
        "entry_mandate_version_id": str(custody.mandate_version_id),
        "entry_authority_lineage_status": {
            "campaign": evaluation.get("campaign_status"), "mandate": evaluation.get("mandate_status"),
        },
        "evaluation_time_economics": evaluation_economics,
        "construction_time_economics": construction_economics,
        "preview_id": str(preview.crypto_order_preview_id),
        "risk_event_id": str(preview.risk_event_id),
        "audit_correlation_id": str(preview.audit_correlation_id),
        "created_at": now.isoformat(),
        "provenance_classification": custody.provenance_classification,
        "automatic_activation": False, "provider_submission": False,
    }
    return DecisionRecord(
        idempotency_key=_decision_key(authority),
        source_lineage={
            "custodies": [str(custody.custody_id)], "exit_authorities": [str(authority.authority_id)],
            "autonomous_execution_claims": [str(authority.originating_buy_claim_id)],
            "live_reconciliation_events": [str(authority.originating_reconciliation_event_id)],
            "campaigns": [str(custody.campaign_id)], "risk_events": [str(preview.risk_event_id)],
            "crypto_order_previews": [str(preview.crypto_order_preview_id)], "signals": [],
            "model_outputs": [], "trades": [],
        },
        field_provenance={"execution_details": [{"entity_type": "autonomous_position_exit_authorities", "entity_id": str(authority.authority_id)}]},
        version=DECISION_ENGINE_VERSION, timestamp=now,
        asset={"product_id": authority.product, "provider": authority.provider}, timeframe="15m",
        market_regime={"state": "observed", "source": "autonomous_position_exit_evaluation"},
        indicators={"exit_evaluation_integrity_hash": authority.evaluation_integrity_hash},
        generated_signals=[{"strategy_identity": strategy_identity, "action": "SELL", "exposure_effect": "REDUCE_ONLY"}],
        signal_strength=None, confidence=None, supporting_strategies=[], opposing_strategies=[],
        risk_adjustments=[{"action_taken": "REDUCE_ONLY", "authority": "continuing_exit_authority"}],
        expected_risk={**authority.risk_evidence, "risk_event_id": str(preview.risk_event_id),
                       "risk_verdict": preview.risk_verdict},
        expected_reward=construction_economics,
        position_size=quantity, trade_accepted=True, trade_rejected_reason=None,
        execution_details=authority_binding, exit_details=authority_binding, pnl=construction_economics,
        duration=None, outcome="READY_PACKAGE_PENDING", post_trade_notes=None,
        lessons_learned=None, ai_reflection=None,
        future_tags=["autonomous_position_exit", authority.classification.lower()],
        confidence_calibration=None, review_status=None, human_notes=None,
    )


async def _construct_exit_paperwork_locked(
    *, db: AsyncSession, authority_id: uuid.UUID, now: datetime | None = None,
    package_builder: PackageBuilder = create_canonical_preview_package,
    preview_builder: PreviewBuilder = _create_crypto_order_preview_for_package,
    linkage_guard: LinkageGuard = guard_preview_linkage_integrity,
) -> ExitPaperworkResult:
    observed_at = now or datetime.now(timezone.utc)
    authority = await db.scalar(select(AutonomousPositionExitAuthority).where(
        AutonomousPositionExitAuthority.authority_id == authority_id,
    ).with_for_update().limit(1))
    if authority is None:
        raise InvalidRequestError(message="Continuing exit authority not found")
    if authority.authority_state == "RESERVED" and authority.reserved_decision_id and authority.reserved_package_id:
        return ExitPaperworkResult(authority.authority_id, authority.reserved_decision_id,
                                   authority.reserved_package_id, authority.maximum_sell_quantity, True)
    if authority.authority_state != "ARMED" or observed_at >= authority.expires_at:
        raise InvalidRequestError(message="Continuing exit authority is not fresh and ARMED")

    custody = await db.scalar(select(AutonomousPositionCustody).where(
        AutonomousPositionCustody.custody_id == authority.custody_id,
    ).with_for_update().limit(1))
    if custody is None or custody.custody_state not in NONTERMINAL_CUSTODY_STATES:
        raise InvalidRequestError(message="Custody is terminal or unavailable")
    if custody.active_sell_decision_id or custody.active_sell_package_id or custody.active_sell_claim_id or custody.active_sell_order_id:
        raise InvalidRequestError(message="Unresolved SELL lifecycle already exists")
    unresolved_package = await db.scalar(select(CanonicalPreviewPackage.package_id).where(
        CanonicalPreviewPackage.live_trading_profile_id == custody.live_trading_profile_id,
        CanonicalPreviewPackage.product == custody.product, CanonicalPreviewPackage.side == "SELL",
        CanonicalPreviewPackage.package_state.in_(("CREATED", "READY", "AUTHORIZED", "DRY_RUN_PASSED", "ACTIVATED")),
    ).limit(1))
    unresolved_claim = await db.scalar(select(AutonomousExecutionClaim.claim_id).where(
        AutonomousExecutionClaim.profile_id == custody.live_trading_profile_id,
        AutonomousExecutionClaim.product == custody.product, AutonomousExecutionClaim.side == "SELL",
        AutonomousExecutionClaim.claim_status.in_(("CLAIMED", "EXECUTION_STARTED", "SUBMISSION_PENDING",
                                                    "RECONCILIATION_REQUIRED", "RECOVERY_REQUIRED")),
    ).limit(1))
    unresolved_order = await db.scalar(select(LiveCryptoOrder.live_crypto_order_id).where(
        LiveCryptoOrder.exchange_connection_id == custody.exchange_connection_id,
        LiveCryptoOrder.product_id == custody.product, LiveCryptoOrder.side == "SELL",
        LiveCryptoOrder.status.in_(("SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED",
                                    "RECONCILIATION_REQUIRED")),
    ).limit(1))
    if unresolved_package or unresolved_claim or unresolved_order:
        raise InvalidRequestError(message="Unresolved SELL package, claim, order, submission, or reconciliation exists")
    evaluation = _evaluation(custody)
    if evaluation.get("disposition") != "EXIT_RECOMMENDED" or _digest(evaluation) != authority.evaluation_integrity_hash:
        raise InvalidRequestError(message="Exit evaluation is not current and EXIT_RECOMMENDED")
    evaluated_at = datetime.fromisoformat(str(evaluation.get("evaluated_at")))
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
    if evaluated_at > observed_at or observed_at >= authority.expires_at or evaluation.get("price_fresh") is not True:
        raise InvalidRequestError(message="Exit or market evidence is stale")
    if evaluation.get("policy_conflicts") and evaluation.get("mandatory_safety_exit") is not True:
        raise InvalidRequestError(message="Exit policy conflict is unresolved")
    quantity = await compute_signed_owned_quantity(
        db=db, live_trading_profile_id=custody.live_trading_profile_id, symbol=custody.product,
    )
    evaluated_quantity = Decimal(str(evaluation.get("authoritative_remaining_quantity")))
    if quantity <= 0 or quantity != evaluated_quantity or quantity > authority.maximum_sell_quantity:
        raise InvalidRequestError(message="Authoritative SELL quantity is unavailable, changed, or excessive")
    scope = (custody.custody_id, custody.live_trading_profile_id, custody.paper_account_id,
             custody.exchange_connection_id, custody.provider, custody.environment, custody.product,
             custody.buy_claim_id, custody.buy_reconciliation_event_id, custody.proof_eligible)
    authority_scope = (authority.custody_id, authority.live_trading_profile_id, authority.paper_account_id,
                       authority.exchange_connection_id, authority.provider, authority.environment, authority.product,
                       authority.originating_buy_claim_id, authority.originating_reconciliation_event_id,
                       authority.proof_eligible)
    if scope != authority_scope or authority.side != "SELL" or authority.exposure_effect != "REDUCE_ONLY":
        raise InvalidRequestError(message="Continuing exit authority scope or classification mismatch")

    original = await db.scalar(select(DecisionRecord).where(DecisionRecord.decision_id == custody.decision_record_id).limit(1))
    signals = original.generated_signals if original is not None and isinstance(original.generated_signals, list) else []
    strategy_identity = next((str(item.get("strategy_identity") or "") for item in signals if isinstance(item, dict) and item.get("strategy_identity")), "")
    if not strategy_identity:
        raise InvalidRequestError(message="Originating strategy identity is unavailable")

    package_request = CanonicalPreviewPackageCreateRequest(
        campaign_id=custody.campaign_id, campaign_version=custody.campaign_version,
        paper_account_id=custody.paper_account_id, live_trading_profile_id=custody.live_trading_profile_id,
        provider=custody.provider, environment=custody.environment, product=custody.product,
        max_proposed_order_amount=Decimal("5"), actor="system:autonomous_position_exit",
        idempotency_key=_decision_key(authority), commissioning_entry_mode="autonomous_position_exit",
        expected_decision_record_id=None, forced_action="CLOSE_POSITION_PROPOSED",
        autonomous_exit_custody_id=custody.custody_id,
        autonomous_exit_evaluation_hash=authority.evaluation_integrity_hash,
        autonomous_exit_authority_id=authority.authority_id,
        autonomous_exit_authority_version=authority.authority_version,
        autonomous_exit_classification=authority.classification,
        autonomous_exit_proof_eligible=authority.proof_eligible,
        autonomous_exit_maximum_quantity=authority.maximum_sell_quantity,
    )
    profile = await db.get(LiveTradingProfile, custody.live_trading_profile_id)
    if profile is None or profile.paper_account_id != custody.paper_account_id:
        raise InvalidRequestError(message="Live trading profile scope changed before construction")
    preview = await preview_builder(
        db=db, request=package_request, profile=profile,
        composition={"strategy_identity": strategy_identity},
        selected_decision={"strategy_identity": strategy_identity, "decision_kind": "CLOSE_POSITION_PROPOSED"},
    )
    if (preview.status != "PREVIEW_READY" or preview.side != "SELL" or preview.risk_verdict != "approved_for_preview"
            or preview.risk_event_id is None or preview.audit_correlation_id is None
            or Decimal(str(preview.base_size or 0)) != quantity
            or Decimal(str(preview.estimated_base_size or 0)) > quantity):
        raise InvalidRequestError(message="Fresh canonical SELL preview or risk evidence is invalid")
    gross_proceeds = Decimal(str(preview.estimated_quote_size or 0))
    if gross_proceeds <= 0:
        raise InvalidRequestError(message="Fresh SELL quote proceeds are invalid")
    cost_basis = Decimal(str(evaluation.get("cost_basis") or 0))
    paid_costs = Decimal(str(evaluation.get("paid_costs") or 0))
    construction_net_result = gross_proceeds - Decimal(str(preview.estimated_fee or 0)) - cost_basis - paid_costs
    minimum_profit = Decimal(str(authority.policy_evidence.get("minimum_net_profit_to_exit") or 0))
    if evaluation.get("mandatory_safety_exit") is not True and construction_net_result < minimum_profit:
        raise InvalidRequestError(message="Fresh construction economics no longer authorize profitable exit")

    decision = _exit_decision(custody=custody, authority=authority, evaluation=evaluation, preview=preview,
                              quantity=quantity, construction_net_result=construction_net_result,
                              now=observed_at, strategy_identity=strategy_identity)
    db.add(decision)
    await db.flush()
    preview.decision_record_id = decision.decision_id
    violations = await linkage_guard(
        db=db, actor="system:autonomous_position_exit", preview=preview, stage="autonomous_exit_bound",
    )
    if violations:
        raise InvalidRequestError(message="Canonical SELL preview linkage validation failed")
    package_request = replace(package_request, expected_decision_record_id=decision.decision_id)
    result = await package_builder(db=db, request=package_request)
    payload = result.get("package") if isinstance(result, dict) else None
    if not isinstance(payload, dict) or payload.get("package_state") != "READY" or payload.get("side") != "SELL":
        raise InvalidRequestError(message="Canonical SELL package construction did not produce READY paperwork")
    package_id = uuid.UUID(str(payload["package_id"]))
    package = await db.get(CanonicalPreviewPackage, package_id)
    if package is None or package.decision_record_id != decision.decision_id:
        raise InvalidRequestError(message="Canonical SELL package linkage is invalid")

    authority.authority_state = "RESERVED"; authority.reserved_at = observed_at
    authority.reservation_expires_at = observed_at + RESERVATION_TTL
    authority.reserved_decision_id = decision.decision_id; authority.reserved_package_id = package_id
    authority.updated_at = observed_at
    authority.last_construction_failure_at = None; authority.last_construction_failure_code = None
    authority.last_construction_exception_class = None; authority.last_construction_failure_retryable = None
    custody.continuing_exit_authority_state = "RESERVED"
    custody.active_sell_decision_id = decision.decision_id; custody.active_sell_package_id = package_id
    custody.custody_state = "EXIT_PENDING"; custody.updated_at = observed_at
    db.add(AuditLog(
        actor="system:autonomous_position_exit", action="autonomous_position_exit_package.constructed",
        entity_type="autonomous_position_exit_authority", entity_id=authority.authority_id,
        before_state={"authority_state": "ARMED"},
        after_state={"authority_state": "RESERVED", "decision_id": str(decision.decision_id),
                     "package_id": str(package_id), "side": "SELL", "exposure_effect": "REDUCE_ONLY",
                     "quantity": format(quantity, "f"), "maximum_quantity": format(authority.maximum_sell_quantity, "f"),
                     "proof_eligible": authority.proof_eligible, "classification": authority.classification,
                     "package_activation_connected": False, "automatic_sell_execution_connected": False,
                     "provider_submission_connected": False, "autonomous_proof_sell_ready": False},
    ))
    await db.flush()
    return ExitPaperworkResult(authority.authority_id, decision.decision_id, package_id, quantity, False)


async def construct_exit_paperwork(
    *, db: AsyncSession, authority_id: uuid.UUID, now: datetime | None = None,
    package_builder: PackageBuilder = create_canonical_preview_package,
    preview_builder: PreviewBuilder = _create_crypto_order_preview_for_package,
    linkage_guard: LinkageGuard = guard_preview_linkage_integrity,
) -> ExitPaperworkResult:
    """Build and reserve as one savepoint-contained unit.

    The caller still owns the outer commit. Any decision, preview, risk event,
    package, reservation, custody linkage, or audit failure rolls this complete
    unit back without disturbing other custody work in the scheduler cycle.
    """
    async with db.begin_nested():
        return await _construct_exit_paperwork_locked(
            db=db, authority_id=authority_id, now=now, package_builder=package_builder,
            preview_builder=preview_builder, linkage_guard=linkage_guard,
        )


def _safe_failure(exc: Exception) -> tuple[str, bool]:
    message = getattr(exc, "message", "")
    known = {
        "Continuing exit authority is not fresh and ARMED": ("authority_not_fresh_and_armed", False),
        "Custody is terminal or unavailable": ("custody_terminal_or_unavailable", False),
        "Unresolved SELL lifecycle already exists": ("unresolved_sell_lifecycle", True),
        "Unresolved SELL package, claim, order, submission, or reconciliation exists": ("unresolved_sell_lifecycle", True),
        "Exit evaluation is not current and EXIT_RECOMMENDED": ("evaluation_not_current", False),
        "Exit or market evidence is stale": ("evaluation_or_market_evidence_stale", True),
        "Exit policy conflict is unresolved": ("policy_conflict_unresolved", False),
        "Authoritative SELL quantity is unavailable, changed, or excessive": ("authoritative_quantity_invalid", True),
        "Fresh canonical SELL preview or risk evidence is invalid": ("canonical_preview_or_risk_invalid", True),
        "Fresh SELL quote proceeds are invalid": ("sell_quote_proceeds_invalid", True),
        "Fresh construction economics no longer authorize profitable exit": ("construction_economics_rejected", True),
        "Canonical SELL preview linkage validation failed": ("preview_linkage_invalid", True),
    }
    if message in known:
        return known[message]
    return ("construction_internal_error", True)


async def construct_due_exit_paperwork(
    *, db: AsyncSession, now: datetime | None = None, limit: int = 10,
    construct_one: Callable[..., Awaitable[ExitPaperworkResult]] = construct_exit_paperwork,
) -> ExitConstructionPollOutcome:
    observed_at = now or datetime.now(timezone.utc)
    ids = list((await db.scalars(select(AutonomousPositionExitAuthority.authority_id).where(
        AutonomousPositionExitAuthority.authority_state == "ARMED",
    ).order_by(AutonomousPositionExitAuthority.issued_at.asc()).limit(limit).with_for_update(skip_locked=True))).all())
    constructed = failed = 0
    for authority_id in ids:
        try:
            result = await construct_one(db=db, authority_id=authority_id, now=observed_at)
            constructed += int(not result.idempotent)
        except Exception as exc:
            failed += 1
            code, retryable = _safe_failure(exc)
            exception_class = f"{type(exc).__module__}.{type(exc).__qualname__}"
            try:
                authority = await db.get(AutonomousPositionExitAuthority, authority_id)
                custody_id = None if authority is None else authority.custody_id
                if authority is not None:
                    authority.last_construction_failure_at = observed_at
                    authority.last_construction_failure_code = code
                    authority.last_construction_exception_class = exception_class
                    authority.last_construction_failure_retryable = retryable
                db.add(AuditLog(
                    actor="system:autonomous_position_exit",
                    action="autonomous_position_exit_package.construction_failed",
                    entity_type="autonomous_position_exit_authority", entity_id=authority_id,
                    before_state=None,
                    after_state={"authority_id": str(authority_id),
                                 "custody_id": None if custody_id is None else str(custody_id),
                                 "exception_classification": exception_class,
                                 "reason_code": code, "failed_at": observed_at.isoformat(),
                                 "retryable": retryable},
                ))
                await db.flush()
            except Exception:
                logger.exception("autonomous exit construction failure evidence persistence failed authority_id=%s", authority_id)
            continue
    return ExitConstructionPollOutcome(len(ids), constructed, failed)
