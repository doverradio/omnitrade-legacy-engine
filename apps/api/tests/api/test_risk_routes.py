from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.audit_log import AuditLog
from app.models.paper_account import PaperAccount
from app.models.risk_kill_switch import RiskKillSwitch
from app.models.risk_rule_config import RiskRuleConfig


class _BeginContext:
    async def __aenter__(self) -> "_BeginContext":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.paper_accounts: dict[uuid.UUID, PaperAccount] = {}
        self.kill_switches: list[RiskKillSwitch] = []
        self.rule_configs: list[RiskRuleConfig] = []
        self.audit_logs: list[AuditLog] = []

    def begin(self) -> _BeginContext:
        return _BeginContext()

    async def flush(self) -> None:
        return None

    async def get(self, model: Any, obj_id: uuid.UUID) -> Any:
        if model is PaperAccount:
            return self.paper_accounts.get(obj_id)
        return None

    async def scalar(self, statement: Any) -> Any:
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

        if "FROM risk_rule_configs" in sql:
            account_id = next((value for value in params.values() if isinstance(value, uuid.UUID)), None)
            if "IS NULL" in sql:
                account_id = None
            for item in self.rule_configs:
                if item.paper_account_id == account_id:
                    return item
            return None

        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, RiskKillSwitch):
            if obj.id is None:
                obj.id = uuid.uuid4()
            if obj.changed_at is None:
                obj.changed_at = datetime.now(timezone.utc)
            self.kill_switches.append(obj)
            return

        if isinstance(obj, RiskRuleConfig):
            if obj.id is None:
                obj.id = uuid.uuid4()
            if obj.updated_at is None:
                obj.updated_at = datetime.now(timezone.utc)
            self.rule_configs.append(obj)
            return

        if isinstance(obj, AuditLog):
            self.audit_logs.append(obj)


def create_test_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _seed_account(fake_session: _FakeSession) -> uuid.UUID:
    account_id = uuid.uuid4()
    fake_session.paper_accounts[account_id] = PaperAccount(
        id=account_id,
        owner_user_id=uuid.uuid4(),
        name="Test Account",
        asset_class="crypto",
        starting_balance=Decimal("1000"),
        current_cash_balance=Decimal("975"),
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    return account_id


def test_risk_status_returns_503_when_kill_switch_state_unknown() -> None:
    fake_session = _FakeSession()
    account_id = _seed_account(fake_session)

    with create_test_client(fake_session) as client:
        response = client.get("/risk/status", params={"account_id": str(account_id)})

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "service_unavailable"


def test_enable_and_disable_kill_switch_require_confirm_and_write_audit() -> None:
    fake_session = _FakeSession()
    account_id = _seed_account(fake_session)

    with create_test_client(fake_session) as client:
        rejected = client.post(
            "/risk/kill-switch/enable",
            json={
                "scope": "account",
                "account_id": str(account_id),
                "reason": "manual stop",
                "confirm": False,
                "actor": "user:risk-admin",
            },
        )
        assert rejected.status_code == 400

        enabled = client.post(
            "/risk/kill-switch/enable",
            json={
                "scope": "account",
                "account_id": str(account_id),
                "reason": "manual stop",
                "confirm": True,
                "actor": "user:risk-admin",
            },
        )
        assert enabled.status_code == 200
        assert enabled.json()["engaged"] is True

        disabled = client.post(
            "/risk/kill-switch/disable",
            json={
                "scope": "account",
                "account_id": str(account_id),
                "reason": "resume trading",
                "confirm": True,
                "actor": "user:risk-admin",
            },
        )

    assert disabled.status_code == 200
    assert disabled.json()["engaged"] is False
    assert len(fake_session.audit_logs) == 2
    assert fake_session.audit_logs[0].action == "risk.kill_switch.enable"
    assert fake_session.audit_logs[1].action == "risk.kill_switch.disable"


def test_get_risk_rules_returns_defaults() -> None:
    fake_session = _FakeSession()

    with create_test_client(fake_session) as client:
        response = client.get("/risk/rules")

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_override"] is False
    assert payload["rules"]["max_position_size_pct"] == "0.10"
    assert payload["rules"]["max_daily_loss_pct"] == "0.03"


def test_patch_risk_rules_requires_confirm_for_loosening() -> None:
    fake_session = _FakeSession()

    with create_test_client(fake_session) as client:
        response = client.patch(
            "/risk/rules",
            json={
                "rules": {"max_daily_loss_pct": "0.04"},
                "actor": "user:risk-admin",
            },
        )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"


def test_patch_risk_rules_tighten_writes_audit() -> None:
    fake_session = _FakeSession()
    account_id = _seed_account(fake_session)

    with create_test_client(fake_session) as client:
        response = client.patch(
            "/risk/rules",
            json={
                "account_id": str(account_id),
                "rules": {"max_position_size_pct": "0.08"},
                "actor": "user:risk-admin",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_override"] is True
    assert payload["rules"]["max_position_size_pct"] == "0.08"
    assert len(fake_session.audit_logs) == 1
    audit = fake_session.audit_logs[0]
    assert audit.action == "risk.rules.patch"
    assert audit.after_state["max_position_size_pct"] == Decimal("0.08")