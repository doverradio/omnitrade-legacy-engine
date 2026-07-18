from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.models.live_approval_event import LiveApprovalEvent
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeSession:
    def __init__(
        self,
        *,
        profiles: list[LiveTradingProfile] | None = None,
        approval_events: list[LiveApprovalEvent] | None = None,
        reconciliation_events: list[LiveReconciliationEvent] | None = None,
    ) -> None:
        self.profiles = profiles or []
        self.approval_events = approval_events or []
        self.reconciliation_events = reconciliation_events or []
        self.added: list[Any] = []
        self.commit_count = 0

    async def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_trading_profiles" in sql:
            profile_id = params.get("id_1")
            paper_account_id = params.get("paper_account_id_1")
            for item in self.profiles:
                if profile_id is not None and item.id == profile_id:
                    return item
                if paper_account_id is not None and item.paper_account_id == paper_account_id:
                    return item
            return None

        if "FROM live_approval_events" in sql and "idempotency_key_1" in params:
            key = params.get("idempotency_key_1")
            for item in self.approval_events:
                if item.idempotency_key == key:
                    return item
            return None

        if "max(live_approval_events.sequence_number)" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            seqs = [item.sequence_number for item in self.approval_events if item.live_trading_profile_id == profile_id]
            return max(seqs) if seqs else None

        return None

    def add(self, obj: Any) -> None:
        if isinstance(obj, LiveApprovalEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.approval_events.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commit_count += 1

    async def scalars(self, statement: Any) -> _Rows:
        sql = str(statement)
        params = statement.compile().params

        if "FROM live_approval_events" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            rows = [item for item in self.approval_events if item.live_trading_profile_id == profile_id]
            rows.sort(key=lambda item: item.sequence_number, reverse=True)
            return _Rows(rows)

        if "FROM live_reconciliation_events" in sql:
            profile_id = params.get("live_trading_profile_id_1")
            rows = [item for item in self.reconciliation_events if item.live_trading_profile_id == profile_id]
            rows.sort(key=lambda item: item.sequence_number, reverse=True)
            return _Rows(rows)

        return _Rows([])


def _create_client(fake_session: _FakeSession) -> TestClient:
    app = create_app()

    async def override_get_db() -> _FakeSession:
        yield fake_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _profile(*, operating_mode: str = "paper") -> LiveTradingProfile:
    return LiveTradingProfile(
        id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        operating_mode=operating_mode,
        lifecycle_state="pending_approval" if operating_mode == "paper" else "enabled",
        approval_state="pending" if operating_mode == "paper" else "approved",
        live_opt_in=True,
        human_approval_recorded=(operating_mode == "live"),
        paper_default_mode=True,
        governance_approved=(operating_mode == "live"),
        risk_authority_model="risk_engine_final",
        autonomous_capital_allocation=False,
        autonomous_strategy_evolution=False,
        automatic_promotion_enabled=False,
        provenance_metadata={"source": "test"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_registration_status_returns_unknown_when_profile_missing() -> None:
    fake_session = _FakeSession()

    with _create_client(fake_session) as client:
        response = client.get("/live/registration/status", params={"paper_account_id": str(uuid.uuid4())})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_state"] == "unknown"
    assert any(item["code"] == "registration_state_unknown" for item in payload["warnings"])


def test_approvals_status_returns_events_and_warnings() -> None:
    profile = _profile(operating_mode="paper")
    approval = LiveApprovalEvent(
        id=uuid.uuid4(),
        idempotency_key="approval-key",
        event_hash="approval-hash",
        live_trading_profile_id=profile.id,
        sequence_number=1,
        event_type="approval_granted",
        checkpoint_type="first_live_enablement",
        approval_state="approved",
        approver_id="operator",
        approver_role="risk_owner",
        rationale="ok",
        approval_scope={"scope": ["first_live_enablement"]},
        expires_at=None,
        renewal_condition=None,
        event_payload={"scope": "x"},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    fake_session = _FakeSession(profiles=[profile], approval_events=[approval])

    with _create_client(fake_session) as client:
        response = client.get("/live/approvals/status", params={"live_trading_profile_id": str(profile.id)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_state"] == "available"
    assert payload["total_events"] == 1
    assert payload["items"][0]["checkpoint_type"] == "first_live_enablement"
    assert any(item["code"] == "paper_mode_active" for item in payload["warnings"])


def test_reconciliation_status_reports_unresolved_counts() -> None:
    profile = _profile(operating_mode="paper")
    rec_one = LiveReconciliationEvent(
        id=uuid.uuid4(),
        idempotency_key="rec-1",
        event_hash="hash-1",
        live_trading_profile_id=profile.id,
        source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created",
        sequence_number=2,
        event_type="order_reconciled",
        reconciliation_status="open",
        provider_name="paper-sim",
        provider_order_id="order-1",
        provider_fill_id=None,
        event_payload={"status": "open"},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    rec_two = LiveReconciliationEvent(
        id=uuid.uuid4(),
        idempotency_key="rec-2",
        event_hash="hash-2",
        live_trading_profile_id=profile.id,
        source_execution_event_id=uuid.uuid4(),
        source_execution_event_type="execution_intent_created",
        sequence_number=1,
        event_type="fill_reconciled",
        reconciliation_status="filled",
        provider_name="paper-sim",
        provider_order_id="order-2",
        provider_fill_id="fill-1",
        event_payload={"status": "filled"},
        provenance={"source": "test"},
        immutable_contract_version="v1",
        recorded_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

    fake_session = _FakeSession(profiles=[profile], reconciliation_events=[rec_one, rec_two])

    with _create_client(fake_session) as client:
        response = client.get("/live/reconciliation/status", params={"live_trading_profile_id": str(profile.id)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_state"] == "available"
    assert payload["open_count"] == 1
    assert payload["filled_count"] == 1
    assert payload["unresolved_count"] == 1


def test_registration_status_requires_identifier() -> None:
    fake_session = _FakeSession()

    with _create_client(fake_session) as client:
        response = client.get("/live/registration/status")

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"


def test_live_approval_checkpoint_requires_operator_auth(monkeypatch) -> None:
    app = create_app()

    async def _override_get_db():
        yield _FakeSession()

    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as client:
        response = client.post(
            "/live/approvals/checkpoints",
            json={
                "live_trading_profile_id": str(uuid.uuid4()),
                "checkpoint_type": "first_live_enablement",
                "approver_id": "forged:body",
                "approver_role": "risk_owner",
                "rationale": "approved",
                "approval_scope": {"scope": ["x"]},
                "requested_by": "forged:body",
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_live_approval_checkpoint_rejects_non_operator_token() -> None:
    app = create_app()

    async def _override_get_db():
        yield _FakeSession()

    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as client:
        response = client.post(
            "/live/approvals/checkpoints",
            json={
                "live_trading_profile_id": str(uuid.uuid4()),
                "checkpoint_type": "first_live_enablement",
                "approver_id": "forged:body",
                "approver_role": "risk_owner",
                "rationale": "approved",
                "approval_scope": {"scope": ["x"]},
                "requested_by": "forged:body",
            },
            headers={"Authorization": "Bearer service:automation"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_live_approval_checkpoint_uses_authenticated_actor_identity(monkeypatch) -> None:
    app = create_app()
    captured = {}

    async def _override_get_db():
        yield _FakeSession()

    async def _record_stub(*, db, request):
        _ = db
        captured["approver_id"] = request.approver_id
        captured["requested_by"] = request.requested_by
        captured["approver_role"] = request.approver_role
        return type(
            "ApprovalResult",
            (),
            {
                "approval_event_id": uuid.uuid4(),
                "live_trading_profile_id": request.live_trading_profile_id,
                "checkpoint_type": request.checkpoint_type,
                "approval_state": "approved",
                "lifecycle_state": "enabled",
                "operating_mode": "live",
                "expires_at": None,
                "renewal_condition": None,
                "idempotency_key": request.idempotency_key or "approval-key",
            },
        )()

    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.api.routes.live.record_live_approval_checkpoint", _record_stub)

    with TestClient(app) as client:
        response = client.post(
            "/live/approvals/checkpoints",
            json={
                "live_trading_profile_id": str(uuid.uuid4()),
                "checkpoint_type": "first_live_enablement",
                "approver_id": "forged:body",
                "approver_role": "risk_owner",
                "rationale": "approved",
                "approval_scope": {"scope": ["x"]},
                "requested_by": "forged:body",
            },
            headers={"Authorization": "Bearer operator:human"},
        )

    assert response.status_code == 200
    assert captured["approver_id"] == "operator:human"
    assert captured["requested_by"] == "operator:human"
    assert captured["approver_role"] == "risk_owner"


def test_live_approval_checkpoint_route_durably_persists_first_live_enablement() -> None:
    """Exercises the real (unstubbed) record_live_approval_checkpoint() through the HTTP route
    to prove the checkpoint and the profile transition are actually committed, not just returned."""
    profile = _profile(operating_mode="paper")
    session = _FakeSession(profiles=[profile])
    app = create_app()

    async def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app) as client:
        response = client.post(
            "/live/approvals/checkpoints",
            json={
                "live_trading_profile_id": str(profile.id),
                "checkpoint_type": "first_live_enablement",
                "approver_id": "forged:body",
                "approver_role": "risk_owner",
                "rationale": "approved",
                "approval_scope": {"scope": ["x"]},
                "requested_by": "forged:body",
                "idempotency_key": "route-persist-key-1",
            },
            headers={"Authorization": "Bearer operator:human"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["operating_mode"] == "live"
    assert body["lifecycle_state"] == "enabled"
    assert session.commit_count == 1
    assert len(session.approval_events) == 1
    assert profile.operating_mode == "live"
    assert profile.lifecycle_state == "enabled"
    assert profile.approval_state == "approved"
    assert profile.human_approval_recorded is True


def test_live_approval_revoke_uses_authenticated_actor_identity(monkeypatch) -> None:
    app = create_app()
    captured = {}

    async def _override_get_db():
        yield _FakeSession()

    async def _revoke_stub(*, db, request):
        _ = db
        captured["approver_id"] = request.approver_id
        captured["requested_by"] = request.requested_by
        return type(
            "ApprovalResult",
            (),
            {
                "approval_event_id": uuid.uuid4(),
                "live_trading_profile_id": request.live_trading_profile_id,
                "checkpoint_type": request.checkpoint_type,
                "approval_state": "revoked",
                "lifecycle_state": "suspended",
                "operating_mode": "paper",
                "expires_at": None,
                "renewal_condition": None,
                "idempotency_key": request.idempotency_key or "approval-key",
            },
        )()

    from app.db.session import get_db

    app.dependency_overrides[get_db] = _override_get_db
    monkeypatch.setattr("app.api.routes.live.revoke_live_approval", _revoke_stub)

    with TestClient(app) as client:
        response = client.post(
            "/live/approvals/revoke",
            json={
                "live_trading_profile_id": str(uuid.uuid4()),
                "checkpoint_type": "first_live_enablement",
                "approver_id": "forged:body",
                "approver_role": "risk_owner",
                "rationale": "revoke",
                "approval_scope": {"scope": ["x"]},
                "requested_by": "forged:body",
            },
            headers={"Authorization": "Bearer operator:human"},
        )

    assert response.status_code == 200
    assert captured["approver_id"] == "operator:human"
    assert captured["requested_by"] == "operator:human"
