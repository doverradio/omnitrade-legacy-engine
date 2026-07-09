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
    )

    def generate_candidates(self) -> tuple[StrategyCandidate, ...]:
        generated_at = datetime.now(timezone.utc)
        candidate = StrategyCandidate(
            candidate_id=uuid.uuid5(_CANDIDATE_NAMESPACE, "baseline-volatility-filter-ma-rsi"),
            generated_at=generated_at,
            originating_agent=self.agent_name,
            strategy_name="Volatility Filter MA-RSI Blend",
            description="Combines moving-average trend filter with RSI threshold confirmation under deterministic volatility guardrails.",
            parameter_set={
                "fast_period": 12,
                "slow_period": 48,
                "rsi_period": 14,
                "buy_threshold": 32,
                "sell_threshold": 68,
                "min_atr_pct": "0.004",
            },
            rationale="Baseline deterministic candidate intended to improve signal quality under mixed trend and mean-reversion market states.",
            status="PROPOSED",
        )
        return (candidate,)
