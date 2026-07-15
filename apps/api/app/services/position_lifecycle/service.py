from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.schemas.position_lifecycle import (
    PositionLifecycleItemResponse,
    PositionLifecycleResponse,
)
from app.services.position_lifecycle.evaluator import evaluate_position_lifecycle
from app.services.position_lifecycle.policy_registry import resolve_lifecycle_policy
from app.services.position_lifecycle.source_adapter import load_position_snapshots


async def build_position_lifecycle_report(
    *,
    db: AsyncSession,
    position_id: str | None,
    account_id: UUID | None,
    campaign_id: int | None,
    asset_class: str | None,
    recommendation: str | None,
) -> PositionLifecycleResponse:
    now = datetime.now(timezone.utc)

    snapshots = await load_position_snapshots(
        db=db,
        account_id=account_id,
        campaign_id=campaign_id,
    )

    items: list[PositionLifecycleItemResponse] = []
    for snapshot in snapshots:
        if position_id is not None and snapshot.position_id != position_id:
            continue
        if asset_class is not None and snapshot.asset_class.lower() != asset_class.strip().lower():
            continue

        policy = resolve_lifecycle_policy(
            asset_class=snapshot.asset_class,
            symbol=snapshot.symbol,
            venue="venue-neutral",
            now=now,
        )
        if policy is None:
            raise InvalidRequestError(
                message="No eligible lifecycle policy for position",
                details={
                    "position_id": snapshot.position_id,
                    "asset_class": snapshot.asset_class,
                    "symbol": snapshot.symbol,
                },
            )

        evaluation = evaluate_position_lifecycle(snapshot=snapshot, policy=policy, now=now)
        if recommendation is not None and evaluation.recommendation.lower() != recommendation.strip().lower():
            continue

        item = PositionLifecycleItemResponse(
            position_id=snapshot.position_id,
            live_trading_profile_id=str(snapshot.live_trading_profile_id),
            account_id=str(snapshot.account_id),
            capital_campaign_id=snapshot.capital_campaign_id,
            symbol=snapshot.symbol,
            asset_class=snapshot.asset_class,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            lifecycle_state=evaluation.lifecycle_state,
            recommendation=evaluation.recommendation,
            reason=evaluation.reason,
            position_size=snapshot.position_size,
            entry_price=snapshot.entry_price,
            current_price=snapshot.current_price,
            current_market_value=evaluation.current_market_value,
            expected_net_realized_pnl_if_sold_now=evaluation.expected_net_realized_pnl_if_sold_now,
            break_even_price=evaluation.break_even_price,
            minimum_profitable_exit_price=evaluation.minimum_profitable_exit_price,
            opened_at=snapshot.opened_at,
            last_fill_at=snapshot.last_fill_at,
            provider_order_ids=list(snapshot.provider_order_ids),
            provider_fill_ids=list(snapshot.provider_fill_ids),
            accounting_record_count=snapshot.accounting_record_count,
            market_data_timestamp=snapshot.market_data_timestamp,
            market_data_interval=snapshot.market_data_interval,
            market_data_source=snapshot.market_data_source,
            market_data_candle_id=snapshot.market_data_candle_id,
            market_data_age_minutes=snapshot.market_data_age_minutes,
            market_data_stale=evaluation.market_data_stale,
            stale_indicator=evaluation.stale_indicator,
            dust_indicator=evaluation.dust_indicator,
            closed_indicator=evaluation.closed_indicator,
            evaluated_at=now,
        )
        items.append(item)

    # Each item is a latest evaluation snapshot; keep deterministic ordering.
    items.sort(key=lambda item: (item.account_id, item.symbol, item.position_id))

    return PositionLifecycleResponse(
        generated_at=now,
        count=len(items),
        items=items,
    )
