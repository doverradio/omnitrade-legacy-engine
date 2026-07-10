from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_event import LiveTradingEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.risk_event import RiskEvent
from app.schemas.live_crypto_orders import (
    LiveCryptoOrderCancelRequest,
    LiveCryptoOrderPrepareRequest,
    LiveCryptoOrderPrepareResponse,
    LiveCryptoOrderReadinessResponse,
    LiveCryptoOrderReconcileRequest,
    LiveCryptoOrderReconcileResponse,
    LiveCryptoOrderResponse,
    LiveCryptoOrderSubmitRequest,
    LiveCryptoOrderSubmitResponse,
)
from app.services.exchange_connections.service import get_decrypted_credentials_for_connection
from app.services.exchange_connections.providers.coinbase_advanced import CoinbaseAdvancedClient
from app.services.live.approval import evaluate_live_approval_gate
from app.services.live.resilience import evaluate_live_submission_guard
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationContext, RiskEvaluationRequest, evaluate_signal_risk
from app.services.risk.risk_persistence import persist_risk_decision


CONFIRMATION_PHRASE = "CONFIRM BTC-USD BUY $5"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_serialize_payload(payload).encode("utf-8")).hexdigest()


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quantize_usd(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _order_status_from_provider(provider_status: str | None, fill_ratio: Decimal | None) -> str:
    if provider_status in {"FILLED"}:
        return "FILLED"
    if provider_status in {"CANCELLED", "EXPIRED"}:
        return "CANCELLED" if provider_status == "CANCELLED" else "RECONCILIATION_REQUIRED"
    if fill_ratio is not None and fill_ratio > Decimal("0"):
        return "PARTIALLY_FILLED"
    if provider_status in {"OPEN", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED", "PENDING"}:
        return "SUBMITTED"
    if provider_status in {"FAILED", "UNKNOWN"}:
        return "RECONCILIATION_REQUIRED"
    return "UNKNOWN"


class LiveCryptoOrderService:
    async def get_readiness(self, *, db: AsyncSession, live_trading_profile_id: uuid.UUID) -> LiveCryptoOrderReadinessResponse:
        settings = get_settings()
        profile = await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1))
        if profile is None:
            return LiveCryptoOrderReadinessResponse(
                live_mode_enabled=False,
                live_profile_ready=False,
                feature_flag_enabled=settings.live_crypto_order_submission_enabled,
                max_order_usd=settings.live_crypto_max_order_usd,
                latest_preview_age_seconds=None,
                latest_balance_age_seconds=None,
                latest_readiness_age_seconds=None,
                latest_price_age_seconds=None,
                reason="live_profile_not_found",
            )

        latest_preview = await db.scalar(
            select(CryptoOrderPreview)
            .where(CryptoOrderPreview.live_trading_profile_id == profile.id)
            .order_by(CryptoOrderPreview.created_at.desc())
            .limit(1)
        )
        preview_age = None
        if latest_preview is not None:
            preview_age = int((_utcnow() - latest_preview.created_at).total_seconds())

        live_mode_enabled = profile.operating_mode == "live" and profile.lifecycle_state in {"approved", "enabled"}
        return LiveCryptoOrderReadinessResponse(
            live_mode_enabled=live_mode_enabled,
            live_profile_ready=profile.lifecycle_state in {"approved", "enabled", "suspended"},
            feature_flag_enabled=settings.live_crypto_order_submission_enabled,
            max_order_usd=settings.live_crypto_max_order_usd,
            latest_preview_age_seconds=preview_age,
            latest_balance_age_seconds=None,
            latest_readiness_age_seconds=None,
            latest_price_age_seconds=None,
            reason=None if live_mode_enabled and settings.live_crypto_order_submission_enabled else "live_submission_disabled",
        )

    async def list_orders(
        self,
        *,
        db: AsyncSession,
        live_trading_profile_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> list[LiveCryptoOrderResponse]:
        query = select(LiveCryptoOrder).order_by(LiveCryptoOrder.created_at.desc())
        if live_trading_profile_id is not None:
            preview_ids = select(CryptoOrderPreview.crypto_order_preview_id).where(
                CryptoOrderPreview.live_trading_profile_id == live_trading_profile_id
            )
            query = query.where(LiveCryptoOrder.crypto_order_preview_id.in_(preview_ids))
        if status is not None:
            query = query.where(LiveCryptoOrder.status == status)
        items = list(await db.scalars(query))
        return [self._to_response(item) for item in items]

    async def get_order(
        self,
        *,
        db: AsyncSession,
        live_crypto_order_id: uuid.UUID,
    ) -> LiveCryptoOrderResponse:
        live_order = await db.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id).limit(1)
        )
        if live_order is None:
            raise LookupError("live crypto order not found")
        return self._to_response(live_order)

    async def prepare_confirmation(
        self,
        *,
        db: AsyncSession,
        request: LiveCryptoOrderPrepareRequest,
        operator_confirmation_token: str | None = None,
    ) -> LiveCryptoOrderPrepareResponse:
        settings = get_settings()
        if not settings.live_crypto_order_submission_enabled:
            raise PermissionError("live crypto order submission is disabled")

        profile = await db.scalar(
            select(LiveTradingProfile).where(LiveTradingProfile.id == request.live_trading_profile_id).limit(1)
        )
        if profile is None:
            raise LookupError("live trading profile not found")

        preview = await db.scalar(
            select(CryptoOrderPreview).where(
                CryptoOrderPreview.crypto_order_preview_id == request.crypto_order_preview_id
            ).limit(1)
        )
        if preview is None:
            raise LookupError("crypto order preview not found")
        if preview.live_trading_profile_id != profile.id:
            raise ValueError("preview does not belong to the requested live trading profile")
        if preview.side != "BUY" or preview.product_id != "BTC-USD" or preview.order_type != "MARKET":
            raise ValueError("preview is not eligible for live BTC-USD market buy submission")
        requested_quote_size = _decimal(preview.requested_amount)
        if requested_quote_size > settings.live_crypto_max_order_usd:
            raise ValueError("preview exceeds live order size limit")

        preview_age_seconds = int((_utcnow() - preview.created_at).total_seconds())
        if preview_age_seconds > settings.live_crypto_preview_max_age_seconds:
            raise ValueError("preview is too old for submission")

        approval_gate = await evaluate_live_approval_gate(
            db=db,
            live_trading_profile_id=profile.id,
            checkpoint_type="first_live_enablement",
        )
        if not approval_gate.approved:
            raise PermissionError(approval_gate.reason or "approval gate rejected")

        guard_result = await evaluate_live_submission_guard(
            db=db,
            live_trading_profile_id=profile.id,
        )
        if not guard_result.allowed:
            raise PermissionError(guard_result.reason or "submission guard rejected")

        risk_request = RiskEvaluationRequest(
            strategy_id=uuid.uuid4(),
            signal_id=uuid.uuid4(),
            portfolio_id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            side="BUY",
            product_id="BTC-USD",
            quote_size=requested_quote_size,
            order_type="MARKET",
            expected_price=requested_quote_size,
            stop_loss_price=None,
            take_profit_price=None,
            metadata={
                "live_trading_profile_id": str(profile.id),
                "crypto_order_preview_id": str(preview.crypto_order_preview_id),
                "requested_by": request.operator_identity,
                "confirmation": CONFIRMATION_PHRASE,
            },
        )
        risk_context = RiskEvaluationContext(
            signal_strength=Decimal("0.5"),
            position_size=Decimal("0"),
            account_equity=requested_quote_size,
            available_equity=requested_quote_size,
            volatility=Decimal("0.2"),
            drawdown=Decimal("0"),
            recent_trade_count=0,
            open_positions_count=0,
            kill_switch_active=False,
            risk_budget_remaining=requested_quote_size,
            max_loss_per_trade=requested_quote_size,
        )
        risk_result = evaluate_signal_risk(risk_request, risk_context)
        if risk_result.action != RiskDecisionAction.APPROVE:
            raise PermissionError(f"risk engine rejected live order: {risk_result.action.value}")

        risk_event = await persist_risk_decision(
            db=db,
            request=risk_request,
            context=risk_context,
            result=risk_result,
            correlation_id=uuid.uuid4(),
        )

        confirmation_challenge_id = uuid.uuid4()
        confirmation_expires_at = _utcnow() + timedelta(minutes=settings.live_crypto_confirmation_challenge_minutes)
        live_crypto_order = await self._get_or_create_live_order(
            db=db,
            preview=preview,
            profile=profile,
            risk_event_id=risk_event.id,
            request=request,
        )

        return LiveCryptoOrderPrepareResponse(
            live_crypto_order=self._to_response(live_crypto_order),
            confirmation_challenge_id=confirmation_challenge_id,
            confirmation_phrase_required=CONFIRMATION_PHRASE,
            confirmation_expires_at=confirmation_expires_at,
            live_money_warning="LIVE MONEY: operator confirmation required before submission.",
            execution_risk_verdict=risk_result.action.value,
            preview_age_seconds=preview_age_seconds,
            estimated_usd_balance_after=None,
            usd_balance_before=None,
        )

    async def submit(
        self,
        *,
        db: AsyncSession,
        request: LiveCryptoOrderSubmitRequest,
    ) -> LiveCryptoOrderSubmitResponse:
        settings = get_settings()
        if not settings.live_crypto_order_submission_enabled:
            raise PermissionError("live crypto order submission is disabled")

        live_order = await db.scalar(
            select(LiveCryptoOrder).where(
                LiveCryptoOrder.live_crypto_order_id == request.live_crypto_order_id
            ).limit(1)
        )
        if live_order is None:
            raise LookupError("live crypto order not found")
        if live_order.status not in {"PENDING_CONFIRMATION", "VALIDATING", "SUBMISSION_PENDING", "RECONCILIATION_REQUIRED"}:
            raise ValueError("live crypto order is not in a submit-able state")
        if request.confirmation_phrase != CONFIRMATION_PHRASE:
            raise PermissionError("confirmation phrase mismatch")
        if request.operator_identity != live_order.safe_provider_response.get("prepared_by"):
            raise PermissionError("operator identity mismatch")

        preview = await db.scalar(
            select(CryptoOrderPreview).where(
                CryptoOrderPreview.crypto_order_preview_id == live_order.crypto_order_preview_id
            ).limit(1)
        )
        if preview is None:
            raise LookupError("linked preview not found")
        if _utcnow() - preview.created_at > timedelta(seconds=settings.live_crypto_preview_max_age_seconds):
            raise ValueError("linked preview is too old")

        connection_credentials = await get_decrypted_credentials_for_connection(
            db=db,
            exchange_connection_id=live_order.exchange_connection_id,
        )
        provider = CoinbaseAdvancedClient()
        request_payload = {
            "client_order_id": live_order.client_order_id,
            "product_id": live_order.product_id,
            "side": live_order.side,
            "order_configuration": {
                "market_market_ioc": {
                    "quote_size": format(live_order.requested_quote_size, "f"),
                    "rfq_disabled": True,
                }
            },
        }
        provider_response, safe_response = await provider.create_order(
            credentials=connection_credentials,
            environment=live_order.environment,
            request_payload=request_payload,
            idempotency_key=request.idempotency_token,
        )
        success = bool(provider_response.get("success", False))
        success_response = provider_response.get("success_response") if isinstance(provider_response, dict) else None
        provider_order_id = None
        provider_status = None
        if isinstance(success_response, dict):
            provider_order_id = success_response.get("order_id") if isinstance(success_response.get("order_id"), str) else None
            provider_status = success_response.get("status") if isinstance(success_response.get("status"), str) else None

        live_order.provider_order_id = provider_order_id
        live_order.provider_status = provider_status or live_order.provider_status
        live_order.submitted_at = _utcnow()
        live_order.safe_provider_response = {
            **live_order.safe_provider_response,
            "create_order": safe_response,
            "create_order_payload": request_payload,
            "create_order_success": success,
            "create_order_responded": True,
        }
        if success and provider_order_id is not None:
            live_order.status = "SUBMITTED"
        else:
            live_order.status = "RECONCILIATION_REQUIRED"
            live_order.failure_code = "coinbase_order_create_failed"
            live_order.failure_reason = json.dumps(provider_response)

        live_order.updated_at = _utcnow()
        await db.flush()

        return LiveCryptoOrderSubmitResponse(
            live_crypto_order=self._to_response(live_order),
            execution_risk_verdict=str(live_order.safe_provider_response.get("execution_risk_verdict", "UNKNOWN")),
            provider_create_order_responded=True,
            provider_reconciliation_status=live_order.provider_status,
            safe_provider_response=live_order.safe_provider_response,
            order_submitted=success,
        )

    async def reconcile(
        self,
        *,
        db: AsyncSession,
        live_crypto_order_id: uuid.UUID,
        request: LiveCryptoOrderReconcileRequest,
    ) -> LiveCryptoOrderReconcileResponse:
        live_order = await db.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id).limit(1)
        )
        if live_order is None:
            raise LookupError("live crypto order not found")
        if not live_order.provider_order_id:
            raise ValueError("live crypto order has no provider order id")

        connection_credentials = await get_decrypted_credentials_for_connection(
            db=db,
            exchange_connection_id=live_order.exchange_connection_id,
        )
        provider = CoinbaseAdvancedClient()
        order_response, safe_response = await provider.get_historical_order(
            credentials=connection_credentials,
            environment=live_order.environment,
            order_id=live_order.provider_order_id,
        )
        order = order_response.get("order") if isinstance(order_response, dict) else None
        provider_status = order.get("status") if isinstance(order, dict) else None
        average_filled_price = _decimal(order.get("average_filled_price", "0")) if isinstance(order, dict) else Decimal("0")
        filled_size = _decimal(order.get("filled_size", "0")) if isinstance(order, dict) else Decimal("0")
        fill_ratio = filled_size / live_order.requested_quote_size if live_order.requested_quote_size > 0 else None
        live_order.provider_status = provider_status
        live_order.safe_provider_response = {
            **live_order.safe_provider_response,
            "reconcile": safe_response,
            "reconciled_by": request.operator_identity,
        }
        live_order.status = _order_status_from_provider(provider_status, fill_ratio)
        if provider_status == "FILLED":
            live_order.filled_at = _utcnow()
        elif provider_status == "CANCELLED":
            live_order.cancelled_at = _utcnow()
        live_order.updated_at = _utcnow()
        await db.flush()

        return LiveCryptoOrderReconcileResponse(
            live_crypto_order=self._to_response(live_order),
            reconciliation_status=live_order.status,
            provider_status=provider_status,
            provider_order_id=live_order.provider_order_id,
            provider_fill_observed=filled_size > 0,
            safe_provider_response=safe_response,
        )

    async def cancel(
        self,
        *,
        db: AsyncSession,
        live_crypto_order_id: uuid.UUID,
        request: LiveCryptoOrderCancelRequest,
    ) -> LiveCryptoOrderResponse:
        live_order = await db.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == live_crypto_order_id).limit(1)
        )
        if live_order is None:
            raise LookupError("live crypto order not found")
        if live_order.provider_order_id is None:
            raise ValueError("provider order id required for cancel")

        connection_credentials = await get_decrypted_credentials_for_connection(
            db=db,
            exchange_connection_id=live_order.exchange_connection_id,
        )
        provider = CoinbaseAdvancedClient()
        cancel_response, safe_response = await provider.cancel_orders(
            credentials=connection_credentials,
            environment=live_order.environment,
            order_ids=[live_order.provider_order_id],
            idempotency_key=str(uuid.uuid4()),
        )
        live_order.safe_provider_response = {
            **live_order.safe_provider_response,
            "cancel": safe_response,
            "cancel_requested_by": request.operator_identity,
            "cancel_reason": request.reason,
        }
        live_order.status = "CANCELLED"
        live_order.cancelled_at = _utcnow()
        live_order.updated_at = _utcnow()
        await db.flush()
        return self._to_response(live_order)

    async def _get_or_create_live_order(
        self,
        *,
        db: AsyncSession,
        preview: CryptoOrderPreview,
        profile: LiveTradingProfile,
        risk_event_id: uuid.UUID,
        request: LiveCryptoOrderPrepareRequest,
    ) -> LiveCryptoOrder:
        existing = await db.scalar(
            select(LiveCryptoOrder).where(
                LiveCryptoOrder.crypto_order_preview_id == preview.crypto_order_preview_id
            ).limit(1)
        )
        if existing is not None:
            return existing
        client_order_id = str(uuid.uuid4())
        live_order = LiveCryptoOrder(
            crypto_order_preview_id=preview.crypto_order_preview_id,
            exchange_connection_id=preview.exchange_connection_id,
            provider=preview.provider,
            environment=preview.environment,
            product_id=preview.product_id,
            side=preview.side,
            order_type=preview.order_type,
            requested_quote_size=_quantize_usd(_decimal(preview.requested_amount)),
            client_order_id=client_order_id,
            status="PENDING_CONFIRMATION",
            risk_event_id=risk_event_id,
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=None,
            provider_status=None,
            submitted_at=None,
            acknowledged_at=None,
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={
                "prepared_by": request.operator_identity,
                "confirmation_required": True,
                "confirmation_phrase_required": CONFIRMATION_PHRASE,
                "preview_id": str(preview.crypto_order_preview_id),
                "live_trading_profile_id": str(profile.id),
                "prepared_at": _utcnow().isoformat(),
            },
            audit_correlation_id=uuid.uuid4(),
            operator_confirmation_id=None,
        )
        db.add(live_order)
        await db.flush()
        return live_order

    def _to_response(self, live_order: LiveCryptoOrder) -> LiveCryptoOrderResponse:
        return LiveCryptoOrderResponse(
            live_crypto_order_id=live_order.live_crypto_order_id,
            crypto_order_preview_id=live_order.crypto_order_preview_id,
            exchange_connection_id=live_order.exchange_connection_id,
            provider=live_order.provider,
            environment=live_order.environment,
            product_id=live_order.product_id,
            side=live_order.side,
            order_type=live_order.order_type,
            requested_quote_size=live_order.requested_quote_size,
            client_order_id=live_order.client_order_id,
            status=live_order.status,
            risk_event_id=live_order.risk_event_id,
            decision_record_id=live_order.decision_record_id,
            validation_run_id=live_order.validation_run_id,
            provider_order_id=live_order.provider_order_id,
            provider_status=live_order.provider_status,
            submitted_at=live_order.submitted_at,
            acknowledged_at=live_order.acknowledged_at,
            filled_at=live_order.filled_at,
            cancelled_at=live_order.cancelled_at,
            failure_code=live_order.failure_code,
            failure_reason=live_order.failure_reason,
            safe_provider_response=live_order.safe_provider_response or {},
            audit_correlation_id=live_order.audit_correlation_id,
            operator_confirmation_id=live_order.operator_confirmation_id,
            created_at=live_order.created_at,
            updated_at=live_order.updated_at,
        )


service = LiveCryptoOrderService()
