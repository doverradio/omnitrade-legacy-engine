from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from contextlib import asynccontextmanager

from app.core.errors import InvalidRequestError
from app.models.autonomous_position_exit_authority import AutonomousPositionExitAuthority
from app.models.decision_record import DecisionRecord
from app.models.live_trading_profile import LiveTradingProfile
from app.models.canonical_preview_package import CanonicalPreviewPackage
from app.services.orchestration import autonomous_position_exit_construction as subject
from app.services import canonical_preview_package as canonical_packages


class _DB:
    def __init__(self, values):
        self.values = list(values); self.added = []; self.package = None; self.profile = None; self.authorities = {}
    async def scalar(self, _statement): return self.values.pop(0)
    def add(self, value): self.added.append(value)
    async def flush(self):
        for value in self.added:
            if isinstance(value, DecisionRecord) and value.decision_id is None:
                value.decision_id = uuid.uuid4()
    async def get(self, model, identity):
        if model is LiveTradingProfile: return self.profile
        if model is CanonicalPreviewPackage: return self.package
        if model is AutonomousPositionExitAuthority: return self.authorities.get(identity)
        return None
    @asynccontextmanager
    async def begin_nested(self): yield


def _rows(*, state="ARMED", disposition="EXIT_RECOMMENDED", proof_eligible=True):
    now = datetime.now(timezone.utc); custody_id = uuid.uuid4(); quantity = Decimal("0.00008")
    evaluation = {
        "custody_id": str(custody_id), "evaluated_at": now.isoformat(), "disposition": disposition,
        "authoritative_remaining_quantity": format(quantity, "f"), "price_fresh": True,
        "price": "60000", "price_observed_at": now.isoformat(), "estimated_current_proceeds": "4.8",
        "estimated_exit_fee": "0.01", "estimated_slippage": "0.01", "cost_basis": "4.5",
        "paid_costs": "0.01", "estimated_net_exit_result": "0.28", "profitable_exit": True,
        "mandatory_safety_exit": False, "stop_loss_triggered": False, "maximum_hold_exceeded": False,
        "dust": True, "policy_conflicts": [], "campaign_status": "EXPIRED", "mandate_status": "EXPIRED",
    }
    custody = SimpleNamespace(
        custody_id=custody_id, custody_state="ACTIVE", live_trading_profile_id=uuid.uuid4(),
        paper_account_id=uuid.uuid4(), exchange_connection_id=uuid.uuid4(), provider="kraken_spot",
        environment="production", product="BTC-USD", buy_claim_id=uuid.uuid4(),
        buy_reconciliation_event_id=uuid.uuid4(), proof_eligible=proof_eligible,
        campaign_id=uuid.uuid4(), campaign_version=1, runtime_campaign_id=uuid.uuid4(),
        mandate_id=uuid.uuid4(), mandate_version_id=uuid.uuid4(), decision_record_id=uuid.uuid4(),
        provenance_classification="SCHEDULED_PRODUCTION_AUTONOMOUS", audit_metadata={"latest_exit_evaluation": evaluation},
        active_sell_decision_id=None, active_sell_package_id=None, active_sell_claim_id=None,
        active_sell_order_id=None, continuing_exit_authority_state="ARMED", updated_at=now,
    )
    authority = SimpleNamespace(
        authority_id=uuid.uuid4(), authority_version=1, authority_state=state, custody_id=custody_id,
        live_trading_profile_id=custody.live_trading_profile_id, paper_account_id=custody.paper_account_id,
        exchange_connection_id=custody.exchange_connection_id, provider=custody.provider,
        environment=custody.environment, product=custody.product,
        originating_buy_claim_id=custody.buy_claim_id,
        originating_reconciliation_event_id=custody.buy_reconciliation_event_id,
        proof_eligible=proof_eligible,
        classification="PROOF_ELIGIBLE_AUTONOMOUS" if proof_eligible else "NONQUALIFYING_PROTECTIVE_EXIT",
        evaluation_integrity_hash=subject._digest(evaluation), maximum_sell_quantity=quantity,
        side="SELL", exposure_effect="REDUCE_ONLY", policy_evidence={"policy_id": "crypto-default"},
        risk_evidence={"authority": "owned_position_reduction_only"}, blockers=[],
        expires_at=now + timedelta(minutes=10), reserved_decision_id=None, reserved_package_id=None,
        reserved_at=None, reservation_expires_at=None, updated_at=now,
    )
    original = SimpleNamespace(generated_signals=[{"strategy_identity": "btc_momentum@1"}])
    return now, quantity, authority, custody, original


def _preview(*, quantity=Decimal("0.00008"), quote="4.8", fee="0.01"):
    return SimpleNamespace(
        crypto_order_preview_id=uuid.uuid4(), risk_event_id=uuid.uuid4(), audit_correlation_id=uuid.uuid4(),
        status="PREVIEW_READY", side="SELL", risk_verdict="approved_for_preview",
        base_size=quantity, estimated_base_size=quantity, estimated_quote_size=Decimal(quote),
        estimated_average_price=Decimal(quote) / quantity, estimated_fee=Decimal(fee),
        estimated_slippage=Decimal("0.001"), created_at=datetime.now(timezone.utc), decision_record_id=None,
    )


async def _no_violations(**_kwargs): return []


@pytest.mark.asyncio
async def test_fresh_authority_constructs_one_canonical_sell_decision_and_ready_package(monkeypatch):
    now, quantity, authority, custody, original = _rows()
    db = _DB([authority, custody, None, None, None, original]); captured = {}
    db.profile = SimpleNamespace(paper_account_id=custody.paper_account_id)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    async def builder(*, db, request):
        captured["request"] = request; package_id = uuid.uuid4()
        db.package = SimpleNamespace(package_id=package_id, decision_record_id=request.expected_decision_record_id)
        return {"package": {"package_id": str(package_id), "package_state": "READY", "side": "SELL"}}
    async def preview_builder(**_kwargs): return _preview(quantity=quantity)
    result = await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id,
                                                     now=now, package_builder=builder,
                                                     preview_builder=preview_builder, linkage_guard=_no_violations)
    request = captured["request"]
    assert result.quantity == quantity and result.idempotent is False
    assert request.commissioning_entry_mode == "autonomous_position_exit"
    assert request.forced_action == "CLOSE_POSITION_PROPOSED"
    assert request.autonomous_exit_authority_id == authority.authority_id
    assert request.autonomous_exit_proof_eligible is True
    decision = next(item for item in db.added if isinstance(item, DecisionRecord))
    assert decision.generated_signals[0]["action"] == "SELL"
    assert decision.execution_details["exposure_effect"] == "REDUCE_ONLY"
    assert decision.execution_details["evaluation_time_economics"]["estimated_net_exit_result"] == "0.28"
    assert decision.execution_details["construction_time_economics"]["estimated_net_exit_result"] == "0.28"
    assert decision.execution_details["preview_id"]
    assert authority.authority_state == custody.continuing_exit_authority_state == "RESERVED"
    assert authority.reserved_decision_id == custody.active_sell_decision_id == decision.decision_id
    assert authority.reserved_package_id == custody.active_sell_package_id == result.package_id
    assert not any(type(item).__name__ in {"CanonicalProvingActivation", "AutonomousExecutionClaim", "LiveCryptoOrder"} for item in db.added)


@pytest.mark.asyncio
async def test_exact_reserved_replay_returns_same_paperwork_without_builder():
    _now, quantity, authority, _custody, _original = _rows(state="RESERVED")
    authority.reserved_decision_id = uuid.uuid4(); authority.reserved_package_id = uuid.uuid4()
    async def forbidden(**_kw): raise AssertionError("builder must not run")
    result = await subject.construct_exit_paperwork(db=_DB([authority]), authority_id=authority.authority_id,
                                                     package_builder=forbidden)
    assert result.idempotent is True and result.quantity == quantity
    assert result.decision_id == authority.reserved_decision_id and result.package_id == authority.reserved_package_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["REVOKED", "EXPIRED", "CONSUMED", "BLOCKED"])
async def test_nonarmed_authority_cannot_construct(state):
    now, _quantity, authority, _custody, _original = _rows(state=state)
    with pytest.raises(InvalidRequestError, match="fresh and ARMED"):
        await subject.construct_exit_paperwork(db=_DB([authority]), authority_id=authority.authority_id, now=now)


@pytest.mark.asyncio
async def test_package_failure_leaves_authority_and_custody_unreserved(monkeypatch):
    now, quantity, authority, custody, original = _rows(); db = _DB([authority, custody, None, None, None, original])
    db.profile = SimpleNamespace(paper_account_id=custody.paper_account_id)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    async def fail(**_kw): raise RuntimeError("canonical economics rejected")
    async def preview_builder(**_kwargs): return _preview(quantity=quantity)
    with pytest.raises(RuntimeError, match="economics rejected"):
        await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id, now=now,
                                               package_builder=fail, preview_builder=preview_builder,
                                               linkage_guard=_no_violations)
    assert authority.authority_state == "ARMED" and custody.active_sell_package_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["package", "claim", "order"])
async def test_unresolved_sell_lifecycle_prevents_duplicate_construction(existing):
    now, _quantity, authority, custody, _original = _rows()
    unresolved = [None, None, None]
    unresolved[{"package": 0, "claim": 1, "order": 2}[existing]] = uuid.uuid4()
    with pytest.raises(InvalidRequestError, match="Unresolved SELL package"):
        await subject.construct_exit_paperwork(
            db=_DB([authority, custody, *unresolved]), authority_id=authority.authority_id, now=now,
        )


@pytest.mark.asyncio
async def test_quantity_and_every_authority_scope_are_rechecked(monkeypatch):
    now, quantity, authority, custody, original = _rows(); db = _DB([authority, custody, None, None, None, original])
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity + Decimal("0.1")))
    with pytest.raises(InvalidRequestError, match="changed, or excessive"):
        await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id, now=now)
    now, quantity, authority, custody, original = _rows(); authority.product = "ETH-USD"
    db = _DB([authority, custody, None, None, None]); monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    with pytest.raises(InvalidRequestError, match="scope or classification mismatch"):
        await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id, now=now)


@pytest.mark.asyncio
async def test_proof_disqualified_exit_stays_nonqualifying(monkeypatch):
    now, quantity, authority, custody, original = _rows(proof_eligible=False)
    custody.audit_metadata["latest_exit_evaluation"]["mandatory_safety_exit"] = True
    authority.evaluation_integrity_hash = subject._digest(custody.audit_metadata["latest_exit_evaluation"])
    db = _DB([authority, custody, None, None, None, original]); monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    db.profile = SimpleNamespace(paper_account_id=custody.paper_account_id)
    async def builder(*, db, request):
        assert request.autonomous_exit_proof_eligible is False
        assert request.autonomous_exit_classification == "NONQUALIFYING_PROTECTIVE_EXIT"
        package_id=uuid.uuid4(); db.package=SimpleNamespace(package_id=package_id, decision_record_id=request.expected_decision_record_id)
        return {"package":{"package_id":str(package_id),"package_state":"READY","side":"SELL"}}
    async def preview_builder(**_kwargs): return _preview(quantity=quantity)
    await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id, now=now,
                                           package_builder=builder, preview_builder=preview_builder,
                                           linkage_guard=_no_violations)
    decision=next(item for item in db.added if isinstance(item, DecisionRecord))
    assert decision.execution_details["proof_eligible"] is False
    assert decision.execution_details["authority_classification"] == "NONQUALIFYING_PROTECTIVE_EXIT"


@pytest.mark.asyncio
@pytest.mark.parametrize("quote", ["4.20", "7.20"])
async def test_sell_notional_is_preview_derived_and_not_limited_by_buy_entry_cap(monkeypatch, quote):
    now, quantity, authority, custody, original = _rows(); db = _DB([authority, custody, None, None, None, original])
    custody.audit_metadata["latest_exit_evaluation"]["mandatory_safety_exit"] = True
    authority.evaluation_integrity_hash = subject._digest(custody.audit_metadata["latest_exit_evaluation"])
    db.profile = SimpleNamespace(paper_account_id=custody.paper_account_id)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    async def preview_builder(**_kwargs): return _preview(quantity=quantity, quote=quote)
    async def builder(*, db, request):
        package_id=uuid.uuid4(); db.package=SimpleNamespace(package_id=package_id, decision_record_id=request.expected_decision_record_id)
        return {"package":{"package_id":str(package_id),"package_state":"READY","side":"SELL"}}
    await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id, now=now,
                                           package_builder=builder, preview_builder=preview_builder,
                                           linkage_guard=_no_violations)
    decision = next(item for item in db.added if isinstance(item, DecisionRecord))
    assert decision.execution_details["construction_time_economics"]["estimated_gross_quote_proceeds"] == quote
    assert decision.execution_details["evaluation_time_economics"]["estimated_current_proceeds"] == "4.8"


def test_autonomous_sell_package_uses_preview_quote_notional_not_base_quantity_or_five_dollars():
    _now, quantity, authority, custody, _original = _rows()
    request = canonical_packages.CanonicalPreviewPackageCreateRequest(
        campaign_id=custody.campaign_id, campaign_version=1, paper_account_id=custody.paper_account_id,
        live_trading_profile_id=custody.live_trading_profile_id, provider=custody.provider,
        environment=custody.environment, product=custody.product, max_proposed_order_amount=Decimal("5"),
        actor="test", idempotency_key="exit", commissioning_entry_mode="autonomous_position_exit",
        expected_decision_record_id=uuid.uuid4(), forced_action="CLOSE_POSITION_PROPOSED",
        autonomous_exit_custody_id=custody.custody_id,
        autonomous_exit_evaluation_hash=authority.evaluation_integrity_hash,
        autonomous_exit_authority_id=authority.authority_id, autonomous_exit_authority_version=1,
        autonomous_exit_classification=authority.classification, autonomous_exit_proof_eligible=True,
        autonomous_exit_maximum_quantity=quantity,
    )
    preview = _preview(quantity=quantity, quote="4.73")
    assert canonical_packages._package_quote_notional(request=request, preview=preview) == Decimal("4.73")


@pytest.mark.asyncio
async def test_seven_dollar_sell_satisfies_existing_two_dollar_profit_rule(monkeypatch):
    now, quantity, authority, custody, original = _rows()
    authority.policy_evidence["minimum_net_profit_to_exit"] = "2"
    db = _DB([authority, custody, None, None, None, original]); db.profile = SimpleNamespace(paper_account_id=custody.paper_account_id)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    async def preview_builder(**_kwargs): return _preview(quantity=quantity, quote="7.20", fee="0.10")
    async def builder(*, db, request):
        package_id=uuid.uuid4(); db.package=SimpleNamespace(package_id=package_id, decision_record_id=request.expected_decision_record_id)
        return {"package":{"package_id":str(package_id),"package_state":"READY","side":"SELL"}}
    await subject.construct_exit_paperwork(db=db, authority_id=authority.authority_id, now=now,
                                           package_builder=builder, preview_builder=preview_builder,
                                           linkage_guard=_no_violations)
    decision = next(item for item in db.added if isinstance(item, DecisionRecord))
    economics = decision.execution_details["construction_time_economics"]
    assert economics["estimated_gross_quote_proceeds"] == "7.20"
    assert economics["estimated_net_exit_result"] == "2.59"
    assert decision.execution_details["exposure_effect"] == "REDUCE_ONLY"


@pytest.mark.asyncio
async def test_changed_construction_price_that_invalidates_profitable_exit_fails_closed(monkeypatch):
    now, quantity, authority, custody, original = _rows()
    authority.policy_evidence["minimum_net_profit_to_exit"] = "0.20"
    db = _DB([authority, custody, None, None, None, original])
    db.profile = SimpleNamespace(paper_account_id=custody.paper_account_id)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _async(quantity))
    async def preview_builder(**_kwargs): return _preview(quantity=quantity, quote="4.60", fee="0.01")
    with pytest.raises(InvalidRequestError, match="no longer authorize profitable exit"):
        await subject.construct_exit_paperwork(
            db=db, authority_id=authority.authority_id, now=now,
            preview_builder=preview_builder, linkage_guard=_no_violations,
        )
    assert authority.authority_state == "ARMED"
    assert custody.active_sell_decision_id is None and custody.active_sell_package_id is None


@pytest.mark.asyncio
async def test_poll_failure_is_diagnosable_and_does_not_suppress_later_position():
    now = datetime.now(timezone.utc); first_id = uuid.uuid4(); second_id = uuid.uuid4(); custody_id = uuid.uuid4()
    first = SimpleNamespace(
        authority_id=first_id, custody_id=custody_id, authority_state="ARMED",
        last_construction_failure_at=None, last_construction_failure_code=None,
        last_construction_exception_class=None, last_construction_failure_retryable=None,
    )
    db = _DB([]); db.authorities[first_id] = first
    db.scalars = lambda _statement: _async(SimpleNamespace(all=lambda: [first_id, second_id]))
    calls = []
    async def construct_one(*, db, authority_id, now):
        calls.append(authority_id)
        if authority_id == first_id:
            raise InvalidRequestError(message="Fresh construction economics no longer authorize profitable exit")
        return subject.ExitPaperworkResult(authority_id, uuid.uuid4(), uuid.uuid4(), Decimal("0.00008"), False)
    outcome = await subject.construct_due_exit_paperwork(db=db, now=now, construct_one=construct_one)
    assert calls == [first_id, second_id]
    assert (outcome.discovered, outcome.constructed, outcome.failed) == (2, 1, 1)
    assert first.authority_state == "ARMED"
    assert first.last_construction_failure_code == "construction_economics_rejected"
    assert first.last_construction_failure_retryable is True
    audit = next(item for item in db.added if item.action.endswith("construction_failed"))
    assert audit.entity_id == first_id
    assert audit.after_state == {
        "authority_id": str(first_id), "custody_id": str(custody_id),
        "exception_classification": "app.core.errors.InvalidRequestError",
        "reason_code": "construction_economics_rejected", "failed_at": now.isoformat(),
        "retryable": True,
    }


async def _async(value): return value
