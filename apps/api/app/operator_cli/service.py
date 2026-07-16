from __future__ import annotations

import re
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
import uuid

from sqlalchemy import desc, func, select

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.asset import Asset
from app.models.autonomous_capital_mandate import AutonomousCapitalMandate
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.exchange_connection import ExchangeConnection
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.paper_account import PaperAccount
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.models.venue_commissioning_run import VenueCommissioningRun
from app.models.strategy import Strategy
from app.models.trade import Trade
from app.models.validation_run_event import ValidationRunEvent
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.autonomous_cycle import AutonomousCycleRequest, run_autonomous_preview_cycle
from app.services.canonical_campaign_binding import (
    CanonicalCampaignBindingRequest,
    LegacyCampaignTransitionRequest,
    bind_canonical_campaign_runtime as _bind_canonical_campaign_runtime,
    fetch_legacy_campaign_transition_audit as _fetch_legacy_campaign_transition_audit,
    fetch_canonical_campaign_binding_audit as _fetch_canonical_campaign_binding_audit,
    inspect_canonical_campaign_binding as _inspect_canonical_campaign_binding,
    inspect_legacy_campaign_transition as _inspect_legacy_campaign_transition,
    rollback_legacy_campaign_transition as _rollback_legacy_campaign_transition,
    transition_legacy_campaign_to_canonical_successor as _transition_legacy_campaign_to_canonical_successor,
)
from app.services.canonical_preview_package import (
    CanonicalPreviewPackageActivationRequest,
    CanonicalPreviewPackageAuthorizeRequest,
    CanonicalPreviewPackageCreateRequest,
    CanonicalPreviewPackageDryRunRequest,
    CanonicalPreviewPackagePauseRequest,
    CanonicalPreviewPackageRevokeRequest,
    activate_canonical_proving_campaign,
    authorize_canonical_preview_package,
    create_canonical_preview_package,
    get_canonical_preview_package,
    get_canonical_proving_activation_status,
    list_canonical_preview_package_history,
    pause_canonical_proving_activation,
    revoke_canonical_proving_activation,
    run_dry_run_for_canonical_preview_package,
)
from app.services.capital_campaign_orchestration import (
    fetch_campaign_orchestration_history as _fetch_campaign_orchestration_history,
    fetch_campaign_orchestration_readiness as _fetch_campaign_orchestration_readiness,
    fetch_campaign_orchestration_status as _fetch_campaign_orchestration_status,
    run_campaign_orchestration_preview_for_candle,
)
from app.services.paper.accounting import build_account_snapshot
from app.services.risk import risk_monitor
from app.services.risk.equity_evidence import resolve_equity_risk_evidence
from app.services.risk.risk_context import resolve_effective_risk_policy
from app.services.strategy_outcomes import fetch_strategy_scorecards


_EXECUTION_FORENSICS_MAX_SINCE_CYCLES = 200


def _coerce_decimal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _resolve_since_datetime(value: str) -> datetime:
    raw = value.strip()
    lowered = raw.lower()
    if lowered in {"now", "0", "0m", "0h", "0d"}:
        return datetime.now(timezone.utc)

    relative = re.fullmatch(r"(\d+)\s+(minute|minutes|hour|hours|day|days)\s+ago", lowered)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if "minute" in unit:
            return datetime.now(timezone.utc) - timedelta(minutes=amount)
        if "hour" in unit:
            return datetime.now(timezone.utc) - timedelta(hours=amount)
        return datetime.now(timezone.utc) - timedelta(days=amount)

    candidate = raw.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_uuid_list(values: Any) -> list[uuid.UUID]:
    if not isinstance(values, list):
        return []
    out: list[uuid.UUID] = []
    for value in values:
        try:
            out.append(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def _decimal_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _sum_trade_quantity(trades: list[Trade], *, side: str) -> Decimal:
    total = Decimal("0")
    for trade in trades:
        if trade.side == side:
            total += Decimal(str(trade.quantity))
    return total


def _infer_non_candidate_reason(signals: list[Signal]) -> str:
    if not signals:
        return "UNPROVEN"
    actionable = [item for item in signals if item.action in {"buy", "sell"}]
    if not actionable:
        return "HOLD"
    return "UNPROVEN"


def _event_payload_campaign_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        campaign_id = metadata.get("campaign_id")
        if campaign_id is not None:
            return str(campaign_id)
    return None


async def _compute_position_quantity(
    *,
    db: Any,
    paper_account_id: uuid.UUID,
    asset_id: uuid.UUID,
    executed_at: datetime,
    include_trade_at_timestamp: bool,
) -> Decimal:
    trades = list(
        (
            await db.execute(
                select(Trade)
                .where(Trade.paper_account_id == paper_account_id)
                .where(Trade.asset_id == asset_id)
                .where(Trade.executed_at <= executed_at)
                .order_by(Trade.executed_at.asc(), Trade.id.asc())
            )
        ).scalars().all()
    )
    total = Decimal("0")
    for trade in trades:
        if not include_trade_at_timestamp and trade.executed_at == executed_at:
            continue
        qty = Decimal(str(trade.quantity))
        if trade.side == "buy":
            total += qty
        elif trade.side == "sell":
            total -= qty
    return max(Decimal("0"), total)


def _execution_summary_from_audits(audit_rows: list[dict[str, Any]], trades: list[Trade]) -> dict[str, Any]:
    actions = {str(item.get("action") or "") for item in audit_rows}
    service_called = any(action.startswith("signal_execution") for action in actions) or bool(trades)
    rejected = any("rejected" in action for action in actions)
    skipped = any("duplicate" in action for action in actions)
    errored = any("failed" in action for action in actions)
    return {
        "execution_service_called": service_called,
        "order_creation_reason": "paper_internal_sim_creates_trade_directly" if trades else "paper_order_model_absent",
        "trade_created": bool(trades),
        "rejected": rejected,
        "skipped": skipped,
        "error": errored,
    }


async def _build_cycle_forensics(*, db: Any, cycle: AutonomousCycleRun) -> dict[str, Any]:
    decision: DecisionRecord | None = None
    if cycle.decision_record_id is not None:
        decision = await db.get(DecisionRecord, cycle.decision_record_id)

    signal_ids: list[uuid.UUID] = []
    if decision is not None:
        source_lineage = decision.source_lineage or {}
        signal_ids = _safe_uuid_list(source_lineage.get("signals"))

    signals: list[Signal] = []
    if signal_ids:
        signals = list(
            (
                await db.execute(
                    select(Signal)
                    .where(Signal.id.in_(signal_ids))
                    .order_by(Signal.created_at.asc(), Signal.id.asc())
                )
            ).scalars().all()
        )

    strategy_map: dict[uuid.UUID, Strategy] = {}
    if signals:
        strategy_ids = sorted({item.strategy_id for item in signals}, key=str)
        if strategy_ids:
            strategies = list((await db.execute(select(Strategy).where(Strategy.id.in_(strategy_ids)))).scalars().all())
            strategy_map = {item.id: item for item in strategies}

    asset_map: dict[uuid.UUID, Asset] = {}
    if signals:
        asset_ids = sorted({item.asset_id for item in signals}, key=str)
        if asset_ids:
            assets = list((await db.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars().all())
            asset_map = {item.id: item for item in assets}

    risk_events: list[RiskEvent] = []
    if signal_ids:
        risk_events = list(
            (
                await db.execute(
                    select(RiskEvent)
                    .where(RiskEvent.related_signal_id.in_(signal_ids))
                    .order_by(RiskEvent.created_at.asc(), RiskEvent.id.asc())
                )
            ).scalars().all()
        )
    if cycle.risk_event_id is not None and all(item.id != cycle.risk_event_id for item in risk_events):
        extra_event = await db.get(RiskEvent, cycle.risk_event_id)
        if extra_event is not None:
            risk_events.append(extra_event)

    trades: list[Trade] = []
    if signal_ids:
        trades = list(
            (
                await db.execute(
                    select(Trade)
                    .where(Trade.signal_id.in_(signal_ids))
                    .order_by(Trade.executed_at.asc(), Trade.id.asc())
                )
            ).scalars().all()
        )

    audit_rows: list[dict[str, Any]] = []
    if signal_ids:
        audit_rows = [
            {
                "id": item.id,
                "created_at": item.created_at,
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "before_state": item.before_state,
                "after_state": item.after_state,
            }
            for item in (
                (
                    await db.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "signal")
                        .where(AuditLog.entity_id.in_(signal_ids))
                        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    )
                ).scalars().all()
            )
        ]

    execution_summary = _execution_summary_from_audits(audit_rows, trades)

    interval = decision.timeframe if decision is not None else None
    provider = None
    latest_candle_time = None
    primary_asset_id = signals[0].asset_id if signals else None
    primary_asset = asset_map.get(primary_asset_id) if primary_asset_id is not None else None
    if primary_asset is not None:
        provider = primary_asset.exchange
        if interval is None:
            interval = (cycle.cycle_context or {}).get("strategy_interval") if isinstance(cycle.cycle_context, dict) else None
        if interval is not None:
            latest_candle_time = await db.scalar(
                select(Candle.close_time)
                .where(Candle.asset_id == primary_asset.id)
                .where(Candle.interval == interval)
                .order_by(Candle.open_time.desc())
                .limit(1)
            )

    candidate = any(item.action in {"buy", "sell"} for item in signals)
    candidate_reason = None
    if not candidate:
        candidate_reason = _infer_non_candidate_reason(signals)

    accounting_entries: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    trade_fill_evidence = 0
    balance_change_observed = 0
    position_change_observed = 0
    position_change_unproven = 0
    balance_change_unproven = 0
    for trade in trades:
        total_fees += Decimal(str(trade.fee))
        before_position = await _compute_position_quantity(
            db=db,
            paper_account_id=trade.paper_account_id,
            asset_id=trade.asset_id,
            executed_at=trade.executed_at,
            include_trade_at_timestamp=False,
        )
        after_position = await _compute_position_quantity(
            db=db,
            paper_account_id=trade.paper_account_id,
            asset_id=trade.asset_id,
            executed_at=trade.executed_at,
            include_trade_at_timestamp=True,
        )

        trade_audit = await db.scalar(
            select(AuditLog)
            .where(AuditLog.entity_type == "trade")
            .where(AuditLog.entity_id == trade.id)
            .where(AuditLog.action == "paper_trade_simulated")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(1)
        )
        before_balance = None
        after_balance = None
        if trade_audit is not None:
            before_state = trade_audit.before_state if isinstance(trade_audit.before_state, dict) else {}
            after_state = trade_audit.after_state if isinstance(trade_audit.after_state, dict) else {}
            before_balance = before_state.get("cash_balance")
            after_balance = after_state.get("cash_balance")
            trade_fill_evidence += 1
        if before_balance is not None and after_balance is not None and str(before_balance) != str(after_balance):
            balance_change_observed += 1
        elif before_balance is None or after_balance is None:
            balance_change_unproven += 1

        if before_position != after_position:
            position_change_observed += 1
        elif before_position is None or after_position is None:
            position_change_unproven += 1

        accounting_entries.append(
            {
                "trade_id": trade.id,
                "paper_account_id": trade.paper_account_id,
                "asset_id": trade.asset_id,
                "balance_before": before_balance,
                "balance_after": after_balance,
                "position_before": _decimal_str(before_position),
                "position_after": _decimal_str(after_position),
                "fee": _decimal_str(trade.fee),
                "executed_at": trade.executed_at,
            }
        )

    roster_runs = list(
        (
            await db.execute(
                select(StrategyRosterRun)
                .where(StrategyRosterRun.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterRun.started_at.asc(), StrategyRosterRun.roster_run_id.asc())
            )
        ).scalars().all()
    )

    roster_proposals = list(
        (
            await db.execute(
                select(StrategyRosterProposal)
                .where(StrategyRosterProposal.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterProposal.evaluated_at.asc(), StrategyRosterProposal.proposal_id.asc())
            )
        ).scalars().all()
    )

    outcome_score_rows = list(
        (
            await db.execute(
                select(StrategyRosterProposalOutcome)
                .join(
                    StrategyRosterProposal,
                    StrategyRosterProposalOutcome.proposal_id == StrategyRosterProposal.proposal_id,
                )
                .where(StrategyRosterProposal.scheduled_cycle_id == cycle.cycle_id)
                .order_by(StrategyRosterProposalOutcome.evaluated_at.asc(), StrategyRosterProposalOutcome.outcome_id.asc())
            )
        ).scalars().all()
    )

    event_start = cycle.started_at - timedelta(minutes=5)
    event_end = (cycle.completed_at or cycle.started_at) + timedelta(minutes=30)
    research_events = list(
        (
            await db.execute(
                select(ValidationRunEvent)
                .where(ValidationRunEvent.created_at >= event_start)
                .where(ValidationRunEvent.created_at <= event_end)
                .where(ValidationRunEvent.event_type.like("RESEARCH_CYCLE_%"))
                .order_by(ValidationRunEvent.created_at.asc(), ValidationRunEvent.id.asc())
            )
        ).scalars().all()
    )

    signal_rows = []
    for signal in signals:
        strategy = strategy_map.get(signal.strategy_id)
        signal_rows.append(
            {
                "signal_id": signal.id,
                "strategy_id": signal.strategy_id,
                "strategy": None if strategy is None else strategy.slug,
                "action": signal.action.upper(),
                "confidence": _decimal_str(signal.ai_confidence),
                "reason": None,
                "status": signal.status,
                "asset_id": signal.asset_id,
            }
        )

    cycle_context = cycle.cycle_context if isinstance(cycle.cycle_context, dict) else {}
    cycle_handoff = cycle_context.get("execution_handoff") if isinstance(cycle_context.get("execution_handoff"), dict) else {}
    strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
    strategy_signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context.get("signal_payload"), dict) else {}
    autonomous_proposed_action = (getattr(cycle, "proposed_action", None) or strategy_signal_payload.get("action") or "HOLD").upper()
    if autonomous_proposed_action not in {"BUY", "SELL", "HOLD"}:
        autonomous_proposed_action = "HOLD"

    roster_buy = sum(1 for item in roster_proposals if str(item.action).upper() == "BUY")
    roster_sell = sum(1 for item in roster_proposals if str(item.action).upper() == "SELL")
    roster_hold = sum(1 for item in roster_proposals if str(item.action).upper() == "HOLD")
    roster_mode = "SHADOW"
    roster_executable = "NO"
    roster_reason = "Strategy Roster proposals are shadow research observations and never executable orders"

    canonical_signal = cycle_handoff.get("canonical_signal") if isinstance(cycle_handoff.get("canonical_signal"), dict) else None

    if cycle_handoff:
        execution_handoff_status = str(cycle_handoff.get("execution_handoff") or "UNPROVEN")
        cycle_handoff_status = str(cycle_handoff.get("status") or "UNPROVEN")
        if cycle_handoff_status in {"PAPER_EXECUTION_FAILED", "PAPER_EXECUTION_REJECTED", "PAPER_EXECUTION_SKIPPED"}:
            execution_handoff_blocker = str(cycle_handoff.get("exact_result") or cycle_handoff_status)
        else:
            execution_handoff_blocker = "NOT APPLICABLE"
    elif signal_rows:
        execution_handoff_status = "LEGACY_SIGNAL_PIPELINE"
        execution_handoff_blocker = "NOT APPLICABLE"
    elif autonomous_proposed_action in {"BUY", "SELL"}:
        execution_handoff_status = "NOT IMPLEMENTED"
        execution_handoff_blocker = "AUTONOMOUS_CANONICAL_SIGNAL_HANDOFF_NOT_IMPLEMENTED"
    else:
        execution_handoff_status = "NOT APPLICABLE"
        execution_handoff_blocker = "HOLD_ACTION"

    summary = "No legacy executable signals linked to this autonomous cycle"
    if cycle_handoff:
        summary = str(cycle_handoff.get("status") or "UNPROVEN")
    elif candidate and execution_summary.get("trade_created"):
        summary = "Actionable signal became paper trade"
    elif candidate and execution_summary.get("rejected"):
        summary = "Actionable signal rejected before trade"
    elif candidate and execution_summary.get("skipped"):
        summary = "Actionable signal skipped"
    elif candidate and not execution_summary.get("execution_service_called"):
        summary = "Actionable signal not executed"

    candidate_status = "UNPROVEN" if not signal_rows and canonical_signal is None else ("YES" if candidate else "NO")
    if canonical_signal is not None and str(canonical_signal.get("executable") or "NO").upper() == "YES":
        candidate_status = "YES"
    risk_evaluated_status = "YES" if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")
    risk_decision = risk_events[-1].action_taken if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")
    risk_reason = risk_events[-1].detail if risk_events else ("UNPROVEN" if candidate else "NOT APPLICABLE")

    execution_attempted_status = "YES" if bool(cycle_handoff.get("attempted")) else ("YES" if candidate else "NO")
    execution_service_called_status = (
        "YES"
        if (bool(cycle_handoff.get("attempted")) or execution_summary.get("execution_service_called"))
        else "UNPROVEN"
        if candidate
        else "NOT APPLICABLE"
    )
    order_created_status = "NOT APPLICABLE"
    trade_created_status = "YES" if execution_summary.get("trade_created") else "NO"
    if trades:
        filled_status = "YES" if trade_fill_evidence == len(trades) else "UNPROVEN"
    elif candidate:
        filled_status = "NO"
    else:
        filled_status = "NOT APPLICABLE"

    rejected_status = "YES" if execution_summary.get("rejected") else ("NO" if candidate else "NOT APPLICABLE")
    skipped_status = "YES" if execution_summary.get("skipped") else ("NO" if candidate else "NOT APPLICABLE")
    error_status = "YES" if execution_summary.get("error") else ("NO" if candidate else "NOT APPLICABLE")

    decision_record_linkage_status = "YES" if cycle.decision_record_id is not None else ("UNPROVEN" if signal_rows else "NOT APPLICABLE")
    outcome_linkage_status = (
        "YES"
        if outcome_score_rows
        else "NO"
        if cycle.decision_record_id is not None
        else "UNPROVEN"
    )
    research_linkage_status = "YES" if research_events else "NO"

    account_balance_changed_status = (
        "YES"
        if balance_change_observed > 0
        else "UNPROVEN"
        if trades and balance_change_unproven > 0
        else "NO"
        if trades
        else "NOT APPLICABLE"
    )
    position_changed_status = (
        "YES"
        if position_change_observed > 0
        else "UNPROVEN"
        if trades and position_change_unproven > 0
        else "NO"
        if trades
        else "NOT APPLICABLE"
    )
    accounting_entry_status = "YES" if trade_fill_evidence > 0 else ("UNPROVEN" if trades else "NOT APPLICABLE")

    return {
        "cycle_id": cycle.cycle_id,
        "timestamp": cycle.started_at,
        "asset": None if primary_asset is None else primary_asset.symbol,
        "asset_id": primary_asset_id,
        "provider": provider,
        "interval": interval,
        "latest_candle_time": latest_candle_time,
        "signal_section": {
            "signals_generated": len(signal_rows),
            "signals": signal_rows,
            "source": "signals_table_via_decision_lineage",
        },
        "strategy_roster": {
            "proposal_count": len(roster_proposals),
            "buy_count": roster_buy,
            "sell_count": roster_sell,
            "hold_count": roster_hold,
            "mode": roster_mode,
            "executable": roster_executable,
            "reason": roster_reason,
        },
        "canonical_signal": {
            "signal_id": (canonical_signal or {}).get("signal_id"),
            "action": (canonical_signal or {}).get("action"),
            "executable": (canonical_signal or {}).get("executable", "NO"),
            "mode": (canonical_signal or {}).get("mode", "PAPER"),
        },
        "autonomous_decision": {
            "proposed_action": autonomous_proposed_action,
            "mandate_verdict": getattr(cycle, "mandate_verdict", None) or "UNPROVEN",
            "risk_verdict": getattr(cycle, "risk_verdict", None) or "UNPROVEN",
            "execution_handoff": execution_handoff_status,
            "exact_blocker": execution_handoff_blocker,
        },
        "execution_candidate": {
            "is_candidate": candidate,
            "status": candidate_status,
            "reason_if_no": candidate_reason if candidate_status == "NO" else "NOT APPLICABLE",
        },
        "risk": {
            "evaluated_status": risk_evaluated_status,
            "decision": risk_decision,
            "reason": risk_reason,
            "risk_event_ids": [item.id for item in risk_events],
        },
        "execution": {
            "execution_attempted_status": execution_attempted_status,
            "execution_service_called_status": execution_service_called_status,
            "exact_result": cycle_handoff.get("exact_result") if cycle_handoff else None,
            "order_created_status": order_created_status,
            "order_creation_reason": execution_summary.get("order_creation_reason"),
            "trade_created_status": trade_created_status,
            "filled_status": filled_status,
            "rejected_status": rejected_status,
            "skipped_status": skipped_status,
            "error_status": error_status,
            "trade_ids": [item.id for item in trades],
            "signal_ids": signal_ids,
        },
        "accounting": {
            "paper_account_ids": sorted({item.paper_account_id for item in trades}, key=str),
            "entries": accounting_entries,
            "fees_total": _decimal_str(total_fees),
            "pnl": decision.pnl if decision is not None else None,
            "buy_quantity_total": _decimal_str(_sum_trade_quantity(trades, side="buy")),
            "sell_quantity_total": _decimal_str(_sum_trade_quantity(trades, side="sell")),
            "account_balance_changed_status": account_balance_changed_status,
            "position_changed_status": position_changed_status,
            "accounting_entry_persisted_status": accounting_entry_status,
        },
        "decision_records": {
            "decision_record_id": cycle.decision_record_id,
            "outcome_score_linkage_count": len(outcome_score_rows),
            "outcome_score_ids": [item.outcome_id for item in outcome_score_rows],
            "decision_record_linkage_status": decision_record_linkage_status,
            "outcome_linkage_status": outcome_linkage_status,
            "research_linkage_status": research_linkage_status,
            "research_linkage": [
                {
                    "event_id": item.id,
                    "event_type": item.event_type,
                    "campaign_id": _event_payload_campaign_id(item.payload),
                    "created_at": item.created_at,
                }
                for item in research_events
            ],
            "autonomous_cycle_linkage": {
                "cycle_id": cycle.cycle_id,
                "scheduled_roster_run_ids": [item.roster_run_id for item in roster_runs],
            },
        },
        "summary": summary,
    }


async def fetch_execution_forensics(
    *,
    since: str | None,
    cycle_id: UUID | None,
    latest: bool,
) -> dict[str, Any]:
    selectors = int(bool(since)) + int(cycle_id is not None) + int(latest)
    if selectors != 1:
        raise ValueError("Choose exactly one selector: --since, --cycle, or --latest")

    async with AsyncSessionLocal() as db:
        cycles: list[AutonomousCycleRun]
        criteria: dict[str, Any] = {
            "selector": "latest" if latest else "cycle" if cycle_id is not None else "since",
            "since": since,
            "cycle_id": cycle_id,
        }

        if latest:
            item = await db.scalar(select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1))
            cycles = [] if item is None else [item]
        elif cycle_id is not None:
            item = await db.get(AutonomousCycleRun, cycle_id)
            if item is None:
                raise ValueError(f"Cycle {cycle_id} not found")
            cycles = [item]
        else:
            assert since is not None
            threshold = _resolve_since_datetime(since)
            criteria["resolved_since"] = threshold
            cycles = list(
                (
                    await db.execute(
                        select(AutonomousCycleRun)
                        .where(AutonomousCycleRun.started_at >= threshold)
                        .order_by(desc(AutonomousCycleRun.started_at), desc(AutonomousCycleRun.cycle_id))
                        .limit(_EXECUTION_FORENSICS_MAX_SINCE_CYCLES)
                    )
                ).scalars().all()
            )
            criteria["max_cycles"] = _EXECUTION_FORENSICS_MAX_SINCE_CYCLES

        deduped_cycles: list[AutonomousCycleRun] = []
        seen_cycle_ids: set[uuid.UUID] = set()
        for item in cycles:
            cycle_key = item.cycle_id
            if cycle_key in seen_cycle_ids:
                continue
            seen_cycle_ids.add(cycle_key)
            deduped_cycles.append(item)
        cycles = deduped_cycles

        reports = [await _build_cycle_forensics(db=db, cycle=item) for item in cycles]

    return {
        "mode": "read_only_forensics",
        "criteria": criteria,
        "cycle_count": len(reports),
        "truncated": bool(since) and len(reports) >= _EXECUTION_FORENSICS_MAX_SINCE_CYCLES,
        "cycles": reports,
    }


def _seconds_between(later: datetime | None, earlier: datetime | None) -> int | None:
    if later is None or earlier is None:
        return None
    delta = later.astimezone(timezone.utc) - earlier.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds()))


def _preview_command_mode(*, replayed: bool, command_name: str) -> str:
    if command_name == "preview-show":
        return "VIEW_EXISTING"
    return "IDEMPOTENT_REPLAY" if replayed else "NEW_PREVIEW"


def _decision_classification(*, proposed_action: str | None, risk_verdict: str | None, deterministic_explanation: list[str], failure_reason: str | None) -> str:
    action = (proposed_action or "").upper()
    risk = (risk_verdict or "").upper()
    explanation_blob = " ".join(deterministic_explanation).lower()
    reason = (failure_reason or "").lower()

    if reason.startswith("mandate_status_") or "mandate_not_active" in explanation_blob or "mandate_version_invalid" in reason:
        return "MANDATE_REJECTED"
    if "reconciliation_not_ready" in reason or "provider_not_ready" in reason or "insufficient_candle_context" in explanation_blob or "exchange_connection_not_found" in reason:
        return "INFRASTRUCTURE_BLOCKED"
    if risk == "REJECTED":
        return "RISK_REJECTED"
    if action == "HOLD":
        if "strategy_evaluated" in explanation_blob or "signal_action=hold" in explanation_blob:
            return "STRATEGY_DERIVED"
        return "SAFETY_HOLD" if explanation_blob else "INFRASTRUCTURE_BLOCKED"
    if action in {"BUY", "SELL"}:
        return "STRATEGY_DERIVED"
    return "INFRASTRUCTURE_BLOCKED"


def _capital_state(*, preview: CryptoOrderPreview | None, proposed_action: str | None) -> str:
    if preview is not None:
        return "PREVIEW_ONLY"
    if (proposed_action or "").upper() == "HOLD":
        return "NONE"
    return "UNKNOWN"


def _build_timeline_payload(
    *,
    command_mode: str,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cycle_created_at = _parse_datetime(getattr(cycle, "created_at", None)) or _parse_datetime(getattr(cycle, "started_at", None))
    decision_created_at = _parse_datetime(getattr(decision, "timestamp", None))
    snapshot_created_at = _parse_datetime(getattr(snapshot, "timestamp", None))
    preview_created_at = _parse_datetime(getattr(preview, "created_at", None))

    cycle_context = getattr(cycle, "cycle_context", None) or {}
    timeline_context = {}
    if isinstance(cycle_context, dict):
        strategy_context = cycle_context.get("strategy") if isinstance(cycle_context.get("strategy"), dict) else {}
        signal_payload = strategy_context.get("signal_payload") if isinstance(strategy_context, dict) else {}
        if isinstance(signal_payload, dict):
            timeline_context = signal_payload.get("timeline") if isinstance(signal_payload.get("timeline"), dict) else {}
        if not timeline_context and isinstance(cycle_context.get("timeline"), dict):
            timeline_context = cycle_context.get("timeline")

    latest_completed_candle_open = _parse_datetime(timeline_context.get("latest_completed_candle_open")) if isinstance(timeline_context, dict) else None
    latest_completed_candle_close = _parse_datetime(timeline_context.get("latest_completed_candle_close")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_open = _parse_datetime(timeline_context.get("oldest_candle_used_open")) if isinstance(timeline_context, dict) else None
    oldest_candle_used_close = _parse_datetime(timeline_context.get("oldest_candle_used_close")) if isinstance(timeline_context, dict) else None
    evaluated_at = _parse_datetime(timeline_context.get("evaluated_at")) or decision_created_at or cycle_created_at or now

    cycle_age_seconds = _seconds_between(now, cycle_created_at)
    decision_age_seconds = _seconds_between(now, decision_created_at)
    snapshot_age_seconds = _seconds_between(now, snapshot_created_at)
    market_data_age_seconds = _seconds_between(now, latest_completed_candle_close)

    history_candle_count = timeline_context.get("history_candle_count") if isinstance(timeline_context, dict) else None
    current_candle_excluded = bool(timeline_context.get("current_incomplete_candle_excluded")) if isinstance(timeline_context, dict) else None
    decision_applies_to = timeline_context.get("decision_applies_to") if isinstance(timeline_context, dict) else None

    mismatch_warning = False
    if cycle_age_seconds is not None and decision_age_seconds is not None:
        if abs(cycle_age_seconds - decision_age_seconds) > 120:
            mismatch_warning = True

    return {
        "evaluated_at": evaluated_at,
        "cycle_created_at": cycle_created_at,
        "decision_created_at": decision_created_at,
        "snapshot_created_at": snapshot_created_at,
        "preview_created_at": preview_created_at,
        "latest_completed_candle_open": latest_completed_candle_open,
        "latest_completed_candle_close": latest_completed_candle_close,
        "oldest_candle_used_open": oldest_candle_used_open,
        "oldest_candle_used_close": oldest_candle_used_close,
        "history_candle_count": history_candle_count,
        "cycle_age_seconds": cycle_age_seconds,
        "decision_age_seconds": decision_age_seconds,
        "snapshot_age_seconds": snapshot_age_seconds,
        "market_data_age_seconds": market_data_age_seconds,
        "current_incomplete_candle_excluded": current_candle_excluded,
        "decision_applies_to": decision_applies_to,
        "age_sources": {
            "cycle_age_seconds": "autonomous_cycle_runs.created_at",
            "decision_age_seconds": "decision_records.timestamp",
            "snapshot_age_seconds": "decision_snapshots.timestamp",
            "market_data_age_seconds": "candles.close_time",
        },
        "timestamp_mismatch_warning": mismatch_warning,
    }


def _build_preview_evidence_payload(
    *,
    command_name: str,
    result: Any,
    cycle: AutonomousCycleRun | None,
    decision: DecisionRecord | None,
    snapshot: DecisionSnapshot | None,
    preview: CryptoOrderPreview | None,
) -> dict[str, Any]:
    evaluation_mode = _preview_command_mode(replayed=bool(getattr(result, "replayed", False)), command_name=command_name)
    command_mode = evaluation_mode
    if command_name == "preview-show":
        command_mode = "VIEW_EXISTING"

    proposed_action = getattr(result, "proposed_action", None) or getattr(cycle, "proposed_action", None) or "HOLD"
    risk_verdict = getattr(result, "risk_verdict", None) or getattr(cycle, "risk_verdict", None)
    deterministic_explanation = list(getattr(result.diagnostics, "deterministic_explanation", []) if getattr(result, "diagnostics", None) else [])
    if not deterministic_explanation and cycle is not None:
        deterministic_explanation = list(getattr(cycle, "deterministic_explanation", []) or [])

    timeline = _build_timeline_payload(
        command_mode=command_mode,
        cycle=cycle,
        decision=decision,
        snapshot=snapshot,
        preview=preview,
    )

    decision_classification = _decision_classification(
        proposed_action=proposed_action,
        risk_verdict=risk_verdict,
        deterministic_explanation=deterministic_explanation,
        failure_reason=getattr(result.diagnostics, "failure_reason", None) if getattr(result, "diagnostics", None) else getattr(cycle, "failure_reason", None),
    )

    capital_state = _capital_state(preview=preview, proposed_action=proposed_action)
    new_evaluation = command_mode == "NEW_PREVIEW"
    outcome = (proposed_action or "FAILED").upper() if command_mode != "VIEW_EXISTING" else (getattr(decision, "outcome", None) or (proposed_action or "FAILED")).upper()

    if command_mode == "VIEW_EXISTING":
        record_created = timeline.get("decision_created_at") or timeline.get("cycle_created_at")
    elif command_mode == "IDEMPOTENT_REPLAY":
        record_created = timeline.get("cycle_created_at")
    else:
        record_created = timeline.get("cycle_created_at") or timeline.get("decision_created_at")

    timeline_warning = bool(timeline.get("timestamp_mismatch_warning"))

    return {
        "command_mode": command_mode,
        "evaluation_mode": evaluation_mode,
        "outcome": outcome,
        "decision_classification": decision_classification,
        "capital_state": capital_state,
        "new_evaluation": new_evaluation,
        "record_created_at": record_created,
        "timeline": timeline,
        "timeline_warning": timeline_warning,
    }


async def execute_preview_cycle(
    *,
    mandate_id: UUID | None,
    actor: str,
    product_id: str,
    strategy_interval: str,
    trigger: str,
    idempotency_seed: str | None,
    software_build_version: str | None,
    forced_action: str | None,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        resolved_mandate_id = mandate_id
        if resolved_mandate_id is None:
            resolved_mandate_id = await db.scalar(
                select(AutonomousCapitalMandate.mandate_id)
                .where(AutonomousCapitalMandate.status == "ACTIVE")
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            if resolved_mandate_id is None:
                resolved_mandate_id = await db.scalar(
                    select(AutonomousCapitalMandate.mandate_id)
                    .order_by(desc(AutonomousCapitalMandate.updated_at))
                    .limit(1)
                )
        if resolved_mandate_id is None:
            raise ValueError("No mandate found. Seed or create a mandate before running preview.")

        result = await run_autonomous_preview_cycle(
            db=db,
            request=AutonomousCycleRequest(
                mandate_id=resolved_mandate_id,
                actor=actor,
                product_id=product_id,
                strategy_interval=strategy_interval,
                trigger=trigger,
                idempotency_seed=idempotency_seed,
                software_build_version=software_build_version,
                forced_action=forced_action,
            ),
        )

        cycle = await db.get(AutonomousCycleRun, result.cycle_id)
        decision = await db.get(DecisionRecord, result.decision_record_id) if result.decision_record_id else None
        snapshot = await db.get(DecisionSnapshot, result.decision_record_id) if result.decision_record_id else None
        preview = await db.get(CryptoOrderPreview, result.preview_id) if result.preview_id else None

    payload = {
        "cycle_id": result.cycle_id,
        "state": result.state,
        "idempotency_key": result.idempotency_key,
        "mandate_id": result.mandate_id,
        "mandate_version_id": result.mandate_version_id,
        "proposed_action": result.proposed_action,
        "mandate_verdict": result.mandate_verdict,
        "risk_verdict": result.risk_verdict,
        "decision_record_id": result.decision_record_id,
        "preview_id": result.preview_id,
        "mandate_evaluation_id": result.mandate_evaluation_id,
        "risk_event_id": result.risk_event_id,
        "audit_correlation_id": result.audit_correlation_id,
        "replayed": result.replayed,
        "cycle_context": result.cycle_context,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "diagnostics": {
            "duration_ms": result.diagnostics.duration_ms,
            "evaluation_stage": result.diagnostics.evaluation_stage,
            "termination_stage": result.diagnostics.termination_stage,
            "failure_reason": result.diagnostics.failure_reason,
            "deterministic_explanation": list(result.diagnostics.deterministic_explanation),
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview",
            result=result,
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


def _resolve_git_sha() -> str | None:
    configured_sha = (
        Path(__file__).resolve().parents[4] / ".git" / "HEAD"
    )
    if configured_sha.exists():
        try:
            head_value = configured_sha.read_text(encoding="utf-8").strip()
            if head_value.startswith("ref:"):
                ref_path = head_value.split(":", 1)[1].strip()
                ref_file = configured_sha.parent / ref_path
                if ref_file.exists():
                    return ref_file.read_text(encoding="utf-8").strip()[:12]
            if head_value:
                return head_value[:12]
        except OSError:
            return None
    return None


async def fetch_preview_evidence(*, preview_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        preview = await db.get(CryptoOrderPreview, preview_id)
        if preview is None:
            raise ValueError(f"Preview {preview_id} not found")

        decision: DecisionRecord | None = None
        snapshot: DecisionSnapshot | None = None
        if preview.decision_record_id is not None:
            decision = await db.get(DecisionRecord, preview.decision_record_id)
            snapshot = await db.get(DecisionSnapshot, preview.decision_record_id)

        cycle: AutonomousCycleRun | None = await db.scalar(
            select(AutonomousCycleRun)
            .where(AutonomousCycleRun.preview_id == preview.crypto_order_preview_id)
            .order_by(desc(AutonomousCycleRun.started_at))
            .limit(1)
        )

    payload = {
        "preview": {
            "crypto_order_preview_id": preview.crypto_order_preview_id,
            "status": preview.status,
            "provider": preview.provider,
            "environment": preview.environment,
            "product_id": preview.product_id,
            "side": preview.side,
            "order_type": preview.order_type,
            "requested_amount": _coerce_decimal(preview.requested_amount),
            "requested_amount_currency": preview.requested_amount_currency,
            "quote_size": _coerce_decimal(preview.quote_size),
            "base_size": _coerce_decimal(preview.base_size),
            "estimated_average_price": _coerce_decimal(preview.estimated_average_price),
            "estimated_total_value": _coerce_decimal(preview.estimated_total_value),
            "estimated_base_size": _coerce_decimal(preview.estimated_base_size),
            "estimated_quote_size": _coerce_decimal(preview.estimated_quote_size),
            "estimated_fee": _coerce_decimal(preview.estimated_fee),
            "estimated_fee_currency": preview.estimated_fee_currency,
            "estimated_slippage": _coerce_decimal(preview.estimated_slippage),
            "estimated_commission_total": _coerce_decimal(preview.estimated_commission_total),
            "best_bid": _coerce_decimal(preview.best_bid),
            "best_ask": _coerce_decimal(preview.best_ask),
            "status_reason": preview.failure_reason,
            "warning_messages": list(preview.warning_messages or []),
            "readiness_verdict": preview.readiness_verdict,
            "risk_verdict": preview.risk_verdict,
            "risk_explanation": preview.risk_explanation,
            "decision_record_id": preview.decision_record_id,
            "risk_event_id": preview.risk_event_id,
            "audit_correlation_id": preview.audit_correlation_id,
            "created_at": preview.created_at,
            "updated_at": preview.updated_at,
            "expires_at": preview.expires_at,
        },
        "decision_record": {
            "decision_id": decision.decision_id if decision else None,
            "timeframe": decision.timeframe if decision else None,
            "trade_accepted": decision.trade_accepted if decision else None,
            "trade_rejected_reason": decision.trade_rejected_reason if decision else None,
            "outcome": decision.outcome if decision else None,
            "generated_signals": decision.generated_signals if decision else None,
            "indicators": decision.indicators if decision else None,
            "risk_adjustments": decision.risk_adjustments if decision else None,
            "supporting_strategies": decision.supporting_strategies if decision else None,
            "opposing_strategies": decision.opposing_strategies if decision else None,
            "execution_details": decision.execution_details if decision else None,
        },
        "decision_snapshot": {
            "decision_id": snapshot.decision_id if snapshot else None,
            "strategy_version": snapshot.strategy_version if snapshot else None,
            "configuration_version": snapshot.configuration_version if snapshot else None,
            "decision_engine_version": snapshot.decision_engine_version if snapshot else None,
            "generated_features": snapshot.generated_features if snapshot else None,
            "strategy_inputs": snapshot.strategy_inputs if snapshot else None,
            "risk_inputs": snapshot.risk_inputs if snapshot else None,
        },
        "cycle": {
            "cycle_id": cycle.cycle_id if cycle else None,
            "state": cycle.state if cycle else None,
            "evaluation_stage": cycle.evaluation_stage if cycle else None,
            "termination_stage": cycle.termination_stage if cycle else None,
            "failure_reason": cycle.failure_reason if cycle else None,
            "mandate_id": cycle.mandate_id if cycle else None,
            "mandate_version_id": cycle.mandate_version_id if cycle else None,
            "proposed_action": cycle.proposed_action if cycle else None,
            "risk_verdict": cycle.risk_verdict if cycle else None,
            "started_at": cycle.started_at if cycle else None,
            "completed_at": cycle.completed_at if cycle else None,
            "created_at": cycle.created_at if cycle else None,
            "deterministic_explanation": cycle.deterministic_explanation if cycle else None,
            "cycle_context": cycle.cycle_context if cycle else None,
        },
    }

    payload.update(
        _build_preview_evidence_payload(
            command_name="preview-show",
            result=type("_PreviewResult", (), {"replayed": False, "proposed_action": preview.side, "risk_verdict": preview.risk_verdict, "diagnostics": type("_Diag", (), {"deterministic_explanation": cycle.deterministic_explanation if cycle else [], "failure_reason": cycle.failure_reason if cycle else None})()})(),
            cycle=cycle,
            decision=decision,
            snapshot=snapshot,
            preview=preview,
        )
    )
    return payload


async def fetch_campaign_orchestration_readiness(*, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_readiness(db=db, campaign_id=campaign_id, version=version)


async def fetch_campaign_orchestration_preview(*, campaign_id: UUID | None, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await run_campaign_orchestration_preview_for_candle(db=db, campaign_id=campaign_id, version=version, allow_draft_preview=True)


async def fetch_campaign_orchestration_status(*, campaign_id: UUID, version: int | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_status(db=db, campaign_id=campaign_id, version=version)


async def fetch_campaign_orchestration_history(*, campaign_id: UUID, version: int | None, limit: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_campaign_orchestration_history(db=db, campaign_id=campaign_id, version=version, limit=limit)


async def fetch_candle_readiness(
    *,
    symbol: str,
    interval: str,
    exchange: str | None,
    max_age_minutes: int,
    lookback_limit: int,
) -> dict[str, Any]:
    normalized_symbol = symbol.strip().upper()
    normalized_exchange = exchange.strip().lower() if exchange else None

    async with AsyncSessionLocal() as db:
        asset_query = select(Asset).where(func.upper(Asset.symbol) == normalized_symbol)
        if normalized_exchange:
            asset_query = asset_query.where(func.lower(Asset.exchange) == normalized_exchange)
        assets = (await db.execute(asset_query.order_by(desc(Asset.created_at)).limit(2))).scalars().all()

        if not assets:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "asset_not_found",
            }

        if len(assets) > 1:
            return {
                "symbol": normalized_symbol,
                "exchange": normalized_exchange,
                "interval": interval,
                "asset_id": None,
                "row_count": 0,
                "latest_open_time": None,
                "latest_close_time": None,
                "age_minutes": None,
                "ready": False,
                "reason": "ambiguous_asset_resolution",
            }

        asset = assets[0]
        latest_candle = await db.scalar(
            select(Candle)
            .where(Candle.asset_id == asset.id, Candle.interval == interval)
            .order_by(desc(Candle.open_time))
            .limit(1)
        )
        row_count = (
            await db.scalar(
                select(func.count())
                .select_from(Candle)
                .where(Candle.asset_id == asset.id, Candle.interval == interval)
            )
            or 0
        )

    if latest_candle is None:
        return {
            "symbol": asset.symbol,
            "exchange": asset.exchange,
            "interval": interval,
            "asset_id": asset.id,
            "row_count": int(row_count),
            "latest_open_time": None,
            "latest_close_time": None,
            "age_minutes": None,
            "ready": False,
            "reason": "no_candles",
        }

    now = datetime.now(timezone.utc)
    close_time = latest_candle.close_time
    if close_time.tzinfo is None:
        close_time = close_time.replace(tzinfo=timezone.utc)
    age_minutes = max(0, int((now - close_time).total_seconds() // 60))
    ready = age_minutes <= max_age_minutes

    return {
        "symbol": asset.symbol,
        "exchange": asset.exchange,
        "interval": interval,
        "asset_id": asset.id,
        "row_count": int(row_count),
        "latest_open_time": latest_candle.open_time,
        "latest_close_time": latest_candle.close_time,
        "age_minutes": age_minutes,
        "ready": ready,
        "reason": "ok" if ready else "stale_candles",
        "max_age_minutes": max_age_minutes,
        "lookback_limit": lookback_limit,
    }


async def fetch_operator_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        await db.execute(select(1))

        if mandate_id is not None:
            mandate: AutonomousCapitalMandate | None = await db.get(AutonomousCapitalMandate, mandate_id)
            if mandate is None:
                raise ValueError(f"Mandate {mandate_id} not found")
            cycle_stmt = (
                select(AutonomousCycleRun)
                .where(AutonomousCycleRun.mandate_id == mandate_id)
                .order_by(desc(AutonomousCycleRun.started_at))
                .limit(1)
            )
        else:
            mandate = await db.scalar(
                select(AutonomousCapitalMandate)
                .order_by(desc(AutonomousCapitalMandate.updated_at))
                .limit(1)
            )
            cycle_stmt = select(AutonomousCycleRun).order_by(desc(AutonomousCycleRun.started_at)).limit(1)

        latest_cycle = await db.scalar(cycle_stmt)
        latest_preview = await db.scalar(select(CryptoOrderPreview).order_by(desc(CryptoOrderPreview.created_at)).limit(1))
        connections = (
            await db.execute(select(ExchangeConnection).order_by(ExchangeConnection.provider.asc(), ExchangeConnection.environment.asc()))
        ).scalars().all()
        campaign_count = int((await db.scalar(select(func.count()).select_from(CapitalCampaign))) or 0)
        decision_count = int((await db.scalar(select(func.count()).select_from(DecisionRecord))) or 0)
        open_preview_count = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(CryptoOrderPreview)
                    .where(CryptoOrderPreview.expires_at > now)
                )
            )
            or 0
        )

        open_live_orders = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(LiveCryptoOrder)
                    .where(
                        func.lower(LiveCryptoOrder.status).notin_(
                            [
                                "filled",
                                "cancelled",
                                "failed",
                                "rejected",
                                "expired",
                                "settled",
                                "completed",
                            ]
                        )
                    )
                )
            )
            or 0
        )

    candle_summary: dict[str, Any] | None = None
    if candle_symbol:
        candle_summary = await fetch_candle_readiness(
            symbol=candle_symbol,
            interval=candle_interval,
            exchange=candle_exchange,
            max_age_minutes=candle_max_age_minutes,
            lookback_limit=200,
        )

    kraken_production = None
    for item in connections:
        if item.provider == "kraken_spot" and item.environment == "production":
            kraken_production = item
            break

    latest_strategy: dict[str, Any] = {"name": None, "version": None}
    open_positions: int | None = None
    if latest_cycle is not None:
        context = latest_cycle.cycle_context or {}
        strategy = context.get("strategy") if isinstance(context, dict) else None
        reconciliation = context.get("reconciliation_status") if isinstance(context, dict) else None
        if isinstance(strategy, dict):
            latest_strategy = {
                "name": strategy.get("name"),
                "version": strategy.get("version"),
            }
        if isinstance(reconciliation, dict) and isinstance(reconciliation.get("open_position_count"), int):
            open_positions = reconciliation.get("open_position_count")

    latest_signal = latest_cycle.proposed_action if latest_cycle else None
    worker_heartbeat = latest_cycle.completed_at if latest_cycle and latest_cycle.completed_at else None
    if worker_heartbeat is None and latest_cycle is not None:
        worker_heartbeat = latest_cycle.started_at

    system_health = "healthy"
    if kraken_production is not None and kraken_production.status not in {"connected"}:
        system_health = "degraded"
    if candle_summary and not candle_summary.get("ready"):
        system_health = "degraded"

    preview_operator_recommendation = "No action required."
    if latest_cycle is not None:
        action = str(latest_cycle.proposed_action or "").upper()
        state = str(latest_cycle.state or "").upper()
        risk_verdict = str(latest_cycle.risk_verdict or "").upper()
        if state == "FAILED":
            preview_operator_recommendation = "Inspect latest cycle failure before proceeding."
        elif action == "HOLD":
            preview_operator_recommendation = "Waiting for next qualifying BUY."
        elif action in {"BUY", "SELL"} and risk_verdict == "REJECTED":
            preview_operator_recommendation = "Inspect Risk rejection."
        elif action in {"BUY", "SELL"}:
            preview_operator_recommendation = "Review latest preview evidence and approval readiness."

    api_status = "responsive"
    database_status = "connected"
    kraken_status = "Unavailable"
    if kraken_production is not None:
        readiness = kraken_production.last_readiness_verdict or "Unknown"
        kraken_status = f"{kraken_production.status} ({readiness})"

    worker_status = "Unavailable"
    if worker_heartbeat is not None:
        heartbeat_value = worker_heartbeat if worker_heartbeat.tzinfo is not None else worker_heartbeat.replace(tzinfo=timezone.utc)
        age_minutes = int(max(0, (now - heartbeat_value).total_seconds() // 60))
        worker_status = f"heartbeat {age_minutes}m ago"

    git_sha = _resolve_git_sha()

    return {
        "environment": settings.environment,
        "git_sha": git_sha,
        "api_status": api_status,
        "database_status": database_status,
        "worker_status": worker_status,
        "worker_heartbeat": worker_heartbeat,
        "kraken_status": kraken_status,
        "system_health": system_health,
        "database_url_configured": bool(settings.database_url),
        "mandate_id": mandate.mandate_id if mandate else None,
        "mandate_status": mandate.status if mandate else None,
        "latest_strategy": latest_strategy,
        "latest_signal": latest_signal,
        "campaign_count": campaign_count,
        "decision_count": decision_count,
        "open_positions": open_positions,
        "open_previews": open_preview_count,
        "open_live_orders": open_live_orders,
        "research_status": "available" if settings.research_evolution_enabled else "disabled",
        "operator_recommendation": preview_operator_recommendation,
        "safety_flags": {
            "live_crypto_order_submission_enabled": settings.live_crypto_order_submission_enabled,
            "live_crypto_dry_run_enabled": settings.live_crypto_dry_run_enabled,
            "live_crypto_max_order_usd": _coerce_decimal(settings.live_crypto_max_order_usd),
            "live_crypto_preparation_enabled": settings.live_crypto_preparation_enabled,
        },
        "latest_cycle": {
            "cycle_id": latest_cycle.cycle_id if latest_cycle else None,
            "state": latest_cycle.state if latest_cycle else None,
            "proposed_action": latest_cycle.proposed_action if latest_cycle else None,
            "risk_verdict": latest_cycle.risk_verdict if latest_cycle else None,
            "failure_reason": latest_cycle.failure_reason if latest_cycle else None,
            "started_at": latest_cycle.started_at if latest_cycle else None,
            "completed_at": latest_cycle.completed_at if latest_cycle else None,
        },
        "latest_preview": {
            "crypto_order_preview_id": latest_preview.crypto_order_preview_id if latest_preview else None,
            "status": latest_preview.status if latest_preview else None,
            "provider": latest_preview.provider if latest_preview else None,
            "product_id": latest_preview.product_id if latest_preview else None,
            "side": latest_preview.side if latest_preview else None,
            "created_at": latest_preview.created_at if latest_preview else None,
            "expires_at": latest_preview.expires_at if latest_preview else None,
        },
        "connection_summary": [
            {
                "exchange_connection_id": item.exchange_connection_id,
                "provider": item.provider,
                "environment": item.environment,
                "status": item.status,
                "credentials_valid": item.credentials_valid,
                "last_readiness_verdict": item.last_readiness_verdict,
                "last_verified_at": item.last_verified_at,
                "last_heartbeat_at": item.last_heartbeat_at,
            }
            for item in connections
        ],
        "candle_summary": candle_summary,
    }


async def fetch_risk_ledger_diagnosis(*, account_id: UUID) -> dict[str, Any]:
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        account = await db.get(PaperAccount, account_id)
        if account is None:
            raise ValueError(f"Paper account {account_id} not found")

        effective_policy = await resolve_effective_risk_policy(db=db, paper_account_id=account.id)
        latest_trade = await db.scalar(
            select(Trade)
            .where(Trade.paper_account_id == account.id)
            .order_by(desc(Trade.executed_at), desc(Trade.id))
            .limit(1)
        )
        trade_count = int(
            (await db.scalar(select(func.count()).select_from(Trade).where(Trade.paper_account_id == account.id))) or 0
        )

        equity_evidence = await resolve_equity_risk_evidence(
            db=db,
            paper_account=account,
            actor="operator_cli:risk_diagnosis",
            max_price_age_seconds=settings.live_crypto_price_max_age_seconds,
        )

        status_payload: dict[str, Any] | None = None
        status_error: dict[str, Any] | None = None
        try:
            risk_status = await risk_monitor.get_risk_status(db=db, account_id=account.id)
            status_payload = {
                "daily_loss": {
                    "used": format(risk_status.daily_loss.used, "f"),
                    "limit": format(risk_status.daily_loss.limit, "f"),
                    "pct_used": format(risk_status.daily_loss.pct_used, "f"),
                },
                "drawdown": {
                    "used": format(risk_status.drawdown.used, "f"),
                    "limit": format(risk_status.drawdown.limit, "f"),
                    "pct_used": format(risk_status.drawdown.pct_used, "f"),
                },
                "daily_loss_input_source": risk_status.daily_loss_input_source,
                "drawdown_input_source": risk_status.drawdown_input_source,
                "current_equity": format(risk_status.current_equity, "f"),
                "current_cash_balance": format(risk_status.current_cash_balance, "f"),
                "current_position_value": format(risk_status.current_position_value, "f"),
                "start_of_day_equity": format(risk_status.start_of_day_equity, "f"),
                "high_water_mark_equity": format(risk_status.high_water_mark_equity, "f"),
                "valuation_source": risk_status.valuation_source,
                "valuation_state": risk_status.valuation_state,
                "daily_loss_baseline_source": risk_status.daily_loss_baseline_source,
                "drawdown_baseline_source": risk_status.drawdown_baseline_source,
                "baseline_state": risk_status.baseline_state,
                "generated_at": risk_status.generated_at,
            }
        except Exception as exc:  # pragma: no cover - defensive payload branch
            status_error = {
                "error": str(exc),
                "equity_evidence_ready": equity_evidence.ready,
                "equity_evidence_fail_closed_reason": equity_evidence.fail_closed_reason,
            }

        snapshot = await build_account_snapshot(
            db=db,
            paper_account_id=account.id,
            starting_balance=account.starting_balance,
        )

    starting_balance = Decimal(account.starting_balance)
    current_cash_balance = Decimal(account.current_cash_balance)
    old_daily_loss_limit = starting_balance * Decimal(effective_policy.max_daily_loss_pct)
    old_drawdown_limit = starting_balance * Decimal(effective_policy.max_drawdown_pct)
    old_daily_loss_used = max(Decimal("0"), starting_balance - current_cash_balance)
    old_drawdown_used = old_daily_loss_used

    authoritative_start_of_day_equity = equity_evidence.baseline.start_of_day_equity
    authoritative_high_water_mark_equity = equity_evidence.baseline.high_water_mark_equity
    authoritative_current_equity = equity_evidence.valuation.current_equity
    authoritative_daily_loss_used = max(Decimal("0"), authoritative_start_of_day_equity - authoritative_current_equity)
    authoritative_drawdown_used = max(Decimal("0"), authoritative_high_water_mark_equity - authoritative_current_equity)
    authoritative_daily_loss_limit = authoritative_start_of_day_equity * Decimal(effective_policy.max_daily_loss_pct)
    authoritative_drawdown_limit = authoritative_high_water_mark_equity * Decimal(effective_policy.max_drawdown_pct)

    old_daily_loss_pct = old_daily_loss_used / old_daily_loss_limit if old_daily_loss_limit > 0 else Decimal("0")
    old_drawdown_pct = old_drawdown_used / old_drawdown_limit if old_drawdown_limit > 0 else Decimal("0")
    authoritative_daily_loss_pct = authoritative_daily_loss_used / authoritative_daily_loss_limit if authoritative_daily_loss_limit > 0 else Decimal("0")
    authoritative_drawdown_pct = authoritative_drawdown_used / authoritative_drawdown_limit if authoritative_drawdown_limit > 0 else Decimal("0")

    latest_trade_executed_at = None if latest_trade is None else latest_trade.executed_at
    balance_source_timestamp = latest_trade_executed_at or account.created_at
    snapshot_gap_cash = snapshot.cash_balance - current_cash_balance
    snapshot_gap_equity = snapshot.equity - current_cash_balance

    return {
        "account": {
            "account_id": str(account.id),
            "created_at": account.created_at,
            "asset_class": account.asset_class,
            "is_active": bool(account.is_active),
        },
        "evaluation": {
            "generated_at": datetime.now(timezone.utc),
            "policy_source": effective_policy.source,
            "status_input_source": None if status_payload is None else status_payload.get("daily_loss_input_source"),
            "latest_trade_executed_at": latest_trade_executed_at,
            "balance_source_timestamp": balance_source_timestamp,
            "trade_count": trade_count,
        },
        "inputs": {
            "starting_balance": {
                "value": format(starting_balance, "f"),
                "source": "paper_accounts.starting_balance",
                "record_created_at": account.created_at,
            },
            "current_cash_balance": {
                "value": format(current_cash_balance, "f"),
                "source": "paper_accounts.current_cash_balance",
                "record_created_at": account.created_at,
                "latest_trade_executed_at": latest_trade_executed_at,
            },
            "max_daily_loss_pct": {
                "value": format(effective_policy.max_daily_loss_pct, "f"),
                "source": effective_policy.source,
            },
            "max_drawdown_pct": {
                "value": format(effective_policy.max_drawdown_pct, "f"),
                "source": effective_policy.source,
            },
        },
        "formulas": {
            "legacy_cash_only.daily_loss.used": "max(0, starting_balance - current_cash_balance)",
            "legacy_cash_only.daily_loss.limit": "starting_balance * max_daily_loss_pct",
            "legacy_cash_only.drawdown.used": "max(0, starting_balance - current_cash_balance)",
            "legacy_cash_only.drawdown.limit": "starting_balance * max_drawdown_pct",
            "authoritative_equity.daily_loss.used": "max(0, start_of_day_equity - current_equity)",
            "authoritative_equity.daily_loss.limit": "start_of_day_equity * max_daily_loss_pct",
            "authoritative_equity.drawdown.used": "max(0, high_water_mark_equity - current_equity)",
            "authoritative_equity.drawdown.limit": "high_water_mark_equity * max_drawdown_pct",
            "pct_used": "used / limit if limit > 0 else 0",
        },
        "status": status_payload,
        "status_error": status_error,
        "equity_evidence": {
            "ready": equity_evidence.ready,
            "fail_closed_reason": equity_evidence.fail_closed_reason,
            "valuation_state": equity_evidence.valuation.valuation_state,
            "valuation_source": equity_evidence.valuation.valuation_source,
            "latest_price_timestamp": equity_evidence.valuation.latest_price_timestamp,
            "stale_cutoff": equity_evidence.valuation.stale_cutoff,
            "missing_price_assets": equity_evidence.valuation.missing_price_assets,
            "stale_price_assets": equity_evidence.valuation.stale_price_assets,
            "price_evidence": equity_evidence.valuation.price_evidence,
            "unresolved_reconciliation_count": equity_evidence.unresolved_reconciliation_count,
            "unknown_provider_order_count": equity_evidence.unknown_provider_order_count,
            "start_of_day_source": equity_evidence.baseline.start_of_day_source,
            "high_water_mark_source": equity_evidence.baseline.high_water_mark_source,
            "baseline_state": equity_evidence.baseline.baseline_state,
        },
        "snapshot": {
            "cash_balance": format(snapshot.cash_balance, "f"),
            "position_value": format(snapshot.position_value, "f"),
            "equity": format(snapshot.equity, "f"),
            "equity_return_usd": format(snapshot.equity_return_usd, "f"),
            "equity_return_pct": format(snapshot.equity_return_pct, "f"),
            "positions": [
                {
                    "asset_id": str(item.asset_id),
                    "symbol": item.symbol,
                    "quantity": format(item.quantity, "f"),
                    "avg_entry_price": format(item.avg_entry_price, "f"),
                    "position_value": format(item.position_value, "f"),
                    "unrealized_pnl_usd": format(item.unrealized_pnl_usd, "f"),
                    "unrealized_pnl_pct": format(item.unrealized_pnl_pct, "f"),
                }
                for item in snapshot.positions
            ],
        },
        "diagnosis": {
            "persisted_cash_balance_minus_computed_cash_balance": format(snapshot_gap_cash, "f"),
            "persisted_cash_balance_minus_snapshot_equity": format(snapshot_gap_equity, "f"),
            "ledger_alignment": "aligned" if snapshot_gap_cash == Decimal("0") else "divergent",
            "legacy_cash_only": {
                "daily_loss": {
                    "used": format(old_daily_loss_used, "f"),
                    "limit": format(old_daily_loss_limit, "f"),
                    "pct_used": format(old_daily_loss_pct, "f"),
                },
                "drawdown": {
                    "used": format(old_drawdown_used, "f"),
                    "limit": format(old_drawdown_limit, "f"),
                    "pct_used": format(old_drawdown_pct, "f"),
                },
            },
            "authoritative_equity_based": {
                "daily_loss": {
                    "used": format(authoritative_daily_loss_used, "f"),
                    "limit": format(authoritative_daily_loss_limit, "f"),
                    "pct_used": format(authoritative_daily_loss_pct, "f"),
                },
                "drawdown": {
                    "used": format(authoritative_drawdown_used, "f"),
                    "limit": format(authoritative_drawdown_limit, "f"),
                    "pct_used": format(authoritative_drawdown_pct, "f"),
                },
                "current_equity": format(authoritative_current_equity, "f"),
                "current_cash_balance": format(equity_evidence.valuation.cash_balance, "f"),
                "current_position_value": format(equity_evidence.valuation.position_value, "f"),
                "start_of_day_equity": format(authoritative_start_of_day_equity, "f"),
                "high_water_mark_equity": format(authoritative_high_water_mark_equity, "f"),
                "valuation_source": equity_evidence.valuation.valuation_source,
                "valuation_state": equity_evidence.valuation.valuation_state,
                "daily_loss_baseline_source": equity_evidence.baseline.start_of_day_source,
                "drawdown_baseline_source": equity_evidence.baseline.high_water_mark_source,
            },
        },
    }


async def fetch_watch_status(
    *,
    mandate_id: UUID | None,
    candle_symbol: str | None,
    candle_interval: str,
    candle_exchange: str | None,
    candle_max_age_minutes: int,
) -> dict[str, Any]:
    return await fetch_operator_status(
        mandate_id=mandate_id,
        candle_symbol=candle_symbol,
        candle_interval=candle_interval,
        candle_exchange=candle_exchange,
        candle_max_age_minutes=candle_max_age_minutes,
    )


async def fetch_strategy_roster_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_run = await db.scalar(
            select(StrategyRosterRun)
            .where(StrategyRosterRun.provider == provider)
            .where(StrategyRosterRun.product_id == product_id)
            .where(StrategyRosterRun.interval == interval)
            .order_by(desc(StrategyRosterRun.candle_close_time), desc(StrategyRosterRun.created_at))
            .limit(1)
        )

        if latest_run is None:
            return {
                "provider": provider,
                "product_id": product_id,
                "interval": interval,
                "roster_run": None,
                "proposals": [],
            }

        proposals = list(
            (
                await db.execute(
                    select(StrategyRosterProposal)
                    .where(StrategyRosterProposal.roster_run_id == latest_run.roster_run_id)
                    .order_by(StrategyRosterProposal.strategy_slug.asc())
                )
            ).scalars().all()
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "roster_run": {
            "roster_run_id": latest_run.roster_run_id,
            "asset_id": latest_run.asset_id,
            "candle_open_time": latest_run.candle_open_time,
            "candle_close_time": latest_run.candle_close_time,
            "trigger": latest_run.trigger,
            "started_at": latest_run.started_at,
            "completed_at": latest_run.completed_at,
            "strategies_requested": list(latest_run.strategies_requested or []),
            "strategies_completed": list(latest_run.strategies_completed or []),
            "strategies_failed": list(latest_run.strategies_failed or []),
            "buy_count": latest_run.buy_count,
            "sell_count": latest_run.sell_count,
            "hold_count": latest_run.hold_count,
            "execution_mode": latest_run.execution_mode,
            "live_submission_allowed": latest_run.live_submission_allowed,
            "scheduled_cycle_id": latest_run.scheduled_cycle_id,
        },
        "proposals": [
            {
                "proposal_id": item.proposal_id,
                "strategy_slug": item.strategy_slug,
                "strategy_version": item.strategy_version,
                "strategy_identity": item.strategy_identity,
                "parameter_set_identity": item.parameter_set_identity,
                "action": item.action,
                "evaluation_status": item.evaluation_status,
                "strength": item.strength,
                "confidence": item.confidence,
                "reason": item.reason,
                "deterministic_explanation": list(item.deterministic_explanation or []),
                "indicator_values": item.indicator_values,
                "market_window_evidence": item.market_window_evidence,
                "evaluated_at": item.evaluated_at,
            }
            for item in proposals
        ],
    }
async def fetch_strategy_scorecards_summary(
    *,
    provider: str,
    product_id: str,
    interval: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        latest_outcome_at = await db.scalar(
            select(StrategyRosterProposalOutcome.evaluated_at)
            .where(StrategyRosterProposalOutcome.provider == provider)
            .where(StrategyRosterProposalOutcome.product_id == product_id)
            .where(StrategyRosterProposalOutcome.interval == interval)
            .order_by(desc(StrategyRosterProposalOutcome.evaluated_at))
            .limit(1)
        )
        scorecards = await fetch_strategy_scorecards(
            db=db,
            provider=provider,
            product_id=product_id,
            interval=interval,
        )

    return {
        "provider": provider,
        "product_id": product_id,
        "interval": interval,
        "latest_outcome_evaluated_at": latest_outcome_at,
        "scorecards": [
            {
                "strategy_slug": item.strategy_slug,
                "per_horizon": [
                    {
                        "horizon": bucket.horizon_label,
                        "total_evaluated": bucket.total_evaluated,
                        "buy_evaluations": bucket.buy_evaluations,
                        "buy_correct": bucket.buy_correct,
                        "sell_evaluations": bucket.sell_evaluations,
                        "sell_correct": bucket.sell_correct,
                        "hold_evaluations": bucket.hold_evaluations,
                        "hold_correct": bucket.hold_correct,
                        "overall_correct_pct": bucket.overall_correct_pct,
                        "average_raw_return_pct": bucket.average_raw_return_pct,
                        "average_fee_adjusted_return_pct": bucket.average_fee_adjusted_return_pct,
                        "average_mfe_pct": bucket.average_mfe_pct,
                        "average_mae_pct": bucket.average_mae_pct,
                    }
                    for bucket in item.per_horizon
                ],
                "aggregate": {
                    "horizon": item.aggregate.horizon_label,
                    "total_evaluated": item.aggregate.total_evaluated,
                    "buy_evaluations": item.aggregate.buy_evaluations,
                    "buy_correct": item.aggregate.buy_correct,
                    "sell_evaluations": item.aggregate.sell_evaluations,
                    "sell_correct": item.aggregate.sell_correct,
                    "hold_evaluations": item.aggregate.hold_evaluations,
                    "hold_correct": item.aggregate.hold_correct,
                    "overall_correct_pct": item.aggregate.overall_correct_pct,
                    "average_raw_return_pct": item.aggregate.average_raw_return_pct,
                    "average_fee_adjusted_return_pct": item.aggregate.average_fee_adjusted_return_pct,
                    "average_mfe_pct": item.aggregate.average_mfe_pct,
                    "average_mae_pct": item.aggregate.average_mae_pct,
                },
                "best_regime": item.best_regime,
                "worst_regime": item.worst_regime,
                "regime_evidence_count": item.regime_evidence_count,
                "regime_min_evidence_required": item.regime_min_evidence_required,
            }
            for item in scorecards
        ],
    }


def _serialize_commissioning_run(run: VenueCommissioningRun) -> dict[str, Any]:
    return {
        "commissioning_run_id": run.commissioning_run_id,
        "status": run.status,
        "execution_purpose": run.execution_purpose,
        "commissioning_type": run.commissioning_type,
        "provider": run.provider,
        "environment": run.environment,
        "product_id": run.product_id,
        "max_quote_notional": run.max_quote_notional,
        "max_buys": run.max_buys,
        "max_sells": run.max_sells,
        "hold_minutes": run.hold_minutes,
        "buy_requested_quote_usd": run.buy_requested_quote_usd,
        "buy_client_order_id": run.buy_client_order_id,
        "buy_provider_order_id": run.buy_provider_order_id,
        "buy_submitted_at": run.buy_submitted_at,
        "buy_filled_at": run.buy_filled_at,
        "buy_filled_quote_usd": run.buy_filled_quote_usd,
        "buy_filled_base_btc": run.buy_filled_base_btc,
        "buy_avg_price_usd": run.buy_avg_price_usd,
        "buy_fee_usd": run.buy_fee_usd,
        "hold_started_at": run.hold_started_at,
        "hold_due_at": run.hold_due_at,
        "sell_client_order_id": run.sell_client_order_id,
        "sell_provider_order_id": run.sell_provider_order_id,
        "sell_submitted_at": run.sell_submitted_at,
        "sell_filled_at": run.sell_filled_at,
        "sell_requested_base_btc": run.sell_requested_base_btc,
        "sell_filled_base_btc": run.sell_filled_base_btc,
        "sell_filled_quote_usd": run.sell_filled_quote_usd,
        "sell_avg_price_usd": run.sell_avg_price_usd,
        "sell_fee_usd": run.sell_fee_usd,
        "gross_pnl_usd": run.gross_pnl_usd,
        "total_fees_usd": run.total_fees_usd,
        "net_realized_pnl_usd": run.net_realized_pnl_usd,
        "dust_base_btc": run.dust_base_btc,
        "duplicate_orders_detected": run.duplicate_orders_detected,
        "manual_intervention_required": run.manual_intervention_required,
        "ledger_matches_kraken": run.ledger_matches_kraken,
        "activated_by": run.activated_by,
        "activated_at": run.activated_at,
        "started_by": run.started_by,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "revoked_by": run.revoked_by,
        "revoked_reason": run.revoked_reason,
        "updated_at": run.updated_at,
    }


async def fetch_venue_commission_readiness(
    *,
    provider: str,
    product_id: str,
    environment: str,
    amount_usd: Decimal,
    hold_minutes: int,
) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service
    from app.services.live.venue_commissioning import CommissioningConfig

    config = CommissioningConfig(
        provider=provider,
        product_id=product_id,
        environment=environment,
        amount=amount_usd,
        hold_minutes=hold_minutes,
    )
    async with AsyncSessionLocal() as db:
        readiness = await venue_commissioning_service["evaluate_readiness"](db=db, config=config)

    return {
        "provider": provider,
        "product_id": product_id,
        "environment": environment,
        "amount_usd": amount_usd,
        "hold_minutes": hold_minutes,
        "would_activate_safely": readiness.would_activate_safely,
        "exact_blocker": readiness.exact_blocker,
        "existing_active_run": readiness.existing_active_run,
        "checks": [
            {"label": item.label, "status": item.status, "reason": item.reason}
            for item in readiness.checks
        ],
    }


async def activate_venue_commission_run(
    *,
    actor: str,
    provider: str,
    product_id: str,
    environment: str,
    amount_usd: Decimal,
    hold_minutes: int,
    confirm: bool,
) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service
    from app.services.live.venue_commissioning import CommissioningConfig

    config = CommissioningConfig(
        provider=provider,
        product_id=product_id,
        environment=environment,
        amount=amount_usd,
        hold_minutes=hold_minutes,
    )
    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["activate_run"](
            db=db,
            actor=actor,
            config=config,
            confirm=confirm,
        )

    return {
        "activation": "accepted",
        "run": _serialize_commissioning_run(run),
    }


async def start_venue_commission_run(*, actor: str, commissioning_run_id: UUID, confirm: bool) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["start_run"](
            db=db,
            actor=actor,
            run_id=commissioning_run_id,
            confirm=confirm,
        )

    return {
        "start": "processed",
        "run": _serialize_commissioning_run(run),
    }


async def fetch_venue_commission_status(*, commissioning_run_id: UUID) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["get_run"](db=db, run_id=commissioning_run_id)

    return {
        "run": _serialize_commissioning_run(run),
    }


async def revoke_venue_commission_run(*, actor: str, commissioning_run_id: UUID, confirm: bool) -> dict[str, Any]:
    from app.services.live.venue_commissioning import service as venue_commissioning_service

    async with AsyncSessionLocal() as db:
        run = await venue_commissioning_service["revoke_run"](
            db=db,
            actor=actor,
            run_id=commissioning_run_id,
            confirm=confirm,
        )

    return {
        "revoke": "processed",
        "run": _serialize_commissioning_run(run),
    }


async def inspect_canonical_campaign_binding(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_canonical_campaign_binding(
            db=db,
            request=CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )

    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def bind_canonical_campaign_runtime(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _bind_canonical_campaign_runtime(
            db=db,
            request=CanonicalCampaignBindingRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )

    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_canonical_campaign_binding_status(*, campaign_id: UUID, campaign_version: int, paper_account_id: UUID, live_trading_profile_id: UUID, provider: str, environment: str, product_id: str, actor: str, confirm: bool) -> dict[str, Any]:
    return await inspect_canonical_campaign_binding(
        campaign_id=campaign_id,
        campaign_version=campaign_version,
        paper_account_id=paper_account_id,
        live_trading_profile_id=live_trading_profile_id,
        provider=provider,
        environment=environment,
        product_id=product_id,
        actor=actor,
        confirm=confirm,
    )


async def fetch_canonical_campaign_binding_audit(*, campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_canonical_campaign_binding_audit(db=db, campaign_id=campaign_id, limit=limit)


async def create_canonical_preview_package_bundle(
    *,
    campaign_id: UUID,
    campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    max_proposed_order_amount: Decimal,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await create_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageCreateRequest(
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product=product_id,
                max_proposed_order_amount=max_proposed_order_amount,
                actor=actor,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
    return payload


async def show_canonical_preview_package_bundle(*, package_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await get_canonical_preview_package(db=db, package_id=package_id)


async def canonical_preview_package_readiness(*, package_id: UUID) -> dict[str, Any]:
    payload = await show_canonical_preview_package_bundle(package_id=package_id)
    return {
        "package_id": str(package_id),
        "readiness": payload.get("readiness"),
    }


async def canonical_preview_package_history(
    *,
    campaign_id: UUID,
    campaign_version: int | None,
    limit: int,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await list_canonical_preview_package_history(
            db=db,
            campaign_id=campaign_id,
            campaign_version=campaign_version,
            limit=limit,
        )


async def authorize_canonical_preview_package_bundle(
    *,
    package_id: UUID,
    actor: str,
    approver_role: str,
    rationale: str,
    expires_at: datetime,
    max_order_usd: Decimal,
    max_total_deployed_campaign_capital_usd: Decimal,
    no_leverage: bool,
    idempotency_key: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await authorize_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageAuthorizeRequest(
                package_id=package_id,
                actor=actor,
                approver_role=approver_role,
                rationale=rationale,
                expires_at=expires_at,
                max_order_usd=max_order_usd,
                max_total_deployed_campaign_capital_usd=max_total_deployed_campaign_capital_usd,
                no_leverage=no_leverage,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def dry_run_canonical_preview_package_bundle(
    *,
    package_id: UUID,
    approval_event_id: UUID,
    operator_identity: str,
    idempotency_token: str,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await run_dry_run_for_canonical_preview_package(
            db=db,
            request=CanonicalPreviewPackageDryRunRequest(
                package_id=package_id,
                approval_event_id=approval_event_id,
                operator_identity=operator_identity,
                idempotency_token=idempotency_token,
            ),
        )
        await db.commit()
        return payload


async def activate_canonical_proving_campaign_bundle(
    *,
    package_id: UUID,
    approval_event_id: UUID,
    dry_run_live_crypto_order_id: UUID,
    actor: str,
    expires_at: datetime,
    idempotency_key: str,
    confirm: bool,
) -> dict[str, Any]:
    if not confirm:
        raise PermissionError("confirmation required for canonical proving activation")

    async with AsyncSessionLocal() as db:
        payload = await activate_canonical_proving_campaign(
            db=db,
            request=CanonicalPreviewPackageActivationRequest(
                package_id=package_id,
                approval_event_id=approval_event_id,
                dry_run_live_crypto_order_id=dry_run_live_crypto_order_id,
                actor=actor,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def canonical_proving_activation_status(*, package_id: UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await get_canonical_proving_activation_status(db=db, package_id=package_id)


async def pause_canonical_proving_activation_bundle(*, package_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await pause_canonical_proving_activation(
            db=db,
            request=CanonicalPreviewPackagePauseRequest(
                package_id=package_id,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def revoke_canonical_proving_activation_bundle(*, package_id: UUID, actor: str, reason: str, idempotency_key: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        payload = await revoke_canonical_proving_activation(
            db=db,
            request=CanonicalPreviewPackageRevokeRequest(
                package_id=package_id,
                actor=actor,
                reason=reason,
                idempotency_key=idempotency_key,
            ),
        )
        await db.commit()
        return payload


async def inspect_legacy_campaign_transition(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _inspect_legacy_campaign_transition(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "ready": result.ready,
        "blockers": result.blockers,
        "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.checks],
        "snapshot": result.snapshot,
    }


async def transition_legacy_campaign_to_canonical_successor(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _transition_legacy_campaign_to_canonical_successor(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def rollback_legacy_campaign_transition(
    *,
    legacy_campaign_id: UUID,
    canonical_campaign_id: UUID,
    canonical_campaign_version: int,
    paper_account_id: UUID,
    live_trading_profile_id: UUID,
    provider: str,
    environment: str,
    product_id: str,
    actor: str,
    confirm: bool,
) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await _rollback_legacy_campaign_transition(
            db=db,
            request=LegacyCampaignTransitionRequest(
                legacy_campaign_id=legacy_campaign_id,
                canonical_campaign_id=canonical_campaign_id,
                canonical_campaign_version=canonical_campaign_version,
                paper_account_id=paper_account_id,
                live_trading_profile_id=live_trading_profile_id,
                provider=provider,
                environment=environment,
                product_id=product_id,
                actor=actor,
                confirm=confirm,
            ),
        )
    return {
        "changed": result.changed,
        "idempotent": result.idempotent,
        "audit_created": result.audit_created,
        "before": result.before,
        "after": result.after,
        "readiness": {
            "ready": result.readiness.ready,
            "blockers": result.readiness.blockers,
            "checks": [{"code": item.code, "passed": item.passed, "detail": item.detail} for item in result.readiness.checks],
            "snapshot": result.readiness.snapshot,
        },
    }


async def fetch_legacy_campaign_transition_audit(*, legacy_campaign_id: UUID, limit: int = 20) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        return await _fetch_legacy_campaign_transition_audit(db=db, legacy_campaign_id=legacy_campaign_id, limit=limit)
