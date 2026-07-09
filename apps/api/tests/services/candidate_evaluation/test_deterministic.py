from __future__ import annotations

import uuid

import pytest

from app.services.candidate_evaluation.deterministic import (
    CandidateNotFoundError,
    build_candidate_evaluation_v1,
    resolve_candidate_by_id_v1,
)
from app.services.research_agents.registry import list_generated_strategy_candidates


def test_candidate_evaluation_contains_expected_fields() -> None:
    candidates = list_generated_strategy_candidates()
    evaluation = build_candidate_evaluation_v1(
        candidate=candidates[0],
        all_candidates=list(candidates),
    )

    assert evaluation.candidate_id == candidates[0].candidate_id
    assert evaluation.replay_status == "COMPLETED"
    assert evaluation.decision_quality_score in {0, 50, 100}
    assert evaluation.ai_coach_summary
    assert evaluation.decision_intelligence_summary
    assert evaluation.promotion_eligible is False


def test_candidate_evaluation_is_deterministic() -> None:
    candidates = list_generated_strategy_candidates()
    first = build_candidate_evaluation_v1(candidate=candidates[0], all_candidates=list(candidates))
    second = build_candidate_evaluation_v1(candidate=candidates[0], all_candidates=list(candidates))

    assert first.evaluation_id == second.evaluation_id
    assert first.replay_status == second.replay_status
    assert first.decision_quality_score == second.decision_quality_score
    assert first.ai_coach_summary == second.ai_coach_summary
    assert first.decision_intelligence_summary == second.decision_intelligence_summary
    assert first.tournament_rank == second.tournament_rank
    assert first.promotion_eligible == second.promotion_eligible


def test_candidate_resolution_raises_for_missing_candidate() -> None:
    candidates = list_generated_strategy_candidates()
    with pytest.raises(CandidateNotFoundError):
        resolve_candidate_by_id_v1(
            candidate_id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
            candidates=list(candidates),
        )
