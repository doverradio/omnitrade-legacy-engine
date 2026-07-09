from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import NotFoundError
from app.schemas.candidate_evaluation import CandidateEvaluationRequest, CandidateEvaluationResponse
from app.schemas.research_agents import ResearchAgentResponse, StrategyCandidateResponse
from app.services.candidate_evaluation.deterministic import (
    CandidateNotFoundError,
    build_candidate_evaluation_v1,
    resolve_candidate_by_id_v1,
)
from app.services.research_agents.registry import list_generated_strategy_candidates, list_registered_research_agents

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/agents", response_model=list[ResearchAgentResponse])
async def get_research_agents() -> list[ResearchAgentResponse]:
    return [
        ResearchAgentResponse(
            agent_id=item.agent_id,
            agent_name=item.agent_name,
            capabilities=list(item.capabilities),
        )
        for item in list_registered_research_agents()
    ]


@router.get("/candidates", response_model=list[StrategyCandidateResponse])
async def get_research_candidates() -> list[StrategyCandidateResponse]:
    return [
        StrategyCandidateResponse(
            candidate_id=item.candidate_id,
            generated_at=item.generated_at,
            originating_agent=item.originating_agent,
            strategy_name=item.strategy_name,
            description=item.description,
            parameter_set=dict(item.parameter_set),
            rationale=item.rationale,
            status=item.status,
        )
        for item in list_generated_strategy_candidates()
    ]


@router.post("/evaluate-candidate", response_model=CandidateEvaluationResponse)
async def evaluate_candidate(request: CandidateEvaluationRequest) -> CandidateEvaluationResponse:
    all_candidates = list_generated_strategy_candidates()
    try:
        candidate = resolve_candidate_by_id_v1(
            candidate_id=request.candidate_id,
            candidates=list(all_candidates),
        )
    except CandidateNotFoundError:
        raise NotFoundError(
            message="Strategy candidate not found",
            details={"candidate_id": str(request.candidate_id)},
        )

    evaluation = build_candidate_evaluation_v1(
        candidate=candidate,
        all_candidates=list(all_candidates),
    )
    return CandidateEvaluationResponse(
        evaluation_id=evaluation.evaluation_id,
        candidate_id=evaluation.candidate_id,
        replay_status=evaluation.replay_status,
        decision_quality_score=evaluation.decision_quality_score,
        ai_coach_summary=evaluation.ai_coach_summary,
        decision_intelligence_summary=evaluation.decision_intelligence_summary,
        tournament_rank=evaluation.tournament_rank,
        promotion_eligible=evaluation.promotion_eligible,
    )
