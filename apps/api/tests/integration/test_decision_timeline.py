from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.risk_event import RiskEvent
from app.services.decisions.timeline import TimelineReadFilters, read_decision_timeline


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, rows: list[Any], scalar_items: list[Any] | None = None) -> None:
        self._rows = rows
        self._scalar_items = scalar_items if scalar_items is not None else rows

    def all(self) -> list[Any]:
        return self._rows

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._scalar_items)


class _FakeSession:
    def __init__(
        self,
        *,
        decision_rows: list[tuple[DecisionRecord, DecisionSnapshot | None]],
        risk_events: list[RiskEvent],
    ) -> None:
        self.decision_rows = decision_rows
        self.risk_events = risk_events

    async def execute(self, statement: Any) -> _ExecuteResult:
        sql = str(statement)
        params = statement.compile().params

        if "FROM decision_records" in sql:
            return _ExecuteResult(self.decision_rows)

        if "FROM risk_events" in sql:
            requested = {str(value) for value in params.values() if isinstance(value, uuid.UUID)}
            rows = [item for item in self.risk_events if str(item.id) in requested]
            return _ExecuteResult(rows, scalar_items=rows)

        return _ExecuteResult([])


def _make_decision_record(
    *,
    decision_id: uuid.UUID,
    ts: datetime,
    asset_id: uuid.UUID,
    action: str,
    trade_accepted: bool,
    rejected_reason: str | None,
    risk_action: str | None,
    account_id: uuid.UUID | None,
    strategy_id: uuid.UUID | None,
    include_risk_lineage: bool,
) -> tuple[DecisionRecord, DecisionSnapshot | None, list[RiskEvent]]:
    risk_event_id = uuid.uuid4()
    lineage = {
        "signals": [str(uuid.uuid4())],
        "model_outputs": [],
        "risk_events": [str(risk_event_id)] if include_risk_lineage else [],
        "trades": [],
    }

    risk_adjustments = []
    if risk_action is not None:
        risk_adjustments.append(
            {
                "risk_event_id": str(risk_event_id),
                "event_type": "risk_decision",
                "action_taken": risk_action,
                "detail": {},
            }
        )

    execution_details = None
    if account_id is not None and trade_accepted:
        execution_details = {
            "paper_account_id": str(account_id),
            "quantity": "0.01",
            "execution_venue": "internal_sim",
        }

    decision_record = DecisionRecord(
        decision_id=decision_id,
        idempotency_key=f"k:{decision_id}",
        source_lineage=lineage,
        field_provenance={},
        version="v1",
        timestamp=ts,
        asset={"asset_id": str(asset_id)},
        timeframe="unknown",
        market_regime={},
        indicators={},
        generated_signals=[{"action": action, "status": "generated"}],
        signal_strength=Decimal("0.5"),
        confidence=Decimal("0.6"),
        supporting_strategies=[],
        opposing_strategies=[],
        risk_adjustments=risk_adjustments,
        expected_risk=None,
        expected_reward=None,
        position_size=None,
        trade_accepted=trade_accepted,
        trade_rejected_reason=rejected_reason,
        execution_details=execution_details,
        exit_details=None,
        pnl=None,
        duration=None,
        outcome=None,
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=None,
        confidence_calibration=None,
        review_status="unreviewed",
        human_notes=None,
    )

    snapshot: DecisionSnapshot | None = None
    if strategy_id is not None:
        snapshot = DecisionSnapshot(
            decision_id=decision_id,
            timestamp=ts,
            asset={"asset_id": str(asset_id)},
            exchange="unknown",
            timeframe="unknown",
            ohlcv_context=[],
            indicators={},
            generated_features={},
            market_regime={},
            volatility={},
            spread_liquidity_context=None,
            strategy_inputs={"strategy_id": str(strategy_id)},
            risk_inputs={},
            current_position_state=None,
            open_trades=[],
            portfolio_exposure={},
            parameter_set_version="unknown",
            strategy_version=str(strategy_id),
            ai_model_version="unknown",
            decision_engine_version="v1",
            configuration_version="unknown",
        )

    events: list[RiskEvent] = []
    if include_risk_lineage:
        events.append(
            RiskEvent(
                id=risk_event_id,
                paper_account_id=account_id,
                related_signal_id=uuid.uuid4(),
                event_type="risk_decision",
                action_taken=risk_action or "blocked",
                detail={},
                created_at=ts,
            )
        )

    return decision_record, snapshot, events


@pytest.mark.asyncio
async def test_timeline_orders_entries_descending_by_timestamp() -> None:
    base = datetime(2026, 7, 6, tzinfo=timezone.utc)
    asset_id = uuid.uuid4()

    older, older_snapshot, older_events = _make_decision_record(
        decision_id=uuid.uuid4(),
        ts=base,
        asset_id=asset_id,
        action="buy",
        trade_accepted=True,
        rejected_reason=None,
        risk_action="approved",
        account_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        include_risk_lineage=True,
    )
    newer, newer_snapshot, newer_events = _make_decision_record(
        decision_id=uuid.uuid4(),
        ts=base + timedelta(minutes=1),
        asset_id=asset_id,
        action="sell",
        trade_accepted=False,
        rejected_reason="risk_rejected",
        risk_action="blocked",
        account_id=uuid.uuid4(),
        strategy_id=uuid.uuid4(),
        include_risk_lineage=True,
    )

    session = _FakeSession(
        decision_rows=[(older, older_snapshot), (newer, newer_snapshot)],
        risk_events=older_events + newer_events,
    )

    entries = await read_decision_timeline(db=session, filters=TimelineReadFilters())

    assert [item.decision_id for item in entries] == [newer.decision_id, older.decision_id]


@pytest.mark.asyncio
async def test_timeline_supports_account_asset_strategy_and_status_filters() -> None:
    base = datetime(2026, 7, 6, tzinfo=timezone.utc)
    target_account = uuid.uuid4()
    target_asset = uuid.uuid4()
    target_strategy = uuid.uuid4()

    matched, matched_snapshot, matched_events = _make_decision_record(
        decision_id=uuid.uuid4(),
        ts=base,
        asset_id=target_asset,
        action="buy",
        trade_accepted=True,
        rejected_reason=None,
        risk_action="resized",
        account_id=target_account,
        strategy_id=target_strategy,
        include_risk_lineage=True,
    )
    other, other_snapshot, other_events = _make_decision_record(
        decision_id=uuid.uuid4(),
        ts=base + timedelta(minutes=1),
        asset_id=uuid.uuid4(),
        action="hold",
        trade_accepted=False,
        rejected_reason="wait_signal",
        risk_action=None,
        account_id=None,
        strategy_id=uuid.uuid4(),
        include_risk_lineage=False,
    )

    session = _FakeSession(
        decision_rows=[(matched, matched_snapshot), (other, other_snapshot)],
        risk_events=matched_events + other_events,
    )

    entries = await read_decision_timeline(
        db=session,
        filters=TimelineReadFilters(
            account_id=target_account,
            asset_id=target_asset,
            strategy_id=target_strategy,
            status="resized",
        ),
    )

    assert len(entries) == 1
    assert entries[0].decision_id == matched.decision_id
    assert entries[0].status == "resized"


@pytest.mark.asyncio
async def test_timeline_preserves_unknown_unavailable_state_semantics() -> None:
    base = datetime(2026, 7, 6, tzinfo=timezone.utc)
    asset_id = uuid.uuid4()

    unknown_account, unknown_snapshot, unknown_events = _make_decision_record(
        decision_id=uuid.uuid4(),
        ts=base,
        asset_id=asset_id,
        action="sell",
        trade_accepted=False,
        rejected_reason="risk_rejected",
        risk_action="blocked",
        account_id=None,
        strategy_id=uuid.uuid4(),
        include_risk_lineage=True,
    )
    unavailable_account, _, unavailable_events = _make_decision_record(
        decision_id=uuid.uuid4(),
        ts=base + timedelta(minutes=1),
        asset_id=asset_id,
        action="hold",
        trade_accepted=False,
        rejected_reason="wait_signal",
        risk_action=None,
        account_id=None,
        strategy_id=None,
        include_risk_lineage=False,
    )

    session = _FakeSession(
        decision_rows=[(unknown_account, unknown_snapshot), (unavailable_account, None)],
        risk_events=unknown_events + unavailable_events,
    )

    entries = await read_decision_timeline(db=session, filters=TimelineReadFilters())
    by_id = {item.decision_id: item for item in entries}

    unknown_entry = by_id[unknown_account.decision_id]
    unavailable_entry = by_id[unavailable_account.decision_id]

    assert unknown_entry.account_id.state == "unknown"
    assert unknown_entry.strategy_id.state == "known"
    assert unavailable_entry.account_id.state == "unavailable"
    assert unavailable_entry.strategy_id.state == "unavailable"