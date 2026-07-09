from __future__ import annotations

import uuid

from app.services.replay.interface import ReplayAgentCapability, ReplayAgentRegistration

DEFAULT_REPLAY_AGENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")

_REGISTERED_REPLAY_AGENTS: tuple[ReplayAgentRegistration, ...] = (
    ReplayAgentRegistration(
        replay_agent_id=DEFAULT_REPLAY_AGENT_ID,
        name="Default Replay Agent",
        status="Registered",
        capabilities=(
            ReplayAgentCapability(
                name="Decision Package consumer",
                description="Consumes immutable Decision Packages for read-only research analysis.",
            ),
        ),
        decision_package_consumer=True,
        execution_logic=False,
        processing_enabled=False,
        scheduling_enabled=False,
        writes_enabled=False,
    ),
)


def list_registered_replay_agents() -> tuple[ReplayAgentRegistration, ...]:
    return _REGISTERED_REPLAY_AGENTS


def get_default_replay_agent() -> ReplayAgentRegistration:
    return _REGISTERED_REPLAY_AGENTS[0]
