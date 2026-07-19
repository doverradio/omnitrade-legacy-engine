from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.candle import Candle
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.models.risk_equity_baseline import RiskEquityBaseline
from app.services.exchange_connections.providers.registry import get_exchange_provider
from app.services.exchange_connections.service import get_decrypted_credentials_for_connection
from app.services.paper.accounting import build_account_snapshot


_UNRESOLVED_RECONCILIATION_STATUSES = {"open", "partially_filled", "reconciliation_required", "unknown", "conflict", "balance_mismatch"}
_TERMINAL_LIVE_ORDER_STATUSES = {"DRY_RUN_READY", "DRY_RUN_BLOCKED", "FILLED", "CANCELLED", "FAILED", "REJECTED", "EXPIRED", "COMPLETED"}
_INCONSISTENT_BALANCE_TOLERANCE = Decimal("0.00000001")
_SUPPORTED_INTERVAL_SUFFIXES = {"s": 1, "m": 60, "h": 3600, "d": 86400}


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
    price_evidence: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PositionPriceEvidence:
    symbol: str
    asset_id: uuid.UUID
    source: str
    product_id: str | None
    reference_price: Decimal | None
    observed_at: datetime | None
    stale_at: datetime | None
    state: str
    detail: str
    interval: str | None
    provider: str | None


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


def _parse_interval_seconds(interval: str | None) -> int | None:
    if interval is None:
        return None
    value = interval.strip().lower()
    if len(value) < 2:
        return None
    unit = value[-1]
    if unit not in _SUPPORTED_INTERVAL_SUFFIXES:
        return None
    amount_raw = value[:-1]
    if not amount_raw.isdigit():
        return None
    amount = int(amount_raw)
    if amount <= 0:
        return None
    return amount * _SUPPORTED_INTERVAL_SUFFIXES[unit]


def _normalize_profile_environment(raw: str | None) -> str:
    value = str(raw or "production").strip().lower()
    return value if value in {"production", "sandbox"} else "production"


def _normalize_product_for_quote(symbol: str) -> str | None:
    normalized = symbol.strip().upper()
    if not normalized:
        return None
    if "-" in normalized:
        base, quote = normalized.split("-", 1)
        quote = quote.strip().upper()
        if quote in {"USD", "USDT"}:
            return f"{base.strip().upper()}-USD"
        return None
    for suffix in ("USDT", "USD"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return f"{normalized[: -len(suffix)]}-USD"
    return None


async def _resolve_provider_quote_context(*, db: AsyncSession, paper_account_id: uuid.UUID) -> tuple[str, str, dict[str, str]] | None:
    profile = await db.scalar(
        select(LiveTradingProfile)
        .where(LiveTradingProfile.paper_account_id == paper_account_id)
        .order_by(LiveTradingProfile.created_at.desc(), LiveTradingProfile.id.desc())
        .limit(1)
    )
    if profile is None:
        return None

    provenance = profile.provenance_metadata if isinstance(profile.provenance_metadata, dict) else {}
    provider = str(provenance.get("provider") or "").strip().lower()
    if not provider:
        return None
    environment = _normalize_profile_environment(str(provenance.get("exchange_environment") or provenance.get("environment") or "production"))
    connection = await db.scalar(
        select(ExchangeConnection)
        .where(ExchangeConnection.provider == provider)
        .where(ExchangeConnection.environment == environment)
        .order_by(ExchangeConnection.created_at.desc(), ExchangeConnection.exchange_connection_id.desc())
        .limit(1)
    )
    if connection is None or not bool(connection.credentials_valid):
        return None

    credentials = get_decrypted_credentials_for_connection(connection)
    if not credentials.get("api_key") or not credentials.get("api_secret"):
        return None

    return (provider, environment, credentials)


async def _load_open_position_latest_candle_points(
    *,
    db: AsyncSession,
    asset_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[Decimal | None, datetime | None, str | None, str | None]]:
    result: dict[uuid.UUID, tuple[Decimal | None, datetime | None, str | None, str | None]] = {}
    for asset_id in asset_ids:
        row = await db.execute(
            select(Candle.close, Candle.close_time, Candle.interval, Candle.source)
            .where(Candle.asset_id == asset_id)
            .order_by(Candle.open_time.desc())
            .limit(1)
        )
        item = row.first()
        if item is None:
            result[asset_id] = (None, None, None, None)
            continue
        close, close_time, interval, source = item
        result[asset_id] = (Decimal(str(close)) if close is not None else None, close_time, interval, source)
    return result


async def _load_open_position_price_evidence_from_provider(
    *,
    provider: str,
    environment: str,
    credentials: dict[str, str],
    product_id: str,
) -> tuple[Decimal | None, datetime | None, str | None]:
    client = get_exchange_provider(provider, environment=environment)
    evidence = await client.fetch_price_evidence(
        credentials=credentials,
        environment=environment,
        product_id=product_id,
    )
    return evidence.reference_price, evidence.observed_at, evidence.source_endpoint


def _serialize_price_evidence(item: PositionPriceEvidence) -> dict[str, Any]:
    return {
        "symbol": item.symbol,
        "asset_id": str(item.asset_id),
        "source": item.source,
        "provider": item.provider,
        "product_id": item.product_id,
        "reference_price": None if item.reference_price is None else format(item.reference_price, "f"),
        "observed_at": item.observed_at,
        "stale_at": item.stale_at,
        "state": item.state,
        "detail": item.detail,
        "interval": item.interval,
    }


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
            price_evidence=[],
        )

    provider_context = await _resolve_provider_quote_context(db=db, paper_account_id=paper_account.id)
    provider_price_points: dict[uuid.UUID, tuple[Decimal | None, datetime | None, str | None]] = {}
    provider_failures: dict[uuid.UUID, str] = {}
    if provider_context is not None:
        provider, environment, credentials = provider_context
        for position in open_positions:
            product_id = _normalize_product_for_quote(position.symbol)
            if product_id is None:
                provider_failures[position.asset_id] = "unsupported_symbol_for_provider_quote"
                continue
            try:
                provider_price_points[position.asset_id] = await _load_open_position_price_evidence_from_provider(
                    provider=provider,
                    environment=environment,
                    credentials=credentials,
                    product_id=product_id,
                )
            except Exception as exc:  # pragma: no cover - exercised through monkeypatched provider failure tests
                provider_failures[position.asset_id] = str(exc)

    candle_points = await _load_open_position_latest_candle_points(db=db, asset_ids=[item.asset_id for item in open_positions])

    missing_price_assets: list[str] = []
    stale_price_assets: list[str] = []
    latest_price_timestamp: datetime | None = None
    evidence_entries: list[PositionPriceEvidence] = []
    source_kinds: set[str] = set()

    for position in open_positions:
        product_id = _normalize_product_for_quote(position.symbol)
        provider_quote = provider_price_points.get(position.asset_id)
        provider_failure = provider_failures.get(position.asset_id)

        if provider_quote is not None:
            reference_price, observed_at, source_endpoint = provider_quote
            observed_utc = observed_at if observed_at is None else observed_at.astimezone(timezone.utc)
            stale_at = None if observed_utc is None else observed_utc + timedelta(seconds=max(1, int(max_price_age_seconds)))
            if reference_price is None or reference_price <= Decimal("0") or observed_utc is None:
                missing_price_assets.append(position.symbol)
                evidence_entries.append(
                    PositionPriceEvidence(
                        symbol=position.symbol,
                        asset_id=position.asset_id,
                        source="provider_quote",
                        provider=provider_context[0] if provider_context is not None else None,
                        product_id=product_id,
                        reference_price=reference_price,
                        observed_at=observed_utc,
                        stale_at=stale_at,
                        state="missing",
                        detail="provider_quote_missing_reference_or_timestamp",
                        interval=None,
                    )
                )
                source_kinds.add("provider_quote")
                continue
            if stale_at is not None and now > stale_at:
                stale_price_assets.append(position.symbol)
                evidence_entries.append(
                    PositionPriceEvidence(
                        symbol=position.symbol,
                        asset_id=position.asset_id,
                        source="provider_quote",
                        provider=provider_context[0] if provider_context is not None else None,
                        product_id=product_id,
                        reference_price=reference_price,
                        observed_at=observed_utc,
                        stale_at=stale_at,
                        state="stale",
                        detail="provider_quote_exceeds_max_age",
                        interval=None,
                    )
                )
                source_kinds.add("provider_quote")
                if latest_price_timestamp is None or observed_utc > latest_price_timestamp:
                    latest_price_timestamp = observed_utc
                continue

            evidence_entries.append(
                PositionPriceEvidence(
                    symbol=position.symbol,
                    asset_id=position.asset_id,
                    source="provider_quote",
                    provider=provider_context[0] if provider_context is not None else None,
                    product_id=product_id,
                    reference_price=reference_price,
                    observed_at=observed_utc,
                    stale_at=stale_at,
                    state="ready",
                    detail="provider_quote_fresh",
                    interval=None,
                )
            )
            source_kinds.add("provider_quote")
            if latest_price_timestamp is None or observed_utc > latest_price_timestamp:
                latest_price_timestamp = observed_utc
            continue

        close, close_time, interval, candle_source = candle_points.get(position.asset_id, (None, None, None, None))
        interval_seconds = _parse_interval_seconds(interval)
        close_utc = close_time if close_time is None else close_time.astimezone(timezone.utc)
        cadence_bound_seconds = None if interval_seconds is None else interval_seconds + max(1, int(max_price_age_seconds))
        stale_at = None if close_utc is None or cadence_bound_seconds is None else close_utc + timedelta(seconds=cadence_bound_seconds)
        detail = "candle_fallback"
        if provider_failure is not None:
            detail = f"provider_quote_failed:{provider_failure}"

        if close is None or close <= Decimal("0") or close_utc is None:
            missing_price_assets.append(position.symbol)
            evidence_entries.append(
                PositionPriceEvidence(
                    symbol=position.symbol,
                    asset_id=position.asset_id,
                    source="candle",
                    provider=None,
                    product_id=product_id,
                    reference_price=close,
                    observed_at=close_utc,
                    stale_at=stale_at,
                    state="missing",
                    detail=f"{detail}:missing_candle_price_or_timestamp",
                    interval=interval,
                )
            )
            source_kinds.add("candle")
            continue
        if interval_seconds is None:
            stale_price_assets.append(position.symbol)
            evidence_entries.append(
                PositionPriceEvidence(
                    symbol=position.symbol,
                    asset_id=position.asset_id,
                    source="candle",
                    provider=None,
                    product_id=product_id,
                    reference_price=close,
                    observed_at=close_utc,
                    stale_at=stale_at,
                    state="stale",
                    detail=f"{detail}:unsupported_candle_interval",
                    interval=interval,
                )
            )
            source_kinds.add("candle")
            if latest_price_timestamp is None or close_utc > latest_price_timestamp:
                latest_price_timestamp = close_utc
            continue
        if stale_at is not None and now > stale_at:
            stale_price_assets.append(position.symbol)
            evidence_entries.append(
                PositionPriceEvidence(
                    symbol=position.symbol,
                    asset_id=position.asset_id,
                    source="candle",
                    provider=None,
                    product_id=product_id,
                    reference_price=close,
                    observed_at=close_utc,
                    stale_at=stale_at,
                    state="stale",
                    detail=f"{detail}:candle_exceeds_interval_bound",
                    interval=interval,
                )
            )
            source_kinds.add("candle")
            if latest_price_timestamp is None or close_utc > latest_price_timestamp:
                latest_price_timestamp = close_utc
            continue

        evidence_entries.append(
            PositionPriceEvidence(
                symbol=position.symbol,
                asset_id=position.asset_id,
                source="candle",
                provider=None,
                product_id=product_id,
                reference_price=close,
                observed_at=close_utc,
                stale_at=stale_at,
                state="ready",
                detail=detail,
                interval=interval,
            )
        )
        source_kinds.add("candle")
        if latest_price_timestamp is None or close_utc > latest_price_timestamp:
            latest_price_timestamp = close_utc

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

    valuation_source = "paper_account_snapshot_mark_to_market_candles"
    if source_kinds == {"provider_quote"}:
        valuation_source = "provider_quotes"
    elif source_kinds == {"candle"}:
        valuation_source = "candle_interval_bound"
    elif source_kinds:
        valuation_source = "mixed_provider_quote_and_candle"

    return EquityValuationSnapshot(
        generated_at=now,
        current_equity=Decimal(snapshot.equity),
        cash_balance=Decimal(snapshot.cash_balance),
        position_value=Decimal(snapshot.position_value),
        latest_price_timestamp=latest_price_timestamp,
        valuation_source=valuation_source,
        valuation_state=valuation_state,
        missing_price_assets=sorted(missing_price_assets),
        stale_price_assets=sorted(stale_price_assets),
        stale_cutoff=stale_cutoff,
        price_evidence=[_serialize_price_evidence(item) for item in evidence_entries],
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

    # Reconciliation events are append-only: an order can accumulate several
    # historical events (e.g. partially_filled, then reconciliation_required)
    # as its state evolves. Only the LATEST event per order reflects its
    # current effective state, so unresolved-ness is evaluated per order
    # (max sequence_number within that order), not by counting every
    # superseded historical row.
    latest_per_order = (
        select(
            LiveReconciliationEvent.live_crypto_order_id.label("order_id"),
            func.max(LiveReconciliationEvent.sequence_number).label("max_seq"),
        )
        .where(LiveReconciliationEvent.live_trading_profile_id.in_(profile_ids))
        .where(LiveReconciliationEvent.live_crypto_order_id.is_not(None))
        .group_by(LiveReconciliationEvent.live_crypto_order_id)
        .subquery()
    )
    unresolved_orders_count = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(latest_per_order)
                .join(
                    LiveReconciliationEvent,
                    and_(
                        LiveReconciliationEvent.live_crypto_order_id == latest_per_order.c.order_id,
                        LiveReconciliationEvent.sequence_number == latest_per_order.c.max_seq,
                        LiveReconciliationEvent.live_trading_profile_id.in_(profile_ids),
                    ),
                )
                .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATUSES)))
            )
        )
        or 0
    )
    # Events with no order association (no per-order "latest state" concept
    # applies) are each counted individually -- conservative/fail-closed.
    unresolved_unassociated_count = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(LiveReconciliationEvent)
                .where(LiveReconciliationEvent.live_trading_profile_id.in_(profile_ids))
                .where(LiveReconciliationEvent.live_crypto_order_id.is_(None))
                .where(LiveReconciliationEvent.reconciliation_status.in_(sorted(_UNRESOLVED_RECONCILIATION_STATUSES)))
            )
        )
        or 0
    )
    unresolved_count = unresolved_orders_count + unresolved_unassociated_count

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
