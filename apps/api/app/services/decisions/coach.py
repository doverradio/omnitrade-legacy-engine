from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_experiment_recommendation import DecisionExperimentRecommendation
from app.models.decision_record import DecisionRecord


AI_COACH_ENGINE_VERSION = "ai_coach_v1"


@dataclass(frozen=True, slots=True)
class CoachReviewBatchResult:
    scanned_records: int
    inserted_recommendations: int
    skipped_existing: int
    recommendation_ids: list[uuid.UUID]


@dataclass(frozen=True, slots=True)
class CoachReviewSummary:
    total_decisions: int
    trade_accepted_count: int
    trade_rejected_count: int
    action_counts: dict[str, int]
    review_status_counts: dict[str, int]
    top_asset_ids: list[str]
    top_strategy_ids: list[str]
    period_start: datetime | None
    period_end: datetime | None


async def generate_ai_coach_batch_reviews(
    *,
    db: AsyncSession,
    lookback_hours: int = 24,
    limit: int = 250,
) -> CoachReviewBatchResult:
    safe_lookback_hours = max(1, lookback_hours)
    safe_limit = max(1, min(limit, 1000))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=safe_lookback_hours)
    result = await db.execute(
        select(DecisionRecord)
        .where(DecisionRecord.timestamp >= cutoff)
        .order_by(DecisionRecord.timestamp.desc(), DecisionRecord.decision_id.desc())
        .limit(safe_limit)
    )
    records = list(result.scalars().all())

    if not records:
        return CoachReviewBatchResult(
            scanned_records=0,
            inserted_recommendations=0,
            skipped_existing=0,
            recommendation_ids=[],
        )

    summary = _build_summary(records)
    idempotency_key = _build_idempotency_key(summary=summary, decision_ids=[item.decision_id for item in records])

    existing_recommendation_id = await db.scalar(
        select(DecisionExperimentRecommendation.id)
        .where(DecisionExperimentRecommendation.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing_recommendation_id is not None:
        return CoachReviewBatchResult(
            scanned_records=len(records),
            inserted_recommendations=0,
            skipped_existing=1,
            recommendation_ids=[],
        )

    recommendation = DecisionExperimentRecommendation(
        idempotency_key=idempotency_key,
        recommendation_engine_version=AI_COACH_ENGINE_VERSION,
        recommendation_type="recurring_decision_pattern",
        recommendation_category="pattern",
        confidence_level="medium",
        expected_impact_level="medium",
        required_human_review_level="required",
        supporting_evidence_refs=[
            {
                "source": "decision_records",
                "decision_count": summary.total_decisions,
                "period_start": summary.period_start.isoformat() if summary.period_start is not None else None,
                "period_end": summary.period_end.isoformat() if summary.period_end is not None else None,
            }
        ],
        originating_decision_ids=[str(item.decision_id) for item in records],
        explanation=_build_explanation(summary),
        suggested_experiment={
            "name": "ai_coach_batch_review",
            "objective": "Human review of recurring decision patterns in paper mode",
            "constraints": [
                "advisory_only",
                "no_auto_strategy_changes",
                "paper_mode_only",
            ],
            "summary": {
                "trade_accepted_count": summary.trade_accepted_count,
                "trade_rejected_count": summary.trade_rejected_count,
                "action_counts": summary.action_counts,
                "review_status_counts": summary.review_status_counts,
                "top_asset_ids": summary.top_asset_ids,
                "top_strategy_ids": summary.top_strategy_ids,
            },
        },
        evidence_state="known",
        state_reason=None,
        provenance={
            "engine_version": AI_COACH_ENGINE_VERSION,
            "source": "ai_coach_batch_review",
            "lookback_hours": safe_lookback_hours,
            "limit": safe_limit,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        advisory_only=True,
    )
    db.add(recommendation)
    await db.flush()

    return CoachReviewBatchResult(
        scanned_records=len(records),
        inserted_recommendations=1,
        skipped_existing=0,
        recommendation_ids=[recommendation.id],
    )


def _build_summary(records: list[DecisionRecord]) -> CoachReviewSummary:
    trade_accepted_count = sum(1 for item in records if bool(item.trade_accepted))
    trade_rejected_count = len(records) - trade_accepted_count

    action_counts: dict[str, int] = {}
    review_status_counts: dict[str, int] = {}
    asset_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}

    for record in records:
        action = _extract_action(record)
        action_counts[action] = action_counts.get(action, 0) + 1

        review_status = record.review_status or "unreviewed"
        review_status_counts[review_status] = review_status_counts.get(review_status, 0) + 1

        asset_id = _extract_asset_id(record)
        if asset_id is not None:
            asset_counts[asset_id] = asset_counts.get(asset_id, 0) + 1

        strategy_id = _extract_strategy_id(record)
        if strategy_id is not None:
            strategy_counts[strategy_id] = strategy_counts.get(strategy_id, 0) + 1

    period_end = max(item.timestamp for item in records)
    period_start = min(item.timestamp for item in records)

    return CoachReviewSummary(
        total_decisions=len(records),
        trade_accepted_count=trade_accepted_count,
        trade_rejected_count=trade_rejected_count,
        action_counts=dict(sorted(action_counts.items())),
        review_status_counts=dict(sorted(review_status_counts.items())),
        top_asset_ids=_top_keys(asset_counts),
        top_strategy_ids=_top_keys(strategy_counts),
        period_start=period_start,
        period_end=period_end,
    )


def _top_keys(counter: dict[str, int], *, size: int = 5) -> list[str]:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [key for key, _ in ranked[:size]]


def _extract_action(record: DecisionRecord) -> str:
    if record.generated_signals:
        value = record.generated_signals[0].get("action")
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _extract_asset_id(record: DecisionRecord) -> str | None:
    asset_value = record.asset.get("asset_id") if isinstance(record.asset, dict) else None
    if isinstance(asset_value, str) and asset_value:
        return asset_value
    return None


def _extract_strategy_id(record: DecisionRecord) -> str | None:
    if record.generated_signals:
        value = record.generated_signals[0].get("strategy_id")
        if isinstance(value, str) and value:
            return value
    return None


def _build_explanation(summary: CoachReviewSummary) -> str:
    return (
        "AI Coach batch review generated for recent paper-mode decisions. "
        f"Reviewed {summary.total_decisions} decisions "
        f"({summary.trade_accepted_count} accepted, {summary.trade_rejected_count} rejected/not_taken). "
        "This recommendation is advisory-only and requires explicit human review before any follow-up experiments."
    )


def _build_idempotency_key(*, summary: CoachReviewSummary, decision_ids: list[uuid.UUID]) -> str:
    payload: dict[str, Any] = {
        "engine": AI_COACH_ENGINE_VERSION,
        "period_start": summary.period_start.isoformat() if summary.period_start is not None else None,
        "period_end": summary.period_end.isoformat() if summary.period_end is not None else None,
        "decision_ids": sorted(str(item) for item in decision_ids),
        "action_counts": summary.action_counts,
        "review_status_counts": summary.review_status_counts,
    }
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"ai_coach_batch:{digest}"
