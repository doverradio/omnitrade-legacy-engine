from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ReplayResult:
    replay_id: uuid.UUID
    replay_agent_id: uuid.UUID
    decision_package_id: str
    replay_timestamp: datetime
    reconstructed_action: Literal["BUY", "SELL", "HOLD"]
    confidence: Decimal | None
    supporting_evidence: tuple[dict[str, Any], ...]
    explanation: str | None
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

    async def replay(self, *, db: AsyncSession, decision_package_id: str) -> ReplayResult: ...
