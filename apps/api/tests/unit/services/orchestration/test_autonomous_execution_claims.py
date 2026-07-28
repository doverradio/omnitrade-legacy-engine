from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.services.orchestration import autonomous_execution_claims as subject


def _package(now: datetime):
    return SimpleNamespace(
        package_id=uuid4(), package_state="ACTIVATED", side="BUY", preview_expires_at=now + timedelta(minutes=5),
        superseded_at=None, authorization_source="MANDATE", mandate_id=uuid4(), mandate_version_id=uuid4(),
        mandate_evaluation_id=uuid4(), campaign_id=uuid4(), campaign_version=1, paper_account_id=uuid4(),
        live_trading_profile_id=uuid4(), provider="kraken_spot", environment="production", product="BTC-USD",
        runtime_campaign_id=uuid4(), market_evidence_identity={"exchange_connection_id": str(uuid4())},
    )


def _settings(package):
    return SimpleNamespace(
        automatic_mandate_package_activation_campaign_id=package.campaign_id,
        automatic_mandate_package_activation_campaign_version=package.campaign_version,
        automatic_mandate_package_activation_mandate_id=package.mandate_id,
        automatic_mandate_package_activation_mandate_version_id=package.mandate_version_id,
    )


@pytest.mark.asyncio
async def test_provider_rejection_diagnostics_are_attached_to_claim_and_proof() -> None:
    claim = _claim()
    claim.last_error_code = None
    order = SimpleNamespace(
        live_crypto_order_id=uuid4(),
        status="REJECTED",
        provider="kraken_spot",
        environment="production",
        product_id="BTC-USD",
        side="BUY",
        failure_code="provider_rejected",
        failure_reason="rejected",
        safe_provider_response={
            "create_order_error": {
                "code": "insufficient_funds",
                "message": "EOrder:Insufficient funds",
                "http_status": 200,
                "provider_response_body": {"error": ["EOrder:Insufficient funds"]},
            }
        },
    )
    proof = SimpleNamespace(proof_id=uuid4(), failure_reason=None, updated_at=None)
    db = SimpleNamespace(scalar=AsyncMock(return_value=proof), add=Mock())

    await subject._persist_provider_rejection_diagnostics(db=db, claim=claim, order=order)

    assert claim.last_error_code == "insufficient_funds"
    assert "EOrder:Insufficient funds" in proof.failure_reason
    assert str(order.live_crypto_order_id) in proof.failure_reason
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_stale_activated_package_creates_no_claim() -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    package.preview_expires_at = now
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, None]))
    outcome = await subject.claim_activated_package(db=db, package_id=package.package_id, now=now)
    assert outcome.claim is None
    assert outcome.reason_code == "package_not_eligible"


@pytest.mark.asyncio
async def test_existing_claim_is_idempotently_replayed() -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    claim = SimpleNamespace(claim_id=uuid4(), package_id=package.package_id, claim_status="SAFETY_DISABLED", claim_owner="worker:other")
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[package, claim]))
    outcome = await subject.claim_activated_package(db=db, package_id=package.package_id, now=now)
    assert outcome.claim is claim
    assert not outcome.created
    assert outcome.reason_code == "already_claimed"


@pytest.mark.asyncio
async def test_fresh_matching_package_creates_one_durable_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    package = _package(now)
    activation = SimpleNamespace(
        activation_id=uuid4(), package_id=package.package_id, activation_state="ACTIVE",
        activated_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=4),
        campaign_id=package.campaign_id, campaign_version=1, paper_account_id=package.paper_account_id,
        live_trading_profile_id=package.live_trading_profile_id, provider=package.provider,
        environment=package.environment, product=package.product,
    )
    runtime = SimpleNamespace(id=7, status="RUNNING", definition_version=1)
    mandate = SimpleNamespace(status="ACTIVE", expires_at=now + timedelta(days=1))
    version = SimpleNamespace(is_active=True, is_authorized=True, mandate_id=package.mandate_id)
    claim = SimpleNamespace(claim_id=uuid4(), package_id=package.package_id, claim_status="CLAIMED")
    db = SimpleNamespace(
        # The extra `None` right after the existing-claim check is the new
        # ControlledProofRun linkage lookup (_resolve_autonomous_execution_scope)
        # -- no proof is linked to this package, so it falls through to the
        # unchanged, settings-derived configured-scope path exercised here.
        scalar=AsyncMock(side_effect=[package, None, None, activation, runtime, mandate, version, None, None, None, None, 0, uuid4(), claim]),
        add=Mock(), flush=AsyncMock(),
    )
    monkeypatch.setattr(subject, "get_settings", lambda: _settings(package))
    outcome = await subject.claim_activated_package(db=db, package_id=package.package_id, claim_owner="worker:test", now=now)
    assert outcome.created
    assert outcome.claim is claim
    assert outcome.reason_code == "claimed"


@pytest.mark.asyncio
async def test_submission_disabled_is_recoverable_and_not_ambiguous() -> None:
    now = datetime.now(timezone.utc)
    claim = SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), claim_status="CLAIMED", claim_owner="worker:test",
        last_error_code=None, recover_after=now, updated_at=now, reconciliation_state=None,
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    await subject.mark_submission_safety_disabled(db=db, claim=claim)
    assert claim.claim_status == "SAFETY_DISABLED"
    assert claim.last_error_code == "live_submission_disabled"
    assert claim.reconciliation_state is None
    assert claim.recover_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "released_status",
    ["SAFETY_DISABLED", "FAILED_PRE_PROVIDER", "BUY_RECONCILED", "POSITION_OPENED", "COMPLETED", "CANCELLED", "BLOCKED"],
)
async def test_mark_submission_safety_disabled_never_overwrites_an_already_released_claim(released_status: str) -> None:
    claim = SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), claim_status=released_status, claim_owner="worker:test",
        last_error_code="original", recover_after=None, updated_at=None, reconciliation_state=None,
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    await subject.mark_submission_safety_disabled(db=db, claim=claim)
    assert claim.claim_status == released_status
    assert claim.last_error_code == "original"
    db.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "released_status",
    ["SAFETY_DISABLED", "FAILED_PRE_PROVIDER", "BUY_RECONCILED", "POSITION_OPENED", "COMPLETED", "CANCELLED", "BLOCKED"],
)
async def test_mark_pre_provider_blocked_never_overwrites_an_already_released_claim(released_status: str) -> None:
    claim = SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), claim_status=released_status, claim_owner="worker:test",
        last_error_code="original", recover_after=None, updated_at=None,
    )
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    await subject.mark_pre_provider_blocked(db=db, claim=claim, reason_code="some_new_reason")
    assert claim.claim_status == released_status
    assert claim.last_error_code == "original"
    db.add.assert_not_called()


def test_claim_schema_prevents_duplicate_package_and_activation() -> None:
    ddl = str(CreateTable(AutonomousExecutionClaim.__table__).compile(dialect=postgresql.dialect()))
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in AutonomousExecutionClaim.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("package_id",) in unique_columns
    assert ("activation_id",) in unique_columns
    assert "side IN ('BUY','SELL')" in ddl


# --- advance_claimed_execution: shared prepare-then-execute path, always terminal on failure ---

def _claim(*, claim_status: str = "CLAIMED") -> SimpleNamespace:
    return SimpleNamespace(
        claim_id=uuid4(), package_id=uuid4(), campaign_id=uuid4(), campaign_version=1,
        claim_status=claim_status, claim_owner="worker:test", product="BTC-USD", side="BUY",
        provider="kraken_spot", environment="production",
    )


def _prepared(claim) -> SimpleNamespace:
    order = SimpleNamespace(live_crypto_order_id=uuid4())
    return SimpleNamespace(claim=claim, order=order, replayed=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "released_status",
    ["SAFETY_DISABLED", "FAILED_PRE_PROVIDER", "BUY_RECONCILED", "POSITION_OPENED", "COMPLETED", "CANCELLED", "BLOCKED"],
)
async def test_advance_claimed_execution_is_a_true_no_op_for_any_already_released_claim(
    monkeypatch: pytest.MonkeyPatch, released_status: str,
) -> None:
    """Regression: continuous_pipeline_worker calls advance_claimed_execution
    on every cycle for as long as the package's own package_state stays
    ACTIVATED (nothing ever advances it past that) -- including for a claim
    that has already reached a released status. Before this guard existed,
    that re-drove prepare_autonomous_claimed_order every cycle, which would
    typically fail on the by-then-expired activation window, and
    mark_pre_provider_blocked would overwrite even a genuinely successful
    BUY_RECONCILED claim's status back to FAILED_PRE_PROVIDER -- silently
    corrupting the record of a real, profitable BUY."""
    claim = _claim(claim_status=released_status)

    async def _unexpected_prepare(*, db, claim_id):
        raise AssertionError("prepare_autonomous_claimed_order must not be called for an already-released claim")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _unexpected_prepare)
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert claim.claim_status == released_status
    db.add.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_advance_claimed_execution_reaches_submission_pending_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)

    async def _prepare(*, db, claim_id):
        assert claim_id == claim.claim_id
        return prepared

    async def _execute(*, db, prepared):
        return SimpleNamespace(current_state="SUBMITTED")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert claim.claim_status == "SUBMISSION_PENDING"
    assert claim.reconciliation_state == "SUBMITTED"


@pytest.mark.asyncio
async def test_advance_does_not_overwrite_claim_released_by_authoritative_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    prepared = _prepared(claim)
    prepared.order.status = "REJECTED"

    async def _prepare(**_kwargs):
        return prepared

    async def _execute(**_kwargs):
        # LiveCryptoOrderService's canonical terminal release occurs inside
        # the execution call before the response returns to orchestration.
        claim.claim_status = "CANCELLED"
        return SimpleNamespace(current_state="CANCELLED")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "_persist_provider_rejection_diagnostics", AsyncMock())
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert claim.claim_status == "CANCELLED"
    assert claim.reconciliation_state == "CANCELLED"


@pytest.mark.asyncio
async def test_advance_claimed_execution_reaches_reconciliation_required_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)

    async def _prepare(*, db, claim_id):
        return prepared

    async def _execute(*, db, prepared):
        return SimpleNamespace(current_state="RECONCILIATION_REQUIRED")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert claim.claim_status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_advance_claimed_execution_invalid_request_error_terminalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import InvalidRequestError

    claim = _claim()
    blocked = []

    async def _prepare(*, db, claim_id):
        raise InvalidRequestError(message="failed closed", details={"blocker": "activation_not_effective"})

    async def _mark_blocked(*, db, claim, reason_code):
        blocked.append((claim, reason_code))

    async def _execute(*, db, prepared):
        raise AssertionError("must not execute when preparation failed")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert blocked == [(claim, "activation_not_effective")]


@pytest.mark.asyncio
async def test_advance_claimed_execution_unexpected_exception_terminalizes_instead_of_leaving_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 5: an unforeseen exception (not InvalidRequestError) from
    preparation must still reach a terminal state -- this is exactly the gap
    that left claim 854e9b17-f608-400a-b6fe-58647b730cf0 with no lifecycle
    event beyond 'created'."""
    claim = _claim()
    blocked = []

    async def _prepare(*, db, claim_id):
        raise RuntimeError("unexpected bug deep in preparation")

    async def _mark_blocked(*, db, claim, reason_code):
        blocked.append((claim, reason_code))

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert blocked == [(claim, "unexpected_preparation_failure")]
    assert claim.claim_status == "CLAIMED"  # advance_claimed_execution itself never mutates; mark_pre_provider_blocked (mocked here) does


@pytest.mark.asyncio
async def test_advance_claimed_execution_marks_safety_disabled_when_submission_off(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)
    safety_calls = []

    async def _prepare(*, db, claim_id):
        return prepared

    async def _mark_safety_disabled(*, db, claim):
        safety_calls.append(claim)

    async def _execute(*, db, prepared):
        raise AssertionError("must not execute when live submission is disabled")

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "mark_submission_safety_disabled", _mark_safety_disabled)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=False))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert safety_calls == [claim]


@pytest.mark.asyncio
async def test_advance_claimed_execution_execute_failure_terminalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    claim = _claim()
    prepared = _prepared(claim)
    blocked = []

    async def _prepare(*, db, claim_id):
        return prepared

    async def _execute(*, db, prepared):
        raise RuntimeError("provider evidence unavailable")

    async def _mark_blocked(*, db, claim, reason_code, safe_failure_evidence=None):
        blocked.append((claim, reason_code, safe_failure_evidence))

    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    await subject.advance_claimed_execution(db=db, claim=claim)

    assert blocked == [(
        claim,
        "commissioned_execution_request_evidence_unavailable",
        {
            "exception_type": "RuntimeError",
            "exception_message": "provider evidence unavailable",
            "safe_reason_code": "RuntimeError",
            "failing_stage": "commissioned_execution",
            "provider_call_made": False,
        },
    )]


@pytest.mark.asyncio
async def test_execute_failure_logs_redacted_structured_traceback_and_exact_identities(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    claim = _claim()
    prepared = _prepared(claim)
    proof_id = uuid4()
    secret = "kraken-test-secret-value"
    blocked = []

    async def _prepare(*, db, claim_id):
        return prepared

    async def _execute(*, db, prepared):
        exc = PermissionError(f"credential validation failed for {secret}")
        setattr(exc, "omnitrade_failing_stage", "live_crypto_order_submit")
        raise exc

    async def _proof_id(*, db, package_id):
        assert package_id == claim.package_id
        return proof_id

    async def _mark_blocked(*, db, claim, reason_code, safe_failure_evidence=None):
        blocked.append((reason_code, safe_failure_evidence))

    monkeypatch.setenv("OT_KRAKEN_API_SECRET", secret)
    monkeypatch.setattr(subject, "prepare_autonomous_claimed_order", _prepare)
    monkeypatch.setattr(subject, "execute_prepared_autonomous_claim", _execute)
    monkeypatch.setattr(subject, "_controlled_proof_id_for_failure_diagnostics", _proof_id)
    monkeypatch.setattr(subject, "mark_pre_provider_blocked", _mark_blocked)
    monkeypatch.setattr(subject, "get_settings", lambda: SimpleNamespace(live_crypto_order_submission_enabled=True, database_url=""))
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())

    with caplog.at_level("ERROR", logger=subject.__name__):
        await subject.advance_claimed_execution(db=db, claim=claim)

    message = caplog.text
    assert "event=commissioned_execution_pre_provider_exception" in message
    assert f"claim_id={claim.claim_id}" in message
    assert f"package_id={claim.package_id}" in message
    assert f"controlled_proof_id={proof_id}" in message
    assert f"live_order_id={prepared.order.live_crypto_order_id}" in message
    assert f"campaign_id={claim.campaign_id}" in message
    assert "campaign_version=1" in message
    assert "product=BTC-USD side=BUY provider=kraken_spot environment=production" in message
    assert "exception_type=PermissionError" in message
    assert "failing_stage=live_crypto_order_submit provider_call_made=false" in message
    assert "Traceback (most recent call last)" in message
    assert "[REDACTED]" in message
    assert secret not in message
    assert blocked == [(
        "commissioned_execution_request_evidence_unavailable",
        {
            "exception_type": "PermissionError",
            "exception_message": "credential validation failed for [REDACTED]",
            "safe_reason_code": "PermissionError",
            "failing_stage": "live_crypto_order_submit",
            "provider_call_made": False,
        },
    )]


@pytest.mark.asyncio
async def test_mark_pre_provider_blocked_persists_safe_specific_failure_evidence() -> None:
    claim = _claim()
    db = SimpleNamespace(add=Mock(), flush=AsyncMock())
    evidence = {
        "exception_type": "PermissionError",
        "exception_message": "confirmation phrase mismatch",
        "safe_reason_code": "PermissionError",
        "failing_stage": "live_crypto_order_submit",
        "provider_call_made": False,
    }

    await subject.mark_pre_provider_blocked(
        db=db,
        claim=claim,
        reason_code="commissioned_execution_request_evidence_unavailable",
        safe_failure_evidence=evidence,
    )

    assert claim.claim_status == "FAILED_PRE_PROVIDER"
    assert claim.last_error_code == "commissioned_execution_request_evidence_unavailable"
    audit = db.add.call_args.args[0]
    assert audit.after_state == {
        "claim_status": "FAILED_PRE_PROVIDER",
        "reason_code": "commissioned_execution_request_evidence_unavailable",
        "provider_call_made": False,
        "safe_failure_evidence": evidence,
    }


@pytest.mark.asyncio
async def test_mark_pre_provider_blocked_terminalizes_provider_never_called_order() -> None:
    claim = _claim()
    claim.live_order_id = uuid4()
    order = SimpleNamespace(
        live_crypto_order_id=claim.live_order_id,
        status="PENDING_CONFIRMATION",
        provider_order_id=None,
        submitted_at=None,
        cancelled_at=None,
        failure_code=None,
        failure_reason=None,
        safe_provider_response={"provider_call_made": False},
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=order), add=Mock(), flush=AsyncMock())

    await subject.mark_pre_provider_blocked(
        db=db, claim=claim, reason_code="readiness_evidence_stale",
    )

    assert claim.claim_status == "FAILED_PRE_PROVIDER"
    assert order.status == "CANCELLED"
    assert order.provider_order_id is None
    assert order.submitted_at is None
    assert order.failure_code == "failed_pre_provider"
    assert order.safe_provider_response["provider_call_made"] is False
    audit = db.add.call_args.args[0]
    assert audit.after_state["live_order_transition"] == {
        "live_order_id": str(order.live_crypto_order_id),
        "before_status": "PENDING_CONFIRMATION",
        "after_status": "CANCELLED",
    }


@pytest.mark.asyncio
async def test_historical_failed_pre_provider_order_is_safely_recovered() -> None:
    package = SimpleNamespace(provider="kraken_spot", environment="production", product="BTC-USD")
    order = SimpleNamespace(
        live_crypto_order_id=uuid4(), provider="kraken_spot", environment="production",
        product_id="BTC-USD", status="PENDING_CONFIRMATION", provider_order_id=None,
        submitted_at=None, cancelled_at=None, failure_code=None, failure_reason=None,
        safe_provider_response={"provider_call_made": False},
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=order), add=Mock(), flush=AsyncMock())

    recovered_id = await subject._recover_failed_pre_provider_order_for_scope(db=db, package=package)

    assert recovered_id == order.live_crypto_order_id
    assert order.status == "CANCELLED"
    assert order.failure_code == "failed_pre_provider"
    assert order.provider_order_id is None
    assert order.submitted_at is None
    assert order.safe_provider_response["provider_call_made"] is False
    audit = db.add.call_args.args[0]
    assert audit.action == "live_crypto_order.failed_pre_provider_recovered"
    assert audit.after_state["provider_call_made"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_order_id", "submitted_at", "provider_call_made"),
    [
        ("provider-123", None, False),
        (None, datetime.now(timezone.utc), False),
        (None, None, True),
        (None, None, None),
    ],
)
async def test_historical_recovery_refuses_any_uncertain_provider_boundary(
    provider_order_id, submitted_at, provider_call_made,
) -> None:
    package = SimpleNamespace(provider="kraken_spot", environment="production", product="BTC-USD")
    evidence = {} if provider_call_made is None else {"provider_call_made": provider_call_made}
    order = SimpleNamespace(
        live_crypto_order_id=uuid4(), provider="kraken_spot", environment="production",
        product_id="BTC-USD", status="PENDING_CONFIRMATION", provider_order_id=provider_order_id,
        submitted_at=submitted_at, cancelled_at=None, failure_code=None, failure_reason=None,
        safe_provider_response=evidence,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=order), add=Mock(), flush=AsyncMock())

    recovered_id = await subject._recover_failed_pre_provider_order_for_scope(db=db, package=package)

    assert recovered_id is None
    assert order.status == "PENDING_CONFIRMATION"
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_visible_order_is_never_terminalized_as_pre_provider() -> None:
    claim = _claim()
    claim.live_order_id = uuid4()
    order = SimpleNamespace(
        live_crypto_order_id=claim.live_order_id,
        status="SUBMISSION_PENDING",
        provider_order_id="provider-123",
        submitted_at=datetime.now(timezone.utc),
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=order), add=Mock(), flush=AsyncMock())

    await subject.mark_pre_provider_blocked(db=db, claim=claim, reason_code="unexpected")

    assert order.status == "SUBMISSION_PENDING"
    assert order.provider_order_id == "provider-123"


# --- sweep_stale_autonomous_execution_claims: the never-implemented recovery pass ----

@pytest.mark.asyncio
async def test_sweep_advances_each_stale_claim_found(monkeypatch: pytest.MonkeyPatch) -> None:
    claim_a, claim_b = _claim(), _claim()
    advanced = []

    async def _advance(*, db, claim):
        advanced.append(claim)

    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [claim_a, claim_b])))
    monkeypatch.setattr(subject, "advance_claimed_execution", _advance)

    swept = await subject.sweep_stale_autonomous_execution_claims(db=db)

    assert swept == 2
    assert advanced == [claim_a, claim_b]


@pytest.mark.asyncio
async def test_sweep_returns_zero_when_nothing_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    monkeypatch.setattr(subject, "advance_claimed_execution", AsyncMock())

    swept = await subject.sweep_stale_autonomous_execution_claims(db=db)

    assert swept == 0


@pytest.mark.asyncio
async def test_sweep_isolates_one_claims_failure_from_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    claim_a, claim_b = _claim(), _claim()
    advanced = []

    async def _advance(*, db, claim):
        if claim is claim_a:
            raise RuntimeError("advance_claimed_execution itself misbehaved")
        advanced.append(claim)

    db = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [claim_a, claim_b])))
    monkeypatch.setattr(subject, "advance_claimed_execution", _advance)

    swept = await subject.sweep_stale_autonomous_execution_claims(db=db)

    assert swept == 2
    assert advanced == [claim_b]


@pytest.mark.asyncio
async def test_sweep_query_filters_by_claimed_or_execution_started_status_and_due_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the sweep's real WHERE clause targets claim_status IN
    ('CLAIMED', 'EXECUTION_STARTED') AND recover_after IS NOT NULL AND
    recover_after <= now -- against a real SQLite-backed session, not a
    mock, and against the actual sweep_stale_autonomous_execution_claims
    function (not a hand-duplicated query), so a future change to the
    production WHERE clause cannot silently drift from this assertion.
    EXECUTION_STARTED must be swept too -- a crash between prepare_
    autonomous_claimed_buy's own EXECUTION_STARTED transition and the
    submission call that follows it would otherwise orphan the claim (and
    its campaign scope) forever. SUBMISSION_PENDING and RECONCILIATION_
    REQUIRED must never be swept -- a provider call has already been made
    (or may have been); blind re-preparation there would be unsafe."""
    from datetime import timedelta

    from app.models.audit_log import AuditLog
    from tests.support.real_sqlite_session import real_sqlite_session

    now = datetime.now(timezone.utc)

    async with real_sqlite_session([AutonomousExecutionClaim.__table__, AuditLog.__table__]) as session:
        def _row(**overrides):
            defaults = dict(
                claim_id=uuid4(), package_id=uuid4(), activation_id=uuid4(), campaign_id=uuid4(),
                campaign_version=1, mandate_id=uuid4(), mandate_version_id=uuid4(), account_id=uuid4(),
                profile_id=uuid4(), connection_id=uuid4(), provider="kraken_spot", environment="production",
                product="BTC-USD", side="BUY", claim_status="CLAIMED", claimed_at=now - timedelta(minutes=10),
                claim_owner="worker:test", recover_after=now - timedelta(minutes=8), attempt_count=1,
            )
            defaults.update(overrides)
            return AutonomousExecutionClaim(**defaults)

        due_claimed = _row()
        due_execution_started = _row(claim_status="EXECUTION_STARTED")
        not_yet_due = _row(recover_after=now + timedelta(minutes=5))
        never_swept = _row(recover_after=None)
        already_terminal = _row(claim_status="COMPLETED", recover_after=now - timedelta(minutes=8))
        due_submission_pending = _row(claim_status="SUBMISSION_PENDING")
        due_reconciliation_required = _row(claim_status="RECONCILIATION_REQUIRED")
        session.add_all([
            due_claimed, due_execution_started, not_yet_due, never_swept,
            already_terminal, due_submission_pending, due_reconciliation_required,
        ])
        await session.flush()

        advanced: list = []

        async def _fake_advance(*, db, claim):
            advanced.append(claim.claim_id)

        monkeypatch.setattr(subject, "advance_claimed_execution", _fake_advance)

        swept = await subject.sweep_stale_autonomous_execution_claims(db=session, now=now)

        assert swept == 2
        assert set(advanced) == {due_claimed.claim_id, due_execution_started.claim_id}
