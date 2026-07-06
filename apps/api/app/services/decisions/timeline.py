from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.risk_event import RiskEvent


KnownState = Literal["known", "unknown", "unavailable"]
DecisionStatus = Literal["approved", "resized", "rejected", "wait", "unknown"]


@dataclass(frozen=True, slots=True)
class TimelineReadFilters:
    account_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    strategy_id: uuid.UUID | None = None
    status: DecisionStatus | None = None


@dataclass(frozen=True, slots=True)
class TimelineStateField:
    value: str | None
    state: KnownState


@dataclass(frozen=True, slots=True)
class DecisionTimelineEntry:
    decision_id: uuid.UUID
    timestamp: datetime
    narrative: str
    status: DecisionStatus
    account_id: TimelineStateField
    asset_id: TimelineStateField
    strategy_id: TimelineStateField
    source_lineage: dict[str, list[str]]


async def read_decision_timeline(
    *,
    db: AsyncSession,
    filters: TimelineReadFilters,
) -> list[DecisionTimelineEntry]:
    decision_rows = await _load_decision_rows(db=db)
    if not decision_rows:
        return []

    risk_event_lookup = await _build_risk_event_lookup(db=db, decision_rows=decision_rows)

    entries: list[DecisionTimelineEntry] = []
    for decision_record, decision_snapshot in decision_rows:
        entry = _compose_timeline_entry(
            decision_record=decision_record,
            decision_snapshot=decision_snapshot,
            risk_event_lookup=risk_event_lookup,
        )
        if _matches_filters(entry=entry, filters=filters):
            entries.append(entry)

    entries.sort(key=lambda item: (item.timestamp, str(item.decision_id)), reverse=True)
    return entries


async def _load_decision_rows(
    *,
    db: AsyncSession,
) -> list[tuple[DecisionRecord, DecisionSnapshot | None]]:
    result = await db.execute(
        select(DecisionRecord, DecisionSnapshot)
        .outerjoin(DecisionSnapshot, DecisionSnapshot.decision_id == DecisionRecord.decision_id)
    )
    return [(row[0], row[1]) for row in result.all()]


async def _build_risk_event_lookup(
    *,
    db: AsyncSession,
    decision_rows: list[tuple[DecisionRecord, DecisionSnapshot | None]],
) -> dict[str, RiskEvent]:
    risk_event_ids: list[uuid.UUID] = []
    for decision_record, _ in decision_rows:
        for raw_id in decision_record.source_lineage.get("risk_events", []):
            try:
                risk_event_ids.append(uuid.UUID(raw_id))
            except ValueError:
                continue

    unique_ids = sorted(set(risk_event_ids))
    if not unique_ids:
        return {}

    result = await db.execute(select(RiskEvent).where(RiskEvent.id.in_(unique_ids)))
    events = list(result.scalars().all())
    return {str(item.id): item for item in events}


def _compose_timeline_entry(
    *,
    decision_record: DecisionRecord,
    decision_snapshot: DecisionSnapshot | None,
    risk_event_lookup: dict[str, RiskEvent],
) -> DecisionTimelineEntry:
    status = _resolve_status(decision_record=decision_record)

    asset_id = _resolve_asset_id(decision_record=decision_record)
    strategy_id = _resolve_strategy_id(decision_snapshot=decision_snapshot)
    account_id = _resolve_account_id(decision_record=decision_record, risk_event_lookup=risk_event_lookup)

    narrative = _build_narrative(
        decision_record=decision_record,
        status=status,
        asset_id=asset_id,
        strategy_id=strategy_id,
        account_id=account_id,
    )

    return DecisionTimelineEntry(
        decision_id=decision_record.decision_id,
        timestamp=decision_record.timestamp,
        narrative=narrative,
        status=status,
        account_id=account_id,
        asset_id=asset_id,
        strategy_id=strategy_id,
        source_lineage=decision_record.source_lineage,
    )


def _resolve_status(*, decision_record: DecisionRecord) -> DecisionStatus:
    signal_action = _first_signal_field(decision_record.generated_signals, key="action")
    if signal_action == "hold":
        return "wait"

    if decision_record.trade_accepted:
        resized = any(
            str(item.get("action_taken", "")).lower() == "resized"
            for item in (decision_record.risk_adjustments or [])
        )
        return "resized" if resized else "approved"

    if decision_record.trade_rejected_reason:
        return "rejected"

    return "unknown"


def _resolve_asset_id(*, decision_record: DecisionRecord) -> TimelineStateField:
    raw_asset = decision_record.asset or {}
    value = raw_asset.get("asset_id") if isinstance(raw_asset, dict) else None
    if value is None:
        return TimelineStateField(value=None, state="unknown")

    return TimelineStateField(value=str(value), state="known")


def _resolve_strategy_id(*, decision_snapshot: DecisionSnapshot | None) -> TimelineStateField:
    if decision_snapshot is None:
        return TimelineStateField(value=None, state="unavailable")

    strategy_inputs = decision_snapshot.strategy_inputs or {}
    value = strategy_inputs.get("strategy_id") if isinstance(strategy_inputs, dict) else None
    if value is None:
        return TimelineStateField(value=None, state="unknown")

    return TimelineStateField(value=str(value), state="known")


def _resolve_account_id(
    *,
    decision_record: DecisionRecord,
    risk_event_lookup: dict[str, RiskEvent],
) -> TimelineStateField:
    execution_details = decision_record.execution_details or {}
    if isinstance(execution_details, dict):
        value = execution_details.get("paper_account_id")
        if value is not None:
            return TimelineStateField(value=str(value), state="known")

    lineage_risk_events = decision_record.source_lineage.get("risk_events", [])
    for risk_event_id in lineage_risk_events:
        item = risk_event_lookup.get(risk_event_id)
        if item is not None and item.paper_account_id is not None:
            return TimelineStateField(value=str(item.paper_account_id), state="known")

    if lineage_risk_events:
        return TimelineStateField(value=None, state="unknown")

    return TimelineStateField(value=None, state="unavailable")


def _first_signal_field(signals: list[dict[str, Any]] | None, *, key: str) -> str | None:
    if not signals:
        return None
    first = signals[0]
    if not isinstance(first, dict):
        return None
    value = first.get(key)
    return str(value) if value is not None else None


def _build_narrative(
    *,
    decision_record: DecisionRecord,
    status: DecisionStatus,
    asset_id: TimelineStateField,
    strategy_id: TimelineStateField,
    account_id: TimelineStateField,
) -> str:
    action = _first_signal_field(decision_record.generated_signals, key="action") or "unknown"

    asset_phrase = asset_id.value if asset_id.state == "known" else f"asset:{asset_id.state}"
    strategy_phrase = strategy_id.value if strategy_id.state == "known" else f"strategy:{strategy_id.state}"
    account_phrase = account_id.value if account_id.state == "known" else f"account:{account_id.state}"

    if status == "rejected":
        reason = decision_record.trade_rejected_reason or "unknown_reason"
        return (
            f"{action.upper()} signal for {asset_phrase} was rejected ({reason}); "
            f"context {strategy_phrase} on {account_phrase}."
        )

    if status == "wait":
        return (
            f"WAIT decision for {asset_phrase}; "
            f"context {strategy_phrase} on {account_phrase}."
        )

    if status == "resized":
        qty = None
        if isinstance(decision_record.execution_details, dict):
            qty = decision_record.execution_details.get("quantity")
        qty_phrase = qty if qty is not None else "unknown_quantity"
        return (
            f"{action.upper()} signal for {asset_phrase} was approved with resize to {qty_phrase}; "
            f"context {strategy_phrase} on {account_phrase}."
        )

    if status == "approved":
        return (
            f"{action.upper()} signal for {asset_phrase} was approved; "
            f"context {strategy_phrase} on {account_phrase}."
        )

    return (
        f"Decision state unknown for {asset_phrase}; "
        f"context {strategy_phrase} on {account_phrase}."
    )


def _matches_filters(*, entry: DecisionTimelineEntry, filters: TimelineReadFilters) -> bool:
    if filters.account_id is not None:
        if entry.account_id.state != "known" or entry.account_id.value != str(filters.account_id):
            return False

    if filters.asset_id is not None:
        if entry.asset_id.state != "known" or entry.asset_id.value != str(filters.asset_id):
            return False

    if filters.strategy_id is not None:
        if entry.strategy_id.state != "known" or entry.strategy_id.value != str(filters.strategy_id):
            return False

    if filters.status is not None and entry.status != filters.status:
        return False

    return True