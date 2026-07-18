from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config import get_settings
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidRequestError,
    NotFoundError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.exchange_connections.providers.base import ExchangeOrderSubmissionRequest
from app.services.exchange_connections.providers.registry import (
    get_exchange_provider,
    require_provider_capabilities,
)
from app.services.live.accounting_reconciliation import reconcile_live_order_and_fills
from app.services.live_crypto_orders import (
    _commit_if_supported,
    _decimal,
    _extract_usd_available_balance,
    _load_asset_for_product,
    _load_decrypted_credentials,
    _load_kill_switch_state,
    _normalize_exchange_environment,
    _quantize_usd,
    _record_audit,
    _redact_sensitive,
    _utcnow,
    _validate_quote_size,
)
from app.services.risk.risk_engine import (
    RiskDecisionAction,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
)
from app.services.risk.risk_monitor import get_risk_rules
from app.services.risk.risk_persistence import (
    RiskDecisionPersistenceRequest,
    persist_risk_decision,
)

from app.models.exchange_connection import ExchangeConnection
from app.schemas.instant_trades import (
    InstantTradeBuyRequest,
    InstantTradeReceiptResponse,
)


_TERMINAL_STATUSES = {"FILLED", "REJECTED", "CANCELLED", "PARTIALLY_FILLED"}


def _require_user_uuid(user_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(user_id)
    except ValueError as exc:
        raise UnauthorizedError(
            message="Instant trades require an authenticated user UUID token",
            details={"user_id": user_id},
        ) from exc


def _stable_client_order_id(*, request: InstantTradeBuyRequest) -> str:
    payload = "|".join(
        [
            str(request.paper_account_id),
            str(request.live_trading_profile_id),
            request.provider.strip().lower(),
            request.environment.strip().lower(),
            request.product.strip().upper(),
            format(request.quote_amount, "f"),
            request.idempotency_key.strip(),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"instant-{digest[:48]}"


def _preview_identity_for_order(client_order_id: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"instant-preview:{client_order_id}")


def _to_lifecycle_status(order: LiveCryptoOrder) -> str:
    if order.status == "FILLED":
        return "FILLED"
    if order.status == "RECONCILIATION_REQUIRED":
        return "RECONCILIATION_REQUIRED"
    if order.status in {"REJECTED", "CANCELLED"}:
        return "REJECTED"
    if order.status in {"SUBMISSION_PENDING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN"}:
        return "PENDING"
    if order.failure_code:
        return "FAILED"
    return "PENDING"


async def _with_db_timeout(coro, *, timeout_seconds: int):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except (TimeoutError, asyncio.TimeoutError) as exc:
        raise ServiceUnavailableError(
            message="Database operation timed out",
            details={"timeout_seconds": timeout_seconds},
        ) from exc


async def _ensure_db_checkout_ready(*, db: AsyncSession) -> None:
    # Force checkout + pre_ping + simple round-trip under the instant-trade timeout budget.
    await db.connection()
    await db.execute(text("SELECT 1"))


async def _load_owned_account(*, db: AsyncSession, paper_account_id: uuid.UUID, authenticated_user_id: str) -> PaperAccount:
    account = await db.scalar(select(PaperAccount).where(PaperAccount.id == paper_account_id).limit(1))
    if account is None:
        raise NotFoundError(message="Paper account not found", details={"paper_account_id": str(paper_account_id)})

    owner_uuid = _require_user_uuid(authenticated_user_id)
    if account.owner_user_id != owner_uuid:
        raise ForbiddenError(
            message="Paper account ownership mismatch",
            details={"paper_account_id": str(paper_account_id), "authenticated_user_id": authenticated_user_id},
        )
    return account


async def _load_profile(*, db: AsyncSession, profile_id: uuid.UUID) -> LiveTradingProfile:
    profile = await db.scalar(select(LiveTradingProfile).where(LiveTradingProfile.id == profile_id).limit(1))
    if profile is None:
        raise NotFoundError(message="Live trading profile not found", details={"live_trading_profile_id": str(profile_id)})
    return profile


async def _resolve_connection(*, db: AsyncSession, provider: str, environment: str) -> ExchangeConnection:
    rows = list(
        await db.scalars(
            select(ExchangeConnection)
            .where(ExchangeConnection.provider == provider)
            .where(ExchangeConnection.environment == environment)
            .where(ExchangeConnection.status == "connected")
            .where(ExchangeConnection.credentials_valid.is_(True))
        )
    )
    if len(rows) != 1:
        raise InvalidRequestError(
            message="Instant trade requires one unambiguous active exchange connection",
            details={
                "provider": provider,
                "environment": environment,
                "matched_connections": len(rows),
            },
        )
    return rows[0]


class InstantTradeService:
    async def buy(
        self,
        *,
        db: AsyncSession,
        request: InstantTradeBuyRequest,
        authenticated_user_id: str,
    ) -> InstantTradeReceiptResponse:
        settings = get_settings()
        if request.actor != authenticated_user_id:
            raise UnauthorizedError(message="Authenticated actor mismatch", details={})
        if not request.confirmation:
            raise InvalidRequestError(message="Explicit confirmation is required", details={"confirmation": False})

        db_timeout = int(getattr(settings, "instant_trade_db_timeout_seconds", 4))
        provider_timeout = int(getattr(settings, "instant_trade_provider_timeout_seconds", 8))

        await _with_db_timeout(
            _ensure_db_checkout_ready(db=db),
            timeout_seconds=db_timeout,
        )

        normalized_provider = request.provider.strip().lower()
        normalized_environment = _normalize_exchange_environment(request.environment)
        normalized_product = request.product.strip().upper()
        normalized_quote = _validate_quote_size(
            requested_quote_size=_decimal(request.quote_amount),
            max_order_usd=settings.live_crypto_max_order_usd,
        )
        client_order_id = _stable_client_order_id(request=request)

        account = await _with_db_timeout(
            _load_owned_account(
                db=db,
                paper_account_id=request.paper_account_id,
                authenticated_user_id=authenticated_user_id,
            ),
            timeout_seconds=db_timeout,
        )
        profile = await _with_db_timeout(
            _load_profile(db=db, profile_id=request.live_trading_profile_id),
            timeout_seconds=db_timeout,
        )
        if profile.paper_account_id != account.id:
            raise ForbiddenError(
                message="Live profile does not belong to requested paper account",
                details={
                    "paper_account_id": str(account.id),
                    "live_trading_profile_id": str(profile.id),
                },
            )

        existing = await _with_db_timeout(
            db.scalar(
                select(LiveCryptoOrder)
                .where(LiveCryptoOrder.client_order_id == client_order_id)
                .limit(1)
            ),
            timeout_seconds=db_timeout,
        )
        if existing is not None:
            if (
                existing.provider != normalized_provider
                or existing.environment != normalized_environment
                or existing.product_id != normalized_product
                or existing.side != "BUY"
                or _quantize_usd(_decimal(existing.requested_quote_size)) != normalized_quote
            ):
                raise ConflictError(
                    message="Idempotency key payload mismatch",
                    details={"internal_order_id": str(existing.live_crypto_order_id)},
                )
            await self._bounded_reconcile(
                db=db,
                order=existing,
                actor=request.actor,
                provider_timeout=provider_timeout,
                timeout_seconds=int(getattr(settings, "instant_trade_reconciliation_poll_timeout_seconds", 6)),
            )
            return self._build_receipt(existing)

        connection = await _with_db_timeout(
            _resolve_connection(
                db=db,
                provider=normalized_provider,
                environment=normalized_environment,
            ),
            timeout_seconds=db_timeout,
        )
        await _with_db_timeout(
            _load_asset_for_product(db=db, product_id=normalized_product),
            timeout_seconds=db_timeout,
        )

        available_usd = _extract_usd_available_balance(connection)
        if available_usd < normalized_quote:
            raise InvalidRequestError(
                message="Insufficient USD balance for requested quote amount",
                details={
                    "available_usd": format(available_usd, "f"),
                    "requested_quote_amount": format(normalized_quote, "f"),
                },
            )

        global_switch = await _with_db_timeout(
            _load_kill_switch_state(db=db, scope="global", account_id=None),
            timeout_seconds=db_timeout,
        )
        account_switch = await _with_db_timeout(
            _load_kill_switch_state(db=db, scope="account", account_id=account.id),
            timeout_seconds=db_timeout,
        )
        if bool(global_switch.engaged) or bool(global_switch.rearm_required):
            raise ForbiddenError(message="Global kill switch blocks trading", details={})
        if bool(account_switch.engaged) or bool(account_switch.rearm_required):
            raise ForbiddenError(message="Account kill switch blocks trading", details={})

        rules = await _with_db_timeout(
            get_risk_rules(db=db, account_id=account.id),
            timeout_seconds=db_timeout,
        )

        require_provider_capabilities(
            provider=normalized_provider,
            operation="instant_trade_buy",
            required=("preview_market_order", "create_order", "stable_client_order_id"),
            environment=normalized_environment,
        )
        provider = get_exchange_provider(normalized_provider, environment=normalized_environment)
        credentials = _load_decrypted_credentials(connection)

        try:
            preview = await asyncio.wait_for(
                provider.preview_market_order(
                    credentials=credentials,
                    environment=normalized_environment,
                    product_id=normalized_product,
                    side="BUY",
                    quote_size=normalized_quote,
                    base_size=None,
                    client_order_id=client_order_id,
                ),
                timeout=provider_timeout,
            )
        except TimeoutError as exc:
            raise ServiceUnavailableError(
                message="Provider preview timed out",
                details={"timeout_seconds": provider_timeout},
            ) from exc

        if not preview.success:
            raise InvalidRequestError(
                message="Provider preview rejected instant buy",
                details={
                    "reason": preview.failure_reason,
                    "warnings": preview.warning_messages,
                },
            )

        reference_price = preview.estimated_average_price or preview.best_ask
        if reference_price is None or reference_price <= Decimal("0"):
            raise InvalidRequestError(message="Provider preview missing reference price", details={})

        requested_base_quantity = normalized_quote / reference_price
        governed_capital = min(_decimal(account.current_cash_balance), available_usd)
        if governed_capital <= Decimal("0"):
            raise InvalidRequestError(message="No governed capital available", details={})

        risk_result = evaluate_signal_risk(
            request=RiskEvaluationRequest(
                signal_id=_preview_identity_for_order(client_order_id),
                paper_account_id=account.id,
                asset_id=_preview_identity_for_order(f"asset:{normalized_product}"),
                side="buy",
                quantity=requested_base_quantity,
                account_equity=governed_capital,
                max_position_size_pct=Decimal(str(rules.rules["max_position_size_pct"])),
                min_order_notional=Decimal("0.01"),
                qty_step_size=None,
                supports_fractional=True,
                start_of_day_equity=_decimal(account.starting_balance),
                current_equity=_decimal(account.current_cash_balance),
                max_daily_loss_pct=Decimal(str(rules.rules["max_daily_loss_pct"])),
                high_water_mark_equity=max(_decimal(account.starting_balance), _decimal(account.current_cash_balance)),
                max_drawdown_pct=Decimal(str(rules.rules["max_drawdown_pct"])),
                global_kill_switch_engaged_state=bool(global_switch.engaged),
                global_kill_switch_rearm_required=bool(global_switch.rearm_required),
                global_kill_switch_rearmed_by_human=(not bool(global_switch.rearm_required)),
                global_kill_switch_state_observed=True,
                account_kill_switch_engaged_state=bool(account_switch.engaged),
                account_kill_switch_rearm_required=bool(account_switch.rearm_required),
                account_kill_switch_rearmed_by_human=(not bool(account_switch.rearm_required)),
                account_kill_switch_state_observed=True,
                actor=request.actor,
            ),
            reference_price=reference_price,
            context=RiskEvaluationContext(
                global_kill_switch_engaged=bool(global_switch.engaged),
                account_trading_paused=False,
                asset_in_no_trade_zone=False,
                pair_in_cooldown=False,
                would_breach_daily_loss=False,
                would_breach_drawdown=False,
                has_computable_stop_loss=True,
                bypass_sizing_rule=False,
            ),
        )
        risk_persist = await _with_db_timeout(
            persist_risk_decision(
                db=db,
                request=RiskDecisionPersistenceRequest(
                    paper_account_id=account.id,
                    signal_id=_preview_identity_for_order(client_order_id),
                    actor=request.actor,
                    evaluation_result=risk_result,
                ),
            ),
            timeout_seconds=db_timeout,
        )
        if risk_result.action == RiskDecisionAction.REJECT:
            raise ForbiddenError(
                message="Risk engine blocked instant buy",
                details={"reason_code": risk_result.reason_code or "rejected"},
            )

        order = LiveCryptoOrder(
            crypto_order_preview_id=_preview_identity_for_order(client_order_id),
            exchange_connection_id=connection.exchange_connection_id,
            provider=normalized_provider,
            environment=normalized_environment,
            product_id=normalized_product,
            side="BUY",
            order_type="MARKET",
            requested_quote_size=normalized_quote,
            client_order_id=client_order_id,
            status="SUBMISSION_PENDING",
            risk_event_id=risk_persist.risk_event_id,
            decision_record_id=None,
            validation_run_id=None,
            provider_order_id=None,
            provider_status=None,
            submitted_at=_utcnow(),
            acknowledged_at=None,
            filled_at=None,
            cancelled_at=None,
            failure_code=None,
            failure_reason=None,
            safe_provider_response={
                "authority_classification": "USER_DIRECTED_INSTANT_TRADE",
                "paper_account_id": str(account.id),
                "live_trading_profile_id": str(profile.id),
                "actor": request.actor,
                "idempotency_key": request.idempotency_key.strip(),
                "requested_quote_amount": format(normalized_quote, "f"),
                "preview_summary": {
                    "estimated_base_size": None if preview.estimated_base_size is None else format(preview.estimated_base_size, "f"),
                    "estimated_fee": None if preview.estimated_fee is None else format(preview.estimated_fee, "f"),
                    "estimated_fee_currency": preview.estimated_fee_currency,
                    "estimated_average_price": None if preview.estimated_average_price is None else format(preview.estimated_average_price, "f"),
                    "warning_messages": preview.warning_messages,
                },
            },
            audit_correlation_id=uuid.uuid4(),
            operator_confirmation_id=None,
        )
        db.add(order)
        await _with_db_timeout(db.flush(), timeout_seconds=db_timeout)
        await _record_audit(
            db=db,
            action="INSTANT_BUY_ACCEPTED",
            actor=request.actor,
            entity_id=order.live_crypto_order_id,
            before_state=None,
            after_state={
                "status": order.status,
                "authority_classification": "USER_DIRECTED_INSTANT_TRADE",
                "provider": normalized_provider,
                "environment": normalized_environment,
                "product": normalized_product,
                "requested_quote_amount": format(normalized_quote, "f"),
            },
        )
        await _with_db_timeout(_commit_if_supported(db=db), timeout_seconds=db_timeout)

        request_payload = {
            "client_order_id": order.client_order_id,
            "product_id": order.product_id,
            "side": order.side,
            "order_configuration": {
                "market_market_ioc": {
                    "quote_size": format(order.requested_quote_size, "f"),
                    "rfq_disabled": True,
                }
            },
        }

        try:
            submission = await asyncio.wait_for(
                provider.submit_order(
                    credentials=credentials,
                    environment=normalized_environment,
                    request=ExchangeOrderSubmissionRequest(
                        product_id=order.product_id,
                        side=order.side,
                        order_type=order.order_type,
                        quote_size=order.requested_quote_size,
                        base_size=None,
                        client_order_id=order.client_order_id,
                        idempotency_key=order.client_order_id,
                        raw_payload=request_payload,
                    ),
                ),
                timeout=provider_timeout,
            )
        except TimeoutError:
            order.status = "RECONCILIATION_REQUIRED"
            order.failure_code = "provider_timeout"
            order.failure_reason = "Provider submit timeout"
            order.safe_provider_response = {
                **(order.safe_provider_response or {}),
                "create_order_payload": request_payload,
                "create_order_responded": False,
                "timeout_seconds": provider_timeout,
            }
            order.updated_at = _utcnow()
            await _record_audit(
                db=db,
                action="INSTANT_BUY_PROVIDER_TIMEOUT",
                actor=request.actor,
                entity_id=order.live_crypto_order_id,
                before_state=None,
                after_state={"status": order.status, "failure_code": order.failure_code},
            )
            await _with_db_timeout(db.flush(), timeout_seconds=db_timeout)
            await _with_db_timeout(_commit_if_supported(db=db), timeout_seconds=db_timeout)
            return self._build_receipt(order)

        order.safe_provider_response = {
            **(order.safe_provider_response or {}),
            "create_order_payload": request_payload,
            "create_order": _redact_sensitive(submission.raw_response),
            "create_order_headers": _redact_sensitive(submission.safe_headers),
            "create_order_responded": True,
        }

        if submission.classification == "rejected":
            order.status = "REJECTED"
            order.failure_code = "provider_rejected"
            order.failure_reason = submission.rejection.message if submission.rejection is not None else "Provider rejected"
        elif submission.classification == "ambiguous" and (submission.order is None or submission.order.provider_order_id is None):
            order.status = "RECONCILIATION_REQUIRED"
            order.failure_code = "provider_response_ambiguous"
            order.failure_reason = "Provider response was ambiguous"
        else:
            order.provider_order_id = None if submission.order is None else submission.order.provider_order_id
            order.provider_status = None if submission.order is None else submission.order.status
            order.status = "ACKNOWLEDGED" if order.provider_order_id else "RECONCILIATION_REQUIRED"
            order.acknowledged_at = _utcnow() if order.status == "ACKNOWLEDGED" else None
            order.failure_code = None
            order.failure_reason = None

        order.updated_at = _utcnow()
        await _record_audit(
            db=db,
            action="INSTANT_BUY_SUBMISSION_RECORDED",
            actor=request.actor,
            entity_id=order.live_crypto_order_id,
            before_state=None,
            after_state={
                "status": order.status,
                "provider_order_id": order.provider_order_id,
                "provider_status": order.provider_status,
                "failure_code": order.failure_code,
            },
        )
        await _with_db_timeout(db.flush(), timeout_seconds=db_timeout)
        await _with_db_timeout(_commit_if_supported(db=db), timeout_seconds=db_timeout)

        await self._bounded_reconcile(
            db=db,
            order=order,
            actor=request.actor,
            provider_timeout=provider_timeout,
            timeout_seconds=int(getattr(settings, "instant_trade_reconciliation_poll_timeout_seconds", 6)),
        )
        return self._build_receipt(order)

    async def read_receipt(
        self,
        *,
        db: AsyncSession,
        order_id: uuid.UUID,
        authenticated_user_id: str,
    ) -> InstantTradeReceiptResponse:
        order = await db.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == order_id).limit(1)
        )
        if order is None:
            raise NotFoundError(message="Instant order not found", details={"order_id": str(order_id)})

        paper_account_id_raw = (order.safe_provider_response or {}).get("paper_account_id")
        if paper_account_id_raw is None:
            raise ForbiddenError(message="Order is not an instant trade order", details={"order_id": str(order_id)})
        await _load_owned_account(
            db=db,
            paper_account_id=uuid.UUID(str(paper_account_id_raw)),
            authenticated_user_id=authenticated_user_id,
        )
        return self._build_receipt(order)

    async def adopt_into_autonomous_management(
        self,
        *,
        db: AsyncSession,
        order_id: uuid.UUID,
        actor: str,
        authenticated_user_id: str,
    ) -> InstantTradeReceiptResponse:
        if actor != authenticated_user_id:
            raise UnauthorizedError(message="Authenticated actor mismatch", details={})

        order = await db.scalar(
            select(LiveCryptoOrder).where(LiveCryptoOrder.live_crypto_order_id == order_id).limit(1)
        )
        if order is None:
            raise NotFoundError(message="Instant order not found", details={"order_id": str(order_id)})

        paper_account_id_raw = (order.safe_provider_response or {}).get("paper_account_id")
        if paper_account_id_raw is None:
            raise ForbiddenError(message="Order is not an instant trade order", details={"order_id": str(order_id)})

        await _load_owned_account(
            db=db,
            paper_account_id=uuid.UUID(str(paper_account_id_raw)),
            authenticated_user_id=authenticated_user_id,
        )

        reconciliation = (order.safe_provider_response or {}).get("reconciliation")
        if not isinstance(reconciliation, dict) or order.status not in {"FILLED", "PARTIALLY_FILLED"}:
            raise InvalidRequestError(
                message="Instant trade adoption is allowed only after reconciliation with fills",
                details={"order_status": order.status},
            )

        order.safe_provider_response = {
            **(order.safe_provider_response or {}),
            "instant_trade_adoption": {
                "adopted": True,
                "adopted_at": _utcnow().isoformat(),
                "adopted_by": actor,
            },
        }
        order.updated_at = _utcnow()
        await _record_audit(
            db=db,
            action="INSTANT_TRADE_ADOPTED_INTO_AUTONOMOUS_MANAGEMENT",
            actor=actor,
            entity_id=order.live_crypto_order_id,
            before_state=None,
            after_state={"status": order.status, "adopted": True},
        )
        await db.flush()
        await _commit_if_supported(db=db)
        return self._build_receipt(order)

    async def _bounded_reconcile(
        self,
        *,
        db: AsyncSession,
        order: LiveCryptoOrder,
        actor: str,
        provider_timeout: int,
        timeout_seconds: int,
    ) -> None:
        if order.status in _TERMINAL_STATUSES:
            return

        deadline = _utcnow() + timedelta(seconds=max(timeout_seconds, 0))
        while _utcnow() <= deadline:
            try:
                await asyncio.wait_for(
                    reconcile_live_order_and_fills(
                        db=db,
                        live_crypto_order_id=order.live_crypto_order_id,
                        operator_identity=actor,
                    ),
                    timeout=max(provider_timeout, 1),
                )
            except TimeoutError:
                order.status = "RECONCILIATION_REQUIRED"
                order.failure_code = order.failure_code or "reconciliation_timeout"
                order.failure_reason = order.failure_reason or "Reconciliation polling timed out"
                await db.flush()
                return
            except Exception:
                order.status = "RECONCILIATION_REQUIRED"
                order.failure_code = order.failure_code or "reconciliation_error"
                order.failure_reason = order.failure_reason or "Reconciliation failed"
                await db.flush()
                return

            if hasattr(db, "refresh"):
                await db.refresh(order)
            if order.status in _TERMINAL_STATUSES:
                return
            if order.status == "RECONCILIATION_REQUIRED":
                return
            if _utcnow() >= deadline:
                return
            await asyncio.sleep(1)

    def _build_receipt(self, order: LiveCryptoOrder) -> InstantTradeReceiptResponse:
        reconciliation = (order.safe_provider_response or {}).get("reconciliation")
        reconciliation_dict = reconciliation if isinstance(reconciliation, dict) else {}
        fees_raw = reconciliation_dict.get("fees")
        fees: dict[str, str] = {}
        if isinstance(fees_raw, dict):
            for key, value in fees_raw.items():
                fees[str(key)] = str(value)

        return InstantTradeReceiptResponse(
            internal_order_id=order.live_crypto_order_id,
            provider_order_id=order.provider_order_id,
            status=self._map_receipt_status(order=order),
            requested_amount=_quantize_usd(_decimal(order.requested_quote_size)),
            executed_quantity=None if reconciliation_dict.get("total_filled_quantity") is None else str(reconciliation_dict.get("total_filled_quantity")),
            average_fill_price=None if reconciliation_dict.get("weighted_average_fill_price") is None else str(reconciliation_dict.get("weighted_average_fill_price")),
            fees=fees,
            created_at=order.created_at,
            submitted_at=order.submitted_at,
            acknowledged_at=order.acknowledged_at,
            filled_at=order.filled_at,
            updated_at=order.updated_at,
            reconciliation_state=None if reconciliation_dict.get("normalized_status") is None else str(reconciliation_dict.get("normalized_status")),
            order={
                "live_crypto_order_id": str(order.live_crypto_order_id),
                "provider": order.provider,
                "environment": order.environment,
                "product": order.product_id,
                "side": order.side,
                "raw_status": order.status,
                "failure_code": order.failure_code,
                "failure_reason": order.failure_reason,
            },
        )

    def _map_receipt_status(self, *, order: LiveCryptoOrder) -> str:
        status = _to_lifecycle_status(order)
        return status


service = InstantTradeService()
