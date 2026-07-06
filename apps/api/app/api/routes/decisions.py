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
from app.services.decisions.explainability import read_decision_explainability
from app.services.decisions.recommendations import read_experiment_recommendations
from app.services.decisions.timeline import TimelineReadFilters, read_decision_timeline

router = APIRouter(prefix="/decisions", tags=["decisions"])

MAX_PAGE_SIZE = 200


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
