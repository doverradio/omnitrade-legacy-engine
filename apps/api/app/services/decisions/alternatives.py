from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_alternative_action import DecisionAlternativeAction
from app.models.decision_counterfactual_result import DecisionCounterfactualResult
from app.models.decision_record import DecisionRecord


Action = Literal["buy", "sell", "wait"]
AvailabilityState = Literal["known", "unknown", "unavailable"]
_ALL_ACTIONS: tuple[Action, ...] = ("buy", "sell", "wait")


@dataclass(frozen=True, slots=True)
class AlternativeActionDraft:
    chosen_action: Action
    alternative_action: Action
    reference_horizon_minutes: int | None
    comparison_payload: dict[str, Any]
    provenance: dict[str, Any]
    availability_state: AvailabilityState
    state_reason: str | None


@dataclass(frozen=True, slots=True)
class DecisionAlternativeActionReadModel:
    decision_id: uuid.UUID
    chosen_action: Action
    alternatives: list[dict[str, Any]]


async def persist_alternative_actions_for_decision(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
) -> int:
    decision_record = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.decision_id == decision_id)
        .limit(1)
    )
    if decision_record is None:
        return 0

    counterfactual_result = await db.execute(
        select(DecisionCounterfactualResult)
        .where(DecisionCounterfactualResult.decision_id == decision_id)
        .order_by(DecisionCounterfactualResult.horizon_minutes.asc(), DecisionCounterfactualResult.id.asc())
    )
    counterfactual_records = list(counterfactual_result.scalars().all())

    drafts = build_alternative_action_drafts(
        decision_record=decision_record,
        counterfactual_records=counterfactual_records,
    )

    inserted = 0
    for draft in drafts:
        key = build_alternative_action_idempotency_key(
            decision_id=decision_id,
            draft=draft,
        )

        existing = await db.scalar(
            select(DecisionAlternativeAction.id)
            .where(DecisionAlternativeAction.idempotency_key == key)
            .limit(1)
        )
        if existing is not None:
            continue

        async with db.begin():
            db.add(
                DecisionAlternativeAction(
                    decision_id=decision_id,
                    idempotency_key=key,
                    chosen_action=draft.chosen_action,
                    alternative_action=draft.alternative_action,
                    reference_horizon_minutes=draft.reference_horizon_minutes,
                    comparison_payload=draft.comparison_payload,
                    provenance=draft.provenance,
                    availability_state=draft.availability_state,
                    state_reason=draft.state_reason,
                )
            )

        inserted += 1

    return inserted


async def read_decision_alternative_actions(
    *,
    db: AsyncSession,
    decision_id: uuid.UUID,
) -> DecisionAlternativeActionReadModel | None:
    decision = await db.scalar(
        select(DecisionRecord)
        .where(DecisionRecord.decision_id == decision_id)
        .limit(1)
    )
    if decision is None:
        return None

    rows_result = await db.execute(
        select(DecisionAlternativeAction)
        .where(DecisionAlternativeAction.decision_id == decision_id)
        .order_by(DecisionAlternativeAction.alternative_action.asc(), DecisionAlternativeAction.created_at.asc())
    )
    rows = list(rows_result.scalars().all())

    chosen = _resolve_action(decision_record=decision)
    if not rows:
        return DecisionAlternativeActionReadModel(decision_id=decision_id, chosen_action=chosen, alternatives=[])

    alternatives = [
        {
            "alternative_action": item.alternative_action,
            "reference_horizon_minutes": item.reference_horizon_minutes,
            "comparison_payload": item.comparison_payload,
            "provenance": item.provenance,
            "availability_state": item.availability_state,
            "state_reason": item.state_reason,
        }
        for item in rows
    ]
    return DecisionAlternativeActionReadModel(
        decision_id=decision_id,
        chosen_action=chosen,
        alternatives=alternatives,
    )


def build_alternative_action_drafts(
    *,
    decision_record: DecisionRecord,
    counterfactual_records: list[DecisionCounterfactualResult],
) -> list[AlternativeActionDraft]:
    chosen = _resolve_action(decision_record=decision_record)
    alternatives = [action for action in _ALL_ACTIONS if action != chosen]

    resolved = [item for item in counterfactual_records if item.evaluation_state == "resolved"]
    any_counterfactual = bool(counterfactual_records)

    output: list[AlternativeActionDraft] = []
    for alternative in alternatives:
        if not any_counterfactual:
            output.append(
                _state_only_draft(
                    decision_record=decision_record,
                    chosen_action=chosen,
                    alternative_action=alternative,
                    availability_state="unavailable",
                    state_reason="counterfactual_unavailable",
                )
            )
            continue

        if not resolved:
            output.append(
                _state_only_draft(
                    decision_record=decision_record,
                    chosen_action=chosen,
                    alternative_action=alternative,
                    availability_state="unknown",
                    state_reason="counterfactual_unresolved",
                )
            )
            continue

        payload, horizon = _comparison_payload_for_actions(
            chosen_action=chosen,
            alternative_action=alternative,
            records=resolved,
        )
        output.append(
            AlternativeActionDraft(
                chosen_action=chosen,
                alternative_action=alternative,
                reference_horizon_minutes=horizon,
                comparison_payload=payload,
                provenance=_provenance(decision_record=decision_record, counterfactual_records=resolved),
                availability_state="known",
                state_reason=None,
            )
        )

    return output


def build_alternative_action_idempotency_key(*, decision_id: uuid.UUID, draft: AlternativeActionDraft) -> str:
    payload = {
        "decision_id": str(decision_id),
        "chosen_action": draft.chosen_action,
        "alternative_action": draft.alternative_action,
        "reference_horizon_minutes": draft.reference_horizon_minutes,
        "comparison_payload": draft.comparison_payload,
        "provenance": draft.provenance,
        "availability_state": draft.availability_state,
        "state_reason": draft.state_reason,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = sha256(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"decision_alt:{digest}"


def _comparison_payload_for_actions(
    *,
    chosen_action: Action,
    alternative_action: Action,
    records: list[DecisionCounterfactualResult],
) -> tuple[dict[str, Any], int | None]:
    chosen_returns = [_action_return(record=item, action=chosen_action) for item in records]
    alternative_returns = [_action_return(record=item, action=alternative_action) for item in records]

    chosen_avg = _mean_decimal(chosen_returns)
    alternative_avg = _mean_decimal(alternative_returns)
    delta = _quantize(alternative_avg - chosen_avg)

    horizon = records[0].horizon_minutes if records else None
    explanation = (
        f"Alternative action {alternative_action} changes expected return by {delta} "
        f"versus chosen action {chosen_action} across resolved horizons."
    )

    return (
        {
            "chosen_action": chosen_action,
            "alternative_action": alternative_action,
            "chosen_average_return_pct": _to_str(chosen_avg),
            "alternative_average_return_pct": _to_str(alternative_avg),
            "expected_return_delta_pct": _to_str(delta),
            "changed_fields": [
                {
                    "field": "expected_return_delta_pct",
                    "from": _to_str(chosen_avg),
                    "to": _to_str(alternative_avg),
                    "delta": _to_str(delta),
                }
            ],
            "summary": explanation,
        },
        horizon,
    )


def _state_only_draft(
    *,
    decision_record: DecisionRecord,
    chosen_action: Action,
    alternative_action: Action,
    availability_state: AvailabilityState,
    state_reason: str,
) -> AlternativeActionDraft:
    summary = (
        f"Alternative action {alternative_action} cannot be compared with chosen action {chosen_action} "
        f"because {state_reason}."
    )
    return AlternativeActionDraft(
        chosen_action=chosen_action,
        alternative_action=alternative_action,
        reference_horizon_minutes=None,
        comparison_payload={
            "chosen_action": chosen_action,
            "alternative_action": alternative_action,
            "changed_fields": [],
            "summary": summary,
        },
        provenance=_provenance(decision_record=decision_record, counterfactual_records=[]),
        availability_state=availability_state,
        state_reason=state_reason,
    )


def _provenance(
    *,
    decision_record: DecisionRecord,
    counterfactual_records: list[DecisionCounterfactualResult],
) -> dict[str, Any]:
    return {
        "source_ids": {
            "decision_record": str(decision_record.decision_id),
            "counterfactual_results": sorted(str(item.id) for item in counterfactual_records),
        },
        "lineage": {
            "decision_record_lineage": decision_record.source_lineage,
            "resolved_horizons": sorted(item.horizon_minutes for item in counterfactual_records),
        },
    }


def _action_return(*, record: DecisionCounterfactualResult, action: Action) -> Decimal:
    if action == "buy":
        return record.shadow_buy_return_pct or Decimal("0")
    if action == "sell":
        return record.shadow_sell_return_pct or Decimal("0")
    return record.shadow_wait_return_pct or Decimal("0")


def _resolve_action(*, decision_record: DecisionRecord) -> Action:
    if not decision_record.generated_signals:
        return "wait"

    first = decision_record.generated_signals[0]
    if not isinstance(first, dict):
        return "wait"

    raw = str(first.get("action") or "").lower()
    if raw in {"buy", "sell"}:
        return raw
    return "wait"


def _mean_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return _quantize(sum(values, Decimal("0")) / Decimal(len(values)))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _to_str(value: Decimal) -> str:
    return format(value, "f")
