from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.risk_rule_config import RiskRuleConfig
from app.services.paper.accounting import build_account_snapshot
from app.services.risk.risk_monitor import DEFAULT_RULES


@dataclass(frozen=True, slots=True)
class ExecutionRiskContext:
    account_equity: Decimal
    start_of_day_equity: Decimal
    current_equity: Decimal
    max_position_size_pct: Decimal
    max_daily_loss_pct: Decimal
    high_water_mark_equity: Decimal
    max_drawdown_pct: Decimal
    consecutive_losses_on_pair: int
    cooldown_after_losses: int
    last_loss_at: datetime | None
    cooldown_duration_minutes: Decimal
    evaluation_time: datetime
    data_is_stale: bool
    data_has_gaps: bool
    global_kill_switch_engaged_state: bool
    global_kill_switch_rearm_required: bool
    account_kill_switch_engaged_state: bool
    account_kill_switch_rearm_required: bool


async def _resolve_effective_risk_rules(*, db: AsyncSession, paper_account_id: uuid.UUID) -> dict[str, Decimal | int]:
    override_stmt = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id == paper_account_id)
    override = await db.scalar(override_stmt)

    if override is not None:
        return {
            "max_position_size_pct": Decimal(override.max_position_size_pct),
            "max_daily_loss_pct": Decimal(override.max_daily_loss_pct),
            "max_drawdown_pct": Decimal(override.max_drawdown_pct),
            "cooldown_after_losses": int(override.cooldown_after_losses),
            "cooldown_duration_hours": int(override.cooldown_duration_hours),
        }

    defaults_stmt = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id.is_(None))
    defaults = await db.scalar(defaults_stmt)
    if defaults is not None:
        return {
            "max_position_size_pct": Decimal(defaults.max_position_size_pct),
            "max_daily_loss_pct": Decimal(defaults.max_daily_loss_pct),
            "max_drawdown_pct": Decimal(defaults.max_drawdown_pct),
            "cooldown_after_losses": int(defaults.cooldown_after_losses),
            "cooldown_duration_hours": int(defaults.cooldown_duration_hours),
        }

    return {
        "max_position_size_pct": Decimal(DEFAULT_RULES["max_position_size_pct"]),
        "max_daily_loss_pct": Decimal(DEFAULT_RULES["max_daily_loss_pct"]),
        "max_drawdown_pct": Decimal(DEFAULT_RULES["max_drawdown_pct"]),
        "cooldown_after_losses": int(DEFAULT_RULES["cooldown_after_losses"]),
        "cooldown_duration_hours": int(DEFAULT_RULES["cooldown_duration_hours"]),
    }


async def _resolve_kill_switch_state(*, db: AsyncSession, scope: str, paper_account_id: uuid.UUID | None) -> tuple[bool, bool]:
    statement = select(RiskKillSwitch).where(
        RiskKillSwitch.scope == scope,
        RiskKillSwitch.paper_account_id == paper_account_id,
    )
    state = await db.scalar(statement)
    if state is None:
        return (False, False)
    return (bool(state.engaged), bool(state.rearm_required))


async def _resolve_data_quality_inputs(*, db: AsyncSession, asset_id: uuid.UUID, evaluation_time: datetime) -> tuple[bool, bool]:
    latest_open_stmt = select(Candle.open_time).where(Candle.asset_id == asset_id).order_by(Candle.open_time.desc()).limit(1)
    latest_open = await db.scalar(latest_open_stmt)
    if latest_open is None:
        return (False, False)

    # Use a conservative stale threshold for execution-time gating input.
    stale_cutoff = evaluation_time - timedelta(hours=2)
    return (latest_open < stale_cutoff, False)


async def resolve_execution_risk_context(
    *,
    db: AsyncSession,
    paper_account: PaperAccount,
    asset: Asset,
) -> ExecutionRiskContext:
    now = datetime.now(timezone.utc)

    snapshot = await build_account_snapshot(
        db=db,
        paper_account_id=paper_account.id,
        starting_balance=paper_account.starting_balance,
    )

    effective_rules = await _resolve_effective_risk_rules(db=db, paper_account_id=paper_account.id)
    global_engaged, global_rearm_required = await _resolve_kill_switch_state(db=db, scope="global", paper_account_id=None)
    account_engaged, account_rearm_required = await _resolve_kill_switch_state(
        db=db,
        scope="account",
        paper_account_id=paper_account.id,
    )
    data_is_stale, data_has_gaps = await _resolve_data_quality_inputs(db=db, asset_id=asset.id, evaluation_time=now)

    # Start-of-day and high-water mark persistence are not yet modeled for paper accounts,
    # so execution uses explicit conservative fallbacks from existing account/snapshot values.
    start_of_day_equity = Decimal(paper_account.starting_balance)
    current_equity = Decimal(snapshot.equity)
    high_water_mark_equity = max(start_of_day_equity, current_equity)

    return ExecutionRiskContext(
        account_equity=current_equity,
        start_of_day_equity=start_of_day_equity,
        current_equity=current_equity,
        max_position_size_pct=Decimal(effective_rules["max_position_size_pct"]),
        max_daily_loss_pct=Decimal(effective_rules["max_daily_loss_pct"]),
        high_water_mark_equity=high_water_mark_equity,
        max_drawdown_pct=Decimal(effective_rules["max_drawdown_pct"]),
        consecutive_losses_on_pair=0,
        cooldown_after_losses=int(effective_rules["cooldown_after_losses"]),
        last_loss_at=None,
        cooldown_duration_minutes=Decimal(str(int(effective_rules["cooldown_duration_hours"]) * 60)),
        evaluation_time=now,
        data_is_stale=data_is_stale,
        data_has_gaps=data_has_gaps,
        global_kill_switch_engaged_state=global_engaged,
        global_kill_switch_rearm_required=global_rearm_required,
        account_kill_switch_engaged_state=account_engaged,
        account_kill_switch_rearm_required=account_rearm_required,
    )
