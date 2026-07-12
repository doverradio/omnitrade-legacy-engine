from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.models.live_trading_event import LiveTradingEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.models.paper_account import PaperAccount
from app.services.live.contracts import LiveAccountRegistrationRequest
from app.services.live.registration import (
    build_live_registration_idempotency_key,
    register_live_account,
    validate_live_registration_eligibility,
)


class _BeginContext:
    async def __aenter__(self) -> _BeginContext:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self, *, paper_accounts: list[PaperAccount]) -> None:
        self.paper_accounts = paper_accounts
        self.live_profiles: list[LiveTradingProfile] = []
        self.live_events: list[LiveTradingEvent] = []
        self._in_transaction = False
        self.begin_calls = 0

    def begin(self) -> _BeginContext:
        self.begin_calls += 1
        return _BeginContext()

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_trading_events" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            event_type = params.get("event_type_1")
            for item in self.live_events:
                if item.idempotency_key == key and item.event_type == event_type:
                    return item
            return None

        if "FROM live_trading_profiles" in sql:
            profile_id = params.get("id_1")
            for item in self.live_profiles:
                if item.id == profile_id:
                    return item
            return None

        if "FROM paper_accounts" in sql:
            paper_account_id = params.get("id_1")
            for item in self.paper_accounts:
                if item.id == paper_account_id:
                    return item
            return None

        if "max(live_trading_events.sequence_number)" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            candidates = [item.sequence_number for item in self.live_events if item.live_trading_profile_id == profile_id]
            if not candidates:
                return None
            return max(candidates)

        return None

    async def flush(self) -> None:
        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveTradingProfile):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.live_profiles.append(obj)
            return
        if isinstance(obj, LiveTradingEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.live_events.append(obj)


def _paper_account(*, is_active: bool = True) -> PaperAccount:
    return PaperAccount(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        name="paper-account",
        asset_class="crypto",
        starting_balance=1000,
        current_cash_balance=1000,
        is_active=is_active,
        created_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


def _request(*, paper_account_id: uuid.UUID, **overrides: Any) -> LiveAccountRegistrationRequest:
    payload: dict[str, Any] = {
        "paper_account_id": paper_account_id,
        "requested_by": "human.operator",
        "registration_source": "human_console",
        "live_opt_in": True,
        "governance_approved": False,
        "human_approval_recorded": False,
        "provenance_metadata": {"ticket": "LIVE-92"},
        "idempotency_key": "live-reg-key",
    }
    payload.update(overrides)
    return LiveAccountRegistrationRequest(**payload)


def test_live_registration_idempotency_key_is_deterministic() -> None:
    paper_account_id = uuid.uuid4()
    key_a = build_live_registration_idempotency_key(
        paper_account_id=paper_account_id,
        registration_source="human_console",
        live_opt_in=True,
    )
    key_b = build_live_registration_idempotency_key(
        paper_account_id=paper_account_id,
        registration_source="human_console",
        live_opt_in=True,
    )
    assert key_a == key_b


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"paper_account_exists": False}, "paper_account_not_found"),
        ({"paper_account_active": False}, "paper_account_inactive"),
        ({"registration_source": "system_task"}, "human_registration_source_required"),
        ({"live_opt_in": False}, "live_opt_in_required"),
        ({"autonomous_capital_allocation": True}, "autonomous_capital_allocation_not_allowed"),
        ({"autonomous_strategy_evolution": True}, "autonomous_strategy_evolution_not_allowed"),
        ({"automatic_promotion_enabled": True}, "automatic_promotion_not_allowed"),
        ({"risk_authority_model": "alternate_gate"}, "risk_engine_final_authority_required"),
    ],
)
def test_validate_live_registration_eligibility_rejections(kwargs: dict[str, Any], reason: str) -> None:
    result = validate_live_registration_eligibility(
        paper_account_exists=kwargs.get("paper_account_exists", True),
        paper_account_active=kwargs.get("paper_account_active", True),
        registration_source=kwargs.get("registration_source", "human_console"),
        live_opt_in=kwargs.get("live_opt_in", True),
        autonomous_capital_allocation=kwargs.get("autonomous_capital_allocation", False),
        autonomous_strategy_evolution=kwargs.get("autonomous_strategy_evolution", False),
        automatic_promotion_enabled=kwargs.get("automatic_promotion_enabled", False),
        risk_authority_model=kwargs.get("risk_authority_model", "risk_engine_final"),
    )

    assert result.eligible is False
    assert result.rejection_reason == reason


@pytest.mark.asyncio
async def test_register_live_account_creates_pending_approval_profile_with_paper_default() -> None:
    paper = _paper_account(is_active=True)
    session = _FakeSession(paper_accounts=[paper])

    result = await register_live_account(db=session, request=_request(paper_account_id=paper.id))

    assert result.accepted is True
    assert result.rejection_reason is None
    assert result.readiness_state == "pending_approval"
    assert result.operating_mode == "paper"
    assert len(session.live_profiles) == 1
    profile = session.live_profiles[0]
    assert profile.lifecycle_state == "pending_approval"
    assert profile.approval_state == "pending"
    assert profile.operating_mode == "paper"
    assert profile.live_opt_in is True
    assert profile.human_approval_recorded is False
    assert profile.paper_default_mode is True
    assert profile.risk_authority_model == "risk_engine_final"
    assert len(session.live_events) == 1
    assert session.live_events[0].event_type == "registration_created"


@pytest.mark.asyncio
async def test_register_live_account_rejects_when_paper_account_inactive() -> None:
    paper = _paper_account(is_active=False)
    session = _FakeSession(paper_accounts=[paper])

    result = await register_live_account(db=session, request=_request(paper_account_id=paper.id, idempotency_key="inactive-key"))

    assert result.accepted is False
    assert result.rejection_reason == "paper_account_inactive"
    assert result.readiness_state == "draft"
    assert result.operating_mode == "paper"
    assert len(session.live_profiles) == 1
    assert session.live_profiles[0].lifecycle_state == "draft"
    assert session.live_profiles[0].approval_state == "not_requested"
    assert len(session.live_events) == 1


@pytest.mark.asyncio
async def test_register_live_account_is_idempotent() -> None:
    paper = _paper_account(is_active=True)
    session = _FakeSession(paper_accounts=[paper])
    request = _request(paper_account_id=paper.id, idempotency_key="same-live-key")

    first = await register_live_account(db=session, request=request)
    second = await register_live_account(db=session, request=request)

    assert first.live_trading_profile_id == second.live_trading_profile_id
    assert first.created_event_id == second.created_event_id
    assert len(session.live_profiles) == 1
    assert len(session.live_events) == 1


@pytest.mark.asyncio
async def test_register_live_account_reuses_active_caller_transaction() -> None:
    paper = _paper_account(is_active=True)
    session = _FakeSession(paper_accounts=[paper])
    session._in_transaction = True

    result = await register_live_account(
        db=session,
        request=_request(paper_account_id=paper.id, idempotency_key="active-tx-key"),
    )

    assert result.accepted is True
    assert len(session.live_profiles) == 1
    assert len(session.live_events) == 1
    assert session.begin_calls == 0
