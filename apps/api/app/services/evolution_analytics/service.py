from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
import uuid

from app.services.evolution_analytics.interface import (
    EvolutionAnalyticsAgentLeaderboardItem,
    EvolutionAnalyticsBestCandidate,
    EvolutionAnalyticsGenerationDistributionItem,
    EvolutionAnalyticsLargestLineageTree,
    EvolutionAnalyticsQualityPoint,
    EvolutionAnalyticsRunPoint,
    EvolutionAnalyticsSummary,
)
from app.services.research_memory.interface import ResearchMemoryCandidateRecord, ResearchMemoryLaboratoryRunRecord
from app.services.research_memory.registry import get_research_memory


class EvolutionAnalyticsService:
    def __init__(
        self,
        *,
        runs_provider: Callable[[], tuple[ResearchMemoryLaboratoryRunRecord, ...]] | None = None,
        candidates_provider: Callable[[], tuple[ResearchMemoryCandidateRecord, ...]] | None = None,
    ) -> None:
        memory = get_research_memory()
        self._runs_provider = runs_provider or memory.list_runs
        self._candidates_provider = candidates_provider or memory.list_candidates

    def build_summary(self) -> EvolutionAnalyticsSummary:
        runs_desc = list(self._runs_provider())
        candidates_desc = list(self._candidates_provider())

        runs = list(reversed(runs_desc))
        candidates = list(reversed(candidates_desc))
        scored = [item for item in candidates if item.quality_score is not None]
        evolved = [item for item in candidates if item.parent_candidate_id is not None or item.generation > 1]

        average_quality_score = (
            None
            if not scored
            else round(sum(int(item.quality_score or 0) for item in scored) / len(scored), 2)
        )
        best = _resolve_best_candidate(scored)
        generation_distribution = _resolve_generation_distribution(candidates)
        lineage_depth = max((item.generation for item in candidates), default=0)

        successful_mutations, unsuccessful_mutations = _resolve_mutation_outcomes(candidates)
        mutation_total = successful_mutations + unsuccessful_mutations
        mutation_success_rate = 0.0 if mutation_total == 0 else round((successful_mutations / mutation_total) * 100, 2)

        top_research_agent, leaderboard = _resolve_agent_analytics(candidates)
        largest_tree = _resolve_largest_lineage_tree(candidates)

        return EvolutionAnalyticsSummary(
            total_laboratory_runs=len(runs),
            total_candidates_generated=sum(item.candidates_generated for item in runs),
            total_evolved_candidates=len(evolved),
            average_quality_score=average_quality_score,
            best_quality_score=None if best is None else best.quality_score,
            best_candidate=best,
            successful_mutations=successful_mutations,
            unsuccessful_mutations=unsuccessful_mutations,
            generation_distribution=generation_distribution,
            lineage_depth=lineage_depth,
            top_research_agent=top_research_agent,
            quality_score_over_time=tuple(
                EvolutionAnalyticsQualityPoint(sequence=index, quality_score=int(item.quality_score or 0))
                for index, item in enumerate(scored, start=1)
            ),
            candidates_generated_per_laboratory_run=tuple(
                EvolutionAnalyticsRunPoint(
                    laboratory_run_id=item.laboratory_run_id,
                    candidates_generated=item.candidates_generated,
                )
                for item in runs
            ),
            mutation_success_rate=mutation_success_rate,
            research_agent_leaderboard=leaderboard,
            largest_lineage_tree=largest_tree,
        )


def _resolve_best_candidate(scored: list[ResearchMemoryCandidateRecord]) -> EvolutionAnalyticsBestCandidate | None:
    if not scored:
        return None

    best = max(
        scored,
        key=lambda item: (
            int(item.quality_score or 0),
            -(item.tournament_rank or 999999),
            str(item.candidate_id),
        ),
    )
    return EvolutionAnalyticsBestCandidate(
        candidate_id=best.candidate_id,
        quality_score=int(best.quality_score or 0),
        tournament_rank=best.tournament_rank,
        originating_agent=best.originating_agent,
    )


def _resolve_generation_distribution(
    candidates: list[ResearchMemoryCandidateRecord],
) -> tuple[EvolutionAnalyticsGenerationDistributionItem, ...]:
    counts = Counter(item.generation for item in candidates)
    return tuple(
        EvolutionAnalyticsGenerationDistributionItem(generation=generation, count=counts[generation])
        for generation in sorted(counts)
    )


def _resolve_mutation_outcomes(candidates: list[ResearchMemoryCandidateRecord]) -> tuple[int, int]:
    by_id = {item.candidate_id: item for item in candidates}
    successful = 0
    unsuccessful = 0

    for item in candidates:
        if item.parent_candidate_id is None:
            continue

        parent = by_id.get(item.parent_candidate_id)
        if parent is None or parent.quality_score is None or item.quality_score is None:
            continue

        if int(item.quality_score) >= int(parent.quality_score):
            successful += 1
        else:
            unsuccessful += 1

    return successful, unsuccessful


def _resolve_agent_analytics(
    candidates: list[ResearchMemoryCandidateRecord],
) -> tuple[str | None, tuple[EvolutionAnalyticsAgentLeaderboardItem, ...]]:
    grouped: dict[str, list[ResearchMemoryCandidateRecord]] = defaultdict(list)
    for item in candidates:
        grouped[item.originating_agent].append(item)

    leaderboard: list[EvolutionAnalyticsAgentLeaderboardItem] = []
    for agent_name, items in grouped.items():
        scores = [int(item.quality_score) for item in items if item.quality_score is not None]
        leaderboard.append(
            EvolutionAnalyticsAgentLeaderboardItem(
                agent_name=agent_name,
                average_quality_score=None if not scores else round(sum(scores) / len(scores), 2),
                best_quality_score=None if not scores else max(scores),
                total_candidates=len(items),
            )
        )

    ordered = sorted(
        leaderboard,
        key=lambda item: (
            -(item.average_quality_score if item.average_quality_score is not None else -1),
            -(item.best_quality_score if item.best_quality_score is not None else -1),
            -item.total_candidates,
            item.agent_name,
        ),
    )
    top = None if not ordered else ordered[0].agent_name
    return top, tuple(ordered)


def _resolve_largest_lineage_tree(
    candidates: list[ResearchMemoryCandidateRecord],
) -> EvolutionAnalyticsLargestLineageTree:
    by_id = {item.candidate_id: item for item in candidates}
    descendants_per_root: dict[uuid.UUID, int] = defaultdict(int)
    depth_per_root: dict[uuid.UUID, int] = defaultdict(int)

    for item in candidates:
        if item.parent_candidate_id is None:
            continue

        root_id = _resolve_root(item=item, by_id=by_id)
        descendants_per_root[root_id] += 1
        depth_per_root[root_id] = max(depth_per_root[root_id], item.generation)

    if not descendants_per_root:
        return EvolutionAnalyticsLargestLineageTree(
            root_candidate_id=None,
            lineage_depth=0,
            descendant_count=0,
        )

    root_id = max(
        descendants_per_root,
        key=lambda item: (
            descendants_per_root[item],
            depth_per_root[item],
            str(item),
        ),
    )
    return EvolutionAnalyticsLargestLineageTree(
        root_candidate_id=root_id,
        lineage_depth=depth_per_root[root_id],
        descendant_count=descendants_per_root[root_id],
    )


def _resolve_root(*, item: ResearchMemoryCandidateRecord, by_id: dict[uuid.UUID, ResearchMemoryCandidateRecord]) -> uuid.UUID:
    current = item
    seen: set[uuid.UUID] = set()

    while current.parent_candidate_id is not None and current.parent_candidate_id not in seen:
        seen.add(current.candidate_id)
        parent = by_id.get(current.parent_candidate_id)
        if parent is None:
            return current.parent_candidate_id
        current = parent

    return current.candidate_id
