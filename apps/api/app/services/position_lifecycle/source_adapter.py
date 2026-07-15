from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.candle import Candle
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_trading_profile import LiveTradingProfile
from app.services.position_lifecycle.contracts import PositionSnapshot


@dataclass
class _Aggregate:
    live_trading_profile_id: uuid.UUID
    account_id: uuid.UUID
    capital_campaign_id: int | None
    symbol: str

    buy_qty: Decimal = Decimal("0")
    buy_notional: Decimal = Decimal("0")
    buy_fees: Decimal = Decimal("0")
    sell_qty: Decimal = Decimal("0")

    opened_at: datetime | None = None
    last_fill_at: datetime | None = None
    provider_order_ids: set[str] = None
    provider_fill_ids: set[str] = None
    accounting_record_count: int = 0
    fail_closed_reason: str | None = None

    def __post_init__(self) -> None:
        if self.provider_order_ids is None:
            self.provider_order_ids = set()
        if self.provider_fill_ids is None:
            self.provider_fill_ids = set()


def _position_id(*, live_trading_profile_id: uuid.UUID, capital_campaign_id: int | None, symbol: str) -> str:
    key = f"{live_trading_profile_id}:{capital_campaign_id}:{symbol.upper()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"position-lifecycle:{key}"))


def _symbol_base(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if "-" in normalized:
        return normalized.split("-", 1)[0]
    return normalized


async def load_position_snapshots(
    *,
    db: AsyncSession,
    account_id: uuid.UUID | None,
    campaign_id: int | None,
) -> list[PositionSnapshot]:
    statement = (
        select(LiveAccountingRecord, LiveTradingProfile.paper_account_id)
        .join(LiveTradingProfile, LiveTradingProfile.id == LiveAccountingRecord.live_trading_profile_id)
        .where(LiveAccountingRecord.record_type.in_(["fill_accounting", "partial_fill_accounting"]))
        .order_by(LiveAccountingRecord.recorded_at.asc())
    )
    if account_id is not None:
        statement = statement.where(LiveTradingProfile.paper_account_id == account_id)
    if campaign_id is not None:
        statement = statement.where(LiveAccountingRecord.capital_campaign_id == campaign_id)

    rows = (await db.execute(statement)).all()

    aggregates: dict[tuple[uuid.UUID, int | None, str], _Aggregate] = {}
    seen_record_dedupe_keys: set[tuple[uuid.UUID, int | None, str, str, str, str]] = set()
    for record, paper_account_id in rows:
        dedupe_key = (
            record.live_trading_profile_id,
            record.capital_campaign_id,
            record.symbol.upper(),
            str(record.provider_order_id),
            str(record.provider_fill_id or ""),
            str(record.record_type),
        )
        if dedupe_key in seen_record_dedupe_keys:
            continue
        seen_record_dedupe_keys.add(dedupe_key)

        key = (record.live_trading_profile_id, record.capital_campaign_id, record.symbol.upper())
        agg = aggregates.get(key)
        if agg is None:
            agg = _Aggregate(
                live_trading_profile_id=record.live_trading_profile_id,
                account_id=paper_account_id,
                capital_campaign_id=record.capital_campaign_id,
                symbol=record.symbol.upper(),
            )
            aggregates[key] = agg

        qty = Decimal(record.filled_quantity)
        gross = Decimal(record.gross_notional)
        fee = Decimal(record.fee_amount)
        agg.accounting_record_count += 1
        agg.provider_order_ids.add(str(record.provider_order_id))
        if record.provider_fill_id:
            agg.provider_fill_ids.add(str(record.provider_fill_id))

        if record.side == "buy":
            agg.buy_qty += qty
            agg.buy_notional += gross
            agg.buy_fees += fee
            if agg.opened_at is None:
                agg.opened_at = record.recorded_at
        elif record.side == "sell":
            agg.sell_qty += qty

        agg.last_fill_at = record.recorded_at

    if not aggregates:
        return []

    base_symbols = sorted({_symbol_base(agg.symbol) for agg in aggregates.values()})
    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.symbol.in_(base_symbols))
            .where(Asset.is_active.is_(True))
        )
    ).scalars().all()
    assets_by_symbol: dict[str, list[Asset]] = {}
    for asset in assets:
        assets_by_symbol.setdefault(asset.symbol.upper(), []).append(asset)

    now = datetime.now(timezone.utc)
    latest_candle_by_asset_id: dict[uuid.UUID, Candle] = {}
    future_candle_asset_ids: set[uuid.UUID] = set()
    for asset in assets:
        latest = (
            await db.execute(
                select(Candle)
                .where(Candle.asset_id == asset.id)
                .order_by(desc(Candle.close_time))
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is not None:
            if latest.close_time.astimezone(timezone.utc) > now:
                future_candle_asset_ids.add(asset.id)
            else:
                latest_candle_by_asset_id[asset.id] = latest

    snapshots: list[PositionSnapshot] = []
    for agg in aggregates.values():
        open_qty = agg.buy_qty - agg.sell_qty
        fail_closed_reason = agg.fail_closed_reason
        if open_qty < Decimal("0"):
            open_qty = Decimal("0")
            fail_closed_reason = "net_short_not_supported"

        if agg.buy_qty > Decimal("0"):
            avg_entry_price = agg.buy_notional / agg.buy_qty
            allocated_buy_costs = (open_qty / agg.buy_qty) * agg.buy_fees
        else:
            avg_entry_price = Decimal("0")
            allocated_buy_costs = Decimal("0")

        base = _symbol_base(agg.symbol)
        matched_assets = assets_by_symbol.get(base, [])
        if len(matched_assets) > 1:
            fail_closed_reason = "asset_symbol_ambiguous_multi_exchange"
            asset = None
        elif len(matched_assets) == 1:
            asset = matched_assets[0]
        else:
            asset = None
        candle = latest_candle_by_asset_id.get(asset.id) if asset is not None else None
        if asset is not None and asset.id in future_candle_asset_ids:
            fail_closed_reason = "market_data_timestamp_in_future"

        current_price = None if candle is None else Decimal(candle.close)
        market_data_timestamp = None if candle is None else candle.close_time.astimezone(timezone.utc)
        market_data_age_minutes = None
        if market_data_timestamp is not None and market_data_timestamp > now:
            fail_closed_reason = "market_data_timestamp_in_future"
        if current_price is not None and current_price <= Decimal("0"):
            fail_closed_reason = "market_price_non_positive"
        if market_data_timestamp is not None:
            market_data_age_minutes = max(0, int((now - market_data_timestamp).total_seconds() // 60))

        snapshots.append(
            PositionSnapshot(
                position_id=_position_id(
                    live_trading_profile_id=agg.live_trading_profile_id,
                    capital_campaign_id=agg.capital_campaign_id,
                    symbol=agg.symbol,
                ),
                live_trading_profile_id=agg.live_trading_profile_id,
                account_id=agg.account_id,
                capital_campaign_id=agg.capital_campaign_id,
                symbol=agg.symbol,
                asset_class=(asset.asset_class if asset is not None else "crypto"),
                position_size=open_qty,
                entry_price=avg_entry_price,
                accumulated_entry_and_carry_costs=allocated_buy_costs,
                opened_at=agg.opened_at,
                last_fill_at=agg.last_fill_at,
                provider_order_ids=tuple(sorted(agg.provider_order_ids)),
                provider_fill_ids=tuple(sorted(agg.provider_fill_ids)),
                accounting_record_count=agg.accounting_record_count,
                fail_closed_reason=fail_closed_reason,
                current_price=current_price,
                market_data_timestamp=market_data_timestamp,
                market_data_age_minutes=market_data_age_minutes,
                market_data_interval=None if candle is None else candle.interval,
                market_data_source=None if candle is None else candle.source,
                market_data_candle_id=None if candle is None else candle.id,
            )
        )

    return snapshots
