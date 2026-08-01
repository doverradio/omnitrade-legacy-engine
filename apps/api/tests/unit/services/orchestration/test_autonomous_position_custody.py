from __future__ import annotations

import uuid
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.errors import InvalidRequestError
from app.models.autonomous_position_custody import AutonomousPositionCustody
from app.services.orchestration import autonomous_position_custody as custody
from app.services.orchestration.autonomous_execution_claims import release_execution_claim_scope_if_order_resolved


class _Rows:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _DB:
    def __init__(self, *, scalars=(), rows=()):
        self.scalar_values = list(scalars)
        self.row_values = list(rows)
        self.added = []
        self.flushes = 0

    async def scalar(self, _statement): return self.scalar_values.pop(0)
    async def scalars(self, _statement): return _Rows(self.row_values.pop(0))
    async def get(self, _model, _identity): return None
    def add(self, value): self.added.append(value)
    async def flush(self):
        self.flushes += 1
        for value in self.added:
            if isinstance(value, AutonomousPositionCustody) and value.custody_id is None:
                value.custody_id = uuid.uuid4()


def _lineage():
    now = datetime.now(timezone.utc)
    claim = SimpleNamespace(
        claim_id=uuid.uuid4(), package_id=uuid.uuid4(), activation_id=uuid.uuid4(),
        campaign_id=uuid.uuid4(), campaign_version=1, mandate_id=uuid.uuid4(),
        mandate_version_id=uuid.uuid4(), account_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        connection_id=uuid.uuid4(), provider="kraken_spot", environment="production",
        product="BTC-USD", side="BUY", live_order_id=uuid.uuid4(),
        claim_status="RECONCILIATION_REQUIRED", updated_at=now,
    )
    package = SimpleNamespace(
        package_id=claim.package_id, side="BUY", authorization_source="MANDATE",
        decision_record_id=uuid.uuid4(), campaign_id=claim.campaign_id,
        campaign_version=claim.campaign_version, runtime_campaign_id=uuid.uuid4(),
        mandate_version_id=claim.mandate_version_id, market_evidence_identity={},
    )
    mandate = SimpleNamespace(mandate_id=claim.mandate_id, purpose="PRODUCTION")
    origin = SimpleNamespace(cycle_id=uuid.uuid4())
    campaign_cycle = SimpleNamespace(
        cycle_id=uuid.uuid4(),
        cycle_context={"originating_autonomous_cycle_id": str(origin.cycle_id)},
    )
    reconciliation = SimpleNamespace(id=uuid.uuid4())
    return claim, package, mandate, origin, campaign_cycle, reconciliation


@pytest.mark.asyncio
async def test_eligible_scheduled_buy_establishes_one_active_custody(monkeypatch):
    claim, package, mandate, origin, campaign_cycle, reconciliation = _lineage()
    db = _DB(
        scalars=[None, package, mandate, None, origin, reconciliation],
        rows=[[campaign_cycle]],
    )
    monkeypatch.setattr(custody, "compute_buy_order_acquired_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))

    result = await custody.establish_buy_custody(db=db, claim=claim, observed_at=datetime.now(timezone.utc))

    assert result.custody_state == "ACTIVE"
    assert result.buy_claim_id == claim.claim_id
    assert result.original_acquired_quantity == Decimal("0.00008")
    assert result.provenance_classification == custody.SCHEDULED_PRODUCTION_PROVENANCE
    assert result.proof_eligible is True
    assert result.continuing_exit_authority_state == "UNARMED"
    assert sum(isinstance(item, AutonomousPositionCustody) for item in db.added) == 1


@pytest.mark.asyncio
async def test_reconciliation_replay_reuses_existing_custody():
    claim, *_ = _lineage()
    existing = SimpleNamespace(custody_id=uuid.uuid4())
    db = _DB(scalars=[existing])

    assert await custody.establish_buy_custody(
        db=db, claim=claim, observed_at=datetime.now(timezone.utc),
    ) is existing
    assert db.added == []


@pytest.mark.asyncio
async def test_controlled_proof_and_manual_authority_do_not_require_production_custody():
    claim, package, mandate, *_ = _lineage()
    controlled_db = _DB(scalars=[package, mandate, uuid.uuid4()])
    assert await custody.requires_production_custody_handoff(db=controlled_db, claim=claim) is False

    package.authorization_source = "HUMAN"
    manual_db = _DB(scalars=[package, mandate])
    assert await custody.requires_production_custody_handoff(db=manual_db, claim=claim) is False


@pytest.mark.asyncio
async def test_sell_reconciliation_retains_completed_behavior_without_custody(monkeypatch):
    claim, *_ = _lineage()
    claim.side = "SELL"
    db = _DB(scalars=[claim])

    called = False

    async def _unexpected(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(custody, "establish_buy_custody", _unexpected)
    await release_execution_claim_scope_if_order_resolved(
        db=db, live_crypto_order_id=claim.live_order_id, order_status="FILLED",
    )

    assert claim.claim_status == "COMPLETED"
    assert called is False


@pytest.mark.asyncio
async def test_positive_buy_cannot_terminalize_when_custody_persistence_fails(monkeypatch):
    claim, *_ = _lineage()
    db = _DB(scalars=[claim])
    monkeypatch.setattr(custody, "requires_production_custody_handoff", lambda **_kwargs: _async(True))

    async def _fail(**_kwargs):
        raise RuntimeError("custody persistence failed")

    monkeypatch.setattr(custody, "establish_buy_custody", _fail)
    with pytest.raises(RuntimeError, match="custody persistence failed"):
        await release_execution_claim_scope_if_order_resolved(
            db=db, live_crypto_order_id=claim.live_order_id, order_status="FILLED",
        )
    assert claim.claim_status == "RECONCILIATION_REQUIRED"


@pytest.mark.asyncio
async def test_audit_persistence_failure_cannot_terminalize_buy(monkeypatch):
    claim, *_ = _lineage()
    db = _DB(scalars=[claim])
    monkeypatch.setattr(custody, "requires_production_custody_handoff", lambda **_kwargs: _async(True))

    async def _custody_then_audit_failure(*, db, **_kwargs):
        db.add(SimpleNamespace(kind="custody"))
        await db.flush()
        db.add(SimpleNamespace(kind="custody_audit"))
        raise RuntimeError("custody audit flush failed")

    monkeypatch.setattr(custody, "establish_buy_custody", _custody_then_audit_failure)
    with pytest.raises(RuntimeError, match="custody audit flush failed"):
        await release_execution_claim_scope_if_order_resolved(
            db=db, live_crypto_order_id=claim.live_order_id, order_status="FILLED",
        )
    assert claim.claim_status == "RECONCILIATION_REQUIRED"
    assert claim.completed_at is None if hasattr(claim, "completed_at") else True


@pytest.mark.asyncio
async def test_restart_discovery_reconstructs_authoritative_remaining_quantity(monkeypatch):
    row = SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE", live_trading_profile_id=uuid.uuid4(),
        product="BTC-USD", original_acquired_quantity=Decimal("0.00008"),
        created_at=datetime.now(timezone.utc),
    )
    db = _DB(rows=[[row]])
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00005")))

    result = await custody.discover_nonterminal_custodies(db=db)

    assert result[0].custody_id == row.custody_id
    assert result[0].authoritative_remaining_quantity == Decimal("0.00005")
    assert result[0].blockers == ()
    assert result[0].sell_supervisor_connected is False


@pytest.mark.asyncio
async def test_campaign_expiration_does_not_hide_active_custody(monkeypatch):
    row = SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE", live_trading_profile_id=uuid.uuid4(),
        product="BTC-USD", original_acquired_quantity=Decimal("0.00008"),
        created_at=datetime.now(timezone.utc),
    )
    db = _DB(rows=[[row]])
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))

    result = await custody.discover_nonterminal_custodies(db=db)

    assert len(result) == 1
    assert result[0].custody_state == "ACTIVE"


@pytest.mark.asyncio
async def test_operator_recovery_disqualifies_proof_but_preserves_active_custody(monkeypatch):
    row = SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE", proof_eligible=True,
        disqualification_reason=None, disqualified_at=None, terminal_at=None, updated_at=None,
    )
    db = _DB(scalars=[row])

    result = await custody.permanently_disqualify_custody(
        db=db, custody_id=row.custody_id, reason="operator_exit_recovery", actor="operator:human",
    )

    assert result.custody_state == "ACTIVE"
    assert result.proof_eligible is False
    assert result.disqualification_reason == "operator_exit_recovery"
    assert result.disqualified_at is not None
    assert result.terminal_at is None

    db.row_values = [[row]]
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    discovered = await custody.discover_nonterminal_custodies(db=db)
    assert [item.custody_id for item in discovered] == [row.custody_id]


@pytest.mark.asyncio
async def test_proof_ineligible_custody_closes_only_when_authoritative_quantity_is_zero(monkeypatch):
    row = SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE", proof_eligible=False,
        live_trading_profile_id=uuid.uuid4(), product="BTC-USD",
        observed_remaining_quantity=Decimal("0.00008"), terminal_at=None, updated_at=None,
    )
    db = _DB(scalars=[row])
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    with pytest.raises(InvalidRequestError, match="while authoritative inventory remains"):
        await custody.close_custody_if_unowned(db=db, custody_id=row.custody_id, actor="system:test")
    assert row.custody_state == "ACTIVE"

    db.scalar_values = [row]
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0")))
    result = await custody.close_custody_if_unowned(db=db, custody_id=row.custody_id, actor="system:test")
    assert result.custody_state == "CLOSED"
    assert result.terminal_at is not None


@pytest.mark.asyncio
async def test_zero_quantity_buy_cannot_establish_cosmetic_custody(monkeypatch):
    claim, package, mandate, origin, campaign_cycle, reconciliation = _lineage()
    db = _DB(scalars=[None, package, mandate, None, origin, reconciliation], rows=[[campaign_cycle]])
    monkeypatch.setattr(custody, "compute_buy_order_acquired_quantity", lambda **_kwargs: _async(Decimal("0")))
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0")))

    with pytest.raises(InvalidRequestError, match="Positive authoritative BUY ownership"):
        await custody.establish_buy_custody(db=db, claim=claim, observed_at=datetime.now(timezone.utc))
    assert not any(isinstance(item, AutonomousPositionCustody) for item in db.added)


@pytest.mark.asyncio
async def test_unrelated_preexisting_inventory_fails_ownership_attribution(monkeypatch):
    claim, package, mandate, origin, campaign_cycle, reconciliation = _lineage()
    db = _DB(scalars=[None, package, mandate, None, origin, reconciliation], rows=[[campaign_cycle]])
    monkeypatch.setattr(custody, "compute_buy_order_acquired_quantity", lambda **_kwargs: _async(Decimal("0.00008")))
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00011")))

    with pytest.raises(InvalidRequestError, match="cannot be attributed exclusively"):
        await custody.establish_buy_custody(db=db, claim=claim, observed_at=datetime.now(timezone.utc))
    assert not any(isinstance(item, AutonomousPositionCustody) for item in db.added)


@pytest.mark.asyncio
async def test_positive_authoritative_quantity_without_custody_is_reported(monkeypatch):
    claim, *_ = _lineage()
    claim.claim_status = "BUY_RECONCILED"
    db = _DB(rows=[[claim]])
    monkeypatch.setattr(custody, "requires_production_custody_handoff", lambda **_kwargs: _async(True))
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))

    result = await custody.discover_uncustodied_reconciled_buys(db=db)

    assert result == [{
        "claim_id": str(claim.claim_id), "live_order_id": str(claim.live_order_id),
        "remaining_quantity": "0.00008", "reason": "positive_autonomous_ownership_without_custody",
    }]


@pytest.mark.asyncio
async def test_status_reports_custody_and_sell_supervision_unconnected(monkeypatch):
    row = SimpleNamespace(
        custody_id=uuid.uuid4(), custody_state="ACTIVE",
        originating_autonomous_cycle_id=uuid.uuid4(), originating_campaign_cycle_id=uuid.uuid4(),
        buy_claim_id=uuid.uuid4(), buy_live_order_id=uuid.uuid4(), campaign_id=uuid.uuid4(),
        buy_reconciliation_event_id=uuid.uuid4(),
        campaign_version=1, mandate_id=uuid.uuid4(), provider="kraken_spot",
        environment="production", product="BTC-USD", original_acquired_quantity=Decimal("0.00008"),
        observed_remaining_quantity=Decimal("0.00008"), quantity_authority="live_accounting_records",
        provenance_classification=custody.SCHEDULED_PRODUCTION_PROVENANCE, proof_eligible=True,
        disqualification_reason=None, latest_exit_evaluation_at=None, next_exit_evaluation_at=None,
        active_sell_decision_id=None, active_sell_package_id=None, active_sell_claim_id=None,
        active_sell_order_id=None, continuing_exit_authority_state="UNARMED",
        live_trading_profile_id=uuid.uuid4(), runtime_campaign_id=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    )
    db = _DB(rows=[[row], [row], []], scalars=[SimpleNamespace(status="EXPIRED"), SimpleNamespace(status="EXPIRED"), None])
    monkeypatch.setattr(custody, "compute_signed_owned_quantity", lambda **_kwargs: _async(Decimal("0.00008")))

    result = await custody.custody_status(db=db)

    assert result["verdict"] == "SELL_SUPERVISION_NOT_IMPLEMENTED"
    assert result["automatic_sell_submission"] is False
    assert result["items"][0]["sell_supervisor_connected"] is False
    assert result["items"][0]["authoritative_remaining_quantity"] == "0.00008"
    assert result["items"][0]["entry_campaign_status"] == "EXPIRED"
    assert result["items"][0]["entry_mandate_status"] == "EXPIRED"
    assert result["items"][0]["positive_inventory_supervised"] is True
    assert result["items"][0]["ownership_attribution_ambiguous"] is False


def test_state_machine_fails_closed_and_terminal_states_cannot_reopen():
    custody.validate_custody_transition(current="ACTIVE", target="EXIT_PENDING")
    with pytest.raises(InvalidRequestError):
        custody.validate_custody_transition(current="CLOSED", target="ACTIVE")


def test_model_enforces_lineage_and_nonterminal_scope_uniqueness():
    names = {item.name for item in AutonomousPositionCustody.__table__.constraints}
    assert {"uq_apc_buy_claim", "uq_apc_buy_package", "uq_apc_buy_order"} <= names
    index = next(item for item in AutonomousPositionCustody.__table__.indexes if item.name == "uq_apc_nonterminal_position_scope")
    assert index.unique is True
    assert "HANDOFF_PENDING" in str(index.dialect_options["postgresql"]["where"])
    assert [column.name for column in index.columns] == ["live_trading_profile_id", "product"]


def test_sqlite_constraint_prevents_two_runtime_campaigns_claiming_same_aggregate_scope():
    connection = sqlite3.connect(":memory:")
    connection.execute("""
        CREATE TABLE autonomous_position_custodies (
            custody_id TEXT PRIMARY KEY,
            custody_state TEXT NOT NULL,
            runtime_campaign_id TEXT NOT NULL,
            paper_account_id TEXT NOT NULL,
            live_trading_profile_id TEXT NOT NULL,
            exchange_connection_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            environment TEXT NOT NULL,
            product TEXT NOT NULL
        )
    """)
    connection.execute("""
        CREATE UNIQUE INDEX uq_apc_nonterminal_position_scope
        ON autonomous_position_custodies (live_trading_profile_id, product)
        WHERE custody_state IN ('HANDOFF_PENDING','ACTIVE','EXIT_PENDING','BLOCKED')
    """)
    account_id, profile_id, connection_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    scope = (account_id, profile_id, connection_id, "kraken_spot", "production", "BTC-USD")
    connection.execute(
        "INSERT INTO autonomous_position_custodies VALUES (?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), str(uuid.uuid4()), *scope),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO autonomous_position_custodies VALUES (?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?)",
            # Even corrupted/different lineage metadata cannot partition the
            # same authoritative profile/product accounting balance.
            (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), profile_id,
             str(uuid.uuid4()), "another_provider", "sandbox", "BTC-USD"),
        )


async def _async(value):
    return value
