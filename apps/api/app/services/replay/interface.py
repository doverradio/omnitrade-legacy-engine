from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class ReplayResult:
    replay_id: uuid.UUID
    replay_agent_id: uuid.UUID
    strategy_name: str
    decision_package_id: uuid.UUID
    replay_timestamp: datetime
    decision_outcome: Literal["BUY", "SELL", "HOLD"]
    confidence: Decimal | None
    supporting_evidence: tuple[dict[str, Any], ...]
    explanation: str | None
    simulated_execution_metrics: dict[str, Any]
    risk_assessment: dict[str, Any]
    quality_metrics: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplayAgentCapability:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ReplayAgentRegistration:
    replay_agent_id: uuid.UUID
    name: str
    status: str
    capabilities: tuple[ReplayAgentCapability, ...]
    decision_package_consumer: bool
    execution_logic: bool
    processing_enabled: bool
    scheduling_enabled: bool
    writes_enabled: bool


class ReplayAgent(Protocol):
    replay_agent_id: uuid.UUID
    name: str
    status: str

    async def replay(self, *, decision_package_id: uuid.UUID) -> ReplayResult: ...
