from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.services.replay import default_agent


@dataclass(frozen=True)
class _AvailabilityState:
    ready: bool = True


def test_default_replay_agent_reconstructs_deterministically(monkeypatch) -> None:
    decision_id = uuid.uuid4()
    decision_package_id = "dpkg:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

    candidate = SimpleNamespace(
        decision_id=decision_id,
        decision_package_id=decision_package_id,
        package_hash="hash",
        package_version="v1",
        replay_ready=True,
    )
    package = SimpleNamespace(
        decision_id=decision_id,
        built_at=datetime(2026, 7, 9, 12, tzinfo=timezone.utc),
        decision_record=SimpleNamespace(
            confidence=Decimal("0.875"),
            generated_signals=[{"action": "buy"}],
            supporting_strategies=[{"name": "MA Crossover"}],
            opposing_strategies=[],
        ),
        decision_snapshot=SimpleNamespace(strategy_inputs={"strategy": "MA Crossover"}),
        availability_state=_AvailabilityState(),
    )

    async def _fake_candidates(*, db: Any) -> list[Any]:
        return [candidate]

    async def _fake_build(self: Any, *, db: Any, decision_id: uuid.UUID) -> Any:
        assert decision_id == candidate.decision_id
        return package

    monkeypatch.setattr(default_agent, "list_replay_candidates_v0", _fake_candidates)
    monkeypatch.setattr(default_agent.DecisionPackageBuilder, "build_decision_package", _fake_build)

    result = asyncio.run(default_agent.replay_decision_package_v0(db=SimpleNamespace(), decision_package_id=decision_package_id))

    assert result.decision_package_id == decision_package_id
    assert result.reconstructed_action == "BUY"
    assert result.confidence == Decimal("0.875")
    assert result.metadata["replay_ready"] is True
