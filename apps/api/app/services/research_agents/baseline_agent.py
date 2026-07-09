from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.services.research_agents.interface import StrategyCandidate


BASELINE_RESEARCH_AGENT_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
_CANDIDATE_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000005")


class BaselineResearchAgent:
    agent_id = BASELINE_RESEARCH_AGENT_ID
    agent_name = "Baseline Research Agent"
    capabilities = (
        "Generate deterministic candidate strategies",
        "Generate deterministic candidate batches",
    )

    def generate_candidates(self) -> tuple[StrategyCandidate, ...]:
        generated_at = datetime.now(timezone.utc)
        candidates = (
            {
                "slug": "ma-rsi-baseline-rsi14",
                "strategy_name": "MA-RSI Blend rsi14",
                "description": "MA crossover trend filter with RSI(14) confirmation thresholds.",
                "parameter_set": {
                    "family": "ma_rsi_blend",
                    "fast_period": 12,
                    "slow_period": 48,
                    "rsi_period": 14,
                    "buy_threshold": 32,
                    "sell_threshold": 68,
                },
                "rationale": "Baseline blended trend and mean-reversion candidate for deterministic comparison.",
            },
            {
                "slug": "ma-rsi-variant-rsi10",
                "strategy_name": "MA-RSI Blend rsi10",
                "description": "Shorter RSI lookback variant to increase sensitivity in momentum transitions.",
                "parameter_set": {
                    "family": "ma_rsi_blend",
                    "fast_period": 12,
                    "slow_period": 48,
                    "rsi_period": 10,
                    "buy_threshold": 32,
                    "sell_threshold": 68,
                },
                "rationale": "Tests whether shorter RSI lookback improves deterministic decision fidelity.",
            },
            {
                "slug": "ma-rsi-threshold-variant-35-65",
                "strategy_name": "MA-RSI Blend threshold 35/65",
                "description": "RSI threshold variant with narrower entry/exit bands.",
                "parameter_set": {
                    "family": "ma_rsi_blend",
                    "fast_period": 12,
                    "slow_period": 48,
                    "rsi_period": 14,
                    "buy_threshold": 35,
                    "sell_threshold": 65,
                },
                "rationale": "Assesses deterministic quality under less extreme RSI trigger levels.",
            },
            {
                "slug": "ma-crossover-fast9-slow30",
                "strategy_name": "MA Crossover 9/30",
                "description": "Faster MA crossover profile for earlier trend entries.",
                "parameter_set": {
                    "family": "ma_crossover",
                    "fast_period": 9,
                    "slow_period": 30,
                },
                "rationale": "Evaluates deterministic replay quality impact of a more responsive MA pair.",
            },
            {
                "slug": "ma-crossover-fast20-slow100",
                "strategy_name": "MA Crossover 20/100",
                "description": "Slower MA crossover profile to reduce signal churn.",
                "parameter_set": {
                    "family": "ma_crossover",
                    "fast_period": 20,
                    "slow_period": 100,
                },
                "rationale": "Evaluates deterministic replay quality impact of a slower trend-following profile.",
            },
        )

        return tuple(
            StrategyCandidate(
                candidate_id=uuid.uuid5(_CANDIDATE_NAMESPACE, item["slug"]),
                generated_at=generated_at,
                originating_agent=self.agent_name,
                strategy_name=item["strategy_name"],
                description=item["description"],
                parameter_set=item["parameter_set"],
                rationale=item["rationale"],
                status="PROPOSED",
            )
            for item in candidates
        )
