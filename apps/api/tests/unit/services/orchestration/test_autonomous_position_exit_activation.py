from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.models.autonomous_execution_claim import AutonomousExecutionClaim
from app.models.canonical_proving_activation import CanonicalProvingActivation
from app.services.orchestration import autonomous_position_exit_activation as subject


class _Db:
    def __init__(self, scalars, gets, discovered=()):
        self._scalar_queue = list(scalars); self.gets = gets; self.added = []; self.flushes = 0
        self.discovered = list(discovered)

    async def scalar(self, _statement):
        return self._scalar_queue.pop(0)

    async def get(self, model, identity):
        return self.gets.get((model, identity))

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: self.discovered)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1

    @asynccontextmanager
    async def begin_nested(self):
        yield


def _rows(*, proceeds="7.20", proof_eligible=True):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc); quantity = Decimal("0.00008")
    ids = {name: uuid.uuid4() for name in (
        "authority", "custody", "decision", "package", "preview", "risk", "audit", "campaign",
        "account", "profile", "connection", "buy_claim", "reconciliation", "mandate", "mandate_version",
    )}
    evaluation = {
        "custody_id": str(ids["custody"]), "evaluated_at": now.isoformat(),
        "disposition": "EXIT_RECOMMENDED", "price_fresh": True,
        "authoritative_remaining_quantity": format(quantity, "f"),
    }
    classification = "PROOF_ELIGIBLE_AUTONOMOUS" if proof_eligible else "NONQUALIFYING_PROTECTIVE_EXIT"
    custody = SimpleNamespace(
        custody_id=ids["custody"], custody_state="EXIT_PENDING", terminal_at=None,
        latest_exit_evaluation_at=now, observed_remaining_quantity=quantity,
        audit_metadata={"latest_exit_evaluation": evaluation},
        live_trading_profile_id=ids["profile"], paper_account_id=ids["account"],
        exchange_connection_id=ids["connection"], provider="kraken_spot", environment="production",
        product="BTC-USD", buy_claim_id=ids["buy_claim"],
        buy_reconciliation_event_id=ids["reconciliation"], buy_package_id=uuid.uuid4(),
        mandate_id=ids["mandate"], mandate_version_id=ids["mandate_version"],
        proof_eligible=proof_eligible,
        disqualification_reason=None if proof_eligible else "historical_lineage_disqualified",
        active_sell_claim_id=None, updated_at=now,
    )
    authority = SimpleNamespace(
        authority_id=ids["authority"], authority_version=1, authority_state="RESERVED",
        reserved_decision_id=ids["decision"], reserved_package_id=ids["package"],
        reserved_activation_id=None, reserved_claim_id=None, custody_id=ids["custody"],
        expires_at=now + timedelta(minutes=10), reservation_expires_at=now + timedelta(minutes=5),
        revoked_at=None, consumed_at=None,
        side="SELL", exposure_effect="REDUCE_ONLY", buy_forbidden=True,
        increased_exposure_forbidden=True, maximum_sell_quantity=quantity,
        evaluation_integrity_hash=subject._digest(evaluation),
        live_trading_profile_id=ids["profile"], paper_account_id=ids["account"],
        exchange_connection_id=ids["connection"], provider="kraken_spot", environment="production",
        product="BTC-USD", originating_buy_claim_id=ids["buy_claim"],
        originating_reconciliation_event_id=ids["reconciliation"],
        proof_eligible=proof_eligible, classification=classification,
        policy_evidence={"minimum_net_profit_to_exit": "2"}, risk_evidence={"verdict": "approved"},
        updated_at=now,
    )
    package = SimpleNamespace(
        package_id=ids["package"], package_state="READY", superseded_at=None,
        preview_expires_at=now + timedelta(minutes=4), side="SELL", proposed_base_quantity=quantity,
        maximum_authorized_base_quantity=quantity, expected_quote_proceeds=Decimal(proceeds),
        capital_deployment_amount=Decimal("0"), crypto_order_preview_id=ids["preview"],
        decision_record_id=ids["decision"], risk_event_id=ids["risk"],
        campaign_id=ids["campaign"], campaign_version=1,
    )
    details = {
        "custody_id": str(ids["custody"]), "exit_authority_id": str(ids["authority"]),
        "live_trading_profile_id": str(ids["profile"]), "paper_account_id": str(ids["account"]),
        "exchange_connection_id": str(ids["connection"]), "provider": "kraken_spot",
        "environment": "production", "product": "BTC-USD", "side": "SELL",
        "exposure_effect": "REDUCE_ONLY",
        "construction_time_economics": {"estimated_gross_quote_proceeds": proceeds},
    }
    decision = SimpleNamespace(decision_id=ids["decision"], execution_details=details)
    preview = SimpleNamespace(
        crypto_order_preview_id=ids["preview"], decision_record_id=ids["decision"],
        side="SELL", status="PREVIEW_READY", risk_verdict="approved_for_preview",
        base_size=quantity, estimated_quote_size=Decimal(proceeds), audit_correlation_id=ids["audit"],
    )
    mandate = SimpleNamespace(mandate_id=ids["mandate"])
    version = SimpleNamespace(mandate_version_id=ids["mandate_version"], mandate_id=ids["mandate"])
    buy_claim = SimpleNamespace(
        claim_id=ids["buy_claim"], profile_id=ids["profile"], account_id=ids["account"],
        connection_id=ids["connection"], provider="kraken_spot", environment="production", product="BTC-USD",
    )
    gets = {
        (subject.DecisionRecord, ids["decision"]): decision,
        (subject.CryptoOrderPreview, ids["preview"]): preview,
        (subject.RiskEvent, ids["risk"]): SimpleNamespace(id=ids["risk"]),
        (subject.LiveTradingProfile, ids["profile"]): SimpleNamespace(paper_account_id=ids["account"]),
        (subject.PaperAccount, ids["account"]): SimpleNamespace(id=ids["account"]),
        (subject.ExchangeConnection, ids["connection"]): SimpleNamespace(provider="kraken_spot", environment="production"),
        (subject.AutonomousExecutionClaim, ids["buy_claim"]): buy_claim,
        (subject.LiveReconciliationEvent, ids["reconciliation"]): SimpleNamespace(id=ids["reconciliation"]),
        (subject.AutonomousCapitalMandate, ids["mandate"]): mandate,
        (subject.AutonomousCapitalMandateVersion, ids["mandate_version"]): version,
    }
    return now, quantity, authority, custody, package, gets


async def _owned(value): return value


@pytest.mark.asyncio
@pytest.mark.parametrize("proceeds", ["4.80", "7.20"])
async def test_valid_reduce_only_package_atomically_creates_one_activation_and_claim(monkeypatch, proceeds):
    now, quantity, authority, custody, package, gets = _rows(proceeds=proceeds)
    db = _Db([authority, custody, package, None, None, None, None], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _owned(quantity))

    result = await subject.activate_exit_package_and_claim(db=db, authority_id=authority.authority_id, now=now)

    activation = next(row for row in db.added if isinstance(row, CanonicalProvingActivation))
    claim = next(row for row in db.added if isinstance(row, AutonomousExecutionClaim))
    assert result.claim_id == claim.claim_id and result.activation_id == activation.activation_id
    assert activation.authority_source == "CONTINUING_EXIT" and activation.dry_run_live_crypto_order_id is None
    assert activation.max_deployed_capital == claim.capital_deployment_amount == Decimal("0")
    assert claim.side == "SELL" and claim.exposure_effect == "REDUCE_ONLY"
    assert claim.claimed_base_quantity == claim.maximum_authorized_base_quantity == quantity
    assert claim.expected_quote_proceeds == Decimal(proceeds)
    assert claim.live_order_id is None and custody.active_sell_claim_id == claim.claim_id
    assert authority.authority_state == "RESERVED"
    assert authority.reserved_activation_id == activation.activation_id
    assert authority.reserved_claim_id == claim.claim_id
    assert package.package_state == "ACTIVATED"


@pytest.mark.asyncio
async def test_exact_replay_returns_bound_activation_and_claim_without_new_rows(monkeypatch):
    now, quantity, authority, custody, package, gets = _rows()
    activation = SimpleNamespace(activation_id=uuid.uuid4())
    claim = SimpleNamespace(claim_id=uuid.uuid4(), activation_id=activation.activation_id,
                            exit_authority_id=authority.authority_id, package_id=package.package_id,
                            claimed_base_quantity=quantity, expected_quote_proceeds=Decimal("7.20"),
                            claim_status="CLAIMED", expires_at=now + timedelta(minutes=2))
    authority.reserved_activation_id = activation.activation_id; authority.reserved_claim_id = claim.claim_id
    gets[(AutonomousExecutionClaim, claim.claim_id)] = claim
    gets[(CanonicalProvingActivation, activation.activation_id)] = activation
    gets[(subject.AutonomousPositionCustody, custody.custody_id)] = custody
    db = _Db([authority], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _owned(quantity))
    result = await subject.activate_exit_package_and_claim(db=db, authority_id=authority.authority_id, now=now)
    assert result.idempotent is True and result.claim_id == claim.claim_id and db.added == []


@pytest.mark.asyncio
async def test_replay_rejects_quantity_change(monkeypatch):
    now, quantity, authority, custody, package, gets = _rows()
    activation = SimpleNamespace(activation_id=uuid.uuid4())
    claim = SimpleNamespace(
        claim_id=uuid.uuid4(), activation_id=activation.activation_id,
        exit_authority_id=authority.authority_id, package_id=package.package_id,
        claimed_base_quantity=quantity, expected_quote_proceeds=Decimal("7.20"),
        claim_status="CLAIMED", expires_at=now + timedelta(minutes=2),
    )
    authority.reserved_activation_id = activation.activation_id; authority.reserved_claim_id = claim.claim_id
    gets[(AutonomousExecutionClaim, claim.claim_id)] = claim
    gets[(CanonicalProvingActivation, activation.activation_id)] = activation
    gets[(subject.AutonomousPositionCustody, custody.custody_id)] = custody
    db = _Db([authority], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _owned(quantity / 2))
    with pytest.raises(InvalidRequestError, match="quantity-invalid"):
        await subject.activate_exit_package_and_claim(db=db, authority_id=authority.authority_id, now=now)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation,error", [
    (lambda a, c, p: setattr(a, "authority_state", "REVOKED"), "completely RESERVED"),
    (lambda a, c, p: setattr(a, "exposure_effect", "INCREASE"), "reduce-only SELL"),
    (lambda a, c, p: setattr(p, "side", "BUY"), "zero-capital SELL"),
    (lambda a, c, p: setattr(p, "proposed_base_quantity", Decimal("0.00009")), "changed, ambiguous, or excessive"),
    (lambda a, c, p: setattr(a, "product", "ETH-USD"), "scope mismatch"),
    (lambda a, c, p: setattr(p, "package_state", "SUPERSEDED"), "stale, superseded, or not READY"),
])
async def test_invalid_authority_quantity_side_and_scope_fail_closed(monkeypatch, mutation, error):
    now, quantity, authority, custody, package, gets = _rows(); mutation(authority, custody, package)
    db = _Db([authority, custody, package], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _owned(quantity))
    with pytest.raises(InvalidRequestError, match=error):
        await subject.activate_exit_package_and_claim(db=db, authority_id=authority.authority_id, now=now)
    assert not any(isinstance(row, (CanonicalProvingActivation, AutonomousExecutionClaim)) for row in db.added)


@pytest.mark.asyncio
async def test_proof_disqualified_protective_claim_remains_nonqualifying(monkeypatch):
    now, quantity, authority, custody, package, gets = _rows(proof_eligible=False)
    db = _Db([authority, custody, package, None, None, None, None], gets)
    monkeypatch.setattr(subject, "compute_signed_owned_quantity", lambda **_kw: _owned(quantity))
    await subject.activate_exit_package_and_claim(db=db, authority_id=authority.authority_id, now=now)
    claim = next(row for row in db.added if isinstance(row, AutonomousExecutionClaim))
    assert claim.proof_eligible is False
    assert claim.disqualification_reason == "historical_lineage_disqualified"
    assert claim.authority_evidence["automatic_proof_sell_ready"] is False


@pytest.mark.asyncio
async def test_poll_records_failure_and_continues_to_later_authority(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()
    failed_row = SimpleNamespace(
        authority_id=first, authority_state="RESERVED",
        last_activation_failure_at=None, last_activation_failure_code=None,
        last_activation_exception_class=None, last_activation_failure_retryable=None,
    )
    db = _Db([], {(subject.AutonomousPositionExitAuthority, first): failed_row}, discovered=[first, second])

    async def activate_one(*, authority_id, **_kwargs):
        if authority_id == first:
            raise InvalidRequestError(message="Canonical SELL package is stale, superseded, or not READY")
        return SimpleNamespace()

    monkeypatch.setattr(subject, "activate_exit_package_and_claim", activate_one)
    result = await subject.activate_due_exit_claims(db=db, now=datetime.now(timezone.utc))
    assert (result.discovered, result.activated, result.failed) == (2, 1, 1)
    assert failed_row.last_activation_failure_retryable is True
    assert failed_row.last_activation_failure_code == "canonical_sell_package_is_stale,_superseded,_or_not_ready"
