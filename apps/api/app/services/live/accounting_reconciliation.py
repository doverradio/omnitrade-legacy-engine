from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.capital_campaign import CapitalCampaign
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_execution_event import LiveExecutionEvent
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.exchange_connections.providers.registry import (
    get_exchange_provider,
    require_provider_capabilities,
)
from app.services.live.audit_compliance import record_live_audit_evidence
from app.services.live.contracts import (
    LiveAuditEvidenceRequest,
    LiveFillReconciliationRequest,
    LiveOrderReconciliationRequest,
    LiveReconciliationResult,
)


def build_live_reconciliation_idempotency_key(
    *,
    live_trading_profile_id: uuid.UUID,
    source_execution_event_id: uuid.UUID,
    provider_name: str,
    provider_order_id: str,
    provider_fill_id: str | None,
    event_type: str,
) -> str:
    payload = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "source_execution_event_id": str(source_execution_event_id),
            "provider_name": provider_name,
            "provider_order_id": provider_order_id,
            "provider_fill_id": provider_fill_id,
            "event_type": event_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_RECONCILIATION_TERMINAL_STATUSES = {"filled", "canceled", "rejected"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _extract_provider_timestamp(*, payload: dict[str, object]) -> datetime | None:
    for key in ("completion_time", "last_fill_time", "created_time", "created_at"):
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, str):
            text = raw.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                continue
    return None


def _normalize_provider_status(*, provider_status: str | None) -> str:
    if provider_status is None:
        return "unknown"
    status = provider_status.upper()
    if status in {"OPEN", "PENDING", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED"}:
        return "open"
    if status in {"FILLED", "CLOSED"}:
        return "filled"
    if status in {"PARTIALLY_FILLED", "PARTIAL"}:
        return "partially_filled"
    if status in {"CANCELLED", "CANCELED", "EXPIRED"}:
        return "canceled"
    if status in {"FAILED", "REJECTED"}:
        return "rejected"
    return "unknown"


def _safe_json(value: dict[str, object]) -> dict[str, object]:
    return json.loads(json.dumps(value, default=str))


def _safe_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _safe_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _event_is_stale(*, now: datetime, observed_at: datetime | None, max_age_seconds: int) -> bool:
    if observed_at is None:
        return True
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return (now - observed_at).total_seconds() > max_age_seconds


async def _resolve_campaign_for_live_order(
    *,
    db: AsyncSession,
    live_order: LiveCryptoOrder,
    profile: LiveTradingProfile,
) -> tuple[CapitalCampaign | None, str]:
    row_campaign_ids = set(
        await db.scalars(
            select(LiveAccountingRecord.capital_campaign_id)
            .where(LiveAccountingRecord.live_crypto_order_id == live_order.live_crypto_order_id)
            .where(LiveAccountingRecord.capital_campaign_id.is_not(None))
        )
    )
    row_campaign_ids.update(
        set(
            await db.scalars(
                select(LiveReconciliationEvent.capital_campaign_id)
                .where(LiveReconciliationEvent.live_crypto_order_id == live_order.live_crypto_order_id)
                .where(LiveReconciliationEvent.capital_campaign_id.is_not(None))
            )
        )
    )

    typed_campaign_id = _safe_int((live_order.safe_provider_response or {}).get("capital_campaign_id"))
    if typed_campaign_id is not None:
        row_campaign_ids.add(typed_campaign_id)

    if len(row_campaign_ids) > 1:
        return None, "mismatch"
    if not row_campaign_ids:
        return None, "uncategorized"

    campaign_id = next(iter(row_campaign_ids))
    campaign = await db.scalar(select(CapitalCampaign).where(CapitalCampaign.id == campaign_id).limit(1))
    if campaign is None:
        return None, "mismatch"

    profile_paper_account_id = getattr(profile, "paper_account_id", None)
    if campaign.paper_account_id is not None and profile_paper_account_id is not None and campaign.paper_account_id != profile_paper_account_id:
        return None, "mismatch"
    return campaign, "verified"


async def _ensure_execution_source(
    *,
    db: AsyncSession,
    live_order: LiveCryptoOrder,
    profile: LiveTradingProfile,
) -> LiveExecutionEvent:
    existing = await db.scalar(
        select(LiveExecutionEvent)
        .where(LiveExecutionEvent.idempotency_key == f"live_order_source:{live_order.live_crypto_order_id}")
        .limit(1)
    )
    if existing is not None:
        return existing

    latest_sequence = await db.scalar(
        select(func.max(LiveExecutionEvent.sequence_number)).where(LiveExecutionEvent.live_trading_profile_id == profile.id)
    )
    sequence_number = int(latest_sequence or 0) + 1
    recorded_at = datetime.now(timezone.utc)
    payload = {
        "live_crypto_order_id": str(live_order.live_crypto_order_id),
        "client_order_id": live_order.client_order_id,
        "product_id": live_order.product_id,
        "side": live_order.side,
        "order_type": live_order.order_type,
        "requested_quote_size": format(_decimal(live_order.requested_quote_size), "f"),
    }
    event_hash = hashlib.sha256(
        json.dumps(
            {
                "live_trading_profile_id": str(profile.id),
                "sequence_number": sequence_number,
                "event_type": "execution_intent_created",
                "provider_name": live_order.provider,
                "idempotency_key": f"live_order_source:{live_order.live_crypto_order_id}",
                "recorded_at": recorded_at.isoformat(),
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    event = LiveExecutionEvent(
        idempotency_key=f"live_order_source:{live_order.live_crypto_order_id}",
        event_hash=event_hash,
        live_trading_profile_id=profile.id,
        sequence_number=sequence_number,
        event_type="execution_intent_created",
        provider_name=live_order.provider,
        risk_decision_id=live_order.risk_event_id or uuid.uuid4(),
        approval_event_id=uuid.UUID(str(live_order.safe_provider_response.get("approval_event_id"))) if live_order.safe_provider_response.get("approval_event_id") else uuid.uuid4(),
        audit_correlation_id=str(live_order.audit_correlation_id),
        operating_mode=getattr(profile, "operating_mode", "live"),
        paper_default_mode=bool(getattr(profile, "paper_default_mode", True)),
        risk_authority_model=getattr(profile, "risk_authority_model", "risk_engine_final"),
        event_payload=payload,
        provenance={"generated_by": "live_reconciliation", "recorded_at": recorded_at.isoformat()},
        immutable_contract_version="v1",
        recorded_at=recorded_at,
    )
    db.add(event)
    await db.flush()
    return event


async def reconcile_live_order_and_fills(
    *,
    db: AsyncSession,
    live_crypto_order_id: uuid.UUID,
    operator_identity: str,
) -> dict[str, object]:
    live_order = await db.scalar(
        select(LiveCryptoOrder)
        .where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id)
        .with_for_update()
        .limit(1)
    )
    if live_order is None:
        raise LookupError("live crypto order not found")

    profile_id_raw = (live_order.safe_provider_response or {}).get("live_trading_profile_id")
    profile_id: uuid.UUID | None = None
    if profile_id_raw is not None:
        try:
            profile_id = uuid.UUID(str(profile_id_raw))
        except ValueError:
            profile_id = None

    profile = None
    if profile_id is not None:
        profile = await db.scalar(
            select(LiveTradingProfile)
            .where(LiveTradingProfile.id == profile_id)
            .limit(1)
        )
    if profile is None:
        profile = await db.scalar(select(LiveTradingProfile).limit(1))
    if profile is None:
        raise LookupError("live trading profile not found")

    source_event = await _ensure_execution_source(db=db, live_order=live_order, profile=profile)
    campaign, campaign_correlation_status = await _resolve_campaign_for_live_order(
        db=db,
        live_order=live_order,
        profile=profile,
    )

    from app.services.live_crypto_orders import _load_exchange_connection, _load_decrypted_credentials

    connection = await _load_exchange_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
    credentials = _load_decrypted_credentials(connection)
    require_provider_capabilities(
        provider=live_order.provider,
        operation="reconcile_live_order",
        required=("order_lookup_history", "fill_lookup"),
        environment=live_order.environment,
    )
    provider = get_exchange_provider(live_order.provider, environment=live_order.environment)
    stored_provider_client_order_id = (live_order.safe_provider_response or {}).get("provider_client_order_id")
    provider_client_order_id = (
        stored_provider_client_order_id
        if isinstance(stored_provider_client_order_id, str) and stored_provider_client_order_id
        else live_order.client_order_id
    )

    if hasattr(provider, "lookup_order"):
        provider_order = await provider.lookup_order(
            credentials=credentials,
            environment=live_order.environment,
            provider_order_id=live_order.provider_order_id,
            client_order_id=provider_client_order_id,
            product_id=live_order.product_id,
        )
    else:
        payload: dict[str, object] | None = None
        if live_order.provider_order_id:
            order_payload, _headers = await provider.get_historical_order(
                credentials=credentials,
                environment=live_order.environment,
                order_id=live_order.provider_order_id,
                client_order_id=provider_client_order_id,
            )
            payload = order_payload.get("order") if isinstance(order_payload.get("order"), dict) else None
        else:
            list_payload, _headers = await provider.list_historical_orders(
                credentials=credentials,
                environment=live_order.environment,
                product_ids=[live_order.product_id],
                order_status=["PENDING", "OPEN", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED", "FILLED", "FAILED", "CANCELLED", "EXPIRED"],
            )
            rows = list_payload.get("orders") if isinstance(list_payload.get("orders"), list) else []
            for item in rows:
                if not isinstance(item, dict):
                    continue
                if str(item.get("client_order_id", "")) != provider_client_order_id:
                    continue
                if str(item.get("product_id", "")) != live_order.product_id:
                    continue
                payload = item
                break
        provider_order = None
        if isinstance(payload, dict):
            from app.services.exchange_connections.providers.base import ExchangeProviderOrder

            provider_order = ExchangeProviderOrder(
                provider_order_id=payload.get("order_id") if isinstance(payload.get("order_id"), str) else live_order.provider_order_id,
                client_order_id=payload.get("client_order_id") if isinstance(payload.get("client_order_id"), str) else provider_client_order_id,
                product_id=payload.get("product_id") if isinstance(payload.get("product_id"), str) else live_order.product_id,
                side=payload.get("side") if isinstance(payload.get("side"), str) else None,
                status=payload.get("status") if isinstance(payload.get("status"), str) else None,
                submitted_at=_extract_provider_timestamp(payload=payload),
                acknowledged_at=_extract_provider_timestamp(payload=payload),
                raw=payload,
            )
    if provider_order is None:
        await record_live_order_reconciliation(
            db=db,
            request=LiveOrderReconciliationRequest(
                live_trading_profile_id=profile.id,
                source_execution_event_id=source_event.id,
                provider_name=live_order.provider,
                provider_order_id=None,
                client_order_id=live_order.client_order_id,
                reconciliation_status="reconciliation_required",
                live_crypto_order_id=live_order.live_crypto_order_id,
                capital_campaign_id=None if campaign is None else campaign.id,
                provider_recorded_at=None,
                requested_by=operator_identity,
                provenance_metadata={"reason": "provider_order_not_found"},
                idempotency_key=f"lco-reconcile:{live_order.live_crypto_order_id}:missing",
            ),
        )
        live_order.status = "RECONCILIATION_REQUIRED"
        await db.flush()
        return {"reconciliation_status": live_order.status, "provider_status": live_order.provider_status, "provider_order_id": live_order.provider_order_id, "provider_fill_observed": False, "safe_provider_response": {"reason": "provider_order_not_found"}}

    discovered_provider_order_id = provider_order.provider_order_id
    if discovered_provider_order_id and live_order.provider_order_id and live_order.provider_order_id != discovered_provider_order_id:
        await record_live_order_reconciliation(
            db=db,
            request=LiveOrderReconciliationRequest(
                live_trading_profile_id=profile.id,
                source_execution_event_id=source_event.id,
                provider_name=live_order.provider,
                provider_order_id=discovered_provider_order_id,
                client_order_id=live_order.client_order_id,
                reconciliation_status="conflict",
                live_crypto_order_id=live_order.live_crypto_order_id,
                capital_campaign_id=None if campaign is None else campaign.id,
                provider_recorded_at=provider_order.submitted_at,
                requested_by=operator_identity,
                provenance_metadata={"reason": "provider_order_id_conflict", "existing": live_order.provider_order_id, "new": discovered_provider_order_id},
                idempotency_key=f"lco-reconcile:{live_order.live_crypto_order_id}:provider-id-conflict",
            ),
        )
        live_order.status = "RECONCILIATION_REQUIRED"
        live_order.failure_code = "provider_order_id_conflict"
        live_order.failure_reason = json.dumps({"existing": live_order.provider_order_id, "new": discovered_provider_order_id})
        await db.flush()
        return {"reconciliation_status": live_order.status, "provider_status": live_order.provider_status, "provider_order_id": live_order.provider_order_id, "provider_fill_observed": False, "safe_provider_response": _safe_json(provider_order.raw)}

    live_order.provider_order_id = discovered_provider_order_id or live_order.provider_order_id

    provider_status_raw = provider_order.status
    normalized_status = _normalize_provider_status(provider_status=provider_status_raw)
    provider_recorded_at = provider_order.submitted_at

    order_reconciliation = await record_live_order_reconciliation(
        db=db,
        request=LiveOrderReconciliationRequest(
            live_trading_profile_id=profile.id,
            source_execution_event_id=source_event.id,
            provider_name=live_order.provider,
            provider_order_id=live_order.provider_order_id,
            client_order_id=live_order.client_order_id,
            reconciliation_status=normalized_status,
            live_crypto_order_id=live_order.live_crypto_order_id,
            capital_campaign_id=None if campaign is None else campaign.id,
            provider_recorded_at=provider_recorded_at,
            requested_by=operator_identity,
            provenance_metadata={"provider_status": provider_status_raw, "source": "provider_contract_lookup"},
            idempotency_key=f"lco-reconcile:{live_order.live_crypto_order_id}:status:{normalized_status}:{provider_status_raw or 'none'}",
        ),
    )

    fill_count = 0
    fill_events: list[dict[str, object]] = []
    if live_order.provider_order_id:
        if hasattr(provider, "list_fills"):
            fills = await provider.list_fills(
                credentials=credentials,
                environment=live_order.environment,
                provider_order_id=live_order.provider_order_id,
            )
        else:
            fills_payload, _fill_headers = await provider.list_historical_fills(
                credentials=credentials,
                environment=live_order.environment,
                order_id=live_order.provider_order_id,
            )
            rows = fills_payload.get("fills") if isinstance(fills_payload.get("fills"), list) else []
            fills = []
            from app.services.exchange_connections.providers.base import ExchangeProviderFee, ExchangeProviderFill

            for item in rows:
                if not isinstance(item, dict):
                    continue
                size = _decimal(item.get("size", "0"))
                price = _decimal(item.get("price", "0"))
                if size <= Decimal("0") or price <= Decimal("0"):
                    continue
                fee_amount = _decimal(item.get("commission", "0")) if item.get("commission") is not None else None
                fee = None if fee_amount is None else ExchangeProviderFee(amount=fee_amount, currency=str(item.get("commission_currency") or "USD"))
                fills.append(
                    ExchangeProviderFill(
                        provider_fill_id=item.get("trade_id") if isinstance(item.get("trade_id"), str) else None,
                        provider_order_id=item.get("order_id") if isinstance(item.get("order_id"), str) else live_order.provider_order_id,
                        product_id=item.get("product_id") if isinstance(item.get("product_id"), str) else live_order.product_id,
                        size=size,
                        price=price,
                        fee=fee,
                        occurred_at=_extract_provider_timestamp(payload=item),
                        raw=item,
                    )
                )
        for index, fill in enumerate(fills):
            provider_fill_id = fill.provider_fill_id
            if provider_fill_id is None:
                continue
            size = _decimal(fill.size)
            if size <= Decimal("0"):
                continue
            price = _decimal(fill.price)
            fee = _decimal("0") if fill.fee is None else _decimal(fill.fee.amount)
            fee_currency = "USD" if fill.fee is None else str(fill.fee.currency)
            cumulative = size
            fill_time = fill.occurred_at

            result = await record_live_fill_reconciliation(
                db=db,
                request=LiveFillReconciliationRequest(
                    live_trading_profile_id=profile.id,
                    source_execution_event_id=source_event.id,
                    provider_name=live_order.provider,
                    provider_order_id=live_order.provider_order_id,
                    provider_fill_id=provider_fill_id,
                    client_order_id=live_order.client_order_id,
                    symbol=live_order.product_id,
                    side=live_order.side.lower(),
                    fill_quantity=format(size, "f"),
                    cumulative_filled_quantity=format(cumulative, "f"),
                    order_quantity=format(live_order.requested_quote_size, "f"),
                    fill_price=format(price, "f"),
                    fee_amount=format(fee, "f"),
                    fee_currency=fee_currency,
                    live_crypto_order_id=live_order.live_crypto_order_id,
                    capital_campaign_id=None if campaign is None else campaign.id,
                    provider_fill_timestamp=fill_time,
                    provider_recorded_at=fill_time,
                    requested_by=operator_identity,
                    provenance_metadata={"fill_index": index},
                    idempotency_key=f"lco-reconcile:{live_order.live_crypto_order_id}:fill:{provider_fill_id}",
                ),
            )
            fill_events.append({"fill_id": provider_fill_id, "status": result.status})
            fill_count += 1

    accounting_rows = list(
        await db.scalars(
            select(LiveAccountingRecord)
            .where(LiveAccountingRecord.live_crypto_order_id == live_order.live_crypto_order_id)
            .order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.created_at.asc())
        )
    )
    fill_rows = [row for row in accounting_rows if row.record_type in {"fill_accounting", "partial_fill_accounting"}]
    total_filled_quantity = sum((_decimal(row.filled_quantity) for row in fill_rows), Decimal("0"))
    total_quote_notional = sum((_decimal(row.gross_notional) for row in fill_rows), Decimal("0"))
    total_fees_by_currency: dict[str, Decimal] = {}
    for row in accounting_rows:
        total_fees_by_currency[row.fee_currency] = total_fees_by_currency.get(row.fee_currency, Decimal("0")) + _decimal(row.fee_amount)

    weighted_average_fill_price: Decimal | None = None
    if total_filled_quantity > Decimal("0"):
        weighted_average_fill_price = total_quote_notional / total_filled_quantity

    quote_currency = "USD"
    settings = get_settings()
    balance_tolerance = _decimal(settings.live_crypto_accounting_balance_tolerance_usd)
    expected_quote_reduction = total_quote_notional + total_fees_by_currency.get(quote_currency, Decimal("0"))

    pre_balance = None
    for key in ("usd_available_before_submit", "usd_balance_before_submit", "usd_balance_before"):
        raw = (live_order.safe_provider_response or {}).get(key)
        if raw is not None:
            pre_balance = _decimal(raw)
            break

    post_balance = None
    for item in connection.balances or []:
        if str(item.get("currency", "")).upper() == quote_currency:
            post_balance = _decimal(item.get("available", "0"))
            break

    now = _utcnow()
    balance_observed_at = (
        _safe_datetime(getattr(connection, "last_verified_at", None))
        or _safe_datetime(getattr(connection, "last_successful_sync_at", None))
        or _safe_datetime(getattr(connection, "last_heartbeat_at", None))
    )
    is_stale_balance = _event_is_stale(
        now=now,
        observed_at=balance_observed_at,
        max_age_seconds=int(settings.live_crypto_balance_max_age_seconds),
    )

    balance_mismatch_state = "ok"
    if expected_quote_reduction <= Decimal("0"):
        balance_mismatch_state = "not_required"
    elif post_balance is None:
        balance_mismatch_state = "missing"
    elif is_stale_balance:
        balance_mismatch_state = "stale"
    elif pre_balance is None:
        balance_mismatch_state = "missing"
    else:
        observed_reduction = pre_balance - post_balance
        delta = abs(observed_reduction - expected_quote_reduction)
        if delta > balance_tolerance:
            balance_mismatch_state = "material_mismatch"
        elif delta > Decimal("0"):
            balance_mismatch_state = "tolerated"

    balance_status = "ok" if balance_mismatch_state in {"ok", "tolerated", "not_required"} else "mismatch"

    preview_fee = _decimal(live_order.safe_provider_response.get("preview_estimated_fee", "0")) if live_order.safe_provider_response.get("preview_estimated_fee") is not None else Decimal("0")
    provider_fee_total = total_fees_by_currency.get("USD", Decimal("0"))
    fee_delta = provider_fee_total - preview_fee

    live_order.provider_status = provider_status_raw
    if normalized_status in {"filled", "canceled", "rejected"}:
        if normalized_status == "filled" and total_filled_quantity > Decimal("0"):
            live_order.status = "FILLED"
            live_order.filled_at = live_order.filled_at or _utcnow()
        elif normalized_status == "canceled" and total_filled_quantity > Decimal("0"):
            live_order.status = "PARTIALLY_FILLED"
            live_order.cancelled_at = live_order.cancelled_at or _utcnow()
        elif normalized_status == "canceled":
            live_order.status = "CANCELLED"
            live_order.cancelled_at = live_order.cancelled_at or _utcnow()
        else:
            live_order.status = "REJECTED"
    elif normalized_status == "unknown":
        live_order.status = "UNKNOWN"
    else:
        live_order.status = "ACKNOWLEDGED"

    if balance_mismatch_state in {"missing", "stale", "material_mismatch"}:
        live_order.status = "RECONCILIATION_REQUIRED"
        mismatch_status = "balance_mismatch" if balance_mismatch_state == "material_mismatch" else "reconciliation_required"
        await record_live_order_reconciliation(
            db=db,
            request=LiveOrderReconciliationRequest(
                live_trading_profile_id=profile.id,
                source_execution_event_id=source_event.id,
                provider_name=live_order.provider,
                provider_order_id=live_order.provider_order_id,
                client_order_id=live_order.client_order_id,
                reconciliation_status=mismatch_status,
                live_crypto_order_id=live_order.live_crypto_order_id,
                capital_campaign_id=None if campaign is None else campaign.id,
                provider_recorded_at=None,
                requested_by=operator_identity,
                provenance_metadata={
                    "reason": "balance_evidence_unresolved",
                    "balance_mismatch_state": balance_mismatch_state,
                },
                idempotency_key=f"lco-reconcile:{live_order.live_crypto_order_id}:balance-unresolved:{balance_mismatch_state}",
            ),
        )

    accounting_projection_status = "projected" if total_filled_quantity > Decimal("0") else "not_projected"
    accounting_complete = (
        normalized_status in _RECONCILIATION_TERMINAL_STATUSES
        and campaign_correlation_status != "mismatch"
        and balance_mismatch_state not in {"missing", "stale", "material_mismatch"}
    )
    accounting_completion_status = "complete" if accounting_complete else "unresolved"

    if campaign_correlation_status == "mismatch":
        live_order.status = "RECONCILIATION_REQUIRED"

    audit_evidence_written = True
    try:
        await record_live_audit_evidence(
            db=db,
            request=LiveAuditEvidenceRequest(
                live_trading_profile_id=profile.id,
                event_type="order_lifecycle_evidence",
                attributable_actor_id=operator_identity,
                attributable_actor_role="operator",
                action_name="CAPITAL_LEDGER_PROJECTION_GENERATED",
                action_source="live_reconciliation",
                action_summary="Projected live accounting evidence for capital ledger read model.",
                evidence_payload={
                    "live_crypto_order_id": str(live_order.live_crypto_order_id),
                    "provider_order_id": live_order.provider_order_id,
                    "client_order_id": live_order.client_order_id,
                    "filled_quantity": format(total_filled_quantity, "f"),
                    "gross_filled_notional": format(total_quote_notional, "f"),
                    "provider_fee_usd": format(provider_fee_total, "f"),
                    "net_quote_capital_effect": format(expected_quote_reduction, "f"),
                    "campaign_correlation_status": campaign_correlation_status,
                    "accounting_projection_status": accounting_projection_status,
                },
                provenance_metadata={"phase": "10.6C"},
                live_execution_event_id=source_event.id,
                live_reconciliation_event_id=order_reconciliation.reconciliation_event_id,
            ),
        )
    except Exception:
        audit_evidence_written = False
        accounting_completion_status = "unresolved"
        live_order.status = "RECONCILIATION_REQUIRED"

    if not audit_evidence_written:
        await record_live_order_reconciliation(
            db=db,
            request=LiveOrderReconciliationRequest(
                live_trading_profile_id=profile.id,
                source_execution_event_id=source_event.id,
                provider_name=live_order.provider,
                provider_order_id=live_order.provider_order_id,
                client_order_id=live_order.client_order_id,
                reconciliation_status="reconciliation_required",
                live_crypto_order_id=live_order.live_crypto_order_id,
                capital_campaign_id=None if campaign is None else campaign.id,
                provider_recorded_at=None,
                requested_by=operator_identity,
                provenance_metadata={"reason": "audit_persistence_failure"},
                idempotency_key=f"lco-reconcile:{live_order.live_crypto_order_id}:audit-unresolved",
            ),
        )

    live_order.safe_provider_response = {
        **(live_order.safe_provider_response or {}),
        "capital_campaign_id": None if campaign is None else campaign.id,
        "reconciliation": {
            "provider_status": provider_status_raw,
            "normalized_status": normalized_status,
            "fill_count": fill_count,
            "fill_events": fill_events,
            "total_filled_quantity": format(total_filled_quantity, "f"),
            "total_quote_notional": format(total_quote_notional, "f"),
            "weighted_average_fill_price": None if weighted_average_fill_price is None else format(weighted_average_fill_price, "f"),
            "fees": {currency: format(amount, "f") for currency, amount in total_fees_by_currency.items()},
            "expected_quote_reduction": format(expected_quote_reduction, "f"),
            "balance_status": balance_status,
            "balance_mismatch_state": balance_mismatch_state,
            "balance_tolerance_usd": format(balance_tolerance, "f"),
            "balance_observed_at": None if balance_observed_at is None else balance_observed_at.isoformat(),
            "campaign_correlation_status": campaign_correlation_status,
            "accounting_projection_status": accounting_projection_status,
            "accounting_completion_status": accounting_completion_status,
            "net_quote_capital_effect": format(expected_quote_reduction, "f"),
            "profit_cycle_consistency": {
                "buy_fill_realized_profit": "0",
                "distributable_profit_created": False,
                "fees_reflected": provider_fee_total > Decimal("0"),
                "partial_fill_non_overstated": total_quote_notional <= _decimal(live_order.requested_quote_size),
                "cancellation_partial_consistent": normalized_status != "canceled" or total_filled_quantity >= Decimal("0"),
            },
            "fee_delta_vs_preview": format(fee_delta, "f"),
            "observed_at": _utcnow().isoformat(),
        }
    }
    live_order.updated_at = _utcnow()
    await db.flush()

    return {
        "reconciliation_status": live_order.status,
        "provider_status": provider_status_raw,
        "provider_order_id": live_order.provider_order_id,
        "provider_fill_observed": total_filled_quantity > Decimal("0"),
        "campaign_correlation_status": campaign_correlation_status,
        "accounting_projection_status": accounting_projection_status,
        "accounting_completion_status": accounting_completion_status,
        "balance_mismatch_state": balance_mismatch_state,
        "filled_quantity": format(total_filled_quantity, "f"),
        "gross_filled_notional": format(total_quote_notional, "f"),
        "provider_fees": format(provider_fee_total, "f"),
        "net_quote_capital_effect": format(expected_quote_reduction, "f"),
        "safe_provider_response": _safe_json(live_order.safe_provider_response.get("reconciliation", {})),
    }


def build_live_reconciliation_event_hash(
    *,
    live_trading_profile_id: uuid.UUID,
    sequence_number: int,
    event_type: str,
    idempotency_key: str,
    recorded_at: datetime,
    payload: dict[str, object],
) -> str:
    blob = json.dumps(
        {
            "live_trading_profile_id": str(live_trading_profile_id),
            "sequence_number": sequence_number,
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "recorded_at": recorded_at.isoformat(),
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def record_live_order_reconciliation(
    *,
    db: AsyncSession,
    request: LiveOrderReconciliationRequest,
) -> LiveReconciliationResult:
    idempotency_key = request.idempotency_key or build_live_reconciliation_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        provider_name=request.provider_name,
        provider_order_id=request.provider_order_id,
        provider_fill_id=None,
        event_type="order_reconciled",
    )

    existing = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return LiveReconciliationResult(
            accepted=True,
            status="replayed",
            reason=None,
            live_trading_profile_id=existing.live_trading_profile_id,
            source_execution_event_id=existing.source_execution_event_id,
            reconciliation_event_id=existing.id,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )

    source_event = await db.scalar(
        select(LiveExecutionEvent)
        .where(
            LiveExecutionEvent.id == request.source_execution_event_id,
            LiveExecutionEvent.live_trading_profile_id == request.live_trading_profile_id,
        )
        .limit(1)
    )
    if source_event is None:
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_found",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )
    if source_event.event_type != "execution_intent_created":
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_reconcilable",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )

    recorded_at = datetime.now(timezone.utc)
    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveReconciliationEvent.sequence_number)).where(
                LiveReconciliationEvent.live_trading_profile_id == request.live_trading_profile_id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        payload = {
            "provider_order_id": request.provider_order_id,
            "client_order_id": request.client_order_id,
            "status": request.reconciliation_status,
        }
        event = LiveReconciliationEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_reconciliation_event_hash(
                live_trading_profile_id=request.live_trading_profile_id,
                sequence_number=sequence_number,
                event_type="order_reconciled",
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                payload=payload,
            ),
            live_trading_profile_id=request.live_trading_profile_id,
            live_crypto_order_id=request.live_crypto_order_id,
            capital_campaign_id=request.capital_campaign_id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            sequence_number=sequence_number,
            event_type="order_reconciled",
            reconciliation_status=request.reconciliation_status,
            provider_name=request.provider_name,
            provider_order_id=request.provider_order_id,
            provider_fill_id=None,
            event_payload=payload,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            provider_recorded_at=request.provider_recorded_at,
            recorded_at=recorded_at,
        )
        db.add(event)
        await db.flush()

    return LiveReconciliationResult(
        accepted=True,
        status="recorded",
        reason=None,
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        reconciliation_event_id=event.id,
        accounting_record_ids=tuple(),
        idempotency_key=idempotency_key,
    )


async def record_live_fill_reconciliation(
    *,
    db: AsyncSession,
    request: LiveFillReconciliationRequest,
) -> LiveReconciliationResult:
    idempotency_key = request.idempotency_key or build_live_reconciliation_idempotency_key(
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        provider_name=request.provider_name,
        provider_order_id=request.provider_order_id,
        provider_fill_id=request.provider_fill_id,
        event_type="fill_reconciled",
    )

    existing = await db.scalar(
        select(LiveReconciliationEvent)
        .where(LiveReconciliationEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        accounting_rows = await db.scalars(
            select(LiveAccountingRecord)
            .where(LiveAccountingRecord.reconciliation_event_id == existing.id)
            .order_by(LiveAccountingRecord.created_at.asc())
        )
        return LiveReconciliationResult(
            accepted=True,
            status="replayed",
            reason=None,
            live_trading_profile_id=existing.live_trading_profile_id,
            source_execution_event_id=existing.source_execution_event_id,
            reconciliation_event_id=existing.id,
            accounting_record_ids=tuple(item.id for item in accounting_rows),
            idempotency_key=idempotency_key,
        )

    source_event = await db.scalar(
        select(LiveExecutionEvent)
        .where(
            LiveExecutionEvent.id == request.source_execution_event_id,
            LiveExecutionEvent.live_trading_profile_id == request.live_trading_profile_id,
        )
        .limit(1)
    )
    if source_event is None:
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_found",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )
    if source_event.event_type != "execution_intent_created":
        return LiveReconciliationResult(
            accepted=False,
            status="blocked",
            reason="source_execution_event_not_reconcilable",
            live_trading_profile_id=request.live_trading_profile_id,
            source_execution_event_id=request.source_execution_event_id,
            reconciliation_event_id=None,
            accounting_record_ids=tuple(),
            idempotency_key=idempotency_key,
        )

    fill_quantity = Decimal(request.fill_quantity)
    cumulative_filled = Decimal(request.cumulative_filled_quantity)
    order_quantity = Decimal(request.order_quantity)
    fill_price = Decimal(request.fill_price)
    fee_amount = Decimal(request.fee_amount)
    gross_notional = fill_quantity * fill_price
    is_partial = cumulative_filled < order_quantity

    reconciliation_status = "partially_filled" if is_partial else "filled"
    accounting_record_type = "partial_fill_accounting" if is_partial else "fill_accounting"

    net_cash_impact = gross_notional + fee_amount
    if request.side == "buy":
        net_cash_impact = net_cash_impact * Decimal("-1")

    recorded_at = datetime.now(timezone.utc)
    async with db.begin():
        existing_sequence = await db.scalar(
            select(func.max(LiveReconciliationEvent.sequence_number)).where(
                LiveReconciliationEvent.live_trading_profile_id == request.live_trading_profile_id
            )
        )
        sequence_number = int(existing_sequence or 0) + 1

        payload = {
            "provider_order_id": request.provider_order_id,
            "provider_fill_id": request.provider_fill_id,
            "client_order_id": request.client_order_id,
            "fill_quantity": request.fill_quantity,
            "cumulative_filled_quantity": request.cumulative_filled_quantity,
            "order_quantity": request.order_quantity,
            "fill_price": request.fill_price,
            "fee_amount": request.fee_amount,
            "fee_currency": request.fee_currency,
            "partial_fill": is_partial,
        }
        event = LiveReconciliationEvent(
            idempotency_key=idempotency_key,
            event_hash=build_live_reconciliation_event_hash(
                live_trading_profile_id=request.live_trading_profile_id,
                sequence_number=sequence_number,
                event_type="fill_reconciled",
                idempotency_key=idempotency_key,
                recorded_at=recorded_at,
                payload=payload,
            ),
            live_trading_profile_id=request.live_trading_profile_id,
            live_crypto_order_id=request.live_crypto_order_id,
            capital_campaign_id=request.capital_campaign_id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            sequence_number=sequence_number,
            event_type="fill_reconciled",
            reconciliation_status=reconciliation_status,
            provider_name=request.provider_name,
            provider_order_id=request.provider_order_id,
            provider_fill_id=request.provider_fill_id,
            event_payload=payload,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                **request.provenance_metadata,
            },
            immutable_contract_version="v1",
            provider_recorded_at=request.provider_recorded_at,
            recorded_at=recorded_at,
        )
        db.add(event)
        await db.flush()

        accounting = LiveAccountingRecord(
            idempotency_key=f"{idempotency_key}:fill",
            live_trading_profile_id=request.live_trading_profile_id,
            live_crypto_order_id=request.live_crypto_order_id,
            capital_campaign_id=request.capital_campaign_id,
            reconciliation_event_id=event.id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            record_type=accounting_record_type,
            provider_order_id=request.provider_order_id,
            provider_fill_id=request.provider_fill_id,
            symbol=request.symbol,
            side=request.side,
            filled_quantity=fill_quantity,
            fill_price=fill_price,
            gross_notional=gross_notional,
            fee_amount=fee_amount,
            fee_currency=request.fee_currency,
            net_cash_impact=net_cash_impact,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                "reconciliation_status": reconciliation_status,
                **request.provenance_metadata,
            },
            provider_fill_timestamp=request.provider_fill_timestamp,
            recorded_at=recorded_at,
        )
        db.add(accounting)

        fee_attribution = LiveAccountingRecord(
            idempotency_key=f"{idempotency_key}:fee",
            live_trading_profile_id=request.live_trading_profile_id,
            live_crypto_order_id=request.live_crypto_order_id,
            capital_campaign_id=request.capital_campaign_id,
            reconciliation_event_id=event.id,
            source_execution_event_id=request.source_execution_event_id,
            source_execution_event_type="execution_intent_created",
            record_type="fee_attribution",
            provider_order_id=request.provider_order_id,
            provider_fill_id=request.provider_fill_id,
            symbol=request.symbol,
            side=request.side,
            filled_quantity=fill_quantity,
            fill_price=fill_price,
            gross_notional=gross_notional,
            fee_amount=fee_amount,
            fee_currency=request.fee_currency,
            net_cash_impact=fee_amount,
            provenance={
                "requested_by": request.requested_by,
                "recorded_at": recorded_at.isoformat(),
                "attribution": "fill_fee",
                **request.provenance_metadata,
            },
            provider_fill_timestamp=request.provider_fill_timestamp,
            recorded_at=recorded_at,
        )
        db.add(fee_attribution)
        await db.flush()

    return LiveReconciliationResult(
        accepted=True,
        status="recorded",
        reason=None,
        live_trading_profile_id=request.live_trading_profile_id,
        source_execution_event_id=request.source_execution_event_id,
        reconciliation_event_id=event.id,
        accounting_record_ids=(accounting.id, fee_attribution.id),
        idempotency_key=idempotency_key,
    )
