from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from typing import Any, Sequence

from strategy_lab.candles import Candle
from strategy_lab.capital import CapitalPolicy, FULL_COMPOUNDING, apply_capital_policy
from strategy_lab.comparison import CostScenario, buy_and_hold_ending_value
from strategy_lab.config import SimulationConfig
from strategy_lab.costs import CostModel
from strategy_lab.engine import SimulationResult, run_simulation
from strategy_lab.metrics import compute_metrics
from strategy_lab.pattern_intelligence.partitions import partition_bounds
from strategy_lab.strategies.trailing_limit_v1 import TrailingLimitV1Strategy
from strategy_lab.strategies.trailing_limit_v2 import TrailingLimitV2Strategy

from .models import CandidateRule, StrategyBranch
from .strategy import RuleBranchStrategy

PARTITIONS = ("training", "validation", "final_test", "entire_dataset")


def replay_branch_partition(
    candles: Sequence[Candle], candidate: CandidateRule, branch: StrategyBranch,
    config: SimulationConfig, partition: str, capital_policy: CapitalPolicy = FULL_COMPOUNDING,
) -> dict[str, Any]:
    if partition not in PARTITIONS:
        raise ValueError("unsupported replay partition")
    bounds = partition_bounds(len(candles))[partition]
    selected = tuple(candles[bounds[0]:bounds[1] + 1])
    parent = _parent_strategy(candidate.parent_strategy_version, config)
    parent_result = run_simulation(selected, parent, config, CostModel.from_config(config))
    candidate_result = run_simulation(
        selected,
        RuleBranchStrategy(_parent_strategy(candidate.parent_strategy_version, config), candidate, branch, config),
        config,
        CostModel.from_config(config),
    )
    scenario = CostScenario("candidate_rule", config.fee_pct, config.slippage_pct, "Explicit candidate-rule replay costs")
    buy_hold = buy_and_hold_ending_value(selected, config.initial_capital, scenario)
    parent_summary = _summary(parent_result, config.initial_capital, buy_hold, capital_policy)
    candidate_summary = _summary(candidate_result, config.initial_capital, buy_hold, capital_policy)
    matches = [event for event in candidate_result.replay_events if event.kind == "RULE_MATCHED"]
    actions = [event for event in candidate_result.replay_events if event.kind == "RULE_ACTION_APPLIED"]
    rejected = [event for event in candidate_result.replay_events if event.kind == "RULE_REJECTED"]
    return {
        "partition": partition,
        "selected_range": list(bounds),
        "parent_strategy": candidate.parent_strategy_version,
        "strategy_branch": branch.strategy_branch_id,
        "cost_model": {"fee_pct": str(config.fee_pct), "slippage_pct": str(config.slippage_pct)},
        "capital_policy": {
            "name": capital_policy.name,
            "trade_deployment_pct": str(capital_policy.trade_deployment_pct),
            "profit_compound_pct": str(capital_policy.profit_compound_pct),
            "profit_withdrawal_pct": str(capital_policy.profit_withdrawal_pct),
            "profit_tax_reserve_pct": str(capital_policy.profit_tax_reserve_pct),
        },
        "parent": parent_summary,
        "candidate": candidate_summary,
        "buy_and_hold": {
            "ending_value": str(buy_hold),
            "return_pct": str((buy_hold / config.initial_capital - Decimal("1")) * Decimal("100")),
        },
        "rule_match_count": len(matches),
        "rule_action_count": len(actions),
        "unsupported_rule_behavior_count": len(rejected),
        "parent_delta": {
            "net_return_percentage_points": str(Decimal(candidate_summary["net_return_pct"]) - Decimal(parent_summary["net_return_pct"])),
            "ending_capital": str(Decimal(candidate_summary["ending_capital"]) - Decimal(parent_summary["ending_capital"])),
            "total_economic_value": str(Decimal(candidate_summary["total_economic_value"]) - Decimal(parent_summary["total_economic_value"])),
        },
        "buy_and_hold_delta": str(Decimal(candidate_summary["total_economic_value"]) - buy_hold),
        "rule_events": [_event(event, bounds[0]) for event in candidate_result.replay_events if event.kind.startswith("RULE_")],
        "candidate_trade_net_returns_pct": [str(trade.net_return_pct * Decimal("100")) for trade in candidate_result.trades],
    }


def overfitting_warnings(reports: dict[str, dict[str, Any]], *, rules_tested_on_dataset: int = 1) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    training = reports.get("training")
    validation = reports.get("validation")
    final_test = reports.get("final_test")
    if training and validation and _delta(training) > 0 and _delta(validation) < 0:
        warnings.append({"code": "TRAINING_VALIDATION_DIVERGENCE", "message": "Training improved while Validation worsened."})
    if final_test and _delta(final_test) < Decimal("-0.5"):
        warnings.append({"code": "FINAL_TEST_MATERIAL_DETERIORATION", "message": "Final Test worsened by more than 0.5 percentage points."})
    tested = [report for name, report in reports.items() if name != "entire_dataset"]
    total_matches = sum(int(report["rule_match_count"]) for report in tested)
    if total_matches < 10:
        warnings.append({"code": "FEW_RULE_MATCHES", "message": f"Only {total_matches} rule matches occurred across Training, Validation, and Final Test."})
    entire = reports.get("entire_dataset")
    if entire:
        returns = [abs(Decimal(value)) for value in entire.get("candidate_trade_net_returns_pct", [])]
        if int(entire["candidate"]["trade_count"]) == 0:
            warnings.append({"code": "NO_CANDIDATE_TRADES", "message": "The Candidate Rule took no trades, so post-rule outcome quality cannot be estimated."})
        if returns and sum(returns) and max(returns) / sum(returns) >= Decimal("0.5"):
            warnings.append({"code": "ONE_TRADE_DOMINATES", "message": "One trade accounts for at least half of absolute candidate trade results."})
    if rules_tested_on_dataset > 20:
        warnings.append({"code": "MANY_RULES_TESTED", "message": f"{rules_tested_on_dataset} Candidate Rules were tested on this dataset."})
    return warnings


def promotion_eligibility(candidate: CandidateRule, reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required = ("training", "validation", "final_test")
    checks = {
        "training_replay_completed": "training" in reports,
        "validation_replay_completed": "validation" in reports,
        "final_test_replay_completed": "final_test" in reports,
        "minimum_occurrence_count_satisfied": False,
        "validation_and_final_test_outcomes_observed": False,
        "validation_does_not_collapse": False,
        "final_test_not_used_for_tuning": not bool(candidate.risk_controls.get("final_test_used_for_tuning")),
        "cost_model_explicit": all(bool(reports[name].get("cost_model")) for name in required if name in reports),
        "maximum_drawdown_within_bounds": False,
        "no_unsupported_rule_behavior": all(int(reports[name]["unsupported_rule_behavior_count"]) == 0 for name in required if name in reports),
    }
    if all(name in reports for name in required):
        checks["minimum_occurrence_count_satisfied"] = sum(int(reports[name]["rule_match_count"]) for name in required) >= int(candidate.risk_controls["minimum_occurrences"])
        checks["validation_and_final_test_outcomes_observed"] = all(int(reports[name]["candidate"]["trade_count"]) > 0 for name in ("validation", "final_test"))
        checks["validation_does_not_collapse"] = _delta(reports["validation"]) >= Decimal("-0.5")
        drawdown_limit = Decimal(str(candidate.risk_controls["maximum_drawdown_pct"]))
        checks["maximum_drawdown_within_bounds"] = all(Decimal(reports[name]["candidate"]["maximum_drawdown_pct"]) <= drawdown_limit for name in required)
    eligible = all(checks.values())
    return {"eligible": eligible, "status": "PROMOTABLE" if eligible else "REJECTED", "checks": checks}


def build_strategy_package(
    *, candidate: CandidateRule, branch: StrategyBranch, dataset_identity: dict[str, Any],
    reports: dict[str, dict[str, Any]], feature_versions: dict[str, str], detector_versions: dict[str, str],
) -> dict[str, Any]:
    eligibility = promotion_eligibility(candidate, reports)
    package = {
        "package_version": "1.0.0",
        "strategy_branch_id": branch.strategy_branch_id,
        "parent_strategy": candidate.parent_strategy_version,
        "candidate_rule": asdict(candidate),
        "dataset_identity": dataset_identity,
        "simulator_version": branch.simulator_version,
        "feature_versions": feature_versions,
        "detector_versions": detector_versions,
        "cost_assumptions": reports.get("training", {}).get("cost_model"),
        "training_result": reports.get("training"),
        "validation_result": reports.get("validation"),
        "final_test_result": reports.get("final_test"),
        "promotion_status": eligibility["status"],
    }
    payload = json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {**package, "content_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest()}


def _parent_strategy(version: str, config: SimulationConfig):
    return TrailingLimitV1Strategy(config) if version == "001" else TrailingLimitV2Strategy(config)


def _summary(result: SimulationResult, initial_capital: Decimal, buy_hold: Decimal, policy: CapitalPolicy) -> dict[str, Any]:
    metrics = compute_metrics(result)
    capital = apply_capital_policy(result.trades, initial_capital, policy)
    return {
        "trade_count": metrics.total_trades,
        "win_rate_pct": _decimal(metrics.win_rate_pct),
        "gross_return_pct": str(metrics.gross_return_pct),
        "net_return_pct": str(metrics.net_return_pct),
        "ending_capital": str(capital.trading_capital_final),
        "withdrawn_profit": str(capital.cumulative_withdrawn_final),
        "tax_reserve": str(capital.cumulative_tax_reserve_final),
        "total_economic_value": str(capital.total_economic_value_final),
        "maximum_drawdown_pct": str(metrics.max_drawdown_pct),
        "profit_factor": _decimal(metrics.profit_factor),
        "fees": str(metrics.fees_paid),
        "slippage": str(metrics.estimated_slippage),
        "average_holding_candles": _decimal(metrics.average_holding_candles),
        "average_mfe_pct": _decimal(metrics.average_mfe_pct),
        "average_mae_pct": _decimal(metrics.average_mae_pct),
        "buy_and_hold_delta": str(capital.total_economic_value_final - buy_hold),
        "return_on_initial_capital_pct": str((capital.total_economic_value_final / initial_capital - Decimal("1")) * Decimal("100")),
        "raw_strategy_net_return_pct": str(capital.raw_strategy_net_return_pct),
    }


def _event(event, offset: int) -> dict[str, Any]:
    return {
        "candidate_rule_id": event.candidate_rule_id,
        "strategy_branch": event.strategy_branch,
        "candle_index": event.candle_index + offset,
        "timestamp": event.timestamp.isoformat(),
        "kind": event.kind,
        "condition_values": event.condition_values,
        "thresholds": event.thresholds,
        "result": event.result,
        "action": event.action,
    }


def _decimal(value) -> str | None:
    return None if value is None else str(value)


def _delta(report: dict[str, Any]) -> Decimal:
    return Decimal(str(report["parent_delta"]["net_return_percentage_points"]))