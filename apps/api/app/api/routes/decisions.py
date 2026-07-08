from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.models.signal import Signal
from app.services.arena.comparison import read_latest_arena_comparison_record
from app.services.arena.contracts import ArenaLeaderboardFilterContract
from app.services.arena.leaderboard import read_latest_arena_leaderboard_snapshot
from app.services.arena.tournaments import (
    read_arena_tournament_history_events,
    read_arena_tournament_lifecycle_state,
)
from app.services.decisions.coach import generate_ai_coach_batch_reviews
from app.services.decisions.explainability import read_decision_explainability
from app.services.decisions.recommendations import read_experiment_recommendations
from app.services.decisions.timeline import TimelineReadFilters, read_decision_timeline

router = APIRouter(prefix="/decisions", tags=["decisions"])

MAX_PAGE_SIZE = 200


@router.get("/arena-comparisons/latest")
async def get_latest_arena_comparison(
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID | None = Query(default=None),
    cycle_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    read_model = await read_latest_arena_comparison_record(
        db=db,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
    )

    if read_model is None:
        return {
            "comparison_scope": "cycle" if cycle_id is not None else "tournament" if tournament_id is not None else "competition",
            "competition_id": str(competition_id),
            "tournament_id": str(tournament_id) if tournament_id else None,
            "cycle_id": str(cycle_id) if cycle_id else None,
            "availability_state": "unavailable",
            "state_reason": "arena_comparison_unavailable",
            "comparison_hash": None,
            "compared_agent_ids": [],
            "comparison_timestamp": None,
            "agent_summaries": [],
            "portfolio_dimensions": {},
            "evidence_sources": {},
            "provenance": {},
        }

    return {
        "comparison_scope": read_model.comparison_scope,
        "competition_id": str(read_model.competition_id),
        "tournament_id": str(read_model.tournament_id) if read_model.tournament_id else None,
        "cycle_id": str(read_model.cycle_id) if read_model.cycle_id else None,
        "availability_state": "known",
        "state_reason": None,
        "comparison_hash": read_model.comparison_hash,
        "compared_agent_ids": [str(item) for item in read_model.compared_agent_ids],
        "comparison_timestamp": read_model.comparison_timestamp.isoformat(),
        "agent_summaries": [
            {
                "agent_id": str(item.agent_id),
                "decision_quality": {
                    "value": _decimal_to_str(item.decision_quality.value),
                    "status": item.decision_quality.status,
                    "reason": item.decision_quality.reason,
                },
                "explainability_support_ratio": {
                    "value": _decimal_to_str(item.explainability_support_ratio.value),
                    "status": item.explainability_support_ratio.status,
                    "reason": item.explainability_support_ratio.reason,
                },
                "counterfactual_correctness": {
                    "value": _decimal_to_str(item.counterfactual_correctness.value),
                    "status": item.counterfactual_correctness.status,
                    "reason": item.counterfactual_correctness.reason,
                },
                "evidence_provenance": item.evidence_provenance,
            }
            for item in read_model.agent_summaries
        ],
        "portfolio_dimensions": {
            key: {
                "value": _decimal_to_str(value.value),
                "status": value.status,
                "reason": value.reason,
            }
            for key, value in read_model.portfolio_dimensions.items()
        },
        "evidence_sources": read_model.evidence_sources,
        "provenance": read_model.provenance,
    }


@router.get("/arena-leaderboard/latest")
async def get_latest_arena_leaderboard(
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID | None = Query(default=None),
    cycle_id: uuid.UUID | None = Query(default=None),
    included_agent_ids: list[uuid.UUID] | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    availability_mode: str = Query(default="all", pattern="^(all|known_only)$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    filters = ArenaLeaderboardFilterContract(
        included_agent_ids=included_agent_ids,
        limit=limit,
        availability_mode=availability_mode,
    )

    read_model = await read_latest_arena_leaderboard_snapshot(
        db=db,
        competition_id=competition_id,
        tournament_id=tournament_id,
        cycle_id=cycle_id,
        filters=filters,
    )

    scope = "cycle" if cycle_id is not None else "tournament" if tournament_id is not None else "competition"
    if read_model is None:
        return {
            "snapshot_scope": scope,
            "competition_id": str(competition_id),
            "tournament_id": str(tournament_id) if tournament_id else None,
            "cycle_id": str(cycle_id) if cycle_id else None,
            "availability_state": "unavailable",
            "state_reason": "arena_leaderboard_unavailable",
            "ranking_hash": None,
            "ranking_methodology_version": None,
            "snapshot_timestamp": None,
            "filters": {
                "included_agent_ids": [str(item) for item in included_agent_ids] if included_agent_ids else None,
                "limit": limit,
                "availability_mode": availability_mode,
            },
            "entries": [],
            "evidence_sources": {},
            "provenance": {},
        }

    return {
        "snapshot_scope": read_model.snapshot_scope,
        "competition_id": str(read_model.competition_id),
        "tournament_id": str(read_model.tournament_id) if read_model.tournament_id else None,
        "cycle_id": str(read_model.cycle_id) if read_model.cycle_id else None,
        "availability_state": "known",
        "state_reason": None,
        "ranking_hash": read_model.ranking_hash,
        "ranking_methodology_version": read_model.ranking_methodology_version,
        "snapshot_timestamp": read_model.snapshot_timestamp.isoformat(),
        "filters": {
            "included_agent_ids": [str(item) for item in read_model.filters.included_agent_ids]
            if read_model.filters.included_agent_ids
            else None,
            "limit": read_model.filters.limit,
            "availability_mode": read_model.filters.availability_mode,
        },
        "entries": [
            {
                "rank": item.rank,
                "agent_id": str(item.agent_id),
                "composite_rank_score": {
                    "value": _decimal_to_str(item.composite_rank_score.value),
                    "status": item.composite_rank_score.status,
                    "reason": item.composite_rank_score.reason,
                },
                "decision_quality": {
                    "value": _decimal_to_str(item.decision_quality.value),
                    "status": item.decision_quality.status,
                    "reason": item.decision_quality.reason,
                },
                "profit": {
                    "value": _decimal_to_str(item.profit.value),
                    "status": item.profit.status,
                    "reason": item.profit.reason,
                },
                "drawdown": {
                    "value": _decimal_to_str(item.drawdown.value),
                    "status": item.drawdown.status,
                    "reason": item.drawdown.reason,
                },
                "fee_drag": {
                    "value": _decimal_to_str(item.fee_drag.value),
                    "status": item.fee_drag.status,
                    "reason": item.fee_drag.reason,
                },
                "consistency": {
                    "value": _decimal_to_str(item.consistency.value),
                    "status": item.consistency.status,
                    "reason": item.consistency.reason,
                },
                "risk_discipline": {
                    "value": _decimal_to_str(item.risk_discipline.value),
                    "status": item.risk_discipline.status,
                    "reason": item.risk_discipline.reason,
                },
                "explainability": {
                    "value": _decimal_to_str(item.explainability.value),
                    "status": item.explainability.status,
                    "reason": item.explainability.reason,
                },
                "evidence_provenance": item.evidence_provenance,
            }
            for item in read_model.entries
        ],
        "evidence_sources": read_model.evidence_sources,
        "provenance": read_model.provenance,
    }


@router.get("/arena-tournaments/history")
async def get_arena_tournament_history(
    competition_id: uuid.UUID,
    tournament_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    read_model = await read_arena_tournament_lifecycle_state(
        db=db,
        competition_id=competition_id,
        tournament_id=tournament_id,
    )
    history_events = await read_arena_tournament_history_events(
        db=db,
        competition_id=competition_id,
        tournament_id=tournament_id,
    )

    if read_model is None or not history_events:
        return {
            "competition_id": str(competition_id),
            "tournament_id": str(tournament_id),
            "availability_state": "unavailable",
            "state_reason": "arena_tournament_history_unavailable",
            "current_state": None,
            "latest_event_type": None,
            "latest_event_timestamp": None,
            "history_count": 0,
            "replay_metadata": {},
            "latest_schedule_payload": {},
            "latest_standings": [],
            "history": [],
        }

    return {
        "competition_id": str(competition_id),
        "tournament_id": str(tournament_id),
        "availability_state": "known",
        "state_reason": None,
        "current_state": read_model.current_state,
        "latest_event_type": read_model.latest_event_type,
        "latest_event_timestamp": read_model.latest_event_timestamp.isoformat(),
        "history_count": read_model.history_count,
        "replay_metadata": read_model.replay_metadata,
        "latest_schedule_payload": read_model.latest_schedule_payload,
        "latest_standings": [
            {
                "rank": item.rank,
                "agent_id": str(item.agent_id),
                "composite_score": {
                    "value": _decimal_to_str(item.composite_score.value),
                    "status": item.composite_score.status,
                    "reason": item.composite_score.reason,
                },
                "decision_quality": {
                    "value": _decimal_to_str(item.decision_quality.value),
                    "status": item.decision_quality.status,
                    "reason": item.decision_quality.reason,
                },
                "risk_discipline": {
                    "value": _decimal_to_str(item.risk_discipline.value),
                    "status": item.risk_discipline.status,
                    "reason": item.risk_discipline.reason,
                },
                "drawdown": {
                    "value": _decimal_to_str(item.drawdown.value),
                    "status": item.drawdown.status,
                    "reason": item.drawdown.reason,
                },
                "fee_drag": {
                    "value": _decimal_to_str(item.fee_drag.value),
                    "status": item.fee_drag.status,
                    "reason": item.fee_drag.reason,
                },
                "profit": {
                    "value": _decimal_to_str(item.profit.value),
                    "status": item.profit.status,
                    "reason": item.profit.reason,
                },
                "evidence_provenance": item.evidence_provenance,
            }
            for item in read_model.latest_standings
        ],
        "history": [
            {
                "history_record_id": str(item.history_record_id),
                "event_hash": item.event_hash,
                "sequence_number": item.sequence_number,
                "event_type": item.event_type,
                "lifecycle_state": item.lifecycle_state,
                "event_timestamp": item.event_timestamp.isoformat(),
                "schedule_payload": item.schedule_payload,
                "replay_metadata": item.replay_metadata,
                "tie_break_rules": item.tie_break_rules,
                "ordering_rules": item.ordering_rules,
                "standings": [
                    {
                        "rank": row.rank,
                        "agent_id": str(row.agent_id),
                        "composite_score": {
                            "value": _decimal_to_str(row.composite_score.value),
                            "status": row.composite_score.status,
                            "reason": row.composite_score.reason,
                        },
                        "decision_quality": {
                            "value": _decimal_to_str(row.decision_quality.value),
                            "status": row.decision_quality.status,
                            "reason": row.decision_quality.reason,
                        },
                        "risk_discipline": {
                            "value": _decimal_to_str(row.risk_discipline.value),
                            "status": row.risk_discipline.status,
                            "reason": row.risk_discipline.reason,
                        },
                        "drawdown": {
                            "value": _decimal_to_str(row.drawdown.value),
                            "status": row.drawdown.status,
                            "reason": row.drawdown.reason,
                        },
                        "fee_drag": {
                            "value": _decimal_to_str(row.fee_drag.value),
                            "status": row.fee_drag.status,
                            "reason": row.fee_drag.reason,
                        },
                        "profit": {
                            "value": _decimal_to_str(row.profit.value),
                            "status": row.profit.status,
                            "reason": row.profit.reason,
                        },
                        "evidence_provenance": row.evidence_provenance,
                    }
                    for row in item.standings
                ],
                "provenance": item.provenance,
            }
            for item in history_events
        ],
    }


@router.get("/timeline")
async def get_decision_timeline(
    account_id: uuid.UUID | None = Query(default=None),
    portfolio_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(start_time=start_time, end_time=end_time)
    resolved_account_id = _resolve_account_filter(account_id=account_id, portfolio_id=portfolio_id)

    filters = TimelineReadFilters(
        account_id=resolved_account_id,
        asset_id=asset_id,
        strategy_id=strategy_id,
        status=status,  # type: ignore[arg-type]
    )
    entries = await read_decision_timeline(db=db, filters=filters)

    if start_time is not None:
        entries = [item for item in entries if item.timestamp >= start_time]
    if end_time is not None:
        entries = [item for item in entries if item.timestamp <= end_time]

    items = [
        {
            "decision_id": str(item.decision_id),
            "timestamp": item.timestamp.isoformat(),
            "narrative": item.narrative,
            "status": item.status,
            "account_id": asdict(item.account_id),
            "asset_id": asdict(item.asset_id),
            "strategy_id": asdict(item.strategy_id),
            "source_lineage": item.source_lineage,
        }
        for item in entries
    ]
    return _paginate(items=items, page=page, page_size=page_size)


@router.get("/records")
async def list_decision_records(
    asset_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    trade_accepted: bool | None = Query(default=None),
    review_status: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(start_time=start_time, end_time=end_time)

    statement = select(DecisionRecord).order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc())
    if trade_accepted is not None:
        statement = statement.where(DecisionRecord.trade_accepted.is_(trade_accepted))
    if review_status is not None:
        statement = statement.where(DecisionRecord.review_status == review_status)
    if start_time is not None:
        statement = statement.where(DecisionRecord.timestamp >= start_time)
    if end_time is not None:
        statement = statement.where(DecisionRecord.timestamp <= end_time)

    decision_rows = list((await db.execute(statement)).scalars().all())

    signal_ids: list[uuid.UUID] = []
    for row in decision_rows:
        signal_id = _extract_primary_signal_id(row)
        if signal_id is not None:
            signal_ids.append(signal_id)

    signal_map: dict[uuid.UUID, Signal] = {}
    if signal_ids:
        signal_rows = list(
            (
                await db.execute(
                    select(Signal).where(Signal.id.in_(sorted(set(signal_ids))))
                )
            ).scalars().all()
        )
        signal_map = {item.id: item for item in signal_rows}

    filtered_rows: list[DecisionRecord] = []
    for row in decision_rows:
        linked_signal = _linked_signal(row=row, signal_map=signal_map)
        record_asset_id = _coerce_uuid_from_asset(row.asset.get("asset_id") if isinstance(row.asset, dict) else None)
        resolved_asset_id = linked_signal.asset_id if linked_signal is not None else record_asset_id

        if asset_id is not None and resolved_asset_id != asset_id:
            continue
        if strategy_id is not None:
            if linked_signal is None or linked_signal.strategy_id != strategy_id:
                continue
        if action is not None:
            resolved_action = linked_signal.action if linked_signal is not None else _record_action(row)
            if resolved_action != action:
                continue

        filtered_rows.append(row)

    filtered_decision_ids = [row.decision_id for row in filtered_rows]
    filtered_decision_id_set = set(filtered_decision_ids)

    latest_quality_by_decision: dict[uuid.UUID, DecisionQualityScore] = {}
    counterfactuals_by_decision: dict[uuid.UUID, list[DecisionCounterfactualResult]] = {}
    recommendation_history_by_decision: dict[uuid.UUID, dict[str, Any]] = {}

    if filtered_decision_ids:
        quality_rows = list(
            (
                await db.execute(
                    select(DecisionQualityScore)
                    .where(DecisionQualityScore.decision_id.in_(filtered_decision_ids))
                    .order_by(
                        DecisionQualityScore.decision_id.asc(),
                        DecisionQualityScore.created_at.desc(),
                        DecisionQualityScore.id.desc(),
                    )
                )
            ).scalars().all()
        )
        for quality_row in quality_rows:
            if quality_row.decision_id not in latest_quality_by_decision:
                latest_quality_by_decision[quality_row.decision_id] = quality_row

        counterfactual_rows = list(
            (
                await db.execute(
                    select(DecisionCounterfactualResult)
                    .where(DecisionCounterfactualResult.decision_id.in_(filtered_decision_ids))
                    .order_by(
                        DecisionCounterfactualResult.decision_id.asc(),
                        DecisionCounterfactualResult.evaluated_at.desc(),
                        DecisionCounterfactualResult.horizon_minutes.asc(),
                        DecisionCounterfactualResult.id.asc(),
                    )
                )
            ).scalars().all()
        )
        for counterfactual in counterfactual_rows:
            counterfactuals_by_decision.setdefault(counterfactual.decision_id, []).append(counterfactual)

        recommendation_rows = await read_experiment_recommendations(db=db)
        for recommendation_row in recommendation_rows:
            recommendation_created_at = _coerce_datetime(recommendation_row.created_at)

            for decision_id_value in recommendation_row.originating_decision_ids:
                decision_id = _coerce_uuid_string(decision_id_value)
                if decision_id is None or decision_id not in filtered_decision_id_set:
                    continue

                summary = recommendation_history_by_decision.get(decision_id)
                if summary is None:
                    summary = {
                        "count": 0,
                        "latest_recommendation_at": recommendation_created_at.isoformat(),
                        "latest_recommendation_type": recommendation_row.recommendation_type,
                        "latest_recommendation_state": recommendation_row.evidence_state,
                        "recommendation_ids": [],
                    }
                    recommendation_history_by_decision[decision_id] = summary

                summary["count"] += 1
                summary["recommendation_ids"].append(str(recommendation_row.recommendation_id))
                latest_at = summary.get("latest_recommendation_at")
                if not isinstance(latest_at, str) or recommendation_created_at.isoformat() > latest_at:
                    summary["latest_recommendation_at"] = recommendation_created_at.isoformat()
                    summary["latest_recommendation_type"] = recommendation_row.recommendation_type
                    summary["latest_recommendation_state"] = recommendation_row.evidence_state

    items: list[dict[str, Any]] = []
    for row in filtered_rows:
        linked_signal = _linked_signal(row=row, signal_map=signal_map)
        items.append(
            {
                "decision_id": str(row.decision_id),
                "timestamp": row.timestamp.isoformat(),
                "asset_id": row.asset.get("asset_id") if isinstance(row.asset, dict) else None,
                "trade_accepted": row.trade_accepted,
                "review_status": row.review_status,
                "outcome": row.outcome,
                "action": _record_action(row),
                "decision_explanation": {
                    "trade_rejected_reason": row.trade_rejected_reason,
                    "ai_reflection": row.ai_reflection,
                    "post_trade_notes": row.post_trade_notes,
                    "human_notes": row.human_notes,
                    "lessons_learned": row.lessons_learned,
                },
                "linked_signal": {
                    "signal_id": str(linked_signal.id) if linked_signal is not None else _record_signal_id_string(row),
                    "strategy_id": str(linked_signal.strategy_id) if linked_signal is not None else None,
                    "asset_id": str(linked_signal.asset_id) if linked_signal is not None else None,
                    "action": linked_signal.action if linked_signal is not None else _record_action(row),
                    "status": linked_signal.status if linked_signal is not None else _record_signal_status(row),
                    "signal_time": linked_signal.signal_time.isoformat() if linked_signal is not None else None,
                },
                "quality_score": _quality_score_summary(latest_quality_by_decision.get(row.decision_id)),
                "future_outcome_tracking": _future_outcome_summary(counterfactuals_by_decision.get(row.decision_id, [])),
                "recommendation_history": recommendation_history_by_decision.get(
                    row.decision_id,
                    {
                        "count": 0,
                        "latest_recommendation_at": None,
                        "latest_recommendation_type": None,
                        "latest_recommendation_state": "unavailable",
                        "recommendation_ids": [],
                    },
                ),
            }
        )

    return _paginate(items=items, page=page, page_size=page_size)


@router.post("/coach/reviews/generate")
async def generate_ai_coach_reviews(
    lookback_hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=250, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await generate_ai_coach_batch_reviews(
        db=db,
        lookback_hours=lookback_hours,
        limit=limit,
    )
    await db.commit()

    return {
        "status": "ok",
        "advisory_only": True,
        "paper_mode_only": True,
        "no_automatic_strategy_changes": True,
        "scanned_records": result.scanned_records,
        "inserted_recommendations": result.inserted_recommendations,
        "skipped_existing": result.skipped_existing,
        "recommendation_ids": [str(item) for item in result.recommendation_ids],
    }


@router.get("/{decision_id}/explainability")
async def get_decision_explainability(
    decision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    read_model = await read_decision_explainability(db=db, decision_id=decision_id)
    if read_model is None:
        raise NotFoundError(message="Decision not found", details={"decision_id": str(decision_id)})

    return {
        "decision_id": str(read_model.decision_id),
        "decision_status": read_model.decision_status,
        "explanation": read_model.explanation,
        "supporting_evidence": read_model.supporting_evidence,
        "opposing_evidence": read_model.opposing_evidence,
        "confidence_factors": read_model.confidence_factors,
        "risk_adjustments": read_model.risk_adjustments,
    }


@router.get("/counterfactuals")
async def list_counterfactual_outcomes(
    account_id: uuid.UUID | None = Query(default=None),
    portfolio_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(start_time=start_time, end_time=end_time)
    decision_ids = await _filtered_decision_ids(
        db=db,
        account_id=account_id,
        portfolio_id=portfolio_id,
        strategy_id=strategy_id,
        asset_id=asset_id,
        status=status,
        start_time=start_time,
        end_time=end_time,
    )

    result = await db.execute(
        select(DecisionCounterfactualResult)
        .order_by(
            DecisionCounterfactualResult.decision_timestamp.desc(),
            DecisionCounterfactualResult.horizon_minutes.asc(),
            DecisionCounterfactualResult.id.asc(),
        )
    )
    rows = list(result.scalars().all())
    if decision_ids is not None:
        rows = [item for item in rows if str(item.decision_id) in decision_ids]

    items = [
        {
            "id": str(item.id),
            "decision_id": str(item.decision_id),
            "horizon_label": item.horizon_label,
            "horizon_minutes": item.horizon_minutes,
            "decision_timestamp": item.decision_timestamp.isoformat(),
            "evaluated_at": item.evaluated_at.isoformat(),
            "asset_symbol": item.asset_symbol,
            "actual_action": item.actual_action,
            "shadow_buy_return_pct": _decimal_to_str(item.shadow_buy_return_pct),
            "shadow_sell_return_pct": _decimal_to_str(item.shadow_sell_return_pct),
            "shadow_wait_return_pct": _decimal_to_str(item.shadow_wait_return_pct),
            "best_action": item.best_action,
            "actual_action_correct": item.actual_action_correct,
            "evaluation_state": item.evaluation_state,
            "state_reason": item.state_reason,
            "lesson_tags": item.lesson_tags,
            "feature_snapshot": item.feature_snapshot,
            "created_at": item.created_at.isoformat(),
        }
        for item in rows
    ]
    return _paginate(items=items, page=page, page_size=page_size)


@router.get("/{decision_id}/counterfactuals")
async def get_decision_counterfactual_outcomes(
    decision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    result = await db.execute(
        select(DecisionCounterfactualResult)
        .where(DecisionCounterfactualResult.decision_id == decision_id)
        .order_by(DecisionCounterfactualResult.horizon_minutes.asc(), DecisionCounterfactualResult.id.asc())
    )
    rows = list(result.scalars().all())

    if not rows:
        exists = await db.scalar(select(DecisionRecord.decision_id).where(DecisionRecord.decision_id == decision_id).limit(1))
        if exists is None:
            raise NotFoundError(message="Decision not found", details={"decision_id": str(decision_id)})

        return {
            "decision_id": str(decision_id),
            "availability_state": "unavailable",
            "state_reason": "counterfactual_outcomes_unavailable",
            "items": [],
        }

    return {
        "decision_id": str(decision_id),
        "availability_state": "known",
        "state_reason": None,
        "items": [
            {
                "id": str(item.id),
                "horizon_label": item.horizon_label,
                "horizon_minutes": item.horizon_minutes,
                "evaluation_state": item.evaluation_state,
                "actual_action": item.actual_action,
                "best_action": item.best_action,
                "actual_action_correct": item.actual_action_correct,
                "lesson_tags": item.lesson_tags,
                "feature_snapshot": item.feature_snapshot,
            }
            for item in rows
        ],
    }


@router.get("/quality")
async def list_decision_quality(
    account_id: uuid.UUID | None = Query(default=None),
    portfolio_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(start_time=start_time, end_time=end_time)

    decision_ids = await _filtered_decision_ids(
        db=db,
        account_id=account_id,
        portfolio_id=portfolio_id,
        strategy_id=strategy_id,
        asset_id=asset_id,
        status=status,
        start_time=start_time,
        end_time=end_time,
    )
    selected_ids = sorted(decision_ids) if decision_ids is not None else []

    items: list[dict[str, Any]] = []
    for decision_id in selected_ids:
        score = await db.scalar(
            select(DecisionQualityScore)
            .where(DecisionQualityScore.decision_id == uuid.UUID(decision_id))
            .order_by(DecisionQualityScore.created_at.desc(), DecisionQualityScore.id.desc())
            .limit(1)
        )

        if score is None:
            items.append(
                {
                    "decision_id": decision_id,
                    "availability_state": "unavailable",
                    "state_reason": "quality_score_unavailable",
                    "scoring_model_version": None,
                    "composite_score": None,
                    "component_scores": [],
                    "weight_profile": {},
                    "provenance": {},
                    "created_at": None,
                }
            )
            continue

        items.append(
            {
                "decision_id": str(score.decision_id),
                "availability_state": "known",
                "state_reason": None,
                "scoring_model_version": score.scoring_model_version,
                "composite_score": _decimal_to_str(score.composite_score),
                "component_scores": score.component_scores,
                "weight_profile": score.weight_profile,
                "provenance": score.provenance,
                "created_at": score.created_at.isoformat(),
            }
        )

    return _paginate(items=items, page=page, page_size=page_size)


@router.get("/recommendations")
async def list_decision_recommendations(
    account_id: uuid.UUID | None = Query(default=None),
    portfolio_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    _validate_time_range(start_time=start_time, end_time=end_time)

    decision_ids = await _filtered_decision_ids(
        db=db,
        account_id=account_id,
        portfolio_id=portfolio_id,
        strategy_id=strategy_id,
        asset_id=asset_id,
        status=status,
        start_time=start_time,
        end_time=end_time,
    )

    rows = await read_experiment_recommendations(db=db)
    if decision_ids is not None:
        rows = [
            item
            for item in rows
            if any(decision_id in decision_ids for decision_id in item.originating_decision_ids)
        ]

    if start_time is not None:
        rows = [item for item in rows if _coerce_datetime(item.created_at) >= start_time]
    if end_time is not None:
        rows = [item for item in rows if _coerce_datetime(item.created_at) <= end_time]

    items = [
        {
            "id": str(item.recommendation_id),
            "recommendation_type": item.recommendation_type,
            "recommendation_category": item.recommendation_category,
            "confidence_level": item.confidence_level,
            "expected_impact": item.expected_impact_level,
            "required_human_review_level": item.required_human_review_level,
            "supporting_evidence_refs": item.supporting_evidence_refs,
            "originating_decision_ids": item.originating_decision_ids,
            "explanation": item.explanation,
            "suggested_experiment": item.suggested_experiment,
            "provenance": item.provenance,
            "availability_state": item.evidence_state,
            "state_reason": item.state_reason,
            "advisory_only": item.advisory_only,
            "created_at": _coerce_datetime(item.created_at).isoformat(),
        }
        for item in rows
    ]
    return _paginate(items=items, page=page, page_size=page_size)


async def _filtered_decision_ids(
    *,
    db: AsyncSession,
    account_id: uuid.UUID | None,
    portfolio_id: uuid.UUID | None,
    strategy_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    status: str | None,
    start_time: datetime | None,
    end_time: datetime | None,
) -> set[str] | None:
    resolved_account_id = _resolve_account_filter(account_id=account_id, portfolio_id=portfolio_id)

    timeline_filters = TimelineReadFilters(
        account_id=resolved_account_id,
        asset_id=asset_id,
        strategy_id=strategy_id,
        status=status,  # type: ignore[arg-type]
    )
    entries = await read_decision_timeline(db=db, filters=timeline_filters)
    if start_time is not None:
        entries = [item for item in entries if item.timestamp >= start_time]
    if end_time is not None:
        entries = [item for item in entries if item.timestamp <= end_time]

    return {str(item.decision_id) for item in entries}


def _validate_time_range(*, start_time: datetime | None, end_time: datetime | None) -> None:
    if start_time is not None and end_time is not None and start_time > end_time:
        raise InvalidRequestError(
            message="Invalid time range",
            details={
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
            },
        )


def _resolve_account_filter(*, account_id: uuid.UUID | None, portfolio_id: uuid.UUID | None) -> uuid.UUID | None:
    if account_id is None:
        return portfolio_id
    if portfolio_id is None:
        return account_id
    if account_id != portfolio_id:
        raise InvalidRequestError(
            message="account_id and portfolio_id must match when both are provided",
            details={"account_id": str(account_id), "portfolio_id": str(portfolio_id)},
        )
    return account_id


def _paginate(*, items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
    total = len(items)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    return {
        "items": items[start_index:end_index],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def _decimal_to_str(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "as_tuple"):
        return format(value, "f")
    return str(value)


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise InvalidRequestError(message="Invalid timestamp value", details={"value": str(value)})


def _extract_primary_signal_id(record: DecisionRecord) -> uuid.UUID | None:
    lineage = record.source_lineage if isinstance(record.source_lineage, dict) else {}
    signal_refs = lineage.get("signals")
    if not isinstance(signal_refs, list) or not signal_refs:
        return None

    first = signal_refs[0]
    if not isinstance(first, str):
        return None
    try:
        return uuid.UUID(first)
    except ValueError:
        return None


def _linked_signal(*, row: DecisionRecord, signal_map: dict[uuid.UUID, Signal]) -> Signal | None:
    signal_id = _extract_primary_signal_id(row)
    if signal_id is None:
        return None
    return signal_map.get(signal_id)


def _record_action(row: DecisionRecord) -> str | None:
    if row.generated_signals and isinstance(row.generated_signals[0], dict):
        value = row.generated_signals[0].get("action")
        if isinstance(value, str):
            return value
    return None


def _record_signal_status(row: DecisionRecord) -> str | None:
    if row.generated_signals and isinstance(row.generated_signals[0], dict):
        value = row.generated_signals[0].get("status")
        if isinstance(value, str):
            return value
    return None


def _record_signal_id_string(row: DecisionRecord) -> str | None:
    signal_id = _extract_primary_signal_id(row)
    if signal_id is None:
        return None
    return str(signal_id)


def _coerce_uuid_from_asset(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _coerce_uuid_string(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _quality_score_summary(score: DecisionQualityScore | None) -> dict[str, Any]:
    if score is None:
        return {
            "availability_state": "unavailable",
            "state_reason": "quality_score_unavailable",
            "scoring_model_version": None,
            "composite_score": None,
            "created_at": None,
        }

    return {
        "availability_state": "known",
        "state_reason": None,
        "scoring_model_version": score.scoring_model_version,
        "composite_score": _decimal_to_str(score.composite_score),
        "created_at": score.created_at.isoformat(),
    }


def _future_outcome_summary(counterfactual_rows: list[DecisionCounterfactualResult]) -> dict[str, Any]:
    if not counterfactual_rows:
        return {
            "availability_state": "unavailable",
            "state_reason": "counterfactual_outcomes_unavailable",
            "horizons_evaluated": [],
            "resolved_horizons": 0,
            "total_horizons": 0,
            "latest_evaluated_at": None,
            "latest_horizon_label": None,
            "latest_evaluation_state": None,
            "latest_best_action": None,
            "latest_actual_action_correct": None,
        }

    latest = max(
        counterfactual_rows,
        key=lambda item: (item.evaluated_at, item.horizon_minutes, str(item.id)),
    )
    resolved_horizons = sum(1 for item in counterfactual_rows if item.evaluation_state == "resolved")
    horizon_set = sorted({item.horizon_label for item in counterfactual_rows})

    return {
        "availability_state": "known",
        "state_reason": None,
        "horizons_evaluated": horizon_set,
        "resolved_horizons": resolved_horizons,
        "total_horizons": len(counterfactual_rows),
        "latest_evaluated_at": latest.evaluated_at.isoformat(),
        "latest_horizon_label": latest.horizon_label,
        "latest_evaluation_state": latest.evaluation_state,
        "latest_best_action": latest.best_action,
        "latest_actual_action_correct": latest.actual_action_correct,
    }
