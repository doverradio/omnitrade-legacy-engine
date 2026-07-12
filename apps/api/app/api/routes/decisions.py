from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_quality_score import DecisionQualityScore
from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.crypto_order_preview import CryptoOrderPreview
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_approval_event import LiveApprovalEvent
from app.models.audit_log import AuditLog
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.services.arena.comparison import read_latest_arena_comparison_record
from app.services.arena.contracts import ArenaLeaderboardFilterContract
from app.services.arena.leaderboard import read_latest_arena_leaderboard_snapshot
from app.services.arena.tournaments import (
    read_arena_tournament_history_events,
    read_arena_tournament_lifecycle_state,
)
from app.services.decisions.coach import generate_ai_coach_batch_reviews
from app.services.decisions.coach_reader import list_ai_coach_replay_reviews_v0
from app.services.decisions.explainability import read_decision_explainability
from app.services.decisions.recommendations import read_experiment_recommendations
from app.services.decisions.replay_candidates import list_replay_candidates_v0
from app.services.decisions.timeline import TimelineReadFilters, read_decision_timeline

router = APIRouter(prefix="/decisions", tags=["decisions"])

MAX_PAGE_SIZE = 200

_FEATURE_INTRODUCED_AT: dict[str, datetime] = {
    "decision_snapshot": datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    "counterfactual": datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    "decision_quality": datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc),
    "preview_linkage": datetime(2026, 7, 9, 22, 30, tzinfo=timezone.utc),
    "live_submission": datetime(2026, 7, 9, 22, 50, tzinfo=timezone.utc),
}


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
    decision_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    trade_accepted: bool | None = Query(default=None),
    review_status: str | None = Query(default=None),
    environment: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str = Query(default="newest", pattern="^(newest|oldest|highest_confidence|lowest_confidence|highest_quality|lowest_quality|largest_requested_notional|largest_approved_notional|most_recently_reviewed)$"),
    has_decision_snapshot: bool | None = Query(default=None),
    has_price_evidence: bool | None = Query(default=None),
    has_risk_event: bool | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await _read_decision_record_items(
        db=db,
        decision_id=decision_id,
        asset_id=asset_id,
        strategy_id=strategy_id,
        action=action,
        trade_accepted=trade_accepted,
        review_status=review_status,
        environment=environment,
        provider=provider,
        product_id=product_id,
        q=q,
        sort=sort,
        has_decision_snapshot=has_decision_snapshot,
        has_price_evidence=has_price_evidence,
        has_risk_event=has_risk_event,
        start_time=start_time,
        end_time=end_time,
    )
    return _paginate(items=items, page=page, page_size=page_size)


@router.get("/explorer/summary")
async def get_decision_explorer_summary(
    decision_id: uuid.UUID | None = Query(default=None),
    asset_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    trade_accepted: bool | None = Query(default=None),
    review_status: str | None = Query(default=None),
    environment: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
    q: str | None = Query(default=None),
    has_decision_snapshot: bool | None = Query(default=None),
    has_price_evidence: bool | None = Query(default=None),
    has_risk_event: bool | None = Query(default=None),
    start_time: datetime | None = Query(default=None),
    end_time: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await _read_decision_record_items(
        db=db,
        decision_id=decision_id,
        asset_id=asset_id,
        strategy_id=strategy_id,
        action=action,
        trade_accepted=trade_accepted,
        review_status=review_status,
        environment=environment,
        provider=provider,
        product_id=product_id,
        q=q,
        sort="newest",
        has_decision_snapshot=has_decision_snapshot,
        has_price_evidence=has_price_evidence,
        has_risk_event=has_risk_event,
        start_time=start_time,
        end_time=end_time,
    )

    summary = {
        "total_decisions": len(items),
        "accepted": 0,
        "risk_rejected": 0,
        "hold_wait": 0,
        "preview_ready": 0,
        "submitted": 0,
        "executed": 0,
        "needs_review": 0,
        "missing_linkage": 0,
    }

    for item in items:
        if item.get("trade_accepted") is True:
            summary["accepted"] += 1
        if item.get("risk_verdict") == "rejected":
            summary["risk_rejected"] += 1
        if item.get("action") in {"hold", "wait"}:
            summary["hold_wait"] += 1
        if item.get("preview_status") == "ready":
            summary["preview_ready"] += 1
        if item.get("execution_status") in {"submitted", "filled"}:
            summary["submitted"] += 1
        if item.get("execution_status") == "filled":
            summary["executed"] += 1
        if item.get("review_status") in {"unreviewed", "flagged"}:
            summary["needs_review"] += 1
        if item.get("evidence_completeness") != "complete":
            summary["missing_linkage"] += 1

    return summary


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


@router.get("/{decision_id}/inspector")
async def get_decision_inspector(
    decision_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    decision = await db.scalar(select(DecisionRecord).where(DecisionRecord.decision_id == decision_id).limit(1))
    if decision is None:
        raise NotFoundError(message="Decision not found", details={"decision_id": str(decision_id)})

    snapshot = await db.scalar(select(DecisionSnapshot).where(DecisionSnapshot.decision_id == decision_id).limit(1))
    preview = await db.scalar(
        select(CryptoOrderPreview)
        .where(CryptoOrderPreview.decision_record_id == decision_id)
        .order_by(CryptoOrderPreview.created_at.desc(), CryptoOrderPreview.preview_version.desc())
        .limit(1)
    )
    live_order = await db.scalar(
        select(LiveCryptoOrder)
        .where(LiveCryptoOrder.decision_record_id == decision_id)
        .order_by(LiveCryptoOrder.created_at.desc())
        .limit(1)
    )

    risk_event_id = _decision_risk_event_id(decision)
    risk_event = None
    if risk_event_id is not None:
        risk_event = await db.scalar(select(RiskEvent).where(RiskEvent.id == risk_event_id).limit(1))

    quality = await db.scalar(
        select(DecisionQualityScore)
        .where(DecisionQualityScore.decision_id == decision_id)
        .order_by(DecisionQualityScore.created_at.desc(), DecisionQualityScore.id.desc())
        .limit(1)
    )
    counterfactual_rows = list(
        (
            await db.execute(
                select(DecisionCounterfactualResult)
                .where(DecisionCounterfactualResult.decision_id == decision_id)
                .order_by(
                    DecisionCounterfactualResult.horizon_minutes.asc(),
                    DecisionCounterfactualResult.evaluated_at.desc(),
                )
            )
        ).scalars().all()
    )

    linked_signal = None
    signal_id = _extract_primary_signal_id(decision)
    if signal_id is not None:
        linked_signal = await db.scalar(select(Signal).where(Signal.id == signal_id).limit(1))

    approval_event = None
    approval_event_id = _decision_live_approval_event_id(decision)
    if approval_event_id is not None:
        approval_event = await db.scalar(select(LiveApprovalEvent).where(LiveApprovalEvent.id == approval_event_id).limit(1))

    audit_events = await _load_inspector_audit_events(
        db=db,
        decision_id=decision_id,
        preview=preview,
        live_order=live_order,
    )

    execution_evidence = _execution_price_evidence_payload(decision=decision)
    risk_panel = _risk_panel_payload(decision=decision, risk_event=risk_event)
    linkage_health = _linkage_health_payload(
        decision=decision,
        snapshot=snapshot,
        execution_evidence=execution_evidence,
        risk_event=risk_event,
        preview=preview,
        approval_event=approval_event,
        live_order=live_order,
        counterfactual_rows=counterfactual_rows,
        quality=quality,
        audit_events=audit_events,
    )
    timeline = _timeline_payload(
        decision=decision,
        linked_signal=linked_signal,
        execution_evidence=execution_evidence,
        risk_panel=risk_panel,
        preview=preview,
        approval_event=approval_event,
        live_order=live_order,
        linkage_health=linkage_health,
    )

    return {
        "decision_id": str(decision.decision_id),
        "header": _inspector_header_payload(
            decision=decision,
            linked_signal=linked_signal,
            quality=quality,
            preview=preview,
            live_order=live_order,
        ),
        "timeline": timeline,
        "narrative": _deterministic_narrative_payload(
            decision=decision,
            linked_signal=linked_signal,
            execution_evidence=execution_evidence,
            risk_panel=risk_panel,
            preview=preview,
            live_order=live_order,
        ),
        "execution_price_evidence": execution_evidence,
        "risk_evaluation": risk_panel,
        "decision_intelligence": {
            "decision_record": "linked",
            "decision_snapshot": "linked" if snapshot is not None else "missing",
            "decision_version": decision.version,
            "risk_event": "linked" if risk_event is not None else "missing",
            "execution_evidence": "linked" if execution_evidence["availability"] == "linked" else "missing",
            "counterfactual_package": "linked" if counterfactual_rows else "unavailable",
            "decision_quality": "linked" if quality is not None else "unavailable",
            "review_status": decision.review_status or "unreviewed",
        },
        "preview": _preview_panel_payload(preview=preview, live_order=live_order, approval_event=approval_event),
        "audit_timeline": audit_events,
        "counterfactual": _counterfactual_panel_payload(counterfactual_rows),
        "linkage_health": linkage_health,
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


@router.get("/replay/candidates")
async def list_decision_replay_candidates(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await list_replay_candidates_v0(db=db)

    items = [
        {
            "decision_package_id": item.decision_package_id,
            "decision_id": str(item.decision_id),
            "package_hash": item.package_hash,
            "package_version": item.package_version,
            "replay_ready": item.replay_ready,
            "missing_artifacts": item.missing_artifacts,
            "unavailable_artifacts": item.unavailable_artifacts,
            "candidate_reason": item.candidate_reason,
            "created_at": item.created_at.isoformat(),
        }
        for item in rows
    ]
    return _paginate(items=items, page=page, page_size=page_size)


@router.get("/coach/replay-reviews")
async def list_ai_coach_replay_reviews(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    rows = await list_ai_coach_replay_reviews_v0(db=db)

    items = [
        {
            "decision_id": str(item.decision_id),
            "decision_package_id": item.decision_package_id,
            "package_hash": item.package_hash,
            "package_version": item.package_version,
            "replay_ready": item.replay_ready,
            "summary": item.summary,
            "strengths": item.strengths,
            "weaknesses": item.weaknesses,
            "missing_evidence": item.missing_evidence,
            "suggested_followups": item.suggested_followups,
            "advisory_only": item.advisory_only,
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


async def _read_decision_record_items(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID | None,
    asset_id: uuid.UUID | None,
    strategy_id: uuid.UUID | None,
    action: str | None,
    trade_accepted: bool | None,
    review_status: str | None,
    environment: str | None,
    provider: str | None,
    product_id: str | None,
    q: str | None,
    sort: str,
    has_decision_snapshot: bool | None,
    has_price_evidence: bool | None,
    has_risk_event: bool | None,
    start_time: datetime | None,
    end_time: datetime | None,
) -> list[dict[str, Any]]:
    _validate_time_range(start_time=start_time, end_time=end_time)

    statement = select(DecisionRecord).order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc())
    if decision_id is not None:
        statement = statement.where(DecisionRecord.decision_id == decision_id)
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
        signal_rows = list((await db.execute(select(Signal).where(Signal.id.in_(sorted(set(signal_ids)))))).scalars().all())
        signal_map = {item.id: item for item in signal_rows}

    filtered_rows: list[DecisionRecord] = []
    for row in decision_rows:
        linked_signal = _linked_signal(row=row, signal_map=signal_map)
        record_asset_id = _coerce_uuid_from_asset(row.asset.get("asset_id") if isinstance(row.asset, dict) else None)
        resolved_asset_id = linked_signal.asset_id if linked_signal is not None else record_asset_id

        if asset_id is not None and resolved_asset_id != asset_id:
            continue
        if strategy_id is not None and (linked_signal is None or linked_signal.strategy_id != strategy_id):
            continue
        if action is not None:
            resolved_action = (linked_signal.action if linked_signal is not None else _record_action(row)) or ""
            if resolved_action.lower() != action.lower():
                continue

        if provider is not None:
            provider_value = _record_provider(row)
            if provider_value is None or provider_value.lower() != provider.lower():
                continue
        if environment is not None:
            env_value = _record_environment(row)
            if env_value is None or env_value.lower() != environment.lower():
                continue
        if product_id is not None:
            product_value = _record_product_id(row)
            if product_value is None or product_value.upper() != product_id.upper():
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
                linked_decision_id = _coerce_uuid_string(decision_id_value)
                if linked_decision_id is None or linked_decision_id not in filtered_decision_id_set:
                    continue

                summary = recommendation_history_by_decision.get(linked_decision_id)
                if summary is None:
                    summary = {
                        "count": 0,
                        "latest_recommendation_at": recommendation_created_at.isoformat(),
                        "latest_recommendation_type": recommendation_row.recommendation_type,
                        "latest_recommendation_state": recommendation_row.evidence_state,
                        "recommendation_ids": [],
                    }
                    recommendation_history_by_decision[linked_decision_id] = summary

                summary["count"] += 1
                summary["recommendation_ids"].append(str(recommendation_row.recommendation_id))
                latest_at = summary.get("latest_recommendation_at")
                if not isinstance(latest_at, str) or recommendation_created_at.isoformat() > latest_at:
                    summary["latest_recommendation_at"] = recommendation_created_at.isoformat()
                    summary["latest_recommendation_type"] = recommendation_row.recommendation_type
                    summary["latest_recommendation_state"] = recommendation_row.evidence_state

    items: list[dict[str, Any]] = []
    q_norm = q.lower().strip() if isinstance(q, str) and q.strip() else None
    for row in filtered_rows:
        linked_signal = _linked_signal(row=row, signal_map=signal_map)
        quality_score = latest_quality_by_decision.get(row.decision_id)
        confidence_value = _decimal_to_str(row.confidence)
        quality_value = _decimal_to_str(quality_score.composite_score) if quality_score is not None else None
        requested_notional = _requested_notional(row)
        approved_notional = _approved_notional(row)
        risk_verdict = _risk_verdict(row)
        first_failing_rule = _first_failing_rule(row)
        preview_status = _preview_status(row)
        approval_status = _approval_status(row)
        rehearsal_status = _rehearsal_status(row)
        execution_status = _execution_status(row)
        has_snapshot_value = _has_decision_snapshot(row)
        has_price_evidence_value = _has_price_evidence(row)
        has_risk_event_value = _has_risk_event(row)

        item = {
            "decision_id": str(row.decision_id),
            "timestamp": row.timestamp.isoformat(),
            "asset_id": row.asset.get("asset_id") if isinstance(row.asset, dict) else None,
            "trade_accepted": row.trade_accepted,
            "review_status": row.review_status,
            "outcome": row.outcome,
            "action": _record_action(row),
            "provider": _record_provider(row),
            "environment": _record_environment(row),
            "product_id": _record_product_id(row),
            "confidence": confidence_value,
            "risk_verdict": risk_verdict,
            "first_failing_risk_rule": first_failing_rule,
            "requested_notional": requested_notional,
            "approved_notional": approved_notional,
            "preview_status": preview_status,
            "approval_status": approval_status,
            "rehearsal_status": rehearsal_status,
            "execution_status": execution_status,
            "has_decision_snapshot": has_snapshot_value,
            "has_price_evidence": has_price_evidence_value,
            "has_risk_event": has_risk_event_value,
            "evidence_completeness": "complete" if (has_snapshot_value and has_price_evidence_value and has_risk_event_value) else "missing_linkage",
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
            "quality_score": _quality_score_summary(quality_score),
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

        if has_decision_snapshot is not None and item["has_decision_snapshot"] != has_decision_snapshot:
            continue
        if has_price_evidence is not None and item["has_price_evidence"] != has_price_evidence:
            continue
        if has_risk_event is not None and item["has_risk_event"] != has_risk_event:
            continue

        if q_norm is not None:
            haystack = [
                item["decision_id"],
                item.get("product_id") or "",
                item.get("provider") or "",
                (row.asset.get("symbol") if isinstance(row.asset, dict) else "") or "",
                item.get("action") or "",
                item.get("outcome") or "",
                row.trade_rejected_reason or "",
                row.human_notes or "",
                _record_audit_correlation_id(row) or "",
            ]
            if not any(q_norm in str(value).lower() for value in haystack):
                continue

        items.append(item)

    if sort == "oldest":
        items.sort(key=lambda value: (value.get("timestamp") or "", value.get("decision_id") or ""))
    elif sort == "highest_confidence":
        items.sort(key=lambda value: _float_or_min(value.get("confidence")), reverse=True)
    elif sort == "lowest_confidence":
        items.sort(key=lambda value: _float_or_max(value.get("confidence")))
    elif sort == "highest_quality":
        items.sort(key=lambda value: _float_or_min(value.get("quality_score", {}).get("composite_score")), reverse=True)
    elif sort == "lowest_quality":
        items.sort(key=lambda value: _float_or_max(value.get("quality_score", {}).get("composite_score")))
    elif sort == "largest_requested_notional":
        items.sort(key=lambda value: _float_or_min(value.get("requested_notional")), reverse=True)
    elif sort == "largest_approved_notional":
        items.sort(key=lambda value: _float_or_min(value.get("approved_notional")), reverse=True)
    elif sort == "most_recently_reviewed":
        items.sort(
            key=lambda value: (
                1 if value.get("review_status") not in {None, "unreviewed"} else 0,
                value.get("timestamp") or "",
            ),
            reverse=True,
        )
    else:
        items.sort(key=lambda value: (value.get("timestamp") or "", value.get("decision_id") or ""), reverse=True)

    return items


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


def _record_provider(row: DecisionRecord) -> str | None:
    if isinstance(row.asset, dict):
        value = row.asset.get("provider") or row.asset.get("exchange")
        if isinstance(value, str) and value.strip():
            return value

    if isinstance(row.execution_details, dict):
        value = row.execution_details.get("provider")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _record_environment(row: DecisionRecord) -> str | None:
    if isinstance(row.execution_details, dict):
        value = row.execution_details.get("environment")
        if isinstance(value, str) and value.strip():
            return value
    return None


def _record_product_id(row: DecisionRecord) -> str | None:
    if isinstance(row.asset, dict):
        value = row.asset.get("product_id")
        if isinstance(value, str) and value.strip():
            return value
        symbol = row.asset.get("symbol")
        quote = row.asset.get("quote_currency")
        if isinstance(symbol, str) and isinstance(quote, str):
            return f"{symbol}-{quote}"
    return None


def _requested_notional(row: DecisionRecord) -> str | None:
    if row.generated_signals and isinstance(row.generated_signals[0], dict):
        value = row.generated_signals[0].get("quote_size") or row.generated_signals[0].get("requested_amount")
        if value is not None:
            return str(value)

    if isinstance(row.execution_details, dict):
        value = row.execution_details.get("requested_quote_size")
        if value is not None:
            return str(value)
    return None


def _approved_notional(row: DecisionRecord) -> str | None:
    if isinstance(row.execution_details, dict):
        value = row.execution_details.get("approved_quantity")
        if value is not None:
            return str(value)

    if row.position_size is not None:
        return _decimal_to_str(row.position_size)
    return None


def _risk_verdict(row: DecisionRecord) -> str:
    if row.trade_accepted:
        return "approved"
    return "rejected"


def _first_failing_rule(row: DecisionRecord) -> str | None:
    if row.risk_adjustments:
        for adjustment in row.risk_adjustments:
            if not isinstance(adjustment, dict):
                continue
            status = adjustment.get("status")
            if status == "reject":
                reason_code = adjustment.get("reason_code")
                if isinstance(reason_code, str):
                    return reason_code
    return row.trade_rejected_reason


def _preview_status(row: DecisionRecord) -> str:
    lineage = row.source_lineage if isinstance(row.source_lineage, dict) else {}
    previews = lineage.get("crypto_order_previews")
    if isinstance(previews, list) and previews:
        return "ready"

    if isinstance(row.execution_details, dict):
        if row.execution_details.get("preview_id") is not None:
            return "ready"
    return "none"


def _approval_status(row: DecisionRecord) -> str:
    lineage = row.source_lineage if isinstance(row.source_lineage, dict) else {}
    approvals = lineage.get("live_crypto_order_approvals")
    if isinstance(approvals, list) and approvals:
        return "created"
    return "none"


def _rehearsal_status(row: DecisionRecord) -> str:
    lineage = row.source_lineage if isinstance(row.source_lineage, dict) else {}
    rehearsals = lineage.get("live_crypto_order_rehearsals")
    if isinstance(rehearsals, list) and rehearsals:
        return "created"
    return "none"


def _execution_status(row: DecisionRecord) -> str:
    lineage = row.source_lineage if isinstance(row.source_lineage, dict) else {}
    trades = lineage.get("trades")
    if isinstance(trades, list) and trades:
        return "filled"

    if isinstance(row.execution_details, dict):
        status = row.execution_details.get("status")
        if isinstance(status, str):
            lower = status.lower()
            if lower in {"submitted", "filled", "executed"}:
                return "filled" if lower in {"filled", "executed"} else "submitted"
    return "not_submitted"


def _has_decision_snapshot(row: DecisionRecord) -> bool:
    return bool(row.market_regime)


def _has_price_evidence(row: DecisionRecord) -> bool:
    if isinstance(row.market_regime, dict) and row.market_regime.get("execution_price_evidence_id"):
        return True
    if isinstance(row.execution_details, dict):
        value = row.execution_details.get("execution_price_evidence_id")
        return bool(value)
    return False


def _has_risk_event(row: DecisionRecord) -> bool:
    if isinstance(row.expected_risk, dict) and row.expected_risk.get("risk_event_id"):
        return True
    lineage = row.source_lineage if isinstance(row.source_lineage, dict) else {}
    risk_events = lineage.get("risk_events")
    return isinstance(risk_events, list) and len(risk_events) > 0


def _record_audit_correlation_id(row: DecisionRecord) -> str | None:
    if isinstance(row.execution_details, dict):
        value = row.execution_details.get("audit_correlation_id")
        if isinstance(value, str):
            return value
    return None


def _float_or_min(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return -1.0


def _float_or_max(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 1e12


def _decision_risk_event_id(decision: DecisionRecord) -> uuid.UUID | None:
    if isinstance(decision.expected_risk, dict):
        value = decision.expected_risk.get("risk_event_id")
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                return None
    lineage = decision.source_lineage if isinstance(decision.source_lineage, dict) else {}
    refs = lineage.get("risk_events")
    if isinstance(refs, list) and refs:
        first = refs[0]
        if isinstance(first, str):
            try:
                return uuid.UUID(first)
            except ValueError:
                return None
    return None


def _decision_live_approval_event_id(decision: DecisionRecord) -> uuid.UUID | None:
    lineage = decision.source_lineage if isinstance(decision.source_lineage, dict) else {}
    refs = lineage.get("live_approval_events")
    if isinstance(refs, list) and refs:
        first = refs[0]
        if isinstance(first, str):
            try:
                return uuid.UUID(first)
            except ValueError:
                return None
    return None


async def _load_inspector_audit_events(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
    preview: CryptoOrderPreview | None,
    live_order: LiveCryptoOrder | None,
) -> list[dict[str, Any]]:
    rows: list[AuditLog] = []
    rows.extend(
        list(
            (
                await db.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_type == "decision_record", AuditLog.entity_id == decision_id)
                    .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                )
            ).scalars().all()
        )
    )

    if preview is not None:
        rows.extend(
            list(
                (
                    await db.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "crypto_order_preview", AuditLog.entity_id == preview.crypto_order_preview_id)
                        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    )
                ).scalars().all()
            )
        )
    if live_order is not None:
        rows.extend(
            list(
                (
                    await db.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "live_crypto_order", AuditLog.entity_id == live_order.live_crypto_order_id)
                        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
                    )
                ).scalars().all()
            )
        )

    unique_rows: dict[tuple[str, int], AuditLog] = {}
    for row in rows:
        unique_rows[(row.entity_type, row.id)] = row

    ordered = sorted(unique_rows.values(), key=lambda item: (item.created_at, item.id))
    events: list[dict[str, Any]] = []
    for row in ordered:
        events.append(
            {
                "actor": row.actor,
                "timestamp": row.created_at.isoformat(),
                "action": row.action,
                "entity_type": row.entity_type,
                "correlation_id": _audit_correlation_id_from_states(row.before_state, row.after_state),
            }
        )
    return events


def _audit_correlation_id_from_states(before_state: Any, after_state: Any) -> str | None:
    for state in (after_state, before_state):
        if isinstance(state, dict):
            value = state.get("audit_correlation_id")
            if isinstance(value, str):
                return value
    return None


def _inspector_header_payload(
    *,
    decision: DecisionRecord,
    linked_signal: Signal | None,
    quality: DecisionQualityScore | None,
    preview: CryptoOrderPreview | None,
    live_order: LiveCryptoOrder | None,
) -> dict[str, Any]:
    action_value = (linked_signal.action if linked_signal is not None else _record_action(decision)) or "hold"
    product_id = _record_product_id(decision) or (preview.product_id if preview is not None else None) or (live_order.product_id if live_order is not None else None) or "Unknown"
    readable_action = action_value.upper()
    environment_value = _record_environment(decision) or (preview.environment if preview is not None else None) or (live_order.environment if live_order is not None else None) or "unknown"
    market_label = f"{_record_provider(decision) or (preview.provider if preview is not None else None) or (live_order.provider if live_order is not None else None) or 'unknown'} / {product_id}"

    return {
        "title": f"{product_id} {readable_action} Recommendation",
        "decision_id": str(decision.decision_id),
        "current_status": decision.outcome or ("accepted" if decision.trade_accepted else "rejected"),
        "timestamp": decision.timestamp.isoformat(),
        "strategy": str(linked_signal.strategy_id) if linked_signal is not None else None,
        "campaign": _campaign_id(decision),
        "provider": _record_provider(decision) or (preview.provider if preview is not None else None) or (live_order.provider if live_order is not None else None),
        "environment": environment_value,
        "market": market_label,
        "confidence": _decimal_to_str(decision.confidence),
        "decision_quality": _decimal_to_str(quality.composite_score) if quality is not None else None,
        "review_status": decision.review_status or "unreviewed",
        "environment_badge": environment_value.upper(),
        "paper_live_badge": "PAPER" if environment_value != "live" else "LIVE",
    }


def _campaign_id(decision: DecisionRecord) -> str | None:
    lineage = decision.source_lineage if isinstance(decision.source_lineage, dict) else {}
    refs = lineage.get("capital_campaigns")
    if isinstance(refs, list) and refs:
        first = refs[0]
        if isinstance(first, str):
            return first
    return None


def _execution_price_evidence_payload(*, decision: DecisionRecord) -> dict[str, Any]:
    regime = decision.market_regime if isinstance(decision.market_regime, dict) else {}
    indicators = decision.indicators if isinstance(decision.indicators, dict) else {}

    observed_ts = regime.get("observed_at")
    retrieved_ts = regime.get("retrieved_at")
    freshness_seconds = indicators.get("freshness_seconds")
    age_seconds = None
    if isinstance(observed_ts, str) and isinstance(retrieved_ts, str):
        try:
            observed_dt = _coerce_datetime(observed_ts)
            retrieved_dt = _coerce_datetime(retrieved_ts)
            age_seconds = int((retrieved_dt - observed_dt).total_seconds())
        except Exception:
            age_seconds = None

    linked = bool(regime.get("execution_price_evidence_id"))
    return {
        "availability": "linked" if linked else "missing",
        "provider": regime.get("provider"),
        "venue": regime.get("venue"),
        "product": decision.asset.get("product_id") if isinstance(decision.asset, dict) else None,
        "base_currency": decision.asset.get("base_currency") if isinstance(decision.asset, dict) else None,
        "quote_currency": decision.asset.get("quote_currency") if isinstance(decision.asset, dict) else None,
        "observed_price": regime.get("reference_price"),
        "bid": indicators.get("bid"),
        "ask": indicators.get("ask"),
        "reference_price": regime.get("reference_price"),
        "observed_timestamp": observed_ts,
        "retrieved_timestamp": retrieved_ts,
        "evidence_age_seconds": age_seconds,
        "freshness_seconds": freshness_seconds,
        "validation_status": "valid" if linked else "missing",
        "evidence_id": regime.get("execution_price_evidence_id"),
    }


def _risk_panel_payload(*, decision: DecisionRecord, risk_event: RiskEvent | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    if risk_event is not None and isinstance(risk_event.detail, dict):
        for step in risk_event.detail.get("steps", []):
            if not isinstance(step, dict):
                continue
            status = str(step.get("status", "unknown"))
            checks.append(
                {
                    "rule_name": step.get("step"),
                    "policy": "risk_engine_final",
                    "observed_value": None,
                    "threshold": None,
                    "result": "PASS" if status == "approve" else "FAIL" if status == "reject" else "UNKNOWN",
                    "reason": step.get("reason_code"),
                    "status": status,
                }
            )
    elif decision.risk_adjustments:
        for step in decision.risk_adjustments:
            if not isinstance(step, dict):
                continue
            status = str(step.get("status", "unknown"))
            checks.append(
                {
                    "rule_name": step.get("step"),
                    "policy": "risk_engine_final",
                    "observed_value": step.get("observed_value"),
                    "threshold": step.get("threshold"),
                    "result": "PASS" if status == "approve" else "FAIL" if status == "reject" else "UNKNOWN",
                    "reason": step.get("reason_code"),
                    "status": status,
                }
            )

    first_fail = None
    for check in checks:
        if check["result"] == "FAIL":
            first_fail = check
            break

    return {
        "verdict": "approved" if decision.trade_accepted else "rejected",
        "first_failing_rule": first_fail,
        "stopped_after_first_fail": first_fail is not None,
        "risk_adjusted_sizing": _approved_notional(decision),
        "can_attribute_risk_engine": risk_event is not None,
        "evidence_source": "risk_event" if risk_event is not None else "decision_record" if checks else "unavailable",
        "checks": checks,
    }


def _timeline_payload(
    *,
    decision: DecisionRecord,
    linked_signal: Signal | None,
    execution_evidence: dict[str, Any],
    risk_panel: dict[str, Any],
    preview: CryptoOrderPreview | None,
    approval_event: LiveApprovalEvent | None,
    live_order: LiveCryptoOrder | None,
    linkage_health: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    def stage_status(code: str, detail: str) -> dict[str, Any]:
        symbols = {
            "completed": "✓ Completed",
            "rejected": "✕ Rejected",
            "pending": "○ Pending",
            "not_applicable": "— Not Applicable",
            "missing": "? Missing Evidence",
            "unavailable": "∅ Unavailable",
        }
        return {"status": code, "label": symbols[code], "detail": detail}

    linkage_by_component = {item["component"]: item for item in linkage_health}

    def linkage_stage(component: str, fallback_status: str, fallback_detail: str) -> dict[str, Any]:
        item = linkage_by_component.get(component)
        if item is None:
            return stage_status(fallback_status, fallback_detail)
        return stage_status(str(item["status"]), str(item["reason"]))

    signal_status = stage_status("completed", "Signal linkage resolved") if linked_signal is not None else stage_status("missing", "Signal linkage missing")
    strategy_status = stage_status("completed", "Strategy identified") if linked_signal is not None else stage_status("missing", "Strategy linkage missing")
    evidence_status = linkage_stage("Execution Evidence", "missing", "Execution price evidence linkage missing")
    risk_status = linkage_stage("Risk Event", "missing", "Risk event linkage missing")
    persisted_status = stage_status("completed", "Decision record persisted")

    preview_status = linkage_stage("Preview", "not_applicable", "Preview not applicable")
    approval_status = linkage_stage("Approval", "not_applicable", "Approval not applicable")
    rehearsal_status = linkage_stage("Rehearsal", "unavailable", "Rehearsal linkage capability unavailable")
    submission_status = linkage_stage("Submission", "not_applicable", "Submission not applicable")
    execution_status = linkage_stage("Execution", "not_applicable", "Execution not applicable")

    outcome_status = stage_status("completed", f"Outcome: {decision.outcome}") if decision.outcome else stage_status("pending", "Outcome not yet known")

    return [
        {"stage": "Signal Generated", **signal_status},
        {"stage": "Strategy Selected", **strategy_status},
        {"stage": "Execution Price Evidence", **evidence_status},
        {"stage": "Risk Evaluation", **risk_status},
        {"stage": "Decision Record Persisted", **persisted_status},
        {"stage": "Preview", **preview_status},
        {"stage": "Approval", **approval_status},
        {"stage": "Rehearsal", **rehearsal_status},
        {"stage": "Submission", **submission_status},
        {"stage": "Execution", **execution_status},
        {"stage": "Outcome", **outcome_status},
    ]


def _deterministic_narrative_payload(
    *,
    decision: DecisionRecord,
    linked_signal: Signal | None,
    execution_evidence: dict[str, Any],
    risk_panel: dict[str, Any],
    preview: CryptoOrderPreview | None,
    live_order: LiveCryptoOrder | None,
) -> dict[str, Any]:
    action = (linked_signal.action if linked_signal is not None else _record_action(decision) or "hold").upper()
    confidence = _decimal_to_str(decision.confidence) or "unknown"
    reason = decision.trade_rejected_reason or "no_rejection_reason_recorded"

    lines: list[str] = []
    if linked_signal is not None:
        lines.append(f"Signal evidence shows a strategy action of {action}.")
    else:
        lines.append(f"The decision record indicates an action of {action}, but direct signal linkage is unavailable.")

    if decision.trade_accepted:
        lines.append("The decision record marks this opportunity as trade_accepted=true.")
    else:
        lines.append(f"The decision record marks this opportunity as trade_accepted=false with reason {reason}.")

    if risk_panel.get("can_attribute_risk_engine"):
        if decision.trade_accepted:
            lines.append("A linked Risk Event is present and indicates a governed approval path.")
        else:
            lines.append("A linked Risk Event is present and indicates a governed rejection path.")
    elif risk_panel.get("checks"):
        lines.append(
            "Risk step data exists in the decision record, but no linked Risk Event is available; "
            "the Inspector cannot determine final Risk Engine causality from persisted linkage alone."
        )
    else:
        lines.append("The Inspector cannot determine whether a Risk Engine evaluation was persisted for this decision.")

    if execution_evidence.get("availability") == "linked":
        lines.append("Provider-native execution price evidence was linked and used for decision-time context.")
    else:
        lines.append("Execution price evidence linkage is not available, so price validation details cannot be confirmed.")

    if risk_panel.get("first_failing_rule"):
        first = risk_panel["first_failing_rule"]
        lines.append(
            "Risk evaluation stopped at the first failing rule: "
            f"{first.get('rule_name')} ({first.get('reason') or 'no_reason_code_recorded'})."
        )
    elif risk_panel.get("checks"):
        lines.append("Recorded risk steps do not show a failing rule.")
    else:
        lines.append("Rule-by-rule risk evidence is unavailable.")

    if preview is not None:
        lines.append(f"A linked preview record exists with status {preview.status}.")
    elif _decision_predates_feature(decision=decision, feature_key="preview_linkage"):
        lines.append("This decision predates preview linkage persistence in this repository.")
    else:
        lines.append("The Inspector cannot determine whether a preview record should exist for this decision.")

    if live_order is not None:
        lines.append(f"A linked live-order record exists with status {live_order.status}.")
    elif _decision_predates_feature(decision=decision, feature_key="live_submission"):
        lines.append("This decision predates live submission persistence in this repository.")
    else:
        lines.append("No linked live-order submission record is available.")

    lines.append(f"Recorded confidence at decision time was {confidence}.")

    return {
        "title": "Why",
        "explanation": " ".join(lines),
        "evidence_gaps": [
            "Execution price evidence linkage unavailable" if execution_evidence.get("availability") != "linked" else None,
            "Risk Event linkage unavailable for causality attribution" if not risk_panel.get("can_attribute_risk_engine") else None,
            "Preview linkage unavailable" if (preview is None and not _decision_predates_feature(decision=decision, feature_key="preview_linkage")) else None,
        ],
    }


def _preview_panel_payload(
    *,
    preview: CryptoOrderPreview | None,
    live_order: LiveCryptoOrder | None,
    approval_event: LiveApprovalEvent | None,
) -> dict[str, Any]:
    if preview is None:
        return {
            "availability": "unavailable",
            "state_reason": "no_preview_linked",
            "preview_id": None,
            "requested_amount": None,
            "approved_amount": None,
            "estimated_quantity": None,
            "estimated_fees": None,
            "expiration": None,
            "submission_state": "not_applicable",
            "execution_state": "not_applicable",
            "human_approval_state": "not_applicable",
        }

    return {
        "availability": "linked",
        "state_reason": None,
        "preview_id": preview.preview_id,
        "requested_amount": _decimal_to_str(preview.requested_amount),
        "approved_amount": _decimal_to_str(preview.estimated_quote_size),
        "estimated_quantity": _decimal_to_str(preview.estimated_base_size),
        "estimated_fees": _decimal_to_str(preview.estimated_fee),
        "expiration": preview.expires_at.isoformat(),
        "submission_state": live_order.status if live_order is not None else "not_submitted",
        "execution_state": live_order.status if live_order is not None else "not_executed",
        "human_approval_state": approval_event.approval_state if approval_event is not None else "not_applicable",
    }


def _counterfactual_panel_payload(rows: list[DecisionCounterfactualResult]) -> dict[str, Any]:
    if not rows:
        return {
            "availability": "unavailable",
            "state_reason": "counterfactual_outcomes_unavailable",
            "items": [],
            "summary": "Counterfactual package unavailable because no horizon evaluations are linked yet.",
        }

    items = []
    for row in rows:
        items.append(
            {
                "horizon": row.horizon_label,
                "evaluation_horizon_minutes": row.horizon_minutes,
                "alternative_actions": {
                    "buy_return_pct": _decimal_to_str(row.shadow_buy_return_pct),
                    "sell_return_pct": _decimal_to_str(row.shadow_sell_return_pct),
                    "wait_return_pct": _decimal_to_str(row.shadow_wait_return_pct),
                },
                "expected_return_pct": _decimal_to_str(row.shadow_buy_return_pct),
                "expected_downside_pct": _decimal_to_str(row.shadow_sell_return_pct),
                "confidence": row.evaluation_state,
                "expected_value": _decimal_to_str(row.shadow_wait_return_pct),
                "best_action": row.best_action,
            }
        )

    return {
        "availability": "linked",
        "state_reason": None,
        "items": items,
        "summary": "Counterfactual package linked; horizons can be compared without mutating decision history.",
    }


def _linkage_health_payload(
    *,
    decision: DecisionRecord,
    snapshot: DecisionSnapshot | None,
    execution_evidence: dict[str, Any],
    risk_event: RiskEvent | None,
    preview: CryptoOrderPreview | None,
    approval_event: LiveApprovalEvent | None,
    live_order: LiveCryptoOrder | None,
    counterfactual_rows: list[DecisionCounterfactualResult],
    quality: DecisionQualityScore | None,
    audit_events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    action = (_record_action(decision) or "hold").lower()
    hold_like = action in {"hold", "wait"}

    preview_refs = _lineage_refs(decision=decision, key="crypto_order_previews")
    preview_status = "unavailable"
    preview_reason = "Inspector cannot determine whether preview linkage should exist."
    if hold_like:
        preview_status = "not_applicable"
        preview_reason = "HOLD/WAIT workflows do not require preview/approval/submission/execution stages."
    elif preview is not None:
        preview_status = "rejected" if preview.status in {"RISK_REJECTED", "PREVIEW_FAILED", "CANCELLED"} else "completed"
        preview_reason = f"Preview record linked with status {preview.status}."
    elif preview_refs:
        preview_status = "missing"
        preview_reason = "Source lineage references a preview, but the preview record is unavailable."
    elif approval_event is not None or live_order is not None:
        preview_status = "missing"
        preview_reason = "Downstream records exist, so preview linkage should exist but is missing."
    elif _decision_predates_feature(decision=decision, feature_key="preview_linkage"):
        preview_status = "unavailable"
        preview_reason = "This decision predates preview linkage persistence in this repository."
    elif decision.trade_accepted:
        preview_status = "pending"
        preview_reason = "Preview linkage is expected later for an accepted non-HOLD decision."

    approval_status = "unavailable"
    approval_reason = "Inspector cannot determine approval applicability from persisted linkage."
    if hold_like:
        approval_status = "not_applicable"
        approval_reason = "HOLD/WAIT workflows do not require approval."
    elif approval_event is not None:
        approval_status = "completed"
        approval_reason = f"Approval event linked with state {approval_event.approval_state}."
    elif live_order is not None:
        approval_status = "missing"
        approval_reason = "Live submission exists but approval linkage is missing."
    elif preview is not None and preview.status in {"RISK_REJECTED", "PREVIEW_FAILED", "CANCELLED"}:
        approval_status = "not_applicable"
        approval_reason = f"Preview ended at {preview.status}; approval stage did not apply."
    elif preview is not None:
        approval_status = "pending"
        approval_reason = "Preview exists; approval is expected next."
    elif preview_status == "unavailable":
        approval_status = "unavailable"
        approval_reason = "Preview linkage generation is unavailable for this historical decision."

    submission_status = "unavailable"
    submission_reason = "Inspector cannot determine submission applicability from persisted linkage."
    if hold_like:
        submission_status = "not_applicable"
        submission_reason = "HOLD/WAIT workflows do not require submission."
    elif live_order is not None:
        if live_order.status in {"FAILED", "CANCELLED", "REJECTED"}:
            submission_status = "rejected"
        elif live_order.status in {"SUBMITTED", "ACKNOWLEDGED", "FILLED", "EXECUTED"}:
            submission_status = "completed"
        else:
            submission_status = "pending"
        submission_reason = f"Live-order record linked with status {live_order.status}."
    elif approval_event is not None:
        submission_status = "pending"
        submission_reason = "Approval exists; submission is expected later."
    elif preview is not None and preview.status in {"RISK_REJECTED", "PREVIEW_FAILED", "CANCELLED"}:
        submission_status = "not_applicable"
        submission_reason = f"Preview ended at {preview.status}; submission stage did not apply."
    elif preview is not None:
        submission_status = "pending"
        submission_reason = "Preview exists; submission is pending approval/operator action."
    elif _decision_predates_feature(decision=decision, feature_key="live_submission"):
        submission_status = "unavailable"
        submission_reason = "This decision predates live submission persistence in this repository."

    execution_status = "unavailable"
    execution_reason = "Inspector cannot determine execution applicability from persisted linkage."
    if hold_like:
        execution_status = "not_applicable"
        execution_reason = "HOLD/WAIT workflows do not require execution."
    elif live_order is not None:
        if live_order.status in {"FILLED", "EXECUTED"}:
            execution_status = "completed"
            execution_reason = f"Execution reached terminal state {live_order.status}."
        elif live_order.status in {"FAILED", "CANCELLED", "REJECTED"}:
            execution_status = "rejected"
            execution_reason = f"Execution ended in governed non-fill state {live_order.status}."
        else:
            execution_status = "pending"
            execution_reason = f"Execution is pending with live-order status {live_order.status}."
    elif submission_status == "pending":
        execution_status = "pending"
        execution_reason = "Submission has not completed yet."
    elif submission_status in {"not_applicable", "unavailable"}:
        execution_status = submission_status
        execution_reason = submission_reason

    snapshot_status = "completed" if snapshot is not None else "missing"
    snapshot_reason = "Decision Snapshot linked."
    if snapshot is None and _decision_predates_feature(decision=decision, feature_key="decision_snapshot"):
        snapshot_status = "unavailable"
        snapshot_reason = "This decision predates Decision Snapshot persistence in this repository."
    elif snapshot is None:
        snapshot_reason = "Decision Snapshot linkage should exist but is missing."

    counterfactual_status = "completed" if counterfactual_rows else "unavailable"
    counterfactual_reason = "Counterfactual package linked."
    if not counterfactual_rows and _decision_predates_feature(decision=decision, feature_key="counterfactual"):
        counterfactual_reason = "This decision predates counterfactual persistence in this repository."
    elif not counterfactual_rows:
        counterfactual_reason = "Counterfactual package is not available for this decision yet."

    quality_status = "completed" if quality is not None else "unavailable"
    quality_reason = "Decision Quality score linked."
    if quality is None and _decision_predates_feature(decision=decision, feature_key="decision_quality"):
        quality_reason = "This decision predates Decision Quality persistence in this repository."
    elif quality is None:
        quality_reason = "Decision Quality score is not available for this decision yet."

    risk_status = "completed" if risk_event is not None else "missing"
    risk_reason = "Linked Risk Event is present."
    if risk_event is None and hold_like:
        risk_status = "not_applicable"
        risk_reason = "HOLD/WAIT decision has no linked Risk Event and no required risk-execution path."
    elif risk_event is None and decision.risk_adjustments:
        risk_status = "unavailable"
        risk_reason = "Risk steps are present in the decision record, but linked Risk Event causality is unavailable."
    elif risk_event is None:
        risk_reason = "Risk Event linkage should exist for causal attribution but is missing."

    execution_evidence_status = "completed" if execution_evidence.get("availability") == "linked" else "missing"
    execution_evidence_reason = "Execution price evidence linked."
    if execution_evidence_status == "missing":
        execution_evidence_reason = "Execution price evidence linkage is missing."

    return [
        {"component": "Decision Record", "status": "completed", "reason": "Decision Record is present."},
        {"component": "Execution Evidence", "status": execution_evidence_status, "reason": execution_evidence_reason},
        {"component": "Risk Event", "status": risk_status, "reason": risk_reason},
        {"component": "Preview", "status": preview_status, "reason": preview_reason},
        {"component": "Audit", "status": "completed" if audit_events else "missing", "reason": "Audit events are linked." if audit_events else "No linked audit events were found."},
        {"component": "Approval", "status": approval_status, "reason": approval_reason},
        {"component": "Submission", "status": submission_status, "reason": submission_reason},
        {"component": "Execution", "status": execution_status, "reason": execution_reason},
        {"component": "Counterfactual", "status": counterfactual_status, "reason": counterfactual_reason},
        {"component": "Decision Quality", "status": quality_status, "reason": quality_reason},
        {"component": "Decision Snapshot", "status": snapshot_status, "reason": snapshot_reason},
        {"component": "Rehearsal", "status": "unavailable", "reason": "Rehearsal linkage capability is not available in the current repository model."},
    ]


def _decision_predates_feature(*, decision: DecisionRecord, feature_key: str) -> bool:
    cutoff = _FEATURE_INTRODUCED_AT.get(feature_key)
    if cutoff is None:
        return False
    ts = decision.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts < cutoff


def _lineage_refs(*, decision: DecisionRecord, key: str) -> list[str]:
    lineage = decision.source_lineage if isinstance(decision.source_lineage, dict) else {}
    value = lineage.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
