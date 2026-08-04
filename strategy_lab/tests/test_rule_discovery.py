from dataclasses import replace
from decimal import Decimal

import pytest

from strategy_lab.rule_discovery import EvaluationContext, RuleValidationError, evaluate_rule, validate_rule_document
from strategy_lab.config import SimulationConfig
from strategy_lab.capital import BALANCED
from strategy_lab.engine import run_simulation
from strategy_lab.rule_discovery import (
    RuleBranchStrategy, build_strategy_package, create_candidate_rule, create_strategy_branch,
    overfitting_warnings, promotion_eligibility, replay_branch_partition,
)
from strategy_lab.strategies.trailing_limit_v2 import TrailingLimitV2Strategy
from strategy_lab.tests.helpers import candle


def ce_001_rule():
    return {
        "schema_version": "1.0.0",
        "when": {"all": [{"feature": "short_window_momentum", "operator": "<", "value": "0", "lookback": 3}]},
        "then": {"action": "BLOCK_LONG_ENTRY"},
        "risk_controls": {"minimum_occurrences": 5, "maximum_drawdown_pct": "10", "final_test_used_for_tuning": False},
    }


def test_ce_001_rule_evaluates_completed_negative_momentum() -> None:
    candles = [candle(index, o=100-index, h=101-index, l=99-index, c=100-index) for index in range(5)]
    result = evaluate_rule(ce_001_rule(), candles, EvaluationContext())
    assert result.matched is True
    assert result.action == "BLOCK_LONG_ENTRY"
    assert Decimal(result.condition_values["when.all[0]"]) < 0


@pytest.mark.parametrize("document", [
    {**ce_001_rule(), "python": "__import__('os').system('id')"},
    {**ce_001_rule(), "when": {"all": [{"feature": "short_window_momentum", "operator": "exec", "value": "0"}]}},
    {**ce_001_rule(), "when": {"all": [{"feature": "close", "operator": ">", "reference": "next_close"}]}},
])
def test_rule_schema_rejects_code_unsupported_operators_and_lookahead(document) -> None:
    with pytest.raises(RuleValidationError):
        validate_rule_document(document)


def _candidate():
    return create_candidate_rule(
        candidate_rule_id="CR-000001", name="Block negative momentum entries",
        description="CE-001 controlled entry gate", source_analysis_id="analysis_001",
        source_finding_ids=("finding_0001",), source_candidate_experiment_id="CE-001",
        parent_strategy_version="002", rule_document=ce_001_rule(), created_by="human_with_copilot",
        created_at="2026-08-04T00:00:00+00:00",
    )


def test_branch_identity_is_deterministic_and_parent_replay_is_unchanged() -> None:
    candidate = _candidate()
    first = create_strategy_branch(candidate, created_at="2026-08-04T00:00:00+00:00")
    second = create_strategy_branch(candidate, created_at="2026-08-05T00:00:00+00:00")
    assert first.strategy_branch_id == second.strategy_branch_id
    assert first.content_hash == second.content_hash

    candles = [candle(index, o=100-index, h=101-index, l=98-index, c=100-index) for index in range(8)]
    config = SimulationConfig(fee_pct=Decimal("0"), slippage_pct=Decimal("0"))
    parent = TrailingLimitV2Strategy(config)
    before = run_simulation(candles, parent, config)
    branch_result = run_simulation(candles, RuleBranchStrategy(parent, candidate, first, config), config)
    after = run_simulation(candles, parent, config)
    assert before == after
    assert any(event.kind == "RULE_MATCHED" for event in branch_result.replay_events)
    assert any(event.kind == "RULE_ACTION_APPLIED" and event.action == "BLOCK_LONG_ENTRY" for event in branch_result.replay_events)


def test_phase_one_replay_rejects_non_entry_actions_instead_of_silently_ignoring_them() -> None:
    candidate = replace(_candidate(), action={"action": "EXIT_POSITION"})
    branch = create_strategy_branch(candidate, created_at="2026-08-04T00:00:00+00:00")
    config = SimulationConfig()
    with pytest.raises(ValueError, match="not executable by the Phase 1 entry-rule replay hook"):
        RuleBranchStrategy(TrailingLimitV2Strategy(config), candidate, branch, config)


def test_partition_replay_comparison_promotion_and_package_hash_are_deterministic() -> None:
    candidate = _candidate()
    branch = create_strategy_branch(candidate, created_at="2026-08-04T00:00:00+00:00")
    candles = [candle(index, o=100-index % 8, h=101-index % 8, l=98-index % 8, c=100-index % 8) for index in range(90)]
    config = SimulationConfig(fee_pct=Decimal("0.002"), slippage_pct=Decimal("0.0005"))
    reports = {name: replay_branch_partition(candles, candidate, branch, config, name) for name in ("training", "validation", "final_test", "entire_dataset")}
    assert set(reports["training"]["parent_delta"]) == {"net_return_percentage_points", "ending_capital", "total_economic_value"}
    assert reports["training"]["rule_match_count"] >= reports["training"]["rule_action_count"]
    assert promotion_eligibility(candidate, reports)["status"] in {"PROMOTABLE", "REJECTED"}
    assert isinstance(overfitting_warnings(reports), list)
    first = build_strategy_package(candidate=candidate, branch=branch, dataset_identity={"id": "fixture"}, reports=reports, feature_versions={"rule_features": "1.0.0"}, detector_versions={"negative_slope_v1": "1.0.0"})
    second = build_strategy_package(candidate=candidate, branch=branch, dataset_identity={"id": "fixture"}, reports=reports, feature_versions={"rule_features": "1.0.0"}, detector_versions={"negative_slope_v1": "1.0.0"})
    assert first["content_hash"] == second["content_hash"]


def test_partition_replay_reports_requested_capital_policy() -> None:
    candidate = _candidate()
    branch = create_strategy_branch(candidate, created_at="2026-08-04T00:00:00+00:00")
    candles = [candle(index, o=100-index % 4, h=102-index % 4, l=97-index % 4, c=100-index % 4) for index in range(90)]
    report = replay_branch_partition(candles, candidate, branch, SimulationConfig(), "entire_dataset", BALANCED)
    assert report["capital_policy"] == {
        "name": "balanced", "trade_deployment_pct": "25", "profit_compound_pct": "60",
        "profit_withdrawal_pct": "20", "profit_tax_reserve_pct": "20",
    }
    assert "raw_strategy_net_return_pct" in report["candidate"]
    assert "withdrawn_profit" in report["candidate"]


def test_zero_trade_candidate_is_not_promotable_without_observed_outcomes() -> None:
    candidate = _candidate()
    reports = {
        partition: {
            "rule_match_count": 20, "unsupported_rule_behavior_count": 0,
            "cost_model": {"fee_pct": "0.002", "slippage_pct": "0.0005"},
            "candidate": {"trade_count": 0, "maximum_drawdown_pct": "0"},
            "parent_delta": {"net_return_percentage_points": "1"},
            "candidate_trade_net_returns_pct": [],
        }
        for partition in ("training", "validation", "final_test", "entire_dataset")
    }
    eligibility = promotion_eligibility(candidate, reports)
    assert eligibility["status"] == "REJECTED"
    assert eligibility["checks"]["validation_and_final_test_outcomes_observed"] is False
    assert any(warning["code"] == "NO_CANDIDATE_TRADES" for warning in overfitting_warnings(reports))