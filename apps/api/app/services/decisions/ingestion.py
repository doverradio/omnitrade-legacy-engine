from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision_record import DecisionRecord
from app.models.decision_snapshot import DecisionSnapshot
from app.models.model_output import ModelOutput
from app.models.risk_event import RiskEvent
from app.models.signal import Signal
from app.models.trade import Trade
from app.services.decisions.contracts import (
    DecisionProvenanceContract,
    DecisionRecordContract,
    DecisionSnapshotContract,
)
from app.services.decisions.provenance import (
    DECISION_RECORD_PROVENANCE_MAPPING,
    DECISION_SNAPSHOT_PROVENANCE_MAPPING,
    validate_provenance_mappings,
)


DECISION_ENGINE_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class DecisionIngestionResult:
    scanned_signals: int
    inserted_records: int
    skipped_existing: int


def build_signal_idempotency_key(signal_id: uuid.UUID) -> str:
    digest = sha256(str(signal_id).encode("ascii"), usedforsecurity=False).hexdigest()
    return f"signal:{digest}"


async def ingest_decision_records(
    *,
    db: AsyncSession,
    signal_ids: Iterable[uuid.UUID] | None = None,
) -> DecisionIngestionResult:
    validate_provenance_mappings()
    signal_id_list = sorted(set(signal_ids)) if signal_ids is not None else None
    signals = await _load_signals(db=db, signal_ids=signal_id_list)

    inserted_records = 0
    skipped_existing = 0

    for signal in signals:
        idempotency_key = build_signal_idempotency_key(signal.id)
        existing_decision_id = await db.scalar(
            select(DecisionRecord.decision_id)
            .where(DecisionRecord.idempotency_key == idempotency_key)
            .limit(1)
        )
        if existing_decision_id is not None:
            skipped_existing += 1
            continue

        model_outputs = await _load_model_outputs(db=db, signal_id=signal.id)
        risk_events = await _load_risk_events(db=db, signal_id=signal.id)
        trades = await _load_trades(db=db, signal_id=signal.id)

        provenance = DecisionProvenanceContract(
            signals=[signal.id],
            model_outputs=[item.id for item in model_outputs],
            risk_events=[item.id for item in risk_events],
            trades=[item.id for item in trades],
        )

        decision_record_contract = _compose_decision_record(
            signal=signal,
            model_outputs=model_outputs,
            risk_events=risk_events,
            trades=trades,
        )
        decision_snapshot_contract = _compose_decision_snapshot(
            signal=signal,
            model_outputs=model_outputs,
            risk_events=risk_events,
            trades=trades,
        )

        source_lineage = _serialize_lineage(provenance=provenance)
        field_provenance = _build_field_provenance(provenance=provenance)

        async with db.begin():
            decision_record = DecisionRecord(
                idempotency_key=idempotency_key,
                source_lineage=source_lineage,
                field_provenance=field_provenance,
                version=decision_record_contract.version,
                timestamp=decision_record_contract.timestamp,
                asset=decision_record_contract.asset,
                timeframe=decision_record_contract.timeframe,
                market_regime=decision_record_contract.market_regime,
                indicators=decision_record_contract.indicators,
                generated_signals=decision_record_contract.generated_signals,
                signal_strength=decision_record_contract.signal_strength,
                confidence=decision_record_contract.confidence,
                supporting_strategies=decision_record_contract.supporting_strategies,
                opposing_strategies=decision_record_contract.opposing_strategies,
                risk_adjustments=decision_record_contract.risk_adjustments,
                expected_risk=decision_record_contract.expected_risk,
                expected_reward=decision_record_contract.expected_reward,
                position_size=decision_record_contract.position_size,
                trade_accepted=decision_record_contract.trade_accepted,
                trade_rejected_reason=decision_record_contract.trade_rejected_reason,
                execution_details=decision_record_contract.execution_details,
                exit_details=decision_record_contract.exit_details,
                pnl=decision_record_contract.pnl,
                duration=decision_record_contract.duration,
                outcome=decision_record_contract.outcome,
                post_trade_notes=decision_record_contract.post_trade_notes,
                lessons_learned=decision_record_contract.lessons_learned,
                ai_reflection=decision_record_contract.ai_reflection,
                future_tags=decision_record_contract.future_tags,
                confidence_calibration=decision_record_contract.confidence_calibration,
                review_status=decision_record_contract.review_status,
                human_notes=decision_record_contract.human_notes,
            )
            db.add(decision_record)
            await db.flush()

            decision_snapshot = DecisionSnapshot(
                decision_id=decision_record.decision_id,
                timestamp=decision_snapshot_contract.timestamp,
                asset=decision_snapshot_contract.asset,
                exchange=decision_snapshot_contract.exchange,
                timeframe=decision_snapshot_contract.timeframe,
                ohlcv_context=decision_snapshot_contract.ohlcv_context,
                indicators=decision_snapshot_contract.indicators,
                generated_features=decision_snapshot_contract.generated_features,
                market_regime=decision_snapshot_contract.market_regime,
                volatility=decision_snapshot_contract.volatility,
                spread_liquidity_context=decision_snapshot_contract.spread_liquidity_context,
                strategy_inputs=decision_snapshot_contract.strategy_inputs,
                risk_inputs=decision_snapshot_contract.risk_inputs,
                current_position_state=decision_snapshot_contract.current_position_state,
                open_trades=decision_snapshot_contract.open_trades,
                portfolio_exposure=decision_snapshot_contract.portfolio_exposure,
                parameter_set_version=decision_snapshot_contract.parameter_set_version,
                strategy_version=decision_snapshot_contract.strategy_version,
                ai_model_version=decision_snapshot_contract.ai_model_version,
                decision_engine_version=decision_snapshot_contract.decision_engine_version,
                configuration_version=decision_snapshot_contract.configuration_version,
            )
            db.add(decision_snapshot)

        inserted_records += 1

    return DecisionIngestionResult(
        scanned_signals=len(signals),
        inserted_records=inserted_records,
        skipped_existing=skipped_existing,
    )


async def _load_signals(*, db: AsyncSession, signal_ids: list[uuid.UUID] | None) -> list[Signal]:
    statement = select(Signal).order_by(Signal.signal_time.asc(), Signal.id.asc())
    if signal_ids:
        statement = statement.where(Signal.id.in_(signal_ids))

    result = await db.execute(statement)
    return list(result.scalars().all())


async def _load_model_outputs(*, db: AsyncSession, signal_id: uuid.UUID) -> list[ModelOutput]:
    result = await db.execute(
        select(ModelOutput)
        .where(ModelOutput.related_signal_id == signal_id)
        .order_by(ModelOutput.created_at.asc(), ModelOutput.id.asc())
    )
    return list(result.scalars().all())


async def _load_risk_events(*, db: AsyncSession, signal_id: uuid.UUID) -> list[RiskEvent]:
    result = await db.execute(
        select(RiskEvent)
        .where(RiskEvent.related_signal_id == signal_id)
        .order_by(RiskEvent.created_at.asc(), RiskEvent.id.asc())
    )
    return list(result.scalars().all())


async def _load_trades(*, db: AsyncSession, signal_id: uuid.UUID) -> list[Trade]:
    result = await db.execute(
        select(Trade)
        .where(Trade.signal_id == signal_id)
        .order_by(Trade.executed_at.asc(), Trade.id.asc())
    )
    return list(result.scalars().all())


def _compose_decision_record(
    *,
    signal: Signal,
    model_outputs: list[ModelOutput],
    risk_events: list[RiskEvent],
    trades: list[Trade],
) -> DecisionRecordContract:
    decision_state = _resolve_decision_state(signal=signal, risk_events=risk_events, trades=trades)
    trade_accepted = decision_state in {"approved", "resized"}

    latest_trade = trades[-1] if trades else None
    risk_adjustments = [
        {
            "risk_event_id": str(item.id),
            "event_type": item.event_type,
            "action_taken": item.action_taken,
            "detail": item.detail,
        }
        for item in risk_events
    ]

    supporting = [
        {
            "model_output_id": str(item.id),
            "model_name": item.model_name,
            "evidence": item.output,
        }
        for item in model_outputs
        if item.model_name in {"signal_scorer", "allocator", "explainer"}
    ]
    opposing = [
        {
            "model_output_id": str(item.id),
            "model_name": item.model_name,
            "evidence": item.output,
        }
        for item in model_outputs
        if item.model_name not in {"signal_scorer", "allocator", "explainer"}
    ]

    trade_rejected_reason: str | None = None
    if decision_state == "rejected":
        rejected_event = next((item for item in risk_events if item.action_taken == "blocked"), None)
        if rejected_event is not None:
            trade_rejected_reason = str(rejected_event.detail.get("reason_code") or "risk_rejected")
        else:
            trade_rejected_reason = "risk_rejected"
    elif decision_state == "wait":
        trade_rejected_reason = "wait_signal"

    execution_details: dict[str, Any] | None = None
    if latest_trade is not None:
        execution_details = {
            "trade_id": str(latest_trade.id),
            "paper_account_id": str(latest_trade.paper_account_id),
            "execution_venue": latest_trade.execution_venue,
            "side": latest_trade.side,
            "quantity": _decimal_to_str(latest_trade.quantity),
            "price": _decimal_to_str(latest_trade.price),
            "fee": _decimal_to_str(latest_trade.fee),
            "executed_at": latest_trade.executed_at.isoformat(),
        }

    indicators: dict[str, Any] = {}
    if model_outputs:
        indicators = dict(model_outputs[0].input_summary)

    confidence_calibration: dict[str, Any] | None = None
    if signal.ai_confidence is not None:
        confidence_calibration = {
            "stated_confidence": _decimal_to_str(signal.ai_confidence),
            "evaluation_state": "pending_outcome",
        }

    return DecisionRecordContract(
        version=DECISION_ENGINE_VERSION,
        timestamp=signal.signal_time,
        asset={"asset_id": str(signal.asset_id)},
        timeframe="unknown",
        market_regime={
            "regime_tag": signal.regime_tag,
            "confidence": _decimal_to_str(signal.ai_confidence),
        },
        indicators=indicators,
        generated_signals=[
            {
                "signal_id": str(signal.id),
                "action": signal.action,
                "status": signal.status,
            }
        ],
        signal_strength=signal.raw_strength,
        confidence=signal.ai_confidence,
        supporting_strategies=supporting,
        opposing_strategies=opposing,
        risk_adjustments=risk_adjustments,
        expected_risk=None,
        expected_reward=None,
        position_size=latest_trade.quantity if latest_trade is not None else None,
        trade_accepted=trade_accepted,
        trade_rejected_reason=trade_rejected_reason,
        execution_details=execution_details,
        exit_details=None,
        pnl=None,
        duration=None,
        outcome=_resolve_outcome(decision_state=decision_state, has_trade=latest_trade is not None),
        post_trade_notes=None,
        lessons_learned=None,
        ai_reflection=None,
        future_tags=None,
        confidence_calibration=confidence_calibration,
        review_status="unreviewed",
        human_notes=None,
    )


def _compose_decision_snapshot(
    *,
    signal: Signal,
    model_outputs: list[ModelOutput],
    risk_events: list[RiskEvent],
    trades: list[Trade],
) -> DecisionSnapshotContract:
    first_input_summary = dict(model_outputs[0].input_summary) if model_outputs else {}
    latest_model_version = model_outputs[-1].model_version if model_outputs else "unknown"

    return DecisionSnapshotContract(
        timestamp=signal.signal_time,
        asset={"asset_id": str(signal.asset_id)},
        exchange="unknown",
        timeframe="unknown",
        ohlcv_context=[],
        indicators=first_input_summary,
        generated_features=first_input_summary,
        market_regime={
            "regime_tag": signal.regime_tag,
            "confidence": _decimal_to_str(signal.ai_confidence),
        },
        volatility=first_input_summary.get("volatility", {}),
        spread_liquidity_context=first_input_summary.get("spread_liquidity_context"),
        strategy_inputs={
            "signal_id": str(signal.id),
            "strategy_id": str(signal.strategy_id),
            "parameter_set_id": str(signal.parameter_set_id),
            "action": signal.action,
            "raw_strength": _decimal_to_str(signal.raw_strength),
            "ai_confidence": _decimal_to_str(signal.ai_confidence),
        },
        risk_inputs={
            "signal_status": signal.status,
            "risk_events": [
                {
                    "risk_event_id": str(item.id),
                    "event_type": item.event_type,
                    "action_taken": item.action_taken,
                    "detail": item.detail,
                }
                for item in risk_events
            ],
        },
        current_position_state=None,
        open_trades=[
            {
                "trade_id": str(item.id),
                "side": item.side,
                "quantity": _decimal_to_str(item.quantity),
                "price": _decimal_to_str(item.price),
                "executed_at": item.executed_at.isoformat(),
            }
            for item in trades
        ],
        portfolio_exposure={
            "trade_count_for_signal": len(trades),
            "total_notional_for_signal": _decimal_to_str(
                sum((item.quantity * item.price for item in trades), Decimal("0"))
            ),
        },
        parameter_set_version=str(signal.parameter_set_id),
        strategy_version=str(signal.strategy_id),
        ai_model_version=latest_model_version,
        decision_engine_version=DECISION_ENGINE_VERSION,
        configuration_version="unknown",
    )


def _resolve_decision_state(*, signal: Signal, risk_events: list[RiskEvent], trades: list[Trade]) -> str:
    if signal.action == "hold":
        return "wait"

    if any(item.action_taken == "resized" for item in risk_events):
        return "resized"
    if any(item.action_taken == "approved" for item in risk_events):
        return "approved"
    if any(item.action_taken == "blocked" for item in risk_events):
        return "rejected"

    if trades:
        return "approved"
    if signal.status in {"risk_rejected", "expired"}:
        return "rejected"
    if signal.status in {"risk_approved", "executed"}:
        return "approved"

    return "wait"


def _resolve_outcome(*, decision_state: str, has_trade: bool) -> str:
    if has_trade:
        return "executed"
    if decision_state in {"rejected", "wait"}:
        return "not_taken"
    return "pending"


def _serialize_lineage(*, provenance: DecisionProvenanceContract) -> dict[str, list[str]]:
    return {
        "signals": _sorted_uuid_strings(provenance.signals),
        "model_outputs": _sorted_uuid_strings(provenance.model_outputs),
        "risk_events": _sorted_uuid_strings(provenance.risk_events),
        "trades": _sorted_uuid_strings(provenance.trades),
    }


def _build_field_provenance(*, provenance: DecisionProvenanceContract) -> dict[str, list[dict[str, Any]]]:
    lineage = _serialize_lineage(provenance=provenance)
    combined_mapping: dict[str, tuple[str, ...]] = {
        **DECISION_RECORD_PROVENANCE_MAPPING,
        **DECISION_SNAPSHOT_PROVENANCE_MAPPING,
    }

    result: dict[str, list[dict[str, Any]]] = {}
    for field_name in sorted(combined_mapping):
        entries: list[dict[str, Any]] = []
        for source_ref in combined_mapping[field_name]:
            source_root = source_ref.split(".", 1)[0]
            entries.append(
                {
                    "source_ref": source_ref,
                    "record_ids": lineage.get(source_root, []),
                }
            )
        result[field_name] = entries

    return result


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _sorted_uuid_strings(values: Iterable[uuid.UUID]) -> list[str]:
    return [str(value) for value in sorted(values)]