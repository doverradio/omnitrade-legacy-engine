from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.asset import Asset
from app.models.candle import Candle
from app.models.paper_account import PaperAccount
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.risk_rule_config import RiskRuleConfig
from app.services.risk.equity_evidence import resolve_equity_risk_evidence


RISK_POLICY_DEFAULTS = {
    "max_position_size_pct": Decimal("0.10"),
    "max_daily_loss_pct": Decimal("0.03"),
    "max_drawdown_pct": Decimal("0.10"),
    "default_stop_loss_pct": Decimal("0.03"),
    "cooldown_after_losses": 3,
    "cooldown_duration_hours": 24,
}


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
    global_kill_switch_engaged_state: bool | None
    global_kill_switch_rearm_required: bool | None
    account_kill_switch_engaged_state: bool | None
    account_kill_switch_rearm_required: bool | None
    global_kill_switch_state_observed: bool
    account_kill_switch_state_observed: bool
    risk_policy_source: str
    runtime_cooldown_state: str
    runtime_no_trade_zone_state: str
    start_of_day_equity_source: str
    high_water_mark_equity_source: str


@dataclass(frozen=True, slots=True)
class EffectiveRiskPolicy:
    max_position_size_pct: Decimal
    max_daily_loss_pct: Decimal
    max_drawdown_pct: Decimal
    default_stop_loss_pct: Decimal
    cooldown_after_losses: int
    cooldown_duration_hours: int
    source: str


async def resolve_effective_risk_policy(*, db: AsyncSession, paper_account_id: uuid.UUID) -> EffectiveRiskPolicy:
    override_stmt = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id == paper_account_id)
    override = await db.scalar(override_stmt)
    if override is not None:
        return EffectiveRiskPolicy(
            max_position_size_pct=Decimal(override.max_position_size_pct),
            max_daily_loss_pct=Decimal(override.max_daily_loss_pct),
            max_drawdown_pct=Decimal(override.max_drawdown_pct),
            default_stop_loss_pct=Decimal(override.default_stop_loss_pct),
            cooldown_after_losses=int(override.cooldown_after_losses),
            cooldown_duration_hours=int(override.cooldown_duration_hours),
            source="account_override",
        )

    defaults_stmt = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id.is_(None))
    defaults = await db.scalar(defaults_stmt)
    if defaults is not None:
        return EffectiveRiskPolicy(
            max_position_size_pct=Decimal(defaults.max_position_size_pct),
            max_daily_loss_pct=Decimal(defaults.max_daily_loss_pct),
            max_drawdown_pct=Decimal(defaults.max_drawdown_pct),
            default_stop_loss_pct=Decimal(defaults.default_stop_loss_pct),
            cooldown_after_losses=int(defaults.cooldown_after_losses),
            cooldown_duration_hours=int(defaults.cooldown_duration_hours),
            source="system_default_config",
        )

    return EffectiveRiskPolicy(
        max_position_size_pct=Decimal(RISK_POLICY_DEFAULTS["max_position_size_pct"]),
        max_daily_loss_pct=Decimal(RISK_POLICY_DEFAULTS["max_daily_loss_pct"]),
        max_drawdown_pct=Decimal(RISK_POLICY_DEFAULTS["max_drawdown_pct"]),
        default_stop_loss_pct=Decimal(RISK_POLICY_DEFAULTS["default_stop_loss_pct"]),
        cooldown_after_losses=int(RISK_POLICY_DEFAULTS["cooldown_after_losses"]),
        cooldown_duration_hours=int(RISK_POLICY_DEFAULTS["cooldown_duration_hours"]),
        source="module_fallback_default",
    )


async def _resolve_effective_risk_rules(*, db: AsyncSession, paper_account_id: uuid.UUID) -> dict[str, Decimal | int]:
    policy = await resolve_effective_risk_policy(db=db, paper_account_id=paper_account_id)
    return {
        "max_position_size_pct": policy.max_position_size_pct,
        "max_daily_loss_pct": policy.max_daily_loss_pct,
        "max_drawdown_pct": policy.max_drawdown_pct,
        "cooldown_after_losses": policy.cooldown_after_losses,
        "cooldown_duration_hours": policy.cooldown_duration_hours,
        "source": policy.source,
    }


async def _resolve_kill_switch_state(*, db: AsyncSession, scope: str, paper_account_id: uuid.UUID | None) -> tuple[bool | None, bool | None]:
    statement = select(RiskKillSwitch).where(
        RiskKillSwitch.scope == scope,
        RiskKillSwitch.paper_account_id == paper_account_id,
    )
    state = await db.scalar(statement)
    if state is None:
        return (None, None)
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
    settings = get_settings()

    effective_rules = await _resolve_effective_risk_rules(db=db, paper_account_id=paper_account.id)
    global_engaged, global_rearm_required = await _resolve_kill_switch_state(db=db, scope="global", paper_account_id=None)
    account_engaged, account_rearm_required = await _resolve_kill_switch_state(
        db=db,
        scope="account",
        paper_account_id=paper_account.id,
    )
    equity_evidence = await resolve_equity_risk_evidence(
        db=db,
        paper_account=paper_account,
        actor="risk_context",
        max_price_age_seconds=settings.live_crypto_price_max_age_seconds,
    )

    candle_data_is_stale, candle_data_has_gaps = await _resolve_data_quality_inputs(db=db, asset_id=asset.id, evaluation_time=now)
    valuation_is_stale = equity_evidence.valuation.valuation_state == "stale_price_evidence"
    valuation_has_gaps = equity_evidence.valuation.valuation_state in {"missing_price_evidence", "inconsistent_account_state"}
    baseline_has_gaps = not equity_evidence.baseline.baseline_ready
    reconciliation_has_gaps = (
        equity_evidence.unresolved_reconciliation_count > 0
        or equity_evidence.unknown_provider_order_count > 0
    )

    data_is_stale = candle_data_is_stale or valuation_is_stale
    data_has_gaps = candle_data_has_gaps or valuation_has_gaps or baseline_has_gaps or reconciliation_has_gaps

    start_of_day_equity = equity_evidence.baseline.start_of_day_equity
    current_equity = equity_evidence.valuation.current_equity
    high_water_mark_equity = equity_evidence.baseline.high_water_mark_equity

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
        global_kill_switch_state_observed=True,
        account_kill_switch_state_observed=True,
        risk_policy_source=str(effective_rules["source"]),
        runtime_cooldown_state="unavailable_not_persisted",
        runtime_no_trade_zone_state="unavailable_not_persisted",
        start_of_day_equity_source=equity_evidence.baseline.start_of_day_source,
        high_water_mark_equity_source=equity_evidence.baseline.high_water_mark_source,
    )
