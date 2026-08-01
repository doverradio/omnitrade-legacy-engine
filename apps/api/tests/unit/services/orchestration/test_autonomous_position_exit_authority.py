from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.services.orchestration import autonomous_position_exit_authority as subject


class _DB:
    def __init__(self, values=()): self.values=list(values); self.added=[]; self.flushes=0
    async def scalar(self, _statement): return self.values.pop(0)
    def add(self, value): self.added.append(value)
    async def flush(self):
        self.flushes += 1
        for value in self.added:
            if isinstance(value, AutonomousPositionExitAuthority) and value.authority_id is None:
                value.authority_id = uuid.uuid4()


def _custody(*, now, proof_eligible=True, disposition="EXIT_RECOMMENDED", quantity="0.00008"):
    evaluation = {
        "custody_id": None, "evaluated_at": now.isoformat(), "disposition": disposition,
        "authoritative_remaining_quantity": quantity, "price_fresh": True,
        "profitable_exit": True, "mandatory_safety_exit": False, "reason_codes": [],
        "policy_id": "crypto-default", "policy_version": "1",
        "minimum_net_profit_to_exit": "2", "dust_threshold": "5", "policy_conflicts": [],
        "campaign_status": "EXPIRED", "mandate_status": "EXPIRED",
    }
    row = SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE", live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(), provider="kraken_spot",
        environment="production", product="BTC-USD", buy_claim_id=uuid.uuid4(),
        buy_reconciliation_event_id=uuid.uuid4(), provenance_classification="SCHEDULED_PRODUCTION_AUTONOMOUS",
        proof_eligible=proof_eligible, disqualification_reason=None if proof_eligible else "operator_assisted",
        active_sell_claim_id=None, active_sell_order_id=None, continuing_exit_authority_state="UNARMED",
        updated_at=now, audit_metadata={"latest_exit_evaluation": evaluation},
    )
    evaluation["custody_id"] = str(row.custody_id)
    return row


@pytest.mark.asyncio
async def test_fresh_exit_recommendation_arms_exactly_one_authority(monkeypatch):
    now = datetime.now(timezone.utc); custody = _custody(now=now)
    db = _DB([custody, None, None])
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    authority, blockers, replay = await subject.issue_exit_authority(db=db, custody_id=custody.custody_id, now=now)
    assert authority.authority_state == "ARMED" and blockers == () and replay is False
    assert authority.side == "SELL" and authority.exposure_effect == "REDUCE_ONLY"
    assert authority.maximum_sell_quantity == Decimal("0.00008")
    assert authority.buy_forbidden is True and authority.increased_exposure_forbidden is True
    assert custody.continuing_exit_authority_state == "ARMED"


@pytest.mark.asyncio
async def test_replay_returns_same_authority(monkeypatch):
    now = datetime.now(timezone.utc); custody = _custody(now=now)
    existing = SimpleNamespace(
        blockers=[], authority_state="ARMED",
        evaluation_integrity_hash=subject._digest(subject._evaluation(custody)),
    )
    db = _DB([custody, existing])
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    authority, _, replay = await subject.issue_exit_authority(db=db, custody_id=custody.custody_id, now=now)
    assert authority is existing and replay is True and db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["HOLD", "BLOCKED", "CLOSED_CANDIDATE"])
async def test_non_exit_dispositions_cannot_arm(monkeypatch, disposition):
    now = datetime.now(timezone.utc); custody = _custody(now=now, disposition=disposition)
    db = _DB([custody, None, None])
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    authority, blockers, _ = await subject.issue_exit_authority(db=db, custody_id=custody.custody_id, now=now)
    assert authority.authority_state == "BLOCKED"
    assert "evaluation_not_exit_recommended" in blockers


@pytest.mark.asyncio
async def test_stale_or_zero_quantity_cannot_arm(monkeypatch):
    now = datetime.now(timezone.utc); custody = _custody(now=now - timedelta(hours=1))
    db = _DB([custody, None, None])
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0")))
    authority, blockers, _ = await subject.issue_exit_authority(db=db, custody_id=custody.custody_id, now=now)
    assert authority is None
    assert {"evaluation_stale_or_invalid", "authoritative_quantity_changed_or_ambiguous"} <= set(blockers)


@pytest.mark.asyncio
async def test_disqualified_custody_gets_nonqualifying_protective_authority(monkeypatch):
    now = datetime.now(timezone.utc); custody = _custody(now=now, proof_eligible=False)
    custody.audit_metadata["latest_exit_evaluation"]["profitable_exit"] = False
    custody.audit_metadata["latest_exit_evaluation"]["mandatory_safety_exit"] = True
    db = _DB([custody, None, None])
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    authority, _, _ = await subject.issue_exit_authority(db=db, custody_id=custody.custody_id, now=now)
    assert authority.classification == "NONQUALIFYING_PROTECTIVE_EXIT"
    assert authority.proof_eligible is False and custody.custody_state == "ACTIVE"


@pytest.mark.asyncio
async def test_reservation_rejects_buy_excess_quantity_and_cross_scope():
    now = datetime.now(timezone.utc); custody = _custody(now=now)
    authority = SimpleNamespace(
        authority_state="ARMED", expires_at=now + timedelta(minutes=5), maximum_sell_quantity=Decimal("0.00008"),
        authority_id=uuid.uuid4(),
        custody_id=custody.custody_id, live_trading_profile_id=custody.live_trading_profile_id,
        paper_account_id=custody.paper_account_id, exchange_connection_id=custody.exchange_connection_id,
        provider=custody.provider, environment=custody.environment, product=custody.product,
        reserved_at=None, reservation_expires_at=None, updated_at=None,
    )
    common = dict(db=_DB([authority, custody]), authority_id=uuid.uuid4(), quantity=Decimal("0.00008"),
                  custody_id=custody.custody_id, profile_id=custody.live_trading_profile_id,
                  account_id=custody.paper_account_id, connection_id=custody.exchange_connection_id,
                  provider=custody.provider, environment=custody.environment, product=custody.product, now=now)
    with pytest.raises(InvalidRequestError, match="only bounded SELL"):
        await subject.reserve_exit_authority(side="BUY", **common)
    common["db"] = _DB([authority, custody]); common["quantity"] = Decimal("0.00009")
    with pytest.raises(InvalidRequestError, match="only bounded SELL"):
        await subject.reserve_exit_authority(side="SELL", **common)
    common["db"] = _DB([authority, custody]); common["quantity"] = Decimal("0.00008"); common["product"] = "ETH-USD"
    with pytest.raises(InvalidRequestError, match="scope mismatch"):
        await subject.reserve_exit_authority(side="SELL", **common)
    common["db"] = _DB([authority, custody]); common["product"] = custody.product
    reserved = await subject.reserve_exit_authority(side="SELL", **common)
    assert reserved.authority_state == "RESERVED"
    assert custody.continuing_exit_authority_state == "RESERVED"


@pytest.mark.asyncio
async def test_expired_reservation_recovers_but_quantity_reduction_revokes(monkeypatch):
    now = datetime.now(timezone.utc); custody = _custody(now=now)
    authority = SimpleNamespace(
        authority_id=uuid.uuid4(), authority_state="RESERVED", expires_at=now + timedelta(minutes=5),
        reservation_expires_at=now - timedelta(seconds=1), reserved_at=now - timedelta(minutes=5),
        custody_id=custody.custody_id, maximum_sell_quantity=Decimal("0.00008"),
        proof_eligible=True, evaluation_integrity_hash=subject._digest(subject._evaluation(custody)),
        expired_at=None, revoked_at=None, consumed_at=None, superseded_at=None, updated_at=None,
    )
    db = _DB([custody])
    db.scalars = lambda _statement: _async(SimpleNamespace(all=lambda: [authority]))
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    assert await subject.revalidate_active_exit_authorities(db=db, now=now) == 1
    assert authority.authority_state == "ARMED"

    authority.authority_state = "ARMED"; db.values = [custody]
    db.scalars = lambda _statement: _async(SimpleNamespace(all=lambda: [authority]))
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00004")))
    assert await subject.revalidate_active_exit_authorities(db=db, now=now) == 1
    assert authority.authority_state == "REVOKED"


@pytest.mark.asyncio
async def test_authority_audit_failure_propagates_before_scheduler_commit(monkeypatch):
    now = datetime.now(timezone.utc); custody = _custody(now=now)
    db = _DB([custody, None, None])
    original_flush = db.flush
    async def fail_second_flush():
        if db.flushes == 1:
            raise RuntimeError("authority audit persistence failed")
        await original_flush()
    db.flush = fail_second_flush
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    with pytest.raises(RuntimeError, match="authority audit persistence failed"):
        await subject.issue_exit_authority(db=db, custody_id=custody.custody_id, now=now)


async def _async(value): return value
