from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.capital_campaign import CapitalCampaign
from app.models.candle import Candle
from app.models.decision_record import DecisionRecord
from app.models.paper_account import PaperAccount
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome
from app.models.strategy_roster_run import StrategyRosterRun
from app.services.position_lifecycle.contracts import PositionLifecycleEvaluation
from app.schemas.capital_campaign_domain import (
    CapitalCampaignDefinitionResponse,
    CapitalCampaignPreviewRequest,
    LifecycleEvidenceInput,
    RiskPreviewInput,
    StrategyEvidenceInput,
)
from app.services.capital_campaign_domain.preview_engine import build_campaign_preview
from app.services.position_lifecycle.evaluator import evaluate_position_lifecycle
from app.services.position_lifecycle.policy_registry import resolve_lifecycle_policy
from app.services.position_lifecycle.source_adapter import load_position_snapshots
from app.services.profitability.engine import ProfitabilityInput, evaluate_exit_profitability
from app.services.risk import (
    RiskDecisionAction,
    RiskDecisionPersistenceRequest,
    RiskEvaluationContext,
    RiskEvaluationRequest,
    evaluate_signal_risk,
    persist_risk_decision,
)
from app.services.risk.risk_context import resolve_execution_risk_context
from app.services.strategy_outcomes.service import fetch_strategy_scorecards


@dataclass(frozen=True)
class CampaignAuthoritativeCycleResult:
    composition: dict[str, Any]
    preview: Any | None


_SUPPORTED_FRESHNESS_MINUTES = 15


def _normalize_symbol(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _product_symbol(value: str) -> str:
    return _normalize_symbol(value).split("-", 1)[0]


async def _load_runtime_campaign(*, db: AsyncSession, runtime_campaign_uuid: UUID) -> CapitalCampaign | None:
    return await db.scalar(select(CapitalCampaign).where(CapitalCampaign.uuid == runtime_campaign_uuid).limit(1))


async def _load_latest_asset(*, db: AsyncSession, symbol: str, exchange: str) -> Asset | None:
    result = await db.execute(
        select(Asset)
        .where(Asset.symbol == symbol)
        .where(Asset.exchange == exchange)
        .where(Asset.asset_class == "crypto")
        .where(Asset.is_active.is_(True))
        .order_by(Asset.created_at.desc(), Asset.id.desc())
    )
    assets = list(result.scalars().all())
    if not assets:
        return None
    if len(assets) > 1:
        return None
    return assets[0]


async def _load_latest_closed_candle(*, db: AsyncSession, asset_id: UUID, interval: str, now: datetime) -> Candle | None:
    result = await db.execute(
        select(Candle)
        .where(Candle.asset_id == asset_id)
        .where(Candle.interval == interval)
        .where(Candle.close_time <= now)
        .order_by(Candle.close_time.desc(), Candle.open_time.desc(), Candle.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_latest_strategy_evidence(
    *,
    db: AsyncSession,
    asset_id: UUID,
    product_id: str,
    interval: str,
) -> tuple[dict[str, Any] | None, str | None]:
    scorecards = await fetch_strategy_scorecards(db=db, provider="kraken_spot", product_id=product_id, interval=interval)
    if not scorecards:
        return None, "strategy_evidence_unavailable"

    best_scorecard = max(
        scorecards,
        key=lambda item: (
            item.aggregate.average_fee_adjusted_return_pct or Decimal("-999999"),
            item.aggregate.overall_correct_pct or Decimal("-999999"),
            item.aggregate.total_evaluated,
            item.strategy_slug,
        ),
    )

    proposal_result = await db.execute(
        select(StrategyRosterProposal)
        .where(StrategyRosterProposal.asset_id == asset_id)
        .where(StrategyRosterProposal.product_id == product_id)
        .where(StrategyRosterProposal.interval == interval)
        .order_by(StrategyRosterProposal.candle_close_time.desc(), StrategyRosterProposal.created_at.desc())
        .limit(1)
    )
    proposal = proposal_result.scalar_one_or_none()
    if proposal is None:
        return None, "strategy_evidence_unavailable"

    decision_records = (
        await db.execute(
            select(DecisionRecord)
            .order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc())
            .limit(100)
        )
    ).scalars().all()
    decision_record = None
    for item in decision_records:
        asset = item.asset if isinstance(item.asset, dict) else {}
        if _normalize_symbol(str(asset.get("symbol") or asset.get("product_id") or "")) != _normalize_symbol(product_id):
            continue
        if str(item.timeframe).strip().lower() != interval.strip().lower():
            continue
        decision_record = item
        break
    if decision_record is None:
        return None, "strategy_evidence_unavailable"

    matching_support = None
    for entry in decision_record.supporting_strategies or []:
        strategy_identity = str(entry.get("strategy_identity") or entry.get("strategyId") or entry.get("strategy_id") or entry.get("slug") or entry.get("name") or "")
        if not strategy_identity or strategy_identity.split("@", 1)[0] == proposal.strategy_slug or strategy_identity == proposal.strategy_identity:
            matching_support = entry
            break
    if matching_support is None and decision_record.supporting_strategies:
        matching_support = decision_record.supporting_strategies[0]
    if matching_support is None:
        return None, "strategy_evidence_unavailable"

    expected_value = None
    if isinstance(decision_record.expected_reward, dict):
        expected_value = decision_record.expected_reward.get("expected_value") or decision_record.expected_reward.get("value")

    evidence = {
        "authority_class": "AUTHORITATIVE",
        "source_type": "decision_record_and_strategy_roster",
        "source_identity": {
            "decision_record_id": str(decision_record.decision_id),
            "strategy_roster_run_id": str(proposal.roster_run_id),
            "proposal_id": str(proposal.proposal_id),
            "scorecard_strategy_slug": best_scorecard.strategy_slug,
        },
        "observed_at": max(
            decision_record.timestamp,
            proposal.evaluated_at,
            getattr(best_scorecard.aggregate, "evaluated_at", proposal.evaluated_at),
        ).isoformat(),
        "freshness": "fresh",
        "availability": "available",
        "reason": "strategy evidence resolved from persisted decision and roster records",
        "strategy_identity": str(matching_support.get("strategy_identity") or matching_support.get("strategy_id") or proposal.strategy_identity),
        "strategy_version": str(matching_support.get("version") or proposal.strategy_version),
        "action": str(matching_support.get("action") or proposal.action),
        "score": str(matching_support.get("score")) if matching_support.get("score") is not None else (format(proposal.strength, "f") if proposal.strength is not None else None),
        "confidence": str(matching_support.get("confidence")) if matching_support.get("confidence") is not None else (format(proposal.confidence, "f") if proposal.confidence is not None else None),
        "sample_size": best_scorecard.aggregate.total_evaluated,
        "profitable_after_fees_performance": None
        if best_scorecard.aggregate.average_fee_adjusted_return_pct is None
        else format(best_scorecard.aggregate.average_fee_adjusted_return_pct, "f"),
        "expected_value": None if expected_value is None else str(expected_value),
        "evidence_timestamp": proposal.evaluated_at.isoformat(),
        "scorecard": {
            "best_strategy_slug": best_scorecard.strategy_slug,
            "aggregate_total_evaluated": best_scorecard.aggregate.total_evaluated,
            "aggregate_average_fee_adjusted_return_pct": None
            if best_scorecard.aggregate.average_fee_adjusted_return_pct is None
            else format(best_scorecard.aggregate.average_fee_adjusted_return_pct, "f"),
            "aggregate_overall_correct_pct": None
            if best_scorecard.aggregate.overall_correct_pct is None
            else format(best_scorecard.aggregate.overall_correct_pct, "f"),
        },
        "decision_record": {
            "decision_id": str(decision_record.decision_id),
            "trade_accepted": decision_record.trade_accepted,
            "trade_rejected_reason": decision_record.trade_rejected_reason,
            "supporting_strategies": decision_record.supporting_strategies,
            "opposing_strategies": decision_record.opposing_strategies,
            "expected_risk": decision_record.expected_risk,
            "expected_reward": decision_record.expected_reward,
            "generated_signals": decision_record.generated_signals,
        },
    }
    return evidence, None


async def _load_position_evidence(
    *,
    db: AsyncSession,
    account_id: UUID | None,
    campaign_id: int,
    symbol: str,
    asset: Asset,
    candle: Candle,
    now: datetime,
) -> dict[str, Any]:
    if account_id is None:
        return {
            "authority_class": "UNAVAILABLE",
            "source_type": "campaign_account",
            "source_identity": {"paper_account_id": None},
            "observed_at": now.isoformat(),
            "freshness": "unavailable",
            "availability": "unavailable",
            "reason": "paper_account_unavailable",
            "position": None,
            "lifecycle": None,
            "profitability": None,
        }

    snapshots = await load_position_snapshots(db=db, account_id=account_id, campaign_id=campaign_id)
    snapshot = next((item for item in snapshots if _product_symbol(item.symbol) == _product_symbol(symbol)), None)
    if snapshot is None:
        return {
            "authority_class": "AUTHORITATIVE",
            "source_type": "position_lifecycle",
            "source_identity": {"paper_account_id": str(account_id), "campaign_id": campaign_id},
            "observed_at": now.isoformat(),
            "freshness": "fresh",
            "availability": "available",
            "reason": "no_open_position",
            "position": None,
            "lifecycle": None,
            "profitability": None,
        }

    policy = resolve_lifecycle_policy(asset_class=snapshot.asset_class, symbol=snapshot.symbol, venue=asset.exchange, now=now)
    if policy is None:
        return {
            "authority_class": "UNAVAILABLE",
            "source_type": "position_lifecycle",
            "source_identity": {"paper_account_id": str(account_id), "campaign_id": campaign_id, "symbol": snapshot.symbol},
            "observed_at": now.isoformat(),
            "freshness": "unavailable",
            "availability": "unavailable",
            "reason": "lifecycle_policy_unavailable",
            "position": None,
            "lifecycle": None,
            "profitability": None,
        }

    evaluation = evaluate_position_lifecycle(snapshot=snapshot, policy=policy, now=now)
    profitability = None
    if snapshot.position_size > Decimal("0") and snapshot.current_price is not None:
        max_hold_until = None
        if snapshot.opened_at is not None and policy.max_hold_minutes is not None:
            max_hold_until = snapshot.opened_at + timedelta(minutes=policy.max_hold_minutes)
        profitability = evaluate_exit_profitability(
            ProfitabilityInput(
                position_size=snapshot.position_size,
                entry_price=snapshot.entry_price,
                current_price=snapshot.current_price,
                accumulated_entry_and_carry_costs=snapshot.accumulated_entry_and_carry_costs,
                estimated_exit_fee_rate=policy.estimated_exit_fee_rate,
                estimated_slippage_rate=policy.estimated_slippage_rate,
                minimum_net_profit_to_exit=policy.minimum_net_profit_to_exit,
                stop_loss_price=policy.stop_loss_price,
                now=now,
                max_hold_until=max_hold_until,
            )
        )

    return {
        "authority_class": "AUTHORITATIVE" if not evaluation.market_data_stale else "STALE",
        "source_type": "position_lifecycle",
        "source_identity": {
            "paper_account_id": str(account_id),
            "campaign_id": campaign_id,
            "symbol": snapshot.symbol,
            "position_id": snapshot.position_id,
            "candle_id": None if snapshot.market_data_candle_id is None else str(snapshot.market_data_candle_id),
        },
        "observed_at": now.isoformat(),
        "freshness": "fresh" if not evaluation.market_data_stale else "stale",
        "availability": "available",
        "reason": evaluation.reason,
        "position": {
            "quantity": format(snapshot.position_size, "f"),
            "entry_price": format(snapshot.entry_price, "f"),
            "paid_costs": format(snapshot.accumulated_entry_and_carry_costs, "f"),
            "current_market_value": None if evaluation.current_market_value is None else format(evaluation.current_market_value, "f"),
            "break_even_price": None if evaluation.break_even_price is None else format(evaluation.break_even_price, "f"),
            "minimum_profitable_exit_price": None if evaluation.minimum_profitable_exit_price is None else format(evaluation.minimum_profitable_exit_price, "f"),
            "expected_net_pnl_if_sold_now": None if evaluation.expected_net_realized_pnl_if_sold_now is None else format(evaluation.expected_net_realized_pnl_if_sold_now, "f"),
            "lifecycle_state": evaluation.lifecycle_state,
            "lifecycle_recommendation": evaluation.recommendation,
            "stale_indicator": evaluation.stale_indicator,
            "dust_indicator": evaluation.dust_indicator,
            "closed_indicator": evaluation.closed_indicator,
            "market_data_source": snapshot.market_data_source,
            "market_data_timestamp": None if snapshot.market_data_timestamp is None else snapshot.market_data_timestamp.isoformat(),
            "market_data_age_minutes": snapshot.market_data_age_minutes,
            "market_data_interval": snapshot.market_data_interval,
            "market_data_candle_id": snapshot.market_data_candle_id,
        },
        "lifecycle": {
            "lifecycle_state": evaluation.lifecycle_state,
            "recommendation": evaluation.recommendation,
            "reason": evaluation.reason,
            "market_data_stale": evaluation.market_data_stale,
            "stale_indicator": evaluation.stale_indicator,
            "dust_indicator": evaluation.dust_indicator,
            "closed_indicator": evaluation.closed_indicator,
        },
        "profitability": None
        if profitability is None
        else {
            "entry_price": format(profitability.entry_price, "f"),
            "current_price": format(profitability.current_price, "f"),
            "current_market_value": format(profitability.current_market_value, "f"),
            "gross_pnl": format(profitability.gross_pnl, "f"),
            "paid_costs": format(profitability.paid_costs, "f"),
            "estimated_exit_fee": format(profitability.estimated_exit_fee, "f"),
            "estimated_slippage": format(profitability.estimated_slippage, "f"),
            "break_even_price": None if profitability.break_even_price is None else format(profitability.break_even_price, "f"),
            "minimum_profitable_exit_price": None if profitability.minimum_profitable_exit_price is None else format(profitability.minimum_profitable_exit_price, "f"),
            "expected_net_realized_pnl_if_sold_now": format(profitability.expected_net_realized_pnl_if_sold_now, "f"),
            "recommendation": profitability.recommendation,
            "reason": profitability.reason,
        },
    }


async def _load_market_evidence(
    *,
    db: AsyncSession,
    symbol: str,
    exchange: str,
    candle_interval: str,
    now: datetime,
) -> tuple[dict[str, Any], Asset | None, Candle | None]:
    base = _product_symbol(symbol)
    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.symbol == base)
            .where(Asset.asset_class == "crypto")
            .where(Asset.is_active.is_(True))
            .order_by(Asset.created_at.desc(), Asset.id.desc())
        )
    ).scalars().all()
    if not assets:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "asset_table",
                "source_identity": {"symbol": base, "exchange": exchange},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "asset_mapping_unavailable",
            },
            None,
            None,
        )

    matching_assets = [item for item in assets if _normalize_symbol(item.exchange) == _normalize_symbol(exchange) and str(item.base_currency or "").upper() in {"USD", "USDC", "USDT"}]
    if len(matching_assets) > 1:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "asset_table",
                "source_identity": {"symbol": base, "exchange": exchange},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "ambiguous_market_source",
            },
            None,
            None,
        )
    if not matching_assets:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "asset_table",
                "source_identity": {"symbol": base, "exchange": exchange},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "provider_product_unsupported",
            },
            None,
            None,
        )

    asset = matching_assets[0]
    candle = await _load_latest_closed_candle(db=db, asset_id=asset.id, interval=candle_interval, now=now)
    if candle is None:
        return (
            {
                "authority_class": "UNAVAILABLE",
                "source_type": "candle_table",
                "source_identity": {"asset_id": str(asset.id), "interval": candle_interval},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "market_data_unavailable",
            },
            asset,
            None,
        )

    freshness_seconds = int((now - candle.close_time.astimezone(timezone.utc)).total_seconds())
    if freshness_seconds < 0:
        freshness_seconds = abs(freshness_seconds)
    freshness_minutes = freshness_seconds // 60
    if freshness_minutes > _SUPPORTED_FRESHNESS_MINUTES:
        return (
            {
                "authority_class": "STALE",
                "source_type": "candle_table",
                "source_identity": {"asset_id": str(asset.id), "candle_id": candle.id, "interval": candle.interval},
                "observed_at": candle.close_time.astimezone(timezone.utc).isoformat(),
                "freshness": "stale",
                "availability": "available",
                "reason": "stale_market_data",
                "asset_id": str(asset.id),
                "provider": asset.exchange,
                "product": symbol,
                "latest_closed_candle_id": candle.id,
                "interval": candle.interval,
                "close_price": format(Decimal(candle.close), "f"),
                "close_timestamp": candle.close_time.astimezone(timezone.utc).isoformat(),
                "freshness_seconds": freshness_seconds,
                "freshness_minutes": freshness_minutes,
            },
            asset,
            candle,
        )

    return (
        {
            "authority_class": "AUTHORITATIVE",
            "source_type": "candle_table",
            "source_identity": {"asset_id": str(asset.id), "candle_id": candle.id, "interval": candle.interval},
            "observed_at": candle.close_time.astimezone(timezone.utc).isoformat(),
            "freshness": "fresh",
            "availability": "available",
            "reason": "market data resolved from canonical asset and candle tables",
            "asset_id": str(asset.id),
            "provider": asset.exchange,
            "product": symbol,
            "latest_closed_candle_id": candle.id,
            "interval": candle.interval,
            "close_price": format(Decimal(candle.close), "f"),
            "close_timestamp": candle.close_time.astimezone(timezone.utc).isoformat(),
            "freshness_seconds": freshness_seconds,
            "freshness_minutes": freshness_minutes,
        },
        asset,
        candle,
    )


async def compose_campaign_authoritative_cycle(
    *,
    db: AsyncSession,
    campaign_definition: CapitalCampaignDefinitionResponse,
    trigger: str,
    candle: Candle,
) -> CampaignAuthoritativeCycleResult:
    now = datetime.now(timezone.utc)
    runtime_campaign = await _load_runtime_campaign(db=db, runtime_campaign_uuid=campaign_definition.runtime_campaign_uuid)
    if runtime_campaign is None or runtime_campaign.paper_account_id is None:
        composition = {
            "campaign_id": str(campaign_definition.campaign_id),
            "campaign_version": campaign_definition.version,
            "execution_mode": "preview",
            "execution_submitted": False,
            "provider_order_id": None,
            "failed_closed": True,
            "termination_stage": "failed_closed",
            "proposed_action": "FAILED_CLOSED",
            "failure_reason": "runtime_campaign_or_paper_account_unavailable",
            "selected_decision": {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": "runtime_campaign_or_paper_account_unavailable"},
            "eligible_candidates": [],
            "rejected_candidates": [],
            "ranked_candidates": [],
            "risk_outputs": [],
            "authoritative_evidence": {},
            "deterministic_explanation": ["runtime_campaign_or_paper_account_unavailable"],
            "candidate_instruments": list(campaign_definition.allowed_instruments),
            "decision_evidence": {},
        }
        return CampaignAuthoritativeCycleResult(composition=composition, preview=None)

    paper_account = await db.scalar(select(PaperAccount).where(PaperAccount.id == runtime_campaign.paper_account_id).limit(1))
    if paper_account is None:
        composition = {
            "campaign_id": str(campaign_definition.campaign_id),
            "campaign_version": campaign_definition.version,
            "execution_mode": "preview",
            "execution_submitted": False,
            "provider_order_id": None,
            "failed_closed": True,
            "termination_stage": "failed_closed",
            "proposed_action": "FAILED_CLOSED",
            "failure_reason": "paper_account_unavailable",
            "selected_decision": {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": "paper_account_unavailable"},
            "eligible_candidates": [],
            "rejected_candidates": [],
            "ranked_candidates": [],
            "risk_outputs": [],
            "authoritative_evidence": {},
            "deterministic_explanation": ["paper_account_unavailable"],
            "candidate_instruments": list(campaign_definition.allowed_instruments),
            "decision_evidence": {},
        }
        return CampaignAuthoritativeCycleResult(composition=composition, preview=None)

    allowed_instruments = [_normalize_symbol(item) for item in campaign_definition.allowed_instruments]
    market_evidence: dict[str, Any] = {}
    strategy_evidence: dict[str, Any] = {}
    position_evidence: dict[str, Any] = {}
    risk_outputs: dict[str, Any] = {}
    candidate_rows: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    for instrument in allowed_instruments:
        market, asset, candle_item = await _load_market_evidence(
            db=db,
            symbol=instrument,
            exchange=runtime_campaign.exchange or "kraken_spot",
            candle_interval=candle.interval,
            now=now,
        )
        market_evidence[instrument] = market
        if asset is None or candle_item is None or market.get("reason") in {"asset_mapping_unavailable", "provider_product_unsupported", "ambiguous_market_source", "market_data_unavailable", "stale_market_data"}:
            rejected_candidates.append({"instrument": instrument, "reason": market.get("reason", "market_data_unavailable"), "market": market})
            continue

        strategy, strategy_reason = await _load_latest_strategy_evidence(
            db=db,
            asset_id=asset.id,
            product_id=instrument,
            interval=candle.interval,
        )
        if strategy is None:
            rejected_candidates.append({"instrument": instrument, "reason": strategy_reason or "strategy_evidence_unavailable", "market": market})
            strategy_evidence[instrument] = {"authority_class": "UNAVAILABLE", "reason": strategy_reason or "strategy_evidence_unavailable"}
            continue
        strategy_evidence[instrument] = strategy

        position = await _load_position_evidence(
            db=db,
            account_id=runtime_campaign.paper_account_id,
            campaign_id=runtime_campaign.id,
            symbol=instrument,
            asset=asset,
            candle=candle_item,
            now=now,
        )
        position_evidence[instrument] = position

        risk_result = None
        risk_reason = None
        risk_verdict = None
        approved_quantity = None
        if position.get("authority_class") == "UNAVAILABLE":
            risk_outputs[instrument] = {
                "authority_class": "UNAVAILABLE",
                "source_type": "risk_engine",
                "source_identity": None,
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": "risk_unavailable",
            }
            rejected_candidates.append({"instrument": instrument, "reason": "risk_unavailable", "market": market, "strategy": strategy, "position": position})
            continue

        risk_context = await resolve_execution_risk_context(db=db, paper_account=paper_account, asset=asset)
        if position["position"] is not None and position["position"].get("closed_indicator") is False and position["position"].get("quantity") not in {None, "0", "0.0"}:
            side = "sell"
            quantity = Decimal(str(position["position"]["quantity"]))
        else:
            side = "buy"
            proposed_allocation = min(
                campaign_definition.remaining_unallocated_capital,
                campaign_definition.maximum_position_size,
                campaign_definition.maximum_total_exposure,
            )
            if runtime_campaign.current_equity is not None:
                proposed_allocation = min(proposed_allocation, Decimal(runtime_campaign.current_equity))
            if proposed_allocation < campaign_definition.minimum_position_size:
                rejected_candidates.append({"instrument": instrument, "reason": "allocation_below_minimum", "market": market, "strategy": strategy, "position": position})
                continue
            quantity = proposed_allocation / Decimal(str(candle_item.close))

        try:
            risk_result = evaluate_signal_risk(
                request=RiskEvaluationRequest(
                    signal_id=UUID(int=0),
                    paper_account_id=runtime_campaign.paper_account_id,
                    asset_id=asset.id,
                    side=side,
                    quantity=quantity,
                    account_equity=risk_context.account_equity,
                    max_position_size_pct=risk_context.max_position_size_pct,
                    min_order_notional=asset.min_order_notional,
                    qty_step_size=asset.qty_step_size,
                    supports_fractional=asset.supports_fractional,
                    start_of_day_equity=risk_context.start_of_day_equity,
                    current_equity=risk_context.current_equity,
                    max_daily_loss_pct=risk_context.max_daily_loss_pct,
                    high_water_mark_equity=risk_context.high_water_mark_equity,
                    max_drawdown_pct=risk_context.max_drawdown_pct,
                    consecutive_losses_on_pair=risk_context.consecutive_losses_on_pair,
                    cooldown_after_losses=risk_context.cooldown_after_losses,
                    last_loss_at=risk_context.last_loss_at,
                    cooldown_duration_minutes=risk_context.cooldown_duration_minutes,
                    evaluation_time=risk_context.evaluation_time,
                    data_is_stale=risk_context.data_is_stale,
                    data_has_gaps=risk_context.data_has_gaps,
                    global_kill_switch_engaged_state=risk_context.global_kill_switch_engaged_state,
                    global_kill_switch_rearm_required=risk_context.global_kill_switch_rearm_required,
                    account_kill_switch_engaged_state=risk_context.account_kill_switch_engaged_state,
                    account_kill_switch_rearm_required=risk_context.account_kill_switch_rearm_required,
                    global_kill_switch_state_observed=risk_context.global_kill_switch_state_observed,
                    account_kill_switch_state_observed=risk_context.account_kill_switch_state_observed,
                    actor="campaign_orchestration",
                ),
                reference_price=Decimal(str(candle_item.close)),
                context=RiskEvaluationContext(
                    global_kill_switch_engaged=bool(risk_context.global_kill_switch_engaged_state),
                    account_trading_paused=False,
                    asset_in_no_trade_zone=False,
                    pair_in_cooldown=False,
                    would_breach_daily_loss=False,
                    would_breach_drawdown=False,
                    has_computable_stop_loss=True,
                    bypass_sizing_rule=False,
                ),
            )
            risk_summary = {
                "authority_class": "AUTHORITATIVE",
                "source_type": "risk_engine",
                "source_identity": {"paper_account_id": str(runtime_campaign.paper_account_id), "asset_id": str(asset.id)},
                "observed_at": risk_context.evaluation_time.isoformat(),
                "freshness": "fresh",
                "availability": "available",
                "reason": risk_result.reason_code or risk_result.action.value,
                "verdict": "ALLOW" if risk_result.action == RiskDecisionAction.APPROVE else ("REDUCE" if risk_result.action == RiskDecisionAction.RESIZE else "VETO"),
                "approved_quantity": format(risk_result.approved_quantity, "f"),
                "risk_event_id": None,
                "policy_identity": risk_context.risk_policy_source,
                "policy_version": None,
                "evaluated_at": risk_context.evaluation_time.isoformat(),
            }
            persist_result = await persist_risk_decision(
                db=db,
                request=RiskDecisionPersistenceRequest(
                    paper_account_id=runtime_campaign.paper_account_id,
                    signal_id=None,
                    actor="campaign_orchestration",
                    evaluation_result=risk_result,
                ),
            )
            risk_summary["risk_event_id"] = str(persist_result.risk_event_id)
            if risk_result.action == RiskDecisionAction.REJECT:
                risk_summary["reason"] = risk_result.reason_code or "risk_rejected"
        except Exception as exc:
            risk_summary = {
                "authority_class": "UNAVAILABLE",
                "source_type": "risk_engine",
                "source_identity": {"paper_account_id": str(runtime_campaign.paper_account_id), "asset_id": str(asset.id)},
                "observed_at": now.isoformat(),
                "freshness": "unavailable",
                "availability": "unavailable",
                "reason": f"risk_unavailable:{exc.__class__.__name__}",
                "verdict": "VETO",
                "approved_quantity": "0",
                "risk_event_id": None,
                "policy_identity": risk_context.risk_policy_source,
                "policy_version": None,
                "evaluated_at": now.isoformat(),
            }
            rejected_candidates.append({"instrument": instrument, "reason": "risk_unavailable", "market": market, "strategy": strategy, "position": position, "risk": risk_summary})
            risk_outputs[instrument] = risk_summary
            continue
        risk_outputs[instrument] = risk_summary

        expected_gross_edge = strategy.get("profitable_after_fees_performance")
        if expected_gross_edge is None and strategy.get("expected_value") is not None:
            expected_gross_edge = strategy.get("expected_value")
        expected_gross_edge_decimal = Decimal(str(expected_gross_edge or "0"))
        expected_fees = Decimal(str(candle_item.close)) * Decimal("0.0001")
        expected_slippage = Decimal(str(candle_item.close)) * Decimal("0.0001")
        expected_net_edge = expected_gross_edge_decimal - expected_fees - expected_slippage
        expected_net_dollars = expected_net_edge * Decimal(str(candle_item.close)) / Decimal("100")
        if position["position"] is not None and position["position"].get("profitability") is not None:
            expected_net_dollars = Decimal(str(position["position"]["expected_net_pnl_if_sold_now"] or "0"))
            current_market_value = Decimal(str(position["position"]["current_market_value"] or "0"))
            if current_market_value > 0:
                expected_net_edge = (expected_net_dollars / current_market_value) * Decimal("100")

        if risk_summary["verdict"] == "VETO":
            rejected_candidates.append({"instrument": instrument, "reason": risk_summary["reason"], "market": market, "strategy": strategy, "position": position, "risk": risk_summary})
            continue

        candidate_kind = "OPEN_POSITION_PROPOSED"
        if position["position"] is not None and position["position"].get("closed_indicator") is False and position["position"].get("quantity") not in {None, "0", "0.0"}:
            candidate_kind = "CLOSE_POSITION_PROPOSED" if expected_net_dollars > Decimal("0") else "HOLD_POSITION"
        elif expected_net_dollars <= Decimal("0"):
            rejected_candidates.append({"instrument": instrument, "reason": "non_positive_net_edge", "market": market, "strategy": strategy, "position": position, "risk": risk_summary})
            continue

        candidate_rows.append(
            {
                "instrument": instrument,
                "decision_kind": candidate_kind,
                "expected_net_dollars": format(expected_net_dollars, "f"),
                "expected_net_edge_pct": format(expected_net_edge, "f"),
                "risk_adjusted_score": format(expected_net_dollars * (Decimal(str(strategy.get("confidence") or "1")) if strategy.get("confidence") is not None else Decimal("1")), "f"),
                "confidence": strategy.get("confidence"),
                "sample_size": strategy.get("sample_size"),
                "expected_fees": format(expected_fees, "f"),
                "expected_slippage": format(expected_slippage, "f"),
                "proposed_allocation": format(
                    min(
                        campaign_definition.remaining_unallocated_capital,
                        campaign_definition.maximum_position_size,
                        campaign_definition.maximum_total_exposure,
                    ),
                    "f",
                ),
                "maximum_risk_approved_allocation": risk_summary.get("approved_quantity"),
                "campaign_constraint_result": "pass",
                "rank": None,
                "rejection_reasons": [],
                "market_evidence": market,
                "strategy_evidence": strategy,
                "position_evidence": position,
                "risk_evidence": risk_summary,
            }
        )

    candidate_rows.sort(key=lambda item: (Decimal(str(item["expected_net_dollars"])), Decimal(str(item["risk_adjusted_score"])), item["instrument"]), reverse=True)
    for index, item in enumerate(candidate_rows, start=1):
        item["rank"] = index

    selected = candidate_rows[0] if candidate_rows else None
    critical_rejections = {"risk_unavailable", "strategy_evidence_unavailable", "market_data_unavailable", "stale_market_data", "asset_mapping_unavailable", "provider_product_unsupported", "ambiguous_market_source"}
    failed_closed = bool(rejected_candidates) and not candidate_rows and any(item.get("reason") in critical_rejections for item in rejected_candidates)
    if selected is None:
        if failed_closed:
            first_reason = next((item.get("reason") for item in rejected_candidates if item.get("reason") in critical_rejections), "no_qualifying_candidate")
            selected_decision = {"decision_kind": "MANUAL_REVIEW_REQUIRED", "reason": first_reason}
        else:
            selected_decision = {"decision_kind": "NO_ACTION", "reason": "no_qualifying_candidate"}
    else:
        selected_decision = {
            "decision_kind": selected["decision_kind"],
            "instrument": selected["instrument"],
            "why_this_asset": f"best risk-adjusted net economics among authoritative candidates: {selected['expected_net_dollars']}",
            "why_not_other_assets": [item["instrument"] for item in candidate_rows[1:]],
            "why_not_cash": "selected candidate exceeds cash baseline" if Decimal(str(selected["expected_net_dollars"])) > Decimal("0") else "cash baseline preferred",
            "costs_included": {
                "expected_fees": selected.get("expected_fees"),
                "expected_slippage": selected.get("expected_slippage"),
            },
            "risk_verdict": selected["risk_evidence"]["verdict"],
            "evidence_freshness": selected["market_evidence"]["freshness"],
            "missing_evidence": [item["reason"] for item in rejected_candidates],
            "campaign_constraints": {
                    "maximum_open_positions": getattr(campaign_definition, "maximum_open_positions", len(candidate_rows)),
                "maximum_position_size": format(campaign_definition.maximum_position_size, "f"),
                "maximum_total_exposure": format(campaign_definition.maximum_total_exposure, "f"),
                "remaining_unallocated_capital": format(campaign_definition.remaining_unallocated_capital, "f"),
            },
        }

    composition = {
        "campaign_id": str(campaign_definition.campaign_id),
        "campaign_version": campaign_definition.version,
        "execution_mode": "preview",
        "execution_submitted": False,
        "provider_order_id": None,
        "failed_closed": failed_closed,
        "termination_stage": "failed_closed" if failed_closed else ("preview_generated" if selected is not None else "hold_terminal"),
        "proposed_action": "FAILED_CLOSED" if failed_closed else (selected["decision_kind"] if selected is not None else "NO_ACTION"),
        "failure_reason": None if selected is not None and not failed_closed else (selected_decision.get("reason") if selected is None else None),
        "selected_decision": selected_decision,
        "eligible_candidates": candidate_rows,
        "rejected_candidates": rejected_candidates,
        "ranked_candidates": candidate_rows,
        "risk_outputs": risk_outputs,
        "authoritative_evidence": {
            "market": market_evidence,
            "strategy": strategy_evidence,
            "position": position_evidence,
            "risk": risk_outputs,
            "authority_class": "AUTHORITATIVE",
        },
        "deterministic_explanation": [
            f"trigger={trigger}",
            f"campaign_version={campaign_definition.version}",
            f"candidates={len(candidate_rows)}",
            f"rejected={len(rejected_candidates)}",
        ],
        "decision_evidence": selected_decision,
        "candidate_instruments": allowed_instruments,
    }
    preview = build_campaign_preview(
        campaign=campaign_definition,
        request=CapitalCampaignPreviewRequest(
            candidate_instruments=allowed_instruments,
            strategy_evidence=[
                StrategyEvidenceInput(
                    instrument=item["instrument"],
                    authority_class="AUTHORITATIVE",
                    confidence=Decimal(str(item["confidence"] or "0")) if item.get("confidence") is not None else Decimal("0"),
                    expected_gross_edge=Decimal(str(item["strategy_evidence"].get("profitable_after_fees_performance") or "0")),
                    expected_fees=Decimal("0"),
                    expected_slippage=Decimal("0"),
                )
                for item in candidate_rows
            ],
            lifecycle_snapshots=[
                LifecycleEvidenceInput(
                    instrument=item["instrument"],
                    authority_class="AUTHORITATIVE",
                    lifecycle_state=item["position_evidence"]["lifecycle"]["lifecycle_state"] if item["position_evidence"].get("lifecycle") else "OPEN",
                    recommendation=item["position_evidence"]["lifecycle"]["recommendation"] if item["position_evidence"].get("lifecycle") else "HOLD_FOR_PROFIT",
                    market_data_stale=item["position_evidence"]["lifecycle"]["market_data_stale"] if item["position_evidence"].get("lifecycle") else False,
                    dust_indicator=item["position_evidence"]["position"]["dust_indicator"] if item["position_evidence"].get("position") else False,
                    closed_indicator=item["position_evidence"]["position"]["closed_indicator"] if item["position_evidence"].get("position") else False,
                    expected_net_realized_pnl_if_sold_now=None,
                )
                for item in candidate_rows
            ],
            risk_preview=[
                RiskPreviewInput(
                    instrument=item["instrument"],
                    authority_class="AUTHORITATIVE",
                    verdict=item["risk_evidence"]["verdict"],
                    reason=item["risk_evidence"]["reason"],
                    max_allocation=Decimal(str(item["maximum_risk_approved_allocation"] or "0")),
                )
                for item in candidate_rows
            ],
        ),
        now=now,
    )
    composition["preview"] = preview.model_dump(mode="json")
    return CampaignAuthoritativeCycleResult(composition=composition, preview=preview)
