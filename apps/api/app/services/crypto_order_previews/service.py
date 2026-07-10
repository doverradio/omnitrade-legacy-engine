from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ConflictError, InvalidRequestError, NotFoundError, ServiceUnavailableError
from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.exchange_connection import ExchangeConnection
from app.models.risk_kill_switch import RiskKillSwitch
from app.schemas.crypto_order_previews import (
    CryptoOrderPreviewCancelRequest,
    CryptoOrderPreviewCreateRequest,
    CryptoOrderPreviewListResponse,
    CryptoOrderPreviewReadinessResponse,
    CryptoOrderPreviewRefreshRequest,
    CryptoOrderPreviewResponse,
    CryptoOrderPreviewRiskVerdict,
    CryptoOrderPreviewStatus,
)
from app.services.exchange_connections.service import get_decrypted_credentials_for_connection
from app.services.exchange_connections.providers.base import ExchangeAuthResult, ExchangePreviewResult
from app.services.exchange_connections.providers.registry import get_exchange_provider
from app.services.risk.risk_context import RISK_POLICY_DEFAULTS
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationContext, RiskEvaluationRequest, evaluate_signal_risk
from app.services.risk.risk_monitor import get_risk_rules


SUPPORTED_SIDE = {"BUY", "SELL"}
SUPPORTED_ORDER_TYPE = {"MARKET"}
SUPPORTED_AMOUNT_CURRENCY = {"USD", "BTC"}
REFERENCE_INTERVALS = ("1m", "5m", "15m", "1h", "1d")


@dataclass(frozen=True, slots=True)
class _PreviewContext:
    connection: ExchangeConnection
    credentials: dict[str, str]
    asset: Asset
    reference_price: Decimal
    available_quote_balance: Decimal
    available_base_balance: Decimal
    market_age_minutes: int
    global_kill_switch_engaged: bool
    risk_max_position_size_pct: Decimal
    risk_max_daily_loss_pct: Decimal
    risk_max_drawdown_pct: Decimal
    min_order_notional: Decimal
    qty_step_size: Decimal | None
    supports_fractional: bool
    clock_skew_seconds: int | None


async def _record_audit(
    *,
    db: AsyncSession,
    actor: str,
    action: str,
    entity_id: uuid.UUID | None,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="crypto_order_preview",
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
        )
    )


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        converted = Decimal(str(value))
    except Exception:
        return None
    if not converted.is_finite():
        return None
    return converted


def _require_decimal(value: object, *, field_name: str) -> Decimal:
    decimal_value = _decimal_or_none(value)
    if decimal_value is None:
        raise InvalidRequestError(message=f"{field_name} must be a finite decimal", details={"field": field_name})
    if decimal_value <= Decimal("0"):
        raise InvalidRequestError(message=f"{field_name} must be greater than zero", details={"field": field_name})
    return decimal_value


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_SENSITIVE_SUBSTRINGS = (
    "secret",
    "private_key",
    "api_key",
    "passphrase",
    "authorization",
    "jwt",
    "token",
    "signature",
)


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(fragment in key_l for fragment in _SENSITIVE_SUBSTRINGS):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


def _preview_idempotency_key(request: CryptoOrderPreviewCreateRequest, *, actor: str, window_minutes: int) -> str:
    window = int(datetime.now(timezone.utc).timestamp() // (window_minutes * 60))
    payload = {
        "actor": actor,
        "connection": str(request.exchange_connection_id),
        "product_id": request.product_id.upper(),
        "side": request.side.upper(),
        "order_type": request.order_type.upper(),
        "quote_size": format(request.quote_size, "f") if request.quote_size is not None else None,
        "base_size": format(request.base_size, "f") if request.base_size is not None else None,
        "requested_amount_currency": request.requested_amount_currency,
        "generated_by": request.generated_by,
        "client_request_id": request.client_request_id,
        "decision_record_id": str(request.decision_record_id) if request.decision_record_id else None,
        "validation_run_id": str(request.validation_run_id) if request.validation_run_id else None,
        "strategy_id": str(request.strategy_id) if request.strategy_id else None,
        "strategy_name": request.strategy_name,
        "window": window,
    }
    return _stable_hash(payload)


async def _get_global_kill_switch(db: AsyncSession) -> bool:
    statement = select(RiskKillSwitch).where(RiskKillSwitch.scope == "global", RiskKillSwitch.paper_account_id.is_(None))
    row = await db.scalar(statement)
    if row is None:
        raise ServiceUnavailableError(message="Global risk kill switch state is unavailable", details={"scope": "global"})
    return bool(row.engaged)


async def _load_exchange_connection(db: AsyncSession, exchange_connection_id: uuid.UUID) -> ExchangeConnection:
    connection = await db.scalar(
        select(ExchangeConnection).where(ExchangeConnection.exchange_connection_id == exchange_connection_id)
    )
    if connection is None:
        raise NotFoundError(message="Exchange connection not found", details={"exchange_connection_id": str(exchange_connection_id)})
    return connection


async def _load_asset_and_price(db: AsyncSession, product_id: str) -> tuple[Asset, Decimal, datetime]:
    normalized_product = product_id.strip().upper()
    if "-" not in normalized_product:
        raise InvalidRequestError(message="product_id must be a Coinbase spot pair like BTC-USD", details={"product_id": product_id})
    base_symbol, quote_symbol = normalized_product.split("-", 1)
    if quote_symbol != "USD":
        raise InvalidRequestError(message="Only USD quote products are supported in v1", details={"product_id": product_id})

    asset = await db.scalar(
        select(Asset)
        .where(Asset.symbol == base_symbol)
        .where(Asset.asset_class == "crypto")
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc())
    )
    if asset is None:
        raise InvalidRequestError(message="Unsupported product", details={"product_id": product_id})

    candle = await db.scalar(
        select(Candle)
        .where(Candle.asset_id == asset.id)
        .where(Candle.interval.in_(REFERENCE_INTERVALS))
        .order_by(Candle.open_time.desc())
    )
    if candle is None:
        raise ServiceUnavailableError(message="No market data available for preview", details={"product_id": product_id})

    return asset, Decimal(candle.close), candle.close_time


async def _load_ready_preview(db: AsyncSession, preview_id: uuid.UUID) -> CryptoOrderPreview:
    preview = await db.scalar(
        select(CryptoOrderPreview).where(CryptoOrderPreview.crypto_order_preview_id == preview_id)
    )
    if preview is None:
        raise NotFoundError(message="Crypto order preview not found", details={"crypto_order_preview_id": str(preview_id)})
    return preview


def _to_response(record: CryptoOrderPreview) -> CryptoOrderPreviewResponse:
    return CryptoOrderPreviewResponse(
        crypto_order_preview_id=record.crypto_order_preview_id,
        preview_version=record.preview_version,
        status=_computed_status(record),
        provider=record.provider,
        environment=record.environment,
        product_id=record.product_id,
        side=record.side,
        order_type=record.order_type,
        quote_size=record.quote_size,
        base_size=record.base_size,
        requested_amount=record.requested_amount,
        requested_amount_currency=record.requested_amount_currency,
        readiness_verdict=record.readiness_verdict,
        risk_verdict=record.risk_verdict,
        risk_explanation=record.risk_explanation,
        strategy_id=record.strategy_id,
        strategy_name=record.strategy_name,
        decision_record_id=record.decision_record_id,
        validation_run_id=record.validation_run_id,
        preview_id=record.preview_id,
        estimated_average_price=record.estimated_average_price,
        estimated_total_value=record.estimated_total_value,
        estimated_base_size=record.estimated_base_size,
        estimated_quote_size=record.estimated_quote_size,
        estimated_fee=record.estimated_fee,
        estimated_fee_currency=record.estimated_fee_currency,
        estimated_slippage=record.estimated_slippage,
        estimated_commission_total=record.estimated_commission_total,
        best_bid=record.best_bid,
        best_ask=record.best_ask,
        available_balance_before=record.available_balance_before,
        estimated_balance_after=record.estimated_balance_after,
        failure_reason=record.failure_reason,
        warning_messages=list(record.warning_messages or []),
        exchange_response_summary=_redact_sensitive(dict(record.exchange_response_summary or {})),
        expires_at=record.expires_at,
        generated_by=record.generated_by,  # type: ignore[arg-type]
        audit_correlation_id=record.audit_correlation_id,
        order_submitted=False,
        execution_available=False,
        created_at=record.created_at,
        updated_at=record.updated_at,
        refreshed_from_preview_id=record.refreshed_from_preview_id,
    )


def _computed_status(record: CryptoOrderPreview) -> str:
    if record.status in {"CANCELLED", "PREVIEW_FAILED", "RISK_REJECTED", "CONNECTION_NOT_READY", "BALANCE_INSUFFICIENT"}:
        return record.status
    if datetime.now(timezone.utc) > record.expires_at:
        return "EXPIRED"
    return record.status


def _readiness_response() -> CryptoOrderPreviewReadinessResponse:
    settings = get_settings()
    return CryptoOrderPreviewReadinessResponse(
        ready=True,
        allowed_products=settings.parsed_crypto_preview_allowed_products,
        max_quote_size_usd=settings.crypto_preview_max_quote_size_usd,
        default_quote_size_usd=settings.crypto_preview_default_quote_size_usd,
        market_data_max_age_minutes=settings.crypto_preview_market_data_max_age_minutes,
        expiration_minutes=settings.crypto_preview_expiration_minutes,
    )


async def list_crypto_order_previews(*, db: AsyncSession, limit: int = 50) -> CryptoOrderPreviewListResponse:
    rows = (
        await db.execute(
            select(CryptoOrderPreview)
            .order_by(desc(CryptoOrderPreview.created_at), desc(CryptoOrderPreview.preview_version))
            .limit(limit)
        )
    ).scalars().all()
    return CryptoOrderPreviewListResponse(items=[_to_response(item) for item in rows])


async def get_crypto_order_preview(*, db: AsyncSession, preview_id: uuid.UUID) -> CryptoOrderPreviewResponse:
    return _to_response(await _load_ready_preview(db, preview_id))


async def get_crypto_order_preview_readiness() -> CryptoOrderPreviewReadinessResponse:
    return _readiness_response()


async def create_crypto_order_preview(
    *,
    db: AsyncSession,
    request: CryptoOrderPreviewCreateRequest,
    actor: str = "operator",
) -> CryptoOrderPreviewResponse:
    settings = get_settings()
    connection = await _load_exchange_connection(db, request.exchange_connection_id)
    if connection.provider != "coinbase_advanced":
        raise InvalidRequestError(message="Only Coinbase Advanced is supported", details={"provider": connection.provider})
    if connection.environment != request.environment:
        raise InvalidRequestError(
            message="Requested environment does not match the verified exchange connection",
            details={"request_environment": request.environment, "connection_environment": connection.environment},
        )
    if connection.credentials_encrypted in {"", None}:
        raise InvalidRequestError(message="Exchange credentials are not configured", details={"exchange_connection_id": str(connection.exchange_connection_id)})
    if connection.last_readiness_verdict != "READY_FOR_PREVIEW":
        raise InvalidRequestError(
            message="Exchange connection is not ready for preview",
            details={"readiness_verdict": connection.last_readiness_verdict or "UNKNOWN"},
        )

    normalized_product = request.product_id.strip().upper()
    if normalized_product not in settings.parsed_crypto_preview_allowed_products:
        raise InvalidRequestError(message="Unsupported product for crypto order preview", details={"product_id": request.product_id})
    if request.side not in SUPPORTED_SIDE:
        raise InvalidRequestError(message="Unsupported side for crypto order preview", details={"side": request.side})
    if request.order_type not in SUPPORTED_ORDER_TYPE:
        raise InvalidRequestError(message="Unsupported order type for crypto order preview", details={"order_type": request.order_type})
    if request.side == "SELL":
        raise InvalidRequestError(
            message="SELL previews are deferred in v1",
            details={"side": request.side, "feature_state": "deferred"},
        )

    if request.requested_amount_currency != "USD":
        raise InvalidRequestError(message="BUY previews must be quoted in USD in v1", details={"requested_amount_currency": request.requested_amount_currency})
    if request.quote_size is None:
        raise InvalidRequestError(message="quote_size is required for BUY previews", details={"field": "quote_size"})

    quote_size = _require_decimal(request.quote_size, field_name="quote_size")
    if quote_size > settings.crypto_preview_max_quote_size_usd:
        raise InvalidRequestError(
            message="quote_size exceeds the configured maximum preview amount",
            details={"quote_size": format(quote_size, "f"), "max_quote_size_usd": format(settings.crypto_preview_max_quote_size_usd, "f")},
        )

    if request.base_size is not None:
        raise InvalidRequestError(message="base_size is not supported for BUY previews in v1", details={"field": "base_size"})

    asset, reference_price, candle_close_time = await _load_asset_and_price(db=db, product_id=normalized_product)
    market_age_minutes = int((datetime.now(timezone.utc) - candle_close_time.astimezone(timezone.utc)).total_seconds() / 60)
    if market_age_minutes > settings.crypto_preview_market_data_max_age_minutes:
        raise InvalidRequestError(
            message="Market data is stale",
            details={"market_age_minutes": market_age_minutes, "max_age_minutes": settings.crypto_preview_market_data_max_age_minutes},
        )

    credentials = get_decrypted_credentials_for_connection(connection)
    provider = get_exchange_provider(connection.provider)
    balances_snapshot = await provider.fetch_balances(credentials=credentials, environment=connection.environment)
    available_quote_balance = next((item.available for item in balances_snapshot.balances if item.currency == "USD"), Decimal("0"))
    available_base_balance = next((item.available for item in balances_snapshot.balances if item.currency == "BTC"), Decimal("0"))
    if available_quote_balance < quote_size:
        raise InvalidRequestError(
            message="Insufficient USD balance for preview amount",
            details={"available_balance": format(available_quote_balance, "f"), "requested_amount": format(quote_size, "f")},
        )

    global_kill_switch_engaged = await _get_global_kill_switch(db)
    rules = await get_risk_rules(db=db, account_id=None)
    risk_eval = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=uuid.uuid5(uuid.NAMESPACE_URL, f"crypto-preview-signal:{request.exchange_connection_id}:{normalized_product}:{quote_size}"),
            paper_account_id=uuid.uuid5(uuid.NAMESPACE_URL, f"crypto-preview-paper-account:{request.exchange_connection_id}"),
            asset_id=asset.id,
            side="buy",
            quantity=(quote_size / reference_price),
            account_equity=available_quote_balance,
            max_position_size_pct=Decimal("1"),
            min_order_notional=Decimal(asset.min_order_notional) if asset.min_order_notional is not None else quote_size,
            qty_step_size=Decimal(asset.qty_step_size) if asset.qty_step_size is not None else None,
            supports_fractional=asset.supports_fractional,
            start_of_day_equity=available_quote_balance,
            current_equity=available_quote_balance,
            max_daily_loss_pct=Decimal(rules.rules["max_daily_loss_pct"]),
            high_water_mark_equity=available_quote_balance,
            max_drawdown_pct=Decimal(rules.rules["max_drawdown_pct"]),
            global_kill_switch_state_observed=True,
            account_kill_switch_state_observed=True,
            evaluation_time=datetime.now(timezone.utc),
            actor=actor,
        ),
        reference_price=reference_price,
        context=RiskEvaluationContext(
            global_kill_switch_engaged=global_kill_switch_engaged,
            account_trading_paused=False,
            asset_in_no_trade_zone=False,
            pair_in_cooldown=False,
            would_breach_daily_loss=False,
            would_breach_drawdown=False,
            has_computable_stop_loss=True,
            ai_scaled_quantity=None,
        ),
    )

    idempotency_key = _preview_idempotency_key(request, actor=actor, window_minutes=settings.crypto_preview_idempotency_window_minutes)
    existing = await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.idempotency_key == idempotency_key)
        .order_by(desc(CryptoOrderPreview.preview_version))
    )
    if existing is not None and _computed_status(existing) in {"PREVIEW_READY", "PREVIEW_REQUESTED", "DRAFT"} and datetime.now(timezone.utc) <= existing.expires_at:
        return _to_response(existing)

    record = CryptoOrderPreview(
        idempotency_key=idempotency_key,
        preview_version=(existing.preview_version + 1 if existing is not None else 1),
        refreshed_from_preview_id=existing.crypto_order_preview_id if existing is not None else None,
        exchange_connection_id=connection.exchange_connection_id,
        provider=connection.provider,
        environment=connection.environment,
        product_id=normalized_product,
        side=request.side,
        order_type=request.order_type,
        quote_size=quote_size,
        base_size=None,
        requested_amount=quote_size,
        requested_amount_currency="USD",
        status="PREVIEW_REQUESTED",
        readiness_verdict=connection.last_readiness_verdict,
        decision_record_id=request.decision_record_id,
        validation_run_id=request.validation_run_id,
        strategy_id=request.strategy_id,
        strategy_name=request.strategy_name,
        preview_id=None,
        warning_messages=[],
        exchange_response_summary={},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.crypto_preview_expiration_minutes),
        generated_by=request.generated_by,
        audit_correlation_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        available_balance_before=available_quote_balance,
    )
    db.add(record)
    await db.flush()

    await _record_audit(
        db=db,
        actor=actor,
        action="crypto_order_preview_initiated",
        entity_id=record.crypto_order_preview_id,
        before_state=None,
        after_state={
            "product_id": record.product_id,
            "side": record.side,
            "quote_size": format(quote_size, "f"),
            "readiness_verdict": record.readiness_verdict,
            "risk_reason_code": risk_eval.reason_code,
        },
    )

    if risk_eval.action == RiskDecisionAction.REJECT:
        record.status = "RISK_REJECTED"
        record.risk_verdict = "rejected"
        record.risk_explanation = risk_eval.reason_code or "Risk engine rejected the preview"
        record.failure_reason = risk_eval.reason_code or "risk_rejected"
        await _record_audit(
            db=db,
            actor=actor,
            action="crypto_order_preview_risk_rejected",
            entity_id=record.crypto_order_preview_id,
            before_state={"status": "PREVIEW_REQUESTED"},
            after_state={"status": record.status, "reason_code": record.failure_reason},
        )
        await db.commit()
        await db.refresh(record)
        return _to_response(record)

    await _record_audit(
        db=db,
        actor=actor,
        action="crypto_order_preview_coinbase_requested",
        entity_id=record.crypto_order_preview_id,
        before_state={"status": "PREVIEW_REQUESTED"},
        after_state={"status": "PREVIEW_REQUESTED"},
    )

    preview: ExchangePreviewResult = await provider.preview_market_order(
        credentials=credentials,
        environment=connection.environment,
        product_id=normalized_product,
        side=request.side,
        quote_size=quote_size,
        base_size=None,
        client_order_id=request.client_request_id,
    )

    record.preview_id = preview.preview_id
    record.warning_messages = list(preview.warning_messages)
    record.exchange_response_summary = _redact_sensitive(dict(preview.exchange_response_summary))
    record.best_bid = preview.best_bid
    record.best_ask = preview.best_ask
    record.estimated_average_price = preview.estimated_average_price or reference_price
    record.estimated_quote_size = preview.estimated_quote_size or quote_size
    record.estimated_base_size = preview.estimated_base_size or (quote_size / (preview.estimated_average_price or reference_price))
    record.estimated_fee = preview.estimated_fee or Decimal("0")
    record.estimated_fee_currency = preview.estimated_fee_currency or "USD"
    record.estimated_commission_total = preview.estimated_commission_total or record.estimated_fee
    record.estimated_total_value = preview.estimated_total_value or ((record.estimated_quote_size or quote_size) + (record.estimated_fee or Decimal("0")))
    record.estimated_slippage = preview.estimated_slippage or abs((record.estimated_average_price or reference_price) - reference_price) / reference_price
    record.estimated_balance_after = available_quote_balance - (record.estimated_total_value or quote_size)

    if not preview.success:
        record.status = "PREVIEW_FAILED"
        record.risk_verdict = "approved_for_preview"
        record.risk_explanation = "Risk engine approved preview; Coinbase preview returned a failure."
        record.failure_reason = preview.failure_reason or "coinbase_preview_failed"
        await _record_audit(
            db=db,
            actor=actor,
            action="crypto_order_preview_failed",
            entity_id=record.crypto_order_preview_id,
            before_state={"status": "PREVIEW_REQUESTED"},
            after_state={"status": record.status, "failure_reason": record.failure_reason},
        )
        await db.commit()
        await db.refresh(record)
        return _to_response(record)

    record.status = "PREVIEW_READY"
    record.risk_verdict = "approved_for_preview"
    record.risk_explanation = "Risk engine approved the proposed preview."
    record.failure_reason = None
    await _record_audit(
        db=db,
        actor=actor,
        action="PREVIEW_GENERATED",
        entity_id=record.crypto_order_preview_id,
        before_state={"status": "PREVIEW_REQUESTED"},
        after_state={
            "status": record.status,
            "preview_id": record.preview_id,
            "estimated_total_value": format(record.estimated_total_value or Decimal("0"), "f"),
        },
    )

    await db.commit()
    await db.refresh(record)
    return _to_response(record)


async def refresh_crypto_order_preview(
    *,
    db: AsyncSession,
    preview_id: uuid.UUID,
    payload: CryptoOrderPreviewRefreshRequest | None = None,
    actor: str = "operator",
) -> CryptoOrderPreviewResponse:
    existing = await _load_ready_preview(db, preview_id)
    if _computed_status(existing) == "CANCELLED":
        raise ConflictError(message="Cancelled previews cannot be refreshed", details={"crypto_order_preview_id": str(preview_id)})

    refreshed_request = CryptoOrderPreviewCreateRequest(
        exchange_connection_id=existing.exchange_connection_id,
        environment=existing.environment,
        product_id=existing.product_id,
        side=existing.side,
        order_type=existing.order_type,
        quote_size=existing.quote_size,
        base_size=existing.base_size,
        requested_amount_currency=existing.requested_amount_currency,
        decision_record_id=existing.decision_record_id,
        validation_run_id=existing.validation_run_id,
        strategy_id=existing.strategy_id,
        strategy_name=existing.strategy_name,
        generated_by=existing.generated_by,  # type: ignore[arg-type]
        client_request_id=payload.client_request_id if payload and payload.client_request_id else f"refresh:{existing.crypto_order_preview_id}:{uuid.uuid4()}",
    )

    await _record_audit(
        db=db,
        actor=actor,
        action="crypto_order_preview_refreshed",
        entity_id=existing.crypto_order_preview_id,
        before_state={"status": existing.status},
        after_state={"status": existing.status},
    )
    return await create_crypto_order_preview(db=db, request=refreshed_request, actor=actor)


async def cancel_crypto_order_preview(
    *,
    db: AsyncSession,
    preview_id: uuid.UUID,
    payload: CryptoOrderPreviewCancelRequest,
    actor: str = "operator",
) -> CryptoOrderPreviewResponse:
    record = await _load_ready_preview(db, preview_id)
    before_state = {"status": record.status, "failure_reason": record.failure_reason}
    record.status = "CANCELLED"
    record.failure_reason = payload.reason
    record.updated_at = datetime.now(timezone.utc)
    await _record_audit(
        db=db,
        actor=actor,
        action="crypto_order_preview_cancelled",
        entity_id=record.crypto_order_preview_id,
        before_state=before_state,
        after_state={"status": record.status, "reason": payload.reason},
    )
    await db.commit()
    await db.refresh(record)
    return _to_response(record)
