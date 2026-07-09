from __future__ import annotations

import uuid

from pydantic import BaseModel


class EvolutionAnalyticsBestCandidateResponse(BaseModel):
    candidate_id: uuid.UUID
    quality_score: int
    tournament_rank: int | None
    originating_agent: str


class EvolutionAnalyticsGenerationDistributionResponse(BaseModel):
    generation: int
    count: int


class EvolutionAnalyticsQualityPointResponse(BaseModel):
    sequence: int
    quality_score: int


class EvolutionAnalyticsRunPointResponse(BaseModel):
    laboratory_run_id: uuid.UUID
    candidates_generated: int


class EvolutionAnalyticsMutationSuccessRateResponse(BaseModel):
    successful_mutations: int
    unsuccessful_mutations: int
    success_rate_percent: float


class EvolutionAnalyticsAgentLeaderboardResponse(BaseModel):
    agent_name: str
    average_quality_score: float | None
    best_quality_score: int | None
    total_candidates: int


class EvolutionAnalyticsLargestLineageTreeResponse(BaseModel):
    root_candidate_id: uuid.UUID | None
    lineage_depth: int
    descendant_count: int


class EvolutionAnalyticsResponse(BaseModel):
    total_laboratory_runs: int
    total_candidates_generated: int
    total_evolved_candidates: int
    average_quality_score: float | None
    best_quality_score: int | None
    best_candidate: EvolutionAnalyticsBestCandidateResponse | None
    successful_mutations: int
    unsuccessful_mutations: int
    generation_distribution: list[EvolutionAnalyticsGenerationDistributionResponse]
    lineage_depth: int
    top_research_agent: str | None
    quality_score_over_time: list[EvolutionAnalyticsQualityPointResponse]
    candidates_generated_per_laboratory_run: list[EvolutionAnalyticsRunPointResponse]
    mutation_success_rate: EvolutionAnalyticsMutationSuccessRateResponse
    research_agent_leaderboard: list[EvolutionAnalyticsAgentLeaderboardResponse]
    largest_lineage_tree: EvolutionAnalyticsLargestLineageTreeResponse
