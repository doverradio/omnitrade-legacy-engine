from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.core.errors import InvalidRequestError
from app.schemas.capital_campaign_domain import (
    CampaignAccountingState,
    CampaignCompoundingPolicy,
    CampaignProfitDistributionPolicy,
    CapitalCampaignDefinitionResponse,
    CapitalCampaignPreviewRequest,
    LifecycleEvidenceInput,
    RiskPreviewInput,
    StrategyEvidenceInput,
)
from app.services.capital_campaign_domain.preview_engine import build_campaign_preview


def _campaign(**overrides) -> CapitalCampaignDefinitionResponse:
    base = CapitalCampaignDefinitionResponse(
        campaign_id=uuid4(),
        version=1,
        runtime_campaign_uuid=uuid4(),
        runtime_definition_version=1,
        name="Maximum Governed Crypto Growth",
        description="Preview-only domain campaign",
        owner_identity="operator",
        status="READY",
        capital_budget=Decimal("25"),
        remaining_unallocated_capital=Decimal("25"),
        base_currency="USD",
        allowed_asset_classes=["crypto"],
        allowed_venues=["kraken_spot"],
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        campaign_modes=["OPPORTUNITY_SEEKING"],
        maximum_open_positions=2,
        maximum_position_size=Decimal("10"),
        minimum_position_size=Decimal("2"),
        maximum_total_exposure=Decimal("20"),
        profitability_policy_id="pfp-1.1",
        profitability_policy_version="1.0.0",
        risk_policy_id="risk-v1",
        risk_policy_version="1.0.0",
        compounding_policy=CampaignCompoundingPolicy(
            policy_type="REINVEST_PERCENTAGE",
            reinvestment_percentage=Decimal("50"),
            profit_distribution_percentage=Decimal("30"),
            reserve_percentage=Decimal("20"),
            cumulative_profit_target=Decimal("20"),
            maximum_campaign_loss=Decimal("5"),
            campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        ),
        profit_distribution_policy=CampaignProfitDistributionPolicy(
            reinvestment_percentage=Decimal("50"),
            profit_distribution_percentage=Decimal("30"),
            reserve_percentage=Decimal("20"),
        ),
        aggression_mode="BALANCED",
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("5"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("0"),
            fees=Decimal("0"),
            realized_net_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("25"),
            maximum_drawdown=Decimal("0"),
            available_capital=Decimal("25"),
        ),
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        activated_at=None,
        paused_at=None,
        completed_at=None,
        metadata_evidence={},
    )
    return CapitalCampaignDefinitionResponse(**{**base.model_dump(), **overrides})


def _request(
    *,
    instruments: list[str],
    confidences: dict[str, str] | None = None,
    gross_edges: dict[str, str] | None = None,
    fees: dict[str, str] | None = None,
    slippage: dict[str, str] | None = None,
    risk_veto: set[str] | None = None,
) -> CapitalCampaignPreviewRequest:
    confidences = confidences or {}
    gross_edges = gross_edges or {}
    fees = fees or {}
    slippage = slippage or {}
    risk_veto = risk_veto or set()

    strategy = []
    lifecycle = []
    risk = []

    for symbol in instruments:
        strategy.append(
            StrategyEvidenceInput(
                instrument=symbol,
                authority_class="SIMULATED",
                confidence=Decimal(confidences.get(symbol, "0.80")),
                expected_gross_edge=Decimal(gross_edges.get(symbol, "1.50")),
                expected_fees=Decimal(fees.get(symbol, "0.20")),
                expected_slippage=Decimal(slippage.get(symbol, "0.10")),
            )
        )
        lifecycle.append(
            LifecycleEvidenceInput(
                instrument=symbol,
                authority_class="SIMULATED",
                lifecycle_state="OPEN",
                recommendation="HOLD_FOR_PROFIT",
                market_data_stale=False,
                dust_indicator=False,
                closed_indicator=False,
                expected_net_realized_pnl_if_sold_now=Decimal("0.5"),
            )
        )
        risk.append(
            RiskPreviewInput(
                instrument=symbol,
                authority_class="OPERATOR_SUPPLIED",
                verdict="VETO" if symbol in risk_veto else "ALLOW",
                reason="policy_veto" if symbol in risk_veto else None,
                max_allocation=Decimal("10"),
            )
        )

    return CapitalCampaignPreviewRequest(
        candidate_instruments=instruments,
        strategy_evidence=strategy,
        lifecycle_snapshots=lifecycle,
        risk_preview=risk,
    )


def test_invalid_percentages_fail_closed() -> None:
    campaign = _campaign(
        compounding_policy=CampaignCompoundingPolicy(
            policy_type="REINVEST_PERCENTAGE",
            reinvestment_percentage=Decimal("70"),
            profit_distribution_percentage=Decimal("20"),
            reserve_percentage=Decimal("5"),
            cumulative_profit_target=Decimal("10"),
            maximum_campaign_loss=Decimal("5"),
            campaign_end_date=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    )
    with pytest.raises(InvalidRequestError):
        build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))


def test_capital_limits_and_remaining_cash_applied() -> None:
    response = build_campaign_preview(campaign=_campaign(), request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert response.no_action is False
    assert response.proposed_opportunities[0].proposed_allocation <= Decimal("10")
    assert response.remaining_cash >= Decimal("0")


def test_maximum_position_constraints_enforced() -> None:
    campaign = _campaign(maximum_open_positions=1)
    response = build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD", "ETH-USD"]), now=datetime.now(timezone.utc))
    assert len(response.proposed_opportunities) == 1


def test_no_qualifying_opportunity_returns_no_action() -> None:
    req = _request(instruments=["BTC-USD"], gross_edges={"BTC-USD": "0.10"}, fees={"BTC-USD": "0.10"}, slippage={"BTC-USD": "0.10"})
    response = build_campaign_preview(campaign=_campaign(), request=req, now=datetime.now(timezone.utc))
    assert response.no_action is True


def test_one_qualifying_opportunity_selected() -> None:
    response = build_campaign_preview(campaign=_campaign(), request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert len(response.proposed_opportunities) == 1
    assert response.proposed_opportunities[0].instrument == "BTC-USD"


def test_multiple_opportunities_ranked_by_edge() -> None:
    req = _request(
        instruments=["BTC-USD", "ETH-USD"],
        gross_edges={"BTC-USD": "2.0", "ETH-USD": "1.2"},
        fees={"BTC-USD": "0.2", "ETH-USD": "0.2"},
        slippage={"BTC-USD": "0.1", "ETH-USD": "0.1"},
    )
    response = build_campaign_preview(campaign=_campaign(maximum_open_positions=2), request=req, now=datetime.now(timezone.utc))
    assert [item.instrument for item in response.proposed_opportunities] == ["BTC-USD", "ETH-USD"]


def test_allocation_cannot_exceed_capital() -> None:
    campaign = _campaign(
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("0"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("0"),
            fees=Decimal("0"),
            realized_net_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("25"),
            maximum_drawdown=Decimal("0"),
            available_capital=Decimal("4"),
        )
    )
    response = build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert response.proposed_opportunities[0].proposed_allocation == Decimal("4")


def test_reserve_preservation_reduces_allocatable_cash() -> None:
    campaign = _campaign(
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("10"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("0"),
            fees=Decimal("0"),
            realized_net_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("25"),
            maximum_drawdown=Decimal("0"),
            available_capital=Decimal("25"),
        )
    )
    response = build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert response.proposed_opportunities[0].proposed_allocation <= Decimal("10")


def test_compounding_calculation_exposed() -> None:
    campaign = _campaign(
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("5"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("10"),
            fees=Decimal("1"),
            realized_net_pnl=Decimal("9"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("34"),
            maximum_drawdown=Decimal("0"),
            available_capital=Decimal("25"),
        )
    )
    response = build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert Decimal(response.decision_evidence["projected_compounded_profit"]) > Decimal("0")


def test_profit_target_completion_gates_preview() -> None:
    campaign = _campaign(
        campaign_modes=["PROFIT_TARGET"],
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("5"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("25"),
            fees=Decimal("2"),
            realized_net_pnl=Decimal("20"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("45"),
            maximum_drawdown=Decimal("0"),
            available_capital=Decimal("25"),
        ),
    )
    response = build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert response.no_action is True
    assert response.no_action_reason == "campaign_profit_target_reached"


def test_maximum_loss_pause_gates_preview() -> None:
    campaign = _campaign(
        accounting_state=CampaignAccountingState(
            initial_capital=Decimal("25"),
            allocated_capital=Decimal("0"),
            reserved_capital=Decimal("5"),
            deployed_capital=Decimal("0"),
            realized_gross_pnl=Decimal("-6"),
            fees=Decimal("0"),
            realized_net_pnl=Decimal("-6"),
            unrealized_pnl=Decimal("0"),
            distributable_profit=Decimal("0"),
            compounded_profit=Decimal("0"),
            withdrawn_profit=Decimal("0"),
            current_campaign_equity=Decimal("19"),
            maximum_drawdown=Decimal("6"),
            available_capital=Decimal("19"),
        ),
    )
    response = build_campaign_preview(campaign=campaign, request=_request(instruments=["BTC-USD"]), now=datetime.now(timezone.utc))
    assert response.no_action is True
    assert response.no_action_reason == "campaign_maximum_loss_reached"


def test_conservative_mode_uses_stricter_thresholds() -> None:
    campaign = _campaign(aggression_mode="CONSERVATIVE")
    req = _request(instruments=["BTC-USD"], confidences={"BTC-USD": "0.60"})
    response = build_campaign_preview(campaign=campaign, request=req, now=datetime.now(timezone.utc))
    assert response.no_action is True


def test_aggressive_mode_still_rejects_non_positive_edge() -> None:
    campaign = _campaign(aggression_mode="AGGRESSIVE")
    req = _request(instruments=["BTC-USD"], gross_edges={"BTC-USD": "0.05"}, fees={"BTC-USD": "0.05"}, slippage={"BTC-USD": "0.01"})
    response = build_campaign_preview(campaign=campaign, request=req, now=datetime.now(timezone.utc))
    assert response.no_action is True


def test_btc_eth_sol_eligibility_supported() -> None:
    response = build_campaign_preview(campaign=_campaign(), request=_request(instruments=["BTC-USD", "ETH-USD", "SOL-USD"]), now=datetime.now(timezone.utc))
    assert set(item.instrument for item in response.proposed_opportunities).issubset({"BTC-USD", "ETH-USD", "SOL-USD"})


def test_unsupported_instrument_rejected() -> None:
    response = build_campaign_preview(campaign=_campaign(), request=_request(instruments=["DOGE-USD"]), now=datetime.now(timezone.utc))
    assert response.no_action is True
    assert any(item.reason == "instrument_not_allowed_by_campaign" for item in response.rejected_opportunities)


def test_deterministic_repeat_preview() -> None:
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    campaign = _campaign()
    req = _request(instruments=["BTC-USD", "ETH-USD"])
    first = build_campaign_preview(campaign=campaign, request=req, now=now)
    second = build_campaign_preview(campaign=campaign, request=req, now=now)
    assert first == second


def test_risk_veto_rejection() -> None:
    req = _request(instruments=["BTC-USD"], risk_veto={"BTC-USD"})
    response = build_campaign_preview(campaign=_campaign(), request=req, now=datetime.now(timezone.utc))
    assert response.no_action is True
    assert any(item.reason.startswith("risk_veto:") for item in response.rejected_opportunities)


def test_rejection_reasons_are_explainable() -> None:
    req = _request(instruments=["BTC-USD"], confidences={"BTC-USD": "0.40"})
    response = build_campaign_preview(campaign=_campaign(), request=req, now=datetime.now(timezone.utc))
    assert response.rejected_opportunities
    assert response.rejected_opportunities[0].reason
