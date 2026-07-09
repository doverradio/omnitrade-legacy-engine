from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.schemas.decision_intelligence import DecisionIntelligenceRecommendationResponse


def test_decision_intelligence_response_serializes() -> None:
    response = DecisionIntelligenceRecommendationResponse(
        recommendation_id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        generated_at=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        compared_strategies=["MA Crossover", "RSI Mean Reversion"],
        highest_quality_strategy="MA Crossover",
        evidence_summary="Compared 2 active strategies using deterministic replay quality and variance tie-breaks.",
        confidence_summary="Best strategy confidence note: Confidence aligned with the original decision.",
        recommendation_summary="MA Crossover ranked highest by deterministic quality scoring with configured tie-break rules.",
        human_review_required=True,
        promotion_recommended=False,
    )

    payload = response.model_dump(mode="json")
    assert payload["highest_quality_strategy"] == "MA Crossover"
    assert payload["human_review_required"] is True
    assert payload["promotion_recommended"] is False