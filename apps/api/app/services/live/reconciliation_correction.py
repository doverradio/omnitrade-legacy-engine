from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.exchange_connections.providers.registry import (
    get_exchange_provider,
    require_provider_capabilities,
)
from app.services.live.accounting_reconciliation import (
    ensure_execution_source,
    record_live_order_reconciliation,
    resolve_campaign_for_live_order,
)
from app.services.live.contracts import LiveOrderReconciliationRequest
from app.services.live.position_quantity import QUANTITY_BEARING_RECORD_TYPES

# The confirmed production defect this correction exists for: Kraken's raw
# "vol" field is quote-currency (USD) for a quote-sized ("viqc") market BUY,
# not base-currency. reconcile_live_order_and_fills compared that
# quote-currency figure against base-currency fill sizes and could never
# find them equal, permanently writing every fill of a genuinely fully
# filled viqc BUY as "partial_fill_accounting" / "partially_filled". That
# fill-level event outranks the order's own correctly-derived "filled"
# event as the latest reconciliation record, which is exactly what
# has_unresolved_reconciliation (and therefore the worker's SELL gate)
# reads. app.services.live.accounting_reconciliation.reconcile_live_order_
# and_fills was already fixed so this can never happen again going forward
# -- this module exists solely to correct the small set of orders that were
# already affected before that fix, via one new, truthful, append-only
# event. It never rewrites or deletes the historical row.
_AFFECTED_PROVIDER = "kraken_spot"
_STALE_STATUS = "partially_filled"
_TERMINAL_STATUS = "filled"
_CORRECTION_REASON = "stale_viqc_classification_corrected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _correction_idempotency_key(live_crypto_order_id: uuid.UUID) -> str:
    # Deliberately has no date component (unlike the live regression-block
    # guard's key): this is a one-time historical correction for a bug that
    # can no longer occur going forward, not a recurring daily block, so the
    # same order must never receive a second corrective event, ever.
    return f"lco-reconcile:{live_crypto_order_id}:stale-viqc-correction"


@dataclass(frozen=True, slots=True)
class StaleViqcReconciliationCorrectionOutcome:
    """Result of one eligibility check (dry_run=True) or applied correction
    for the confirmed stale-viqc-classification bug on one specific live
    crypto order. Nothing is mutated unless applied is True."""

    eligible: bool
    applied: bool
    already_applied: bool
    blocked_reason: str | None
    live_crypto_order_id: uuid.UUID
    provider_order_id: str | None
    prior_effective_status: str | None
    provider_confirmed_status: str | None
    reconciliation_event_id: uuid.UUID | None
    idempotency_key: str
    checked_at: datetime


def _blocked(
    *,
    live_crypto_order_id: uuid.UUID,
    reason: str,
    provider_order_id: str | None = None,
    prior_effective_status: str | None = None,
    provider_confirmed_status: str | None = None,
) -> StaleViqcReconciliationCorrectionOutcome:
    return StaleViqcReconciliationCorrectionOutcome(
        eligible=False,
        applied=False,
        already_applied=False,
        blocked_reason=reason,
        live_crypto_order_id=live_crypto_order_id,
        provider_order_id=provider_order_id,
        prior_effective_status=prior_effective_status,
        provider_confirmed_status=provider_confirmed_status,
        reconciliation_event_id=None,
        idempotency_key=_correction_idempotency_key(live_crypto_order_id),
        checked_at=_utcnow(),
    )


async def correct_stale_viqc_reconciliation(
    *,
    db: AsyncSession,
    live_crypto_order_id: uuid.UUID,
    operator_identity: str,
    dry_run: bool = False,
) -> StaleViqcReconciliationCorrectionOutcome:
    """Append-only correction for the confirmed stale-viqc-classification
    bug on one live crypto order. Fails closed unless every precondition is
    verified fresh; appends exactly one new terminal LiveReconciliationEvent
    when applied; is idempotent forever on repeat calls for the same order
    (a second call always replays the first successful outcome and never
    writes a second corrective event). dry_run=True runs every verification
    -- including the live provider lookup -- but never appends anything."""
    idempotency_key = _correction_idempotency_key(live_crypto_order_id)

    live_order = await db.scalar(
        select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id).limit(1)
    )
    if live_order is None:
        return _blocked(live_crypto_order_id=live_crypto_order_id, reason="live_order_not_found")

    existing_correction = await db.scalar(
        select(LiveReconciliationEvent).where(LiveReconciliationEvent.idempotency_key == idempotency_key).limit(1)
    )
    if existing_correction is not None:
        provenance = existing_correction.provenance or {}
        return StaleViqcReconciliationCorrectionOutcome(
            eligible=True,
            applied=True,
            already_applied=True,
            blocked_reason=None,
            live_crypto_order_id=live_crypto_order_id,
            provider_order_id=live_order.provider_order_id,
            prior_effective_status=provenance.get("prior_effective_status", _STALE_STATUS),
            provider_confirmed_status=provenance.get("provider_confirmed_status"),
            reconciliation_event_id=existing_correction.id,
            idempotency_key=idempotency_key,
            checked_at=_utcnow(),
        )

    if live_order.side.upper() != "BUY":
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="not_a_buy_order",
            provider_order_id=live_order.provider_order_id,
        )
    if live_order.provider != _AFFECTED_PROVIDER:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="not_kraken_provider",
            provider_order_id=live_order.provider_order_id,
        )
    if not live_order.provider_order_id:
        return _blocked(live_crypto_order_id=live_crypto_order_id, reason="provider_order_id_missing")

    claim = await db.scalar(
        select(AutonomousExecutionClaim).where(AutonomousExecutionClaim.live_order_id == live_crypto_order_id).limit(1)
    )
    package = None if claim is None else await db.get(CanonicalPreviewPackage, claim.package_id)
    proof = (
        None if package is None or package.side != "BUY"
        else await db.scalar(select(ControlledProofRun).where(ControlledProofRun.package_id == package.package_id).limit(1))
    )
    if proof is None:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="controlled_proof_lineage_not_found",
            provider_order_id=live_order.provider_order_id,
        )
    if proof.sell_package_id is not None:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="sell_already_proposed",
            provider_order_id=live_order.provider_order_id,
        )

    latest_event = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.live_crypto_order_id == live_crypto_order_id)
        .order_by(LiveReconciliationEvent.sequence_number.desc(), LiveReconciliationEvent.created_at.desc())
        .limit(1)
    )
    if latest_event is None or latest_event.reconciliation_status != _STALE_STATUS:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="latest_reconciliation_not_stale_partial",
            provider_order_id=live_order.provider_order_id,
            prior_effective_status=None if latest_event is None else latest_event.reconciliation_status,
        )

    if live_order.status != "FILLED":
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="order_status_not_filled",
            provider_order_id=live_order.provider_order_id, prior_effective_status=latest_event.reconciliation_status,
        )

    ledger_quantity_raw = await db.scalar(
        select(func.coalesce(func.sum(LiveAccountingRecord.filled_quantity), 0)).where(
            LiveAccountingRecord.live_crypto_order_id == live_crypto_order_id,
            LiveAccountingRecord.side == "buy",
            LiveAccountingRecord.record_type.in_(QUANTITY_BEARING_RECORD_TYPES),
        )
    )
    ledger_quantity = Decimal(str(ledger_quantity_raw or "0"))
    if ledger_quantity <= Decimal("0"):
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="no_positive_ledger_quantity",
            provider_order_id=live_order.provider_order_id, prior_effective_status=latest_event.reconciliation_status,
        )

    # Local import: live_crypto_orders -> ... -> this package would be
    # circular at module load time (same reason accounting_reconciliation.
    # reconcile_live_order_and_fills imports these locally too).
    from app.services.live_crypto_orders import _load_decrypted_credentials, _load_exchange_connection

    connection = await _load_exchange_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
    credentials = _load_decrypted_credentials(connection)
    require_provider_capabilities(
        provider=live_order.provider,
        operation="correct_stale_viqc_reconciliation",
        required=("order_lookup_history",),
        environment=live_order.environment,
    )
    provider = get_exchange_provider(live_order.provider, environment=live_order.environment)
    provider_order = await provider.lookup_order(
        credentials=credentials,
        environment=live_order.environment,
        provider_order_id=live_order.provider_order_id,
        client_order_id=live_order.client_order_id,
        product_id=live_order.product_id,
    )
    if provider_order is None or provider_order.provider_order_id != live_order.provider_order_id:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="provider_order_not_found_or_mismatched",
            provider_order_id=live_order.provider_order_id, prior_effective_status=latest_event.reconciliation_status,
        )
    if provider_order.status != "FILLED":
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="provider_order_not_terminal_filled",
            provider_order_id=live_order.provider_order_id, prior_effective_status=latest_event.reconciliation_status,
            provider_confirmed_status=provider_order.status,
        )
    oflags = str((provider_order.raw or {}).get("oflags") or "").split(",")
    if "viqc" not in oflags:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="not_viqc_shape",
            provider_order_id=live_order.provider_order_id, prior_effective_status=latest_event.reconciliation_status,
            provider_confirmed_status=provider_order.status,
        )

    if dry_run:
        return StaleViqcReconciliationCorrectionOutcome(
            eligible=True,
            applied=False,
            already_applied=False,
            blocked_reason=None,
            live_crypto_order_id=live_crypto_order_id,
            provider_order_id=live_order.provider_order_id,
            prior_effective_status=latest_event.reconciliation_status,
            provider_confirmed_status=provider_order.status,
            reconciliation_event_id=None,
            idempotency_key=idempotency_key,
            checked_at=_utcnow(),
        )

    profile_id_raw = (live_order.safe_provider_response or {}).get("live_trading_profile_id")
    profile_id: uuid.UUID | None = None
    if profile_id_raw is not None:
        try:
            profile_id = uuid.UUID(str(profile_id_raw))
        except ValueError:
            profile_id = None
    profile = None
    if profile_id is not None:
        profile = await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == profile_id).limit(1))
    if profile is None:
        profile = await db.scalar(select(LiveTradingProfile).limit(1))
    if profile is None:
        return _blocked(
            live_crypto_order_id=live_crypto_order_id, reason="live_trading_profile_not_found",
            provider_order_id=live_order.provider_order_id, prior_effective_status=latest_event.reconciliation_status,
            provider_confirmed_status=provider_order.status,
        )

    source_event = await ensure_execution_source(db=db, live_order=live_order, profile=profile)
    campaign, _campaign_status = await resolve_campaign_for_live_order(db=db, live_order=live_order, profile=profile)

    prior_effective_status = latest_event.reconciliation_status
    corrected_at = _utcnow()
    result = await record_live_order_reconciliation(
        db=db,
        request=LiveOrderReconciliationRequest(
            live_trading_profile_id=profile.id,
            source_execution_event_id=source_event.id,
            provider_name=live_order.provider,
            provider_order_id=live_order.provider_order_id,
            client_order_id=live_order.client_order_id,
            reconciliation_status=_TERMINAL_STATUS,
            live_crypto_order_id=live_crypto_order_id,
            capital_campaign_id=None if campaign is None else campaign.id,
            provider_recorded_at=provider_order.submitted_at,
            requested_by=operator_identity,
            provenance_metadata={
                "reason": _CORRECTION_REASON,
                "original_reconciliation_event_id": str(latest_event.id),
                "provider_order_id": live_order.provider_order_id,
                "live_crypto_order_id": str(live_crypto_order_id),
                "prior_effective_status": prior_effective_status,
                "provider_confirmed_status": provider_order.status,
                "operator_identity": operator_identity,
                "corrected_at": corrected_at.isoformat(),
            },
            idempotency_key=idempotency_key,
        ),
    )

    db.add(AuditLog(
        actor=operator_identity,
        action="live_crypto_order.stale_viqc_reconciliation_corrected",
        entity_type="live_crypto_order",
        entity_id=live_crypto_order_id,
        before_state={
            "reconciliation_status": prior_effective_status,
            "reconciliation_event_id": str(latest_event.id),
        },
        after_state={
            "reconciliation_status": _TERMINAL_STATUS,
            "reconciliation_event_id": str(result.reconciliation_event_id),
            "provider_confirmed_status": provider_order.status,
            "reason": _CORRECTION_REASON,
        },
    ))
    await db.commit()

    return StaleViqcReconciliationCorrectionOutcome(
        eligible=True,
        applied=True,
        already_applied=False,
        blocked_reason=None,
        live_crypto_order_id=live_crypto_order_id,
        provider_order_id=live_order.provider_order_id,
        prior_effective_status=prior_effective_status,
        provider_confirmed_status=provider_order.status,
        reconciliation_event_id=result.reconciliation_event_id,
        idempotency_key=idempotency_key,
        checked_at=corrected_at,
    )
