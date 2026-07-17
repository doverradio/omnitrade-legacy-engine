from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_capital_mandate_version import AutonomousCapitalMandateVersion
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.schemas.capital_campaign_domain import (
    CommissionedCampaignState,
    CommissionedPreviewResponse,
    CommissionedReadinessRequest,
    CommissionedReadinessResponse,
)
from app.services.capital_campaign_domain.commissioned_state_machine import commissioned_state_expected_statuses
from app.services.live.approval import evaluate_live_approval_gate
from app.services.mandates.contracts import MandateVersionModel
from app.services.mandates.validation import validate_mandate_version
from app.services.position_lifecycle.source_adapter import load_position_snapshots


_COMMISSIONED_STATE_KEY = "commissioned_seed_campaign"
_AUTHORITY_CLASSIFICATION = "OPERATOR_COMMISSIONED"
_STRATEGY_CLASSIFICATION = "NOT_REQUIRED_FOR_COMMISSIONED_ENTRY"

_ENTRY_ELIGIBLE_STATES: set[CommissionedCampaignState] = {
    "READY",
    "COMMISSIONED",
    "BUY_PENDING",
}

_RECONCILIATION_BLOCKING_STATUSES = {
    "reconciliation_required",
    "conflict",
    "balance_mismatch",
    "unknown",
}

_OPEN_ORDER_STATUSES = {
    "SUBMISSION_PENDING",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "RECONCILIATION_REQUIRED",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_instrument(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _to_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _to_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _age_seconds(*, observed_at: datetime | None, now: datetime) -> float | None:
    if observed_at is None:
        return None
    return max(0.0, (now - observed_at).total_seconds())


def _evidence_source(evidence: dict[str, Any], *, fallback: str) -> str:
    source = str(evidence.get("source") or "").strip()
    return source if source else fallback


def _read_commissioned_blob(*, metadata_evidence: dict[str, Any]) -> dict[str, Any] | None:
    blob = metadata_evidence.get(_COMMISSIONED_STATE_KEY)
    return blob if isinstance(blob, dict) else None


def _check(*, code: str, passed: bool, blocker_reason: str | None = None, warning_reason: str | None = None, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "status": "pass" if passed else "fail",
        "blocker": blocker_reason,
        "warning": warning_reason,
        "detail": detail or {},
    }


def _blockers_and_warnings(checks: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    for item in checks:
        blocker = item.get("blocker")
        warning = item.get("warning")
        if isinstance(blocker, str) and blocker and blocker not in blockers:
            blockers.append(blocker)
        if isinstance(warning, str) and warning and warning not in warnings:
            warnings.append(warning)
    return blockers, warnings


async def _load_campaign_definition(*, db: AsyncSession, campaign_id: UUID, version: int) -> CapitalCampaignDefinition | None:
    return await db.scalar(
        select(CapitalCampaignDefinition)
        .where(CapitalCampaignDefinition.campaign_id == campaign_id)
        .where(CapitalCampaignDefinition.version == version)
        .limit(1)
    )


async def _load_runtime_campaign(*, db: AsyncSession, campaign_id: UUID) -> CapitalCampaign | None:
    return await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.uuid == campaign_id)
        .limit(1)
    )


async def _load_mandate(*, db: AsyncSession, mandate_id: UUID | None) -> AutonomousCapitalMandate | None:
    if mandate_id is None:
        return None
    return await db.scalar(
        select(AutonomousCapitalMandate)
        .where(AutonomousCapitalMandate.mandate_id == mandate_id)
        .limit(1)
    )


async def _load_mandate_version(*, db: AsyncSession, mandate_version_id: UUID | None) -> AutonomousCapitalMandateVersion | None:
    if mandate_version_id is None:
        return None
    return await db.scalar(
        select(AutonomousCapitalMandateVersion)
        .where(AutonomousCapitalMandateVersion.mandate_version_id == mandate_version_id)
        .limit(1)
    )


async def _has_open_order_conflict(*, db: AsyncSession, runtime_campaign_id: int | None, provider: str, environment: str, instrument: str) -> bool:
    statement = (
        select(LiveCryptoOrder)
        .where(LiveCryptoOrder.provider == provider)
        .where(LiveCryptoOrder.environment == environment)
        .where(LiveCryptoOrder.product_id == instrument)
        .where(LiveCryptoOrder.status.in_(sorted(_OPEN_ORDER_STATUSES)))
        .order_by(desc(LiveCryptoOrder.created_at))
        .limit(25)
    )
    rows = list((await db.execute(statement)).scalars().all())
    if runtime_campaign_id is None:
        return bool(rows)

    for row in rows:
        payload = row.safe_provider_response if isinstance(row.safe_provider_response, dict) else {}
        if str(payload.get("capital_campaign_id") or "").strip() == str(runtime_campaign_id):
            return True
    return False


async def _has_reconciliation_conflict(*, db: AsyncSession, runtime_campaign_id: int | None) -> bool:
    if runtime_campaign_id is None:
        return False
    row = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.capital_campaign_id == runtime_campaign_id)
        .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_RECONCILIATION_BLOCKING_STATUSES)))
        .order_by(desc(LiveReconciliationEvent.recorded_at), desc(LiveReconciliationEvent.sequence_number))
        .limit(1)
    )
    return row is not None


def _mandate_version_model(version: AutonomousCapitalMandateVersion) -> MandateVersionModel:
    return MandateVersionModel(
        mandate_version_id=version.mandate_version_id,
        mandate_id=version.mandate_id,
        version_number=version.version_number,
        base_currency=version.base_currency,
        authorized_capital_usd=version.authorized_capital_usd,
        max_order_notional_usd=version.max_order_notional_usd,
        max_open_exposure_usd=version.max_open_exposure_usd,
        max_daily_deployed_usd=version.max_daily_deployed_usd,
        max_daily_realized_loss_usd=version.max_daily_realized_loss_usd,
        max_campaign_drawdown_usd=version.max_campaign_drawdown_usd,
        max_consecutive_losses=version.max_consecutive_losses,
        position_limit=version.position_limit,
        price_evidence_max_age_seconds=version.price_evidence_max_age_seconds,
        max_slippage_bps=version.max_slippage_bps,
        max_fee_bps=version.max_fee_bps,
        allowed_products=tuple(version.allowed_products or []),
        allowed_order_sides=tuple(version.allowed_order_sides or []),
        allowed_strategy_versions=tuple(version.allowed_strategy_versions or []),
        approval_policy=version.approval_policy,
        is_authorized=bool(version.is_authorized),
        is_active=bool(version.is_active),
    )


def _stale_deadline(*, observed_at: datetime | None, max_age_seconds: int | None) -> datetime | None:
    if observed_at is None or max_age_seconds is None:
        return None
    return observed_at + timedelta(seconds=max_age_seconds)


def _preview_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def assess_commissioned_campaign_readiness(
    *,
    db: AsyncSession,
    request: CommissionedReadinessRequest,
) -> CommissionedReadinessResponse:
    now = _utcnow()
    checks: list[dict[str, Any]] = []

    definition = await _load_campaign_definition(db=db, campaign_id=request.campaign_id, version=request.version)
    runtime = await _load_runtime_campaign(db=db, campaign_id=request.campaign_id)

    identity_ok = definition is not None and runtime is not None and runtime.definition_version == request.version
    checks.append(
        _check(
            code="campaign_identity",
            passed=identity_ok,
            blocker_reason=None if identity_ok else "ambiguous_campaign_version_identity",
            detail={"campaign_found": definition is not None, "runtime_found": runtime is not None},
        )
    )

    metadata = dict(definition.metadata_evidence or {}) if definition is not None else {}
    commissioned_blob = _read_commissioned_blob(metadata_evidence=metadata)
    commissioned_state = None if commissioned_blob is None else str(commissioned_blob.get("state") or "DRAFT")

    authority_metadata = commissioned_blob.get("authority_metadata") if commissioned_blob is not None else None
    authority_ok = isinstance(authority_metadata, dict)
    checks.append(
        _check(
            code="commissioned_authority",
            passed=authority_ok,
            blocker_reason=None if authority_ok else "missing_commissioned_authority",
        )
    )

    campaign_type = None if not authority_ok else str(authority_metadata.get("campaign_type") or "").strip()
    checks.append(
        _check(
            code="campaign_type",
            passed=campaign_type == "COMMISSIONED_AUTONOMOUS_SEED",
            blocker_reason=None if campaign_type == "COMMISSIONED_AUTONOMOUS_SEED" else "invalid_campaign_type_for_commissioned_preview",
        )
    )

    if definition is not None and runtime is not None and commissioned_state:
        expected_definition_status, expected_runtime_status = commissioned_state_expected_statuses(
            commissioned_state=commissioned_state
        )
        projection_ok = definition.status == expected_definition_status and runtime.status == expected_runtime_status
    else:
        projection_ok = False
    checks.append(
        _check(
            code="state_projection_consistency",
            passed=projection_ok,
            blocker_reason=None if projection_ok else "inconsistent_commissioned_state_metadata",
        )
    )

    lifecycle_ok = commissioned_state in _ENTRY_ELIGIBLE_STATES
    checks.append(
        _check(
            code="campaign_lifecycle_eligibility",
            passed=lifecycle_ok,
            blocker_reason=None if lifecycle_ok else "campaign_lifecycle_not_entry_eligible",
            detail={"commissioned_state": commissioned_state},
        )
    )

    authorization_expires_at = request.authorization_expires_at
    authorization_present = authorization_expires_at is not None
    authorization_active = authorization_present and authorization_expires_at > now
    checks.append(
        _check(
            code="authorization_window",
            passed=authorization_active,
            blocker_reason=None if authorization_active else (
                "missing_operator_authorization_window" if not authorization_present else "expired_operator_authorization"
            ),
            warning_reason=(
                "authorization_expiring_soon"
                if authorization_active and (authorization_expires_at - now).total_seconds() <= 300
                else None
            ),
        )
    )

    cap = _to_decimal(authority_metadata.get("maximum_entry_notional") if authority_ok else None)
    requested = request.requested_quote_amount
    cap_ok = cap is not None and requested <= cap and requested > Decimal("0")
    checks.append(
        _check(
            code="capital_cap",
            passed=cap_ok,
            blocker_reason=None if cap_ok else "requested_quote_amount_above_authorized_cap",
            detail={"requested_quote_amount": requested, "capital_cap": cap},
        )
    )

    provider_capability = request.provider_capability_evidence
    provider_supported = bool(provider_capability.get("supported") is True)
    checks.append(
        _check(
            code="provider_capability",
            passed=provider_supported,
            blocker_reason=None if provider_supported else "unsupported_provider_capability",
        )
    )

    connectivity = request.connectivity_evidence
    connectivity_ok = bool(connectivity.get("reachable") is True)
    checks.append(
        _check(
            code="connectivity",
            passed=connectivity_ok,
            blocker_reason=None if connectivity_ok else "connectivity_evidence_unavailable",
        )
    )

    balance = request.balance_evidence
    available_balance = _to_decimal(balance.get("available_quote_balance"))
    balance_ok = available_balance is not None and available_balance >= requested
    checks.append(
        _check(
            code="balance",
            passed=balance_ok,
            blocker_reason=None if balance_ok else "insufficient_balance",
        )
    )

    market = request.market_data_evidence
    market_ts = _to_datetime(market.get("observed_at"))
    market_max_age = int(market.get("max_age_seconds") or 0) or None
    market_age = _age_seconds(observed_at=market_ts, now=now)
    market_fresh = market_ts is not None and market_max_age is not None and market_age is not None and market_age <= market_max_age
    checks.append(
        _check(
            code="market_data_freshness",
            passed=market_fresh,
            blocker_reason=None if market_fresh else "stale_or_missing_market_evidence",
        )
    )

    price = request.price_evidence
    reference_price = _to_decimal(price.get("reference_price"))
    price_ts = _to_datetime(price.get("observed_at"))
    price_max_age = int(price.get("max_age_seconds") or 0) or None
    price_age = _age_seconds(observed_at=price_ts, now=now)
    price_fresh = (
        reference_price is not None
        and reference_price > Decimal("0")
        and price_ts is not None
        and price_max_age is not None
        and price_age is not None
        and price_age <= price_max_age
    )
    checks.append(
        _check(
            code="price_evidence_freshness",
            passed=price_fresh,
            blocker_reason=None if price_fresh else "stale_or_missing_price_evidence",
        )
    )

    minimum = request.minimum_order_evidence
    minimum_quote_amount = _to_decimal(minimum.get("minimum_quote_amount"))
    minimum_ok = minimum_quote_amount is not None and requested >= minimum_quote_amount
    checks.append(
        _check(
            code="minimum_order",
            passed=minimum_ok,
            blocker_reason=None if minimum_ok else "minimum_order_violation",
        )
    )

    fee_slippage = request.fee_slippage_evidence
    entry_fee = _to_decimal(fee_slippage.get("estimated_entry_fee"))
    exit_fee = _to_decimal(fee_slippage.get("estimated_future_exit_fee"))
    slippage = _to_decimal(fee_slippage.get("estimated_slippage"))
    costs_available = entry_fee is not None and slippage is not None
    checks.append(
        _check(
            code="fee_slippage_evidence",
            passed=costs_available,
            blocker_reason=None if costs_available else "missing_fee_or_slippage_evidence",
        )
    )

    expected_quantity = None
    if costs_available and price_fresh and reference_price is not None:
        net_quote = requested - entry_fee - slippage
        if net_quote > Decimal("0"):
            expected_quantity = net_quote / reference_price

    min_base_quantity = _to_decimal(minimum.get("minimum_base_quantity"))
    quantity_ok = expected_quantity is not None and expected_quantity > Decimal("0")
    if quantity_ok and min_base_quantity is not None:
        quantity_ok = expected_quantity >= min_base_quantity
    checks.append(
        _check(
            code="expected_entry_quantity",
            passed=quantity_ok,
            blocker_reason=None if quantity_ok else "expected_entry_quantity_invalid",
        )
    )

    idempotency_ok = bool((request.idempotency_key or "").strip())
    checks.append(
        _check(
            code="idempotency_identity",
            passed=idempotency_ok,
            blocker_reason=None if idempotency_ok else "missing_idempotency_identity",
        )
    )

    mandate = await _load_mandate(db=db, mandate_id=request.mandate_id)
    mandate_version = await _load_mandate_version(db=db, mandate_version_id=request.mandate_version_id)
    mandate_ok = mandate is not None and mandate_version is not None
    checks.append(
        _check(
            code="mandate_identity",
            passed=mandate_ok,
            blocker_reason=None if mandate_ok else "missing_mandate_identity",
        )
    )

    mandate_version_ok = False
    if mandate_version is not None:
        validation = validate_mandate_version(_mandate_version_model(mandate_version))
        mandate_version_ok = validation.valid and bool(mandate_version.is_authorized) and bool(mandate_version.is_active)
    checks.append(
        _check(
            code="mandate_version",
            passed=mandate_version_ok,
            blocker_reason=None if mandate_version_ok else "mandate_version_mismatch",
        )
    )

    if request.expected_mandate_version_number is not None and mandate_version is not None:
        number_ok = mandate_version.version_number == request.expected_mandate_version_number
    else:
        number_ok = False
    checks.append(
        _check(
            code="mandate_version_number",
            passed=number_ok,
            blocker_reason=None if number_ok else "mandate_version_mismatch",
        )
    )

    risk_policy_ok = (
        definition is not None
        and request.expected_risk_policy_id is not None
        and request.expected_risk_policy_version is not None
        and definition.risk_policy_id == request.expected_risk_policy_id
        and definition.risk_policy_version == request.expected_risk_policy_version
    )
    checks.append(
        _check(
            code="risk_policy_identity",
            passed=risk_policy_ok,
            blocker_reason=None if risk_policy_ok else "risk_policy_identity_mismatch",
        )
    )

    approval_ok = False
    approval_reason = "approval_checkpoint_missing"
    if request.live_trading_profile_id is not None:
        approval_gate = await evaluate_live_approval_gate(
            db=db,
            live_trading_profile_id=request.live_trading_profile_id,
            checkpoint_type=request.approval_checkpoint_type,
            observed_at=now,
        )
        approval_ok = bool(approval_gate.allowed)
        approval_reason = str(approval_gate.reason or "approval_not_active")
    checks.append(
        _check(
            code="operator_approval_gate",
            passed=approval_ok,
            blocker_reason=None if approval_ok else approval_reason,
        )
    )

    runtime_evidence = request.runtime_readiness_evidence
    runtime_ready = bool(runtime_evidence.get("ready") is True)
    checks.append(
        _check(
            code="system_runtime_readiness",
            passed=runtime_ready,
            blocker_reason=None if runtime_ready else "system_runtime_not_ready",
        )
    )

    open_order_conflict = await _has_open_order_conflict(
        db=db,
        runtime_campaign_id=(None if runtime is None else runtime.id),
        provider=request.provider,
        environment=request.environment,
        instrument=request.instrument,
    )
    checks.append(
        _check(
            code="existing_order_conflict",
            passed=not open_order_conflict,
            blocker_reason=None if not open_order_conflict else "existing_active_entry_conflict",
        )
    )

    reconciliation_conflict = await _has_reconciliation_conflict(
        db=db,
        runtime_campaign_id=(None if runtime is None else runtime.id),
    )
    checks.append(
        _check(
            code="reconciliation_conflict",
            passed=not reconciliation_conflict,
            blocker_reason=None if not reconciliation_conflict else "unresolved_reconciliation_conflict",
        )
    )

    manual_review_requested = bool(request.manual_review_evidence.get("required") is True)
    commissioned_manual_review = commissioned_state == "MANUAL_REVIEW_REQUIRED"
    manual_review_ok = not manual_review_requested and not commissioned_manual_review
    checks.append(
        _check(
            code="manual_review",
            passed=manual_review_ok,
            blocker_reason=None if manual_review_ok else "unresolved_manual_review_condition",
        )
    )

    position_conflict = False
    if runtime is not None:
        snapshots = await load_position_snapshots(
            db=db,
            account_id=request.account_id or runtime.paper_account_id,
            campaign_id=runtime.id,
        )
        position_conflict = any(snapshot.position_size > Decimal("0") for snapshot in snapshots)
    checks.append(
        _check(
            code="position_conflict",
            passed=not position_conflict,
            blocker_reason=None if not position_conflict else "existing_position_or_entry_conflict",
        )
    )

    provider_identity_ok = True
    if mandate is not None:
        provider_identity_ok = (
            mandate.provider == request.provider and mandate.exchange_environment == request.environment
        )
    checks.append(
        _check(
            code="provider_identity",
            passed=provider_identity_ok,
            blocker_reason=None if provider_identity_ok else "provider_identity_mismatch",
        )
    )

    instrument_identity_ok = True
    if mandate_version is not None:
        instrument_identity_ok = _normalize_instrument(request.instrument) in {
            _normalize_instrument(item) for item in (mandate_version.allowed_products or [])
        }
    checks.append(
        _check(
            code="instrument_identity",
            passed=instrument_identity_ok,
            blocker_reason=None if instrument_identity_ok else "instrument_identity_mismatch",
        )
    )

    blockers, warnings = _blockers_and_warnings(checks)
    verdict = "READY" if not blockers else "BLOCKED"

    evidence_timestamps = {
        "authorization_expires_at": authorization_expires_at,
        "provider_capability_observed_at": _to_datetime(provider_capability.get("observed_at")),
        "connectivity_observed_at": _to_datetime(connectivity.get("observed_at")),
        "balance_observed_at": _to_datetime(balance.get("observed_at")),
        "market_data_observed_at": market_ts,
        "price_observed_at": price_ts,
        "minimum_order_observed_at": _to_datetime(minimum.get("observed_at")),
        "runtime_readiness_observed_at": _to_datetime(runtime_evidence.get("observed_at")),
    }

    evidence_provenance = {
        "provider_capability": _evidence_source(provider_capability, fallback="caller_supplied"),
        "connectivity": _evidence_source(connectivity, fallback="caller_supplied"),
        "balance": _evidence_source(balance, fallback="caller_supplied"),
        "market_data": _evidence_source(market, fallback="caller_supplied"),
        "price": _evidence_source(price, fallback="caller_supplied"),
        "minimum_order": _evidence_source(minimum, fallback="caller_supplied"),
        "fee_slippage": _evidence_source(fee_slippage, fallback="caller_supplied"),
        "runtime": _evidence_source(runtime_evidence, fallback="caller_supplied"),
    }

    stale_candidates = [
        _stale_deadline(observed_at=market_ts, max_age_seconds=market_max_age),
        _stale_deadline(observed_at=price_ts, max_age_seconds=price_max_age),
        authorization_expires_at,
    ]
    stale_after = min([item for item in stale_candidates if item is not None], default=None)

    return CommissionedReadinessResponse(
        campaign_id=request.campaign_id,
        version=request.version,
        readiness_verdict=verdict,
        blockers=blockers,
        warnings=warnings,
        checks=checks,
        authority_classification=_AUTHORITY_CLASSIFICATION,
        strategy_signal_classification=_STRATEGY_CLASSIFICATION,
        commissioned_state=commissioned_state,
        expected_entry_quantity=expected_quantity,
        applicable_capital_cap=cap,
        estimated_entry_fee=entry_fee,
        estimated_future_exit_fee=exit_fee,
        estimated_slippage=slippage,
        evidence_timestamps=evidence_timestamps,
        evidence_provenance=evidence_provenance,
        stale_after=stale_after,
    )


async def generate_commissioned_campaign_preview(
    *,
    db: AsyncSession,
    request: CommissionedReadinessRequest,
) -> CommissionedPreviewResponse:
    readiness = await assess_commissioned_campaign_readiness(db=db, request=request)

    reference_price = _to_decimal(request.price_evidence.get("reference_price"))
    reference_price_timestamp = _to_datetime(request.price_evidence.get("observed_at"))
    entry_fee = readiness.estimated_entry_fee
    exit_fee = readiness.estimated_future_exit_fee
    slippage = readiness.estimated_slippage

    total_costs = None
    if entry_fee is not None and slippage is not None:
        total_costs = entry_fee + slippage + (exit_fee or Decimal("0"))

    semantic_payload = {
        "campaign_id": str(request.campaign_id),
        "version": request.version,
        "provider": request.provider,
        "environment": request.environment,
        "instrument": _normalize_instrument(request.instrument),
        "proposed_quote_amount": str(request.requested_quote_amount),
        "expected_entry_quantity": None if readiness.expected_entry_quantity is None else str(readiness.expected_entry_quantity),
        "reference_price": None if reference_price is None else str(reference_price),
        "reference_price_timestamp": None if reference_price_timestamp is None else reference_price_timestamp.isoformat(),
        "estimated_entry_fee": None if entry_fee is None else str(entry_fee),
        "estimated_future_exit_fee": None if exit_fee is None else str(exit_fee),
        "estimated_slippage": None if slippage is None else str(slippage),
        "total_round_trip_costs": None if total_costs is None else str(total_costs),
        "capital_cap": None if readiness.applicable_capital_cap is None else str(readiness.applicable_capital_cap),
        "mandate_id": None if request.mandate_id is None else str(request.mandate_id),
        "mandate_version_id": None if request.mandate_version_id is None else str(request.mandate_version_id),
        "risk_policy_id": request.expected_risk_policy_id,
        "risk_policy_version": request.expected_risk_policy_version,
        "readiness_verdict": readiness.readiness_verdict,
        "blockers": readiness.blockers,
        "warnings": readiness.warnings,
        "stale_after": None if readiness.stale_after is None else readiness.stale_after.isoformat(),
    }

    return CommissionedPreviewResponse(
        campaign_id=request.campaign_id,
        version=request.version,
        authority_classification=readiness.authority_classification,
        strategy_signal_classification=readiness.strategy_signal_classification,
        execution_venue={
            "provider": request.provider,
            "environment": request.environment,
        },
        instrument=_normalize_instrument(request.instrument),
        proposed_quote_amount=request.requested_quote_amount,
        estimated_base_quantity=readiness.expected_entry_quantity,
        reference_price=reference_price,
        reference_price_timestamp=reference_price_timestamp,
        estimated_entry_fee=entry_fee,
        estimated_future_exit_fee=exit_fee,
        estimated_slippage=slippage,
        total_estimated_round_trip_costs=total_costs,
        applicable_capital_cap=readiness.applicable_capital_cap,
        mandate_identity={
            "mandate_id": None if request.mandate_id is None else str(request.mandate_id),
            "mandate_version_id": None if request.mandate_version_id is None else str(request.mandate_version_id),
            "expected_mandate_version_number": request.expected_mandate_version_number,
        },
        risk_policy_identity={
            "risk_policy_id": request.expected_risk_policy_id,
            "risk_policy_version": request.expected_risk_policy_version,
        },
        readiness_verdict=readiness.readiness_verdict,
        blockers=readiness.blockers,
        warnings=readiness.warnings,
        evidence_timestamps=readiness.evidence_timestamps,
        evidence_provenance=readiness.evidence_provenance,
        preview_identity_hash=_preview_hash(semantic_payload),
        stale_after=readiness.stale_after,
        no_database_writes=True,
        no_order_submission=True,
        no_position_creation=True,
    )
