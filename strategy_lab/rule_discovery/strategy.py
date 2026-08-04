from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from strategy_lab.candles import Candle
from strategy_lab.config import SimulationConfig
from strategy_lab.strategy import FillResult, PositionState

from .interpreter import EvaluationContext, RuleEvaluation, evaluate_rule
from .models import CandidateRule, StrategyBranch

SUPPORTED_ENTRY_REPLAY_ACTIONS = frozenset({
    "ALLOW_LONG_ENTRY", "BLOCK_LONG_ENTRY", "WAIT_FOR_CONFIRMATION", "CHANGE_ENTRY_OFFSET",
})


@dataclass
class RuleBranchStrategy:
    parent: Any
    candidate_rule: CandidateRule
    branch: StrategyBranch
    config: SimulationConfig
    last_evaluation: RuleEvaluation | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        action = self.candidate_rule.action["action"]
        if action not in SUPPORTED_ENTRY_REPLAY_ACTIONS:
            raise ValueError(f"{action} is not executable by the Phase 1 entry-rule replay hook")

    def evaluate_entry_rule(self, closed_candles: Sequence[Candle], *, capital: Decimal) -> RuleEvaluation:
        self.last_evaluation = evaluate_rule(
            self.branch.rule_document,
            closed_candles,
            EvaluationContext(strategy_state="flat", capital=capital, baseline_capital=self.config.initial_capital),
        )
        return self.last_evaluation

    def entry_allowed(self, evaluation: RuleEvaluation) -> bool:
        if not evaluation.matched:
            return True
        return evaluation.action not in {"BLOCK_LONG_ENTRY", "WAIT_FOR_CONFIRMATION"}

    def propose_entry_price(self, closed_candles: Sequence[Candle]) -> Decimal:
        evaluation = self.last_evaluation
        if evaluation and evaluation.matched and evaluation.action == "CHANGE_ENTRY_OFFSET":
            return closed_candles[-1].close * (Decimal("1") - Decimal(str(evaluation.action_value)))
        return self.parent.propose_entry_price(closed_candles)

    def open_position(self, fill: FillResult, closed_candles: Sequence[Candle]) -> PositionState:
        return self.parent.open_position(fill, closed_candles)

    def check_exit(self, position, candle, prior_closes):
        return self.parent.check_exit(position, candle, prior_closes)

    def update_position_state(self, position, candle):
        return self.parent.update_position_state(position, candle)