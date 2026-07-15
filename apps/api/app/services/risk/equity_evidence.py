from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_equity_baseline import RiskEquityBaseline
from app.services.paper.accounting import build_account_snapshot


_UNRESOLVED_RECONCILIATION_STATUSES = {"open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch"}
_TERMINAL_LIVE_ORDER_STATUSES = {"DRY_RUN_READY", "DRY_RUN_BLOCKED", "FILLED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED", "COMPLETED"}
_INCONSISTENT_BALANCE_TOLERANCE = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class EquityValuationSnapshot:
    generated_at: datetime
    current_equity: Decimal
    cash_balance: Decimal
    position_value: Decimal
    latest_price_timestamp: datetime | None
    valuation_source: str
    valuation_state: str
    missing_price_assets: list[str]
    stale_price_assets: list[str]
    stale_cutoff: datetime


@dataclass(frozen=True, slots=True)
class EquityBaselineSnapshot:
    start_of_day_equity: Decimal
    high_water_mark_equity: Decimal
    start_of_day_source: str
    high_water_mark_source: str
    session_date: date
    baseline_state: str
    baseline_ready: bool


@dataclass(frozen=True, slots=True)
class EquityRiskEvidence:
    valuation: EquityValuationSnapshot
    baseline: EquityBaselineSnapshot
    unresolved_reconciliation_count: int
    unknown_provider_order_count: int
    ready: bool
    fail_closed_reason: str | None


async def _load_open_position_latest_price_points(*, db: AsyncSession, asset_ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[Decimal | None, datetime | None]]:
    result: dict[uuid.UUID, tuple[Decimal | None, datetime | None]] = {}
    for asset_id in asset_ids:
        row = await db.execute(
            select(Candle.close, Candle.close_time)
            .where(Candle.asset_id == asset_id)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        item = row.first()
        if item is None:
            result[asset_id] = (None, None)
            continue
        close = item[0]
        close_time = item[1]
        result[asset_id] = (Decimal(str(close)) if close is not None else None, close_time)
    return result


async def build_equity_valuation_snapshot(
    *,
    db: AsyncSession,
    paper_account: PaperAccount,
    max_price_age_seconds: int,
) -> EquityValuationSnapshot:
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(seconds=max(1, int(max_price_age_seconds)))

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=paper_account.id,
        starting_balance=paper_account.starting_balance,
    )

    open_positions = [item for item in snapshot.positions if item.quantity > 0]
    if not open_positions:
        return EquityValuationSnapshot(
            generated_at=now,
            current_equity=Decimal(snapshot.equity),
            cash_balance=Decimal(snapshot.cash_balance),
            position_value=Decimal(snapshot.position_value),
            latest_price_timestamp=None,
            valuation_source="paper_account_snapshot_cash_only",
            valuation_state="ready",
            missing_price_assets=[],
            stale_price_assets=[],
            stale_cutoff=stale_cutoff,
        )

    price_points = await _load_open_position_latest_price_points(db=db, asset_ids=[item.asset_id for item in open_positions])

    missing_price_assets: list[str] = []
    stale_price_assets: list[str] = []
    latest_price_timestamp: datetime | None = None

    for position in open_positions:
        _close, close_time = price_points.get(position.asset_id, (None, None))
        if close_time is None:
            missing_price_assets.append(position.symbol)
            continue
        if close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=timezone.utc)
        if close_time < stale_cutoff:
            stale_price_assets.append(position.symbol)
        if latest_price_timestamp is None or close_time > latest_price_timestamp:
            latest_price_timestamp = close_time

    valuation_state = "ready"
    if missing_price_assets:
        valuation_state = "missing_price_evidence"
    elif stale_price_assets:
        valuation_state = "stale_price_evidence"

    # Persisted current_cash_balance and computed cash must remain aligned.
    persisted_cash = Decimal(paper_account.current_cash_balance)
    computed_cash = Decimal(snapshot.cash_balance)
    if abs(persisted_cash - computed_cash) > _INCONSISTENT_BALANCE_TOLERANCE:
        valuation_state = "inconsistent_account_state"

    return EquityValuationSnapshot(
        generated_at=now,
        current_equity=Decimal(snapshot.equity),
        cash_balance=Decimal(snapshot.cash_balance),
        position_value=Decimal(snapshot.position_value),
        latest_price_timestamp=latest_price_timestamp,
        valuation_source="paper_account_snapshot_mark_to_market_candles",
        valuation_state=valuation_state,
        missing_price_assets=sorted(missing_price_assets),
        stale_price_assets=sorted(stale_price_assets),
        stale_cutoff=stale_cutoff,
    )


async def _count_reconciliation_uncertainty(*, db: AsyncSession, paper_account_id: uuid.UUID) -> tuple[int, int]:
    profile_ids = list(
        (
            await db.execute(
                select(LiveTradingProfile.id)
                .where(LiveTradingProfile.paper_account_id == paper_account_id)
                .order_by(LiveTradingProfile.created_at.desc(), LiveTradingProfile.id.desc())
            )
        ).scalars().all()
    )
    if not profile_ids:
        return (0, 0)

    unresolved_count = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(LiveReconciliationEvent)
                .where(LiveReconciliationEvent.live_trading_profile_id.in_(profile_ids))
                .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATUSES)))
            )
        )
        or 0
    )

    unknown_provider_order_count = int(
        (
            await db.scalar(
                select(func.count(func.distinct(LiveCryptoOrder.live_crypto_order_id)))
                .select_from(LiveReconciliationEvent)
                .join(
                    LiveCryptoOrder,
                    LiveCryptoOrder.live_crypto_order_id == LiveReconciliationEvent.live_crypto_order_id,
                )
                .where(LiveReconciliationEvent.live_trading_profile_id.in_(profile_ids))
                .where(func.lower(func.coalesce(LiveCryptoOrder.provider_status, "")) == "unknown")
                .where(LiveCryptoOrder.status.notin_(sorted(_TERMINAL_LIVE_ORDER_STATUSES)))
            )
        )
        or 0
    )
    return (unresolved_count, unknown_provider_order_count)


def _baseline_before_state(item: RiskEquityBaseline | None) -> dict[str, str] | None:
    if item is None:
        return None
    return {
        "session_date": item.session_date.isoformat(),
        "start_of_day_equity": format(Decimal(item.start_of_day_equity), "f"),
        "start_of_day_source": item.start_of_day_source,
        "high_water_mark_equity": format(Decimal(item.high_water_mark_equity), "f"),
        "high_water_mark_source": item.high_water_mark_source,
        "last_equity": format(Decimal(item.last_equity), "f"),
        "valuation_state": item.valuation_state,
    }


def _baseline_after_state(item: RiskEquityBaseline) -> dict[str, str]:
    return {
        "session_date": item.session_date.isoformat(),
        "start_of_day_equity": format(Decimal(item.start_of_day_equity), "f"),
        "start_of_day_source": item.start_of_day_source,
        "high_water_mark_equity": format(Decimal(item.high_water_mark_equity), "f"),
        "high_water_mark_source": item.high_water_mark_source,
        "last_equity": format(Decimal(item.last_equity), "f"),
        "valuation_state": item.valuation_state,
    }


async def _upsert_equity_baseline(
    *,
    db: AsyncSession,
    paper_account_id: uuid.UUID,
    valuation: EquityValuationSnapshot,
    actor: str,
) -> EquityBaselineSnapshot:
    baseline = await db.scalar(
        select(RiskEquityBaseline)
        .where(RiskEquityBaseline.paper_account_id == paper_account_id)
        .limit(1)
    )

    before_state = _baseline_before_state(baseline)
    now = valuation.generated_at
    today = now.date()
    baseline_ready = True
    baseline_state = "ready"
    should_audit = False

    if baseline is None:
        baseline = RiskEquityBaseline(
            paper_account_id=paper_account_id,
            session_date=today,
            start_of_day_equity=valuation.current_equity,
            start_of_day_source="bootstrap_first_equity_observation",
            start_of_day_recorded_at=now,
            high_water_mark_equity=valuation.current_equity,
            high_water_mark_source="bootstrap_first_equity_observation",
            high_water_mark_recorded_at=now,
            last_equity=valuation.current_equity,
            last_cash_balance=valuation.cash_balance,
            last_position_value=valuation.position_value,
            last_price_timestamp=valuation.latest_price_timestamp,
            valuation_source=valuation.valuation_source,
            valuation_state=valuation.valuation_state,
            updated_at=now,
        )
        db.add(baseline)
        await db.flush()
        baseline_ready = False
        baseline_state = "bootstrap_first_observation"
        should_audit = True
    elif baseline.session_date < today:
        baseline.session_date = today
        baseline.start_of_day_equity = Decimal(baseline.last_equity)
        baseline.start_of_day_source = "rolled_from_prior_last_equity"
        baseline.start_of_day_recorded_at = now

        if valuation.current_equity >= baseline.start_of_day_equity:
            baseline.high_water_mark_equity = valuation.current_equity
            baseline.high_water_mark_source = "session_high_water_from_current_equity"
        else:
            baseline.high_water_mark_equity = baseline.start_of_day_equity
            baseline.high_water_mark_source = "session_high_water_from_start_of_day"
        baseline.high_water_mark_recorded_at = now
        should_audit = True
    elif valuation.current_equity > Decimal(baseline.high_water_mark_equity):
        baseline.high_water_mark_equity = valuation.current_equity
        baseline.high_water_mark_source = "updated_from_current_equity_observation"
        baseline.high_water_mark_recorded_at = now
        should_audit = True

    baseline.last_equity = valuation.current_equity
    baseline.last_cash_balance = valuation.cash_balance
    baseline.last_position_value = valuation.position_value
    baseline.last_price_timestamp = valuation.latest_price_timestamp
    baseline.valuation_source = valuation.valuation_source
    baseline.valuation_state = valuation.valuation_state
    baseline.updated_at = now

    if should_audit:
        db.add(
            AuditLog(
                actor=actor,
                action="risk.equity_baseline_state.upsert",
                entity_type="risk_equity_baseline",
                entity_id=baseline.id,
                before_state=before_state,
                after_state=_baseline_after_state(baseline),
            )
        )

    return EquityBaselineSnapshot(
        start_of_day_equity=Decimal(baseline.start_of_day_equity),
        high_water_mark_equity=Decimal(baseline.high_water_mark_equity),
        start_of_day_source=baseline.start_of_day_source,
        high_water_mark_source=baseline.high_water_mark_source,
        session_date=baseline.session_date,
        baseline_state=baseline_state,
        baseline_ready=baseline_ready,
    )


async def resolve_equity_risk_evidence(
    *,
    db: AsyncSession,
    paper_account: PaperAccount,
    actor: str,
    max_price_age_seconds: int,
) -> EquityRiskEvidence:
    valuation = await build_equity_valuation_snapshot(
        db=db,
        paper_account=paper_account,
        max_price_age_seconds=max_price_age_seconds,
    )
    baseline = await _upsert_equity_baseline(
        db=db,
        paper_account_id=paper_account.id,
        valuation=valuation,
        actor=actor,
    )
    unresolved_count, unknown_order_count = await _count_reconciliation_uncertainty(
        db=db,
        paper_account_id=paper_account.id,
    )

    fail_closed_reason: str | None = None
    if valuation.valuation_state == "missing_price_evidence":
        fail_closed_reason = "missing_price_evidence"
    elif valuation.valuation_state == "stale_price_evidence":
        fail_closed_reason = "stale_price_evidence"
    elif valuation.valuation_state == "inconsistent_account_state":
        fail_closed_reason = "inconsistent_account_state"
    elif unresolved_count > 0:
        fail_closed_reason = "unresolved_reconciliation_state"
    elif unknown_order_count > 0:
        fail_closed_reason = "unknown_provider_order_state"
    elif not baseline.baseline_ready:
        fail_closed_reason = "baseline_bootstrap_required"

    return EquityRiskEvidence(
        valuation=valuation,
        baseline=baseline,
        unresolved_reconciliation_count=unresolved_count,
        unknown_provider_order_count=unknown_order_count,
        ready=fail_closed_reason is None,
        fail_closed_reason=fail_closed_reason,
    )
