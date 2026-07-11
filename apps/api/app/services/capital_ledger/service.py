from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_profit_cycle import CapitalCampaignProfitCycle
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
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
_TYPE_OPTIONS = {
    TYPE_ALL,
    "paper_account",
    "validation_run",
    "research_campaign",
    "live_campaign",
    "live_uncategorized",
    "strategy_allocation",
    "position",
    "compounding_recommendation",
    "withdrawal_recommendation",
    "profit_reserve",
    "policy_review",
}


@dataclass(frozen=True, slots=True)
class _ValidationMetricSnapshot:
    current_equity: Decimal | None
    trades: int


@dataclass(frozen=True, slots=True)
class _LiveCampaignProjection:
    campaign_id: int | None
    pool_type: CapitalPoolType
    pool_id: str
    pool_name: str
    status: CapitalPoolStatus
    campaign_correlation_status: str
    provider_reconciliation_status: str
    accounting_projection_status: str
    accounting_completion_status: str
    balance_mismatch_state: str
    filled_quantity: Decimal
    gross_filled_notional: Decimal
    provider_fees: Decimal
    net_quote_capital_effect: Decimal
    live_entry_types: tuple[str, ...]


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


async def _load_profit_cycles(db: AsyncSession) -> list[CapitalCampaignProfitCycle]:
    if not hasattr(db, "execute"):
        return []
    return (
        await db.execute(
            select(CapitalCampaignProfitCycle).order_by(
                CapitalCampaignProfitCycle.capital_campaign_id.asc(),
                CapitalCampaignProfitCycle.cycle_number.desc(),
                CapitalCampaignProfitCycle.cycle_id.desc(),
            )
        )
    ).scalars().all()


async def _load_trade_counts_by_account(db: AsyncSession) -> dict[uuid.UUID, int]:
    rows = (
        await db.execute(select(Trade.paper_account_id, Trade.id).order_by(Trade.paper_account_id.asc()))
    ).all()

    counts: dict[uuid.UUID, int] = {}
    for account_id, _trade_id in rows:
        counts[account_id] = counts.get(account_id, 0) + 1
    return counts


async def _load_live_accounting_records(db: AsyncSession) -> list[LiveAccountingRecord]:
    if not hasattr(db, "execute"):
        return []
    return (
        await db.execute(
            select(LiveAccountingRecord).order_by(LiveAccountingRecord.recorded_at.asc(), LiveAccountingRecord.created_at.asc())
        )
    ).scalars().all()


async def _load_live_orders(db: AsyncSession) -> list[LiveCryptoOrder]:
    if not hasattr(db, "execute"):
        return []
    return (
        await db.execute(select(LiveCryptoOrder).order_by(LiveCryptoOrder.created_at.asc(), LiveCryptoOrder.live_crypto_order_id.asc()))
    ).scalars().all()


async def _load_live_reconciliation_events(db: AsyncSession) -> list[LiveReconciliationEvent]:
    if not hasattr(db, "execute"):
        return []
    return (
        await db.execute(
            select(LiveReconciliationEvent).order_by(LiveReconciliationEvent.recorded_at.asc(), LiveReconciliationEvent.sequence_number.asc())
        )
    ).scalars().all()


def _project_live_campaign_pools(
    *,
    accounting_records: list[LiveAccountingRecord],
    live_orders: list[LiveCryptoOrder],
    reconciliation_events: list[LiveReconciliationEvent],
    campaigns: dict[int, CapitalCampaign],
) -> list[_LiveCampaignProjection]:
    if not accounting_records and not live_orders:
        return []

    orders_by_id = {item.live_crypto_order_id: item for item in live_orders}
    latest_reconciliation_by_order: dict[uuid.UUID, LiveReconciliationEvent] = {}
    for event in reconciliation_events:
        if event.live_crypto_order_id is None:
            continue
        latest_reconciliation_by_order[event.live_crypto_order_id] = event

    per_order: dict[uuid.UUID, dict[str, object]] = {}
    for row in accounting_records:
        if row.live_crypto_order_id is None:
            continue
        order_bucket = per_order.setdefault(
            row.live_crypto_order_id,
            {
                "campaign_ids": set(),
                "filled_quantity": _zero(),
                "gross_filled_notional": _zero(),
                "provider_fees": _zero(),
                "provider_fees_non_usd": _zero(),
                "live_entry_types": set(),
            },
        )
        if row.capital_campaign_id is not None:
            order_bucket["campaign_ids"].add(row.capital_campaign_id)

        if row.record_type in {"fill_accounting", "partial_fill_accounting"}:
            order_bucket["filled_quantity"] += _to_decimal(row.filled_quantity) or _zero()
            order_bucket["gross_filled_notional"] += _to_decimal(row.gross_notional) or _zero()
            order_bucket["live_entry_types"].update(["live_capital_deployment", "asset_received"])
        if row.record_type == "fee_attribution":
            fee_amount = _to_decimal(row.fee_amount) or _zero()
            if str(row.fee_currency).upper() == "USD":
                order_bucket["provider_fees"] += fee_amount
            else:
                order_bucket["provider_fees_non_usd"] += fee_amount
            order_bucket["live_entry_types"].add("provider_fee")

    # Keep orders without fill rows visible as canceled/unresolved evidence when they exist.
    for order_id, order in orders_by_id.items():
        per_order.setdefault(
            order_id,
            {
                "campaign_ids": set(),
                "filled_quantity": _zero(),
                "gross_filled_notional": _zero(),
                "provider_fees": _zero(),
                "provider_fees_non_usd": _zero(),
                "live_entry_types": set(),
            },
        )
        campaign_id_raw = (order.safe_provider_response or {}).get("capital_campaign_id")
        if isinstance(campaign_id_raw, int):
            per_order[order_id]["campaign_ids"].add(campaign_id_raw)

    grouped: dict[str, dict[str, object]] = {}
    for order_id, order_view in per_order.items():
        order = orders_by_id.get(order_id)
        latest_reconciliation = latest_reconciliation_by_order.get(order_id)
        campaign_ids = set(order_view["campaign_ids"])
        correlation_status = "uncategorized"
        campaign_id: int | None = None
        if len(campaign_ids) > 1:
            correlation_status = "mismatch"
        elif len(campaign_ids) == 1:
            campaign_id = next(iter(campaign_ids))
            correlation_status = "verified" if campaign_id in campaigns else "mismatch"

        pool_key = "uncategorized" if campaign_id is None or correlation_status == "mismatch" else f"campaign:{campaign_id}"
        bucket = grouped.setdefault(
            pool_key,
            {
                "campaign_id": campaign_id if correlation_status == "verified" else None,
                "filled_quantity": _zero(),
                "gross_filled_notional": _zero(),
                "provider_fees": _zero(),
                "net_quote_capital_effect": _zero(),
                "provider_reconciliation_status": "unknown",
                "accounting_projection_status": "not_projected",
                "accounting_completion_status": "unresolved",
                "balance_mismatch_state": "unknown",
                "campaign_correlation_status": correlation_status,
                "live_entry_types": set(),
                "status": "inactive",
            },
        )

        filled_quantity = order_view["filled_quantity"]
        gross_notional = order_view["gross_filled_notional"]
        provider_fees = order_view["provider_fees"]
        net_quote_effect = gross_notional + provider_fees

        bucket["filled_quantity"] += filled_quantity
        bucket["gross_filled_notional"] += gross_notional
        bucket["provider_fees"] += provider_fees
        bucket["net_quote_capital_effect"] += net_quote_effect
        bucket["live_entry_types"].update(order_view["live_entry_types"])

        if filled_quantity > _zero():
            bucket["accounting_projection_status"] = "projected"
            bucket["status"] = "active"

        reconciliation_status = "unknown"
        if latest_reconciliation is not None:
            reconciliation_status = latest_reconciliation.reconciliation_status
        elif order is not None and order.provider_status is not None:
            reconciliation_status = str(order.provider_status).lower()
        bucket["provider_reconciliation_status"] = reconciliation_status

        order_reconciliation = ((order.safe_provider_response or {}).get("reconciliation") if order is not None else None) or {}
        completion_status = str(order_reconciliation.get("accounting_completion_status") or "unresolved")
        balance_state = str(order_reconciliation.get("balance_mismatch_state") or "unknown")
        bucket["accounting_completion_status"] = "complete" if bucket["accounting_completion_status"] == "complete" and completion_status == "complete" else completion_status
        if balance_state in {"material_mismatch", "stale", "missing"}:
            bucket["balance_mismatch_state"] = balance_state
        elif bucket["balance_mismatch_state"] == "unknown":
            bucket["balance_mismatch_state"] = balance_state

        if reconciliation_status == "canceled" and filled_quantity <= _zero():
            bucket["live_entry_types"].add("cancellation_no_fill")
        if reconciliation_status == "canceled" and filled_quantity > _zero():
            bucket["live_entry_types"].add("cancellation_partial_fill")
        if reconciliation_status in {"balance_mismatch", "reconciliation_required", "conflict"} or balance_state in {"material_mismatch", "stale", "missing"}:
            bucket["live_entry_types"].add("reconciliation_adjustment_evidence")

        if correlation_status == "mismatch":
            bucket["campaign_correlation_status"] = "mismatch"
            bucket["accounting_completion_status"] = "unresolved"

    projections: list[_LiveCampaignProjection] = []
    for key, item in grouped.items():
        campaign_id = item["campaign_id"]
        campaign = campaigns.get(campaign_id) if campaign_id is not None else None
        is_uncategorized = key == "uncategorized"
        pool_name = "Uncategorized Live Orders" if is_uncategorized else f"{campaign.name} Live Accounting"
        pool_id = "live-uncategorized" if is_uncategorized else f"live-campaign:{campaign.uuid}"
        pool_type: CapitalPoolType = "live_uncategorized" if is_uncategorized else "live_campaign"

        projections.append(
            _LiveCampaignProjection(
                campaign_id=campaign_id,
                pool_type=pool_type,
                pool_id=pool_id,
                pool_name=pool_name,
                status=item["status"],
                campaign_correlation_status=item["campaign_correlation_status"],
                provider_reconciliation_status=item["provider_reconciliation_status"],
                accounting_projection_status=item["accounting_projection_status"],
                accounting_completion_status=item["accounting_completion_status"],
                balance_mismatch_state=item["balance_mismatch_state"],
                filled_quantity=item["filled_quantity"],
                gross_filled_notional=item["gross_filled_notional"],
                provider_fees=item["provider_fees"],
                net_quote_capital_effect=item["net_quote_capital_effect"],
                live_entry_types=tuple(sorted(item["live_entry_types"])),
            )
        )
    return projections


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
    profit_cycles = await _load_profit_cycles(db)
    live_accounting_records = await _load_live_accounting_records(db)
    live_orders = await _load_live_orders(db)
    live_reconciliation_events = await _load_live_reconciliation_events(db)

    campaign_by_validation_run_id: dict[uuid.UUID, CapitalCampaign] = {}
    campaign_by_paper_account_id: dict[uuid.UUID, CapitalCampaign] = {}
    campaign_by_id: dict[int, CapitalCampaign] = {}
    for campaign in capital_campaigns:
        campaign_by_id[campaign.id] = campaign
        if campaign.validation_run_id is not None and campaign.validation_run_id not in campaign_by_validation_run_id:
            campaign_by_validation_run_id[campaign.validation_run_id] = campaign
        if campaign.paper_account_id is not None and campaign.paper_account_id not in campaign_by_paper_account_id:
            campaign_by_paper_account_id[campaign.paper_account_id] = campaign

    live_projection_rows = _project_live_campaign_pools(
        accounting_records=live_accounting_records,
        live_orders=live_orders,
        reconciliation_events=live_reconciliation_events,
        campaigns=campaign_by_id,
    )

    latest_cycle_by_campaign_id: dict[int, CapitalCampaignProfitCycle] = {}
    for cycle in profit_cycles:
        if cycle.capital_campaign_id not in latest_cycle_by_campaign_id:
            latest_cycle_by_campaign_id[cycle.capital_campaign_id] = cycle

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

    for projection in live_projection_rows:
        campaign = campaign_by_id.get(projection.campaign_id) if projection.campaign_id is not None else None
        pools.append(
            CapitalPoolResponse(
                capital_pool_id=projection.pool_id,
                capital_pool_type=projection.pool_type,
                name=projection.pool_name,
                status=projection.status,
                starting_capital=None,
                current_equity=None,
                allocated_capital=projection.net_quote_capital_effect,
                available_capital=None,
                reserved_capital=projection.net_quote_capital_effect,
                realized_pnl=_zero(),
                unrealized_pnl=_zero(),
                pnl_percent=None,
                started_at=generated_at,
                completed_at=None,
                related_entity_type="capital_campaign" if campaign is not None else "live_crypto_order",
                related_entity_id=str(campaign.uuid) if campaign is not None else "uncategorized",
                related_page_url=(f"/capital-campaigns/{campaign.uuid}" if campaign is not None else "/capital/ledger"),
                capital_campaign_uuid=None if campaign is None else str(campaign.uuid),
                capital_campaign_name=None if campaign is None else campaign.name,
                capital_campaign_status=None if campaign is None else campaign.status,
                accounting_source="live",
                provider_reconciliation_status=projection.provider_reconciliation_status,
                accounting_projection_status=projection.accounting_projection_status,
                accounting_completion_status=projection.accounting_completion_status,
                balance_mismatch_state=projection.balance_mismatch_state,
                campaign_correlation_status=projection.campaign_correlation_status,
                filled_quantity=projection.filled_quantity,
                gross_filled_notional=projection.gross_filled_notional,
                provider_fees=projection.provider_fees,
                net_quote_capital_effect=projection.net_quote_capital_effect,
                live_entry_types=list(projection.live_entry_types),
                parent_capital_pool_id=None,
                child_allocations_count=0,
                notes="Live accounting projection derived from append-only provider reconciliation records.",
            )
        )

    # Recommendation-only entries do not change top-level managed/equity totals.
    for campaign_id, cycle in latest_cycle_by_campaign_id.items():
        campaign = campaign_by_id.get(campaign_id)
        if campaign is None:
            continue

        base_id = f"campaign-profit-cycle:{cycle.cycle_uuid}"
        started_at = cycle.calculated_at
        review_status: CapitalPoolStatus = "inactive" if cycle.status in {"REVIEW_REQUIRED", "BELOW_TARGET", "TARGET_REACHED"} else "completed"

        if cycle.compound_amount > 0:
            pools.append(
                CapitalPoolResponse(
                    capital_pool_id=f"{base_id}:compound",
                    capital_pool_type="compounding_recommendation",
                    name=f"{campaign.name} Compounding Recommendation",
                    status=review_status,
                    starting_capital=None,
                    current_equity=None,
                    allocated_capital=None,
                    available_capital=None,
                    reserved_capital=None,
                    realized_pnl=None,
                    unrealized_pnl=None,
                    pnl_percent=None,
                    started_at=started_at,
                    completed_at=cycle.completed_at,
                    related_entity_type="capital_campaign",
                    related_entity_id=str(campaign.uuid),
                    related_page_url=f"/capital-campaigns/{campaign.uuid}",
                    capital_campaign_uuid=str(campaign.uuid),
                    capital_campaign_name=campaign.name,
                    capital_campaign_status=campaign.status,
                    parent_capital_pool_id=None,
                    child_allocations_count=0,
                    notes="Recommendation evidence only. No funds moved.",
                )
            )

        if cycle.withdrawal_amount > 0:
            pools.append(
                CapitalPoolResponse(
                    capital_pool_id=f"{base_id}:withdraw",
                    capital_pool_type="withdrawal_recommendation",
                    name=f"{campaign.name} Withdrawal Recommendation",
                    status=review_status,
                    starting_capital=None,
                    current_equity=None,
                    allocated_capital=None,
                    available_capital=None,
                    reserved_capital=None,
                    realized_pnl=None,
                    unrealized_pnl=None,
                    pnl_percent=None,
                    started_at=started_at,
                    completed_at=cycle.completed_at,
                    related_entity_type="capital_campaign",
                    related_entity_id=str(campaign.uuid),
                    related_page_url=f"/capital-campaigns/{campaign.uuid}",
                    capital_campaign_uuid=str(campaign.uuid),
                    capital_campaign_name=campaign.name,
                    capital_campaign_status=campaign.status,
                    parent_capital_pool_id=None,
                    child_allocations_count=0,
                    notes="Recommendation evidence only. No transfer or payout executed.",
                )
            )

        if cycle.reserve_amount > 0:
            pools.append(
                CapitalPoolResponse(
                    capital_pool_id=f"{base_id}:reserve",
                    capital_pool_type="profit_reserve",
                    name=f"{campaign.name} Profit Reserve",
                    status="inactive",
                    starting_capital=None,
                    current_equity=None,
                    allocated_capital=None,
                    available_capital=None,
                    reserved_capital=None,
                    realized_pnl=None,
                    unrealized_pnl=None,
                    pnl_percent=None,
                    started_at=started_at,
                    completed_at=cycle.completed_at,
                    related_entity_type="capital_campaign",
                    related_entity_id=str(campaign.uuid),
                    related_page_url=f"/capital-campaigns/{campaign.uuid}",
                    capital_campaign_uuid=str(campaign.uuid),
                    capital_campaign_name=campaign.name,
                    capital_campaign_status=campaign.status,
                    parent_capital_pool_id=None,
                    child_allocations_count=0,
                    notes="Reserve recommendation evidence only. Totals unchanged.",
                )
            )

        if cycle.status == "REVIEW_REQUIRED":
            pools.append(
                CapitalPoolResponse(
                    capital_pool_id=f"{base_id}:review",
                    capital_pool_type="policy_review",
                    name=f"{campaign.name} Policy Review",
                    status="inactive",
                    starting_capital=None,
                    current_equity=None,
                    allocated_capital=None,
                    available_capital=None,
                    reserved_capital=None,
                    realized_pnl=None,
                    unrealized_pnl=None,
                    pnl_percent=None,
                    started_at=started_at,
                    completed_at=cycle.completed_at,
                    related_entity_type="capital_campaign",
                    related_entity_id=str(campaign.uuid),
                    related_page_url=f"/capital-campaigns/{campaign.uuid}",
                    capital_campaign_uuid=str(campaign.uuid),
                    capital_campaign_name=campaign.name,
                    capital_campaign_status=campaign.status,
                    parent_capital_pool_id=None,
                    child_allocations_count=0,
                    notes="Operator review required. Accounting recommendation only.",
                )
            )

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
