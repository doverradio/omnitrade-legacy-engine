from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.models.audit_log import AuditLog
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.services.controlled_proof import exit_recovery


class _FakeDb:
    def __init__(self, scalars):
        self.values = list(scalars)
        self.added = []
        self.commits = 0

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None

    def add(self, value):
        if isinstance(value, ControlledProofExitRecovery) and value.recovery_id is None:
            value.recovery_id = uuid.uuid4()
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def refresh(self, _value):
        return None


def _terminal_proof():
    return SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", terminal_verdict="FAILED",
        sell_package_id=None, sell_live_crypto_order_id=None,
    )


@pytest.mark.asyncio
async def test_authorization_is_idempotent_and_preserves_terminal_proof(monkeypatch) -> None:
    proof = _terminal_proof()
    db = _FakeDb([None, proof, None])
    monkeypatch.setattr(exit_recovery, "_validate_exit_recovery", lambda *_args, **_kwargs: _async_none())

    recovery = await exit_recovery.authorize_controlled_proof_exit_recovery(
        db=db, proof_id=proof.proof_id, idempotency_key="exit-recovery-1",
        expires_in_minutes=60, actor="operator:human",
    )

    assert recovery.status == "AUTHORIZED"
    assert proof.status == "EXPIRED"
    assert proof.terminal_verdict == "FAILED"
    assert any(isinstance(item, AuditLog) and item.action == "controlled_proof_exit_recovery.authorized" for item in db.added)
    replay_db = _FakeDb([recovery])
    assert await exit_recovery.authorize_controlled_proof_exit_recovery(
        db=replay_db, proof_id=proof.proof_id, idempotency_key="exit-recovery-1",
        expires_in_minutes=60, actor="operator:human",
    ) is recovery


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_idempotency_key_cannot_cross_proofs() -> None:
    existing = SimpleNamespace(proof_id=uuid.uuid4())
    with pytest.raises(InvalidRequestError, match="belongs to another proof"):
        await exit_recovery.authorize_controlled_proof_exit_recovery(
            db=_FakeDb([existing]), proof_id=uuid.uuid4(), idempotency_key="same-key",
            expires_in_minutes=60, actor="operator:human",
        )


@pytest.mark.asyncio
async def test_terminal_attempt_does_not_prevent_fresh_explicit_authorization(monkeypatch) -> None:
    proof = _terminal_proof()
    async def _valid(*_args, **_kwargs):
        return None
    monkeypatch.setattr(exit_recovery, "_validate_exit_recovery", _valid)
    first = ControlledProofExitRecovery(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="EXPIRED",
        idempotency_key="old-attempt", authorized_by="operator:human",
        authorized_at=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db = _FakeDb([None, proof, None])
    recovery = await exit_recovery.authorize_controlled_proof_exit_recovery(
        db=db, proof_id=proof.proof_id, idempotency_key="fresh-attempt",
        expires_in_minutes=30, actor="operator:human",
    )
    assert first.status == "EXPIRED"
    assert recovery.status == "AUTHORIZED"
    assert recovery.idempotency_key == "fresh-attempt"


@pytest.mark.asyncio
async def test_claim_is_exit_only_and_does_not_change_terminal_proof(monkeypatch) -> None:
    proof = _terminal_proof()
    recovery = ControlledProofExitRecovery(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="AUTHORIZED",
        idempotency_key="r", authorized_by="operator:human",
        authorized_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        audit_correlation_id=uuid.uuid4(), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db = _FakeDb([recovery, proof])
    async def _valid(*_args, **_kwargs):
        return None
    monkeypatch.setattr(exit_recovery, "_validate_exit_recovery", _valid)

    claimed = await exit_recovery.claim_exit_recovery_by_id(db=db, recovery_id=recovery.recovery_id)

    assert claimed == (recovery, proof)
    assert recovery.status == "IN_PROGRESS"
    assert proof.status == "EXPIRED"


@pytest.mark.asyncio
async def test_reconciled_filled_sell_completes_recovery_without_rewriting_proof_idempotently(monkeypatch) -> None:
    import app.services.controlled_proof.service as proof_service
    import app.services.orchestration.continuous_pipeline_worker as worker

    now = datetime.now(timezone.utc)
    sell_order_id = uuid.uuid4()
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", terminal_verdict="FAILED",
        net_pnl_usd=None, sell_live_crypto_order_id=sell_order_id,
        provider="kraken_spot", environment="production", product_id="BTC-USD",
        updated_at=now,
    )
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), status="IN_PROGRESS", completed_at=None, updated_at=now,
    )
    order = SimpleNamespace(live_crypto_order_id=sell_order_id, status="FILLED")
    claim = SimpleNamespace(claim_id=uuid.uuid4(), claim_status="COMPLETED")
    db = _FakeDb([order, claim])

    async def _view(**_kwargs):
        return {"net_pnl_usd": Decimal("0.17")}
    async def _scope(*_args, **_kwargs):
        return SimpleNamespace(), uuid.uuid4()
    async def _zero(**_kwargs):
        return Decimal("0")

    monkeypatch.setattr(proof_service, "get_controlled_proof_view", _view)
    monkeypatch.setattr(worker, "_has_unresolved_reconciliation", lambda **_kwargs: _async_false())
    monkeypatch.setattr(exit_recovery, "_load_scope", _scope)
    monkeypatch.setattr(exit_recovery, "compute_signed_owned_quantity", _zero)
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    await exit_recovery.refresh_exit_recovery_completion(db=db, recovery=recovery, proof=proof)

    assert proof.status == "EXPIRED"
    assert proof.terminal_verdict == "FAILED"
    assert proof.net_pnl_usd is None
    assert recovery.status == "COMPLETED"
    assert recovery.completed_at == now
    assert any(
        isinstance(item, AuditLog)
        and item.action == "controlled_proof_run.exit_recovery_accounting_completed"
        and item.before_state["status"] == "EXPIRED"
        and item.after_state["status"] == "EXPIRED"
        and item.after_state["recovered_terminal_verdict"] == "LIFECYCLE_PROVEN_PROFIT"
        and item.after_state["recovered_net_pnl_usd"] == "0.17"
        for item in db.added
    )
    audit_count = len(db.added)

    await exit_recovery.refresh_exit_recovery_completion(db=db, recovery=recovery, proof=proof)
    assert len(db.added) == audit_count


@pytest.mark.asyncio
@pytest.mark.parametrize(("order_status", "claim_status"), [
    ("PARTIALLY_FILLED", "SUBMISSION_PENDING"),
    ("ACKNOWLEDGED", "SUBMISSION_PENDING"),
    ("FILLED", "RECONCILIATION_REQUIRED"),
])
async def test_nonterminal_sell_or_claim_keeps_exit_recovery_in_progress(
    order_status, claim_status,
) -> None:
    sell_order_id = uuid.uuid4()
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), sell_live_crypto_order_id=sell_order_id,
        provider="kraken_spot", environment="production", product_id="BTC-USD",
    )
    recovery = SimpleNamespace(recovery_id=uuid.uuid4(), status="IN_PROGRESS")
    db = _FakeDb([
        SimpleNamespace(live_crypto_order_id=sell_order_id, status=order_status),
        SimpleNamespace(claim_id=uuid.uuid4(), claim_status=claim_status),
    ])

    await exit_recovery.refresh_exit_recovery_completion(db=db, recovery=recovery, proof=proof)

    assert recovery.status == "IN_PROGRESS"
    assert db.added == []


@pytest.mark.asyncio
async def test_failed_claim_revalidation_commits_block_and_audit(monkeypatch) -> None:
    proof = _terminal_proof()
    recovery = ControlledProofExitRecovery(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="AUTHORIZED",
        idempotency_key="blocked", authorized_by="operator:human",
        authorized_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        audit_correlation_id=uuid.uuid4(), created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db = _FakeDb([recovery, proof])

    async def _invalid(*_args, **_kwargs):
        raise InvalidRequestError(message="ownership changed", details={})

    monkeypatch.setattr(exit_recovery, "_validate_exit_recovery", _invalid)

    assert await exit_recovery.claim_exit_recovery_by_id(db=db, recovery_id=recovery.recovery_id) is None
    assert recovery.status == "BLOCKED"
    assert db.commits == 1
    assert any(
        isinstance(item, AuditLog) and item.action == "controlled_proof_exit_recovery.blocked"
        for item in db.added
    )


@pytest.mark.asyncio
async def test_stale_unused_sell_package_is_superseded_for_fresh_recovery_authority(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="ACTIVATED",
        authorization_source="MANDATE", authorization_expires_at=now - timedelta(minutes=1),
        superseded_at=None, invalidated_reason=None,
    )
    proof.sell_package_id = package.package_id
    proof.updated_at = now
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    activation = SimpleNamespace(
        activation_id=uuid.uuid4(), activation_state="ACTIVE",
        expires_at=now - timedelta(seconds=1), updated_at=now,
    )
    db = _FakeDb([None, activation])
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    await exit_recovery.supersede_stale_exit_recovery_sell_package(
        db=db, recovery=recovery, proof=proof, package=package,
    )

    assert package.package_state == "SUPERSEDED"
    assert activation.activation_state == "EXPIRED"
    assert proof.sell_package_id is None
    assert any(
        isinstance(item, AuditLog)
        and item.action == "controlled_proof_exit_recovery.stale_sell_package_superseded"
        for item in db.added
    )


@pytest.mark.asyncio
async def test_stale_sell_package_with_execution_claim_cannot_be_replaced(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="ACTIVATED",
        authorization_source="MANDATE", authorization_expires_at=now - timedelta(minutes=1),
    )
    proof.sell_package_id = package.package_id
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    existing_claim = SimpleNamespace(claim_status="SUBMISSION_PENDING", live_order_id=uuid.uuid4())
    with pytest.raises(InvalidRequestError, match="unresolved execution lineage"):
        await exit_recovery.supersede_stale_exit_recovery_sell_package(
            db=_FakeDb([existing_claim]), recovery=recovery, proof=proof, package=package,
        )

    assert proof.sell_package_id == package.package_id
    assert package.package_state == "ACTIVATED"


@pytest.mark.asyncio
async def test_failed_pre_provider_claim_permits_fresh_package_only_with_terminal_order_evidence(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="ACTIVATED",
        authorization_source="MANDATE", authorization_expires_at=now - timedelta(minutes=1),
        superseded_at=None, invalidated_reason=None,
    )
    proof.sell_package_id = package.package_id
    order_id = uuid.uuid4()
    claim = SimpleNamespace(claim_status="FAILED_PRE_PROVIDER", live_order_id=order_id)
    order = SimpleNamespace(
        status="CANCELLED", provider_order_id=None, submitted_at=None,
        safe_provider_response={"provider_call_made": False},
    )
    activation = SimpleNamespace(
        activation_id=uuid.uuid4(), activation_state="ACTIVE",
        expires_at=now - timedelta(seconds=1), updated_at=now,
    )
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    await exit_recovery.supersede_stale_exit_recovery_sell_package(
        db=_FakeDb([claim, order, activation]), recovery=recovery, proof=proof, package=package,
    )

    assert package.package_state == "SUPERSEDED"
    assert proof.sell_package_id is None


@pytest.mark.asyncio
async def test_explicit_provider_rejection_permits_governed_stale_package_replacement(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="ACTIVATED",
        authorization_source="MANDATE", authorization_expires_at=now - timedelta(minutes=1),
        superseded_at=None, invalidated_reason=None,
    )
    proof.sell_package_id = package.package_id
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    order_id = uuid.uuid4()
    claim = SimpleNamespace(
        claim_id=uuid.uuid4(), claim_status="CANCELLED", live_order_id=order_id,
    )
    order = SimpleNamespace(
        live_crypto_order_id=order_id, status="REJECTED", provider_order_id=None,
        submitted_at=now - timedelta(minutes=2),
        safe_provider_response={
            # Historical rows may still say false; the classified provider
            # response, not this obsolete marker, is authoritative here.
            "provider_call_made": False,
            "create_order_responded": True,
            "create_order_error": {
                "code": "invalid_base_size",
                "message": "Kraken market sell submission requires base_size > 0",
                "rejection_reason": "provider_explicit_rejection",
            },
        },
    )
    activation = SimpleNamespace(
        activation_id=uuid.uuid4(), activation_state="COMPLETED",
        expires_at=now - timedelta(seconds=1), updated_at=now,
    )
    db = _FakeDb([claim, order, activation])
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    await exit_recovery.supersede_stale_exit_recovery_sell_package(
        db=db, recovery=recovery, proof=proof, package=package,
    )

    assert package.package_state == "SUPERSEDED"
    assert proof.sell_package_id is None
    recovery_audit = next(
        item for item in db.added
        if isinstance(item, AuditLog)
        and item.action == "controlled_proof_exit_recovery.stale_sell_package_superseded"
    )
    lineage = recovery_audit.after_state["terminal_rejection_lineage"]
    assert lineage == {
        "execution_claim_id": str(claim.claim_id),
        "live_crypto_order_id": str(order_id),
        "provider_error_code": "invalid_base_size",
        "rejection_message": "Kraken market sell submission requires base_size > 0",
        "provider_order_id": None,
        "provider_outcome": "REJECTED",
    }

    audit_count = len(db.added)
    with pytest.raises(InvalidRequestError, match="not eligible"):
        await exit_recovery.supersede_stale_exit_recovery_sell_package(
            db=db, recovery=recovery, proof=proof, package=package,
        )
    assert len(db.added) == audit_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("claim_status", "order_status", "provider_order_id", "responded", "rejection_reason"),
    [
        ("RECONCILIATION_REQUIRED", "RECONCILIATION_REQUIRED", None, False, None),
        ("SUBMISSION_PENDING", "UNKNOWN", None, False, None),
        ("COMPLETED", "FILLED", "KRAKEN-ORDER", True, None),
        ("CANCELLED", "REJECTED", "KRAKEN-ORDER", True, "provider_explicit_rejection"),
        ("CANCELLED", "REJECTED", None, False, "provider_explicit_rejection"),
    ],
)
async def test_potentially_filled_or_ambiguous_lineage_still_blocks_replacement(
    monkeypatch, claim_status, order_status, provider_order_id, responded, rejection_reason,
) -> None:
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="ACTIVATED",
        authorization_source="MANDATE", authorization_expires_at=now - timedelta(minutes=1),
    )
    proof.sell_package_id = package.package_id
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    order_id = uuid.uuid4()
    claim = SimpleNamespace(claim_id=uuid.uuid4(), claim_status=claim_status, live_order_id=order_id)
    order = SimpleNamespace(
        live_crypto_order_id=order_id, status=order_status, provider_order_id=provider_order_id,
        submitted_at=now - timedelta(minutes=2),
        safe_provider_response={
            "create_order_responded": responded,
            "create_order_error": {
                "code": "provider_error", "message": "provider result",
                "rejection_reason": rejection_reason,
            },
        },
    )
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    with pytest.raises(InvalidRequestError, match="unresolved"):
        await exit_recovery.supersede_stale_exit_recovery_sell_package(
            db=_FakeDb([claim, order]), recovery=recovery, proof=proof, package=package,
        )

    assert proof.sell_package_id == package.package_id


@pytest.mark.asyncio
async def test_authorization_predicates_accept_exact_authoritative_lineage(monkeypatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", package_id=uuid.uuid4(), sell_package_id=None,
        sell_live_crypto_order_id=None, buy_live_crypto_order_id=uuid.uuid4(), provider="kraken_spot",
        environment="production", product_id="BTC-USD", campaign_id=uuid.uuid4(), position_id="position-1",
    )
    order = SimpleNamespace(live_crypto_order_id=proof.buy_live_crypto_order_id, side="BUY", status="FILLED")
    reconciliation = SimpleNamespace(reconciliation_status="filled")
    accounting = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDb([order, reconciliation, accounting])
    monkeypatch.setattr(worker, "_has_open_live_order", _async_false)
    monkeypatch.setattr(worker, "_has_unresolved_reconciliation", _async_false)
    monkeypatch.setattr(exit_recovery, "_load_scope", _async_scope)
    monkeypatch.setattr(exit_recovery, "compute_signed_owned_quantity", _async_quantity)
    monkeypatch.setattr(exit_recovery, "load_position_snapshots", _async_positions)

    await exit_recovery._validate_exit_recovery(db, proof)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["REQUESTED", "CLAIMED", "POSITION_OPEN", "CANCELLED", "BLOCKED"])
async def test_authorization_rejects_nonterminal_or_unapproved_terminal_status(status: str) -> None:
    proof = _terminal_proof(); proof.status = status; proof.package_id = uuid.uuid4()
    with pytest.raises(InvalidRequestError, match="not eligible"):
        await exit_recovery._validate_exit_recovery(_FakeDb([]), proof)


@pytest.mark.asyncio
async def test_authorization_rejects_existing_sell_lineage() -> None:
    proof = _terminal_proof(); proof.package_id = uuid.uuid4(); proof.sell_package_id = uuid.uuid4()
    with pytest.raises(InvalidRequestError, match="already has SELL lineage"):
        await exit_recovery._validate_exit_recovery(_FakeDb([]), proof)


async def _async_false(**_kwargs):
    return False


async def _async_scope(_db, _proof):
    return SimpleNamespace(id=7, paper_account_id=uuid.uuid4()), uuid.uuid4()


async def _async_quantity(**_kwargs):
    return 0.0001


async def _async_positions(**_kwargs):
    return [SimpleNamespace(symbol="BTC-USD", position_size=0.0001, position_id="position-1")]


def test_model_declares_idempotency_and_one_active_recovery_per_proof() -> None:
    table = ControlledProofExitRecovery.__table__
    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("idempotency_key",) in unique_column_sets
    active_index = next(index for index in table.indexes if index.name == "uq_controlled_proof_exit_recoveries_active_proof")
    assert active_index.unique is True
    assert tuple(column.name for column in active_index.columns) == ("proof_id",)
    assert "AUTHORIZED" in str(active_index.dialect_options["postgresql"]["where"])
    assert "IN_PROGRESS" in str(active_index.dialect_options["postgresql"]["where"])
