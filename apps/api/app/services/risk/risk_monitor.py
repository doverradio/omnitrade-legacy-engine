from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError, ServiceUnavailableError
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.risk_rule_config import RiskRuleConfig
from app.services.risk.risk_context import RISK_POLICY_DEFAULTS, resolve_effective_risk_policy


DEFAULT_RULES = {
    "max_position_size_pct": Decimal(RISK_POLICY_DEFAULTS["max_position_size_pct"]),
    "max_daily_loss_pct": Decimal(RISK_POLICY_DEFAULTS["max_daily_loss_pct"]),
    "max_drawdown_pct": Decimal(RISK_POLICY_DEFAULTS["max_drawdown_pct"]),
    "default_stop_loss_pct": Decimal(RISK_POLICY_DEFAULTS["default_stop_loss_pct"]),
    "cooldown_after_losses": int(RISK_POLICY_DEFAULTS["cooldown_after_losses"]),
    "cooldown_duration_hours": int(RISK_POLICY_DEFAULTS["cooldown_duration_hours"]),
}


@dataclass(frozen=True, slots=True)
class RiskStatusUsage:
    used: Decimal
    limit: Decimal
    pct_used: Decimal


@dataclass(frozen=True, slots=True)
class RiskStatusData:
    global_engaged: bool
    global_engaged_by: str | None
    global_engaged_at: datetime | None
    global_reason: str | None
    account_id: uuid.UUID
    account_engaged: bool
    account_reason: str | None
    daily_loss: RiskStatusUsage
    drawdown: RiskStatusUsage
    active_cooldowns: list[dict[str, str]]
    active_no_trade_zones: list[dict[str, str]]
    active_cooldowns_state: str
    active_no_trade_zones_state: str
    policy_source: str
    daily_loss_input_source: str
    drawdown_input_source: str


@dataclass(frozen=True, slots=True)
class KillSwitchChangeResult:
    scope: str
    account_id: uuid.UUID | None
    engaged: bool
    actor: str
    changed_at: datetime | None


@dataclass(frozen=True, slots=True)
class RiskRulesData:
    account_id: uuid.UUID | None
    rules: dict[str, Decimal | int]
    is_override: bool
    system_defaults: dict[str, Decimal | int]


async def _get_account_or_404(db: AsyncSession, account_id: uuid.UUID) -> PaperAccount:
    account = await db.get(PaperAccount, account_id)
    if account is None:
        raise NotFoundError(
            "Paper account not found",
            details={"account_id": str(account_id)},
        )
    return account


def _compute_pct_used(used: Decimal, limit: Decimal) -> Decimal:
    if limit == Decimal("0"):
        return Decimal("0")
    return used / limit


async def _get_or_create_kill_switch(
    db: AsyncSession,
    *,
    scope: str,
    account_id: uuid.UUID | None,
) -> RiskKillSwitch:
    statement = select(RiskKillSwitch).where(
        RiskKillSwitch.scope == scope,
        RiskKillSwitch.paper_account_id == account_id,
    )
    switch = await db.scalar(statement)
    if switch is not None:
        return switch

    switch = RiskKillSwitch(
        scope=scope,
        paper_account_id=account_id,
        engaged=False,
        rearm_required=False,
    )
    db.add(switch)
    await db.flush()
    return switch


async def _get_or_create_default_rules(db: AsyncSession) -> RiskRuleConfig:
    statement = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id.is_(None))
    config = await db.scalar(statement)
    if config is not None:
        return config

    config = RiskRuleConfig(
        paper_account_id=None,
        max_position_size_pct=DEFAULT_RULES["max_position_size_pct"],
        max_daily_loss_pct=DEFAULT_RULES["max_daily_loss_pct"],
        max_drawdown_pct=DEFAULT_RULES["max_drawdown_pct"],
        default_stop_loss_pct=DEFAULT_RULES["default_stop_loss_pct"],
        cooldown_after_losses=DEFAULT_RULES["cooldown_after_losses"],
        cooldown_duration_hours=DEFAULT_RULES["cooldown_duration_hours"],
    )
    db.add(config)
    await db.flush()
    return config


def _config_to_rules(config: RiskRuleConfig) -> dict[str, Decimal | int]:
    return {
        "max_position_size_pct": Decimal(config.max_position_size_pct),
        "max_daily_loss_pct": Decimal(config.max_daily_loss_pct),
        "max_drawdown_pct": Decimal(config.max_drawdown_pct),
        "default_stop_loss_pct": Decimal(config.default_stop_loss_pct),
        "cooldown_after_losses": int(config.cooldown_after_losses),
        "cooldown_duration_hours": int(config.cooldown_duration_hours),
    }


def _validate_rules(rules: dict[str, Decimal | int]) -> None:
    pct_fields = (
        "max_position_size_pct",
        "max_daily_loss_pct",
        "max_drawdown_pct",
        "default_stop_loss_pct",
    )
    for field_name in pct_fields:
        value = Decimal(rules[field_name])
        if value <= Decimal("0") or value > Decimal("1"):
            raise InvalidRequestError(
                f"{field_name} must be within (0, 1]",
                details={"field": field_name},
            )

    if int(rules["cooldown_after_losses"]) < 0:
        raise InvalidRequestError(
            "cooldown_after_losses must be >= 0",
            details={"field": "cooldown_after_losses"},
        )
    if int(rules["cooldown_duration_hours"]) < 0:
        raise InvalidRequestError(
            "cooldown_duration_hours must be >= 0",
            details={"field": "cooldown_duration_hours"},
        )


def _is_loosening(
    current_rules: dict[str, Decimal | int],
    next_rules: dict[str, Decimal | int],
) -> bool:
    max_fields = (
        "max_position_size_pct",
        "max_daily_loss_pct",
        "max_drawdown_pct",
        "default_stop_loss_pct",
    )
    if any(Decimal(next_rules[field]) > Decimal(current_rules[field]) for field in max_fields):
        return True
    if int(next_rules["cooldown_after_losses"]) < int(current_rules["cooldown_after_losses"]):
        return True
    if int(next_rules["cooldown_duration_hours"]) < int(current_rules["cooldown_duration_hours"]):
        return True
    return False


async def get_risk_status(*, db: AsyncSession, account_id: uuid.UUID) -> RiskStatusData:
    account = await _get_account_or_404(db, account_id)
    effective_policy = await resolve_effective_risk_policy(db=db, paper_account_id=account.id)

    global_stmt = select(RiskKillSwitch).where(
        RiskKillSwitch.scope == "global",
        RiskKillSwitch.paper_account_id.is_(None),
    )
    account_stmt = select(RiskKillSwitch).where(
        RiskKillSwitch.scope == "account",
        RiskKillSwitch.paper_account_id == account.id,
    )
    global_switch = await db.scalar(global_stmt)
    account_switch = await db.scalar(account_stmt)

    if global_switch is None or account_switch is None:
        raise ServiceUnavailableError(
            "Risk status unavailable: kill switch state is unknown",
            details={"account_id": str(account.id)},
        )

    cooldown_event = await db.scalar(
        select(RiskEvent)
        .where(RiskEvent.paper_account_id == account.id)
        .where(RiskEvent.event_type == "cooldown")
        .where(RiskEvent.action_taken == "blocked")
        .order_by(RiskEvent.created_at.desc())
        .limit(1)
    )
    no_trade_event = await db.scalar(
        select(RiskEvent)
        .where(RiskEvent.paper_account_id == account.id)
        .where(RiskEvent.event_type == "no_trade_zone")
        .where(RiskEvent.action_taken == "blocked")
        .order_by(RiskEvent.created_at.desc())
        .limit(1)
    )

    active_cooldowns_state = (
        "active_state_unavailable_from_risk_events" if cooldown_event is not None else "unavailable_not_persisted"
    )
    active_no_trade_zones_state = (
        "active_state_unavailable_from_risk_events" if no_trade_event is not None else "unavailable_not_persisted"
    )

    equity = Decimal(account.current_cash_balance)
    starting_balance = Decimal(account.starting_balance)
    if starting_balance <= Decimal("0"):
        daily_limit = Decimal("0")
        drawdown_limit = Decimal("0")
    else:
        daily_limit = starting_balance * effective_policy.max_daily_loss_pct
        drawdown_limit = starting_balance * effective_policy.max_drawdown_pct

    loss = max(Decimal("0"), starting_balance - equity)

    daily_usage = RiskStatusUsage(
        used=loss,
        limit=daily_limit,
        pct_used=_compute_pct_used(loss, daily_limit),
    )
    drawdown_usage = RiskStatusUsage(
        used=loss,
        limit=drawdown_limit,
        pct_used=_compute_pct_used(loss, drawdown_limit),
    )

    paused_reason: str | None = None
    account_engaged = bool(account_switch.engaged)
    if global_switch.engaged:
        paused_reason = "global_kill_switch_engaged"
    elif account_engaged:
        paused_reason = "account_kill_switch_engaged"

    return RiskStatusData(
        global_engaged=bool(global_switch.engaged),
        global_engaged_by=global_switch.changed_by,
        global_engaged_at=global_switch.changed_at,
        global_reason=global_switch.reason,
        account_id=account.id,
        account_engaged=bool(global_switch.engaged or account_engaged),
        account_reason=paused_reason,
        daily_loss=daily_usage,
        drawdown=drawdown_usage,
        active_cooldowns=[],
        active_no_trade_zones=[],
        active_cooldowns_state=active_cooldowns_state,
        active_no_trade_zones_state=active_no_trade_zones_state,
        policy_source=effective_policy.source,
        daily_loss_input_source="current_cash_balance",
        drawdown_input_source="current_cash_balance",
    )


async def enable_kill_switch(
    *,
    db: AsyncSession,
    scope: str,
    account_id: uuid.UUID | None,
    reason: str,
    confirm: bool,
    actor: str,
) -> KillSwitchChangeResult:
    if not confirm:
        raise InvalidRequestError(
            "confirm must be true to enable kill switch",
            details={"field": "confirm"},
        )

    if scope not in {"global", "account"}:
        raise InvalidRequestError("scope must be one of: global, account", details={"field": "scope"})
    if scope == "account" and account_id is None:
        raise InvalidRequestError(
            "account_id is required for account scope",
            details={"field": "account_id"},
        )
    if scope == "global" and account_id is not None:
        raise InvalidRequestError(
            "account_id must be null for global scope",
            details={"field": "account_id"},
        )
    if account_id is not None:
        await _get_account_or_404(db, account_id)

    async with db.begin():
        switch = await _get_or_create_kill_switch(db, scope=scope, account_id=account_id)
        before_state = {
            "engaged": bool(switch.engaged),
            "rearm_required": bool(switch.rearm_required),
            "reason": switch.reason,
            "changed_by": switch.changed_by,
        }

        switch.engaged = True
        switch.rearm_required = True
        switch.reason = reason
        switch.changed_by = actor
        await db.flush()

        after_state = {
            "engaged": bool(switch.engaged),
            "rearm_required": bool(switch.rearm_required),
            "reason": switch.reason,
            "changed_by": switch.changed_by,
        }
        db.add(
            AuditLog(
                actor=actor,
                action="risk.kill_switch.enable",
                entity_type="risk_kill_switch",
                entity_id=switch.id,
                before_state=before_state,
                after_state=after_state,
            )
        )

    return KillSwitchChangeResult(
        scope=scope,
        account_id=account_id,
        engaged=True,
        actor=actor,
        changed_at=switch.changed_at,
    )


async def disable_kill_switch(
    *,
    db: AsyncSession,
    scope: str,
    account_id: uuid.UUID | None,
    reason: str,
    confirm: bool,
    actor: str,
) -> KillSwitchChangeResult:
    if not confirm:
        raise InvalidRequestError(
            "confirm must be true to disable kill switch",
            details={"field": "confirm"},
        )

    if scope not in {"global", "account"}:
        raise InvalidRequestError("scope must be one of: global, account", details={"field": "scope"})
    if scope == "account" and account_id is None:
        raise InvalidRequestError(
            "account_id is required for account scope",
            details={"field": "account_id"},
        )
    if scope == "global" and account_id is not None:
        raise InvalidRequestError(
            "account_id must be null for global scope",
            details={"field": "account_id"},
        )
    if account_id is not None:
        await _get_account_or_404(db, account_id)

    async with db.begin():
        switch = await _get_or_create_kill_switch(db, scope=scope, account_id=account_id)
        before_state = {
            "engaged": bool(switch.engaged),
            "rearm_required": bool(switch.rearm_required),
            "reason": switch.reason,
            "changed_by": switch.changed_by,
        }

        switch.engaged = False
        switch.rearm_required = False
        switch.reason = reason
        switch.changed_by = actor
        await db.flush()

        after_state = {
            "engaged": bool(switch.engaged),
            "rearm_required": bool(switch.rearm_required),
            "reason": switch.reason,
            "changed_by": switch.changed_by,
        }
        db.add(
            AuditLog(
                actor=actor,
                action="risk.kill_switch.disable",
                entity_type="risk_kill_switch",
                entity_id=switch.id,
                before_state=before_state,
                after_state=after_state,
            )
        )

    return KillSwitchChangeResult(
        scope=scope,
        account_id=account_id,
        engaged=False,
        actor=actor,
        changed_at=switch.changed_at,
    )


async def get_risk_rules(*, db: AsyncSession, account_id: uuid.UUID | None) -> RiskRulesData:
    if account_id is not None:
        await _get_account_or_404(db, account_id)

    default_config = await _get_or_create_default_rules(db)
    default_rules = _config_to_rules(default_config)

    is_override = False
    active_rules = default_rules

    if account_id is not None:
        statement = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id == account_id)
        account_config = await db.scalar(statement)
        if account_config is not None:
            is_override = True
            active_rules = _config_to_rules(account_config)

    return RiskRulesData(
        account_id=account_id,
        rules=active_rules,
        is_override=is_override,
        system_defaults=default_rules,
    )


async def patch_risk_rules(
    *,
    db: AsyncSession,
    account_id: uuid.UUID | None,
    rules_patch: dict[str, Decimal | int | None],
    confirm_loosening: bool | None,
    actor: str,
) -> RiskRulesData:
    if account_id is not None:
        await _get_account_or_404(db, account_id)

    async with db.begin():
        default_config = await _get_or_create_default_rules(db)
        target_stmt = select(RiskRuleConfig).where(RiskRuleConfig.paper_account_id == account_id)
        target_config = await db.scalar(target_stmt)
        if target_config is None:
            if account_id is None:
                target_config = default_config
            else:
                target_config = RiskRuleConfig(
                    paper_account_id=account_id,
                    max_position_size_pct=default_config.max_position_size_pct,
                    max_daily_loss_pct=default_config.max_daily_loss_pct,
                    max_drawdown_pct=default_config.max_drawdown_pct,
                    default_stop_loss_pct=default_config.default_stop_loss_pct,
                    cooldown_after_losses=default_config.cooldown_after_losses,
                    cooldown_duration_hours=default_config.cooldown_duration_hours,
                )
                db.add(target_config)
                await db.flush()

        before_rules = _config_to_rules(target_config)
        next_rules = dict(before_rules)
        for key, value in rules_patch.items():
            if value is not None:
                next_rules[key] = value

        _validate_rules(next_rules)
        if _is_loosening(before_rules, next_rules) and not bool(confirm_loosening):
            raise InvalidRequestError(
                "confirm_loosening must be true when relaxing risk limits",
                details={"field": "confirm_loosening"},
            )

        target_config.max_position_size_pct = Decimal(next_rules["max_position_size_pct"])
        target_config.max_daily_loss_pct = Decimal(next_rules["max_daily_loss_pct"])
        target_config.max_drawdown_pct = Decimal(next_rules["max_drawdown_pct"])
        target_config.default_stop_loss_pct = Decimal(next_rules["default_stop_loss_pct"])
        target_config.cooldown_after_losses = int(next_rules["cooldown_after_losses"])
        target_config.cooldown_duration_hours = int(next_rules["cooldown_duration_hours"])
        await db.flush()

        db.add(
            AuditLog(
                actor=actor,
                action="risk.rules.patch",
                entity_type="risk_rule_config",
                entity_id=target_config.id,
                before_state=before_rules,
                after_state=next_rules,
            )
        )

    default_now = await _get_or_create_default_rules(db)
    defaults = _config_to_rules(default_now)

    return RiskRulesData(
        account_id=account_id,
        rules=next_rules,
        is_override=account_id is not None,
        system_defaults=defaults,
    )