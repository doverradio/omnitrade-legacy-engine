from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DecisionQualityResult:
    quality_score: int
    decision_reproduced: bool
    action_matches_original: bool
    confidence_matches_original: bool
    replay_duration_ms: int | None
    evaluation_timestamp: datetime
    calibration: Decimal | None = None
    opportunity_cost: Decimal | None = None
    drawdown: Decimal | None = None
    risk_adjusted_return: Decimal | None = None
    explanation_quality: Decimal | None = None
