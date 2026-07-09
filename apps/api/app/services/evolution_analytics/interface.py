from __future__ import annotations

from dataclasses import dataclass
import uuid


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsBestCandidate:
    candidate_id: uuid.UUID
    quality_score: int
    tournament_rank: int | None
    originating_agent: str


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsGenerationDistributionItem:
    generation: int
    count: int


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsQualityPoint:
    sequence: int
    quality_score: int


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsRunPoint:
    laboratory_run_id: uuid.UUID
    candidates_generated: int


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsAgentLeaderboardItem:
    agent_name: str
    average_quality_score: float | None
    best_quality_score: int | None
    total_candidates: int


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsLargestLineageTree:
    root_candidate_id: uuid.UUID | None
    lineage_depth: int
    descendant_count: int


@dataclass(frozen=True, slots=True)
class EvolutionAnalyticsSummary:
    total_laboratory_runs: int
    total_candidates_generated: int
    total_evolved_candidates: int
    average_quality_score: float | None
    best_quality_score: int | None
    best_candidate: EvolutionAnalyticsBestCandidate | None
    successful_mutations: int
    unsuccessful_mutations: int
    generation_distribution: tuple[EvolutionAnalyticsGenerationDistributionItem, ...]
    lineage_depth: int
    top_research_agent: str | None
    quality_score_over_time: tuple[EvolutionAnalyticsQualityPoint, ...]
    candidates_generated_per_laboratory_run: tuple[EvolutionAnalyticsRunPoint, ...]
    mutation_success_rate: float
    research_agent_leaderboard: tuple[EvolutionAnalyticsAgentLeaderboardItem, ...]
    largest_lineage_tree: EvolutionAnalyticsLargestLineageTree
