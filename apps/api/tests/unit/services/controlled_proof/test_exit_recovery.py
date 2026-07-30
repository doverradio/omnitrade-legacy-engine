from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRequestError
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.candle import Candle
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.models.controlled_proof_exit_recovery import ControlledProofExitRecovery
from app.models.controlled_proof_run import ControlledProofRun
from app.models.live_accounting_record import LiveAccountingRecord
from app.models.live_crypto_order import LiveCryptoOrder
from app.models.live_reconciliation_event import LiveReconciliationEvent
from app.models.live_trading_profile import LiveTradingProfile
from app.services.controlled_proof import exit_recovery
from tests.support.real_sqlite_session import real_sqlite_session, real_sqlite_session_factory

_RECOVERY_ALL_TABLES = [
    Asset.__table__, AuditLog.__table__, AutonomousExecutionClaim.__table__, Candle.__table__,
    CanonicalPreviewPackage.__table__, ControlledProofExitRecovery.__table__, ControlledProofRun.__table__,
    LiveAccountingRecord.__table__, LiveCryptoOrder.__table__, LiveReconciliationEvent.__table__,
    LiveTradingProfile.__table__,
]


@asynccontextmanager
async def _real_recovery_session() -> AsyncIterator[AsyncSession]:
    async with real_sqlite_session(_RECOVERY_ALL_TABLES) as session:
        yield session


class _ScalarRows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _FakeDb:
    def __init__(self, scalars, many=None):
        self.values = list(scalars)
        self.many = list(many or [])
        self.added = []
        self.commits = 0

    async def scalar(self, _statement):
        return self.values.pop(0) if self.values else None

    async def scalars(self, _statement):
        return _ScalarRows(self.many.pop(0) if self.many else [])

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
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", lambda **_kwargs: _async_false())
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


def _blocked_projection_fixture(*, net_pnl: Decimal = Decimal("0.17")):
    now = datetime.now(timezone.utc)
    proof_id, recovery_id, package_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    buy_order_id, sell_order_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    reconciliation_id = uuid.uuid4()
    latest_reconciliation_id = uuid.uuid4()
    proof = SimpleNamespace(
        proof_id=proof_id, status="EXPIRED", terminal_verdict="FAILED",
        campaign_id=uuid.uuid4(), campaign_version=1, provider="kraken_spot",
        environment="production", product_id="BTC-USD", sell_package_id=package_id,
        buy_live_crypto_order_id=buy_order_id, sell_live_crypto_order_id=sell_order_id,
    )
    recovery = SimpleNamespace(
        recovery_id=recovery_id, proof_id=proof_id, status="BLOCKED",
        blocked_reason="stale_sell_package_replacement_blocked", completed_at=None,
        audit_correlation_id=uuid.uuid4(),
    )
    package = SimpleNamespace(
        package_id=package_id, side="SELL", campaign_id=proof.campaign_id,
        campaign_version=1, provider=proof.provider, environment=proof.environment,
        product=proof.product_id, live_trading_profile_id=profile_id,
        market_evidence_identity={
            "controlled_proof_id": str(proof_id),
            "controlled_proof_exit_recovery_id": str(recovery_id),
        },
    )
    claim = SimpleNamespace(
        claim_id=uuid.uuid4(), package_id=package_id, side="SELL", claim_status="COMPLETED",
        live_order_id=sell_order_id, campaign_id=proof.campaign_id, campaign_version=1,
        profile_id=profile_id, provider=proof.provider, environment=proof.environment,
        product=proof.product_id,
    )
    order = SimpleNamespace(
        live_crypto_order_id=sell_order_id, status="FILLED", provider_order_id="KRAKEN-ORDER",
        side="SELL", provider=proof.provider, environment=proof.environment,
        product_id=proof.product_id,
    )
    reconciliation = SimpleNamespace(
        id=latest_reconciliation_id, reconciliation_status="filled", sequence_number=3,
        event_type="order_reconciled", live_trading_profile_id=profile_id,
        capital_campaign_id=7, provider_name=proof.provider,
        provider_order_id=order.provider_order_id, live_crypto_order_id=sell_order_id,
    )
    accounting_reconciliation = SimpleNamespace(
        id=reconciliation_id, reconciliation_status="filled", sequence_number=2,
        event_type="fill_reconciled", live_trading_profile_id=profile_id,
        capital_campaign_id=7, provider_name=proof.provider,
        provider_order_id=order.provider_order_id, live_crypto_order_id=sell_order_id,
    )
    buy_cash = Decimal("-5")
    accounting = [
        SimpleNamespace(
            live_crypto_order_id=buy_order_id, side="buy", symbol="BTC-USD",
            reconciliation_event_id=uuid.uuid4(), record_type="fill_accounting",
            net_cash_impact=buy_cash,
        ),
        SimpleNamespace(
            live_crypto_order_id=sell_order_id, side="sell", symbol="BTC-USD",
            reconciliation_event_id=reconciliation_id, record_type="fill_accounting",
            net_cash_impact=-buy_cash + net_pnl,
        ),
    ]
    return SimpleNamespace(
        now=now, proof=proof, recovery=recovery, package=package, claim=claim,
        order=order, reconciliation=reconciliation,
        accounting_reconciliation=accounting_reconciliation, accounting=accounting,
        profile_id=profile_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("net_pnl", "verdict"), [
    (Decimal("0.17"), "LIFECYCLE_PROVEN_PROFIT"),
    (Decimal("0"), "LIFECYCLE_PROVEN_FLAT"),
    (Decimal("-0.17"), "LIFECYCLE_PROVEN_LOSS"),
])
async def test_blocked_recovery_projects_separate_reconciled_outcome(
    monkeypatch, net_pnl, verdict,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker

    item = _blocked_projection_fixture(net_pnl=net_pnl)
    db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation,
         item.accounting_reconciliation],
        [[item.package], item.accounting],
    )
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", lambda **_kwargs: _async_false())
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(
        exit_recovery, "_load_scope",
        lambda *_args, **_kwargs: _async((SimpleNamespace(id=7), item.profile_id)),
    )
    monkeypatch.setattr(
        exit_recovery, "compute_signed_owned_quantity",
        lambda **_kwargs: _async(Decimal("0")),
    )
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: item.now)

    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is True

    assert item.recovery.status == "BLOCKED"
    assert item.recovery.blocked_reason == "stale_sell_package_replacement_blocked"
    # The proof finalizes truthfully -- separately from the recovery's own
    # immutable BLOCKED row -- overwriting the stale "FAILED" verdict this
    # fixture seeds (matching the confirmed production shape: an EXPIRED
    # proof whose terminal_verdict was frozen to "FAILED" before the
    # governed-replacement SELL ever filled).
    assert item.proof.net_pnl_usd == net_pnl
    assert item.proof.terminal_verdict == verdict
    audit = next(value for value in db.added if isinstance(value, AuditLog))
    assert audit.action == "controlled_proof_exit_recovery.recovered_outcome_published"
    assert audit.after_state["original_recovery_id"] == str(item.recovery.recovery_id)
    assert audit.after_state["sell_package_id"] == str(item.package.package_id)
    assert audit.after_state["sell_live_crypto_order_id"] == str(item.order.live_crypto_order_id)
    assert audit.after_state["provider_order_id"] == "KRAKEN-ORDER"
    assert audit.after_state["execution_claim_id"] == str(item.claim.claim_id)
    assert audit.after_state["reconciliation_event_id"] == str(item.accounting_reconciliation.id)
    assert audit.after_state["recovered_terminal_verdict"] == verdict
    assert Decimal(audit.after_state["recovered_net_pnl_usd"]) == net_pnl

    replay_db = _FakeDb([item.recovery, audit])
    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=replay_db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert replay_db.added == []
    # Idempotent: replay short-circuits on the existing audit row and never
    # recomputes or re-applies the proof finalization a second time.
    assert item.proof.net_pnl_usd == net_pnl
    assert item.proof.terminal_verdict == verdict


@pytest.mark.asyncio
async def test_existing_recovered_outcome_audit_backfills_null_proof_on_replay(monkeypatch) -> None:
    """Production upgrade case: the immutable recovered-outcome audit row
    already exists (an older deploy published it before proof
    finalization existed) while ControlledProofRun still shows
    net_pnl_usd=null/terminal_verdict=FAILED. The existing-audit branch
    must safely backfill the proof exactly once, without mutating or
    duplicating the audit row."""
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    item = _blocked_projection_fixture(net_pnl=Decimal("0.16066"))
    existing_audit = SimpleNamespace(
        action="controlled_proof_exit_recovery.recovered_outcome_published",
        after_state={
            "status": "COMPLETED_RECONCILED",
            "original_recovery_id": str(item.recovery.recovery_id),
            "proof_id": str(item.proof.proof_id),
            "sell_package_id": str(item.package.package_id),
            "sell_live_crypto_order_id": str(item.order.live_crypto_order_id),
            "recovered_terminal_verdict": "LIFECYCLE_PROVEN_PROFIT",
            "recovered_net_pnl_usd": "0.16066",
        },
    )
    item.proof.net_pnl_usd = None
    item.proof.terminal_verdict = "FAILED"
    db = _FakeDb([item.recovery, existing_audit])

    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is True

    assert db.added == []
    assert item.proof.net_pnl_usd == Decimal("0.16066")
    assert item.proof.terminal_verdict == "LIFECYCLE_PROVEN_PROFIT"

    # Replay again: no additional audit row, values unchanged.
    replay_db = _FakeDb([item.recovery, existing_audit])
    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=replay_db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert replay_db.added == []
    assert item.proof.net_pnl_usd == Decimal("0.16066")
    assert item.proof.terminal_verdict == "LIFECYCLE_PROVEN_PROFIT"


@pytest.mark.asyncio
async def test_existing_recovered_outcome_audit_fails_closed_on_mismatch_or_malformed_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    item = _blocked_projection_fixture(net_pnl=Decimal("0.16066"))
    item.proof.net_pnl_usd = None
    item.proof.terminal_verdict = "FAILED"
    base_payload = {
        "status": "COMPLETED_RECONCILED",
        "original_recovery_id": str(item.recovery.recovery_id),
        "proof_id": str(item.proof.proof_id),
        "sell_package_id": str(item.package.package_id),
        "sell_live_crypto_order_id": str(item.order.live_crypto_order_id),
        "recovered_terminal_verdict": "LIFECYCLE_PROVEN_PROFIT",
        "recovered_net_pnl_usd": "0.16066",
    }
    for broken in (
        {**base_payload, "proof_id": str(uuid.uuid4())},
        {**base_payload, "sell_package_id": str(uuid.uuid4())},
        {**base_payload, "status": "SOMETHING_ELSE"},
        {**base_payload, "recovered_terminal_verdict": "NOT_A_VERDICT"},
        {**base_payload, "recovered_net_pnl_usd": "not-a-number"},
        None,
    ):
        item.proof.net_pnl_usd = None
        item.proof.terminal_verdict = "FAILED"
        existing_audit = SimpleNamespace(
            action="controlled_proof_exit_recovery.recovered_outcome_published", after_state=broken,
        )
        db = _FakeDb([item.recovery, existing_audit])

        assert await exit_recovery.project_blocked_exit_recovery_outcome(
            db=db, recovery=item.recovery, proof=item.proof,
        ) is False
        assert db.added == []
        assert item.proof.net_pnl_usd is None
        assert item.proof.terminal_verdict == "FAILED"


@pytest.mark.asyncio
async def test_blocked_recovery_accepts_identical_latest_and_accounting_fill_event(monkeypatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker

    item = _blocked_projection_fixture()
    item.reconciliation = item.accounting_reconciliation
    db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation,
         item.accounting_reconciliation],
        [[item.package], item.accounting],
    )
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", lambda **_kwargs: _async_false())
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(
        exit_recovery, "_load_scope",
        lambda *_args, **_kwargs: _async((SimpleNamespace(id=7), item.profile_id)),
    )
    monkeypatch.setattr(
        exit_recovery, "compute_signed_owned_quantity",
        lambda **_kwargs: _async(Decimal("0")),
    )

    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["order", "provider"])
async def test_blocked_recovery_rejects_mismatched_accounting_fill_provenance(
    monkeypatch, mismatch,
) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker

    item = _blocked_projection_fixture()
    if mismatch == "order":
        item.accounting_reconciliation.live_crypto_order_id = uuid.uuid4()
    else:
        item.accounting_reconciliation.provider_order_id = "UNRELATED-ORDER"
    db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation,
         item.accounting_reconciliation],
        [[item.package], item.accounting],
    )
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", lambda **_kwargs: _async_false())
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(
        exit_recovery, "_load_scope",
        lambda *_args, **_kwargs: _async((SimpleNamespace(id=7), item.profile_id)),
    )
    monkeypatch.setattr(
        exit_recovery, "compute_signed_owned_quantity",
        lambda **_kwargs: _async(Decimal("0")),
    )

    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_blocked_recovery_does_not_project_unrelated_package() -> None:
    item = _blocked_projection_fixture()
    item.package.market_evidence_identity["controlled_proof_exit_recovery_id"] = str(uuid.uuid4())
    db = _FakeDb([item.recovery, None], [[item.package]])

    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert item.recovery.status == "BLOCKED"
    assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value"), [
    ("claim_status", "RECONCILIATION_REQUIRED"),
    ("order_status", "PARTIALLY_FILLED"),
    ("reconciliation_status", "reconciliation_required"),
])
async def test_blocked_recovery_incomplete_lineage_does_not_project(field, value) -> None:
    item = _blocked_projection_fixture()
    if field == "claim_status":
        item.claim.claim_status = value
    elif field == "order_status":
        item.order.status = value
    else:
        item.reconciliation.reconciliation_status = value
    db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation],
        [[item.package]],
    )

    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_blocked_recovery_requires_zero_ownership_and_complete_accounting(monkeypatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker

    item = _blocked_projection_fixture()
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", lambda **_kwargs: _async_false())
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(
        exit_recovery, "_load_scope",
        lambda *_args, **_kwargs: _async((SimpleNamespace(id=7), item.profile_id)),
    )
    monkeypatch.setattr(
        exit_recovery, "compute_signed_owned_quantity",
        lambda **_kwargs: _async(Decimal("0.00001")),
    )
    nonzero_db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation], [[item.package]],
    )
    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=nonzero_db, recovery=item.recovery, proof=item.proof,
    ) is False

    monkeypatch.setattr(
        exit_recovery, "compute_signed_owned_quantity",
        lambda **_kwargs: _async(Decimal("0")),
    )
    missing_accounting_db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation],
        [[item.package], item.accounting[:1]],
    )
    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=missing_accounting_db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert nonzero_db.added == missing_accounting_db.added == []


@pytest.mark.asyncio
async def test_blocked_recovery_unresolved_reconciliation_does_not_project(monkeypatch) -> None:
    import app.services.orchestration.continuous_pipeline_worker as worker

    item = _blocked_projection_fixture()
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", lambda **_kwargs: _async(True))
    db = _FakeDb(
        [item.recovery, None, item.claim, item.order, item.reconciliation], [[item.package]],
    )
    assert await exit_recovery.project_blocked_exit_recovery_outcome(
        db=db, recovery=item.recovery, proof=item.proof,
    ) is False
    assert db.added == []


@pytest.mark.asyncio
async def test_exit_recovery_view_exposes_blocked_attempt_and_recovered_outcome() -> None:
    item = _blocked_projection_fixture()
    outcome = {
        "status": "COMPLETED_RECONCILED",
        "original_recovery_id": str(item.recovery.recovery_id),
        "proof_id": str(item.proof.proof_id),
        "sell_package_id": str(item.package.package_id),
        "sell_live_crypto_order_id": str(item.order.live_crypto_order_id),
        "provider_order_id": item.order.provider_order_id,
        "execution_claim_id": str(item.claim.claim_id),
        "reconciliation_event_id": str(item.reconciliation.id),
        "recovered_terminal_verdict": "LIFECYCLE_PROVEN_PROFIT",
        "recovered_net_pnl_usd": "0.17",
        "completed_at": item.now.isoformat(),
        "audit_correlation_id": str(item.recovery.audit_correlation_id),
    }
    item.recovery.idempotency_key = "historical"
    item.recovery.authorized_by = "operator:human"
    item.recovery.authorized_at = item.now
    item.recovery.expires_at = item.now
    item.recovery.claimed_at = item.now
    item.recovery.failure_reason = None
    audit = SimpleNamespace(after_state=outcome)
    db = _FakeDb([None, item.recovery, audit], [[]])

    view = await exit_recovery.get_exit_recovery_view(
        db=db, proof_id=item.proof.proof_id,
    )

    assert view["status"] == "BLOCKED"
    assert view["blocked_reason"] == "stale_sell_package_replacement_blocked"
    assert view["completed_at"] is None
    assert view["recovered_outcome"] == outcome


@pytest.mark.asyncio
async def test_periodic_refresh_projects_historical_blocked_recovery(monkeypatch) -> None:
    item = _blocked_projection_fixture()
    projected = []

    async def _project(*, db, recovery, proof):
        projected.append((recovery.recovery_id, proof.proof_id))
        return True

    monkeypatch.setattr(exit_recovery, "project_blocked_exit_recovery_outcome", _project)
    db = _FakeDb([item.proof], [[item.recovery]])

    await exit_recovery.refresh_exit_recovery_outcomes(db=db)

    assert projected == [(item.recovery.recovery_id, item.proof.proof_id)]


async def _async(value):
    return value


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
async def test_expired_preview_unactivated_sell_package_is_superseded_for_fresh_recovery_authority(monkeypatch) -> None:
    """The disjoint staleness shape: a SELL package that never reached
    ACTIVATED before its own canonical preview window expired (the
    confirmed production shape for package 63cd5c1b-...) must also be
    superseded so a fresh governed SELL package can be created -- distinct
    from supersede_stale_exit_recovery_sell_package, which only handles an
    already-ACTIVATED package's post-activation authorization expiry."""
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="READY",
        preview_expires_at=now - timedelta(minutes=1),
        superseded_at=None, invalidated_reason=None,
    )
    proof.sell_package_id = package.package_id
    proof.updated_at = now
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    db = _FakeDb([])
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)

    await exit_recovery.supersede_expired_preview_exit_recovery_sell_package(
        db=db, recovery=recovery, proof=proof, package=package,
    )

    assert package.package_state == "SUPERSEDED"
    assert proof.sell_package_id is None
    assert any(
        isinstance(item, AuditLog)
        and item.action == "controlled_proof_exit_recovery.stale_sell_package_superseded"
        for item in db.added
    )
    assert any(
        isinstance(item, AuditLog)
        and item.action == "canonical_preview_package.superseded_for_exit_recovery"
        for item in db.added
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda proof, recovery, package: setattr(recovery, "proof_id", uuid.uuid4()), "not eligible"),
        (lambda proof, recovery, package: setattr(recovery, "status", "AUTHORIZED"), "not eligible"),
        (lambda proof, recovery, package: setattr(recovery, "expires_at", datetime.now(timezone.utc) - timedelta(minutes=1)), "not eligible"),
        (lambda proof, recovery, package: setattr(proof, "sell_package_id", uuid.uuid4()), "not eligible"),
        (lambda proof, recovery, package: setattr(package, "side", "BUY"), "not eligible"),
        (lambda proof, recovery, package: setattr(package, "package_state", "ACTIVATED"), "not eligible"),
        (lambda proof, recovery, package: setattr(package, "package_state", "COMPLETED"), "not eligible"),
        (lambda proof, recovery, package: setattr(package, "preview_expires_at", datetime.now(timezone.utc) + timedelta(minutes=5)), "not eligible"),
    ],
)
async def test_expired_preview_supersession_fails_closed_for_every_ineligible_case(mutate, match, monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    proof = _terminal_proof()
    package = SimpleNamespace(
        package_id=uuid.uuid4(), side="SELL", package_state="READY",
        preview_expires_at=now - timedelta(minutes=1),
        superseded_at=None, invalidated_reason=None,
    )
    proof.sell_package_id = package.package_id
    recovery = SimpleNamespace(
        recovery_id=uuid.uuid4(), proof_id=proof.proof_id, status="IN_PROGRESS",
        expires_at=now + timedelta(minutes=30),
    )
    monkeypatch.setattr(exit_recovery, "_utcnow", lambda: now)
    mutate(proof, recovery, package)

    with pytest.raises(InvalidRequestError, match=match):
        await exit_recovery.supersede_expired_preview_exit_recovery_sell_package(
            db=_FakeDb([]), recovery=recovery, proof=proof, package=package,
        )
    assert package.package_state != "SUPERSEDED"


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
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", _async_false)
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(exit_recovery, "_load_scope", _async_scope)
    monkeypatch.setattr(exit_recovery, "compute_signed_owned_quantity", _async_quantity)
    monkeypatch.setattr(exit_recovery, "load_position_snapshots", _async_positions)
    # position_id is now recomputed deterministically from
    # (profile_id, campaign_id, symbol) rather than trusted from
    # proof.position_id -- _async_scope returns a fresh random profile_id
    # each call, so pin the expected value directly rather than
    # hand-deriving a matching uuid5.
    monkeypatch.setattr(exit_recovery, "_position_id", lambda **_kwargs: "position-1")

    await exit_recovery._validate_exit_recovery(db, proof)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["REQUESTED", "CLAIMED", "POSITION_OPEN", "CANCELLED", "BLOCKED"])
async def test_authorization_rejects_nonterminal_or_unapproved_terminal_status(status: str) -> None:
    proof = _terminal_proof(); proof.status = status; proof.package_id = uuid.uuid4()
    with pytest.raises(InvalidRequestError, match="not eligible"):
        await exit_recovery._validate_exit_recovery(_FakeDb([]), proof)


@pytest.mark.asyncio
async def test_authorization_rejects_existing_sell_lineage(monkeypatch) -> None:
    proof = _terminal_proof(); proof.package_id = uuid.uuid4(); proof.sell_package_id = uuid.uuid4()
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
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


# --- regression: cached-column-only ownership evidence must never be required ---

@pytest.mark.asyncio
async def test_authorization_recovers_position_when_no_cache_column_was_ever_populated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct regression test for the confirmed production defect: nothing
    in this codebase ever writes ControlledProofRun.buy_live_crypto_order_id
    or .position_id except get_controlled_proof_view's opportunistic
    backfill (and, as of this fix, exit recovery's own repair) -- a proof
    that expired without that view ever having been queried had both
    columns permanently None, which made _validate_exit_recovery raise
    unconditionally on every single authorization attempt, forever. Builds
    real canonical lineage (package -> claim -> order -> accounting), not
    mocks, so the repair and the deterministic position_id computation are
    genuinely exercised end to end."""
    import app.services.orchestration.continuous_pipeline_worker as worker

    campaign_id = uuid.uuid4()
    campaign_row_id = 7
    paper_account_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    product_id = "BTC-USD"

    async with _real_recovery_session() as session:
        package_id = uuid.uuid4()
        proof = ControlledProofRun(
            proof_id=uuid.uuid4(), status="EXPIRED", provider="kraken_spot", environment="production",
            campaign_id=campaign_id, campaign_version=1, product_id=product_id,
            max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            package_id=package_id,
            # Exactly the confirmed production state: no cache column, and
            # position_id, was ever populated.
            buy_live_crypto_order_id=None, sell_live_crypto_order_id=None, position_id=None,
        )
        session.add(proof)
        await session.flush()

        package = CanonicalPreviewPackage(
            package_id=package_id, campaign_id=campaign_id, campaign_version=1,
            runtime_campaign_id=uuid.uuid4(), paper_account_id=paper_account_id, live_trading_profile_id=profile_id,
            provider="kraken_spot", environment="production", product=product_id, side="BUY",
            proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
            strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
            decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
            preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state="ACTIVATED",
            generated_at=datetime.now(timezone.utc), idempotency_key=f"pkg-{uuid.uuid4()}", input_fingerprint="fp",
            market_evidence_identity={"controlled_proof_id": str(proof.proof_id)},
        )
        session.add(package)
        await session.flush()

        order = LiveCryptoOrder(
            live_crypto_order_id=uuid.uuid4(), crypto_order_preview_id=package.crypto_order_preview_id,
            exchange_connection_id=uuid.uuid4(), provider="kraken_spot", environment="production",
            product_id=product_id, side="BUY", order_type="MARKET", requested_quote_size=Decimal("5"),
            client_order_id=f"buy-{uuid.uuid4()}", status="FILLED",
            provider_order_id=f"provider-{uuid.uuid4()}", submitted_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        )
        session.add(order)
        await session.flush()

        claim = AutonomousExecutionClaim(
            claim_id=uuid.uuid4(), package_id=package.package_id, activation_id=uuid.uuid4(),
            campaign_id=campaign_id, campaign_version=1, mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
            account_id=paper_account_id, profile_id=profile_id, connection_id=uuid.uuid4(),
            provider="kraken_spot", environment="production", product=product_id, side="BUY",
            claim_status="COMPLETED", claimed_at=datetime.now(timezone.utc), claim_owner="test",
            live_order_id=order.live_crypto_order_id,
        )
        session.add(claim)

        reconciliation = LiveReconciliationEvent(
            idempotency_key="recon-buy-1", event_hash="hash-buy-1", live_trading_profile_id=profile_id,
            live_crypto_order_id=order.live_crypto_order_id, capital_campaign_id=campaign_row_id,
            source_execution_event_id=uuid.uuid4(), source_execution_event_type="execution_intent_created",
            sequence_number=1, event_type="fill_reconciled", reconciliation_status="filled",
            provider_name="kraken_spot", provider_order_id=order.provider_order_id,
            event_payload={}, provenance={}, immutable_contract_version="1",
            recorded_at=datetime.now(timezone.utc),
        )
        session.add(reconciliation)

        fill = LiveAccountingRecord(
            idempotency_key="buy-fill-1", live_trading_profile_id=profile_id, capital_campaign_id=campaign_row_id,
            live_crypto_order_id=order.live_crypto_order_id,
            reconciliation_event_id=uuid.uuid4(), source_execution_event_id=uuid.uuid4(),
            source_execution_event_type="execution_intent_created", record_type="fill_accounting",
            provider_order_id=order.provider_order_id, symbol=product_id, side="buy",
            filled_quantity=Decimal("0.00007831"), fill_price=Decimal("64000"),
            gross_notional=Decimal("5.01"), fee_amount=Decimal("0.005"), fee_currency="USD",
            net_cash_impact=Decimal("-5.015"), provenance={}, recorded_at=datetime.now(timezone.utc),
        )
        session.add(fill)

        session.add(LiveTradingProfile(
            id=profile_id, paper_account_id=paper_account_id, operating_mode="live", lifecycle_state="enabled",
            approval_state="approved", live_opt_in=True, human_approval_recorded=True, paper_default_mode=True,
            governance_approved=True, risk_authority_model="risk_engine_final", autonomous_capital_allocation=False,
            autonomous_strategy_evolution=False, automatic_promotion_enabled=False, provenance_metadata={},
        ))
        await session.flush()

        monkeypatch.setattr(worker, "_has_open_live_order", _async_false)
        monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", _async_false)

        async def _scope(_db, _proof):
            return SimpleNamespace(id=campaign_row_id, paper_account_id=paper_account_id), profile_id

        monkeypatch.setattr(exit_recovery, "_load_scope", _scope)

        recovery = await exit_recovery.authorize_controlled_proof_exit_recovery(
            db=session, proof_id=proof.proof_id, idempotency_key="exit-recovery-real-1",
            expires_in_minutes=60, actor="operator:alice",
        )

        assert recovery.status == "AUTHORIZED"
        # The two previously-permanent blockers are now backfilled from
        # real canonical lineage as a side effect of authorization
        # succeeding -- never required as a precondition.
        assert proof.buy_live_crypto_order_id == order.live_crypto_order_id
        assert proof.position_id is not None

        from sqlalchemy import select as sa_select
        repair_audit_rows = (await session.scalars(
            sa_select(AuditLog).where(
                AuditLog.entity_type == "controlled_proof_run",
                AuditLog.entity_id == proof.proof_id,
                AuditLog.action == "controlled_proof_run.position_lineage_repaired",
            )
        )).all()
        assert len(repair_audit_rows) == 1
        assert repair_audit_rows[0].after_state["position_id"] == proof.position_id


@pytest.mark.asyncio
async def test_repeated_authorization_attempts_do_not_re_repair_or_duplicate_position_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: idempotency. Revalidating an already-recovered proof a
    second time (e.g. claim_exit_recovery_by_id's own revalidation pass)
    must not raise, must not change an already-correct position_id, and
    must not write a duplicate repair audit row."""
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", package_id=uuid.uuid4(), sell_package_id=None,
        sell_live_crypto_order_id=None, buy_live_crypto_order_id=uuid.uuid4(), provider="kraken_spot",
        environment="production", product_id="BTC-USD", campaign_id=uuid.uuid4(),
    )
    expected_position_id = "position-1"
    proof.position_id = expected_position_id
    order = SimpleNamespace(live_crypto_order_id=proof.buy_live_crypto_order_id, side="BUY", status="FILLED")
    reconciliation = SimpleNamespace(reconciliation_status="filled")
    accounting = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDb([order, reconciliation, accounting])
    import app.services.orchestration.continuous_pipeline_worker as worker
    monkeypatch.setattr(worker, "_has_open_live_order", _async_false)
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", _async_false)
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(exit_recovery, "_load_scope", _async_scope)
    monkeypatch.setattr(exit_recovery, "compute_signed_owned_quantity", _async_quantity)
    monkeypatch.setattr(exit_recovery, "load_position_snapshots", _async_positions)
    monkeypatch.setattr(exit_recovery, "_position_id", lambda **_kwargs: expected_position_id)

    await exit_recovery._validate_exit_recovery(db, proof)
    assert proof.position_id == expected_position_id
    assert not any(
        isinstance(item, AuditLog) and item.action == "controlled_proof_run.position_lineage_repaired"
        for item in db.added
    )

    # Second pass: unchanged state, still no re-write, still no raise.
    db2 = _FakeDb([order, reconciliation, accounting])
    await exit_recovery._validate_exit_recovery(db2, proof)
    assert proof.position_id == expected_position_id
    assert not any(
        isinstance(item, AuditLog) and item.action == "controlled_proof_run.position_lineage_repaired"
        for item in db2.added
    )


@pytest.mark.asyncio
async def test_authorization_fails_closed_when_open_position_belongs_to_a_different_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: fail closed on a genuine mismatch. A position computed
    for a DIFFERENT (profile, campaign, symbol) tuple than this proof's own
    must never be accepted as this proof's position -- the deterministic
    position_id recomputation is a real check, not a rubber stamp."""
    proof = SimpleNamespace(
        proof_id=uuid.uuid4(), status="EXPIRED", package_id=uuid.uuid4(), sell_package_id=None,
        sell_live_crypto_order_id=None, buy_live_crypto_order_id=uuid.uuid4(), provider="kraken_spot",
        environment="production", product_id="BTC-USD", campaign_id=uuid.uuid4(), position_id=None,
    )
    order = SimpleNamespace(live_crypto_order_id=proof.buy_live_crypto_order_id, side="BUY", status="FILLED")
    reconciliation = SimpleNamespace(reconciliation_status="filled")
    accounting = SimpleNamespace(id=uuid.uuid4())
    db = _FakeDb([order, reconciliation, accounting])
    import app.services.orchestration.continuous_pipeline_worker as worker
    monkeypatch.setattr(worker, "_has_open_live_order", _async_false)
    monkeypatch.setattr(exit_recovery, "has_unresolved_reconciliation", _async_false)
    monkeypatch.setattr(
        "app.services.controlled_proof.service.repair_controlled_proof_cached_order_ids", _async_false,
    )
    monkeypatch.setattr(exit_recovery, "_load_scope", _async_scope)
    monkeypatch.setattr(exit_recovery, "compute_signed_owned_quantity", _async_quantity)
    # A real, deterministic id for an unrelated (profile, campaign, symbol)
    # -- never made to match this proof's own scope.
    monkeypatch.setattr(exit_recovery, "load_position_snapshots", _async_positions)
    monkeypatch.setattr(
        exit_recovery, "_position_id",
        lambda **_kwargs: str(uuid.uuid4()),
    )

    with pytest.raises(InvalidRequestError, match="Open position linkage does not match this Controlled Proof"):
        await exit_recovery._validate_exit_recovery(db, proof)


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


@pytest.mark.asyncio
async def test_refresh_exit_recovery_outcomes_sweep_backfills_stuck_proof_from_real_rows() -> None:
    """refresh_exit_recovery_outcomes (the orchestration sweep entry point)
    must backfill a stuck proof from its already-published, valid
    COMPLETED_RECONCILED recovered-outcome audit -- with no live-order
    reconciliation candidates involved at all -- and replay must be a
    no-op (no duplicate audit, unchanged values)."""
    async with _real_recovery_session() as session:
        campaign_id = uuid.uuid4()
        proof_id, recovery_id, package_id, order_id = (
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
        )
        proof = ControlledProofRun(
            proof_id=proof_id, status="FAILED", provider="kraken_spot", environment="production",
            campaign_id=campaign_id, campaign_version=1, product_id="BTC-USD",
            max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            package_id=uuid.uuid4(), sell_package_id=package_id, sell_live_crypto_order_id=order_id,
            net_pnl_usd=None, terminal_verdict="FAILED",
        )
        session.add(proof)
        recovery = ControlledProofExitRecovery(
            recovery_id=recovery_id, proof_id=proof_id, status="BLOCKED",
            idempotency_key=f"idem-{uuid.uuid4()}", authorized_by="operator:alice",
            authorized_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            blocked_reason="stale_sell_package_replacement_blocked:Stale SELL package has unresolved execution lineage",
        )
        session.add(recovery)
        session.add(AuditLog(
            actor="system:controlled_proof_reconciliation_projector",
            action=exit_recovery._RECOVERED_OUTCOME_ACTION,
            entity_type="controlled_proof_exit_recovery", entity_id=recovery_id,
            before_state={}, after_state={
                "status": "COMPLETED_RECONCILED",
                "original_recovery_id": str(recovery_id),
                "proof_id": str(proof_id),
                "sell_package_id": str(package_id),
                "sell_live_crypto_order_id": str(order_id),
                "recovered_terminal_verdict": "LIFECYCLE_PROVEN_LOSS",
                "recovered_net_pnl_usd": "-0.0393333016409",
            },
        ))
        await session.flush()

        await exit_recovery.refresh_exit_recovery_outcomes(db=session)

        refreshed_proof = await session.get(ControlledProofRun, proof_id)
        assert refreshed_proof.net_pnl_usd == Decimal("-0.0393333016409")
        assert refreshed_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
        refreshed_recovery = await session.get(ControlledProofExitRecovery, recovery_id)
        assert refreshed_recovery.status == "BLOCKED"
        outcome_audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
            AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
        ))).all()
        assert len(outcome_audits) == 1

        # Replay: no-op, no duplicate audit, values unchanged.
        await exit_recovery.refresh_exit_recovery_outcomes(db=session)

        replayed_proof = await session.get(ControlledProofRun, proof_id)
        assert replayed_proof.net_pnl_usd == Decimal("-0.0393333016409")
        assert replayed_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
        replayed_audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
            AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
        ))).all()
        assert len(replayed_audits) == 1


@pytest.mark.asyncio
async def test_sweep_projects_expired_proof_with_stale_cache_column_via_real_lineage_repair() -> None:
    """Production-shaped regression: proven root cause was that
    proof.sell_live_crypto_order_id (a denormalized read-side cache) was
    never populated for this proof -- the backfill match against the
    existing recovered-outcome audit silently, permanently skipped every
    sweep pass because that column was None. Builds real canonical
    lineage (package -> claim -> order) so repair_controlled_proof_
    cached_order_ids is genuinely exercised, not mocked."""
    campaign_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    product_id = "BTC-USD"
    package_id, order_id, recovery_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async with _real_recovery_session() as session:
        proof = ControlledProofRun(
            proof_id=uuid.uuid4(), status="EXPIRED", provider="kraken_spot", environment="production",
            campaign_id=campaign_id, campaign_version=1, product_id=product_id,
            max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            package_id=uuid.uuid4(), sell_package_id=package_id,
            # The confirmed production defect: never repaired/populated.
            sell_live_crypto_order_id=None,
            net_pnl_usd=None, terminal_verdict="FAILED",
        )
        session.add(proof)
        await session.flush()

        package = CanonicalPreviewPackage(
            package_id=package_id, campaign_id=campaign_id, campaign_version=1,
            runtime_campaign_id=uuid.uuid4(), paper_account_id=uuid.uuid4(), live_trading_profile_id=profile_id,
            provider="kraken_spot", environment="production", product=product_id, side="SELL",
            proposed_order_amount=Decimal("5"), risk_approved_amount=Decimal("5"),
            strategy_id=uuid.uuid4(), strategy_version="1.0.0", parameter_set_id=uuid.uuid4(), parameter_set_version="1",
            decision_record_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), crypto_order_preview_id=uuid.uuid4(),
            preview_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5), package_state="ACTIVATED",
            generated_at=datetime.now(timezone.utc), idempotency_key=f"pkg-{uuid.uuid4()}", input_fingerprint="fp",
            market_evidence_identity={
                "controlled_proof_id": str(proof.proof_id), "controlled_proof_exit_recovery_id": str(recovery_id),
            },
        )
        session.add(package)
        await session.flush()

        order = LiveCryptoOrder(
            live_crypto_order_id=order_id, crypto_order_preview_id=package.crypto_order_preview_id,
            exchange_connection_id=uuid.uuid4(), provider="kraken_spot", environment="production",
            product_id=product_id, side="SELL", order_type="MARKET", requested_quote_size=Decimal("5"),
            client_order_id=f"sell-{uuid.uuid4()}", status="FILLED",
            provider_order_id="O2ZWU2-ZHMEL-Z6NLLI", submitted_at=datetime.now(timezone.utc),
            filled_at=datetime.now(timezone.utc), audit_correlation_id=uuid.uuid4(),
        )
        session.add(order)
        await session.flush()

        claim = AutonomousExecutionClaim(
            claim_id=uuid.uuid4(), package_id=package.package_id, activation_id=uuid.uuid4(),
            campaign_id=campaign_id, campaign_version=1, mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(),
            account_id=package.paper_account_id, profile_id=profile_id, connection_id=uuid.uuid4(),
            provider="kraken_spot", environment="production", product=product_id, side="SELL",
            claim_status="COMPLETED", claimed_at=datetime.now(timezone.utc), claim_owner="test",
            live_order_id=order.live_crypto_order_id,
        )
        session.add(claim)

        reconciliation = LiveReconciliationEvent(
            idempotency_key="recon-sell-1", event_hash="hash-sell-1", live_trading_profile_id=profile_id,
            live_crypto_order_id=order.live_crypto_order_id, capital_campaign_id=7,
            source_execution_event_id=uuid.uuid4(), source_execution_event_type="execution_intent_created",
            sequence_number=1, event_type="fill_reconciled", reconciliation_status="filled",
            provider_name="kraken_spot", provider_order_id=order.provider_order_id,
            event_payload={}, provenance={}, immutable_contract_version="1",
            recorded_at=datetime.now(timezone.utc),
        )
        session.add(reconciliation)

        recovery = ControlledProofExitRecovery(
            recovery_id=recovery_id, proof_id=proof.proof_id, status="BLOCKED",
            idempotency_key=f"idem-{uuid.uuid4()}", authorized_by="operator:alice",
            authorized_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            blocked_reason="stale_sell_package_replacement_blocked:Stale SELL package has unresolved execution lineage",
        )
        session.add(recovery)
        session.add(AuditLog(
            actor="system:controlled_proof_reconciliation_projector",
            action=exit_recovery._RECOVERED_OUTCOME_ACTION,
            entity_type="controlled_proof_exit_recovery", entity_id=recovery_id,
            before_state={}, after_state={
                "status": "COMPLETED_RECONCILED",
                "original_recovery_id": str(recovery_id),
                "proof_id": str(proof.proof_id),
                "sell_package_id": str(package_id),
                "sell_live_crypto_order_id": str(order_id),
                "recovered_terminal_verdict": "LIFECYCLE_PROVEN_LOSS",
                "recovered_net_pnl_usd": "-0.0393333016409",
            },
        ))
        await session.flush()

        await exit_recovery.refresh_exit_recovery_outcomes(db=session)

        refreshed_proof = await session.get(ControlledProofRun, proof.proof_id)
        assert refreshed_proof.sell_live_crypto_order_id == order_id
        assert refreshed_proof.net_pnl_usd == Decimal("-0.0393333016409")
        assert refreshed_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
        refreshed_recovery = await session.get(ControlledProofExitRecovery, recovery_id)
        assert refreshed_recovery.status == "BLOCKED"
        outcome_audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
            AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
        ))).all()
        assert len(outcome_audits) == 1

        # Replay: no-op.
        await exit_recovery.refresh_exit_recovery_outcomes(db=session)
        replayed_proof = await session.get(ControlledProofRun, proof.proof_id)
        assert replayed_proof.net_pnl_usd == Decimal("-0.0393333016409")
        assert replayed_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
        replayed_audits = (await session.scalars(select(AuditLog).where(
            AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
            AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
        ))).all()
        assert len(replayed_audits) == 1


@pytest.mark.asyncio
async def test_sweep_projection_is_durably_committed_across_independent_sessions() -> None:
    """Production defect: exit_recovery_outcome_sweep_completed reported
    projected=2, but an independent read immediately afterward still
    showed net_pnl_usd=null/terminal_verdict=FAILED -- the projector only
    flushes (in-session visibility), and durability requires the caller's
    own commit. This proves the real sweep entry point, followed by the
    same commit call run_orchestration_cycle performs, is durably visible
    to a completely separate, freshly opened AsyncSession -- not just the
    session that ran it."""
    campaign_id = uuid.uuid4()
    package_id, order_id, recovery_id, proof_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    tables = [
        Asset.__table__, AuditLog.__table__, AutonomousExecutionClaim.__table__, Candle.__table__,
        CanonicalPreviewPackage.__table__, ControlledProofExitRecovery.__table__, ControlledProofRun.__table__,
        LiveAccountingRecord.__table__, LiveCryptoOrder.__table__, LiveReconciliationEvent.__table__,
        LiveTradingProfile.__table__,
    ]

    async with real_sqlite_session_factory(tables) as session_factory:
        async with session_factory() as write_session:
            write_session.add(ControlledProofRun(
                proof_id=proof_id, status="EXPIRED", provider="kraken_spot", environment="production",
                campaign_id=campaign_id, campaign_version=1, product_id="BTC-USD",
                max_notional_usd=Decimal("5"), idempotency_key=f"idem-{uuid.uuid4()}", requested_by="operator:alice",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                package_id=uuid.uuid4(), sell_package_id=package_id, sell_live_crypto_order_id=order_id,
                net_pnl_usd=None, terminal_verdict="FAILED",
            ))
            write_session.add(ControlledProofExitRecovery(
                recovery_id=recovery_id, proof_id=proof_id, status="BLOCKED",
                idempotency_key=f"idem-{uuid.uuid4()}", authorized_by="operator:alice",
                authorized_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
                blocked_reason="stale_sell_package_replacement_blocked:Stale SELL package has unresolved execution lineage",
            ))
            write_session.add(AuditLog(
                actor="system:controlled_proof_reconciliation_projector",
                action=exit_recovery._RECOVERED_OUTCOME_ACTION,
                entity_type="controlled_proof_exit_recovery", entity_id=recovery_id,
                before_state={}, after_state={
                    "status": "COMPLETED_RECONCILED",
                    "original_recovery_id": str(recovery_id),
                    "proof_id": str(proof_id),
                    "sell_package_id": str(package_id),
                    "sell_live_crypto_order_id": str(order_id),
                    "recovered_terminal_verdict": "LIFECYCLE_PROVEN_LOSS",
                    "recovered_net_pnl_usd": "-0.0393333016409",
                },
            ))
            await write_session.commit()

            # The real worker-level sweep entry point, followed by the
            # exact same commit call run_orchestration_cycle's guarded
            # hook performs -- commit ownership belongs to the caller.
            result = await exit_recovery.refresh_exit_recovery_outcomes(db=write_session)
            await write_session.commit()
            assert result.projected == 1
            assert result.failed == 0

        # Close that session entirely; open a completely new, independent
        # one from the same underlying database and reload.
        async with session_factory() as read_session:
            reloaded_proof = await read_session.get(ControlledProofRun, proof_id)
            assert reloaded_proof.net_pnl_usd == Decimal("-0.0393333016")  # sqlite NUMERIC storage precision, not app logic
            assert reloaded_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
            reloaded_recovery = await read_session.get(ControlledProofExitRecovery, recovery_id)
            assert reloaded_recovery.status == "BLOCKED"
            outcome_audits = (await read_session.scalars(select(AuditLog).where(
                AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
                AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
            ))).all()
            assert len(outcome_audits) == 1

        # Replay through yet another new session: no additional transition.
        async with session_factory() as replay_session:
            replay_result = await exit_recovery.refresh_exit_recovery_outcomes(db=replay_session)
            await replay_session.commit()
            assert replay_result.projected == 0
            assert replay_result.skipped == 1

        async with session_factory() as final_session:
            final_proof = await final_session.get(ControlledProofRun, proof_id)
            assert final_proof.net_pnl_usd == Decimal("-0.0393333016")  # sqlite NUMERIC storage precision, not app logic
            assert final_proof.terminal_verdict == "LIFECYCLE_PROVEN_LOSS"
            outcome_audits = (await final_session.scalars(select(AuditLog).where(
                AuditLog.entity_type == "controlled_proof_exit_recovery", AuditLog.entity_id == recovery_id,
                AuditLog.action == exit_recovery._RECOVERED_OUTCOME_ACTION,
            ))).all()
            assert len(outcome_audits) == 1
