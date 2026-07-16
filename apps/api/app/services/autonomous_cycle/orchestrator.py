from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_authorization import AutonomousCapitalMandateAuthorization
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.paper_account import PaperAccount
from app.models.parameter_set import ParameterSet
from app.models.signal import Signal
from app.models.strategy import Strategy
from app.services.crypto_order_previews.service import create_crypto_order_preview
from app.services.decisions.ingestion import DECISION_ENGINE_VERSION
from app.services.decisions.replay_context import ReplayIdentityProvenance, build_canonical_replay_context
from app.services.execution_price_evidence import load_current_execution_price_evidence
from app.services.exchange_connections.readiness import supports_autonomous_preview
from app.services.exchange_connections.providers.registry import get_exchange_provider
from app.services.exchange_connections.service import get_decrypted_credentials_for_connection
from app.services.mandates.contracts import (
    MANDATE_APPROVAL_RESULT_ACTIVE_MANDATE,
    MANDATE_AUTHORIZATION_ALLOWED,
    MANDATE_AUTHORIZATION_REJECTED,
    MandateEligibilityInput,
    MandateVersionModel,
)
from app.services.mandates.eligibility import evaluate_mandate_eligibility
from app.services.mandates.evidence import MandateEvaluationWriteRequest, evaluate_and_record_mandate
from app.services.mandates.lifecycle import get_mandate, list_mandate_versions
from app.services.mandates.validation import validate_mandate_version
from app.services.risk.risk_context import resolve_execution_risk_context
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationRequest, evaluate_signal_risk
from app.services.risk.risk_persistence import RiskDecisionPersistenceRequest, persist_risk_decision
from app.services.signals.execution_orchestrator import SignalExecutionRequest, orchestrate_paper_signal_execution
from app.services.strategies.identity import build_strategy_identity, parse_strategy_identity
from app.services.strategies import strategy_registry
from app.services.strategies.base import StrategyContext
from app.schemas.crypto_order_previews import CryptoOrderPreviewCreateRequest

from .contracts import (
    ACTIONS,
    CYCLE_STATES,
    AutonomousCycleRequest,
    AutonomousCycleResult,
    CycleDiagnostics,
    ReconciliationStatus,
    RiskEvaluationSummary,
    StrategyProposal,
)

_TERMINAL_CYCLE_STATES = {"HOLD", "PREVIEW_READY", "FAILED", "COMPLETE"}
_RESUMABLE_NON_TERMINAL_STATES = {"NOT_STARTED", "LOADING"}
_MAX_RESUME_AGE_SECONDS = 30 * 60
_RESOLVED_ORDER_STATUSES = {"filled", "cancelled", "failed", "rejected", "expired", "settled"}

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NOT_STARTED": {"LOADING", "FAILED"},
    "LOADING": {"READY", "HOLD", "FAILED"},
    "READY": {"EVALUATING", "HOLD", "FAILED"},
    "EVALUATING": {"HOLD", "PREVIEW_READY", "FAILED", "COMPLETE"},
    "HOLD": {"COMPLETE"},
    "PREVIEW_READY": {"COMPLETE"},
    "FAILED": set(),
    "COMPLETE": set(),
}


def build_cycle_idempotency_key(*, request: AutonomousCycleRequest) -> str:
    payload = {
        "mandate_id": str(request.mandate_id),
        "product_id": request.product_id.strip().upper(),
        "trigger": request.trigger,
        "seed": request.idempotency_seed,
        "software_build_version": request.software_build_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _serialize_strategy_signal(*, signal: Any) -> dict[str, Any]:
    def _to_json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.replace(tzinfo=timezone.utc).isoformat()
        if isinstance(value, (list, tuple)):
            return [_to_json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _to_json_safe(item) for key, item in value.items()}
        return None

    if signal is None:
        return {}

    model_dump = getattr(signal, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return _to_json_safe(dumped) if isinstance(dumped, dict) else {}

    payload: dict[str, Any] = {}
    for field in ("action", "strength", "reason", "indicators", "timestamp"):
        if hasattr(signal, field):
            payload[field] = _to_json_safe(getattr(signal, field))

    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str):
        payload["timestamp"] = timestamp

    indicators = payload.get("indicators")
    if indicators is not None and not isinstance(indicators, dict):
        payload["indicators"] = {}

    return payload


def _is_stale_non_terminal_cycle(cycle: AutonomousCycleRun) -> bool:
    reference = cycle.updated_at or cycle.started_at
    if reference is None:
        return True
    observed = reference if reference.tzinfo is not None else reference.replace(tzinfo=timezone.utc)
    age_seconds = int((datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds())
    return age_seconds > _MAX_RESUME_AGE_SECONDS


async def run_autonomous_preview_cycle(
    *,
    db: AsyncSession,
    request: AutonomousCycleRequest,
) -> AutonomousCycleResult:
    if request.forced_action is not None and request.forced_action not in ACTIONS:
        raise InvalidRequestError(
            message="forced_action must be BUY, SELL, or HOLD",
            details={"forced_action": request.forced_action},
        )

    started_monotonic = time.monotonic()
    idempotency_key = build_cycle_idempotency_key(request=request)

    existing = await db.scalar(
        select(AutonomousCycleRun)
        .where(AutonomousCycleRun.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None and existing.state in _TERMINAL_CYCLE_STATES | {"COMPLETE"}:
        return _to_cycle_result(existing, replayed=True)

    if existing is not None and existing.state not in _RESUMABLE_NON_TERMINAL_STATES:
        return await _finish_failed(
            db=db,
            cycle=existing,
            stage="resume_existing_cycle",
            reason="existing_non_terminal_cycle_not_resumable",
            explanation=(
                "CHECK_FAILED:existing_non_terminal_cycle_not_resumable",
                f"CHECK_INFO:existing_cycle_state={existing.state}",
            ),
            started_monotonic=started_monotonic,
        )

    if existing is not None and _is_stale_non_terminal_cycle(existing):
        return await _finish_failed(
            db=db,
            cycle=existing,
            stage="resume_existing_cycle",
            reason="stale_non_terminal_cycle",
            explanation=(
                "CHECK_FAILED:stale_non_terminal_cycle",
                f"CHECK_INFO:existing_cycle_state={existing.state}",
            ),
            started_monotonic=started_monotonic,
        )

    cycle = existing
    if cycle is None:
        cycle = AutonomousCycleRun(
            idempotency_key=idempotency_key,
            mandate_id=request.mandate_id,
            state="NOT_STARTED",
            evaluation_stage="not_started",
            cycle_context={
                "mandate_id": str(request.mandate_id),
                "product_id": request.product_id.strip().upper(),
                "trigger": request.trigger,
                "software_build_version": request.software_build_version,
            },
            diagnostics={},
            deterministic_explanation=[],
            audit_correlation_id=uuid.uuid4(),
            software_build_version=request.software_build_version,
            started_at=datetime.now(timezone.utc),
        )
        db.add(cycle)
        await db.flush()

    try:
        await _transition_cycle(db=db, cycle=cycle, to_state="LOADING", stage="load_mandate")

        mandate = await get_mandate(db=db, mandate_id=request.mandate_id)
        versions = await list_mandate_versions(db=db, mandate_id=mandate.mandate_id)
        if not versions:
            return await _finish_hold(
                db=db,
                cycle=cycle,
                stage="load_mandate_version",
                reason="mandate_version_missing",
                explanation=("CHECK_FAILED:mandate_version_missing",),
                started_monotonic=started_monotonic,
            )

        version = versions[0]
        cycle.mandate_version_id = version.mandate_version_id
        cycle.cycle_context = {
            **cycle.cycle_context,
            "autonomy_level": mandate.autonomy_level,
            "provider": mandate.provider,
            "exchange_environment": mandate.exchange_environment,
            "live_trading_profile_id": str(mandate.live_trading_profile_id),
            "paper_account_id": str(mandate.paper_account_id) if mandate.paper_account_id else None,
            "capital_campaign_id": mandate.capital_campaign_id,
            "mandate_status": mandate.status,
            "mandate_version_id": str(version.mandate_version_id),
            "mandate_version_number": version.version_number,
        }
        await db.flush()

        if mandate.status != "ACTIVE":
            return await _finish_hold(
                db=db,
                cycle=cycle,
                stage="validate_mandate",
                reason=f"mandate_status_{mandate.status.lower()}",
                explanation=(
                    "CHECK_FAILED:mandate_not_active",
                    f"CHECK_INFO:mandate_status={mandate.status}",
                ),
                started_monotonic=started_monotonic,
            )

        version_is_authorized = await _has_valid_exact_version_authorization(
            db=db,
            mandate_id=mandate.mandate_id,
            mandate_version_id=version.mandate_version_id,
            observed_at=datetime.now(timezone.utc),
        )
        version_validation = validate_mandate_version(
            _to_version_model(
                version=version,
                mandate=mandate,
                is_authorized=version_is_authorized,
            )
        )
        if not version_validation.valid:
            return await _finish_hold(
                db=db,
                cycle=cycle,
                stage="validate_mandate",
                reason=version_validation.reason or "invalid_mandate_version",
                explanation=("CHECK_FAILED:mandate_version_invalid",),
                started_monotonic=started_monotonic,
            )

        await _transition_cycle(db=db, cycle=cycle, to_state="READY", stage="provider_readiness")

        connection = await db.get(ExchangeConnection, mandate.exchange_connection_id)
        if connection is None:
            return await _finish_failed(
                db=db,
                cycle=cycle,
                stage="provider_readiness",
                reason="exchange_connection_not_found",
                explanation=("CHECK_FAILED:exchange_connection_not_found",),
                started_monotonic=started_monotonic,
            )

        if not supports_autonomous_preview(connection.last_readiness_verdict):
            return await _finish_hold(
                db=db,
                cycle=cycle,
                stage="provider_readiness",
                reason="provider_not_ready",
                explanation=(
                    "CHECK_FAILED:provider_not_ready",
                    f"CHECK_INFO:readiness_verdict={connection.last_readiness_verdict}",
                ),
                started_monotonic=started_monotonic,
            )

        provider = get_exchange_provider(connection.provider)
        credentials = get_decrypted_credentials_for_connection(connection)

        await _transition_cycle(db=db, cycle=cycle, to_state="EVALUATING", stage="reconciliation")

        reconciliation = await _reconcile_state(
            db=db,
            mandate=mandate,
            provider=provider,
            credentials=credentials,
            environment=connection.environment,
            product_id=request.product_id,
        )
        cycle.cycle_context = {
            **cycle.cycle_context,
            "reconciliation_status": {
                "provider_ready": reconciliation.provider_ready,
                "balances_loaded": reconciliation.balances_loaded,
                "unresolved_order_count": reconciliation.unresolved_order_count,
                "open_position_count": reconciliation.open_position_count,
                "stale_evidence": reconciliation.stale_evidence,
                "explanation": list(reconciliation.explanation),
            },
        }
        await db.flush()

        if (
            not reconciliation.provider_ready
            or not reconciliation.balances_loaded
            or reconciliation.unresolved_order_count > 0
            or reconciliation.stale_evidence
        ):
            return await _finish_hold(
                db=db,
                cycle=cycle,
                stage="reconciliation",
                reason="reconciliation_not_ready",
                explanation=reconciliation.explanation or ("CHECK_FAILED:reconciliation_not_ready",),
                started_monotonic=started_monotonic,
            )

        evidence, reference_price, evidence_age_minutes = await load_current_execution_price_evidence(
            provider_client=provider,
            credentials=credentials,
            environment=connection.environment,
            expected_provider=connection.provider,
            product_id=request.product_id,
            max_age_minutes=max(1, math.ceil(version.price_evidence_max_age_seconds / 60)),
        )
        cycle.cycle_context = {
            **cycle.cycle_context,
            "market_evidence": {
                "evidence_id": str(evidence.evidence_id),
                "provider": evidence.provider,
                "product_id": evidence.product_id,
                "reference_price": format(reference_price, "f"),
                "bid": format(evidence.bid, "f") if evidence.bid is not None else None,
                "ask": format(evidence.ask, "f") if evidence.ask is not None else None,
                "observed_at": evidence.observed_at.isoformat() if evidence.observed_at else None,
                "age_minutes": evidence_age_minutes,
            },
        }
        await db.flush()

        proposal = await _run_approved_strategy(
            db=db,
            mandate=mandate,
            version=version,
            request=request,
        )
        runtime_strategy_identity = _resolve_runtime_strategy_identity(proposal=proposal, version=version)
        cycle.proposed_action = proposal.action
        cycle.cycle_context = {
            **cycle.cycle_context,
            "strategy": {
                "name": proposal.strategy_name,
                "version": runtime_strategy_identity,
                "deterministic_explanation": list(proposal.deterministic_explanation),
                "signal_payload": proposal.signal_payload,
            },
            "proposed_action": proposal.action,
        }
        await db.flush()

        mandate_verdict, mandate_reason = _evaluate_mandate_scope(
            version=version,
            product_id=request.product_id,
            action=proposal.action,
            strategy_version=runtime_strategy_identity,
        )
        cycle.mandate_verdict = mandate_verdict
        if mandate_verdict == MANDATE_AUTHORIZATION_REJECTED:
            return await _finish_hold(
                db=db,
                cycle=cycle,
                stage="mandate_evaluation",
                reason=mandate_reason,
                explanation=(f"CHECK_FAILED:{mandate_reason}",),
                started_monotonic=started_monotonic,
            )

        risk_summary = await _evaluate_risk(
            db=db,
            mandate=mandate,
            version=version,
            product_id=request.product_id,
            action=proposal.action,
            reference_price=reference_price,
            actor=request.actor,
        )
        cycle.risk_verdict = risk_summary.risk_verdict
        cycle.risk_event_id = risk_summary.risk_event_id
        await db.flush()

        canonical_signal = await _create_canonical_signal_for_cycle(
            db=db,
            cycle=cycle,
            mandate=mandate,
            version=version,
            proposal=proposal,
            strategy_interval=request.strategy_interval,
            product_id=request.product_id,
        )
        signal_id = canonical_signal.id if canonical_signal is not None else None

        decision_record_id = await _persist_decision_intelligence(
            db=db,
            cycle=cycle,
            mandate=mandate,
            version=version,
            proposal=proposal,
            risk_summary=risk_summary,
            product_id=request.product_id,
            reference_price=reference_price,
            evidence_age_minutes=evidence_age_minutes,
            strategy_interval=request.strategy_interval,
            canonical_signal_id=signal_id,
            candle_id=request.candle_id,
            candle_close_time=request.candle_close_time,
            canonical_identity=request.canonical_identity,
        )
        cycle.decision_record_id = decision_record_id
        await db.flush()

        mandate_eval = await evaluate_and_record_mandate(
            db=db,
            request=MandateEvaluationWriteRequest(
                mandate_id=mandate.mandate_id,
                actor=request.actor,
                strategy_version=runtime_strategy_identity,
                product=normalize_product_id(request.product_id),
                side=proposal.action,
                proposed_notional_usd=version.max_order_notional_usd,
                current_open_exposure_usd=Decimal("0"),
                daily_deployed_usd=Decimal("0"),
                daily_realized_loss_usd=Decimal("0"),
                campaign_drawdown_usd=Decimal("0"),
                consecutive_losses=0,
                current_position_count=0,
                risk_verdict=risk_summary.risk_verdict,
                evidence_age_seconds=version.price_evidence_max_age_seconds,
                kill_switch_engaged=False,
                observed_at=datetime.now(timezone.utc),
                decision_id=decision_record_id,
                request_context={
                    "cycle_id": str(cycle.cycle_id),
                    "trigger": request.trigger,
                },
                idempotency_key=f"ac-cycle-mandate-eval:{cycle.cycle_id}",
                audit_correlation_id=cycle.audit_correlation_id,
                software_build_version=request.software_build_version,
            ),
        )
        cycle.mandate_evaluation_id = mandate_eval.evaluation_id
        await db.flush()

        preview_id: uuid.UUID | None = None
        if proposal.action in {"BUY", "SELL"} and risk_summary.risk_verdict in {"ACCEPTED", "RESIZED"}:
            preview = await create_crypto_order_preview(
                db=db,
                actor=request.actor,
                request=CryptoOrderPreviewCreateRequest(
                    exchange_connection_id=mandate.exchange_connection_id,
                    environment=mandate.exchange_environment,
                    product_id=normalize_product_id(request.product_id),
                    side=proposal.action,
                    order_type="MARKET",
                    quote_size=version.max_order_notional_usd,
                    requested_amount_currency="USD",
                    decision_record_id=decision_record_id,
                    strategy_name=proposal.strategy_name,
                    generated_by="system_recommendation",
                    client_request_id=f"ac-cycle-preview:{cycle.cycle_id}",
                ),
            )
            preview_id = preview.crypto_order_preview_id
            cycle.preview_id = preview_id
            await _transition_cycle(db=db, cycle=cycle, to_state="PREVIEW_READY", stage="preview_generated")
        else:
            await _transition_cycle(db=db, cycle=cycle, to_state="HOLD", stage="hold_terminal")

        handoff_result = await _attempt_autonomous_paper_execution_handoff(
            db=db,
            cycle=cycle,
            mandate=mandate,
            request=request,
            proposal=proposal,
            risk_summary=risk_summary,
            signal=canonical_signal,
        )
        cycle.cycle_context = {
            **cycle.cycle_context,
            "execution_handoff": handoff_result,
        }
        await db.flush()

        await _transition_cycle(db=db, cycle=cycle, to_state="COMPLETE", stage="complete")
        cycle.termination_stage = "preview_generated" if preview_id is not None else "hold_terminal"
        cycle.deterministic_explanation = list(
            proposal.deterministic_explanation
        )
        cycle.completed_at = datetime.now(timezone.utc)
        cycle.diagnostics = _build_diagnostics_payload(
            started_monotonic=started_monotonic,
            evaluation_stage=cycle.evaluation_stage,
            termination_stage=cycle.termination_stage,
            failure_reason=None,
            explanation=tuple(cycle.deterministic_explanation),
        )

        db.add(
            AuditLog(
                actor=request.actor,
                action="AUTONOMOUS_CYCLE_COMPLETED",
                entity_type="autonomous_cycle_run",
                entity_id=cycle.cycle_id,
                before_state=None,
                after_state={
                    "state": cycle.state,
                    "proposed_action": cycle.proposed_action,
                    "mandate_verdict": cycle.mandate_verdict,
                    "risk_verdict": cycle.risk_verdict,
                    "decision_record_id": str(cycle.decision_record_id) if cycle.decision_record_id else None,
                    "preview_id": str(cycle.preview_id) if cycle.preview_id else None,
                    "audit_correlation_id": str(cycle.audit_correlation_id),
                },
            )
        )
        await db.commit()
        await db.refresh(cycle)
        return _to_cycle_result(cycle, replayed=False)
    except Exception:
        await db.rollback()
        raise


async def _reconcile_state(
    *,
    db: AsyncSession,
    mandate: AutonomousCapitalMandate,
    provider: Any,
    credentials: dict[str, str],
    environment: str,
    product_id: str,
) -> ReconciliationStatus:
    explanations: list[str] = []

    balances = await provider.fetch_balances(credentials=credentials, environment=environment)
    balances_loaded = bool(balances and balances.balances)
    if not balances_loaded:
        explanations.append("CHECK_FAILED:balances_unavailable")

    unresolved_orders = await db.scalar(
        select(func.count())
        .select_from(LiveCryptoOrder)
        .where(
            LiveCryptoOrder.exchange_connection_id == mandate.exchange_connection_id,
            LiveCryptoOrder.status.not_in(tuple(_RESOLVED_ORDER_STATUSES)),
        )
    )
    unresolved_order_count = int(unresolved_orders or 0)
    if unresolved_order_count > 0:
        explanations.append("CHECK_FAILED:unresolved_live_order_exists")

    open_position_count = 0
    if mandate.paper_account_id is not None:
        open_position_count = int(
            await db.scalar(
                select(func.count())
                .select_from(CryptoOrderPreview)
                .where(
                    CryptoOrderPreview.exchange_connection_id == mandate.exchange_connection_id,
                    CryptoOrderPreview.product_id == normalize_product_id(product_id),
                    CryptoOrderPreview.status == "PREVIEW_READY",
                )
            )
            or 0
        )

    return ReconciliationStatus(
        provider_ready=True,
        balances_loaded=balances_loaded,
        unresolved_order_count=unresolved_order_count,
        open_position_count=open_position_count,
        stale_evidence=False,
        explanation=tuple(explanations),
    )


async def _create_canonical_signal_for_cycle(
    *,
    db: AsyncSession,
    cycle: AutonomousCycleRun,
    mandate: AutonomousCapitalMandate,
    version: AutonomousCapitalMandateVersion,
    proposal: StrategyProposal,
    strategy_interval: str,
    product_id: str,
) -> Signal | None:
    if proposal.action not in {"BUY", "SELL"}:
        return None

    signal_id = uuid.uuid5(uuid.NAMESPACE_URL, f"ac-cycle-signal:{cycle.cycle_id}")
    existing = await db.scalar(
        select(Signal)
        .where(Signal.id == signal_id)
        .limit(1)
    )
    if existing is not None:
        return existing

    asset, _ = await _resolve_asset_for_cycle(
        db=db,
        product_id=product_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
    )
    if asset is None:
        return None

    selected_strategy_id: uuid.UUID | None = None
    strategy_identity = proposal.strategy_version
    parsed_identity = parse_strategy_identity(strategy_identity)
    if parsed_identity is not None:
        slug, module_version = parsed_identity
        selected_strategy_id = await db.scalar(
            select(Strategy.id)
            .where(Strategy.slug == slug)
            .where(Strategy.module_version == module_version)
            .where(Strategy.is_active.is_(True))
            .limit(1)
        )

    if selected_strategy_id is None:
        for candidate in version.allowed_strategy_versions:
            parsed = parse_strategy_identity(candidate)
            if parsed is None:
                continue
            slug, module_version = parsed
            selected_strategy_id = await db.scalar(
                select(Strategy.id)
                .where(Strategy.slug == slug)
                .where(Strategy.module_version == module_version)
                .where(Strategy.is_active.is_(True))
                .limit(1)
            )
            if selected_strategy_id is not None:
                break

    if selected_strategy_id is None:
        return None

    parameter_set_id = await db.scalar(
        select(ParameterSet.id)
        .where(ParameterSet.strategy_id == selected_strategy_id)
        .order_by(ParameterSet.created_at.desc())
        .limit(1)
    )
    if parameter_set_id is None:
        return None

    signal = Signal(
        id=signal_id,
        strategy_id=selected_strategy_id,
        parameter_set_id=parameter_set_id,
        asset_id=asset.id,
        signal_time=cycle.started_at or datetime.now(timezone.utc),
        action=proposal.action.lower(),
        raw_strength=None,
        ai_confidence=None,
        regime_tag=f"autonomous_cycle_{strategy_interval}",
        status="generated",
    )
    db.add(signal)
    await db.flush()
    return signal


async def _attempt_autonomous_paper_execution_handoff(
    *,
    db: AsyncSession,
    cycle: AutonomousCycleRun,
    mandate: AutonomousCapitalMandate,
    request: AutonomousCycleRequest,
    proposal: StrategyProposal,
    risk_summary: RiskEvaluationSummary,
    signal: Signal | None,
) -> dict[str, object]:
    if proposal.action == "HOLD":
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "HOLD_NOT_EXECUTABLE",
            "exact_result": "HOLD_NOT_EXECUTABLE",
            "canonical_signal": {
                "signal_id": None,
                "action": "HOLD",
                "executable": "NO",
                "mode": "PAPER",
            },
        }

    if proposal.action not in {"BUY", "SELL"}:
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "PAPER_EXECUTION_FAILED",
            "exact_result": "UNSUPPORTED_ACTION",
            "canonical_signal": {
                "signal_id": None,
                "action": proposal.action,
                "executable": "NO",
                "mode": "PAPER",
            },
        }

    if signal is None:
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "PAPER_EXECUTION_FAILED",
            "exact_result": "SIGNAL_CREATION_FAILED",
            "canonical_signal": {
                "signal_id": None,
                "action": proposal.action,
                "executable": "NO",
                "mode": "PAPER",
            },
        }

    signal_payload = {
        "signal_id": str(signal.id),
        "action": proposal.action,
        "executable": "YES",
        "mode": "PAPER",
    }

    if risk_summary.risk_verdict not in {"ACCEPTED", "RESIZED"}:
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "PAPER_EXECUTION_SKIPPED",
            "exact_result": "RISK_NOT_APPROVED",
            "canonical_signal": signal_payload,
        }

    if mandate.paper_account_id is None:
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "PAPER_EXECUTION_FAILED",
            "exact_result": "PAPER_ACCOUNT_MISSING",
            "canonical_signal": signal_payload,
        }

    paper_account = await db.scalar(
        select(PaperAccount)
        .where(PaperAccount.id == mandate.paper_account_id)
        .where(PaperAccount.is_active.is_(True))
        .limit(1)
    )
    if paper_account is None or paper_account.asset_class != "crypto":
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "PAPER_EXECUTION_FAILED",
            "exact_result": "PAPER_ACCOUNT_INCOMPATIBLE",
            "canonical_signal": signal_payload,
        }

    if risk_summary.approved_quantity is None or risk_summary.approved_quantity <= 0:
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": False,
            "status": "PAPER_EXECUTION_FAILED",
            "exact_result": "APPROVED_QUANTITY_UNAVAILABLE",
            "canonical_signal": signal_payload,
        }

    try:
        result = await orchestrate_paper_signal_execution(
            db=db,
            request=SignalExecutionRequest(
                signal_id=signal.id,
                paper_account_id=paper_account.id,
                asset_id=signal.asset_id,
                side=proposal.action.lower(),
                quantity=risk_summary.approved_quantity,
                actor=request.actor,
                client_order_id=f"ac-cycle:{cycle.cycle_id}",
            ),
        )
    except Exception as exc:
        return {
            "execution_handoff": "PAPER_EXECUTION",
            "attempted": True,
            "status": "PAPER_EXECUTION_FAILED",
            "exact_result": "PAPER_EXECUTION_EXCEPTION",
            "error_type": exc.__class__.__name__,
            "reason": str(exc) or exc.__class__.__name__,
            "canonical_signal": signal_payload,
        }

    mapped_status = "PAPER_EXECUTION_FAILED"
    if result.outcome == "EXECUTED":
        mapped_status = "PAPER_EXECUTION_ACCEPTED"
    elif result.outcome == "REJECTED":
        mapped_status = "PAPER_EXECUTION_REJECTED"
    elif result.outcome == "SKIPPED":
        mapped_status = "PAPER_EXECUTION_SKIPPED"

    return {
        "execution_handoff": "PAPER_EXECUTION",
        "attempted": True,
        "status": mapped_status,
        "exact_result": result.execution_status,
        "trade_id": str(result.trade_id) if result.trade_id is not None else None,
        "execution_venue": result.execution_venue,
        "reason_code": result.reason_code,
        "reason_text": result.reason_text,
        "canonical_signal": signal_payload,
    }


async def _run_approved_strategy(
    *,
    db: AsyncSession,
    mandate: AutonomousCapitalMandate,
    version: AutonomousCapitalMandateVersion,
    request: AutonomousCycleRequest,
) -> StrategyProposal:
    if request.forced_action in ACTIONS:
        return StrategyProposal(
            action=request.forced_action,
            strategy_name="forced_preview_action",
            strategy_version="forced",
            deterministic_explanation=(f"CHECK_INFO:forced_action={request.forced_action}",),
            signal_payload=None,
        )

    if request.forced_action is not None and request.forced_action not in ACTIONS:
        raise InvalidRequestError(
            message="forced_action must be BUY, SELL, or HOLD",
            details={"forced_action": request.forced_action},
        )

    asset, asset_resolution_reason = await _resolve_asset_for_cycle(
        db=db,
        product_id=request.product_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
    )
    if asset is None:
        return StrategyProposal(
            action="HOLD",
            strategy_name="none",
            strategy_version="none",
            deterministic_explanation=(f"CHECK_FAILED:{asset_resolution_reason or 'asset_not_found_for_strategy'}",),
            signal_payload=None,
        )

    strategies = list(
        (
            await db.execute(
                select(Strategy)
                .where(Strategy.is_active.is_(True))
                .order_by(Strategy.created_at.desc())
            )
        ).scalars().all()
    )

    approved_versions = {item for item in version.allowed_strategy_versions}
    selected = next(
        (
            item
            for item in strategies
            if build_strategy_identity(slug=item.slug, module_version=item.module_version) in approved_versions
        ),
        None,
    )
    if selected is None:
        return StrategyProposal(
            action="HOLD",
            strategy_name="none",
            strategy_version="none",
            deterministic_explanation=("CHECK_FAILED:no_approved_strategy_active",),
            signal_payload=None,
        )

    strategy_identity = build_strategy_identity(slug=selected.slug, module_version=selected.module_version)

    if not strategy_registry.has(selected.slug):
        return StrategyProposal(
            action="HOLD",
            strategy_name=selected.slug,
            strategy_version=strategy_identity,
            deterministic_explanation=("CHECK_FAILED:strategy_factory_not_registered",),
            signal_payload=None,
        )

    candles = list(
        (
            await db.execute(
                select(Candle)
                .where(Candle.asset_id == asset.id, Candle.interval == request.strategy_interval)
                .order_by(Candle.open_time.desc())
                .limit(200)
            )
        ).scalars().all()
    )
    if len(candles) < 3:
        return StrategyProposal(
            action="HOLD",
            strategy_name=selected.slug,
            strategy_version=strategy_identity,
            deterministic_explanation=("CHECK_FAILED:insufficient_candle_context",),
            signal_payload=None,
        )

    latest_candle = candles[0]
    oldest_candle = candles[-1]
    timeline = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "history_candle_count": len(candles),
        "latest_completed_candle_open": latest_candle.open_time.isoformat(),
        "latest_completed_candle_close": latest_candle.close_time.isoformat(),
        "oldest_candle_used_open": oldest_candle.open_time.isoformat(),
        "oldest_candle_used_close": oldest_candle.close_time.isoformat(),
        "current_incomplete_candle_excluded": True,
        "decision_applies_to": f"Latest completed {request.strategy_interval} candle",
    }

    params = await db.scalar(
        select(ParameterSet)
        .where(ParameterSet.strategy_id == selected.id)
        .order_by(ParameterSet.created_at.desc())
        .limit(1)
    )

    context = StrategyContext(
        candles=[
            {
                "open_time": row.open_time.isoformat(),
                "close_time": row.close_time.isoformat(),
                "open": format(Decimal(row.open), "f"),
                "high": format(Decimal(row.high), "f"),
                "low": format(Decimal(row.low), "f"),
                "close": format(Decimal(row.close), "f"),
                "volume": format(Decimal(row.volume), "f"),
            }
            for row in reversed(candles)
        ],
        asset_metadata={"symbol": asset.symbol, "asset_class": asset.asset_class},
        interval=request.strategy_interval,
        current_position=None,
        strategy_parameters=params.params if params is not None else {},
    )
    signal = strategy_registry.get(selected.slug).generate_signal(context)
    signal_payload = _serialize_strategy_signal(signal=signal)
    signal_action = str(signal_payload.get("action") or getattr(signal, "action", "hold"))

    action = signal_action.upper()
    if action not in ACTIONS:
        action = "HOLD"

    return StrategyProposal(
        action=action,
        strategy_name=selected.slug,
        strategy_version=strategy_identity,
        deterministic_explanation=(
            "CHECK_PASSED:strategy_evaluated",
            f"CHECK_INFO:strategy={selected.slug}",
            f"CHECK_INFO:strategy_identity={strategy_identity}",
            f"CHECK_INFO:signal_action={signal_action}",
        ),
        signal_payload={
            **signal_payload,
            "timeline": timeline,
            "market_window": {
                "asset_symbol": asset.symbol,
                "interval": request.strategy_interval,
                "history_candle_count": len(candles),
                "latest_completed_candle_open": latest_candle.open_time.isoformat(),
                "latest_completed_candle_close": latest_candle.close_time.isoformat(),
                "oldest_candle_used_open": oldest_candle.open_time.isoformat(),
                "oldest_candle_used_close": oldest_candle.close_time.isoformat(),
                "current_incomplete_candle_excluded": True,
            },
        },
    )


def _resolve_runtime_strategy_identity(
    *,
    proposal: StrategyProposal,
    version: AutonomousCapitalMandateVersion,
) -> str:
    if proposal.strategy_version != "none":
        return proposal.strategy_version

    if proposal.strategy_name != "none":
        for item in version.allowed_strategy_versions:
            parsed = parse_strategy_identity(item)
            if parsed is None:
                continue
            slug, module_version = parsed
            if slug == proposal.strategy_name:
                return build_strategy_identity(slug=slug, module_version=module_version)

    if len(version.allowed_strategy_versions) == 1:
        candidate = version.allowed_strategy_versions[0]
        parsed = parse_strategy_identity(candidate)
        if parsed is None:
            return candidate
        slug, module_version = parsed
        return build_strategy_identity(slug=slug, module_version=module_version)

    return proposal.strategy_version


def _evaluate_mandate_scope(
    *,
    version: AutonomousCapitalMandateVersion,
    product_id: str,
    action: str,
    strategy_version: str,
) -> tuple[str, str]:
    normalized_product = normalize_product_id(product_id)
    if normalized_product not in {item.upper() for item in version.allowed_products}:
        return MANDATE_AUTHORIZATION_REJECTED, "product_not_allowed"
    if action not in {item.upper() for item in version.allowed_order_sides}:
        return MANDATE_AUTHORIZATION_REJECTED, "side_not_allowed"
    if strategy_version != "forced" and strategy_version not in set(version.allowed_strategy_versions):
        return MANDATE_AUTHORIZATION_REJECTED, "strategy_version_not_allowed"
    return MANDATE_AUTHORIZATION_ALLOWED, "authorized_under_active_mandate"


async def _evaluate_risk(
    *,
    db: AsyncSession,
    mandate: AutonomousCapitalMandate,
    version: AutonomousCapitalMandateVersion,
    product_id: str,
    action: str,
    reference_price: Decimal,
    actor: str,
) -> RiskEvaluationSummary:
    if action == "HOLD":
        return RiskEvaluationSummary(
            risk_verdict="NOT_EVALUATED",
            risk_event_id=None,
            reason_code="hold_action",
            approved_quantity=None,
        )

    if mandate.paper_account_id is None:
        return RiskEvaluationSummary(
            risk_verdict="REJECTED",
            risk_event_id=None,
            reason_code="paper_account_required_for_risk_context",
            approved_quantity=None,
        )

    asset, _asset_resolution_reason = await _resolve_asset_for_cycle(
        db=db,
        product_id=product_id,
        provider=mandate.provider,
        exchange_environment=mandate.exchange_environment,
    )
    if asset is None:
        return RiskEvaluationSummary(
            risk_verdict="REJECTED",
            risk_event_id=None,
            reason_code="asset_not_found",
            approved_quantity=None,
        )

    from app.models.paper_account import PaperAccount

    paper_account = await db.scalar(select(PaperAccount).where(PaperAccount.id == mandate.paper_account_id).limit(1))
    if paper_account is None:
        return RiskEvaluationSummary(
            risk_verdict="REJECTED",
            risk_event_id=None,
            reason_code="paper_account_not_found",
            approved_quantity=None,
        )

    risk_context = await resolve_execution_risk_context(db=db, paper_account=paper_account, asset=asset)
    requested_quantity = version.max_order_notional_usd / reference_price

    risk_result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=uuid.uuid5(uuid.NAMESPACE_URL, f"ac-cycle-signal:{mandate.mandate_id}:{product_id}:{action}"),
            paper_account_id=paper_account.id,
            asset_id=asset.id,
            side=action.lower(),
            quantity=requested_quantity,
            account_equity=risk_context.account_equity,
            max_position_size_pct=risk_context.max_position_size_pct,
            min_order_notional=asset.min_order_notional,
            qty_step_size=asset.qty_step_size,
            supports_fractional=asset.supports_fractional,
            start_of_day_equity=risk_context.start_of_day_equity,
            current_equity=risk_context.current_equity,
            max_daily_loss_pct=risk_context.max_daily_loss_pct,
            high_water_mark_equity=risk_context.high_water_mark_equity,
            max_drawdown_pct=risk_context.max_drawdown_pct,
            consecutive_losses_on_pair=risk_context.consecutive_losses_on_pair,
            cooldown_after_losses=risk_context.cooldown_after_losses,
            last_loss_at=risk_context.last_loss_at,
            cooldown_duration_minutes=risk_context.cooldown_duration_minutes,
            evaluation_time=risk_context.evaluation_time,
            data_is_stale=risk_context.data_is_stale,
            data_has_gaps=risk_context.data_has_gaps,
            global_kill_switch_engaged_state=risk_context.global_kill_switch_engaged_state,
            global_kill_switch_rearm_required=risk_context.global_kill_switch_rearm_required,
            account_kill_switch_engaged_state=risk_context.account_kill_switch_engaged_state,
            account_kill_switch_rearm_required=risk_context.account_kill_switch_rearm_required,
            global_kill_switch_state_observed=risk_context.global_kill_switch_state_observed,
            account_kill_switch_state_observed=risk_context.account_kill_switch_state_observed,
            actor=actor,
        ),
        reference_price=reference_price,
    )

    persisted = await persist_risk_decision(
        db=db,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=paper_account.id,
            signal_id=None,
            actor=actor,
            evaluation_result=risk_result,
        ),
    )

    if risk_result.action == RiskDecisionAction.REJECT:
        verdict = "REJECTED"
    elif risk_result.action == RiskDecisionAction.RESIZE:
        verdict = "RESIZED"
    else:
        verdict = "ACCEPTED"

    return RiskEvaluationSummary(
        risk_verdict=verdict,
        risk_event_id=persisted.risk_event_id,
        reason_code=risk_result.reason_code,
        approved_quantity=risk_result.approved_quantity,
    )


async def _persist_decision_intelligence(
    *,
    db: AsyncSession,
    cycle: AutonomousCycleRun,
    mandate: AutonomousCapitalMandate,
    version: AutonomousCapitalMandateVersion,
    proposal: StrategyProposal,
    risk_summary: RiskEvaluationSummary,
    product_id: str,
    reference_price: Decimal,
    evidence_age_minutes: int,
    strategy_interval: str,
    canonical_signal_id: uuid.UUID | None,
    candle_id: uuid.UUID | None = None,
    candle_close_time: datetime | None = None,
    canonical_identity: dict[str, Any] | None = None,
) -> uuid.UUID:
    idempotency_key = f"ac-cycle-decision:{cycle.cycle_id}"
    existing = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return existing.decision_id

    trade_accepted = risk_summary.risk_verdict in {"ACCEPTED", "RESIZED"}
    signal_payload = proposal.signal_payload or {}
    signal_indicators = signal_payload.get("indicators") if isinstance(signal_payload, dict) else {}
    indicators_payload = signal_indicators if isinstance(signal_indicators, dict) else {}
    canonical_identity_payload = canonical_identity if isinstance(canonical_identity, dict) else {}
    replay_identity = ReplayIdentityProvenance(
        autonomous_mandate_id=cycle.mandate_id,
        autonomous_mandate_version=cycle.mandate_version_id,
        mandate_capital_campaign_row_id=getattr(mandate, "capital_campaign_id", None),
        mandate_paper_account_id=getattr(mandate, "paper_account_id", None),
        mandate_live_trading_profile_id=getattr(mandate, "live_trading_profile_id", None),
        canonical_campaign_id=canonical_identity_payload.get("canonical_campaign_id"),
        canonical_campaign_version=canonical_identity_payload.get("canonical_campaign_version"),
        runtime_campaign_id=canonical_identity_payload.get("runtime_campaign_id"),
        canonical_paper_account_id=canonical_identity_payload.get("canonical_paper_account_id"),
        canonical_live_trading_profile_id=canonical_identity_payload.get("canonical_live_trading_profile_id"),
    )
    replay_context = build_canonical_replay_context(
        evidence={
            "strategy_identity": proposal.strategy_name,
            "strategy_version": proposal.strategy_version,
            "action": proposal.action,
            "confidence": None,
            "product": normalize_product_id(product_id),
            "timeframe": strategy_interval,
            "provider": mandate.provider,
            "environment": getattr(mandate, "exchange_environment", None),
            "paper_account_id": canonical_identity_payload.get("canonical_paper_account_id"),
            "live_trading_profile_id": canonical_identity_payload.get("canonical_live_trading_profile_id"),
            "capital_campaign_id": canonical_identity_payload.get("canonical_campaign_id"),
            "capital_campaign_version": canonical_identity_payload.get("canonical_campaign_version"),
            "runtime_campaign_id": canonical_identity_payload.get("runtime_campaign_id"),
            "position_lifecycle_id": None,
            "signal_ids": [canonical_signal_id] if canonical_signal_id is not None else [],
            "risk_event_ids": [cycle.risk_event_id] if cycle.risk_event_id is not None else [],
            "trade_ids": [],
            "candle_id": candle_id,
            "candle_close_time": candle_close_time,
            "decision_timestamp": datetime.now(timezone.utc),
            "market_data_timestamp": signal_payload.get("timestamp") if isinstance(signal_payload, dict) else None,
            "normalized_risk_verdict": risk_summary.risk_verdict,
            "expected_gross_edge": signal_payload.get("expected_gross_edge") if isinstance(signal_payload, dict) else None,
            "expected_fees": signal_payload.get("expected_fees") if isinstance(signal_payload, dict) else None,
            "expected_slippage": signal_payload.get("expected_slippage") if isinstance(signal_payload, dict) else None,
            "expected_net_edge": signal_payload.get("expected_net_edge") if isinstance(signal_payload, dict) else None,
            "actual_execution_fee": None,
            "actual_execution_price": None,
            "actual_execution_quantity": None,
        },
        identity=replay_identity,
    )
    indicators_payload = {
        **indicators_payload,
        "replay_context": replay_context,
    }
    record = DecisionRecord(
        idempotency_key=idempotency_key,
        source_lineage={
            "autonomous_cycle_runs": [str(cycle.cycle_id)],
            "mandates": [str(mandate.mandate_id)],
            "mandate_versions": [str(version.mandate_version_id)],
            "risk_events": [str(cycle.risk_event_id)] if cycle.risk_event_id else [],
            "crypto_order_previews": [],
            "signals": [str(canonical_signal_id)] if canonical_signal_id is not None else [],
            "model_outputs": [],
            "trades": [],
        },
        field_provenance={
            "generated_signals": [{"entity_type": "autonomous_cycle_runs", "entity_id": str(cycle.cycle_id)}],
            "indicators": [{"entity_type": "strategy_signal", "entity_id": str(cycle.cycle_id)}] if signal_payload else [],
            "risk_adjustments": [{"entity_type": "risk_events", "entity_id": str(cycle.risk_event_id)}] if cycle.risk_event_id else [],
        },
        version=DECISION_ENGINE_VERSION,
        timestamp=datetime.now(timezone.utc),
        asset={"product_id": normalize_product_id(product_id), "provider": mandate.provider},
        timeframe=strategy_interval,
        market_regime={"state": "unknown", "source": "autonomous_cycle_preview"},
        indicators=indicators_payload,
        generated_signals=[
            {
                "action": proposal.action,
                "strategy": proposal.strategy_name,
                "strategy_version": proposal.strategy_version,
                "signal_reason": signal_payload.get("reason") if isinstance(signal_payload, dict) else None,
                "signal_generated": signal_payload.get("action") if isinstance(signal_payload, dict) else None,
                "strategy_evidence": signal_indicators if isinstance(signal_indicators, dict) else {},
                "timeline": signal_payload.get("timeline") if isinstance(signal_payload, dict) else None,
            }
        ],
        signal_strength=None,
        confidence=None,
        supporting_strategies=[{"strategy": proposal.strategy_name, "version": proposal.strategy_version}],
        opposing_strategies=[],
        risk_adjustments=[
            {
                "risk_verdict": risk_summary.risk_verdict,
                "risk_event_id": str(cycle.risk_event_id) if cycle.risk_event_id else None,
                "reason_code": risk_summary.reason_code,
            }
        ],
        expected_risk={
            "risk_event_id": str(cycle.risk_event_id) if cycle.risk_event_id else None,
            "risk_verdict": risk_summary.risk_verdict,
        },
        expected_reward=None,
        position_size=version.max_order_notional_usd / reference_price if reference_price > 0 else None,
        trade_accepted=trade_accepted,
        trade_rejected_reason=None if trade_accepted else (risk_summary.reason_code or "risk_rejected"),
        execution_details={
            "preview_id": None,
            "audit_correlation_id": str(cycle.audit_correlation_id),
            "stage": "preview_mode_no_submission",
        },
        exit_details=None,
        pnl=None,
        duration=None,
        outcome="pending_preview" if proposal.action in {"BUY", "SELL"} else "not_taken",
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=["autonomous_cycle_preview"],
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
        exchange=mandate.provider,
        timeframe=strategy_interval,
        ohlcv_context=[],
        indicators=record.indicators,
        generated_features={"evidence_age_minutes": evidence_age_minutes},
        market_regime=record.market_regime,
        volatility={"state": "unknown"},
        spread_liquidity_context=None,
        strategy_inputs={
            "allowed_strategy_versions": list(version.allowed_strategy_versions),
            "selected_strategy": proposal.strategy_name,
            "selected_strategy_identity": proposal.strategy_version,
            "strategy_evidence": signal_indicators if isinstance(signal_indicators, dict) else {},
            "signal_reason": signal_payload.get("reason") if isinstance(signal_payload, dict) else None,
            "signal_generated": signal_payload.get("action") if isinstance(signal_payload, dict) else None,
            "timeline": signal_payload.get("timeline") if isinstance(signal_payload, dict) else None,
        },
        risk_inputs={
            "risk_verdict": risk_summary.risk_verdict,
            "risk_event_id": str(cycle.risk_event_id) if cycle.risk_event_id else None,
        },
        current_position_state=None,
        open_trades=[],
        portfolio_exposure={
            "max_open_exposure_usd": format(version.max_open_exposure_usd, "f"),
            "max_order_notional_usd": format(version.max_order_notional_usd, "f"),
        },
        parameter_set_version="unknown",
        strategy_version=proposal.strategy_version,
        ai_model_version="none",
        decision_engine_version=DECISION_ENGINE_VERSION,
        configuration_version="autonomous_cycle_preview_v1",
    )
    db.add(snapshot)
    await db.flush()
    return record.decision_id


async def _transition_cycle(
    *,
    db: AsyncSession,
    cycle: AutonomousCycleRun,
    to_state: str,
    stage: str,
) -> None:
    if to_state not in CYCLE_STATES:
        raise ValueError(f"unsupported cycle state: {to_state}")

    allowed = _ALLOWED_TRANSITIONS.get(cycle.state, set())
    if to_state != cycle.state and to_state not in allowed:
        raise ValueError(f"invalid cycle transition: {cycle.state} -> {to_state}")

    cycle.state = to_state
    cycle.evaluation_stage = stage
    cycle.updated_at = datetime.now(timezone.utc)
    await db.flush()


async def _finish_hold(
    *,
    db: AsyncSession,
    cycle: AutonomousCycleRun,
    stage: str,
    reason: str,
    explanation: tuple[str, ...],
    started_monotonic: float,
) -> AutonomousCycleResult:
    await _transition_cycle(db=db, cycle=cycle, to_state="HOLD", stage=stage)
    await _transition_cycle(db=db, cycle=cycle, to_state="COMPLETE", stage="complete")
    cycle.termination_stage = stage
    cycle.failure_reason = reason
    cycle.deterministic_explanation = list(explanation)
    cycle.completed_at = datetime.now(timezone.utc)
    cycle.diagnostics = _build_diagnostics_payload(
        started_monotonic=started_monotonic,
        evaluation_stage=stage,
        termination_stage=stage,
        failure_reason=reason,
        explanation=explanation,
    )
    db.add(
        AuditLog(
            actor="autonomous_cycle",
            action="AUTONOMOUS_CYCLE_HOLD",
            entity_type="autonomous_cycle_run",
            entity_id=cycle.cycle_id,
            before_state=None,
            after_state={
                "state": cycle.state,
                "termination_stage": cycle.termination_stage,
                "failure_reason": cycle.failure_reason,
            },
        )
    )
    await db.commit()
    await db.refresh(cycle)
    return _to_cycle_result(cycle, replayed=False)


async def _finish_failed(
    *,
    db: AsyncSession,
    cycle: AutonomousCycleRun,
    stage: str,
    reason: str,
    explanation: tuple[str, ...],
    started_monotonic: float,
) -> AutonomousCycleResult:
    await _transition_cycle(db=db, cycle=cycle, to_state="FAILED", stage=stage)
    cycle.termination_stage = stage
    cycle.failure_reason = reason
    cycle.deterministic_explanation = list(explanation)
    cycle.completed_at = datetime.now(timezone.utc)
    cycle.diagnostics = _build_diagnostics_payload(
        started_monotonic=started_monotonic,
        evaluation_stage=stage,
        termination_stage=stage,
        failure_reason=reason,
        explanation=explanation,
    )
    db.add(
        AuditLog(
            actor="autonomous_cycle",
            action="AUTONOMOUS_CYCLE_FAILED",
            entity_type="autonomous_cycle_run",
            entity_id=cycle.cycle_id,
            before_state=None,
            after_state={
                "state": cycle.state,
                "termination_stage": cycle.termination_stage,
                "failure_reason": cycle.failure_reason,
            },
        )
    )
    await db.commit()
    await db.refresh(cycle)
    return _to_cycle_result(cycle, replayed=False)


def _build_diagnostics_payload(
    *,
    started_monotonic: float,
    evaluation_stage: str | None,
    termination_stage: str,
    failure_reason: str | None,
    explanation: tuple[str, ...],
) -> dict[str, object]:
    return {
        "duration_ms": int((time.monotonic() - started_monotonic) * 1000),
        "evaluation_stage": evaluation_stage,
        "termination_stage": termination_stage,
        "failure_reason": failure_reason,
        "deterministic_explanation": list(explanation),
    }


def _to_cycle_result(cycle: AutonomousCycleRun, *, replayed: bool) -> AutonomousCycleResult:
    diagnostics_raw = cycle.diagnostics or {}
    diagnostics = CycleDiagnostics(
        duration_ms=int(diagnostics_raw.get("duration_ms") or 0),
        evaluation_stage=diagnostics_raw.get("evaluation_stage"),
        termination_stage=str(diagnostics_raw.get("termination_stage") or cycle.termination_stage or "unknown"),
        failure_reason=diagnostics_raw.get("failure_reason"),
        deterministic_explanation=tuple(diagnostics_raw.get("deterministic_explanation") or cycle.deterministic_explanation or []),
    )
    return AutonomousCycleResult(
        cycle_id=cycle.cycle_id,
        state=cycle.state,
        idempotency_key=cycle.idempotency_key,
        mandate_id=cycle.mandate_id,
        mandate_version_id=cycle.mandate_version_id,
        proposed_action=cycle.proposed_action or "HOLD",
        mandate_verdict=cycle.mandate_verdict or MANDATE_AUTHORIZATION_REJECTED,
        risk_verdict=cycle.risk_verdict or "NOT_EVALUATED",
        decision_record_id=cycle.decision_record_id,
        preview_id=cycle.preview_id,
        mandate_evaluation_id=cycle.mandate_evaluation_id,
        risk_event_id=cycle.risk_event_id,
        audit_correlation_id=cycle.audit_correlation_id,
        diagnostics=diagnostics,
        replayed=replayed,
        cycle_context=cycle.cycle_context or {},
        started_at=cycle.started_at,
        completed_at=cycle.completed_at,
    )


async def _has_valid_exact_version_authorization(
    *,
    db: AsyncSession,
    mandate_id: uuid.UUID,
    mandate_version_id: uuid.UUID,
    observed_at: datetime,
) -> bool:
    authorization_id = await db.scalar(
        select(AutonomousCapitalMandateAuthorization.mandate_authorization_id)
        .where(
            AutonomousCapitalMandateAuthorization.mandate_id == mandate_id,
            AutonomousCapitalMandateAuthorization.mandate_version_id == mandate_version_id,
            AutonomousCapitalMandateAuthorization.authorization_state == "AUTHORIZED",
            AutonomousCapitalMandateAuthorization.revoked_at.is_(None),
            or_(
                AutonomousCapitalMandateAuthorization.expires_at.is_(None),
                AutonomousCapitalMandateAuthorization.expires_at > observed_at,
            ),
        )
        .limit(1)
    )
    return authorization_id is not None


def normalize_product_id(product_id: str) -> str:
    return product_id.strip().upper().replace("/", "-")


def product_to_asset_symbol(product_id: str) -> str:
    normalized = normalize_product_id(product_id)
    parts = normalized.split("-", 1)
    if len(parts) == 2:
        return f"{parts[0]}{parts[1]}"
    return normalized.replace("-", "")


def _canonicalize_asset_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    return {
        "XXBT": "BTC",
        "XBT": "BTC",
    }.get(normalized, normalized)


def _candidate_asset_symbols_for_product(product_id: str) -> tuple[str, ...]:
    normalized_product = normalize_product_id(product_id)
    base_symbol = normalized_product.split("-", 1)[0] if "-" in normalized_product else normalized_product
    canonical_base_symbol = _canonicalize_asset_symbol(base_symbol)
    candidates = {canonical_base_symbol}
    if canonical_base_symbol == "BTC":
        candidates.update({"XBT", "XXBT"})
    return tuple(sorted(candidates))


def _provider_exchange_label(*, provider: str, exchange_environment: str) -> str:
    normalized_environment = exchange_environment.strip().lower()
    if normalized_environment == "sandbox":
        return f"{provider}_sandbox"
    return provider


async def _resolve_asset_for_cycle(
    *,
    db: AsyncSession,
    product_id: str,
    provider: str,
    exchange_environment: str,
) -> tuple[Asset | None, str | None]:
    candidate_symbols = _candidate_asset_symbols_for_product(product_id)
    exchange_label = _provider_exchange_label(provider=provider, exchange_environment=exchange_environment)
    rows = list(
        (
            await db.execute(
                select(Asset)
                .where(func.upper(Asset.symbol).in_(candidate_symbols))
                .where(Asset.asset_class == "crypto")
                .where(Asset.exchange == exchange_label)
                .where(Asset.is_active.is_(True))
                .order_by(desc(Asset.created_at))
                .limit(2)
            )
        ).scalars().all()
    )
    if not rows:
        return None, "asset_not_found_for_strategy"
    if len(rows) > 1:
        return None, "ambiguous_asset_resolution_for_strategy"
    return rows[0], None


def _to_version_model(
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
