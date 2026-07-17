from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import InvalidRequestError

from app.core.errors import ServiceUnavailableError
from app.models.audit_log import AuditLog
from app.models.risk_kill_switch import RiskKillSwitch
from app.services.risk import risk_monitor


class _BeginContext:
    def __init__(self, db: "_FakeSession") -> None:
        self._db = db

    async def __aenter__(self) -> "_BeginContext":
        self._db._in_explicit_transaction = True
        self._db._autobegun = False
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._db._in_explicit_transaction = False
        self._db._autobegun = False


class _FakeSession:
    def __init__(self) -> None:
        self.paper_accounts: dict[uuid.UUID, object] = {}
        self.kill_switches: list[RiskKillSwitch] = []
        self.audit_logs: list[AuditLog] = []
        self._in_explicit_transaction = False
        self._autobegun = False

    def begin(self) -> _BeginContext:
        if self._in_explicit_transaction or self._autobegun:
            raise InvalidRequestError("A transaction is already begun on this Session.")
        return _BeginContext(self)

    async def get(self, model, obj_id):
        _ = model
        if not self._in_explicit_transaction:
            self._autobegun = True
        return self.paper_accounts.get(obj_id)

    async def scalar(self, statement):
        if not self._in_explicit_transaction:
            self._autobegun = True

        sql = str(statement)
        params = statement.compile().params

        if "FROM risk_kill_switches" in sql:
            scope = next((value for value in params.values() if value in {"global", "account"}), None)
            account_id = next((value for value in params.values() if isinstance(value, uuid.UUID)), None)
            for item in self.kill_switches:
                if item.scope != scope:
                    continue
                if item.paper_account_id != account_id:
                    continue
                return item
            return None

        if "FROM risk_events" in sql:
            return None

        return None

    def add(self, obj):
        if isinstance(obj, RiskKillSwitch):
            if obj.id is None:
                obj.id = uuid.uuid4()
            if obj.changed_at is None:
                obj.changed_at = datetime.now(timezone.utc)

            for index, existing in enumerate(self.kill_switches):
                if existing.scope == obj.scope and existing.paper_account_id == obj.paper_account_id:
                    self.kill_switches[index] = obj
                    break
            else:
                self.kill_switches.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None


def _seed_account(db: _FakeSession) -> uuid.UUID:
    account_id = uuid.uuid4()
    db.paper_accounts[account_id] = SimpleNamespace(
        id=account_id,
        starting_balance=Decimal("25"),
        current_cash_balance=Decimal("25"),
    )
    return account_id


async def _policy(*_args, **_kwargs):
    return SimpleNamespace(
        max_position_size_pct=Decimal("0.10"),
        max_daily_loss_pct=Decimal("0.03"),
        max_drawdown_pct=Decimal("0.10"),
        default_stop_loss_pct=Decimal("0.03"),
        cooldown_after_losses=3,
        cooldown_duration_hours=24,
        source="system_default_config",
    )


async def _trusted_equity(*_args, **_kwargs):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        ready=True,
        fail_closed_reason=None,
        unresolved_reconciliation_count=0,
        unknown_provider_order_count=0,
        valuation=SimpleNamespace(
            generated_at=now,
            current_equity=Decimal("25"),
            cash_balance=Decimal("25"),
            position_value=Decimal("0"),
            latest_price_timestamp=now,
            valuation_source="paper_account_snapshot_mark_to_market_candles",
            valuation_state="ready",
            missing_price_assets=[],
            stale_price_assets=[],
        ),
        baseline=SimpleNamespace(
            start_of_day_equity=Decimal("25"),
            high_water_mark_equity=Decimal("25"),
            start_of_day_source="rolled_from_prior_last_equity",
            high_water_mark_source="updated_from_current_equity_observation",
            baseline_state="ready",
            baseline_ready=True,
        ),
    )


@pytest.mark.asyncio
async def test_account_disable_initializes_missing_row_without_transaction_collision() -> None:
    db = _FakeSession()
    account_id = _seed_account(db)

    result = await risk_monitor.disable_kill_switch(
        db=db,
        scope="account",
        account_id=account_id,
        reason="bootstrap account disarm",
        confirm=True,
        actor="operator:human",
    )

    assert result.scope == "account"
    assert result.engaged is False
    switch = next(item for item in db.kill_switches if item.scope == "account" and item.paper_account_id == account_id)
    assert switch.engaged is False
    assert switch.rearm_required is False


@pytest.mark.asyncio
async def test_global_disable_initializes_missing_row() -> None:
    db = _FakeSession()

    result = await risk_monitor.disable_kill_switch(
        db=db,
        scope="global",
        account_id=None,
        reason="bootstrap global disarm",
        confirm=True,
        actor="operator:human",
    )

    assert result.scope == "global"
    assert result.engaged is False
    assert len([item for item in db.kill_switches if item.scope == "global" and item.paper_account_id is None]) == 1


@pytest.mark.asyncio
async def test_repeated_account_disable_is_idempotent_and_audited() -> None:
    db = _FakeSession()
    account_id = _seed_account(db)

    await risk_monitor.disable_kill_switch(
        db=db,
        scope="account",
        account_id=account_id,
        reason="bootstrap account disarm",
        confirm=True,
        actor="operator:human",
    )
    await risk_monitor.disable_kill_switch(
        db=db,
        scope="account",
        account_id=account_id,
        reason="repeat bootstrap account disarm",
        confirm=True,
        actor="operator:human",
    )

    matching = [item for item in db.kill_switches if item.scope == "account" and item.paper_account_id == account_id]
    assert len(matching) == 1
    assert matching[0].engaged is False
    assert matching[0].rearm_required is False
    disable_audits = [item for item in db.audit_logs if item.action == "risk.kill_switch.disable"]
    assert len(disable_audits) == 2


@pytest.mark.asyncio
async def test_account_disable_writes_expected_audit_payload() -> None:
    db = _FakeSession()
    account_id = _seed_account(db)

    await risk_monitor.disable_kill_switch(
        db=db,
        scope="account",
        account_id=account_id,
        reason="resume trading",
        confirm=True,
        actor="operator:human",
    )

    audit = db.audit_logs[-1]
    assert audit.action == "risk.kill_switch.disable"
    assert audit.entity_type == "risk_kill_switch"
    assert audit.before_state["engaged"] is False
    assert audit.after_state["engaged"] is False
    assert audit.after_state["rearm_required"] is False
    assert audit.after_state["reason"] == "resume trading"
    assert audit.after_state["changed_by"] == "operator:human"


@pytest.mark.asyncio
async def test_account_enable_works_without_transaction_collision() -> None:
    db = _FakeSession()
    account_id = _seed_account(db)

    result = await risk_monitor.enable_kill_switch(
        db=db,
        scope="account",
        account_id=account_id,
        reason="manual stop",
        confirm=True,
        actor="operator:human",
    )

    assert result.scope == "account"
    assert result.engaged is True
    switch = next(item for item in db.kill_switches if item.scope == "account" and item.paper_account_id == account_id)
    assert switch.engaged is True
    assert switch.rearm_required is True


@pytest.mark.asyncio
async def test_global_enable_works_without_transaction_collision() -> None:
    db = _FakeSession()

    result = await risk_monitor.enable_kill_switch(
        db=db,
        scope="global",
        account_id=None,
        reason="manual stop",
        confirm=True,
        actor="operator:human",
    )

    assert result.scope == "global"
    assert result.engaged is True
    switch = next(item for item in db.kill_switches if item.scope == "global" and item.paper_account_id is None)
    assert switch.engaged is True
    assert switch.rearm_required is True


@pytest.mark.asyncio
async def test_unknown_remains_fail_closed_before_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeSession()
    account_id = _seed_account(db)

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(risk_monitor, "resolve_equity_risk_evidence", _trusted_equity)

    with pytest.raises(ServiceUnavailableError, match="kill switch state is unknown"):
        await risk_monitor.get_risk_status(db=db, account_id=account_id)


@pytest.mark.asyncio
async def test_initialized_disarmed_account_becomes_readable_by_risk_status(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeSession()
    account_id = _seed_account(db)

    monkeypatch.setattr(risk_monitor, "resolve_effective_risk_policy", _policy)
    monkeypatch.setattr(risk_monitor, "resolve_equity_risk_evidence", _trusted_equity)

    await risk_monitor.disable_kill_switch(
        db=db,
        scope="global",
        account_id=None,
        reason="bootstrap global disarm",
        confirm=True,
        actor="operator:human",
    )
    await risk_monitor.disable_kill_switch(
        db=db,
        scope="account",
        account_id=account_id,
        reason="bootstrap account disarm",
        confirm=True,
        actor="operator:human",
    )

    status = await risk_monitor.get_risk_status(db=db, account_id=account_id)

    assert status.global_engaged is False
    assert status.account_engaged is False
    assert status.account_id == account_id
