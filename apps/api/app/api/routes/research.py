from __future__ import annotations

from fastapi import APIRouter
import uuid

from app.core.errors import NotFoundError
from app.schemas.candidate_evaluation import (
    CandidateBatchEvaluationRequest,
    CandidateBatchEvaluationResponse,
    CandidateEvaluationRequest,
    CandidateEvaluationResponse,
)
from app.schemas.evolution import EvolvedCandidateResponse, EvolutionMutationResponse, EvolutionRequest, EvolutionResponse
from app.schemas.evolution_analytics import (
    EvolutionAnalyticsAgentLeaderboardResponse,
    EvolutionAnalyticsBestCandidateResponse,
    EvolutionAnalyticsGenerationDistributionResponse,
    EvolutionAnalyticsLargestLineageTreeResponse,
    EvolutionAnalyticsMutationSuccessRateResponse,
    EvolutionAnalyticsQualityPointResponse,
    EvolutionAnalyticsResponse,
    EvolutionAnalyticsRunPointResponse,
)
from app.schemas.research_laboratory import ResearchLaboratoryRunResponse, ResearchLaboratoryStatusResponse
from app.schemas.research_campaign import ResearchCampaignCreateRequest, ResearchCampaignResponse
from app.schemas.llm_adapter import LLMAdapterResponse
from app.schemas.openai_research_agent import OpenAIResearchGenerationResponse
from app.schemas.research_memory import (
    ResearchMemoryCandidateResponse,
    ResearchMemoryLaboratoryRunResponse,
    ResearchMemorySummaryResponse,
)
from app.schemas.research_agents import ResearchAgentResponse, StrategyCandidateResponse
from app.services.candidate_evaluation.deterministic import (
    CandidateNotFoundError,
    build_candidate_evaluations_batch_v1,
    build_candidate_evaluation_v1,
    resolve_candidate_by_id_v1,
)
from app.services.research_agents.registry import list_generated_strategy_candidates, list_registered_research_agents
from app.services.research_agents.llm_adapter.registry import list_registered_llm_research_adapters
from app.services.research_agents.llm_adapter.contracts import CandidateHistoryItem, HypothesisRequest, TournamentHistoryItem
from app.services.research_agents.openai.registry import get_openai_research_agent, get_openai_research_agent_registration
from app.services.research_campaign.registry import get_research_campaign_engine
from app.services.research_campaign.service import CampaignNotFoundError
from app.services.research_laboratory.registry import get_research_laboratory
from app.services.research_memory.registry import get_research_memory
from app.services.evolution.registry import get_evolution_engine
from app.services.evolution.service import ParentCandidateNotFoundError
from app.services.evolution_analytics.registry import get_evolution_analytics_service

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


@router.get("/llm-adapters", response_model=list[LLMAdapterResponse])
async def get_llm_research_adapters() -> list[LLMAdapterResponse]:
    openai_registration = get_openai_research_agent_registration()
    registrations = {openai_registration.adapter_id: openai_registration}
    for item in list_registered_llm_research_adapters():
        registrations[item.adapter_id] = item

    return [
        LLMAdapterResponse(
            adapter_id=item.adapter_id,
            adapter_name=item.adapter_name,
            provider=item.provider,
            capabilities=list(item.capabilities),
            status=item.status,
        )
        for item in registrations.values()
    ]


@router.post("/llm-adapters/openai/generate-candidates", response_model=OpenAIResearchGenerationResponse)
async def generate_openai_research_candidates() -> OpenAIResearchGenerationResponse:
    agent = get_openai_research_agent()
    if not agent.is_available:
        return OpenAIResearchGenerationResponse(
            status="UNAVAILABLE",
            generated_candidates=[],
            evaluations=[],
            generation_timestamp=None,
            prompt_version=None,
            response_duration_ms=None,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    memory = get_research_memory()
    analytics_summary = get_evolution_analytics_service().build_summary()
    candidates_history = list(memory.list_candidates())
    tournament_outcomes = list(memory.list_tournament_outcomes())

    successful_candidates = [
        item
        for item in candidates_history
        if item.quality_score is not None and item.quality_score >= 75
    ][:10]
    failed_candidates = [
        item
        for item in candidates_history
        if item.quality_score is not None and item.quality_score < 50
    ][:10]

    request = HypothesisRequest(
        research_memory={
            "total_laboratory_runs": memory.get_summary().total_laboratory_runs,
            "total_candidates": memory.get_summary().total_candidates,
            "recent_successful_candidates": [str(item.candidate_id) for item in successful_candidates],
            "recent_failed_candidates": [str(item.candidate_id) for item in failed_candidates],
        },
        evolution_analytics={
            "total_laboratory_runs": analytics_summary.total_laboratory_runs,
            "total_candidates_generated": analytics_summary.total_candidates_generated,
            "total_evolved_candidates": analytics_summary.total_evolved_candidates,
            "average_quality_score": analytics_summary.average_quality_score,
            "best_quality_score": analytics_summary.best_quality_score,
            "top_research_agent": analytics_summary.top_research_agent,
            "lineage_depth": analytics_summary.lineage_depth,
        },
        candidate_history=[
            CandidateHistoryItem(
                candidate_id=item.candidate_id,
                generation=item.generation,
                quality_score=item.quality_score,
                tournament_rank=item.tournament_rank,
                parameter_set=dict(item.parameter_set),
            )
            for item in candidates_history[:20]
        ],
        tournament_history=[
            TournamentHistoryItem(
                tournament_id=None,
                generated_at=None,
                ranking=[
                    {
                        "candidate_id": str(item.candidate_id),
                        "rank": item.tournament_rank,
                    }
                    for item in tournament_outcomes[:20]
                ],
            )
        ],
    )

    ideas, metadata = agent.generate_hypotheses_batch(request=request)
    generated_candidates = agent.to_strategy_candidates(
        ideas=ideas,
        generated_at=metadata.generation_timestamp,
    )
    evaluations = build_candidate_evaluations_batch_v1(
        candidates=list(generated_candidates),
        selected_candidate_ids=[item.candidate_id for item in generated_candidates],
        limit=None,
    )

    return OpenAIResearchGenerationResponse(
        status="AVAILABLE",
        generated_candidates=[
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
            for item in generated_candidates
        ],
        evaluations=[
            CandidateEvaluationResponse(
                evaluation_id=item.evaluation_id,
                candidate_id=item.candidate_id,
                replay_status=item.replay_status,
                decision_quality_score=item.decision_quality_score,
                ai_coach_summary=item.ai_coach_summary,
                decision_intelligence_summary=item.decision_intelligence_summary,
                tournament_rank=item.tournament_rank,
                promotion_eligible=item.promotion_eligible,
            )
            for item in evaluations
        ],
        generation_timestamp=metadata.generation_timestamp,
        prompt_version=metadata.prompt_version,
        response_duration_ms=metadata.response_duration_ms,
        prompt_tokens=metadata.prompt_tokens,
        completion_tokens=metadata.completion_tokens,
        total_tokens=metadata.total_tokens,
    )


@router.get("/campaigns", response_model=list[ResearchCampaignResponse])
async def get_research_campaigns() -> list[ResearchCampaignResponse]:
    campaigns = get_research_campaign_engine().list_campaigns()
    return [
        ResearchCampaignResponse(
            campaign_id=item.campaign_id,
            name=item.name,
            objective=item.objective,
            status=item.status,
            started_at=item.started_at,
            completed_at=item.completed_at,
            participating_agents=list(item.participating_agents),
            laboratory_runs=item.laboratory_runs,
            candidates_generated=item.candidates_generated,
            candidates_evaluated=item.candidates_evaluated,
            best_candidate=item.best_candidate,
            best_quality_score=item.best_quality_score,
            current_champion=item.current_champion,
        )
        for item in campaigns
    ]


@router.get("/campaigns/{campaign_id}", response_model=ResearchCampaignResponse)
async def get_research_campaign(campaign_id: str) -> ResearchCampaignResponse:
    try:
        campaign = get_research_campaign_engine().get_campaign(campaign_id=uuid.UUID(campaign_id))
    except (ValueError, CampaignNotFoundError):
        raise NotFoundError(
            message="Research campaign not found",
            details={"campaign_id": campaign_id},
        )

    return ResearchCampaignResponse(
        campaign_id=campaign.campaign_id,
        name=campaign.name,
        objective=campaign.objective,
        status=campaign.status,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        participating_agents=list(campaign.participating_agents),
        laboratory_runs=campaign.laboratory_runs,
        candidates_generated=campaign.candidates_generated,
        candidates_evaluated=campaign.candidates_evaluated,
        best_candidate=campaign.best_candidate,
        best_quality_score=campaign.best_quality_score,
        current_champion=campaign.current_champion,
    )


@router.post("/campaigns", response_model=ResearchCampaignResponse)
async def create_research_campaign(request: ResearchCampaignCreateRequest) -> ResearchCampaignResponse:
    campaign = get_research_campaign_engine().create_campaign(
        name=request.name,
        objective=request.objective,
    )
    return ResearchCampaignResponse(
        campaign_id=campaign.campaign_id,
        name=campaign.name,
        objective=campaign.objective,
        status=campaign.status,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        participating_agents=list(campaign.participating_agents),
        laboratory_runs=campaign.laboratory_runs,
        candidates_generated=campaign.candidates_generated,
        candidates_evaluated=campaign.candidates_evaluated,
        best_candidate=campaign.best_candidate,
        best_quality_score=campaign.best_quality_score,
        current_champion=campaign.current_champion,
    )


@router.post("/campaigns/{campaign_id}/run", response_model=ResearchCampaignResponse)
async def run_research_campaign(campaign_id: str) -> ResearchCampaignResponse:
    try:
        campaign = get_research_campaign_engine().run_campaign(campaign_id=uuid.UUID(campaign_id))
    except (ValueError, CampaignNotFoundError):
        raise NotFoundError(
            message="Research campaign not found",
            details={"campaign_id": campaign_id},
        )

    return ResearchCampaignResponse(
        campaign_id=campaign.campaign_id,
        name=campaign.name,
        objective=campaign.objective,
        status=campaign.status,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        participating_agents=list(campaign.participating_agents),
        laboratory_runs=campaign.laboratory_runs,
        candidates_generated=campaign.candidates_generated,
        candidates_evaluated=campaign.candidates_evaluated,
        best_candidate=campaign.best_candidate,
        best_quality_score=campaign.best_quality_score,
        current_champion=campaign.current_champion,
    )


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


@router.post("/evaluate-candidates", response_model=CandidateBatchEvaluationResponse)
async def evaluate_candidates(request: CandidateBatchEvaluationRequest) -> CandidateBatchEvaluationResponse:
    all_candidates = list(list_generated_strategy_candidates())

    try:
        evaluations = build_candidate_evaluations_batch_v1(
            candidates=all_candidates,
            selected_candidate_ids=request.candidate_ids,
            limit=request.limit,
        )
    except CandidateNotFoundError as exc:
        raise NotFoundError(
            message="Strategy candidate not found",
            details={"candidate_id": str(exc)},
        )

    return CandidateBatchEvaluationResponse(
        evaluated_count=len(evaluations),
        evaluations=[
            CandidateEvaluationResponse(
                evaluation_id=item.evaluation_id,
                candidate_id=item.candidate_id,
                replay_status=item.replay_status,
                decision_quality_score=item.decision_quality_score,
                ai_coach_summary=item.ai_coach_summary,
                decision_intelligence_summary=item.decision_intelligence_summary,
                tournament_rank=item.tournament_rank,
                promotion_eligible=item.promotion_eligible,
            )
            for item in evaluations
        ],
    )


@router.get("/laboratory", response_model=ResearchLaboratoryStatusResponse)
async def get_research_laboratory_status() -> ResearchLaboratoryStatusResponse:
    laboratory = get_research_laboratory()
    status = laboratory.get_status()
    return ResearchLaboratoryStatusResponse(
        status=status.status,
        registered_agents=list(status.registered_agents),
        last_run=(
            None
            if status.last_run is None
            else ResearchLaboratoryRunResponse(
                laboratory_run_id=status.last_run.laboratory_run_id,
                started_at=status.last_run.started_at,
                completed_at=status.last_run.completed_at,
                participating_agents=list(status.last_run.participating_agents),
                generated_candidates=status.last_run.generated_candidates,
                evaluated_candidates=status.last_run.evaluated_candidates,
                status=status.last_run.status,
            )
        ),
        candidates_generated=status.candidates_generated,
        candidates_evaluated=status.candidates_evaluated,
        success_rate=status.success_rate,
    )


@router.post("/laboratory/run", response_model=ResearchLaboratoryRunResponse)
async def run_research_laboratory() -> ResearchLaboratoryRunResponse:
    laboratory = get_research_laboratory()
    run = laboratory.run()
    return ResearchLaboratoryRunResponse(
        laboratory_run_id=run.laboratory_run_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        participating_agents=list(run.participating_agents),
        generated_candidates=run.generated_candidates,
        evaluated_candidates=run.evaluated_candidates,
        status=run.status,
    )


@router.get("/memory", response_model=ResearchMemorySummaryResponse)
async def get_research_memory_summary() -> ResearchMemorySummaryResponse:
    memory = get_research_memory()
    summary = memory.get_summary()

    return ResearchMemorySummaryResponse(
        total_laboratory_runs=summary.total_laboratory_runs,
        total_candidates=summary.total_candidates,
        highest_quality_candidate=(
            None
            if summary.highest_quality_candidate is None
            else ResearchMemoryCandidateResponse(
                laboratory_run_id=summary.highest_quality_candidate.laboratory_run_id,
                candidate_id=summary.highest_quality_candidate.candidate_id,
                originating_agent=summary.highest_quality_candidate.originating_agent,
                parameter_set=dict(summary.highest_quality_candidate.parameter_set),
                evaluation_summary=summary.highest_quality_candidate.evaluation_summary,
                quality_score=summary.highest_quality_candidate.quality_score,
                tournament_rank=summary.highest_quality_candidate.tournament_rank,
                status=summary.highest_quality_candidate.status,
                parent_candidate_id=summary.highest_quality_candidate.parent_candidate_id,
                generation=summary.highest_quality_candidate.generation,
                mutation_reason=summary.highest_quality_candidate.mutation_reason,
                parameter_diff=[
                    ResearchMemoryCandidateResponse.ParameterDiffResponse(
                        parameter_name=diff.parameter_name,
                        previous_value=diff.previous_value,
                        new_value=diff.new_value,
                    )
                    for diff in summary.highest_quality_candidate.parameter_diff
                ],
            )
        ),
        average_quality_score=summary.average_quality_score,
        latest_laboratory_run=(
            None
            if summary.latest_laboratory_run is None
            else ResearchMemoryLaboratoryRunResponse(
                laboratory_run_id=summary.latest_laboratory_run.laboratory_run_id,
                started_at=summary.latest_laboratory_run.started_at,
                completed_at=summary.latest_laboratory_run.completed_at,
                participating_agents=list(summary.latest_laboratory_run.participating_agents),
                candidates_generated=summary.latest_laboratory_run.candidates_generated,
                candidates_evaluated=summary.latest_laboratory_run.candidates_evaluated,
            )
        ),
    )


@router.get("/memory/runs", response_model=list[ResearchMemoryLaboratoryRunResponse])
async def get_research_memory_runs() -> list[ResearchMemoryLaboratoryRunResponse]:
    memory = get_research_memory()
    return [
        ResearchMemoryLaboratoryRunResponse(
            laboratory_run_id=item.laboratory_run_id,
            started_at=item.started_at,
            completed_at=item.completed_at,
            participating_agents=list(item.participating_agents),
            candidates_generated=item.candidates_generated,
            candidates_evaluated=item.candidates_evaluated,
        )
        for item in memory.list_runs()
    ]


@router.get("/memory/candidates", response_model=list[ResearchMemoryCandidateResponse])
async def get_research_memory_candidates() -> list[ResearchMemoryCandidateResponse]:
    memory = get_research_memory()
    return [
        ResearchMemoryCandidateResponse(
            laboratory_run_id=item.laboratory_run_id,
            candidate_id=item.candidate_id,
            originating_agent=item.originating_agent,
            parameter_set=dict(item.parameter_set),
            evaluation_summary=item.evaluation_summary,
            quality_score=item.quality_score,
            tournament_rank=item.tournament_rank,
            status=item.status,
            parent_candidate_id=item.parent_candidate_id,
            generation=item.generation,
            mutation_reason=item.mutation_reason,
            parameter_diff=[
                ResearchMemoryCandidateResponse.ParameterDiffResponse(
                    parameter_name=diff.parameter_name,
                    previous_value=diff.previous_value,
                    new_value=diff.new_value,
                )
                for diff in item.parameter_diff
            ],
        )
        for item in memory.list_candidates()
    ]


@router.post("/evolve", response_model=EvolutionResponse)
async def evolve_research_candidates(request: EvolutionRequest) -> EvolutionResponse:
    memory = get_research_memory()
    engine = get_evolution_engine()

    try:
        run = engine.evolve(
            memory_candidates=memory.list_candidates(),
            parent_candidate_id=request.parent_candidate_id,
            generation_limit=request.generation_limit,
        )
    except ParentCandidateNotFoundError:
        raise NotFoundError(
            message="Parent candidate not found",
            details={"parent_candidate_id": str(request.parent_candidate_id)},
        )

    memory.record_evolved_candidates(descendants=list(run.descendants))

    return EvolutionResponse(
        generated_count=run.generated_count,
        descendants=[
            EvolvedCandidateResponse(
                candidate_id=item.candidate_id,
                parent_candidate_id=item.parent_candidate_id,
                generation=item.generation,
                mutation_reason=item.mutation_reason,
                parameter_diff=[
                    EvolutionMutationResponse(
                        parameter_name=diff.parameter_name,
                        previous_value=diff.previous_value,
                        new_value=diff.new_value,
                    )
                    for diff in item.parameter_diff
                ],
                parameter_set=dict(item.parameter_set),
                generated_at=item.generated_at,
                quality_score=item.quality_score,
                tournament_rank=item.tournament_rank,
                status=item.status,
            )
            for item in run.descendants
        ],
    )


@router.get("/evolution-analytics", response_model=EvolutionAnalyticsResponse)
async def get_evolution_analytics() -> EvolutionAnalyticsResponse:
    service = get_evolution_analytics_service()
    summary = service.build_summary()

    return EvolutionAnalyticsResponse(
        total_laboratory_runs=summary.total_laboratory_runs,
        total_candidates_generated=summary.total_candidates_generated,
        total_evolved_candidates=summary.total_evolved_candidates,
        average_quality_score=summary.average_quality_score,
        best_quality_score=summary.best_quality_score,
        best_candidate=(
            None
            if summary.best_candidate is None
            else EvolutionAnalyticsBestCandidateResponse(
                candidate_id=summary.best_candidate.candidate_id,
                quality_score=summary.best_candidate.quality_score,
                tournament_rank=summary.best_candidate.tournament_rank,
                originating_agent=summary.best_candidate.originating_agent,
            )
        ),
        successful_mutations=summary.successful_mutations,
        unsuccessful_mutations=summary.unsuccessful_mutations,
        generation_distribution=[
            EvolutionAnalyticsGenerationDistributionResponse(
                generation=item.generation,
                count=item.count,
            )
            for item in summary.generation_distribution
        ],
        lineage_depth=summary.lineage_depth,
        top_research_agent=summary.top_research_agent,
        quality_score_over_time=[
            EvolutionAnalyticsQualityPointResponse(
                sequence=item.sequence,
                quality_score=item.quality_score,
            )
            for item in summary.quality_score_over_time
        ],
        candidates_generated_per_laboratory_run=[
            EvolutionAnalyticsRunPointResponse(
                laboratory_run_id=item.laboratory_run_id,
                candidates_generated=item.candidates_generated,
            )
            for item in summary.candidates_generated_per_laboratory_run
        ],
        mutation_success_rate=EvolutionAnalyticsMutationSuccessRateResponse(
            successful_mutations=summary.successful_mutations,
            unsuccessful_mutations=summary.unsuccessful_mutations,
            success_rate_percent=summary.mutation_success_rate,
        ),
        research_agent_leaderboard=[
            EvolutionAnalyticsAgentLeaderboardResponse(
                agent_name=item.agent_name,
                average_quality_score=item.average_quality_score,
                best_quality_score=item.best_quality_score,
                total_candidates=item.total_candidates,
            )
            for item in summary.research_agent_leaderboard
        ],
        largest_lineage_tree=EvolutionAnalyticsLargestLineageTreeResponse(
            root_candidate_id=summary.largest_lineage_tree.root_candidate_id,
            lineage_depth=summary.largest_lineage_tree.lineage_depth,
            descendant_count=summary.largest_lineage_tree.descendant_count,
        ),
    )
