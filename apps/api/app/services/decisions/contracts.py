from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class DecisionRecordContract:
    version: str
    timestamp: datetime
    asset: dict[str, Any]
    timeframe: str
    market_regime: dict[str, Any]
    indicators: dict[str, Any]
    generated_signals: list[dict[str, Any]]
    signal_strength: Decimal | None
    confidence: Decimal | None
    supporting_strategies: list[dict[str, Any]]
    opposing_strategies: list[dict[str, Any]]
    risk_adjustments: list[dict[str, Any]]
    expected_risk: dict[str, Any] | None
    expected_reward: dict[str, Any] | None
    position_size: Decimal | None
    trade_accepted: bool
    trade_rejected_reason: str | None
    execution_details: dict[str, Any] | None
    exit_details: dict[str, Any] | None
    pnl: dict[str, Any] | None
    duration: str | None
    outcome: str | None
    post_trade_notes: dict[str, Any] | None
    lessons_learned: list[dict[str, Any]] | None
    ai_reflection: dict[str, Any] | None
    future_tags: list[str] | None
    confidence_calibration: dict[str, Any] | None
    review_status: str | None
    human_notes: str | None


@dataclass(frozen=True, slots=True)
class DecisionSnapshotContract:
    timestamp: datetime
    asset: dict[str, Any]
    exchange: str
    timeframe: str
    ohlcv_context: list[dict[str, Any]]
    indicators: dict[str, Any]
    generated_features: dict[str, Any]
    market_regime: dict[str, Any]
    volatility: dict[str, Any]
    spread_liquidity_context: dict[str, Any] | None
    strategy_inputs: dict[str, Any]
    risk_inputs: dict[str, Any]
    current_position_state: dict[str, Any] | None
    open_trades: list[dict[str, Any]]
    portfolio_exposure: dict[str, Any]
    parameter_set_version: str
    strategy_version: str
    ai_model_version: str
    decision_engine_version: str
    configuration_version: str


@dataclass(frozen=True, slots=True)
class DecisionProvenanceContract:
    signals: list[uuid.UUID]
    model_outputs: list[uuid.UUID]
    risk_events: list[uuid.UUID]
    trades: list[uuid.UUID]


class DecisionWriteServiceContract(Protocol):
    async def create_decision_record(
        self,
        *,
        decision_record: DecisionRecordContract,
        provenance: DecisionProvenanceContract,
    ) -> uuid.UUID: ...

    async def create_decision_snapshot(
        self,
        *,
        decision_id: uuid.UUID,
        decision_snapshot: DecisionSnapshotContract,
    ) -> None: ...
