from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

from fastapi.testclient import TestClient
import pytest

from app.core.errors import ConflictError, InvalidRequestError, NotFoundError
from app.db.session import get_db
from app.main import create_app
from app.services.strategies.identity import build_strategy_identity


class _FakeSession:
    pass


def _client(*, raise_server_exceptions: bool = True) -> TestClient:
    app = create_app()

    async def _override_get_db() -> _FakeSession:
        yield _FakeSession()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _mandate_obj(*, mandate_id: uuid.UUID | None = None, status: str = "DRAFT") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        mandate_id=mandate_id or uuid.uuid4(),
        owner_actor_id="operator:owner",
        status=status,
        autonomy_level="LEVEL_2",
        provider="kraken_spot",
        exchange_environment="production",
        exchange_connection_id=uuid.uuid4(),
        live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(),
        capital_campaign_id=101,
        authorized_at=None,
        activated_at=None,
        paused_at=None,
        expires_at=None,
        revoked_at=None,
        created_at=now,
        updated_at=now,
    )


def _version_obj(*, mandate_id: uuid.UUID, version_id: uuid.UUID | None = None, number: int = 1, approval_policy: str = "HUMAN_REQUIRED") -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        mandate_version_id=version_id or uuid.uuid4(),
        mandate_id=mandate_id,
        version_number=number,
        version_hash=f"hash-{number}",
        base_currency="USD",
        authorized_capital_usd=Decimal("25"),
        max_order_notional_usd=Decimal("5"),
        max_open_exposure_usd=Decimal("10"),
        max_daily_deployed_usd=Decimal("10"),
        max_daily_realized_loss_usd=Decimal("3"),
        max_campaign_drawdown_usd=Decimal("5"),
        max_consecutive_losses=2,
        position_limit=1,
        price_evidence_max_age_seconds=30,
        max_slippage_bps=Decimal("25"),
        max_fee_bps=Decimal("10"),
        allowed_products=["BTC-USD"],
        allowed_order_sides=["BUY", "SELL", "HOLD"],
        allowed_strategy_versions=[build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
        approval_policy=approval_policy,
        is_authorized=False,
        is_active=False,
        created_at=now,
        authorized_at=None,
    )


def _auth_header() -> dict[str, str]:
    return {"Authorization": "Bearer operator:owner"}


def _create_payload() -> dict[str, object]:
    return {
        "owner_actor_id": "operator:owner",
        "autonomy_level": "LEVEL_2",
        "provider": "kraken_spot",
        "exchange_environment": "production",
        "exchange_connection_id": str(uuid.uuid4()),
        "live_trading_profile_id": str(uuid.uuid4()),
        "paper_account_id": str(uuid.uuid4()),
        "capital_campaign_id": 101,
        "idempotency_key": "m-create-1",
        "reason": "create_draft",
    }


def _version_payload(*, approval_policy: str = "MANDATE_ALLOWED", idempotency_key: str = "v-create-1") -> dict[str, object]:
    return {
        "base_currency": "USD",
        "authorized_capital_usd": "25",
        "max_order_notional_usd": "5",
        "max_open_exposure_usd": "10",
        "max_daily_deployed_usd": "10",
        "max_daily_realized_loss_usd": "3",
        "max_campaign_drawdown_usd": "5",
        "max_consecutive_losses": 2,
        "position_limit": 1,
        "price_evidence_max_age_seconds": 30,
        "max_slippage_bps": "25",
        "max_fee_bps": "10",
        "allowed_products": ["BTC-USD"],
        "allowed_order_sides": ["BUY", "SELL", "HOLD"],
        "allowed_strategy_versions": [build_strategy_identity(slug="ma_crossover", module_version="1.0.0")],
        "entry_policy": {},
        "exit_policy": {},
        "cooldown_policy": {},
        "operating_schedule": {},
        "approval_policy": approval_policy,
        "reconciliation_policy": {},
        "kill_switch_policy": {},
        "owner_acknowledgements": {"accepted": True},
        "authorization_evidence_summary": {"method": "owner_signature"},
        "idempotency_key": idempotency_key,
    }


def test_create_list_get_mandate_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _mandate_obj(status="DRAFT")

    async def _create_stub(**_kwargs):
        return created

    async def _list_stub(**_kwargs):
        return [created]

    async def _get_stub(**_kwargs):
        return created

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.create_mandate", _create_stub)
    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.list_mandates", _list_stub)
    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.get_mandate", _get_stub)

    with _client() as client:
        created_resp = client.post("/autonomous-capital/mandates", json=_create_payload(), headers=_auth_header())
        assert created_resp.status_code == 201
        assert created_resp.json()["status"] == "DRAFT"

        listed = client.get("/autonomous-capital/mandates")
        assert listed.status_code == 200
        assert len(listed.json()["items"]) == 1

        detail = client.get(f"/autonomous-capital/mandates/{created.mandate_id}")
        assert detail.status_code == 200
        assert detail.json()["mandate_id"] == str(created.mandate_id)


def test_mandate_get_unknown_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _missing(**_kwargs):
        raise NotFoundError(message="Mandate not found", details={})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.get_mandate", _missing)

    with _client() as client:
        response = client.get(f"/autonomous-capital/mandates/{uuid.uuid4()}")

    assert response.status_code == 404


def test_mandate_create_validation_and_auth_failures() -> None:
    with _client() as client:
        unauthorized = client.post("/autonomous-capital/mandates", json=_create_payload())
        assert unauthorized.status_code == 401

        invalid_payload = dict(_create_payload())
        del invalid_payload["provider"]
        invalid = client.post("/autonomous-capital/mandates", json=invalid_payload, headers=_auth_header())
        assert invalid.status_code == 422


def test_mandate_response_is_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    created = _mandate_obj()

    async def _create_stub(**_kwargs):
        return created

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.create_mandate", _create_stub)

    with _client() as client:
        response = client.post("/autonomous-capital/mandates", json=_create_payload(), headers=_auth_header())

    payload = response.json()
    assert response.status_code == 201
    assert "authorization_evidence" not in payload
    assert "api_key" not in str(payload).lower()


def test_version_create_and_idempotent_duplicate_request(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate_id = uuid.uuid4()
    stable_version_id = uuid.uuid4()

    async def _create_version_stub(*, db, request):
        _ = db
        if request.idempotency_key == "idem-v1":
            return _version_obj(mandate_id=mandate_id, version_id=stable_version_id, number=1, approval_policy=request.approval_policy)
        return _version_obj(mandate_id=mandate_id, number=2, approval_policy=request.approval_policy)

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.create_mandate_version", _create_version_stub)

    payload = _version_payload(idempotency_key="idem-v1")
    with _client() as client:
        first = client.post(f"/autonomous-capital/mandates/{mandate_id}/versions", json=payload, headers=_auth_header())
        second = client.post(f"/autonomous-capital/mandates/{mandate_id}/versions", json=payload, headers=_auth_header())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["mandate_version_id"] == second.json()["mandate_version_id"] == str(stable_version_id)


def test_versions_list_ordering_and_no_inherited_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate_id = uuid.uuid4()
    v2 = _version_obj(mandate_id=mandate_id, number=2, approval_policy="MANDATE_ALLOWED")
    v1 = _version_obj(mandate_id=mandate_id, number=1, approval_policy="MANDATE_ALLOWED")
    v1.is_authorized = True
    v2.is_authorized = False

    async def _list_versions_stub(**_kwargs):
        return [v2, v1]

    async def _list_auth_stub(**_kwargs):
        return [
            SimpleNamespace(
                mandate_authorization_id=uuid.uuid4(),
                mandate_id=mandate_id,
                mandate_version_id=v1.mandate_version_id,
                mandate_version_number=1,
                autonomy_level="LEVEL_2",
                authorization_state="AUTHORIZED",
                approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
                authorized_by_actor_id="operator:owner",
                audit_correlation_id=uuid.uuid4(),
                recorded_at=datetime.now(timezone.utc),
                expires_at=None,
                revoked_at=None,
            )
        ]

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.list_mandate_versions", _list_versions_stub)
    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.list_mandate_authorizations", _list_auth_stub)

    with _client() as client:
        versions = client.get(f"/autonomous-capital/mandates/{mandate_id}/versions")
        auths = client.get(f"/autonomous-capital/mandates/{mandate_id}/authorizations")

    assert versions.status_code == 200
    assert versions.json()["items"][0]["version_number"] == 2
    assert versions.json()["items"][0]["is_authorized"] is False
    assert auths.status_code == 200
    assert auths.json()["items"][0]["mandate_version_number"] == 1


def test_mandate_allowed_version_may_exist_while_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate_id = uuid.uuid4()

    async def _create_version_stub(**_kwargs):
        return _version_obj(mandate_id=mandate_id, approval_policy="MANDATE_ALLOWED")

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.create_mandate_version", _create_version_stub)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{mandate_id}/versions",
            json=_version_payload(approval_policy="MANDATE_ALLOWED"),
            headers=_auth_header(),
        )

    assert response.status_code == 201
    assert response.json()["approval_policy"] == "MANDATE_ALLOWED"
    assert response.json()["is_authorized"] is False


def test_authorize_exact_version_and_persist_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate_id = uuid.uuid4()
    version_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def _authorize_stub(*, db, request):
        _ = db
        captured["version_id"] = request.mandate_version_id
        captured["evidence"] = request.authorization_evidence
        return SimpleNamespace(
            mandate_authorization_id=uuid.uuid4(),
            mandate_id=request.mandate_id,
            mandate_version_id=request.mandate_version_id,
            mandate_version_number=3,
            autonomy_level="LEVEL_2",
            authorization_state="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            authorized_by_actor_id=request.actor,
            audit_correlation_id=request.audit_correlation_id,
            recorded_at=datetime.now(timezone.utc),
            expires_at=None,
            revoked_at=None,
        )

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.authorize_mandate_version", _authorize_stub)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{mandate_id}/authorizations",
            json={
                "mandate_version_id": str(version_id),
                "authorization_method": "owner_signature",
                "owner_acknowledgements": {"accepted": True},
                "authorization_evidence": {"signature": "hash"},
                "deterministic_explanation": {"reason": "explicit_owner_authorization"},
                "idempotency_key": "auth-1",
            },
            headers=_auth_header(),
        )

    assert response.status_code == 201
    assert captured["version_id"] == version_id
    assert captured["evidence"] == {"signature": "hash"}


def test_authorization_owner_mismatch_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _authorize_reject(**_kwargs):
        raise InvalidRequestError(message="owner mismatch", details={})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.authorize_mandate_version", _authorize_reject)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/authorizations",
            json={
                "mandate_version_id": str(uuid.uuid4()),
                "authorization_method": "owner_signature",
                "owner_acknowledgements": {},
                "authorization_evidence": {},
                "deterministic_explanation": {},
            },
            headers=_auth_header(),
        )

    assert response.status_code == 400


def test_duplicate_authorization_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate_id = uuid.uuid4()
    auth_id = uuid.uuid4()

    async def _authorize_stub(*, db, request):
        _ = db
        _ = request
        return SimpleNamespace(
            mandate_authorization_id=auth_id,
            mandate_id=mandate_id,
            mandate_version_id=uuid.uuid4(),
            mandate_version_number=1,
            autonomy_level="LEVEL_2",
            authorization_state="AUTHORIZED",
            approval_result="APPROVAL_REQUIRED_HUMAN",
            authorized_by_actor_id="operator:owner",
            audit_correlation_id=None,
            recorded_at=datetime.now(timezone.utc),
            expires_at=None,
            revoked_at=None,
        )

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.authorize_mandate_version", _authorize_stub)

    request_body = {
        "mandate_version_id": str(uuid.uuid4()),
        "authorization_method": "owner_signature",
        "owner_acknowledgements": {},
        "authorization_evidence": {},
        "deterministic_explanation": {},
        "idempotency_key": "auth-idem-1",
    }
    with _client() as client:
        first = client.post(f"/autonomous-capital/mandates/{mandate_id}/authorizations", json=request_body, headers=_auth_header())
        second = client.post(f"/autonomous-capital/mandates/{mandate_id}/authorizations", json=request_body, headers=_auth_header())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["mandate_authorization_id"] == second.json()["mandate_authorization_id"] == str(auth_id)


@pytest.mark.parametrize(
    ("action", "status"),
    [
        ("ACTIVATE", "ACTIVE"),
        ("PAUSE", "PAUSED"),
        ("RESUME", "ACTIVE"),
        ("SET_EXIT_ONLY", "EXIT_ONLY"),
        ("REVOKE", "REVOKED"),
        ("KILL", "KILLED"),
        ("EXPIRE", "EXPIRED"),
        ("COMPLETE", "COMPLETED"),
    ],
)
def test_state_transition_actions(monkeypatch: pytest.MonkeyPatch, action: str, status: str) -> None:
    mandate_id = uuid.uuid4()

    async def _transition_stub(*, db, request):
        _ = db
        return _mandate_obj(mandate_id=request.mandate_id, status=status)

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.apply_mandate_lifecycle_action", _transition_stub)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{mandate_id}/lifecycle-actions",
            json={"action": action, "reason": "operator_transition", "idempotency_key": f"{action}-1"},
            headers=_auth_header(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == status


def test_invalid_transition_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _invalid_transition(**_kwargs):
        raise ConflictError(message="Invalid mandate lifecycle transition", details={})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.apply_mandate_lifecycle_action", _invalid_transition)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/lifecycle-actions",
            json={"action": "ACTIVATE", "reason": "attempt"},
            headers=_auth_header(),
        )

    assert response.status_code == 409


def test_unauthorized_activation_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _reject(**_kwargs):
        raise InvalidRequestError(message="Lifecycle action requires at least one authorized mandate version", details={})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.apply_mandate_lifecycle_action", _reject)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/lifecycle-actions",
            json={"action": "ACTIVATE", "reason": "attempt"},
            headers=_auth_header(),
        )

    assert response.status_code == 400


def test_terminal_state_cannot_reactivate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _invalid_transition(**_kwargs):
        raise ConflictError(message="Invalid mandate lifecycle transition", details={"from": "COMPLETED", "to": "ACTIVE"})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.apply_mandate_lifecycle_action", _invalid_transition)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/lifecycle-actions",
            json={"action": "RESUME", "reason": "reactivate_attempt"},
            headers=_auth_header(),
        )

    assert response.status_code == 409


@pytest.mark.parametrize("side", ["BUY", "SELL", "HOLD"])
def test_evaluation_evidence_persisted_for_all_actions(monkeypatch: pytest.MonkeyPatch, side: str) -> None:
    mandate_id = uuid.uuid4()

    async def _evaluate_stub(*, db, request):
        _ = db
        return SimpleNamespace(
            evaluation_id=uuid.uuid4(),
            mandate_id=request.mandate_id,
            mandate_version_id=uuid.uuid4(),
            mandate_version_number=4,
            autonomy_level="LEVEL_2",
            proposed_action=request.side,
            authorization_result="AUTHORIZED" if request.side != "HOLD" else "REJECTED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE" if request.side == "BUY" else "APPROVAL_REQUIRED_HUMAN",
            risk_verdict=request.risk_verdict,
            risk_evaluated=request.risk_verdict != "NOT_EVALUATED",
            checks_passed=("owner_match", "provider_match"),
            checks_failed=("mandate_status",) if request.side == "HOLD" else (),
            deterministic_explanation=("CHECK_PASSED:owner_match",),
            reason_code="authorized_under_active_mandate" if request.side == "BUY" else "mandate_not_active",
            human_approval_required=request.side != "BUY",
            active_mandate_exemption_eligible=request.side == "BUY",
            decision_id=request.decision_id,
            actor=request.actor,
            audit_correlation_id=request.audit_correlation_id or uuid.uuid4(),
            software_build_version=request.software_build_version,
            idempotency_key=request.idempotency_key or "eval-1",
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.evaluate_and_record_mandate", _evaluate_stub)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{mandate_id}/evaluations",
            json={
                "strategy_version": build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
                "product": "BTC-USD",
                "side": side,
                "proposed_notional_usd": "5",
                "current_open_exposure_usd": "0",
                "daily_deployed_usd": "0",
                "daily_realized_loss_usd": "0",
                "campaign_drawdown_usd": "0",
                "consecutive_losses": 0,
                "current_position_count": 0,
                "risk_verdict": "NOT_EVALUATED" if side == "HOLD" else "ACCEPTED",
                "evidence_age_seconds": 5,
                "kill_switch_engaged": False,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "decision_id": str(uuid.uuid4()),
                "idempotency_key": f"eval-{side.lower()}",
            },
            headers=_auth_header(),
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["proposed_action"] == side
    assert "checks_passed" in payload
    assert "checks_failed" in payload


def test_evaluation_links_decision_and_supports_replay_idempotency(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate_id = uuid.uuid4()
    evaluation_id = uuid.uuid4()
    decision_id = uuid.uuid4()

    async def _evaluate_stub(*, db, request):
        _ = db
        _ = request
        return SimpleNamespace(
            evaluation_id=evaluation_id,
            mandate_id=mandate_id,
            mandate_version_id=uuid.uuid4(),
            mandate_version_number=1,
            autonomy_level="LEVEL_2",
            proposed_action="BUY",
            authorization_result="AUTHORIZED",
            approval_result="APPROVAL_SATISFIED_BY_ACTIVE_MANDATE",
            risk_verdict="ACCEPTED",
            risk_evaluated=True,
            checks_passed=("owner_match",),
            checks_failed=(),
            deterministic_explanation=("CHECK_PASSED:owner_match",),
            reason_code="authorized_under_active_mandate",
            human_approval_required=False,
            active_mandate_exemption_eligible=True,
            decision_id=decision_id,
            actor="operator:owner",
            audit_correlation_id=uuid.uuid4(),
            software_build_version=None,
            idempotency_key="eval-idem-1",
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.evaluate_and_record_mandate", _evaluate_stub)

    request_json = {
        "strategy_version": build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
        "product": "BTC-USD",
        "side": "BUY",
        "proposed_notional_usd": "5",
        "current_open_exposure_usd": "0",
        "daily_deployed_usd": "0",
        "daily_realized_loss_usd": "0",
        "campaign_drawdown_usd": "0",
        "consecutive_losses": 0,
        "current_position_count": 0,
        "risk_verdict": "ACCEPTED",
        "evidence_age_seconds": 5,
        "kill_switch_engaged": False,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "decision_id": str(decision_id),
        "idempotency_key": "eval-idem-1",
    }

    with _client() as client:
        first = client.post(f"/autonomous-capital/mandates/{mandate_id}/evaluations", json=request_json, headers=_auth_header())
        second = client.post(f"/autonomous-capital/mandates/{mandate_id}/evaluations", json=request_json, headers=_auth_header())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["evaluation_id"] == second.json()["evaluation_id"] == str(evaluation_id)
    assert first.json()["decision_id"] == str(decision_id)


def test_evaluation_rejects_invented_risk_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _reject_invalid_risk(**_kwargs):
        raise InvalidRequestError(message="Unsupported risk verdict", details={})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.evaluate_and_record_mandate", _reject_invalid_risk)

    with _client() as client:
        response = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/evaluations",
            json={
                "strategy_version": build_strategy_identity(slug="ma_crossover", module_version="1.0.0"),
                "product": "BTC-USD",
                "side": "BUY",
                "proposed_notional_usd": "5",
                "current_open_exposure_usd": "0",
                "daily_deployed_usd": "0",
                "daily_realized_loss_usd": "0",
                "campaign_drawdown_usd": "0",
                "consecutive_losses": 0,
                "current_position_count": 0,
                "risk_verdict": "ALLOW",
                "evidence_age_seconds": 5,
                "kill_switch_engaged": False,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
            headers=_auth_header(),
        )

    assert response.status_code == 400


def test_invalid_uuid_and_error_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _conflict_stub(**_kwargs):
        raise ConflictError(message="conflict", details={})

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.apply_mandate_lifecycle_action", _conflict_stub)

    with _client() as client:
        bad_uuid = client.get("/autonomous-capital/mandates/not-a-uuid")
        assert bad_uuid.status_code == 400

        conflict = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/lifecycle-actions",
            json={"action": "ACTIVATE", "reason": "x"},
            headers=_auth_header(),
        )
        assert conflict.status_code == 409

        malformed = client.post(
            f"/autonomous-capital/mandates/{uuid.uuid4()}/lifecycle-actions",
            json={"action": "ACTIVATE"},
            headers=_auth_header(),
        )
        assert malformed.status_code == 422


def test_mandate_routes_do_not_invoke_execution_or_provider_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    mandate = _mandate_obj()

    async def _list_stub(**_kwargs):
        return [mandate]

    def _fail(*_args, **_kwargs):
        raise AssertionError("execution/provider path must not be called by mandate routes")

    monkeypatch.setattr("app.api.routes.autonomous_capital_mandates.list_mandates", _list_stub)
    monkeypatch.setattr("app.services.live_crypto_orders.service.submit", _fail)
    monkeypatch.setattr("app.services.exchange_connections.providers.registry.get_exchange_provider", _fail)

    with _client() as client:
        response = client.get("/autonomous-capital/mandates")

    assert response.status_code == 200
