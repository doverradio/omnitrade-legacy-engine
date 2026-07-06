from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import AppError, InvalidRequestError, NotFoundError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.trade import Trade
from app.services.data.http_client import AsyncHTTPClient
from app.services.paper.alpaca_paper import submit_alpaca_paper_order
from app.services.paper.internal_sim import execute_internal_crypto_fill
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceRequest,
    RiskEvaluationRequest,
    evaluate_signal_risk,
    persist_risk_decision,
    resolve_execution_risk_context,
)


@dataclass(frozen=True, slots=True)
class SignalExecutionRequest:
    signal_id: uuid.UUID
    paper_account_id: uuid.UUID
    asset_id: uuid.UUID
    side: str
    quantity: Decimal
    actor: str = "system"
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class SignalExecutionResult:
    signal_id: uuid.UUID
    paper_account_id: uuid.UUID
    asset_id: uuid.UUID
    execution_status: str
    execution_venue: str
    is_paper: bool
    trade_id: uuid.UUID | None
    broker_order_id: str | None
    venue_status: str | None
    message: str


def _map_common_execution_status(*, venue: str, venue_status: str | None, filled_qty: Decimal | None) -> str:
    if venue == "internal_sim":
        return "executed"

    status = (venue_status or "").lower()
    filled = filled_qty or Decimal("0")

    if status in {"filled", "partially_filled"} and filled > 0:
        return "executed"
    if status in {
        "new",
        "accepted",
        "pending_new",
        "accepted_for_bidding",
        "partially_filled",
        "pending_replace",
    }:
        return "pending"
    if status in {"canceled", "expired", "replaced", "rejected", "stopped", "suspended"}:
        return "failed"

    return "pending"


async def orchestrate_paper_signal_execution(
    *,
    db: AsyncSession,
    request: SignalExecutionRequest,
) -> SignalExecutionResult:
    if request.side not in {"buy", "sell"}:
        await _audit_signal_execution_failure(
            db=db,
            request=request,
            reason="Invalid side",
            details={"side": request.side},
        )
        raise InvalidRequestError(message="Invalid side", details={"side": request.side})

    existing_trade = await db.scalar(
        select(Trade)
        .where(Trade.paper_account_id == request.paper_account_id)
        .where(Trade.signal_id == request.signal_id)
        .where(Trade.is_paper.is_(True))
        .order_by(Trade.executed_at.desc())
        .limit(1)
    )
    if existing_trade is not None:
        duplicate_audit = AuditLog(
            actor=request.actor,
            action="signal_execution_duplicate_skipped",
            entity_type="signal",
            entity_id=request.signal_id,
            before_state={
                "paper_account_id": str(request.paper_account_id),
                "asset_id": str(request.asset_id),
            },
            after_state={
                "trade_id": str(existing_trade.id),
                "execution_venue": existing_trade.execution_venue,
                "is_paper": existing_trade.is_paper,
            },
        )
        db.add(duplicate_audit)
        await db.commit()
        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status="duplicate",
            execution_venue=existing_trade.execution_venue,
            is_paper=True,
            trade_id=existing_trade.id,
            broker_order_id=None,
            venue_status="duplicate",
            message="Duplicate execution prevented for signal",
        )

    account = await db.scalar(select(PaperAccount).where(PaperAccount.id == request.paper_account_id))
    if account is None:
        await _audit_signal_execution_failure(
            db=db,
            request=request,
            reason="Paper account not found",
            details={"paper_account_id": str(request.paper_account_id)},
        )
        raise NotFoundError(
            message="Paper account not found",
            details={"paper_account_id": str(request.paper_account_id)},
        )

    asset = await db.scalar(select(Asset).where(Asset.id == request.asset_id))
    if asset is None:
        await _audit_signal_execution_failure(
            db=db,
            request=request,
            reason="Asset not found",
            details={"asset_id": str(request.asset_id)},
        )
        raise NotFoundError(message="Asset not found", details={"asset_id": str(request.asset_id)})

    reference_price = await _load_latest_reference_price(db=db, asset_id=request.asset_id)
    execution_risk_context = await resolve_execution_risk_context(
        db=db,
        paper_account=account,
        asset=asset,
    )
    risk_result = evaluate_signal_risk(
        request=RiskEvaluationRequest(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            side=request.side,
            quantity=request.quantity,
            account_equity=execution_risk_context.account_equity,
            max_position_size_pct=execution_risk_context.max_position_size_pct,
            min_order_notional=asset.min_order_notional,
            qty_step_size=asset.qty_step_size,
            supports_fractional=asset.supports_fractional,
            actor=request.actor,
            start_of_day_equity=execution_risk_context.start_of_day_equity,
            current_equity=execution_risk_context.current_equity,
            max_daily_loss_pct=execution_risk_context.max_daily_loss_pct,
            high_water_mark_equity=execution_risk_context.high_water_mark_equity,
            max_drawdown_pct=execution_risk_context.max_drawdown_pct,
            consecutive_losses_on_pair=execution_risk_context.consecutive_losses_on_pair,
            cooldown_after_losses=execution_risk_context.cooldown_after_losses,
            last_loss_at=execution_risk_context.last_loss_at,
            cooldown_duration_minutes=execution_risk_context.cooldown_duration_minutes,
            evaluation_time=execution_risk_context.evaluation_time,
            data_is_stale=execution_risk_context.data_is_stale,
            data_has_gaps=execution_risk_context.data_has_gaps,
            global_kill_switch_engaged_state=execution_risk_context.global_kill_switch_engaged_state,
            global_kill_switch_rearm_required=execution_risk_context.global_kill_switch_rearm_required,
            account_kill_switch_engaged_state=execution_risk_context.account_kill_switch_engaged_state,
            account_kill_switch_rearm_required=execution_risk_context.account_kill_switch_rearm_required,
        ),
        reference_price=reference_price,
    )
    await persist_risk_decision(
        db=db,
        request=RiskDecisionPersistenceRequest(
            paper_account_id=request.paper_account_id,
            signal_id=request.signal_id,
            actor=request.actor,
            evaluation_result=risk_result,
        ),
    )

    if risk_result.action == RiskDecisionAction.REJECT:
        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status="rejected",
            execution_venue="risk_engine",
            is_paper=True,
            trade_id=None,
            broker_order_id=None,
            venue_status="rejected",
            message=f"Signal rejected by risk engine: {risk_result.reason_code or 'risk_rejected'}",
        )

    approved_quantity = risk_result.approved_quantity

    if asset.asset_class == "crypto":
        if account.asset_class != "crypto":
            details = {"paper_account_id": str(account.id), "asset_class": account.asset_class}
            await _audit_signal_execution_failure(
                db=db,
                request=request,
                reason="Crypto signal cannot execute on non-crypto paper account",
                details=details,
                asset=asset,
            )
            raise InvalidRequestError(
                message="Crypto signal cannot execute on non-crypto paper account",
                details=details,
            )

        try:
            fill = await execute_internal_crypto_fill(
                db=db,
                paper_account_id=request.paper_account_id,
                asset_id=request.asset_id,
                side=request.side,
                quantity=approved_quantity,
                actor=request.actor,
                signal_id=request.signal_id,
            )
        except AppError as exc:
            await _audit_signal_execution_failure(
                db=db,
                request=request,
                reason=exc.message,
                details=exc.details,
                asset=asset,
            )
            raise

        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status="executed",
            execution_venue="internal_sim",
            is_paper=True,
            trade_id=fill.trade_id,
            broker_order_id=None,
            venue_status="filled",
            message="Signal executed via internal crypto simulator",
        )

    if asset.asset_class == "stock":
        if account.asset_class != "stock":
            details = {"paper_account_id": str(account.id), "asset_class": account.asset_class}
            await _audit_signal_execution_failure(
                db=db,
                request=request,
                reason="Stock signal cannot execute on non-stock paper account",
                details=details,
                asset=asset,
            )
            raise InvalidRequestError(
                message="Stock signal cannot execute on non-stock paper account",
                details=details,
            )

        if asset.exchange != "alpaca":
            details = {"exchange": asset.exchange}
            await _audit_signal_execution_failure(
                db=db,
                request=request,
                reason="Stock execution requires Alpaca exchange asset",
                details=details,
                asset=asset,
            )
            raise InvalidRequestError(
                message="Stock execution requires Alpaca exchange asset",
                details=details,
            )

        if not asset.supports_fractional and approved_quantity != approved_quantity.to_integral_value():
            details = {"asset_id": str(asset.id), "quantity": format(approved_quantity, "f")}
            await _audit_signal_execution_failure(
                db=db,
                request=request,
                reason="Asset does not support fractional quantity",
                details=details,
                asset=asset,
            )
            raise InvalidRequestError(
                message="Asset does not support fractional quantity",
                details=details,
            )

        try:
            settings = get_settings()
            async with AsyncHTTPClient() as client:
                venue_result = await submit_alpaca_paper_order(
                    settings=settings,
                    client=client,
                    symbol=asset.symbol,
                    side=request.side,
                    quantity=approved_quantity,
                    client_order_id=request.client_order_id,
                )
        except AppError as exc:
            await _audit_signal_execution_failure(
                db=db,
                request=request,
                reason=exc.message,
                details=exc.details,
                asset=asset,
            )
            raise

        trade: Trade | None = None
        if venue_result.filled_qty > 0 and venue_result.filled_avg_price is not None:
            trade = Trade(
                paper_account_id=request.paper_account_id,
                signal_id=request.signal_id,
                asset_id=request.asset_id,
                side=venue_result.side,
                quantity=venue_result.filled_qty,
                price=venue_result.filled_avg_price,
                fee=Decimal("0"),
                is_paper=True,
                execution_venue="alpaca_paper",
                executed_at=_parse_iso_timestamp(venue_result.filled_at) or datetime.now(timezone.utc),
            )
            db.add(trade)
            if hasattr(db, "flush"):
                await db.flush()

        trade_id = trade.id if trade is not None else None

        mapped_status = _map_common_execution_status(
            venue="alpaca_paper",
            venue_status=venue_result.status,
            filled_qty=venue_result.filled_qty,
        )

        signal_audit = AuditLog(
            actor=request.actor,
            action="signal_execution_orchestrated",
            entity_type="signal",
            entity_id=request.signal_id,
            before_state={
                "asset_class": asset.asset_class,
                "paper_account_id": str(request.paper_account_id),
                "asset_id": str(request.asset_id),
                "requested_quantity": format(request.quantity, "f"),
                "approved_quantity": format(approved_quantity, "f"),
            },
            after_state={
                "execution_venue": "alpaca_paper",
                "execution_status": mapped_status,
                "trade_id": str(trade_id) if trade_id is not None else None,
                "broker_order_id": venue_result.broker_order_id,
                "venue_status": venue_result.status,
                "is_paper": True,
            },
        )
        db.add(signal_audit)
        await db.commit()

        if trade is not None and hasattr(db, "refresh"):
            await db.refresh(trade)

        return SignalExecutionResult(
            signal_id=request.signal_id,
            paper_account_id=request.paper_account_id,
            asset_id=request.asset_id,
            execution_status=mapped_status,
            execution_venue="alpaca_paper",
            is_paper=True,
            trade_id=trade_id,
            broker_order_id=venue_result.broker_order_id,
            venue_status=venue_result.status,
            message="Signal submitted to Alpaca paper adapter",
        )

    raise InvalidRequestError(
        message="Unsupported asset class for paper execution orchestration",
        details={"asset_class": asset.asset_class},
    )


async def _load_latest_reference_price(*, db: AsyncSession, asset_id: uuid.UUID) -> Decimal | None:
    latest_close = await db.scalar(
        select(Candle.close)
        .where(Candle.asset_id == asset_id)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )
    if latest_close is None:
        return None

    return Decimal(str(latest_close))


async def _audit_signal_execution_failure(
    *,
    db: AsyncSession,
    request: SignalExecutionRequest,
    reason: str,
    details: dict[str, str | int | float | bool | None] | None,
    asset: Asset | None = None,
) -> None:
    failure_audit = AuditLog(
        actor=request.actor,
        action="signal_execution_failed",
        entity_type="signal",
        entity_id=request.signal_id,
        before_state={
            "paper_account_id": str(request.paper_account_id),
            "asset_id": str(request.asset_id),
            "asset_class": asset.asset_class if asset is not None else None,
            "side": request.side,
            "quantity": format(request.quantity, "f"),
        },
        after_state={
            "reason": reason,
            "details": details or {},
            "is_paper": True,
        },
    )
    db.add(failure_audit)
    await db.commit()


def _parse_iso_timestamp(raw_value: str | None) -> datetime | None:
    if raw_value is None:
        return None

    value = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
