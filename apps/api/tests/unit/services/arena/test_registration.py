from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.arena_agent_registration import ArenaAgentRegistration
from app.models.arena_participating_agent import ArenaParticipatingAgent
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy
from app.services.arena.contracts import (
    ArenaAgentRegistrationRequest,
    ArenaAgentVersionIdentityContract,
)
from app.services.arena.registration import (
    build_registration_hash,
    register_arena_agent,
    validate_arena_eligibility,
)


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _ExecuteResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return _ScalarResult(self._items)


class _BeginContext:
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self, *, strategies: list[Strategy]) -> None:
        self.strategies = strategies
        self.registrations: list[ArenaAgentRegistration] = []
        self.participants: list[ArenaParticipatingAgent] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM arena_agent_registrations" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            for item in self.registrations:
                if item.idempotency_key == key:
                    return item
            return None

        if "FROM strategies" in sql:
            slug = params.get("slug_1")
            version = params.get("module_version_1")
            for item in self.strategies:
                if item.slug == slug and item.module_version == version:
                    return item.id
            return None

        if "FROM arena_participating_agents" in sql:
            agent_identity = params.get("agent_identity_1")
            competition_id = params.get("competition_id_1")
            for item in self.participants:
                if item.agent_identity == agent_identity and item.competition_id == competition_id:
                    return item.id
            return None

        return None

    async def execute(self, statement: Any) -> _ExecuteResult:
        return _ExecuteResult([])

    async def flush(self) -> None:
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, ArenaAgentRegistration):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.registrations.append(obj)
            return
        if isinstance(obj, ArenaParticipatingAgent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.participants.append(obj)
            return
        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)


def _request(**overrides: Any) -> ArenaAgentRegistrationRequest:
    base: dict[str, Any] = {
        "competition_id": uuid.uuid4(),
        "strategy_id": "ma_crossover",
        "strategy_version": "v1",
        "semantic_version": "1.0.0",
        "registration_source": "human_api",
        "requested_by": "human.reviewer",
        "provenance_metadata": {"ticket": "ARENA-82"},
        "paper_only_eligible": True,
        "live_capital_eligible": False,
        "human_governed": True,
        "autonomous_self_modifying": False,
        "idempotency_key": "reg-key",
    }
    base.update(overrides)
    return ArenaAgentRegistrationRequest(**base)


def _strategy(*, slug: str = "ma_crossover", module_version: str = "v1") -> Strategy:
    return Strategy(
        id=uuid.uuid4(),
        name="MA Crossover",
        slug=slug,
        description=None,
        module_version=module_version,
        is_active=True,
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def test_registration_hash_is_deterministic() -> None:
    agent_id = uuid.uuid4()
    version_id = uuid.uuid4()
    created_at = datetime(2026, 7, 6, tzinfo=timezone.utc)

    hash_a = build_registration_hash(
        agent_id=agent_id,
        version_id=version_id,
        semantic_version="1.0.0",
        created_at=created_at,
        provenance_metadata={"ticket": "ARENA-82"},
        registration_source="human_api",
    )
    hash_b = build_registration_hash(
        agent_id=agent_id,
        version_id=version_id,
        semantic_version="1.0.0",
        created_at=created_at,
        provenance_metadata={"ticket": "ARENA-82"},
        registration_source="human_api",
    )

    assert hash_a == hash_b


def test_version_identity_is_immutable() -> None:
    identity = ArenaAgentVersionIdentityContract(
        agent_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        semantic_version="1.0.0",
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        provenance_metadata={"source": "test"},
        registration_source="human_api",
        registration_hash="hash",
    )

    with pytest.raises(AttributeError):
        identity.semantic_version = "1.0.1"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"paper_only_eligible": False}, "paper_only_competitions_required"),
        ({"live_capital_eligible": True}, "live_capital_eligibility_not_allowed"),
        ({"autonomous_self_modifying": True}, "autonomous_agents_not_allowed"),
        ({"human_governed": False}, "human_governed_registration_required"),
        ({"registration_source": "system_task"}, "human_registration_source_required"),
    ],
)
def test_validate_arena_eligibility_rejections_have_explicit_reason(
    kwargs: dict[str, Any],
    reason: str,
) -> None:
    result = validate_arena_eligibility(
        paper_only_eligible=kwargs.get("paper_only_eligible", True),
        strategy_version_registered=True,
        immutable_version_identity=True,
        live_capital_eligible=kwargs.get("live_capital_eligible", False),
        autonomous_self_modifying=kwargs.get("autonomous_self_modifying", False),
        human_governed=kwargs.get("human_governed", True),
        registration_source=kwargs.get("registration_source", "human_api"),
    )

    assert result.eligible is False
    assert result.rejection_reason == reason


@pytest.mark.asyncio
async def test_registration_accepts_and_writes_audit_and_participant_records() -> None:
    session = _FakeSession(strategies=[_strategy()])
    result = await register_arena_agent(db=session, request=_request())

    assert result.accepted is True
    assert result.rejection_reason is None
    assert result.participating_agent_id is not None
    assert len(session.registrations) == 1
    assert session.registrations[0].eligibility_status == "accepted"
    assert len(session.participants) == 1
    assert len(session.audit_logs) == 1
    assert session.audit_logs[0].action == "arena.agent_registration.accepted"


@pytest.mark.asyncio
async def test_registration_rejects_unregistered_strategy_with_auditable_reason() -> None:
    session = _FakeSession(strategies=[])
    result = await register_arena_agent(db=session, request=_request(idempotency_key="reject-key"))

    assert result.accepted is False
    assert result.rejection_reason == "registered_strategy_version_required"
    assert result.participating_agent_id is None
    assert len(session.registrations) == 1
    assert session.registrations[0].eligibility_status == "rejected"
    assert session.registrations[0].rejection_reason == "registered_strategy_version_required"
    assert len(session.participants) == 0
    assert len(session.audit_logs) == 1
    assert session.audit_logs[0].action == "arena.agent_registration.rejected"
    assert session.audit_logs[0].after_state["rejection_reason"] == "registered_strategy_version_required"


@pytest.mark.asyncio
async def test_registration_idempotency_returns_existing_record() -> None:
    session = _FakeSession(strategies=[_strategy()])
    request = _request(idempotency_key="same-key")

    first = await register_arena_agent(db=session, request=request)
    second = await register_arena_agent(db=session, request=request)

    assert first.registration_record_id == second.registration_record_id
    assert first.identity.agent_id == second.identity.agent_id
    assert len(session.registrations) == 1