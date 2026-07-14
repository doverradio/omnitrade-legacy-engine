from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.audit_log import AuditLog
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_trading_profile import LiveTradingProfile
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.venue_commissioning_run import VenueCommissioningRun
from app.services.exchange_connections.providers.base import (
    ExchangeOrderSubmissionRequest,
    ExchangeProviderFill,
    ExchangeProviderOrder,
    ExchangeProviderRejection,
)
from app.services.exchange_connections.providers.registry import (
    get_exchange_provider,
    require_provider_capabilities,
)


logger = logging.getLogger(__name__)


_ALLOWED_HOLD_RANGE = (5, 120)
_ACTIVE_STATES = {
    "PREPARED",
    "ACTIVE",
    "BUY_SUBMISSION_PENDING",
    "BUY_RECONCILIATION_REQUIRED",
    "BUY_FILLED",
    "HOLDING",
    "SELL_DUE",
    "SELL_SUBMISSION_PENDING",
    "SELL_RECONCILIATION_REQUIRED",
    "SELL_FILLED",
    "RECONCILED",
}
_TERMINAL_STATES = {"COMPLETED", "ABORTED", "MANUAL_REVIEW_REQUIRED", "REVOKED", "EXPIRED"}
_RESUME_ELIGIBLE_STATES = {
    "BUY_SUBMISSION_PENDING",
    "BUY_RECONCILIATION_REQUIRED",
    "BUY_FILLED",
    "HOLDING",
    "SELL_DUE",
    "SELL_SUBMISSION_PENDING",
    "SELL_RECONCILIATION_REQUIRED",
    "SELL_FILLED",
    "RECONCILED",
}
_OPEN_LIVE_ORDER_STATUSES = {
    "PENDING_CONFIRMATION",
    "VALIDATING",
    "SUBMISSION_PENDING",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "RECONCILIATION_REQUIRED",
}

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PREPARED": {"ACTIVE", "REVOKED"},
    "ACTIVE": {"BUY_SUBMISSION_PENDING", "BUY_RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED", "REVOKED"},
    "BUY_SUBMISSION_PENDING": {"BUY_RECONCILIATION_REQUIRED", "BUY_FILLED", "HOLDING", "MANUAL_REVIEW_REQUIRED"},
    "BUY_RECONCILIATION_REQUIRED": {"BUY_RECONCILIATION_REQUIRED", "BUY_FILLED", "HOLDING", "MANUAL_REVIEW_REQUIRED"},
    "BUY_FILLED": {"HOLDING", "MANUAL_REVIEW_REQUIRED"},
    "HOLDING": {"HOLDING", "SELL_DUE", "MANUAL_REVIEW_REQUIRED"},
    "SELL_DUE": {"SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED", "MANUAL_REVIEW_REQUIRED"},
    "SELL_SUBMISSION_PENDING": {"SELL_RECONCILIATION_REQUIRED", "SELL_FILLED", "MANUAL_REVIEW_REQUIRED"},
    "SELL_RECONCILIATION_REQUIRED": {"SELL_RECONCILIATION_REQUIRED", "SELL_FILLED", "MANUAL_REVIEW_REQUIRED"},
    "SELL_FILLED": {"RECONCILED", "MANUAL_REVIEW_REQUIRED"},
    "RECONCILED": {"COMPLETED"},
    "COMPLETED": set(),
    "ABORTED": set(),
    "MANUAL_REVIEW_REQUIRED": set(),
    "REVOKED": set(),
    "EXPIRED": set(),
}


@dataclass(frozen=True)
class ReadinessCheck:
    label: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class ReadinessResult:
    would_activate_safely: bool
    exact_blocker: str | None
    checks: list[ReadinessCheck]
    existing_active_run: str


@dataclass(frozen=True)
class CommissioningConfig:
    provider: str
    product_id: str
    environment: str
    amount: Decimal
    hold_minutes: int


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _quantize(value: Decimal, scale: str = "0.00000001") -> Decimal:
    return value.quantize(Decimal(scale), rounding=ROUND_DOWN)


def _q_usd(value: Decimal) -> Decimal:
    return _quantize(value, "0.01")


def _normalize_status(value: str | None) -> str:
    token = (value or "").upper()
    if token in {"OPEN", "PENDING", "QUEUED", "CANCEL_QUEUED", "EDIT_QUEUED"}:
        return "OPEN"
    if token in {"FILLED", "CLOSED"}:
        return "FILLED"
    if token in {"PARTIALLY_FILLED", "PARTIAL"}:
        return "PARTIAL"
    if token in {"CANCELLED", "CANCELED", "EXPIRED"}:
        return "CANCELLED"
    if token in {"FAILED", "REJECTED"}:
        return "FAILED"
    return "UNKNOWN"


def _derive_blocker(checks: list[ReadinessCheck]) -> str | None:
    for item in checks:
        if item.status in {"FAIL", "DISABLED"}:
            return item.reason or item.label
    return None


def _commissioning_scope_valid(config: CommissioningConfig) -> bool:
    return (
        config.provider == "kraken_spot"
        and config.product_id == "BTC-USD"
        and config.environment == "production"
        and config.amount > Decimal("0")
        and config.amount <= Decimal("5.00")
    )


def _transition(run: VenueCommissioningRun, target: str) -> None:
    current = str(run.status)
    if current == target:
        return
    allowed = _ALLOWED_TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise RuntimeError(f"invalid_transition:{current}->{target}")
    run.status = target


def _mark_manual_review(*, run: VenueCommissioningRun) -> None:
    run.manual_intervention_required = True
    _transition(run, "MANUAL_REVIEW_REQUIRED")


def _is_explicitly_started(run: VenueCommissioningRun) -> bool:
    return run.activated_at is not None and run.started_at is not None


async def _record_audit(
    *,
    db: AsyncSession,
    actor: str,
    action: str,
    run: VenueCommissioningRun,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="venue_commissioning_run",
            entity_id=run.commissioning_run_id,
            before_state=before,
            after_state=after,
        )
    )


async def _load_connection(*, db: AsyncSession, config: CommissioningConfig) -> ExchangeConnection | None:
    return await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == config.provider)
        .where(ExchangeConnection.environment == config.environment)
        .order_by(ExchangeConnection.updated_at.desc())
        .limit(1)
    )


async def _load_profile_for_connection(*, db: AsyncSession, connection: ExchangeConnection | None) -> LiveTradingProfile | None:
    if connection is None:
        return None
    profiles = list(await db.scalars(select(LiveTradingProfile).order_by(LiveTradingProfile.updated_at.desc())))
    for item in profiles:
        meta = item.provenance_metadata if isinstance(item.provenance_metadata, dict) else {}
        if str(meta.get("provider") or "").lower() != connection.provider:
            continue
        env = str(meta.get("exchange_environment") or meta.get("environment") or "production").lower()
        if env == connection.environment:
            return item
    return profiles[0] if profiles else None


async def _active_run(*, db: AsyncSession) -> VenueCommissioningRun | None:
    return await db.scalar(
        select(VenueCommissioningRun)
        .where(VenueCommissioningRun.status.in_(sorted(_ACTIVE_STATES)))
        .order_by(VenueCommissioningRun.created_at.desc())
        .limit(1)
    )


async def _kill_switch_clear(*, db: AsyncSession, profile: LiveTradingProfile | None) -> bool:
    global_switch = await db.scalar(
        select(RiskKillSwitch)
        .where(RiskKillSwitch.scope == "global")
        .where(RiskKillSwitch.paper_account_id.is_(None))
        .limit(1)
    )
    if global_switch is not None and bool(global_switch.engaged):
        return False
    if profile is None:
        return True
    account_switch = await db.scalar(
        select(RiskKillSwitch)
        .where(RiskKillSwitch.scope == "account")
        .where(RiskKillSwitch.paper_account_id == profile.paper_account_id)
        .limit(1)
    )
    return not bool(account_switch and account_switch.engaged)


async def evaluate_readiness(*, db: AsyncSession, config: CommissioningConfig) -> ReadinessResult:
    settings = get_settings()
    checks: list[ReadinessCheck] = []
    checks.append(ReadinessCheck("Commissioning Scope", "PASS" if _commissioning_scope_valid(config) else "FAIL", "scope_mismatch"))
    checks.append(
        ReadinessCheck(
            "Commissioning Gate",
            "ENABLED" if settings.venue_commissioning_enabled else "DISABLED",
            None if settings.venue_commissioning_enabled else "venue_commissioning_gate_disabled",
        )
    )
    if config.hold_minutes < _ALLOWED_HOLD_RANGE[0] or config.hold_minutes > _ALLOWED_HOLD_RANGE[1]:
        checks.append(ReadinessCheck("Hold Range", "FAIL", "invalid_hold_minutes"))
    else:
        checks.append(ReadinessCheck("Hold Range", "PASS"))

    connection = await _load_connection(db=db, config=config)
    checks.append(
        ReadinessCheck(
            "Credentials",
            "PASS" if connection is not None and bool(connection.credentials_valid) else "FAIL",
            "invalid_credentials" if connection is None or not bool(connection.credentials_valid) else None,
        )
    )
    if connection is None:
        blocker = _derive_blocker(checks)
        return ReadinessResult(False, blocker, checks, "NONE")

    usd_available = Decimal("0")
    btc_available = Decimal("0")
    for item in connection.balances or []:
        currency = str(item.get("currency") or "").upper()
        amount = Decimal(str(item.get("available") or "0"))
        if currency == "USD":
            usd_available = amount
        if currency == "BTC":
            btc_available = amount

    checks.append(ReadinessCheck("USD Balance", "PASS" if usd_available >= config.amount else "FAIL", "insufficient_usd_balance"))
    checks.append(ReadinessCheck("BTC Baseline", "PASS" if btc_available >= Decimal("0") else "FAIL", "invalid_btc_baseline"))

    profile = await _load_profile_for_connection(db=db, connection=connection)
    kill_switch_clear = await _kill_switch_clear(db=db, profile=profile)
    checks.append(ReadinessCheck("Kill Switch", "PASS" if kill_switch_clear else "FAIL", None if kill_switch_clear else "kill_switch_engaged"))

    open_live = int(
        await db.scalar(
            select(LiveCryptoOrder.live_crypto_order_id)
            .where(LiveCryptoOrder.provider == config.provider)
            .where(LiveCryptoOrder.environment == config.environment)
            .where(LiveCryptoOrder.product_id == config.product_id)
            .where(LiveCryptoOrder.status.in_(sorted(_OPEN_LIVE_ORDER_STATUSES)))
            .limit(1)
        )
        is not None
    )
    checks.append(ReadinessCheck("Open Orders", "PASS" if open_live == 0 else "FAIL", None if open_live == 0 else "open_live_order_exists"))

    existing = await _active_run(db=db)
    existing_state = "NONE" if existing is None else existing.status
    checks.append(ReadinessCheck("Existing Active Run", "PASS" if existing is None else "FAIL", None if existing is None else "active_run_exists"))

    from app.services.live_crypto_orders import _load_decrypted_credentials

    provider = get_exchange_provider(config.provider, environment=config.environment)
    credentials = _load_decrypted_credentials(connection)
    product_ok = True
    min_ok = False
    precision_ok = False
    market_ok = False
    try:
        product_snapshot = await provider.fetch_product(credentials=credentials, environment=config.environment, product_id=config.product_id)
        product_ok = bool(product_snapshot.available and product_snapshot.trading_enabled)
        preview = await provider.preview_market_order(
            credentials=credentials,
            environment=config.environment,
            product_id=config.product_id,
            side="BUY",
            quote_size=config.amount,
            base_size=None,
            client_order_id=None,
        )
        min_ok = bool(preview.success)
        summary = preview.exchange_response_summary if isinstance(preview.exchange_response_summary, dict) else {}
        precision_ok = "pair_decimals" in summary and "lot_decimals" in summary
        market_ok = bool(preview.best_ask and preview.best_ask > Decimal("0"))
    except Exception:
        product_ok = False

    checks.append(ReadinessCheck("Product Metadata", "PASS" if product_ok else "FAIL", None if product_ok else "product_metadata_unavailable"))
    checks.append(ReadinessCheck("Kraken Minimum", "PASS" if min_ok else "FAIL", None if min_ok else "below_kraken_minimum"))
    checks.append(ReadinessCheck("Quantity Precision", "PASS" if precision_ok else "FAIL", None if precision_ok else "precision_unavailable"))
    checks.append(ReadinessCheck("Market Data", "PASS" if market_ok else "FAIL", None if market_ok else "market_data_unavailable"))

    blocker = _derive_blocker(checks)
    return ReadinessResult(blocker is None, blocker, checks, existing_state)


async def activate_run(*, db: AsyncSession, actor: str, config: CommissioningConfig, confirm: bool) -> VenueCommissioningRun:
    if not confirm:
        raise PermissionError("confirmation required")
    readiness = await evaluate_readiness(db=db, config=config)
    if not readiness.would_activate_safely:
        raise PermissionError(f"activation blocked: {readiness.exact_blocker}")

    existing = await _active_run(db=db)
    if existing is not None:
        return existing

    now = _utcnow()
    run = VenueCommissioningRun(
        status="ACTIVE",
        execution_purpose="VENUE_COMMISSIONING",
        commissioning_type="KRAKEN_FIRST_FLIGHT",
        provider=config.provider,
        environment=config.environment,
        product_id=config.product_id,
        max_quote_notional=Decimal("5.00"),
        max_buys=1,
        max_sells=1,
        strategy_id=None,
        strategy_signal=None,
        expected_profit="NOT_CLAIMED",
        hold_minutes=config.hold_minutes,
        buy_requested_quote_usd=_q_usd(config.amount),
        activated_by=actor,
        activated_at=now,
        state_payload={
            "activation": "explicit",
            "forced_buy": True,
            "execution_purpose": "VENUE_COMMISSIONING",
            "commissioning_type": "KRAKEN_FIRST_FLIGHT",
        },
    )
    db.add(run)
    await db.flush()
    await _record_audit(
        db=db,
        actor=actor,
        action="venue_commission_activate",
        run=run,
        before=None,
        after={"status": run.status, "provider": run.provider, "product_id": run.product_id},
    )
    await db.commit()
    return run


def _build_client_order_id(*, run_id: uuid.UUID, side: str) -> str:
    return f"kff-{str(run_id)[:8]}-{side.lower()}"


async def _reload_run_for_update(*, db: AsyncSession, run_id: uuid.UUID) -> VenueCommissioningRun:
    run = await db.scalar(
        select(VenueCommissioningRun)
        .where(VenueCommissioningRun.commissioning_run_id == run_id)
        .with_for_update()
        .limit(1)
    )
    if run is None:
        raise LookupError("commissioning run not found")
    return run


def _derive_sell_quantity(*, run: VenueCommissioningRun) -> Decimal | None:
    if run.buy_filled_base_btc is None:
        return None
    attributable = _quantize(run.buy_filled_base_btc)
    if attributable <= Decimal("0"):
        return None
    return attributable


async def _submit_order(
    *,
    db: AsyncSession,
    run: VenueCommissioningRun,
    side: str,
    amount: Decimal,
    base_size: Decimal | None,
) -> tuple[str, ExchangeProviderRejection | None, str | None, dict[str, Any]]:
    connection = await _load_connection(
        db=db,
        config=CommissioningConfig(
            provider=run.provider,
            product_id=run.product_id,
            environment=run.environment,
            amount=_q_usd(run.buy_requested_quote_usd),
            hold_minutes=run.hold_minutes,
        ),
    )
    if connection is None:
        return "REJECTED", ExchangeProviderRejection(code="connection_missing", message="connection missing"), None, {}

    from app.services.live_crypto_orders import _load_decrypted_credentials

    credentials = _load_decrypted_credentials(connection)
    provider = get_exchange_provider(run.provider, environment=run.environment)
    require_provider_capabilities(
        provider=run.provider,
        operation="venue_commission_submit",
        required=("create_order", "stable_client_order_id"),
        environment=run.environment,
    )
    client_order_id = _build_client_order_id(run_id=run.commissioning_run_id, side=side)
    request = ExchangeOrderSubmissionRequest(
        product_id=run.product_id,
        side=side,
        order_type="MARKET",
        quote_size=_q_usd(amount) if side == "BUY" else None,
        base_size=base_size if side == "SELL" else None,
        client_order_id=client_order_id,
        idempotency_key=client_order_id,
        raw_payload={"purpose": "VENUE_COMMISSIONING", "commissioning_type": "KRAKEN_FIRST_FLIGHT"},
    )
    result = await provider.submit_order(credentials=credentials, environment=run.environment, request=request)
    if result.classification == "success":
        return "SUCCESS", None, result.order.provider_order_id if result.order else None, {"raw": result.raw_response}
    if result.classification == "rejected":
        return "REJECTED", result.rejection, None, {"raw": result.raw_response}
    return "AMBIGUOUS", None, result.order.provider_order_id if result.order else None, {"raw": result.raw_response}


async def _reconcile_order(
    *,
    db: AsyncSession,
    run: VenueCommissioningRun,
    side: str,
) -> tuple[str, ExchangeProviderOrder | None, list[ExchangeProviderFill]]:
    connection = await _load_connection(
        db=db,
        config=CommissioningConfig(
            provider=run.provider,
            product_id=run.product_id,
            environment=run.environment,
            amount=_q_usd(run.buy_requested_quote_usd),
            hold_minutes=run.hold_minutes,
        ),
    )
    if connection is None:
        return "RECONCILIATION_REQUIRED", None, []

    from app.services.live_crypto_orders import _load_decrypted_credentials

    credentials = _load_decrypted_credentials(connection)
    provider = get_exchange_provider(run.provider, environment=run.environment)
    require_provider_capabilities(
        provider=run.provider,
        operation="venue_commission_reconcile",
        required=("order_lookup_history", "fill_lookup"),
        environment=run.environment,
    )

    provider_order_id = run.buy_provider_order_id if side == "BUY" else run.sell_provider_order_id
    client_order_id = run.buy_client_order_id if side == "BUY" else run.sell_client_order_id
    order = await provider.lookup_order(
        credentials=credentials,
        environment=run.environment,
        provider_order_id=provider_order_id,
        client_order_id=client_order_id,
        product_id=run.product_id,
    )
    if order is None:
        return "RECONCILIATION_REQUIRED", None, []
    if order.provider_order_id is None:
        return "RECONCILIATION_REQUIRED", order, []
    fills = await provider.list_fills(
        credentials=credentials,
        environment=run.environment,
        provider_order_id=order.provider_order_id,
    )
    return _normalize_status(order.status), order, fills


def _fill_aggregates(*, fills: list[ExchangeProviderFill]) -> tuple[Decimal, Decimal, Decimal, datetime | None]:
    total_base = Decimal("0")
    total_quote = Decimal("0")
    total_fees = Decimal("0")
    latest: datetime | None = None
    for fill in fills:
        total_base += fill.size
        total_quote += fill.size * fill.price
        if fill.fee is not None:
            total_fees += fill.fee.amount
        if fill.occurred_at is not None and (latest is None or fill.occurred_at > latest):
            latest = fill.occurred_at
    return total_base, total_quote, total_fees, latest


async def start_run(*, db: AsyncSession, actor: str, run_id: uuid.UUID, confirm: bool) -> VenueCommissioningRun:
    if not confirm:
        raise PermissionError("confirmation required")
    run = await _reload_run_for_update(db=db, run_id=run_id)
    if run.status in _TERMINAL_STATES:
        return run

    now = _utcnow()
    run.started_by = actor
    run.started_at = run.started_at or now

    if run.status == "ACTIVE":
        if run.buy_client_order_id is not None or run.buy_submitted_at is not None or run.buy_provider_order_id is not None:
            _transition(run, "BUY_RECONCILIATION_REQUIRED")
            run.updated_at = now
            await _record_audit(
                db=db,
                actor=actor,
                action="venue_commission_start",
                run=run,
                before=None,
                after={"status": run.status, "started_at": run.started_at.isoformat() if run.started_at else None},
            )
            await db.commit()
            return run
        run.buy_client_order_id = run.buy_client_order_id or _build_client_order_id(run_id=run.commissioning_run_id, side="BUY")
        run.buy_idempotency_key = run.buy_idempotency_key or run.buy_client_order_id
        run.buy_submitted_at = run.buy_submitted_at or now
        _transition(run, "BUY_SUBMISSION_PENDING")
        run.state_payload = {
            **(run.state_payload or {}),
            "buy_submit_intent": {
                "client_order_id": run.buy_client_order_id,
                "idempotency_key": run.buy_idempotency_key,
                "forced_buy": True,
            },
        }
        run.updated_at = now
        await _record_audit(
            db=db,
            actor=actor,
            action="venue_commission_buy_submission_pending",
            run=run,
            before=None,
            after={"status": run.status, "buy_client_order_id": run.buy_client_order_id},
        )
        await db.commit()

        outcome, rejection, provider_order_id, raw = await _submit_order(
            db=db,
            run=run,
            side="BUY",
            amount=_q_usd(run.buy_requested_quote_usd),
            base_size=None,
        )
        run = await _reload_run_for_update(db=db, run_id=run_id)
        run.buy_provider_order_id = provider_order_id or run.buy_provider_order_id
        run.state_payload = {**(run.state_payload or {}), "buy_submit": raw}
        if outcome == "SUCCESS":
            _transition(run, "BUY_SUBMISSION_PENDING")
        elif outcome == "AMBIGUOUS":
            _transition(run, "BUY_RECONCILIATION_REQUIRED")
        else:
            _mark_manual_review(run=run)
            run.state_payload = {
                **(run.state_payload or {}),
                "buy_rejection": None
                if rejection is None
                else {
                    "classification": rejection.code,
                    "provider_errors": (rejection.safe_details or {}).get("provider_errors")
                    or ([rejection.message] if rejection.message else []),
                    "http_status": (rejection.safe_details or {}).get("http_status"),
                    "provider_path": (rejection.safe_details or {}).get("provider_path"),
                    "message": rejection.message,
                    "raw_provider_response": (rejection.safe_details or {}).get("raw_provider_response")
                    or (raw.get("raw") if isinstance(raw, dict) else None),
                    "safe_details": rejection.safe_details,
                },
            }

    if run.status in {"BUY_SUBMISSION_PENDING", "BUY_RECONCILIATION_REQUIRED"}:
        order_status, order, fills = await _reconcile_order(db=db, run=run, side="BUY")
        total_base, total_quote, total_fees, latest_fill = _fill_aggregates(fills=fills)
        if total_base > Decimal("0"):
            run.buy_filled_base_btc = _quantize(total_base)
            run.buy_filled_quote_usd = _q_usd(total_quote)
            run.buy_fee_usd = _q_usd(total_fees)
            run.buy_avg_price_usd = _q_usd(total_quote / total_base)
            run.buy_filled_at = latest_fill
        if order is not None and order.provider_order_id and run.buy_provider_order_id is None:
            run.buy_provider_order_id = order.provider_order_id
        if order_status == "FILLED" and run.buy_filled_base_btc and run.buy_filled_base_btc > Decimal("0"):
            _transition(run, "BUY_FILLED")
            run.hold_started_at = run.buy_filled_at or now
            run.hold_due_at = run.hold_started_at + timedelta(minutes=run.hold_minutes)
            _transition(run, "HOLDING")
        elif order_status == "FAILED":
            _mark_manual_review(run=run)
        else:
            _transition(run, "BUY_RECONCILIATION_REQUIRED")

    if run.status == "BUY_FILLED":
        _transition(run, "HOLDING")

    if run.status == "HOLDING" and run.hold_due_at is not None and now >= run.hold_due_at:
        _transition(run, "SELL_DUE")

    if run.status == "SELL_DUE":
        if run.sell_client_order_id is None:
            sell_qty = _derive_sell_quantity(run=run)
            if sell_qty is None:
                _mark_manual_review(run=run)
                run.state_payload = {
                    **(run.state_payload or {}),
                    "sell_attribution": {
                        "status": "UNPROVEN",
                        "reason": "ATTRIBUTABLE_BUY_QUANTITY_NOT_RECONCILED",
                    },
                }
            else:
                run.sell_requested_base_btc = sell_qty
                run.sell_client_order_id = run.sell_client_order_id or _build_client_order_id(run_id=run.commissioning_run_id, side="SELL")
                run.sell_idempotency_key = run.sell_idempotency_key or run.sell_client_order_id
                run.sell_submitted_at = run.sell_submitted_at or now
                _transition(run, "SELL_SUBMISSION_PENDING")
                run.state_payload = {
                    **(run.state_payload or {}),
                    "sell_attribution": {
                        "status": "PROVEN",
                        "source": "buy_filled_base_btc",
                        "buy_filled_base_btc": format(run.buy_filled_base_btc, "f") if run.buy_filled_base_btc is not None else None,
                        "sell_requested_base_btc": format(sell_qty, "f"),
                    },
                    "sell_submit_intent": {
                        "client_order_id": run.sell_client_order_id,
                        "idempotency_key": run.sell_idempotency_key,
                    },
                }
                run.updated_at = now
                await _record_audit(
                    db=db,
                    actor=actor,
                    action="venue_commission_sell_submission_pending",
                    run=run,
                    before=None,
                    after={"status": run.status, "sell_client_order_id": run.sell_client_order_id},
                )
                await db.commit()

                outcome, rejection, provider_order_id, raw = await _submit_order(
                    db=db,
                    run=run,
                    side="SELL",
                    amount=Decimal("0"),
                    base_size=sell_qty,
                )
                run = await _reload_run_for_update(db=db, run_id=run_id)
                run.sell_provider_order_id = provider_order_id or run.sell_provider_order_id
                run.state_payload = {**(run.state_payload or {}), "sell_submit": raw}
                if outcome == "SUCCESS":
                    _transition(run, "SELL_SUBMISSION_PENDING")
                elif outcome == "AMBIGUOUS":
                    _transition(run, "SELL_RECONCILIATION_REQUIRED")
                else:
                    _mark_manual_review(run=run)
                    run.state_payload = {
                        **(run.state_payload or {}),
                        "sell_rejection": None
                        if rejection is None
                        else {
                            "classification": rejection.code,
                            "provider_errors": (rejection.safe_details or {}).get("provider_errors")
                            or ([rejection.message] if rejection.message else []),
                            "http_status": (rejection.safe_details or {}).get("http_status"),
                            "provider_path": (rejection.safe_details or {}).get("provider_path"),
                            "message": rejection.message,
                            "raw_provider_response": (rejection.safe_details or {}).get("raw_provider_response")
                            or (raw.get("raw") if isinstance(raw, dict) else None),
                            "safe_details": rejection.safe_details,
                        },
                    }
        else:
            run.duplicate_orders_detected = True
            _mark_manual_review(run=run)

    if run.status in {"SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED"}:
        order_status, order, fills = await _reconcile_order(db=db, run=run, side="SELL")
        total_base, total_quote, total_fees, latest_fill = _fill_aggregates(fills=fills)
        if total_base > Decimal("0"):
            run.sell_filled_base_btc = _quantize(total_base)
            run.sell_filled_quote_usd = _q_usd(total_quote)
            run.sell_fee_usd = _q_usd(total_fees)
            run.sell_avg_price_usd = _q_usd(total_quote / total_base)
            run.sell_filled_at = latest_fill
        if order is not None and order.provider_order_id and run.sell_provider_order_id is None:
            run.sell_provider_order_id = order.provider_order_id
        if order_status == "FILLED" and run.sell_filled_base_btc and run.sell_filled_base_btc > Decimal("0"):
            _transition(run, "SELL_FILLED")
        elif order_status == "FAILED":
            _mark_manual_review(run=run)
        else:
            _transition(run, "SELL_RECONCILIATION_REQUIRED")

    if run.status == "SELL_FILLED":
        buy_quote = run.buy_filled_quote_usd or Decimal("0")
        sell_quote = run.sell_filled_quote_usd or Decimal("0")
        buy_fee = run.buy_fee_usd or Decimal("0")
        sell_fee = run.sell_fee_usd or Decimal("0")
        gross = sell_quote - buy_quote
        fees = buy_fee + sell_fee
        run.gross_pnl_usd = _q_usd(gross)
        run.total_fees_usd = _q_usd(fees)
        run.net_realized_pnl_usd = _q_usd(gross - fees)
        if run.buy_filled_base_btc is not None and run.sell_filled_base_btc is not None:
            run.dust_base_btc = _quantize(max(Decimal("0"), run.buy_filled_base_btc - run.sell_filled_base_btc))
        run.ledger_matches_kraken = True
        _transition(run, "RECONCILED")

    if run.status == "RECONCILED":
        _transition(run, "COMPLETED")
        run.completed_at = now

    run.updated_at = now
    await _record_audit(
        db=db,
        actor=actor,
        action="venue_commission_start",
        run=run,
        before=None,
        after={"status": run.status, "started_at": run.started_at.isoformat() if run.started_at else None},
    )
    await db.commit()
    return run


async def revoke_run(*, db: AsyncSession, actor: str, run_id: uuid.UUID, confirm: bool) -> VenueCommissioningRun:
    if not confirm:
        raise PermissionError("confirmation required")
    run = await db.scalar(
        select(VenueCommissioningRun)
        .where(VenueCommissioningRun.commissioning_run_id == run_id)
        .with_for_update()
        .limit(1)
    )
    if run is None:
        raise LookupError("commissioning run not found")

    if run.status in {"PREPARED", "ACTIVE"}:
        _transition(run, "REVOKED")
        run.revoked_by = actor
        run.revoked_reason = "explicit_operator_revoke"
    elif run.status in _TERMINAL_STATES:
        return run
    else:
        _mark_manual_review(run=run)
        run.revoked_by = actor
        run.revoked_reason = "revoke_after_buy_requires_safe_exit"

    run.updated_at = _utcnow()
    await _record_audit(
        db=db,
        actor=actor,
        action="venue_commission_revoke",
        run=run,
        before=None,
        after={"status": run.status, "reason": run.revoked_reason},
    )
    await db.commit()
    return run


async def get_run(*, db: AsyncSession, run_id: uuid.UUID) -> VenueCommissioningRun:
    run = await db.scalar(
        select(VenueCommissioningRun)
        .where(VenueCommissioningRun.commissioning_run_id == run_id)
        .limit(1)
    )
    if run is None:
        raise LookupError("commissioning run not found")
    return run


async def resume_runs(*, db: AsyncSession, actor: str, limit: int = 10) -> int:
    now = _utcnow()
    rows = list(
        await db.scalars(
            select(VenueCommissioningRun)
            .where(VenueCommissioningRun.status.in_(sorted(_RESUME_ELIGIBLE_STATES)))
            .where(VenueCommissioningRun.activated_at.is_not(None))
            .where(VenueCommissioningRun.started_at.is_not(None))
            .where(VenueCommissioningRun.execution_purpose == "VENUE_COMMISSIONING")
            .where(VenueCommissioningRun.provider == "kraken_spot")
            .where(VenueCommissioningRun.environment == "production")
            .where(VenueCommissioningRun.product_id == "BTC-USD")
            .order_by(VenueCommissioningRun.updated_at.asc(), VenueCommissioningRun.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    if not rows:
        logger.info("venue_commission_resume_skipped reason=no_eligible_runs")
        return 0

    logger.info("venue_commission_resume_started run_count=%s", len(rows))
    processed = 0
    for run in rows:
        if not _is_explicitly_started(run):
            logger.info("venue_commission_resume_skipped reason=not_explicitly_started run_id=%s status=%s", run.commissioning_run_id, run.status)
            continue
        before = str(run.status)
        try:
            updated = await start_run(db=db, actor=actor, run_id=run.commissioning_run_id, confirm=True)
        except Exception:
            logger.exception("venue_commission_manual_review_required run_id=%s status=%s", run.commissioning_run_id, before)
            continue

        processed += 1
        after = str(updated.status)
        if before in {"BUY_SUBMISSION_PENDING", "BUY_RECONCILIATION_REQUIRED"} and after in {"BUY_FILLED", "HOLDING", "SELL_DUE", "SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED", "SELL_FILLED", "RECONCILED", "COMPLETED"}:
            logger.info("venue_commission_buy_reconciled run_id=%s status=%s", updated.commissioning_run_id, after)
        if after == "HOLDING":
            logger.info("venue_commission_holding run_id=%s hold_due_at=%s", updated.commissioning_run_id, updated.hold_due_at.isoformat() if updated.hold_due_at else None)
        if before != "SELL_DUE" and after == "SELL_DUE":
            logger.info("venue_commission_sell_due run_id=%s", updated.commissioning_run_id)
        if after in {"SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED"} and updated.sell_submitted_at is not None:
            logger.info("venue_commission_sell_submitted run_id=%s status=%s", updated.commissioning_run_id, after)
        if before in {"SELL_SUBMISSION_PENDING", "SELL_RECONCILIATION_REQUIRED"} and after in {"SELL_FILLED", "RECONCILED", "COMPLETED"}:
            logger.info("venue_commission_sell_reconciled run_id=%s status=%s", updated.commissioning_run_id, after)
        if after == "COMPLETED":
            logger.info("venue_commission_completed run_id=%s", updated.commissioning_run_id)
        if after == "MANUAL_REVIEW_REQUIRED":
            logger.info("venue_commission_manual_review_required run_id=%s", updated.commissioning_run_id)

    return processed


service = {
    "evaluate_readiness": evaluate_readiness,
    "activate_run": activate_run,
    "start_run": start_run,
    "resume_runs": resume_runs,
    "revoke_run": revoke_run,
    "get_run": get_run,
}
