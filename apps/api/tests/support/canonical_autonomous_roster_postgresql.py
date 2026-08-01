from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.autonomous_cycle_run import AutonomousCycleRun
from app.models.candle import Candle
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_definition import CapitalCampaignDefinition
from app.models.paper_account import PaperAccount
from app.models.parameter_set import ParameterSet
from app.models.strategy import Strategy
from app.models.strategy_aggregate_decision import StrategyAggregateDecision
from app.models.strategy_roster_proposal import StrategyRosterProposal
from app.models.strategy_roster_proposal_outcome import StrategyRosterProposalOutcome
from app.services.capital_campaign_orchestration.authoritative import resolve_and_persist_strategy_aggregate_evidence
from app.services.strategies.registry import strategy_registry
from app.services.strategy_roster.contracts import StrategyRosterRequest
from app.services.strategy_roster.registry import ENABLED_PHASE1_ROSTER
from app.services.strategy_roster.service import run_strategy_roster_for_candle


@dataclass(frozen=True, slots=True)
class CanonicalRosterFixture:
    campaign_id: uuid.UUID
    runtime_campaign_id: int
    account_id: uuid.UUID
    asset_id: uuid.UUID
    candle_id: uuid.UUID
    candle_close_time: datetime
    scheduled_cycle_id: uuid.UUID
    roster_run_id: uuid.UUID
    aggregate_decision_id: uuid.UUID
    final_action: str
    provider: str
    product_id: str
    interval: str
    trigger: str
    candle_open_time: datetime


async def build_canonical_buy_roster(
    *,
    db: AsyncSession,
    observed_at: datetime | None = None,
    commit_aggregate: bool = True,
    scheduled_cycle_builder: Callable[..., Awaitable[uuid.UUID]] | None = None,
    seed_positive_buy_scorecard: bool = False,
) -> CanonicalRosterFixture:
    """Persist real roster proposals and their canonical aggregate decision."""
    now = observed_at or datetime.now(timezone.utc)
    close_time = (now - timedelta(minutes=1)).replace(second=0, microsecond=0)
    campaign_id = uuid.uuid4()
    account = PaperAccount(
        owner_user_id=uuid.uuid4(), name="canonical-autonomous-roster", asset_class="crypto",
        starting_balance=Decimal("25"), current_cash_balance=Decimal("25"), is_active=True,
    )
    db.add(account); await db.flush()
    definition = CapitalCampaignDefinition(
        campaign_id=campaign_id, version=1, name="canonical-autonomous-roster",
        owner_identity="test:ordinary-autonomous", status="READY", capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"), base_currency="USD",
        allowed_asset_classes=["crypto"], allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD"], campaign_modes=[], maximum_open_positions=1,
        maximum_position_size=Decimal("5"), minimum_position_size=Decimal("5"),
        maximum_total_exposure=Decimal("5"), profitability_policy_id="test-profit",
        profitability_policy_version="1", risk_policy_id="default", risk_policy_version="1",
        compounding_policy={"policy_type": "FIXED_CAPITAL", "reserve_percentage": "100"},
        profit_distribution_policy={"reserve_percentage": "100"},
    )
    db.add(definition); await db.flush()
    runtime = CapitalCampaign(
        uuid=campaign_id, owner="test:ordinary-autonomous", name="canonical-autonomous-roster",
        status="READY", campaign_type="definition_pinned_runtime",
        definition_campaign_id=campaign_id, definition_version=1, paper_account_id=account.id,
        starting_capital=Decimal("25"), current_equity=Decimal("25"),
    )
    db.add(runtime)
    asset = Asset(
        symbol="BTC", asset_class="crypto", exchange="kraken_spot", base_currency="USD",
        is_active=True, min_order_notional=Decimal("1"), qty_step_size=Decimal("0.00000001"),
        supports_fractional=True,
    )
    db.add(asset); await db.flush()
    for slug in ENABLED_PHASE1_ROSTER:
        implementation = strategy_registry.get(slug)
        params = dict(implementation.default_params)
        if slug == "ma_crossover":
            params.update(fast_period=2, slow_period=20)
        elif slug in {"bollinger_reversion", "mean_reversion"}:
            params.update(window=79)
        strategy = Strategy(
            name=f"Canonical {slug}", slug=slug, description="deterministic PostgreSQL roster fixture",
            module_version="1.0.0", is_active=True,
        )
        db.add(strategy); await db.flush()
        db.add(ParameterSet(
            strategy_id=strategy.id, label="canonical-defaults",
            params=params, created_by="test:ordinary-autonomous",
        ))
    candles: list[Candle] = []
    start = close_time - timedelta(minutes=15 * 80)
    for index in range(80):
        opened = start + timedelta(minutes=15 * index)
        # The old plateau keeps the 79-candle reversion mean above the target;
        # the recent plateau followed by one confirmed breakout makes the
        # crossover, momentum, breakout, and Donchian rules independently BUY.
        price = Decimal("100000") if index < 59 else Decimal("40000")
        if index == 79:
            price = Decimal("50000")
        candle = Candle(
            asset_id=asset.id, interval="15m", open_time=opened,
            close_time=opened + timedelta(minutes=15), open=price - Decimal("50"),
            high=price + Decimal("100"), low=price - Decimal("100"), close=price,
            volume=Decimal("100") if index == 79 else Decimal("10"), source="kraken_spot",
        )
        db.add(candle); candles.append(candle)
    await db.flush()
    target = candles[-1]
    if scheduled_cycle_builder is None:
        scheduled = AutonomousCycleRun(
            idempotency_key=f"canonical-roster-cycle:{target.close_time.isoformat()}",
            mandate_id=None, mandate_version_id=None, cycle_kind="autonomous", state="COMPLETE",
            evaluation_stage="strategy_roster_fixture", deterministic_explanation=["ordinary autonomous test input"],
            cycle_context={"provider": "kraken_spot", "product": "BTC-USD", "interval": "15m"},
            diagnostics={"test_fixture": True}, proposed_action="BUY", mandate_verdict="NOT_APPLICABLE",
            risk_verdict="NOT_APPLICABLE", audit_correlation_id=uuid.uuid4(), started_at=now, completed_at=now,
        )
        db.add(scheduled); await db.flush()
        scheduled_cycle_id = scheduled.cycle_id
    else:
        scheduled_cycle_id = await scheduled_cycle_builder(
            db=db, now=now, account=account, campaign_definition=definition,
            runtime_campaign=runtime, asset=asset, candle=target,
        )
    request = StrategyRosterRequest(
        asset_id=asset.id, provider="kraken_spot", product_id="BTC-USD", interval="15m",
        candle_open_time=target.open_time, candle_close_time=target.close_time,
        trigger="kraken_btc_15m_candle_close", scheduled_cycle_id=scheduled_cycle_id,
    )
    roster = await run_strategy_roster_for_candle(db=db, request=request)
    if seed_positive_buy_scorecard:
        buy_proposal = await db.scalar(
            select(StrategyRosterProposal)
            .where(StrategyRosterProposal.roster_run_id == roster.roster_run_id)
            .where(StrategyRosterProposal.action == "BUY")
            .order_by(StrategyRosterProposal.strategy_slug)
            .limit(1)
        )
        if buy_proposal is None:
            raise AssertionError("canonical roster produced no BUY proposal for scorecard evidence")
        db.add(StrategyRosterProposalOutcome(
            idempotency_key=f"canonical-positive-buy:{buy_proposal.proposal_id}",
            proposal_id=buy_proposal.proposal_id, roster_run_id=roster.roster_run_id,
            asset_id=asset.id, provider="kraken_spot", product_id="BTC-USD", interval="15m",
            strategy_slug=buy_proposal.strategy_slug, strategy_identity=buy_proposal.strategy_identity,
            action="BUY", proposal_evaluation_status="EVALUATED", horizon_label="15m", horizon_minutes=15,
            proposal_candle_close_time=target.close_time - timedelta(minutes=15),
            horizon_time=target.close_time, evaluated_at=now,
            entry_price=Decimal("48000"), exit_price=Decimal("50000"),
            market_return_pct=Decimal("4.16666667"), buy_raw_return_pct=Decimal("4.16666667"),
            buy_fee_adjusted_return_pct=Decimal("3.96666667"), sell_raw_return_pct=Decimal("-4.16666667"),
            sell_fee_adjusted_return_pct=Decimal("-4.36666667"), actual_raw_return_pct=Decimal("4.16666667"),
            actual_fee_adjusted_return_pct=Decimal("3.96666667"), mfe_pct=Decimal("4.2"), mae_pct=Decimal("-0.1"),
            actual_action_correct=True, evaluation_completed=True, evaluation_state="RESOLVED",
            evaluation_reason=None, market_move="UP", regime_trend="TRENDING",
            regime_volatility="HIGH_VOLATILITY", regime_range="EXPANSION", fee_bps=Decimal("10"),
            hold_buy_threshold_pct=Decimal("0"), hold_sell_threshold_pct=Decimal("0"),
            execution_mode="SHADOW", live_submission_allowed=False,
        ))
        await db.flush()
    evidence, blocker = await resolve_and_persist_strategy_aggregate_evidence(
        db=db, asset_id=asset.id, product_id="BTC-USD", interval="15m",
        campaign_id=campaign_id, campaign_version=1, environment="production",
        paper_account_id=account.id, runtime_campaign_id=runtime.id, asset=asset,
        candle_item=target, now=now, required_trigger=request.trigger,
        scheduled_cycle_id=scheduled_cycle_id,
    )
    if evidence is None:
        raise AssertionError(f"canonical aggregate unavailable: {blocker}")
    aggregate = await db.scalar(select(StrategyAggregateDecision).where(
        StrategyAggregateDecision.roster_run_id == roster.roster_run_id,
    ).limit(1))
    if aggregate is None or aggregate.final_action != "BUY":
        raise AssertionError(f"canonical aggregate was not BUY: {None if aggregate is None else aggregate.final_action}")
    if commit_aggregate:
        await db.commit()
    return CanonicalRosterFixture(
        campaign_id, runtime.id, account.id, asset.id, target.id, target.close_time,
        scheduled_cycle_id, roster.roster_run_id, aggregate.aggregate_decision_id, aggregate.final_action,
        "kraken_spot", "BTC-USD", "15m", request.trigger, target.open_time,
    )
