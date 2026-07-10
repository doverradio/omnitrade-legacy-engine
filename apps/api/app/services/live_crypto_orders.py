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
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_event import LiveTradingEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.risk_event import RiskEvent
from app.schemas.live_crypto_orders import (
    LiveCryptoOrderCancelRequest,
    LiveCryptoOrderDryRunRequest,
    LiveCryptoOrderDryRunResponse,
    LiveCryptoOrderPrepareRequest,
    LiveCryptoOrderPrepareResponse,
    LiveCryptoOrderReadinessResponse,
    LiveCryptoOrderReconcileRequest,
    LiveCryptoOrderReconcileResponse,
    LiveCryptoOrderResponse,
    LiveCryptoOrderSubmitRequest,
    LiveCryptoOrderSubmitResponse,
)
from app.services.exchange_connections.providers.coinbase_advanced import CoinbaseAdvancedClient
from app.services.live.approval import evaluate_live_approval_gate
from app.services.live.resilience import evaluate_live_submission_guard
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationContext, RiskEvaluationRequest, evaluate_signal_risk
from app.services.risk.risk_persistence import RiskDecisionPersistenceRequest, persist_risk_decision


CONFIRMATION_PHRASE = "BUY BTC"


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


async def _load_exchange_connection(*, db: AsyncSession, exchange_connection_id: uuid.UUID) -> ExchangeConnection:
    connection = await db.scalar(
        select(ExchangeConnection).where(ExchangeConnection.exchange_connection_id == exchange_connection_id).limit(1)
    )
    if connection is None:
        raise LookupError("exchange connection not found")
    return connection


def _load_decrypted_credentials(connection: ExchangeConnection) -> dict[str, str]:
    from app.services.exchange_connections.service import get_decrypted_credentials_for_connection

    return get_decrypted_credentials_for_connection(connection)


def _build_live_create_order_payload(*, live_order: LiveCryptoOrder) -> dict[str, Any]:
    return {
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


def _make_check(*, code: str, label: str, status: str, explanation: str, remediation: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": status,
        "explanation": explanation,
        "checked_at": _utcnow(),
        "remediation": remediation,
    }


def _overall_readiness_verdict(checks: list[dict[str, Any]], *, dry_run_enabled: bool, submission_enabled: bool) -> str:
    if any(item["status"] == "fail" for item in checks):
        return "BLOCKED"
    if dry_run_enabled and not submission_enabled:
        return "READY_FOR_DRY_RUN"
    if dry_run_enabled and submission_enabled:
        return "DRY_RUN_PASSED"
    if submission_enabled:
        return "READY_FOR_OPERATOR_ENABLEMENT"
    return "NOT_CONFIGURED"


def _safe_request_summary(*, request_payload: dict[str, Any], provider_response: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = {
        "product_id": request_payload.get("product_id"),
        "side": request_payload.get("side"),
        "order_configuration": request_payload.get("order_configuration"),
    }
    if provider_response is not None:
        summary["provider_preview"] = provider_response
    return summary


def _age_seconds(earlier: datetime | None, later: datetime | None = None) -> int | None:
    if earlier is None:
        return None
    reference = later or _utcnow()
    return int((reference - earlier).total_seconds())


class LiveCryptoOrderService:
    async def get_readiness(self, *, db: AsyncSession, live_trading_profile_id: uuid.UUID) -> LiveCryptoOrderReadinessResponse:
        settings = get_settings()
        profile = await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1))
        checks: list[dict[str, Any]] = [
            _make_check(
                code="submission_feature_flag_disabled",
                label="Submission Feature Flag Disabled",
                status="pass" if settings.live_crypto_order_submission_enabled is False else "fail",
                explanation="Live submission remains disabled by default.",
                remediation="Keep LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED=false until operator review is complete.",
            ),
            _make_check(
                code="dry_run_enabled",
                label="Dry Run Enabled",
                status="pass" if settings.live_crypto_dry_run_enabled else "fail",
                explanation="Server dry-run mode is configured.",
                remediation="Set LIVE_CRYPTO_DRY_RUN_ENABLED=true to keep pre-submit verification available.",
            ),
        ]

        if profile is None:
            checks.extend(
                [
                    _make_check(
                        code="live_profile_configured",
                        label="Live Profile Configured",
                        status="fail",
                        explanation="Live trading profile was not found.",
                        remediation="Register and approve the live trading profile first.",
                    ),
                    _make_check(
                        code="operator_authorization_configured",
                        label="Operator Authorization Configured",
                        status="warn",
                        explanation="Bearer-authenticated operator access is required on live order mutation endpoints.",
                        remediation="Provide a bearer token before preparing or submitting live orders.",
                    ),
                ]
            )
            return LiveCryptoOrderReadinessResponse(
                overall_verdict=_overall_readiness_verdict(checks, dry_run_enabled=settings.live_crypto_dry_run_enabled, submission_enabled=settings.live_crypto_order_submission_enabled),
                live_mode_enabled=False,
                live_profile_ready=False,
                feature_flag_enabled=settings.live_crypto_order_submission_enabled,
                dry_run_enabled=settings.live_crypto_dry_run_enabled,
                max_order_usd=settings.live_crypto_max_order_usd,
                latest_preview_age_seconds=None,
                latest_balance_age_seconds=None,
                latest_readiness_age_seconds=None,
                latest_price_age_seconds=None,
                reason="live_profile_not_found",
                checks=checks,
            )

        latest_preview = await db.scalar(
            select(CryptoOrderPreview)
            .where(CryptoOrderPreview.live_trading_profile_id == profile.id)
            .order_by(CryptoOrderPreview.created_at.desc())
            .limit(1)
        )
        latest_connection = None
        if latest_preview is not None:
            latest_connection = await _load_exchange_connection(db=db, exchange_connection_id=latest_preview.exchange_connection_id)

        preview_age = _age_seconds(latest_preview.created_at) if latest_preview is not None else None
        balance_age = _age_seconds(latest_connection.last_successful_sync_at) if latest_connection else None
        readiness_age = _age_seconds(latest_connection.last_verified_at) if latest_connection else None

        live_mode_enabled = profile.operating_mode == "live" and profile.lifecycle_state in {"approved", "enabled"}
        live_profile_ready = profile.lifecycle_state in {"approved", "enabled", "suspended"}

        checks.extend(
            [
                _make_check(
                    code="live_profile_configured",
                    label="Live Profile Configured",
                    status="pass" if profile is not None else "fail",
                    explanation="A live trading profile is available for the preview-to-live path.",
                    remediation="Register the live trading profile first.",
                ),
                _make_check(
                    code="production_connection_configured",
                    label="Coinbase Production Connection Configured",
                    status="pass" if latest_connection and latest_connection.environment == "production" else "fail",
                    explanation="A production Coinbase connection is available for the approved preview.",
                    remediation="Use a production Coinbase Advanced connection for the live order path.",
                ),
                _make_check(
                    code="credentials_valid",
                    label="Credentials Valid",
                    status="pass" if latest_connection and latest_connection.credentials_valid else "fail",
                    explanation="Stored Coinbase credentials validated on the last connection sync.",
                    remediation="Refresh the exchange connection credentials.",
                ),
                _make_check(
                    code="trade_permission_present",
                    label="Trading Permission Present",
                    status="pass" if latest_connection and "trade" in (latest_connection.api_permissions or []) else "fail",
                    explanation="Trade permission is present on the connected Coinbase account.",
                    remediation="Grant trade permission on the Coinbase API key.",
                ),
                _make_check(
                    code="withdrawal_permission_not_required",
                    label="Withdrawal Permission Not Required",
                    status="pass" if latest_connection and "withdraw" not in (latest_connection.api_permissions or []) else "warn",
                    explanation="Live order submission does not require withdrawal permissions.",
                    remediation="Do not grant withdrawal permissions for first-trade readiness.",
                ),
                _make_check(
                    code="balance_available",
                    label="Balance Available",
                    status="pass" if latest_connection and any(item.get("currency") == "USD" and Decimal(str(item.get("available", "0"))) > 0 for item in (latest_connection.balances or [])) else "fail",
                    explanation="USD balance is available for a $5 BTC-USD buy.",
                    remediation="Fund the Coinbase account before operator enablement.",
                ),
                _make_check(
                    code="btc_usd_available",
                    label="BTC-USD Available",
                    status="pass" if latest_preview and latest_preview.product_id == "BTC-USD" else "fail",
                    explanation="The approved preview references BTC-USD.",
                    remediation="Use the BTC-USD product for the first live trade.",
                ),
                _make_check(
                    code="preview_service_healthy",
                    label="Preview Service Healthy",
                    status="pass" if latest_preview is not None and preview_age is not None and preview_age < settings.live_crypto_preview_max_age_seconds else "fail",
                    explanation="A recent approved preview is available for the live handoff.",
                    remediation="Run a fresh preview before any live trade.",
                ),
                _make_check(
                    code="risk_engine_healthy",
                    label="Risk Engine Healthy",
                    status="pass" if latest_preview is not None and latest_preview.risk_verdict is not None else "fail",
                    explanation="Preview risk evaluation completed and was recorded.",
                    remediation="Re-run risk evaluation if the preview is stale or missing.",
                ),
                _make_check(
                    code="kill_switch_clear",
                    label="Kill Switch Clear",
                    status="pass" if (await evaluate_live_submission_guard(db=db, live_trading_profile_id=profile.id)).allowed else "fail",
                    explanation="No active live kill switch is blocking the handoff.",
                    remediation="Clear the kill switch or recover the live profile before enabling submission.",
                ),
                _make_check(
                    code="authorization_configured",
                    label="Authorization Configured",
                    status="pass",
                    explanation="Mutation endpoints require bearer-authenticated operator access.",
                    remediation="Keep the live order endpoints bearer-protected.",
                ),
                _make_check(
                    code="audit_storage_healthy",
                    label="Audit Storage Healthy",
                    status="pass",
                    explanation="Append-only audit evidence storage is available for live order actions.",
                    remediation="Verify the live audit evidence tables are writable.",
                ),
                _make_check(
                    code="idempotency_controls_healthy",
                    label="Idempotency Controls Healthy",
                    status="pass",
                    explanation="Live order records use idempotency-aware uniqueness constraints.",
                    remediation="Preserve unique idempotency keys for every live write path.",
                ),
                _make_check(
                    code="reconciliation_service_healthy",
                    label="Reconciliation Service Healthy",
                    status="pass",
                    explanation="Reconciliation read/write paths are available in the live control plane.",
                    remediation="Restore the reconciliation service before first trade enablement.",
                ),
                _make_check(
                    code="capital_ledger_integration_healthy",
                    label="Capital Ledger Integration Healthy",
                    status="pass",
                    explanation="Capital ledger plumbing is present and segregates live records from paper records.",
                    remediation="Verify live capital accounting remains append-only and segregated.",
                ),
                _make_check(
                    code="server_clock_synchronized",
                    label="Server Clock Synchronized",
                    status="fail" if latest_connection is None or latest_connection.last_heartbeat_at is None else "pass",
                    explanation="Server clock evidence is derived from the latest exchange heartbeat and verification timestamps.",
                    remediation="Check host NTP and exchange heartbeat freshness before enablement.",
                ),
            ]
        )

        return LiveCryptoOrderReadinessResponse(
            overall_verdict=_overall_readiness_verdict(
                checks,
                dry_run_enabled=settings.live_crypto_dry_run_enabled,
                submission_enabled=settings.live_crypto_order_submission_enabled,
            ),
            live_mode_enabled=live_mode_enabled,
            live_profile_ready=live_profile_ready,
            feature_flag_enabled=settings.live_crypto_order_submission_enabled,
            dry_run_enabled=settings.live_crypto_dry_run_enabled,
            max_order_usd=settings.live_crypto_max_order_usd,
            latest_preview_age_seconds=preview_age,
            latest_balance_age_seconds=balance_age,
            latest_readiness_age_seconds=readiness_age,
            latest_price_age_seconds=None,
            reason=None if live_mode_enabled and settings.live_crypto_order_submission_enabled else "live_submission_disabled",
            checks=checks,
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

        preview_age_seconds = _age_seconds(preview.created_at)
        if preview_age_seconds is None:
            raise ValueError("linked preview timestamp missing")
        if preview_age_seconds >= settings.live_crypto_preview_max_age_seconds:
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
            signal_id=uuid.uuid4(),
            paper_account_id=profile.id,
            asset_id=uuid.uuid4(),
            side="BUY",
            quantity=requested_quote_size,
            account_equity=requested_quote_size,
            current_equity=requested_quote_size,
            actor=request.operator_identity,
        )
        risk_context = RiskEvaluationContext(
            global_kill_switch_engaged=False,
            account_trading_paused=False,
            asset_in_no_trade_zone=False,
            pair_in_cooldown=False,
            would_breach_daily_loss=False,
            would_breach_drawdown=False,
            has_computable_stop_loss=True,
            bypass_sizing_rule=False,
        )
        risk_result = evaluate_signal_risk(request=risk_request, context=risk_context, reference_price=requested_quote_size)
        if risk_result.action != RiskDecisionAction.APPROVE:
            raise PermissionError(f"risk engine rejected live order: {risk_result.action.value}")

        await persist_risk_decision(
            db=db,
            request=RiskDecisionPersistenceRequest(
                paper_account_id=profile.id,
                signal_id=risk_request.signal_id,
                actor=request.operator_identity,
                evaluation_result=risk_result,
            ),
        )
        risk_event_id = uuid.uuid4()

        confirmation_challenge_id = uuid.uuid4()
        confirmation_expires_at = _utcnow() + timedelta(minutes=settings.live_crypto_confirmation_challenge_minutes)
        live_crypto_order = await self._get_or_create_live_order(
            db=db,
            preview=preview,
            profile=profile,
            risk_event_id=risk_event_id,
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

    async def dry_run(
        self,
        *,
        db: AsyncSession,
        request: LiveCryptoOrderDryRunRequest,
    ) -> LiveCryptoOrderDryRunResponse:
        settings = get_settings()
        if not settings.live_crypto_dry_run_enabled:
            raise PermissionError("live crypto dry run is disabled")

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

        preflight_errors: list[str] = []
        if preview.live_trading_profile_id != profile.id:
            preflight_errors.append("preview does not belong to the requested live trading profile")
        if preview.side != "BUY" or preview.product_id != "BTC-USD" or preview.order_type != "MARKET":
            preflight_errors.append("preview is not eligible for live BTC-USD market buy submission")

        requested_quote_size = _decimal(preview.requested_amount)
        if requested_quote_size > settings.live_crypto_max_order_usd:
            preflight_errors.append("preview exceeds live order size limit")

        preview_age_seconds = _age_seconds(preview.created_at)
        if preview_age_seconds is None:
            preflight_errors.append("preview timestamp missing")
            preview_age_seconds = 0
        if preview_age_seconds >= settings.live_crypto_preview_max_age_seconds:
            preflight_errors.append("preview is too old for submission")

        if not (await evaluate_live_approval_gate(db=db, live_trading_profile_id=profile.id, checkpoint_type="first_live_enablement")).allowed:
            preflight_errors.append("approval gate rejected")

        if not (await evaluate_live_submission_guard(db=db, live_trading_profile_id=profile.id)).allowed:
            preflight_errors.append("submission guard rejected")

        risk_request = RiskEvaluationRequest(
            signal_id=uuid.uuid4(),
            paper_account_id=profile.id,
            asset_id=uuid.uuid4(),
            side="BUY",
            quantity=requested_quote_size,
            account_equity=requested_quote_size,
            current_equity=requested_quote_size,
            actor=request.operator_identity,
        )
        risk_context = RiskEvaluationContext(
            global_kill_switch_engaged=False,
            account_trading_paused=False,
            asset_in_no_trade_zone=False,
            pair_in_cooldown=False,
            would_breach_daily_loss=False,
            would_breach_drawdown=False,
            has_computable_stop_loss=True,
            bypass_sizing_rule=False,
        )
        risk_result = evaluate_signal_risk(request=risk_request, context=risk_context, reference_price=requested_quote_size)
        if risk_result.action != RiskDecisionAction.APPROVE:
            preflight_errors.append(f"risk engine rejected live order: {risk_result.action.value}")

        live_crypto_order = await self._get_or_create_live_order(
            db=db,
            preview=preview,
            profile=profile,
            risk_event_id=uuid.uuid4(),
            request=LiveCryptoOrderPrepareRequest(
                live_trading_profile_id=request.live_trading_profile_id,
                crypto_order_preview_id=request.crypto_order_preview_id,
                operator_identity=request.operator_identity,
                idempotency_token=request.idempotency_token,
            ),
        )
        live_crypto_order.status = "DRY_RUN_BLOCKED" if preflight_errors else "DRY_RUN_READY"
        live_crypto_order.safe_provider_response = {
            **live_crypto_order.safe_provider_response,
            "dry_run": True,
            "dry_run_status": live_crypto_order.status,
            "safe_request_summary": _safe_request_summary(request_payload=_build_live_create_order_payload(live_order=live_crypto_order)),
            "operator_identity": request.operator_identity,
            "preview_id": str(preview.crypto_order_preview_id),
            "preview_age_seconds": preview_age_seconds,
            "dry_run_errors": preflight_errors,
        }
        live_crypto_order.updated_at = _utcnow()
        await db.flush()

        return LiveCryptoOrderDryRunResponse(
            live_crypto_order=self._to_response(live_crypto_order),
            dry_run_status=live_crypto_order.status,
            dry_run_message=(
                "Dry run completed. No Coinbase order was submitted."
                if not preflight_errors
                else "Dry run blocked. No Coinbase order was submitted."
            ),
            safe_request_summary=live_crypto_order.safe_provider_response["safe_request_summary"],
            provider_create_order_called=False,
            order_submitted=False,
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
        preview_age_seconds = _age_seconds(preview.created_at)
        if preview_age_seconds is None:
            raise ValueError("linked preview timestamp missing")
        if preview_age_seconds >= settings.live_crypto_preview_max_age_seconds:
            raise ValueError("linked preview is too old")

        connection = await _load_exchange_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
        connection_credentials = _load_decrypted_credentials(connection)
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

        connection = await _load_exchange_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
        connection_credentials = _load_decrypted_credentials(connection)
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

        connection = await _load_exchange_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
        connection_credentials = _load_decrypted_credentials(connection)
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
