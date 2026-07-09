from __future__ import annotations

from app.services.research_agents.llm_adapter.contracts import (
    CandidateHistoryItem,
    HypothesisRequest,
    HypothesisResponse,
    TournamentHistoryItem,
)


def test_hypothesis_contract_serialization_roundtrip() -> None:
    request = HypothesisRequest(
        research_memory={"total_candidates": 10},
        evolution_analytics={"average_quality_score": 75.0},
        candidate_history=[
            CandidateHistoryItem(
                candidate_id="00000000-0000-0000-0000-000000000011",
                generation=2,
                quality_score=100,
                tournament_rank=1,
                parameter_set={"rsi_period": 12},
            )
        ],
        tournament_history=[
            TournamentHistoryItem(
                tournament_id="00000000-0000-0000-0000-000000000021",
                generated_at="2026-07-09T12:00:00Z",
                ranking=[{"strategy_name": "MA Crossover", "overall_rank": 1}],
            )
        ],
    )

    payload = request.model_dump()
    restored = HypothesisRequest.model_validate(payload)

    assert restored.research_memory["total_candidates"] == 10
    assert restored.evolution_analytics["average_quality_score"] == 75.0
    assert len(restored.candidate_history) == 1
    assert len(restored.tournament_history) == 1

    response = HypothesisResponse(
        candidate_strategy="MA-RSI Adaptive Blend",
        rationale="Deterministic extrapolation from high-quality candidates.",
        expected_behavior="Improves replay consistency under trend transitions.",
        confidence=0.82,
    )
    assert response.model_dump()["candidate_strategy"] == "MA-RSI Adaptive Blend"
