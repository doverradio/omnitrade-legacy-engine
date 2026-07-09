from __future__ import annotations

from fastapi import APIRouter

from app.schemas.research_agents import ResearchAgentResponse, StrategyCandidateResponse
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
