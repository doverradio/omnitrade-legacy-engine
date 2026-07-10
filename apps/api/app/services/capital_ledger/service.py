from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capital_campaign import CapitalCampaign
from app.models.paper_account import PaperAccount
from app.models.research_campaign import ResearchCampaign
from app.models.trade import Trade
from app.models.validation_run import ValidationRun
from app.models.validation_run_metric import ValidationRunMetric
from app.schemas.capital_ledger import (
    CapitalLedgerResponse,
    CapitalLedgerSummaryResponse,
    CapitalPoolResponse,
    CapitalPoolStatus,
    CapitalPoolType,
)
from app.services.paper.accounting import build_account_snapshot


STATUS_ALL = "all"
TYPE_ALL = "all"

_STATUS_OPTIONS = {STATUS_ALL, "active", "inactive", "completed", "cancelled"}
_TYPE_OPTIONS = {TYPE_ALL, "paper_account", "validation_run", "research_campaign", "strategy_allocation", "position"}


@dataclass(frozen=True, slots=True)
class _ValidationMetricSnapshot:
    current_equity: Decimal | None
    trades: int


def _zero() -> Decimal:
    return Decimal("0")


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> float:
    if denominator <= 0:
        return 0.0
    return float((numerator / denominator) * Decimal("100"))


def _map_validation_status(status: str) -> CapitalPoolStatus:
    upper = status.upper()
    if upper == "RUNNING":
        return "active"
    if upper == "CANCELLED":
        return "cancelled"
    if upper in {"COMPLETED", "FAILED"}:
        return "completed"
    return "inactive"


def _is_active_status(status: CapitalPoolStatus) -> bool:
    return status == "active"


def _pool_sort_key(item: CapitalPoolResponse) -> tuple[int, str, str]:
    priority = 0 if item.parent_capital_pool_id is None else 1
    return (priority, item.status, item.name.lower())


async def _load_validation_run_metrics(db: AsyncSession) -> dict[uuid.UUID, _ValidationMetricSnapshot]:
    rows = (
        await db.execute(
            select(ValidationRunMetric)
            .order_by(ValidationRunMetric.validation_run_id.asc(), ValidationRunMetric.captured_at.desc())
        )
    ).scalars().all()

    latest_by_run: dict[uuid.UUID, _ValidationMetricSnapshot] = {}
    for row in rows:
        if row.validation_run_id in latest_by_run:
            continue
        latest_by_run[row.validation_run_id] = _ValidationMetricSnapshot(
            current_equity=_to_decimal(row.paper_equity),
            trades=int(row.trades),
        )

    return latest_by_run


async def _load_validation_runs(db: AsyncSession) -> list[ValidationRun]:
    return (await db.execute(select(ValidationRun).order_by(ValidationRun.created_at.desc()))).scalars().all()


async def _load_paper_accounts(db: AsyncSession) -> list[PaperAccount]:
    return (await db.execute(select(PaperAccount).order_by(PaperAccount.created_at.desc()))).scalars().all()


async def _load_research_campaigns(db: AsyncSession) -> list[ResearchCampaign]:
    return (await db.execute(select(ResearchCampaign).order_by(ResearchCampaign.created_at.desc()))).scalars().all()


async def _load_capital_campaigns(db: AsyncSession) -> list[CapitalCampaign]:
    if not hasattr(db, "execute"):
        return []
    return (await db.execute(select(CapitalCampaign).order_by(CapitalCampaign.created_at.desc()))).scalars().all()


async def _load_trade_counts_by_account(db: AsyncSession) -> dict[uuid.UUID, int]:
    rows = (
        await db.execute(select(Trade.paper_account_id, Trade.id).order_by(Trade.paper_account_id.asc()))
    ).all()

    counts: dict[uuid.UUID, int] = {}
    for account_id, _trade_id in rows:
        counts[account_id] = counts.get(account_id, 0) + 1
    return counts


async def build_capital_ledger(
    *,
    db: AsyncSession,
    status: str = STATUS_ALL,
    capital_type: str = TYPE_ALL,
    page: int = 1,
    page_size: int = 50,
) -> CapitalLedgerResponse:
    normalized_status = status.strip().lower()
    normalized_type = capital_type.strip().lower()

    if normalized_status not in _STATUS_OPTIONS:
        normalized_status = STATUS_ALL
    if normalized_type not in _TYPE_OPTIONS:
        normalized_type = TYPE_ALL

    if page <= 0:
        page = 1
    if page_size <= 0:
        page_size = 50
    page_size = min(page_size, 200)

    generated_at = datetime.now(timezone.utc)
    unavailable_sources: list[str] = []

    validation_runs = await _load_validation_runs(db)
    paper_accounts = await _load_paper_accounts(db)
    research_campaigns = await _load_research_campaigns(db)
    capital_campaigns = await _load_capital_campaigns(db)

    campaign_by_validation_run_id: dict[uuid.UUID, CapitalCampaign] = {}
    campaign_by_paper_account_id: dict[uuid.UUID, CapitalCampaign] = {}
    for campaign in capital_campaigns:
        if campaign.validation_run_id is not None and campaign.validation_run_id not in campaign_by_validation_run_id:
            campaign_by_validation_run_id[campaign.validation_run_id] = campaign
        if campaign.paper_account_id is not None and campaign.paper_account_id not in campaign_by_paper_account_id:
            campaign_by_paper_account_id[campaign.paper_account_id] = campaign

    metric_by_run = await _load_validation_run_metrics(db)
    trade_counts_by_account = await _load_trade_counts_by_account(db)

    pools: list[CapitalPoolResponse] = []

    known_fields = 0
    total_fields = 0

    total_trades = 0
    active_positions = 0

    # Top-level validation-run pools
    for run in validation_runs:
        pool_id = f"validation-run:{run.validation_run_id}"
        run_status = _map_validation_status(run.status)

        start_capital = _to_decimal(run.paper_capital)
        metric = metric_by_run.get(run.validation_run_id)
        campaign = campaign_by_validation_run_id.get(run.validation_run_id)
        current_equity = metric.current_equity if metric else None
        if current_equity is None:
            unavailable_sources.append(f"validation_run_metrics:{run.validation_run_id}")

        total_trades += metric.trades if metric else 0

        unrealized = (current_equity - start_capital) if current_equity is not None and start_capital is not None else None
        realized = _zero()
        allocated = start_capital if _is_active_status(run_status) else _zero()
        reserved = start_capital if run_status == "active" else _zero()
        available = (current_equity - reserved) if current_equity is not None else None
        pnl_percent = _safe_ratio(unrealized or _zero(), start_capital) if unrealized is not None and start_capital and start_capital > 0 else None

        for value in [start_capital, current_equity, allocated, available, reserved, realized, unrealized]:
            total_fields += 1
            if value is not None:
                known_fields += 1

        pools.append(
            CapitalPoolResponse(
                capital_pool_id=pool_id,
                capital_pool_type="validation_run",
                name=run.name,
                status=run_status,
                starting_capital=start_capital,
                current_equity=current_equity,
                allocated_capital=allocated,
                available_capital=available,
                reserved_capital=reserved,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                pnl_percent=pnl_percent,
                started_at=run.started_at,
                completed_at=run.completed_at,
                related_entity_type="validation_run",
                related_entity_id=str(run.validation_run_id),
                related_page_url="/validation-runs",
                capital_campaign_uuid=None if campaign is None else str(campaign.uuid),
                capital_campaign_name=None if campaign is None else campaign.name,
                capital_campaign_status=None if campaign is None else campaign.status,
                parent_capital_pool_id=None,
                child_allocations_count=len(run.enabled_strategies),
                notes=(
                    "Top-level funded validation pool. Trades and positions are treated as child allocations and excluded "
                    "from Managed Capital to prevent double counting."
                ),
            )
        )

        # Child strategy allocation rows (read-only metadata, not counted in Managed Capital)
        for strategy_name in run.enabled_strategies:
            pools.append(
                CapitalPoolResponse(
                    capital_pool_id=f"strategy-allocation:{run.validation_run_id}:{strategy_name}",
                    capital_pool_type="strategy_allocation",
                    name=f"{strategy_name} Allocation",
                    status="active" if run_status == "active" else "inactive",
                    starting_capital=None,
                    current_equity=None,
                    allocated_capital=None,
                    available_capital=None,
                    reserved_capital=None,
                    realized_pnl=None,
                    unrealized_pnl=None,
                    pnl_percent=None,
                    started_at=run.started_at,
                    completed_at=run.completed_at,
                    related_entity_type="validation_run",
                    related_entity_id=str(run.validation_run_id),
                    related_page_url="/validation-runs",
                    capital_campaign_uuid=None if campaign is None else str(campaign.uuid),
                    capital_campaign_name=None if campaign is None else campaign.name,
                    capital_campaign_status=None if campaign is None else campaign.status,
                    parent_capital_pool_id=pool_id,
                    child_allocations_count=0,
                    notes="Strategy child allocation is informational only. Funding split is not durably tracked yet.",
                )
            )

    # Top-level paper-account pools and child positions
    for account in paper_accounts:
        pool_id = f"paper-account:{account.id}"
        account_status: CapitalPoolStatus = "active" if bool(account.is_active) else "inactive"
        campaign = campaign_by_paper_account_id.get(account.id)

        snapshot = await build_account_snapshot(db=db, paper_account_id=account.id, starting_balance=account.starting_balance)

        start_capital = _to_decimal(account.starting_balance)
        current_equity = _to_decimal(snapshot.equity)
        allocated = _to_decimal(snapshot.position_value)
        reserved = _to_decimal(snapshot.position_value)
        available = _to_decimal(snapshot.cash_balance)
        realized = _to_decimal(snapshot.equity_return_usd) - sum((position.unrealized_pnl_usd for position in snapshot.positions), _zero())
        unrealized = sum((position.unrealized_pnl_usd for position in snapshot.positions), _zero())
        pnl_percent = _safe_ratio(_to_decimal(snapshot.equity_return_usd) or _zero(), start_capital) if start_capital and start_capital > 0 else None

        total_trades += int(trade_counts_by_account.get(account.id, 0))
        active_positions += len(snapshot.positions)

        for value in [start_capital, current_equity, allocated, available, reserved, realized, unrealized]:
            total_fields += 1
            if value is not None:
                known_fields += 1

        pools.append(
            CapitalPoolResponse(
                capital_pool_id=pool_id,
                capital_pool_type="paper_account",
                name=account.name,
                status=account_status,
                starting_capital=start_capital,
                current_equity=current_equity,
                allocated_capital=allocated,
                available_capital=available,
                reserved_capital=reserved,
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                pnl_percent=pnl_percent,
                started_at=account.created_at,
                completed_at=None,
                related_entity_type="paper_account",
                related_entity_id=str(account.id),
                related_page_url="/paper-trading",
                capital_campaign_uuid=None if campaign is None else str(campaign.uuid),
                capital_campaign_name=None if campaign is None else campaign.name,
                capital_campaign_status=None if campaign is None else campaign.status,
                parent_capital_pool_id=None,
                child_allocations_count=len(snapshot.positions),
                notes="Top-level paper account pool. Open positions are child allocations and excluded from Managed Capital.",
            )
        )

        for position in snapshot.positions:
            position_value = _to_decimal(position.position_value)
            unrealized_value = _to_decimal(position.unrealized_pnl_usd)
            start_value = _to_decimal(position.avg_entry_price * position.quantity)
            pools.append(
                CapitalPoolResponse(
                    capital_pool_id=f"position:{account.id}:{position.asset_id}",
                    capital_pool_type="position",
                    name=f"{position.symbol} Position",
                    status="active" if position.quantity > 0 else "inactive",
                    starting_capital=start_value,
                    current_equity=position_value,
                    allocated_capital=position_value,
                    available_capital=_zero(),
                    reserved_capital=position_value,
                    realized_pnl=_zero(),
                    unrealized_pnl=unrealized_value,
                    pnl_percent=_safe_ratio(unrealized_value or _zero(), start_value) if start_value and start_value > 0 else None,
                    started_at=account.created_at,
                    completed_at=None,
                    related_entity_type="paper_account",
                    related_entity_id=str(account.id),
                    related_page_url="/paper-trading",
                    capital_campaign_uuid=None if campaign is None else str(campaign.uuid),
                    capital_campaign_name=None if campaign is None else campaign.name,
                    capital_campaign_status=None if campaign is None else campaign.status,
                    parent_capital_pool_id=pool_id,
                    child_allocations_count=0,
                    notes="Position valuation is derived from parent paper account and not added to Managed Capital.",
                )
            )

    # Research campaigns are included only when independently funded. v1 has no durable funded-allocation table.
    if research_campaigns:
        unavailable_sources.append("research_campaign_allocations")

    # De-duplicate source warnings while preserving order.
    deduped_unavailable: list[str] = []
    seen_unavailable: set[str] = set()
    for source_name in unavailable_sources:
        if source_name in seen_unavailable:
            continue
        seen_unavailable.add(source_name)
        deduped_unavailable.append(source_name)

    top_level_pools = [item for item in pools if item.parent_capital_pool_id is None]

    def _sum_decimal(values: list[Decimal | None]) -> Decimal:
        total = _zero()
        for value in values:
            if value is not None:
                total += value
        return total

    total_managed_capital = _sum_decimal([item.starting_capital for item in top_level_pools])
    total_starting_capital = _sum_decimal([item.starting_capital for item in top_level_pools])
    total_current_equity = _sum_decimal([item.current_equity for item in top_level_pools])
    total_allocated_capital = _sum_decimal([item.allocated_capital for item in top_level_pools if item.status == "active"])
    total_available_capital = _sum_decimal([item.available_capital for item in top_level_pools])
    total_reserved_capital = _sum_decimal([item.reserved_capital for item in top_level_pools])
    total_realized_pnl = _sum_decimal([item.realized_pnl for item in top_level_pools])
    total_unrealized_pnl = _sum_decimal([item.unrealized_pnl for item in top_level_pools])

    active_capital_pools = sum(1 for item in top_level_pools if item.status == "active")
    inactive_capital_pools = sum(1 for item in top_level_pools if item.status != "active")

    # Apply filters to visible list.
    filtered = pools
    if normalized_status != STATUS_ALL:
        filtered = [item for item in filtered if item.status == normalized_status]
    if normalized_type != TYPE_ALL:
        filtered = [item for item in filtered if item.capital_pool_type == normalized_type]

    filtered = sorted(filtered, key=_pool_sort_key)

    total_rows = len(filtered)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    paged = filtered[start_index:end_index]
    has_more = end_index < total_rows

    completeness = 100.0 if total_fields == 0 else (known_fields / total_fields) * 100.0

    summary = CapitalLedgerSummaryResponse(
        total_managed_capital=total_managed_capital,
        total_starting_capital=total_starting_capital,
        total_current_equity=total_current_equity,
        total_allocated_capital=total_allocated_capital,
        total_available_capital=total_available_capital,
        total_reserved_capital=total_reserved_capital,
        total_realized_pnl=total_realized_pnl,
        total_unrealized_pnl=total_unrealized_pnl,
        active_capital_pools=active_capital_pools,
        inactive_capital_pools=inactive_capital_pools,
        active_positions=active_positions,
        total_trades=total_trades,
        utilization_percent=_safe_ratio(total_allocated_capital, total_managed_capital),
        data_completeness_percent=round(completeness, 2),
        unavailable_sources=deduped_unavailable,
        generated_at=generated_at,
    )

    return CapitalLedgerResponse(
        summary=summary,
        capital_pools=paged,
        page=page,
        page_size=page_size,
        total=total_rows,
        has_more=has_more,
    )
