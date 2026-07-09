from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import uuid

from app.services.candidate_evaluation.deterministic import build_candidate_evaluations_batch_v1
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.evolution.interface import EvolvedCandidate, EvolutionMutation, EvolutionRunResult
from app.services.research_agents.interface import StrategyCandidate
from app.services.research_memory.interface import ResearchMemoryCandidateRecord


_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000007")


class ParentCandidateNotFoundError(LookupError):
    pass


class EvolutionEngine:
    def __init__(self) -> None:
        self._descendants: list[EvolvedCandidate] = []

    def clear(self) -> None:
        self._descendants.clear()

    def list_descendants(self) -> tuple[EvolvedCandidate, ...]:
        return tuple(reversed(self._descendants))

    def evolve(
        self,
        *,
        memory_candidates: tuple[ResearchMemoryCandidateRecord, ...],
        parent_candidate_id: uuid.UUID | None,
        generation_limit: int | None,
    ) -> EvolutionRunResult:
        memory_map = {item.candidate_id: item for item in memory_candidates}
        descendant_map = {item.candidate_id: item for item in self._descendants}

        if parent_candidate_id is not None and parent_candidate_id not in memory_map and parent_candidate_id not in descendant_map:
            raise ParentCandidateNotFoundError(str(parent_candidate_id))

        parent_sources = self._resolve_parents(
            memory_candidates=memory_candidates,
            memory_map=memory_map,
            descendant_map=descendant_map,
            parent_candidate_id=parent_candidate_id,
        )

        generated_at = datetime.now(timezone.utc)
        descendants = self._build_descendants(
            parent_sources=parent_sources,
            generated_at=generated_at,
        )

        if generation_limit is not None:
            descendants = descendants[: max(generation_limit, 0)]

        evaluations = self._evaluate_descendants(descendants)
        evaluated_descendants = self._attach_evaluations(
            descendants=descendants,
            evaluations=evaluations,
        )

        self._descendants.extend(evaluated_descendants)
        return EvolutionRunResult(
            generated_count=len(evaluated_descendants),
            descendants=tuple(evaluated_descendants),
        )

    def _resolve_parents(
        self,
        *,
        memory_candidates: tuple[ResearchMemoryCandidateRecord, ...],
        memory_map: dict[uuid.UUID, ResearchMemoryCandidateRecord],
        descendant_map: dict[uuid.UUID, EvolvedCandidate],
        parent_candidate_id: uuid.UUID | None,
    ) -> list[_ParentSource]:
        if parent_candidate_id is not None:
            if parent_candidate_id in memory_map:
                return [_parent_from_memory(memory_map[parent_candidate_id])]
            return [_parent_from_descendant(descendant_map[parent_candidate_id])]

        top_candidates = sorted(
            [item for item in memory_candidates if item.quality_score is not None],
            key=lambda item: (
                -(item.quality_score or 0),
                item.tournament_rank or 999999,
                str(item.candidate_id),
            ),
        )

        selected = top_candidates[:2]
        return [_parent_from_memory(item) for item in selected]

    def _build_descendants(self, *, parent_sources: list[_ParentSource], generated_at: datetime) -> list[EvolvedCandidate]:
        descendants: list[EvolvedCandidate] = []

        for parent in parent_sources:
            mutations = _mutations_for_parameters(parent.parameter_set)
            for index, mutation in enumerate(mutations, start=1):
                mutated_parameter_set = dict(parent.parameter_set)
                for diff in mutation.parameter_diff:
                    mutated_parameter_set[diff.parameter_name] = diff.new_value

                key = (
                    f"{parent.parent_candidate_id}:{parent.generation + 1}:"
                    f"{mutation.reason}:{index}:{_stable_dict(mutated_parameter_set)}"
                )
                candidate_id = uuid.uuid5(_NAMESPACE, key)
                descendants.append(
                    EvolvedCandidate(
                        candidate_id=candidate_id,
                        parent_candidate_id=parent.parent_candidate_id,
                        generation=parent.generation + 1,
                        mutation_reason=mutation.reason,
                        parameter_diff=mutation.parameter_diff,
                        parameter_set=mutated_parameter_set,
                        strategy_name=f"Evolved {parent.strategy_name} g{parent.generation + 1}-{index}",
                        originating_agent=parent.originating_agent,
                        generated_at=generated_at,
                        quality_score=None,
                        tournament_rank=None,
                        status="EVOLVED",
                    )
                )

        return descendants

    def _evaluate_descendants(self, descendants: list[EvolvedCandidate]) -> list[CandidateEvaluation]:
        if not descendants:
            return []

        candidate_inputs = [
            StrategyCandidate(
                candidate_id=item.candidate_id,
                generated_at=item.generated_at,
                originating_agent=item.originating_agent,
                strategy_name=item.strategy_name,
                description=f"Evolved descendant from {item.parent_candidate_id}",
                parameter_set=dict(item.parameter_set),
                rationale=item.mutation_reason,
                status=item.status,
            )
            for item in descendants
        ]

        return build_candidate_evaluations_batch_v1(
            candidates=candidate_inputs,
            selected_candidate_ids=[item.candidate_id for item in candidate_inputs],
            limit=None,
        )

    def _attach_evaluations(
        self,
        *,
        descendants: list[EvolvedCandidate],
        evaluations: list[CandidateEvaluation],
    ) -> list[EvolvedCandidate]:
        evaluation_by_id = {item.candidate_id: item for item in evaluations}
        enriched: list[EvolvedCandidate] = []

        for candidate in descendants:
            evaluation = evaluation_by_id.get(candidate.candidate_id)
            enriched.append(
                EvolvedCandidate(
                    candidate_id=candidate.candidate_id,
                    parent_candidate_id=candidate.parent_candidate_id,
                    generation=candidate.generation,
                    mutation_reason=candidate.mutation_reason,
                    parameter_diff=candidate.parameter_diff,
                    parameter_set=dict(candidate.parameter_set),
                    strategy_name=candidate.strategy_name,
                    originating_agent=candidate.originating_agent,
                    generated_at=candidate.generated_at,
                    quality_score=None if evaluation is None else evaluation.decision_quality_score,
                    tournament_rank=None if evaluation is None else evaluation.tournament_rank,
                    status="EVALUATED" if evaluation is not None else candidate.status,
                )
            )

        return enriched


@dataclass(frozen=True, slots=True)
class _ParentSource:
    parent_candidate_id: uuid.UUID
    generation: int
    strategy_name: str
    originating_agent: str
    parameter_set: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _MutationSpec:
    reason: str
    parameter_diff: tuple[EvolutionMutation, ...]


def _parent_from_memory(candidate: ResearchMemoryCandidateRecord) -> _ParentSource:
    return _ParentSource(
        parent_candidate_id=candidate.candidate_id,
        generation=1,
        strategy_name=str(candidate.candidate_id),
        originating_agent=candidate.originating_agent,
        parameter_set=dict(candidate.parameter_set),
    )


def _parent_from_descendant(candidate: EvolvedCandidate) -> _ParentSource:
    return _ParentSource(
        parent_candidate_id=candidate.candidate_id,
        generation=candidate.generation,
        strategy_name=candidate.strategy_name,
        originating_agent=candidate.originating_agent,
        parameter_set=dict(candidate.parameter_set),
    )


def _mutations_for_parameters(parameter_set: dict[str, Any]) -> list[_MutationSpec]:
    specs: list[_MutationSpec] = []

    rsi_period = _int_value(parameter_set.get("rsi_period"))
    if rsi_period is not None:
        minus_two = max(rsi_period - 2, 1)
        plus_two = rsi_period + 2
        specs.append(
            _MutationSpec(
                reason=f"rsi_period {rsi_period}->{minus_two}",
                parameter_diff=(
                    EvolutionMutation(
                        parameter_name="rsi_period",
                        previous_value=rsi_period,
                        new_value=minus_two,
                    ),
                ),
            )
        )
        specs.append(
            _MutationSpec(
                reason=f"rsi_period {rsi_period}->{plus_two}",
                parameter_diff=(
                    EvolutionMutation(
                        parameter_name="rsi_period",
                        previous_value=rsi_period,
                        new_value=plus_two,
                    ),
                ),
            )
        )

    fast_period = _int_value(parameter_set.get("fast_period"))
    slow_period = _int_value(parameter_set.get("slow_period"))
    if fast_period is not None and slow_period is not None:
        fast_minus_two = max(fast_period - 2, 1)
        fast_plus_two = fast_period + 2
        slow_minus_ten = max(slow_period - 10, fast_period + 1)
        slow_plus_ten = slow_period + 10

        specs.append(
            _MutationSpec(
                reason=f"fast_period {fast_period}->{fast_minus_two}",
                parameter_diff=(
                    EvolutionMutation(
                        parameter_name="fast_period",
                        previous_value=fast_period,
                        new_value=fast_minus_two,
                    ),
                ),
            )
        )
        specs.append(
            _MutationSpec(
                reason=f"fast_period {fast_period}->{fast_plus_two}",
                parameter_diff=(
                    EvolutionMutation(
                        parameter_name="fast_period",
                        previous_value=fast_period,
                        new_value=fast_plus_two,
                    ),
                ),
            )
        )
        specs.append(
            _MutationSpec(
                reason=f"slow_period {slow_period}->{slow_minus_ten}",
                parameter_diff=(
                    EvolutionMutation(
                        parameter_name="slow_period",
                        previous_value=slow_period,
                        new_value=slow_minus_ten,
                    ),
                ),
            )
        )
        specs.append(
            _MutationSpec(
                reason=f"slow_period {slow_period}->{slow_plus_ten}",
                parameter_diff=(
                    EvolutionMutation(
                        parameter_name="slow_period",
                        previous_value=slow_period,
                        new_value=slow_plus_ten,
                    ),
                ),
            )
        )

    return specs


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _stable_dict(value: dict[str, Any]) -> str:
    return "|".join(
        f"{key}:{value[key]}"
        for key in sorted(value)
    )
