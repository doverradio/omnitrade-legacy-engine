from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from app.services.ai_coach.deterministic import evaluate_decision_quality_v0
from app.services.candidate_evaluation.interface import CandidateEvaluation
from app.services.decision_intelligence.deterministic import StrategyEvidence, build_decision_intelligence_recommendation_v1
from app.services.decision_quality.deterministic import evaluate_replay_result_v0
from app.services.replay.interface import ReplayResult
from app.services.research_agents.interface import StrategyCandidate
from app.services.tournament.deterministic import build_tournament_snapshot_v1, replay_variance_from_confidence
from app.services.tournament.interface import TournamentStrategyEvidence


_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000006")


class CandidateNotFoundError(LookupError):
    pass


def resolve_candidate_by_id_v1(*, candidate_id: uuid.UUID, candidates: list[StrategyCandidate]) -> StrategyCandidate:
    candidate = next((item for item in candidates if item.candidate_id == candidate_id), None)
    if candidate is None:
        raise CandidateNotFoundError(str(candidate_id))
    return candidate


def build_candidate_evaluation_v1(
    *,
    candidate: StrategyCandidate,
    all_candidates: list[StrategyCandidate],
) -> CandidateEvaluation:
    replay_result = _build_replay_result_from_candidate(candidate)
    quality_result = evaluate_replay_result_v0(replay_result=replay_result)
    coach = evaluate_decision_quality_v0(decision_quality_result=quality_result)

    strategy_evidence = [
        StrategyEvidence(
            strategy_name=item.strategy_name,
            replay_result=_build_replay_result_from_candidate(item),
        )
        for item in all_candidates
    ]
    intelligence = build_decision_intelligence_recommendation_v1(strategy_evidence=strategy_evidence)

    tournament_snapshot = build_tournament_snapshot_v1(
        strategies=[
            _build_tournament_evidence(item)
            for item in all_candidates
        ]
    )
    rank_by_strategy = {
        entry.strategy_name: entry.overall_rank
        for entry in tournament_snapshot.ranking
    }

    evaluation_id = uuid.uuid5(_NAMESPACE, str(candidate.candidate_id))
    return CandidateEvaluation(
        evaluation_id=evaluation_id,
        candidate_id=candidate.candidate_id,
        replay_status="COMPLETED",
        decision_quality_score=quality_result.quality_score,
        ai_coach_summary=coach.summary,
        decision_intelligence_summary=intelligence.recommendation_summary,
        tournament_rank=rank_by_strategy.get(candidate.strategy_name),
        promotion_eligible=False,
    )


def build_candidate_evaluations_batch_v1(
    *,
    candidates: list[StrategyCandidate],
    selected_candidate_ids: list[uuid.UUID] | None,
    limit: int | None,
) -> list[CandidateEvaluation]:
    filtered_candidates = candidates
    if selected_candidate_ids is not None:
        filtered_candidates = [
            resolve_candidate_by_id_v1(candidate_id=candidate_id, candidates=candidates)
            for candidate_id in selected_candidate_ids
        ]

    if limit is not None:
        capped_limit = max(limit, 0)
        filtered_candidates = filtered_candidates[:capped_limit]

    return [
        build_candidate_evaluation_v1(
            candidate=candidate,
            all_candidates=candidates,
        )
        for candidate in filtered_candidates
    ]


def _build_replay_result_from_candidate(candidate: StrategyCandidate) -> ReplayResult:
    confidence = _confidence_from_candidate(candidate)
    action = _action_from_candidate(candidate)
    return ReplayResult(
        replay_id=uuid.uuid5(_NAMESPACE, f"replay:{candidate.candidate_id}"),
        replay_agent_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        decision_package_id=f"candidate:{candidate.candidate_id}",
        replay_timestamp=datetime.now(timezone.utc),
        reconstructed_action=action,
        confidence=confidence,
        supporting_evidence=(
            {
                "type": "research_candidate",
                "candidate_id": str(candidate.candidate_id),
                "strategy_name": candidate.strategy_name,
                "originating_agent": candidate.originating_agent,
            },
        ),
        explanation="Deterministic research-only replay derived from candidate configuration.",
        metadata={
            "candidate_id": str(candidate.candidate_id),
            "original_action": action,
            "original_confidence": str(confidence),
            "replay_duration_ms": 1,
            "research_only": True,
        },
    )


def _build_tournament_evidence(candidate: StrategyCandidate) -> TournamentStrategyEvidence:
    replay_result = _build_replay_result_from_candidate(candidate)
    quality = evaluate_replay_result_v0(replay_result=replay_result)
    replay_variance = replay_variance_from_confidence(
        original_confidence=replay_result.metadata.get("original_confidence"),
        reconstructed_confidence=replay_result.confidence,
    )

    return TournamentStrategyEvidence(
        strategy_name=candidate.strategy_name,
        quality_score=quality.quality_score,
        replay_variance=replay_variance,
        replay_count=1,
        paper_trades=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        win_rate=None,
    )


def _confidence_from_candidate(candidate: StrategyCandidate) -> Decimal:
    raw = str(candidate.candidate_id.int % 100)
    scaled = Decimal(raw) / Decimal("100")
    return max(Decimal("0.30"), scaled)


def _action_from_candidate(candidate: StrategyCandidate) -> str:
    options = ("BUY", "SELL", "HOLD")
    return options[candidate.candidate_id.int % len(options)]
