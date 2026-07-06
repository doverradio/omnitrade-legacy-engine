from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candle import Candle
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_record import DecisionRecord


CounterfactualAction = Literal["buy", "sell", "wait"]
CounterfactualEvaluationState = Literal["resolved", "unknown", "unavailable"]

V1_COUNTERFACTUAL_HORIZONS: tuple[tuple[str, int], ...] = (("15m", 15), ("1h", 60), ("24h", 1440))


@dataclass(frozen=True, slots=True)
class CounterfactualResultDraft:
    horizon_label: str
    horizon_minutes: int
    decision_timestamp: datetime
    evaluated_at: datetime
    asset_symbol: str
    actual_action: CounterfactualAction
    shadow_buy_return_pct: Decimal | None
    shadow_sell_return_pct: Decimal | None
    shadow_wait_return_pct: Decimal | None
    best_action: CounterfactualAction | None
    actual_action_correct: bool | None
    evaluation_state: CounterfactualEvaluationState
    state_reason: str | None
    lesson_tags: list[dict[str, str]]
    feature_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CounterfactualEvaluationRunResult:
    scanned_decisions: int
    inserted_results: int
    skipped_non_btc: int
    skipped_not_due: int
    skipped_existing: int


async def evaluate_counterfactual_outcome_ledger_v1(
    *,
    db: AsyncSession,
    as_of: datetime | None = None,
) -> CounterfactualEvaluationRunResult:
    now = _ensure_utc(as_of or datetime.now(timezone.utc))
    earliest_due_cutoff = now - timedelta(minutes=V1_COUNTERFACTUAL_HORIZONS[0][1])

    result = await db.execute(
        select(DecisionRecord)
        .where(DecisionRecord.timestamp <= earliest_due_cutoff)
        .order_by(DecisionRecord.timestamp.asc(), DecisionRecord.decision_id.asc())
    )
    decisions = list(result.scalars().all())

    inserted = 0
    skipped_non_btc = 0
    skipped_not_due = 0
    skipped_existing = 0

    for decision in decisions:
        asset_symbol = _extract_asset_symbol(asset=decision.asset)
        if not _is_btc_asset_symbol(asset_symbol):
            skipped_non_btc += 1
            continue

        action = _resolve_actual_action(decision_record=decision)
        asset_id = _extract_asset_id(decision_record=decision)

        for horizon_label, horizon_minutes in V1_COUNTERFACTUAL_HORIZONS:
            horizon_at = _ensure_utc(decision.timestamp) + timedelta(minutes=horizon_minutes)
            if horizon_at > now:
                skipped_not_due += 1
                continue

            existing_id = await db.scalar(
                select(DecisionCounterfactualResult.id)
                .where(DecisionCounterfactualResult.decision_id == decision.decision_id)
                .where(DecisionCounterfactualResult.horizon_minutes == horizon_minutes)
                .limit(1)
            )
            if existing_id is not None:
                skipped_existing += 1
                continue

            entry_price = (
                await _load_close_price_at_or_before(db=db, asset_id=asset_id, target_ts=_ensure_utc(decision.timestamp))
                if asset_id is not None
                else None
            )
            horizon_price = (
                await _load_close_price_at_or_before(db=db, asset_id=asset_id, target_ts=horizon_at)
                if asset_id is not None
                else None
            )

            draft = build_counterfactual_result_draft(
                decision_record=decision,
                asset_symbol=asset_symbol,
                actual_action=action,
                horizon_label=horizon_label,
                horizon_minutes=horizon_minutes,
                evaluated_at=now,
                entry_price=entry_price,
                horizon_price=horizon_price,
            )

            idempotency_key = build_counterfactual_result_idempotency_key(
                decision_id=decision.decision_id,
                horizon_minutes=horizon_minutes,
            )

            async with db.begin():
                db.add(
                    DecisionCounterfactualResult(
                        decision_id=decision.decision_id,
                        idempotency_key=idempotency_key,
                        horizon_label=draft.horizon_label,
                        horizon_minutes=draft.horizon_minutes,
                        decision_timestamp=draft.decision_timestamp,
                        evaluated_at=draft.evaluated_at,
                        asset_symbol=draft.asset_symbol,
                        actual_action=draft.actual_action,
                        shadow_buy_return_pct=draft.shadow_buy_return_pct,
                        shadow_sell_return_pct=draft.shadow_sell_return_pct,
                        shadow_wait_return_pct=draft.shadow_wait_return_pct,
                        best_action=draft.best_action,
                        actual_action_correct=draft.actual_action_correct,
                        evaluation_state=draft.evaluation_state,
                        state_reason=draft.state_reason,
                        lesson_tags=draft.lesson_tags,
                        feature_snapshot=draft.feature_snapshot,
                    )
                )

            inserted += 1

    return CounterfactualEvaluationRunResult(
        scanned_decisions=len(decisions),
        inserted_results=inserted,
        skipped_non_btc=skipped_non_btc,
        skipped_not_due=skipped_not_due,
        skipped_existing=skipped_existing,
    )


def build_counterfactual_result_draft(
    *,
    decision_record: DecisionRecord,
    asset_symbol: str,
    actual_action: CounterfactualAction,
    horizon_label: str,
    horizon_minutes: int,
    evaluated_at: datetime,
    entry_price: Decimal | None,
    horizon_price: Decimal | None,
) -> CounterfactualResultDraft:
    decision_ts = _ensure_utc(decision_record.timestamp)
    evaluated_ts = _ensure_utc(evaluated_at)

    feature_snapshot = {
        "asset_symbol": asset_symbol,
        "decision_timestamp": decision_ts.isoformat(),
        "horizon_label": horizon_label,
        "horizon_minutes": horizon_minutes,
        "regime_tag": decision_record.market_regime.get("regime_tag") if isinstance(decision_record.market_regime, dict) else None,
        "stated_confidence": _decimal_to_str(decision_record.confidence),
        "entry_price": _decimal_to_str(entry_price),
        "horizon_price": _decimal_to_str(horizon_price),
    }

    if entry_price is None or horizon_price is None or entry_price == Decimal("0"):
        state_reason = "missing_market_price_for_horizon"
        if entry_price is None and horizon_price is None:
            state_reason = "missing_entry_and_horizon_prices"
        elif entry_price is None:
            state_reason = "missing_entry_price"
        elif horizon_price is None:
            state_reason = "missing_horizon_price"

        return CounterfactualResultDraft(
            horizon_label=horizon_label,
            horizon_minutes=horizon_minutes,
            decision_timestamp=decision_ts,
            evaluated_at=evaluated_ts,
            asset_symbol=asset_symbol,
            actual_action=actual_action,
            shadow_buy_return_pct=None,
            shadow_sell_return_pct=None,
            shadow_wait_return_pct=None,
            best_action=None,
            actual_action_correct=None,
            evaluation_state="unavailable",
            state_reason=state_reason,
            lesson_tags=[{"tag": "counterfactual_data_unavailable", "reason": state_reason}],
            feature_snapshot=feature_snapshot,
        )

    buy_return = (horizon_price - entry_price) / entry_price
    sell_return = (entry_price - horizon_price) / entry_price
    wait_return = Decimal("0")

    action_returns: dict[CounterfactualAction, Decimal] = {
        "buy": buy_return,
        "sell": sell_return,
        "wait": wait_return,
    }
    best_action = _resolve_best_action(action_returns=action_returns)
    actual_correct = actual_action == best_action
    lesson_tags = _build_lesson_tags(
        decision_record=decision_record,
        actual_action=actual_action,
        best_action=best_action,
        actual_correct=actual_correct,
    )

    return CounterfactualResultDraft(
        horizon_label=horizon_label,
        horizon_minutes=horizon_minutes,
        decision_timestamp=decision_ts,
        evaluated_at=evaluated_ts,
        asset_symbol=asset_symbol,
        actual_action=actual_action,
        shadow_buy_return_pct=buy_return,
        shadow_sell_return_pct=sell_return,
        shadow_wait_return_pct=wait_return,
        best_action=best_action,
        actual_action_correct=actual_correct,
        evaluation_state="resolved",
        state_reason=None,
        lesson_tags=lesson_tags,
        feature_snapshot=feature_snapshot,
    )


def build_counterfactual_result_idempotency_key(*, decision_id: uuid.UUID, horizon_minutes: int) -> str:
    raw = f"{decision_id}:{horizon_minutes}"
    digest = sha256(raw.encode("ascii"), usedforsecurity=False).hexdigest()
    return f"counterfactual:{digest}"


async def _load_close_price_at_or_before(
    *,
    db: AsyncSession,
    asset_id: uuid.UUID,
    target_ts: datetime,
) -> Decimal | None:
    return await db.scalar(
        select(Candle.close)
        .where(Candle.asset_id == asset_id)
        .where(Candle.open_time <= target_ts)
        .order_by(Candle.open_time.desc())
        .limit(1)
    )


def _resolve_best_action(*, action_returns: dict[CounterfactualAction, Decimal]) -> CounterfactualAction:
    # Favor WAIT when returns are tied to avoid implying directional certainty.
    preference_order: tuple[CounterfactualAction, ...] = ("wait", "buy", "sell")
    best = preference_order[0]
    for candidate in preference_order[1:]:
        if action_returns[candidate] > action_returns[best]:
            best = candidate
    return best


def _build_lesson_tags(
    *,
    decision_record: DecisionRecord,
    actual_action: CounterfactualAction,
    best_action: CounterfactualAction,
    actual_correct: bool,
) -> list[dict[str, str]]:
    tags: list[dict[str, str]] = []

    if actual_action in {"wait", "sell"} and best_action == "buy":
        tags.append({"tag": "missed_breakout", "reason": "buy_outperformed_non_buy_action"})

    if actual_action == "buy" and best_action in {"sell", "wait"}:
        tags.append({"tag": "false_breakout", "reason": "buy_underperformed_counterfactuals"})

    if actual_action == "wait" and best_action == "wait":
        tags.append({"tag": "wait_was_correct", "reason": "wait_matched_hindsight_best_action"})

    confidence = decision_record.confidence
    if confidence is not None:
        if confidence >= Decimal("0.80") and not actual_correct:
            tags.append({"tag": "confidence_overestimated", "reason": "high_confidence_wrong_action"})
        if confidence <= Decimal("0.40") and actual_correct:
            tags.append({"tag": "confidence_underestimated", "reason": "low_confidence_right_action"})

    rejected_reason = (decision_record.trade_rejected_reason or "").lower()
    if actual_action == "wait" and "volatility" in rejected_reason and best_action == "wait":
        tags.append({"tag": "volatility_filter_saved_trade", "reason": "volatility_wait_aligned_with_hindsight"})

    regime_tag = str((decision_record.market_regime or {}).get("regime_tag") or "").lower()
    if actual_action == "wait" and "trend" in regime_tag and best_action != "wait":
        tags.append({"tag": "trend_filter_incorrect", "reason": "trend_wait_missed_better_action"})

    if not tags:
        tags.append({"tag": "counterfactual_neutral", "reason": "no_specific_v1_lesson_tag_triggered"})

    return tags


def _extract_asset_symbol(*, asset: dict[str, Any]) -> str:
    symbol_keys = ("symbol", "ticker", "asset_symbol", "base_asset", "name")
    for key in symbol_keys:
        value = asset.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return "UNKNOWN"


def _is_btc_asset_symbol(symbol: str) -> bool:
    normalized = symbol.upper()
    return normalized.startswith("BTC") or normalized.endswith("BTC") or "BTC" in normalized


def _extract_asset_id(*, decision_record: DecisionRecord) -> uuid.UUID | None:
    raw_id = (decision_record.asset or {}).get("asset_id")
    if not isinstance(raw_id, str) or not raw_id:
        return None
    try:
        return uuid.UUID(raw_id)
    except ValueError:
        return None


def _resolve_actual_action(*, decision_record: DecisionRecord) -> CounterfactualAction:
    action = "wait"
    if decision_record.generated_signals:
        candidate = decision_record.generated_signals[0]
        if isinstance(candidate, dict):
            value = str(candidate.get("action") or "").lower()
            if value in {"buy", "sell"}:
                action = value
            elif value in {"hold", "wait"}:
                action = "wait"
    return action


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")
