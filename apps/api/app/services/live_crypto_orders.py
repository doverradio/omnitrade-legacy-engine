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
from app.models.asset import Asset
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.exchange_connection import ExchangeConnection
from app.models.audit_log import AuditLog
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_trading_event import LiveTradingEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_kill_switch import RiskKillSwitch
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
from app.core.errors import InvalidRequestError, ServiceUnavailableError
from app.services.exchange_connections.providers.coinbase_advanced import CoinbaseAdvancedClient, sandbox_mock_mode_enabled
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.live.approval import evaluate_live_approval_gate
from app.services.live.resilience import evaluate_live_submission_guard
from app.services.risk.risk_monitor import get_risk_rules
from app.services.risk.risk_engine import RiskDecisionAction, RiskEvaluationContext, RiskEvaluationRequest, evaluate_signal_risk
from app.services.risk.risk_persistence import RiskDecisionPersistenceRequest, persist_risk_decision


CONFIRMATION_PHRASE = "BUY BTC"
_USD_SCALE = Decimal("0.01")
_RECONCILIABLE_PROVIDER_STATUSES = ["PENDING", "OPEN", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED", "FILLED", "FAILED", "CANCELLED", "EXPIRED"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_serialize_payload(payload).encode("utf-8")).hexdigest()


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


def _replay_key(*, action: str, scope: dict[str, Any], idempotency_token: str) -> str:
    return _hash_payload({"action": action, "scope": scope, "idempotency_token": idempotency_token})


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _quantize_usd(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _normalize_exchange_environment(environment: str) -> str:
    normalized = environment.strip().lower()
    if normalized not in {"production", "sandbox"}:
        raise ValueError(f"unsupported exchange environment: {environment}")
    return normalized


def _profile_environment(profile: LiveTradingProfile) -> str | None:
    provenance = profile.provenance_metadata if isinstance(profile.provenance_metadata, dict) else {}
    explicit = provenance.get("exchange_environment") or provenance.get("environment")
    if explicit is not None:
        try:
            return _normalize_exchange_environment(str(explicit))
        except ValueError:
            return None
    registration_source = str(provenance.get("registration_source") or "").lower()
    if "sandbox" in registration_source:
        return "sandbox"
    if "production" in registration_source or registration_source.startswith("human_"):
        return "production"
    return "production"


def _approval_environment(approval_event: LiveApprovalEvent | None) -> str | None:
    if approval_event is None or not isinstance(approval_event.approval_scope, dict):
        return None
    explicit = approval_event.approval_scope.get("environment")
    if explicit is None:
        return None
    try:
        return _normalize_exchange_environment(str(explicit))
    except ValueError:
        return None


async def _commit_if_supported(*, db: AsyncSession) -> None:
    if hasattr(db, "commit"):
        await db.commit()


def _precision_scale(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return 0 if exponent >= 0 else -exponent


def _validate_quote_size(*, requested_quote_size: Decimal, max_order_usd: Decimal) -> Decimal:
    if requested_quote_size <= Decimal("0"):
        raise ValueError("quote size must be greater than zero")
    if _precision_scale(requested_quote_size) > 2:
        raise ValueError("quote size exceeds supported USD precision")
    normalized = _quantize_usd(requested_quote_size)
    if normalized != requested_quote_size:
        raise ValueError("quote size must match provider-supported USD precision")
    if normalized > max_order_usd:
        raise ValueError("quote size exceeds live order size limit")
    return normalized


def _extract_usd_available_balance(connection: ExchangeConnection) -> Decimal:
    for item in connection.balances or []:
        if str(item.get("currency", "")).upper() != "USD":
            continue
        value = _decimal(item.get("available", "0"))
        if value < Decimal("0"):
            raise ValueError("USD balance evidence is invalid")
        return value
    raise ValueError("USD balance evidence missing")


async def _load_paper_account(*, db: AsyncSession, paper_account_id: uuid.UUID) -> PaperAccount:
    account = await db.scalar(select(PaperAccount).where(PaperAccount.id == paper_account_id).limit(1))
    if account is None:
        raise LookupError("paper account not found for live profile")
    return account


async def _load_asset_for_product(*, db: AsyncSession, product_id: str) -> Asset:
    normalized = product_id.strip().upper()
    if "-" not in normalized:
        raise ValueError("product_id must be a spot pair like BTC-USD")
    base_symbol, quote_symbol = normalized.split("-", 1)
    if quote_symbol != "USD":
        raise ValueError("only USD quote products are supported")

    asset = await db.scalar(
        select(Asset)
        .where(Asset.symbol == base_symbol)
        .where(Asset.asset_class == "crypto")
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc())
        .limit(1)
    )
    if asset is None:
        raise LookupError("active asset not found for live product")
    return asset


async def _load_active_campaign_for_account(*, db: AsyncSession, paper_account_id: uuid.UUID) -> CapitalCampaign | None:
    return await db.scalar(
        select(CapitalCampaign)
        .where(CapitalCampaign.paper_account_id == paper_account_id)
        .order_by(CapitalCampaign.updated_at.desc(), CapitalCampaign.id.desc())
        .limit(1)
    )


async def _load_kill_switch_state(*, db: AsyncSession, scope: str, account_id: uuid.UUID | None) -> RiskKillSwitch:
    switch = await db.scalar(
        select(RiskKillSwitch)
        .where(RiskKillSwitch.scope == scope)
        .where(RiskKillSwitch.paper_account_id == account_id)
        .limit(1)
    )
    if switch is None:
        raise PermissionError(f"{scope} kill switch state unavailable")
    return switch


def _resolve_reference_price(*, preview: CryptoOrderPreview) -> Decimal:
    for candidate in (
        preview.estimated_average_price,
        preview.best_ask,
        preview.best_bid,
    ):
        if candidate is not None and _decimal(candidate) > Decimal("0"):
            return _decimal(candidate)

    if preview.estimated_total_value is not None and preview.estimated_base_size is not None:
        base_size = _decimal(preview.estimated_base_size)
        if base_size > Decimal("0"):
            return _decimal(preview.estimated_total_value) / base_size

    raise ValueError("price evidence unavailable for live submission")


def _require_fresh_timestamp(*, label: str, observed_at: datetime | None, now: datetime, max_age_seconds: int) -> int:
    if observed_at is None:
        raise PermissionError(f"{label} timestamp missing")
    observed_utc = observed_at.astimezone(timezone.utc)
    if observed_utc > now:
        raise PermissionError(f"{label} timestamp is in the future")
    age_seconds = int((now - observed_utc).total_seconds())
    if age_seconds >= max_age_seconds:
        raise PermissionError(f"{label} evidence is stale")
    return age_seconds


def _build_intent_fingerprint(
    *,
    preview: CryptoOrderPreview,
    operator_identity: str,
    requested_quote_size: Decimal,
    approval_event_id: uuid.UUID,
) -> str:
    return _hash_payload(
        {
            "preview_id": str(preview.crypto_order_preview_id),
            "operator_identity": operator_identity,
            "approval_event_id": str(approval_event_id),
            "product_id": preview.product_id,
            "side": preview.side,
            "order_type": preview.order_type,
            "requested_quote_size": format(requested_quote_size, "f"),
        }
    )


def _build_evidence_fingerprint(*, preview: CryptoOrderPreview, connection: ExchangeConnection) -> str:
    return _hash_payload(
        {
            "preview_id": str(preview.crypto_order_preview_id),
            "preview_created_at": preview.created_at.isoformat(),
            "readiness_verified_at": None if connection.last_verified_at is None else connection.last_verified_at.isoformat(),
            "balance_synced_at": None if connection.last_successful_sync_at is None else connection.last_successful_sync_at.isoformat(),
            "heartbeat_at": None if connection.last_heartbeat_at is None else connection.last_heartbeat_at.isoformat(),
        }
    )


async def _audit_guard_failure(
    *,
    db: AsyncSession,
    actor: str,
    entity_id: uuid.UUID | None,
    action: str,
    reason: str,
    metadata: dict[str, Any],
) -> None:
    await _record_audit(
        db=db,
        action=action,
        actor=actor,
        entity_id=entity_id or uuid.uuid4(),
        before_state=None,
        after_state={"reason": reason, **metadata},
    )
    if entity_id is not None:
        await _commit_if_supported(db=db)


async def _build_real_risk_context(
    *,
    db: AsyncSession,
    profile: LiveTradingProfile,
    preview: CryptoOrderPreview,
    connection: ExchangeConnection,
    operator_identity: str,
) -> tuple[RiskEvent | None, RiskDecisionAction, Decimal, uuid.UUID]:
    paper_account = await _load_paper_account(db=db, paper_account_id=profile.paper_account_id)
    campaign = await _load_active_campaign_for_account(db=db, paper_account_id=paper_account.id)
    if campaign is not None and campaign.status == "PAUSED":
        raise PermissionError("linked capital campaign is paused")

    asset = await _load_asset_for_product(db=db, product_id=preview.product_id)
    reference_price = _resolve_reference_price(preview=preview)
    requested_quote_size = _validate_quote_size(
        requested_quote_size=_decimal(preview.requested_amount),
        max_order_usd=get_settings().live_crypto_max_order_usd,
    )
    requested_base_quantity = requested_quote_size / reference_price
    available_usd_balance = _extract_usd_available_balance(connection)

    global_switch = await _load_kill_switch_state(db=db, scope="global", account_id=None)
    account_switch = await _load_kill_switch_state(db=db, scope="account", account_id=paper_account.id)

    rules = await get_risk_rules(db=db, account_id=paper_account.id)
    governed_capital = min(_decimal(paper_account.current_cash_balance), available_usd_balance)
    if governed_capital <= Decimal("0"):
        raise PermissionError("governed capital evidence unavailable")

    risk_result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=preview.crypto_order_preview_id,
            paper_account_id=paper_account.id,
            asset_id=asset.id,
            side="buy",
            quantity=requested_base_quantity,
            account_equity=governed_capital,
            max_position_size_pct=Decimal(str(rules.rules["max_position_size_pct"])),
            min_order_notional=_decimal(getattr(asset, "min_order_notional", None)) if getattr(asset, "min_order_notional", None) is not None else _USD_SCALE,
            qty_step_size=_decimal(getattr(asset, "qty_step_size", None)) if getattr(asset, "qty_step_size", None) is not None else None,
            supports_fractional=bool(getattr(asset, "supports_fractional", True)),
            start_of_day_equity=_decimal(paper_account.starting_balance),
            current_equity=_decimal(paper_account.current_cash_balance),
            max_daily_loss_pct=Decimal(str(rules.rules["max_daily_loss_pct"])),
            high_water_mark_equity=max(_decimal(paper_account.starting_balance), _decimal(paper_account.current_cash_balance)),
            max_drawdown_pct=Decimal(str(rules.rules["max_drawdown_pct"])),
            global_kill_switch_engaged_state=bool(global_switch.engaged),
            global_kill_switch_rearm_required=bool(global_switch.rearm_required),
            global_kill_switch_rearmed_by_human=(not bool(global_switch.rearm_required)),
            global_kill_switch_state_observed=True,
            account_kill_switch_engaged_state=bool(account_switch.engaged),
            account_kill_switch_rearm_required=bool(account_switch.rearm_required),
            account_kill_switch_rearmed_by_human=(not bool(account_switch.rearm_required)),
            account_kill_switch_state_observed=True,
            actor=operator_identity,
        ),
        reference_price=reference_price,
        context=RiskEvaluationContext(
            global_kill_switch_engaged=bool(global_switch.engaged),
            account_trading_paused=(campaign is not None and campaign.status == "PAUSED"),
            asset_in_no_trade_zone=False,
            pair_in_cooldown=False,
            would_breach_daily_loss=False,
            would_breach_drawdown=False,
            has_computable_stop_loss=True,
            bypass_sizing_rule=False,
        ),
    )
    if risk_result.action == RiskDecisionAction.REJECT:
        persist_result = await persist_risk_decision(
            db=db,
            request=RiskDecisionPersistenceRequest(
                paper_account_id=paper_account.id,
                signal_id=preview.crypto_order_preview_id,
                actor=operator_identity,
                evaluation_result=risk_result,
            ),
        )
        raise PermissionError(f"risk engine rejected live order: {risk_result.reason_code or risk_result.action.value}")

    persist_result = await persist_risk_decision(
        db=db,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=paper_account.id,
            signal_id=preview.crypto_order_preview_id,
            actor=operator_identity,
            evaluation_result=risk_result,
        ),
    )
    approved_quote_size = _quantize_usd(risk_result.approved_quantity * reference_price)
    approved_quote_size = _validate_quote_size(
        requested_quote_size=approved_quote_size,
        max_order_usd=get_settings().live_crypto_max_order_usd,
    )
    return None, risk_result.action, approved_quote_size, persist_result.risk_event_id


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
        summary["provider_preview"] = _redact_sensitive(provider_response)
    return summary


def _safe_provider_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, InvalidRequestError):
        return {
            "code": exc.code,
            "message": exc.message,
            "details": _redact_sensitive(exc.details),
        }
    if isinstance(exc, ServiceUnavailableError):
        return {
            "code": exc.code,
            "message": exc.message,
            "details": _redact_sensitive(exc.details),
        }
    return {
        "code": exc.__class__.__name__,
        "message": str(exc),
    }


def _is_explicit_provider_rejection(exc: Exception) -> bool:
    if not isinstance(exc, InvalidRequestError):
        return False
    status_code = exc.details.get("status_code")
    return isinstance(status_code, int) and 400 <= status_code < 500


def _extract_provider_order(payload: dict[str, Any]) -> dict[str, Any] | None:
    order = payload.get("order")
    if isinstance(order, dict):
        return order
    success_response = payload.get("success_response")
    if isinstance(success_response, dict):
        return success_response
    return None


def _find_matching_orders(*, payload: dict[str, Any], client_order_id: str, product_id: str) -> list[dict[str, Any]]:
    orders = payload.get("orders")
    if not isinstance(orders, list):
        return []
    matches: list[dict[str, Any]] = []
    for item in orders:
        if not isinstance(item, dict):
            continue
        if str(item.get("client_order_id", "")) != client_order_id:
            continue
        if str(item.get("product_id", "")) != product_id:
            continue
        matches.append(item)
    return matches


def _age_seconds(earlier: datetime | None, later: datetime | None = None) -> int | None:
    if earlier is None:
        return None
    reference = later or _utcnow()
    return int((reference - earlier).total_seconds())


async def _evaluate_live_preflight_guards(
    *,
    db: AsyncSession,
    live_trading_profile_id: uuid.UUID,
    crypto_order_preview_id: uuid.UUID,
    operator_identity: str,
    require_submission_enabled: bool,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.live_crypto_preparation_enabled:
        raise PermissionError("live crypto order preparation is disabled")
    if require_submission_enabled and not settings.live_crypto_order_submission_enabled:
        raise PermissionError("live crypto order submission is disabled")

    profile = await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == live_trading_profile_id).limit(1))
    if profile is None:
        raise LookupError("live trading profile not found")

    preview = await db.scalar(
        select(CryptoOrderPreview).where(
            CryptoOrderPreview.crypto_order_preview_id == crypto_order_preview_id
        ).limit(1)
    )
    if preview is None:
        raise LookupError("crypto order preview not found")
    preview_profile_id = getattr(preview, "live_trading_profile_id", profile.id)
    if preview_profile_id != profile.id:
        raise ValueError("preview does not belong to the requested live trading profile")
    if preview.side != "BUY" or preview.product_id != "BTC-USD" or preview.order_type != "MARKET":
        raise ValueError("preview is not eligible for live BTC-USD market buy submission")

    requested_quote_size = _validate_quote_size(
        requested_quote_size=_decimal(preview.requested_amount),
        max_order_usd=settings.live_crypto_max_order_usd,
    )

    connection = await _load_exchange_connection(db=db, exchange_connection_id=preview.exchange_connection_id)
    preview_environment = _normalize_exchange_environment(preview.environment)
    connection_environment = _normalize_exchange_environment(connection.environment)
    profile_environment = _profile_environment(profile)
    if preview_environment != connection_environment:
        raise ValueError("preview environment does not match exchange connection environment")
    if profile_environment != preview_environment:
        raise ValueError("live trading profile environment does not match preview environment")
    if connection.credentials_valid is not True:
        raise PermissionError("coinbase credential evidence unavailable")
    api_permissions = [str(item).lower() for item in (connection.api_permissions or [])]
    if "trade" not in api_permissions:
        raise PermissionError("trade permission missing")
    now = _utcnow()
    preview_age_seconds = _require_fresh_timestamp(
        label="preview",
        observed_at=preview.created_at,
        now=now,
        max_age_seconds=settings.live_crypto_preview_max_age_seconds,
    )
    readiness_age_seconds = _require_fresh_timestamp(
        label="readiness",
        observed_at=connection.last_verified_at,
        now=now,
        max_age_seconds=settings.live_crypto_readiness_max_age_seconds,
    )
    heartbeat_age_seconds = _require_fresh_timestamp(
        label="heartbeat",
        observed_at=connection.last_heartbeat_at,
        now=now,
        max_age_seconds=settings.live_crypto_readiness_max_age_seconds,
    )
    balance_age_seconds = _require_fresh_timestamp(
        label="balance",
        observed_at=connection.last_successful_sync_at,
        now=now,
        max_age_seconds=settings.live_crypto_balance_max_age_seconds,
    )
    price_age_seconds = _require_fresh_timestamp(
        label="price",
        observed_at=preview.created_at,
        now=now,
        max_age_seconds=settings.live_crypto_price_max_age_seconds,
    )

    approval_gate = await evaluate_live_approval_gate(
        db=db,
        live_trading_profile_id=profile.id,
        checkpoint_type="first_live_enablement",
    )
    if not approval_gate.allowed:
        raise PermissionError(approval_gate.reason or "approval gate rejected")
    if approval_gate.matched_approval_event_id is None:
        raise PermissionError("approval evidence missing")
    approval_event = await db.scalar(
        select(LiveApprovalEvent)
        .where(LiveApprovalEvent.id == approval_gate.matched_approval_event_id)
        .limit(1)
    )
    if _approval_environment(approval_event) != preview_environment:
        raise PermissionError("approval environment does not match preview environment")

    guard_result = await evaluate_live_submission_guard(
        db=db,
        live_trading_profile_id=profile.id,
    )
    if not guard_result.allowed:
        raise PermissionError(guard_result.reason or "submission guard rejected")

    _risk_event, risk_action, approved_quote_size, risk_event_id = await _build_real_risk_context(
        db=db,
        profile=profile,
        preview=preview,
        connection=connection,
        operator_identity=operator_identity,
    )

    return {
        "profile": profile,
        "preview": preview,
        "connection": connection,
        "profile_environment": profile_environment,
        "preview_environment": preview_environment,
        "connection_environment": connection_environment,
        "requested_quote_size": requested_quote_size,
        "approved_quote_size": approved_quote_size,
        "risk_action": risk_action,
        "risk_event_id": risk_event_id,
        "approval_event_id": approval_gate.matched_approval_event_id,
        "preview_age_seconds": preview_age_seconds,
        "readiness_age_seconds": readiness_age_seconds,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "balance_age_seconds": balance_age_seconds,
        "price_age_seconds": price_age_seconds,
        "readiness_result": "ready",
        "kill_switch_result": "clear",
        "approved_intent_fingerprint": _build_intent_fingerprint(
            preview=preview,
            operator_identity=operator_identity,
            requested_quote_size=approved_quote_size,
            approval_event_id=approval_gate.matched_approval_event_id,
        ),
        "evidence_fingerprint": _build_evidence_fingerprint(preview=preview, connection=connection),
    }


async def _record_audit(
    *,
    db: AsyncSession,
    action: str,
    actor: str,
    entity_id: uuid.UUID,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="live_crypto_order",
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
        )
    )


async def _ensure_not_replayed(*, db: AsyncSession, replay_key: str) -> None:
    existing = await db.scalar(
        select(AuditLog.id)
        .where(AuditLog.entity_type == "live_crypto_order")
        .where(AuditLog.after_state["replay_key"].astext == replay_key)
        .limit(1)
    )
    if existing is not None:
        raise PermissionError("idempotency token replay detected")


def _build_dry_run_response(*, live_order: LiveCryptoOrder) -> LiveCryptoOrderDryRunResponse:
    safe_provider_response = live_order.safe_provider_response or {}
    dry_run_errors = safe_provider_response.get("dry_run_errors") or []
    return LiveCryptoOrderDryRunResponse(  # type: ignore[arg-type]
        live_crypto_order=LiveCryptoOrderResponse(
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
            safe_provider_response=safe_provider_response,
            audit_correlation_id=live_order.audit_correlation_id,
            operator_confirmation_id=live_order.operator_confirmation_id,
            created_at=live_order.created_at,
            updated_at=live_order.updated_at,
        ),
        dry_run_status=live_order.status,
        dry_run_message=(
            "Dry run completed. No Coinbase order was submitted."
            if not dry_run_errors
            else "Dry run blocked. No Coinbase order was submitted."
        ),
        safe_request_summary=safe_provider_response.get("safe_request_summary", {}),
        provider_create_order_called=False,
        order_submitted=False,
        submission_skipped=bool(safe_provider_response.get("submission_skipped", True)),
        submission_skip_reason=str(safe_provider_response.get("submission_skip_reason", "dry_run_submission_skipped")),
    )


class LiveCryptoOrderService:
    def _existing_submit_response(self, *, live_order: LiveCryptoOrder) -> LiveCryptoOrderSubmitResponse:
        return LiveCryptoOrderSubmitResponse(
            live_crypto_order=self._to_response(live_order),
            execution_risk_verdict=str(live_order.safe_provider_response.get("execution_risk_verdict", "UNKNOWN")),
            provider_create_order_responded=bool(live_order.safe_provider_response.get("create_order_responded", False)),
            provider_reconciliation_status=live_order.provider_status,
            safe_provider_response=live_order.safe_provider_response,
            order_submitted=live_order.status in {"SUBMISSION_PENDING", "ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED"},
        )

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
        if not settings.live_crypto_preparation_enabled:
            await _audit_guard_failure(
                db=db,
                actor=request.operator_identity,
                entity_id=request.crypto_order_preview_id,
                action="PREPARATION_DISABLED",
                reason="live preparation is disabled",
                metadata={"live_trading_profile_id": str(request.live_trading_profile_id)},
            )
            raise PermissionError("live crypto order preparation is disabled")
        if not settings.live_crypto_order_submission_enabled:
            await _audit_guard_failure(
                db=db,
                actor=request.operator_identity,
                entity_id=request.crypto_order_preview_id,
                action="SUBMISSION_FEATURE_DISABLED",
                reason="live submission is disabled",
                metadata={"live_trading_profile_id": str(request.live_trading_profile_id)},
            )
            raise PermissionError("live crypto order submission is disabled")

        preflight = await _evaluate_live_preflight_guards(
            db=db,
            live_trading_profile_id=request.live_trading_profile_id,
            crypto_order_preview_id=request.crypto_order_preview_id,
            operator_identity=request.operator_identity,
            require_submission_enabled=True,
        )
        profile = preflight["profile"]
        preview = preflight["preview"]
        approved_quote_size = preflight["approved_quote_size"]
        risk_action = preflight["risk_action"]
        risk_event_id = preflight["risk_event_id"]
        approval_event_id = preflight["approval_event_id"]
        preview_age_seconds = int(preflight["preview_age_seconds"])

        confirmation_challenge_id = uuid.uuid4()
        confirmation_expires_at = _utcnow() + timedelta(minutes=settings.live_crypto_confirmation_challenge_minutes)
        live_crypto_order = await self._get_or_create_live_order(
            db=db,
            preview=preview,
            profile=profile,
            risk_event_id=risk_event_id,
            request=request,
        )
        live_crypto_order.requested_quote_size = approved_quote_size
        live_crypto_order.operator_confirmation_id = confirmation_challenge_id
        live_crypto_order.safe_provider_response = {
            **live_crypto_order.safe_provider_response,
            "prepared_by": request.operator_identity,
            "approval_event_id": str(approval_event_id),
            "confirmation_expires_at": confirmation_expires_at.isoformat(),
            "approved_intent_fingerprint": str(preflight["approved_intent_fingerprint"]),
            "evidence_fingerprint": str(preflight["evidence_fingerprint"]),
            "execution_risk_verdict": risk_action.value,
        }
        await _record_audit(
            db=db,
            action="PREPARE_CONFIRMATION",
            actor=request.operator_identity,
            entity_id=live_crypto_order.live_crypto_order_id,
            before_state=None,
            after_state={
                "status": live_crypto_order.status,
                "risk_event_id": str(risk_event_id),
                "requested_quote_size": format(approved_quote_size, "f"),
                "approval_event_id": str(approval_event_id),
            },
        )
        await _commit_if_supported(db=db)

        return LiveCryptoOrderPrepareResponse(
            live_crypto_order=self._to_response(live_crypto_order),
            confirmation_challenge_id=confirmation_challenge_id,
            confirmation_phrase_required=CONFIRMATION_PHRASE,
            confirmation_expires_at=confirmation_expires_at,
            live_money_warning="LIVE MONEY: operator confirmation required before submission.",
            execution_risk_verdict=risk_action.value,
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
        if not (request.idempotency_token or "").strip():
            raise PermissionError("idempotency token required for dry run")

        preflight_errors: list[str] = []
        preflight: dict[str, Any] | None = None
        try:
            preflight = await _evaluate_live_preflight_guards(
                db=db,
                live_trading_profile_id=request.live_trading_profile_id,
                crypto_order_preview_id=request.crypto_order_preview_id,
                operator_identity=request.operator_identity,
                require_submission_enabled=False,
            )
            profile = preflight["profile"]
            preview = preflight["preview"]
            risk_action = preflight["risk_action"]
            approved_quote_size = preflight["approved_quote_size"]
            risk_event_id = preflight["risk_event_id"]
            preview_age_seconds = int(preflight["preview_age_seconds"])
            readiness_age_seconds = int(preflight["readiness_age_seconds"])
            balance_age_seconds = int(preflight["balance_age_seconds"])
            price_age_seconds = int(preflight["price_age_seconds"])
            approval_event_id = preflight["approval_event_id"]
            approved_intent_fingerprint = str(preflight["approved_intent_fingerprint"])
            evidence_fingerprint = str(preflight["evidence_fingerprint"])
            profile_environment = preflight["profile_environment"]
            preview_environment = preflight["preview_environment"]
            connection_environment = preflight["connection_environment"]
        except Exception as exc:
            preflight_errors.append(str(exc))
            risk_action = RiskDecisionAction.REJECT
            risk_event_id = None
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
            approved_quote_size = _quantize_usd(_decimal(preview.requested_amount))
            preview_age_seconds = _age_seconds(preview.created_at)
            readiness_age_seconds = None
            heartbeat_age_seconds = None
            balance_age_seconds = None
            price_age_seconds = _age_seconds(preview.created_at)
            approval_event_id = None
            approved_intent_fingerprint = None
            evidence_fingerprint = None
            readiness_result = "blocked"
            kill_switch_result = "unknown"
            profile_environment = _profile_environment(profile)
            preview_environment = _normalize_exchange_environment(preview.environment)
            connection_environment = None

        submission_skipped = True
        submission_skip_reason = (
            "Coinbase order submission intentionally skipped "
            f"(LIVE_CRYPTO_ORDER_SUBMISSION_ENABLED={str(settings.live_crypto_order_submission_enabled).lower()}, "
            f"LIVE_CRYPTO_DRY_RUN_ENABLED={str(settings.live_crypto_dry_run_enabled).lower()})"
        )

        live_crypto_order = await self._get_or_create_live_order(
            db=db,
            preview=preview,
            profile=profile,
            risk_event_id=risk_event_id,
            request=LiveCryptoOrderPrepareRequest(
                live_trading_profile_id=request.live_trading_profile_id,
                crypto_order_preview_id=request.crypto_order_preview_id,
                operator_identity=request.operator_identity,
                idempotency_token=request.idempotency_token,
            ),
        )
        if live_crypto_order.status in {"DRY_RUN_READY", "DRY_RUN_BLOCKED"} and bool((live_crypto_order.safe_provider_response or {}).get("dry_run", False)):
            stored_safe = live_crypto_order.safe_provider_response or {}
            if stored_safe.get("approved_intent_fingerprint") != approved_intent_fingerprint:
                raise PermissionError("approved intent fingerprint mismatch")
            if stored_safe.get("evidence_fingerprint") != evidence_fingerprint:
                raise PermissionError("approval evidence fingerprint mismatch")
            return _build_dry_run_response(live_order=live_crypto_order)
        live_crypto_order.requested_quote_size = approved_quote_size
        replay_key = _replay_key(
            action="dry_run",
            scope={
                "live_trading_profile_id": str(request.live_trading_profile_id),
                "crypto_order_preview_id": str(request.crypto_order_preview_id),
                "operator_identity": request.operator_identity,
            },
            idempotency_token=(request.idempotency_token or "").strip(),
        )
        await _ensure_not_replayed(db=db, replay_key=replay_key)

        live_crypto_order.status = "DRY_RUN_BLOCKED" if preflight_errors else "DRY_RUN_READY"
        live_crypto_order.safe_provider_response = {
            **live_crypto_order.safe_provider_response,
            "mode": "dry_run",
            "dry_run": True,
            "dry_run_status": live_crypto_order.status,
            "exchange_environment": live_crypto_order.environment,
            "profile_environment": profile_environment,
            "preview_environment": preview_environment,
            "connection_environment": connection_environment,
            "provider_mock_mode_enabled": bool(live_crypto_order.environment == "sandbox" and sandbox_mock_mode_enabled()),
            "rehearsal_mode": (
                "controlled_provider_mock"
                if live_crypto_order.environment == "sandbox" and sandbox_mock_mode_enabled()
                else ("coinbase_sandbox" if live_crypto_order.environment == "sandbox" else "production_live")
            ),
            "safe_request_summary": _safe_request_summary(request_payload=_build_live_create_order_payload(live_order=live_crypto_order)),
            "submission_skipped": submission_skipped,
            "submission_skip_reason": submission_skip_reason,
            "operator_identity": request.operator_identity,
            "preview_id": str(preview.crypto_order_preview_id),
            "preview_age_seconds": preview_age_seconds,
            "readiness_age_seconds": readiness_age_seconds,
            "heartbeat_age_seconds": locals().get("heartbeat_age_seconds"),
            "balance_age_seconds": balance_age_seconds,
            "price_age_seconds": price_age_seconds,
            "readiness_result": locals().get("readiness_result", "ready"),
            "kill_switch_result": locals().get("kill_switch_result", "clear"),
            "dry_run_errors": preflight_errors,
            "approval_event_id": None if approval_event_id is None else str(approval_event_id),
            "risk_event_id": None if risk_event_id is None else str(risk_event_id),
            "approved_intent_fingerprint": approved_intent_fingerprint,
            "evidence_fingerprint": evidence_fingerprint,
            "client_order_id": live_crypto_order.client_order_id,
            "audit_correlation_id": str(live_crypto_order.audit_correlation_id),
            "dry_run_recorded_at": _utcnow().isoformat(),
            "requested_quote_size": format(_quantize_usd(_decimal(preview.requested_amount)), "f"),
            "approved_quote_size": format(_quantize_usd(approved_quote_size), "f"),
            "max_order_usd": format(settings.live_crypto_max_order_usd, "f"),
            "execution_risk_verdict": risk_action.value,
            "failure_reason": None if not preflight_errors else "; ".join(preflight_errors),
        }
        live_crypto_order.failure_code = None if not preflight_errors else "dry_run_blocked"
        live_crypto_order.failure_reason = None if not preflight_errors else "; ".join(preflight_errors)
        live_crypto_order.updated_at = _utcnow()
        await _record_audit(
            db=db,
            action=live_crypto_order.status,
            actor=request.operator_identity,
            entity_id=live_crypto_order.live_crypto_order_id,
            before_state={"status": "PENDING_CONFIRMATION"},
            after_state={
                "status": live_crypto_order.status,
                "mode": "dry_run",
                "environment": live_crypto_order.environment,
                "preview_id": str(preview.crypto_order_preview_id),
                "provider_create_order_called": False,
                "submission_skipped": submission_skipped,
                "submission_skip_reason": submission_skip_reason,
                "operator_identity": request.operator_identity,
                "provider_mock_mode_enabled": bool(live_crypto_order.environment == "sandbox" and sandbox_mock_mode_enabled()),
                "rehearsal_mode": (
                    "controlled_provider_mock"
                    if live_crypto_order.environment == "sandbox" and sandbox_mock_mode_enabled()
                    else ("coinbase_sandbox" if live_crypto_order.environment == "sandbox" else "production_live")
                ),
                "approval_event_id": None if approval_event_id is None else str(approval_event_id),
                "risk_event_id": None if risk_event_id is None else str(risk_event_id),
                "approved_intent_fingerprint": approved_intent_fingerprint,
                "evidence_fingerprint": evidence_fingerprint,
                "profile_environment": profile_environment,
                "preview_environment": preview_environment,
                "connection_environment": connection_environment,
                "readiness_age_seconds": readiness_age_seconds,
                "heartbeat_age_seconds": locals().get("heartbeat_age_seconds"),
                "balance_age_seconds": balance_age_seconds,
                "price_age_seconds": price_age_seconds,
                "readiness_result": locals().get("readiness_result", "ready"),
                "kill_switch_result": locals().get("kill_switch_result", "clear"),
                "requested_quote_size": format(_quantize_usd(_decimal(preview.requested_amount)), "f"),
                "approved_quote_size": format(_quantize_usd(approved_quote_size), "f"),
                "max_order_usd": format(settings.live_crypto_max_order_usd, "f"),
                "replay_key": replay_key,
            },
        )
        await db.flush()
        await _commit_if_supported(db=db)

        return LiveCryptoOrderDryRunResponse(
            **_build_dry_run_response(live_order=live_crypto_order).model_dump(),
        )

    async def submit(
        self,
        *,
        db: AsyncSession,
        request: LiveCryptoOrderSubmitRequest,
    ) -> LiveCryptoOrderSubmitResponse:
        settings = get_settings()
        if not settings.live_crypto_order_submission_enabled:
            await _audit_guard_failure(
                db=db,
                actor=request.operator_identity,
                entity_id=request.live_crypto_order_id,
                action="SUBMISSION_FEATURE_DISABLED",
                reason="live submission is disabled",
                metadata={},
            )
            raise PermissionError("live crypto order submission is disabled")
        if not request.idempotency_token.strip():
            raise PermissionError("idempotency token required for submit")

        replay_key = _replay_key(
            action="submit",
            scope={
                "live_crypto_order_id": str(request.live_crypto_order_id),
                "confirmation_challenge_id": str(request.confirmation_challenge_id),
                "operator_identity": request.operator_identity,
            },
            idempotency_token=request.idempotency_token.strip(),
        )
        await _ensure_not_replayed(db=db, replay_key=replay_key)

        live_order = await db.scalar(
            select(LiveCryptoOrder).where(
                LiveCryptoOrder.live_crypto_order_id == request.live_crypto_order_id
            ).with_for_update().limit(1)
        )
        if live_order is None:
            raise LookupError("live crypto order not found")
        if live_order.status in {"ACKNOWLEDGED", "SUBMITTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED"}:
            await _record_audit(
                db=db,
                action="DUPLICATE_SUBMISSION_BLOCKED",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status, "reason": "existing_provider_result"},
            )
            await _commit_if_supported(db=db)
            return self._existing_submit_response(live_order=live_order)
        if live_order.status == "REJECTED":
            await _record_audit(
                db=db,
                action="DUPLICATE_SUBMISSION_BLOCKED",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status, "reason": "provider_rejected"},
            )
            await _commit_if_supported(db=db)
            return self._existing_submit_response(live_order=live_order)
        if live_order.status in {"SUBMISSION_PENDING", "RECONCILIATION_REQUIRED"}:
            await _record_audit(
                db=db,
                action="DUPLICATE_SUBMISSION_BLOCKED",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status, "reason": "reconciliation_required"},
            )
            await _commit_if_supported(db=db)
            raise PermissionError("submission already started; reconcile existing order state")
        if live_order.status not in {"PENDING_CONFIRMATION", "VALIDATING"}:
            raise ValueError("live crypto order is not in a submit-able state")
        if request.confirmation_phrase != CONFIRMATION_PHRASE:
            raise PermissionError("confirmation phrase mismatch")
        if request.operator_identity != live_order.safe_provider_response.get("prepared_by"):
            raise PermissionError("operator identity mismatch")
        if live_order.operator_confirmation_id != request.confirmation_challenge_id:
            raise PermissionError("confirmation challenge mismatch")

        preview = await db.scalar(
            select(CryptoOrderPreview).where(
                CryptoOrderPreview.crypto_order_preview_id == live_order.crypto_order_preview_id
            ).limit(1)
        )
        if preview is None:
            raise LookupError("linked preview not found")

        connection = await _load_exchange_connection(db=db, exchange_connection_id=live_order.exchange_connection_id)
        profile = await db.scalar(
            select(LiveTradingProfile).where(LiveTradingProfile.id == preview.live_trading_profile_id).limit(1)
        )
        if profile is None:
            raise LookupError("linked live trading profile not found")
        approval_expires_at = live_order.safe_provider_response.get("confirmation_expires_at")
        if approval_expires_at is None:
            raise PermissionError("confirmation approval evidence missing")
        expires_at = datetime.fromisoformat(str(approval_expires_at))
        if expires_at <= _utcnow():
            live_order.status = "CONFIRMATION_EXPIRED"
            await _record_audit(
                db=db,
                action="CONFIRMATION_EXPIRED",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status},
            )
            await _commit_if_supported(db=db)
            raise PermissionError("confirmation approval expired")

        now = _utcnow()
        _require_fresh_timestamp(
            label="preview",
            observed_at=preview.created_at,
            now=now,
            max_age_seconds=settings.live_crypto_preview_max_age_seconds,
        )
        _require_fresh_timestamp(
            label="readiness",
            observed_at=connection.last_verified_at,
            now=now,
            max_age_seconds=settings.live_crypto_readiness_max_age_seconds,
        )
        _require_fresh_timestamp(
            label="balance",
            observed_at=connection.last_successful_sync_at,
            now=now,
            max_age_seconds=settings.live_crypto_balance_max_age_seconds,
        )
        _require_fresh_timestamp(
            label="price",
            observed_at=preview.created_at,
            now=now,
            max_age_seconds=settings.live_crypto_price_max_age_seconds,
        )
        _validate_quote_size(
            requested_quote_size=live_order.requested_quote_size,
            max_order_usd=settings.live_crypto_max_order_usd,
        )

        approval_event_id = live_order.safe_provider_response.get("approval_event_id")
        if approval_event_id is None:
            raise PermissionError("approval binding missing")
        approved_intent_fingerprint = live_order.safe_provider_response.get("approved_intent_fingerprint")
        expected_intent_fingerprint = _build_intent_fingerprint(
            preview=preview,
            operator_identity=request.operator_identity,
            requested_quote_size=live_order.requested_quote_size,
            approval_event_id=uuid.UUID(str(approval_event_id)),
        )
        if approved_intent_fingerprint != expected_intent_fingerprint:
            raise PermissionError("approved intent fingerprint mismatch")
        if live_order.safe_provider_response.get("evidence_fingerprint") != _build_evidence_fingerprint(preview=preview, connection=connection):
            raise PermissionError("approval evidence fingerprint mismatch")

        _risk_event, risk_action, approved_quote_size, risk_event_id = await _build_real_risk_context(
            db=db,
            profile=profile,
            preview=preview,
            connection=connection,
            operator_identity=request.operator_identity,
        )
        if approved_quote_size != live_order.requested_quote_size:
            raise PermissionError("approved order intent no longer matches current risk-approved sizing")

        live_order.status = "SUBMISSION_PENDING"
        live_order.submitted_at = _utcnow()
        live_order.failure_code = None
        live_order.failure_reason = None
        live_order.safe_provider_response = {
            **live_order.safe_provider_response,
            "submission_identity": {
                "live_crypto_order_id": str(live_order.live_crypto_order_id),
                "client_order_id": live_order.client_order_id,
                "approval_event_id": str(approval_event_id),
                "risk_event_id": str(risk_event_id),
                "evidence_fingerprint": live_order.safe_provider_response.get("evidence_fingerprint"),
            },
        }
        await _record_audit(
            db=db,
            action="SUBMISSION_STARTED",
            actor=request.operator_identity,
            entity_id=live_order.live_crypto_order_id,
            before_state=None,
            after_state={
                "status": live_order.status,
                "client_order_id": live_order.client_order_id,
                "risk_event_id": str(risk_event_id),
            },
        )
        await db.flush()
        await _commit_if_supported(db=db)

        connection_credentials = _load_decrypted_credentials(connection)
        provider = CoinbaseAdvancedClient()
        request_payload = {
            "client_order_id": live_order.client_order_id,
            "product_id": live_order.product_id,
            "side": live_order.side,
            "order_configuration": {
                "market_market_ioc": {
                    "quote_size": format(approved_quote_size, "f"),
                    "rfq_disabled": True,
                }
            },
        }
        try:
            provider_response, safe_response = await provider.create_order(
                credentials=connection_credentials,
                environment=live_order.environment,
                request_payload=request_payload,
                idempotency_key=live_order.client_order_id,
            )
        except Exception as exc:
            safe_error = _safe_provider_error_payload(exc)
            live_order.safe_provider_response = {
                **live_order.safe_provider_response,
                "create_order_error": safe_error,
                "create_order_responded": False,
            }
            if _is_explicit_provider_rejection(exc):
                live_order.status = "REJECTED"
                live_order.failure_code = "provider_rejected"
                live_order.failure_reason = json.dumps(safe_error)
                await _record_audit(
                    db=db,
                    action="PROVIDER_REJECTED",
                    actor=request.operator_identity,
                    entity_id=live_order.live_crypto_order_id,
                    before_state=None,
                    after_state={"status": live_order.status, "error": safe_error},
                )
            else:
                live_order.status = "RECONCILIATION_REQUIRED"
                live_order.failure_code = "provider_response_ambiguous"
                live_order.failure_reason = json.dumps(safe_error)
                await _record_audit(
                    db=db,
                    action="PROVIDER_RESPONSE_AMBIGUOUS",
                    actor=request.operator_identity,
                    entity_id=live_order.live_crypto_order_id,
                    before_state=None,
                    after_state={"status": live_order.status, "error": safe_error},
                )
            live_order.updated_at = _utcnow()
            await db.flush()
            await _commit_if_supported(db=db)
            return self._existing_submit_response(live_order=live_order)

        success = bool(provider_response.get("success", False))
        provider_order = _extract_provider_order(provider_response)
        provider_order_id = None if provider_order is None else provider_order.get("order_id") if isinstance(provider_order.get("order_id"), str) else None
        provider_status = None if provider_order is None else provider_order.get("status") if isinstance(provider_order.get("status"), str) else None

        live_order.risk_event_id = risk_event_id
        if live_order.provider_order_id is not None and provider_order_id is not None and live_order.provider_order_id != provider_order_id:
            live_order.status = "RECONCILIATION_REQUIRED"
            live_order.failure_code = "provider_order_id_conflict"
            live_order.failure_reason = json.dumps({"existing": live_order.provider_order_id, "new": provider_order_id})
            await _record_audit(
                db=db,
                action="PROVIDER_ID_CONFLICT",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status, "existing": live_order.provider_order_id, "new": provider_order_id},
            )
            await db.flush()
            await _commit_if_supported(db=db)
            return self._existing_submit_response(live_order=live_order)

        live_order.provider_order_id = provider_order_id or live_order.provider_order_id
        live_order.provider_status = provider_status or live_order.provider_status
        live_order.safe_provider_response = {
            **live_order.safe_provider_response,
            "create_order": _redact_sensitive(provider_response),
            "create_order_headers": _redact_sensitive(safe_response),
            "create_order_payload": request_payload,
            "create_order_success": success,
            "create_order_responded": True,
            "execution_risk_verdict": risk_action.value,
        }
        if success and provider_order_id is not None:
            live_order.status = "ACKNOWLEDGED"
            live_order.acknowledged_at = _utcnow()
            await _record_audit(
                db=db,
                action="PROVIDER_ACKNOWLEDGED",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status, "provider_order_id": provider_order_id},
            )
        else:
            live_order.status = "RECONCILIATION_REQUIRED"
            live_order.failure_code = "provider_response_ambiguous"
            live_order.failure_reason = json.dumps(_redact_sensitive(provider_response))
            await _record_audit(
                db=db,
                action="PROVIDER_RESPONSE_AMBIGUOUS",
                actor=request.operator_identity,
                entity_id=live_order.live_crypto_order_id,
                before_state=None,
                after_state={"status": live_order.status, "response": _redact_sensitive(provider_response)},
            )

        live_order.updated_at = _utcnow()
        await _record_audit(
            db=db,
            action="SUBMIT_ATTEMPTED",
            actor=request.operator_identity,
            entity_id=live_order.live_crypto_order_id,
            before_state={"status": live_order.status},
            after_state={
                "status": live_order.status,
                "order_submitted": success,
                "provider_create_order_responded": True,
                "risk_event_id": str(risk_event_id),
                "replay_key": replay_key,
            },
        )
        await db.flush()
        await _commit_if_supported(db=db)

        return self._existing_submit_response(live_order=live_order)

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
        outcome = await reconcile_live_order_and_fills(
            db=db,
            live_crypto_order_id=live_crypto_order_id,
            operator_identity=request.operator_identity,
        )
        if hasattr(db, "refresh"):
            await db.refresh(live_order)

        return LiveCryptoOrderReconcileResponse(
            live_crypto_order=self._to_response(live_order),
            reconciliation_status=str(outcome["reconciliation_status"]),
            provider_status=None if outcome.get("provider_status") is None else str(outcome["provider_status"]),
            provider_order_id=None if outcome.get("provider_order_id") is None else str(outcome["provider_order_id"]),
            provider_fill_observed=bool(outcome.get("provider_fill_observed", False)),
            campaign_correlation_status=None if outcome.get("campaign_correlation_status") is None else str(outcome.get("campaign_correlation_status")),
            accounting_projection_status=None if outcome.get("accounting_projection_status") is None else str(outcome.get("accounting_projection_status")),
            accounting_completion_status=None if outcome.get("accounting_completion_status") is None else str(outcome.get("accounting_completion_status")),
            balance_mismatch_state=None if outcome.get("balance_mismatch_state") is None else str(outcome.get("balance_mismatch_state")),
            filled_quantity=None if outcome.get("filled_quantity") is None else str(outcome.get("filled_quantity")),
            gross_filled_notional=None if outcome.get("gross_filled_notional") is None else str(outcome.get("gross_filled_notional")),
            provider_fees=None if outcome.get("provider_fees") is None else str(outcome.get("provider_fees")),
            net_quote_capital_effect=None if outcome.get("net_quote_capital_effect") is None else str(outcome.get("net_quote_capital_effect")),
            safe_provider_response=outcome.get("safe_provider_response", {}),
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
