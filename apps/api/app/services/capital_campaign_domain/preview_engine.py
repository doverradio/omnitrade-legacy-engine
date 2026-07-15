from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING

from app.core.errors import InvalidRequestError
from app.schemas.capital_campaign_domain import (
    CampaignCompoundingPolicy,
    CampaignMode,
    CampaignProfitDistributionPolicy,
    CampaignPreviewOpportunity,
    CampaignPreviewRejection,
    CapitalCampaignDefinitionResponse,
    CapitalCampaignPreviewRequest,
    CapitalCampaignPreviewResponse,
)


@dataclass(frozen=True)
class _AggressionConfig:
    minimum_confidence: Decimal
    max_position_multiplier: Decimal
    reserve_multiplier: Decimal
    scan_frequency: str


_AGGRESSION_CONFIGS: dict[str, _AggressionConfig] = {
    "CONSERVATIVE": _AggressionConfig(
        minimum_confidence=Decimal("0.70"),
        max_position_multiplier=Decimal("0.60"),
        reserve_multiplier=Decimal("1.30"),
        scan_frequency="30m",
    ),
    "BALANCED": _AggressionConfig(
        minimum_confidence=Decimal("0.60"),
        max_position_multiplier=Decimal("0.80"),
        reserve_multiplier=Decimal("1.00"),
        scan_frequency="15m",
    ),
    "AGGRESSIVE": _AggressionConfig(
        minimum_confidence=Decimal("0.50"),
        max_position_multiplier=Decimal("1.00"),
        reserve_multiplier=Decimal("0.80"),
        scan_frequency="5m",
    ),
    "MAXIMUM_GOVERNED": _AggressionConfig(
        minimum_confidence=Decimal("0.45"),
        max_position_multiplier=Decimal("1.00"),
        reserve_multiplier=Decimal("0.70"),
        scan_frequency="1m",
    ),
}


def _normalize_instrument(value: str) -> str:
    return value.strip().upper().replace("/", "-")


def _validate_percentages(*, compounding_policy: CampaignCompoundingPolicy, distribution_policy: CampaignProfitDistributionPolicy) -> None:
    values = [
        compounding_policy.reinvestment_percentage,
        compounding_policy.profit_distribution_percentage,
        compounding_policy.reserve_percentage,
        distribution_policy.reinvestment_percentage,
        distribution_policy.profit_distribution_percentage,
        distribution_policy.reserve_percentage,
    ]
    for value in values:
        if value < Decimal("0") or value > Decimal("100"):
            raise InvalidRequestError(message="Policy percentage outside 0-100", details={"value": format(value, "f")})

    if (
        compounding_policy.reinvestment_percentage
        + compounding_policy.profit_distribution_percentage
        + compounding_policy.reserve_percentage
        != Decimal("100")
    ):
        raise InvalidRequestError(message="Compounding policy percentages must sum to 100", details={})

    if (
        distribution_policy.reinvestment_percentage
        + distribution_policy.profit_distribution_percentage
        + distribution_policy.reserve_percentage
        != Decimal("100")
    ):
        raise InvalidRequestError(message="Distribution policy percentages must sum to 100", details={})


def _validate_mode_policy_compatibility(*, modes: list[CampaignMode], compounding_policy: CampaignCompoundingPolicy) -> None:
    mode_set = set(modes)
    if "TIME_BOUND" in mode_set and compounding_policy.campaign_end_date is None:
        raise InvalidRequestError(message="TIME_BOUND campaign requires campaign_end_date", details={})

    if "PROFIT_TARGET" in mode_set and compounding_policy.cumulative_profit_target is None:
        raise InvalidRequestError(message="PROFIT_TARGET campaign requires cumulative_profit_target", details={})

    if "COMPOUND" in mode_set and compounding_policy.policy_type == "FIXED_CAPITAL":
        raise InvalidRequestError(message="COMPOUND campaign is incompatible with FIXED_CAPITAL policy", details={})


def _policy_edge_floor(campaign: CapitalCampaignDefinitionResponse) -> Decimal:
    base = Decimal("0")
    for mode in campaign.campaign_modes:
        if mode == "CAPITAL_PRESERVATION":
            base = max(base, Decimal("0.20"))
    return base


def build_campaign_preview(
    *,
    campaign: CapitalCampaignDefinitionResponse,
    request: CapitalCampaignPreviewRequest,
    now: datetime,
) -> CapitalCampaignPreviewResponse:
    _validate_percentages(
        compounding_policy=campaign.compounding_policy,
        distribution_policy=campaign.profit_distribution_policy,
    )
    _validate_mode_policy_compatibility(
        modes=campaign.campaign_modes,
        compounding_policy=campaign.compounding_policy,
    )

    aggression_config = _AGGRESSION_CONFIGS[campaign.aggression_mode]

    allowed_instruments = {_normalize_instrument(item) for item in campaign.allowed_instruments}
    requested_candidates = (
        {_normalize_instrument(item) for item in request.candidate_instruments}
        if request.candidate_instruments
        else allowed_instruments
    )

    eligible_candidates = sorted(requested_candidates.intersection(allowed_instruments))
    rejected: list[CampaignPreviewRejection] = []

    for candidate in sorted(requested_candidates - allowed_instruments):
        rejected.append(
            CampaignPreviewRejection(
                instrument=candidate,
                reason="instrument_not_allowed_by_campaign",
            )
        )

    strategy_by_instrument = {_normalize_instrument(item.instrument): item for item in request.strategy_evidence}
    lifecycle_by_instrument = {_normalize_instrument(item.instrument): item for item in request.lifecycle_snapshots}
    risk_by_instrument = {_normalize_instrument(item.instrument): item for item in request.risk_preview}
    strategy_authority_by_instrument = {
        _normalize_instrument(item.instrument): item.authority_class for item in request.strategy_evidence
    }
    lifecycle_authority_by_instrument = {
        _normalize_instrument(item.instrument): item.authority_class for item in request.lifecycle_snapshots
    }
    risk_authority_by_instrument = {
        _normalize_instrument(item.instrument): item.authority_class for item in request.risk_preview
    }

    available_capital = request.available_capital_override if request.available_capital_override is not None else campaign.accounting_state.available_capital
    available_capital = min(available_capital, campaign.remaining_unallocated_capital)

    projected_compounded_profit = Decimal("0")
    projected_distributable_profit = Decimal("0")
    if campaign.accounting_state.realized_net_pnl > Decimal("0"):
        projected_compounded_profit = (
            campaign.accounting_state.realized_net_pnl * campaign.compounding_policy.reinvestment_percentage
        ) / Decimal("100")
        projected_distributable_profit = (
            campaign.accounting_state.realized_net_pnl * campaign.compounding_policy.profit_distribution_percentage
        ) / Decimal("100")

    if (
        "PROFIT_TARGET" in set(campaign.campaign_modes)
        and campaign.compounding_policy.cumulative_profit_target is not None
        and campaign.accounting_state.realized_net_pnl >= campaign.compounding_policy.cumulative_profit_target
    ):
        return CapitalCampaignPreviewResponse(
            campaign_id=campaign.campaign_id,
            campaign_version=campaign.version,
            aggression_mode=campaign.aggression_mode,
            no_action=True,
            no_action_reason="campaign_profit_target_reached",
            proposed_opportunities=[],
            rejected_opportunities=rejected,
            remaining_cash=available_capital,
            expected_fees=Decimal("0"),
            expected_slippage=Decimal("0"),
            expected_net_edge=Decimal("0"),
            campaign_policy_checks=["profit_target_gate_enforced"],
            risk_checks=[],
            decision_evidence={
                "projected_compounded_profit": format(projected_compounded_profit, "f"),
                "projected_distributable_profit": format(projected_distributable_profit, "f"),
                "risk_authority": "caller_supplied_non_authoritative",
            },
            evaluated_at=now,
        )

    if (
        campaign.compounding_policy.maximum_campaign_loss is not None
        and campaign.accounting_state.realized_net_pnl <= (Decimal("0") - campaign.compounding_policy.maximum_campaign_loss)
    ):
        return CapitalCampaignPreviewResponse(
            campaign_id=campaign.campaign_id,
            campaign_version=campaign.version,
            aggression_mode=campaign.aggression_mode,
            no_action=True,
            no_action_reason="campaign_maximum_loss_reached",
            proposed_opportunities=[],
            rejected_opportunities=rejected,
            remaining_cash=available_capital,
            expected_fees=Decimal("0"),
            expected_slippage=Decimal("0"),
            expected_net_edge=Decimal("0"),
            campaign_policy_checks=["maximum_loss_gate_enforced"],
            risk_checks=[],
            decision_evidence={
                "projected_compounded_profit": format(projected_compounded_profit, "f"),
                "projected_distributable_profit": format(projected_distributable_profit, "f"),
                "risk_authority": "caller_supplied_non_authoritative",
            },
            evaluated_at=now,
        )

    if campaign.status not in {"READY", "ACTIVE", "PAUSED"}:
        return CapitalCampaignPreviewResponse(
            campaign_id=campaign.campaign_id,
            campaign_version=campaign.version,
            aggression_mode=campaign.aggression_mode,
            no_action=True,
            no_action_reason=f"campaign_status_not_preview_eligible:{campaign.status}",
            proposed_opportunities=[],
            rejected_opportunities=rejected,
            remaining_cash=available_capital,
            expected_fees=Decimal("0"),
            expected_slippage=Decimal("0"),
            expected_net_edge=Decimal("0"),
            campaign_policy_checks=["status_gate_checked"],
            risk_checks=[],
            decision_evidence={
                "scan_frequency": aggression_config.scan_frequency,
                "minimum_confidence": format(aggression_config.minimum_confidence, "f"),
                "risk_authority": "caller_supplied_non_authoritative",
            },
            evaluated_at=now,
        )

    max_positions_allowed = int(
        (Decimal(campaign.maximum_open_positions) * aggression_config.max_position_multiplier).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    if max_positions_allowed <= 0 and campaign.maximum_open_positions > 0:
        max_positions_allowed = 1
    max_positions_allowed = min(max_positions_allowed, campaign.maximum_open_positions)

    reserve_floor = campaign.accounting_state.reserved_capital * aggression_config.reserve_multiplier
    remaining_cash = max(Decimal("0"), available_capital - reserve_floor)

    opportunities: list[CampaignPreviewOpportunity] = []
    edge_floor = _policy_edge_floor(campaign)

    for instrument in eligible_candidates:
        strategy = strategy_by_instrument.get(instrument)
        if strategy is None:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="missing_strategy_evidence"))
            continue

        if strategy.confidence < aggression_config.minimum_confidence:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="confidence_below_aggression_threshold"))
            continue

        lifecycle = lifecycle_by_instrument.get(instrument)
        if lifecycle is None:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="missing_lifecycle_evidence"))
            continue
        if lifecycle.market_data_stale:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="stale_market_data"))
            continue
        if lifecycle.dust_indicator:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="dust_position_state"))
            continue
        if lifecycle.closed_indicator is False and lifecycle.lifecycle_state == "CLOSED":
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="inconsistent_lifecycle_state"))
            continue

        risk_preview = risk_by_instrument.get(instrument)
        if risk_preview is None:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="risk_preview_missing"))
            continue
        if risk_preview.verdict == "VETO":
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason=f"risk_veto:{risk_preview.reason or 'unspecified'}"))
            continue

        expected_net_edge = strategy.expected_gross_edge - strategy.expected_fees - strategy.expected_slippage
        if expected_net_edge <= Decimal("0"):
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="non_positive_fee_adjusted_edge"))
            continue
        if expected_net_edge < edge_floor:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="edge_below_campaign_floor"))
            continue

        allocation_cap = min(campaign.maximum_position_size, campaign.maximum_total_exposure)
        if risk_preview.max_allocation is not None:
            allocation_cap = min(allocation_cap, risk_preview.max_allocation)
        if allocation_cap < campaign.minimum_position_size:
            rejected.append(CampaignPreviewRejection(instrument=instrument, reason="allocation_cap_below_campaign_minimum"))
            continue

        opportunities.append(
            CampaignPreviewOpportunity(
                instrument=instrument,
                proposed_allocation=allocation_cap,
                expected_fees=strategy.expected_fees,
                expected_slippage=strategy.expected_slippage,
                expected_net_edge=expected_net_edge,
                confidence=strategy.confidence,
                reason="eligible_fee_adjusted_positive_edge",
            )
        )

    opportunities.sort(
        key=lambda item: (
            item.expected_net_edge,
            item.confidence,
            item.instrument,
        ),
        reverse=True,
    )

    selected: list[CampaignPreviewOpportunity] = []
    expected_fees = Decimal("0")
    expected_slippage = Decimal("0")
    expected_net_edge = Decimal("0")

    for opportunity in opportunities:
        if len(selected) >= max_positions_allowed:
            rejected.append(CampaignPreviewRejection(instrument=opportunity.instrument, reason="max_open_positions_reached"))
            continue
        if remaining_cash <= Decimal("0"):
            rejected.append(CampaignPreviewRejection(instrument=opportunity.instrument, reason="capital_exhausted"))
            continue

        allocated = min(opportunity.proposed_allocation, remaining_cash)
        if allocated < campaign.minimum_position_size:
            rejected.append(CampaignPreviewRejection(instrument=opportunity.instrument, reason="remaining_cash_below_minimum_position"))
            continue

        selected.append(
            CampaignPreviewOpportunity(
                instrument=opportunity.instrument,
                proposed_allocation=allocated,
                expected_fees=opportunity.expected_fees,
                expected_slippage=opportunity.expected_slippage,
                expected_net_edge=opportunity.expected_net_edge,
                confidence=opportunity.confidence,
                reason=opportunity.reason,
            )
        )
        remaining_cash -= allocated
        expected_fees += opportunity.expected_fees
        expected_slippage += opportunity.expected_slippage
        expected_net_edge += opportunity.expected_net_edge

    no_action = len(selected) == 0
    no_action_reason = None if not no_action else "no_opportunity_meets_fee_adjusted_policy_and_risk_requirements"

    return CapitalCampaignPreviewResponse(
        campaign_id=campaign.campaign_id,
        campaign_version=campaign.version,
        aggression_mode=campaign.aggression_mode,
        no_action=no_action,
        no_action_reason=no_action_reason,
        proposed_opportunities=selected,
        rejected_opportunities=rejected,
        remaining_cash=remaining_cash,
        expected_fees=expected_fees,
        expected_slippage=expected_slippage,
        expected_net_edge=expected_net_edge,
        campaign_policy_checks=[
            "campaign_modes_validated",
            "percentage_integrity_validated",
            "minimum_position_and_exposure_applied",
        ],
        risk_checks=[
            "risk_preview_verdict_required",
            "risk_veto_enforced",
        ],
        decision_evidence={
            "scan_frequency": aggression_config.scan_frequency,
            "minimum_confidence": format(aggression_config.minimum_confidence, "f"),
            "reserve_floor": format(reserve_floor, "f"),
            "max_positions_allowed": max_positions_allowed,
            "edge_floor": format(edge_floor, "f"),
            "projected_compounded_profit": format(projected_compounded_profit, "f"),
            "projected_distributable_profit": format(projected_distributable_profit, "f"),
            "evidence_authority": {
                "strategy": "caller_supplied_non_authoritative",
                "lifecycle": "caller_supplied_non_authoritative",
                "risk": "caller_supplied_non_authoritative",
            },
            "strategy_authority_by_instrument": strategy_authority_by_instrument,
            "lifecycle_authority_by_instrument": lifecycle_authority_by_instrument,
            "risk_authority_by_instrument": risk_authority_by_instrument,
            "persistence_mode": "computed_read_only",
        },
        evaluated_at=now,
    )
