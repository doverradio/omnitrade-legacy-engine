from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest

from app.core.errors import InvalidRequestError
from app.models.capital_campaign import CapitalCampaign
from app.models.capital_campaign_profit_cycle import CapitalCampaignProfitCycle
from app.models.capital_campaign_profit_policy import CapitalCampaignProfitPolicy
from app.schemas.capital_campaign_profit import CapitalCampaignProfitPolicyUpsertRequest
from app.services.capital_campaign_profit import service


class _FakeDb:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = list(scalar_results or [])
        self.added: list[object] = []
        self.committed = False

    async def scalar(self, _query):
        if self.scalar_results:
            return self.scalar_results.pop(0)
        return None

    async def flush(self):
        for item in self.added:
            if isinstance(item, CapitalCampaignProfitCycle) and item.cycle_id is None:
                item.cycle_id = 1
                item.cycle_uuid = uuid.UUID("77777777-7777-7777-7777-777777777777")
            if isinstance(item, CapitalCampaignProfitCycle):
                now = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
                if item.calculated_at is None:
                    item.calculated_at = now
                if item.created_at is None:
                    item.created_at = now
                if item.updated_at is None:
                    item.updated_at = now

    async def refresh(self, _item):
        return None

    async def commit(self):
        self.committed = True

    def add(self, item):
        self.added.append(item)


def _campaign(*, realized_profit: str, unrealized_profit: str = "0", fees: str = "0", starting_capital: str = "25", current_equity: str = "30"):
    return CapitalCampaign(
        id=1,
        uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        owner="owner-1",
        name="Campaign A",
        description=None,
        status="RUNNING",
        campaign_type="paper_validation",
        exchange=None,
        paper_account_id=None,
        validation_run_id=None,
        strategy_id=None,
        starting_capital=Decimal(starting_capital),
        current_equity=Decimal(current_equity),
        realized_profit=Decimal(realized_profit),
        unrealized_profit=Decimal(unrealized_profit),
        fees=Decimal(fees),
        roi=Decimal("0"),
        created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    )


def _policy(*, policy_type: str, target_amount: str | None = None, target_percent: str | None = None, compound_percent: str = "0", withdraw_percent: str = "0", require_approval: bool = True, protected_principal: str | None = None, max_capital: str | None = None):
    return CapitalCampaignProfitPolicy(
        policy_id=1,
        policy_uuid=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        capital_campaign_id=1,
        policy_type=policy_type,
        profit_target_amount=None if target_amount is None else Decimal(target_amount),
        profit_target_percent=None if target_percent is None else Decimal(target_percent),
        compound_percent=Decimal(compound_percent),
        withdraw_percent=Decimal(withdraw_percent),
        protected_principal_amount=None if protected_principal is None else Decimal(protected_principal),
        minimum_realized_profit=Decimal("0"),
        maximum_campaign_capital=None if max_capital is None else Decimal(max_capital),
        minimum_cash_reserve=Decimal("0"),
        fee_reserve_percent=Decimal("0"),
        tax_reserve_percent=Decimal("0"),
        cooldown_hours=0,
        require_operator_approval=require_approval,
        is_active=True,
        created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_fixed_dollar_target_above_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="6")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[_policy(policy_type="FULL_COMPOUND", target_amount="5"), None, None, None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.target_reached is True
    assert result.eligible_profit == Decimal("6")
    assert result.compound_amount == Decimal("6")
    assert result.status == "REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_percentage_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="3", starting_capital="25")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[_policy(policy_type="WITHDRAW_AND_COMPOUND", target_percent="10", compound_percent="60", withdraw_percent="40", require_approval=False), None, None, None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.target_reached is True
    assert result.compound_amount == Decimal("1.8")
    assert result.withdrawal_amount == Decimal("1.2")


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_below_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="2", unrealized_profit="50")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[_policy(policy_type="FULL_COMPOUND", target_amount="5"), None, None, None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.target_reached is False
    assert result.compound_amount == Decimal("0")
    assert result.withdrawal_amount == Decimal("0")
    assert result.status == "BELOW_TARGET"


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_fee_and_tax_reserve(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy(policy_type="WITHDRAW_PROFIT", target_amount="1", withdraw_percent="100")
    policy.fee_reserve_percent = Decimal("10")
    policy.tax_reserve_percent = Decimal("20")

    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="10", fees="1")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[policy, None, None, None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.eligible_profit == Decimal("6")
    assert result.withdrawal_amount == Decimal("6")


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_protected_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="10", current_equity="112", starting_capital="100")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[_policy(policy_type="PROTECTED_PRINCIPAL", target_amount="1", protected_principal="100"), None, None, None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.compound_amount == Decimal("10")
    assert result.withdrawal_amount == Decimal("0")


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_respects_maximum_campaign_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="10", starting_capital="25")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[_policy(policy_type="FULL_COMPOUND", target_amount="1", max_capital="30"), None, None, None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.compound_amount == Decimal("5")
    assert result.withdrawal_amount == Decimal("5")


@pytest.mark.asyncio
async def test_evaluate_profit_cycle_is_idempotent_for_same_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = CapitalCampaignProfitCycle(
        cycle_id=2,
        cycle_uuid=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        capital_campaign_id=1,
        profit_policy_id=1,
        cycle_number=2,
        opening_capital=Decimal("25"),
        opening_equity=Decimal("30"),
        realized_profit=Decimal("6"),
        unrealized_profit=Decimal("0"),
        fees=Decimal("0"),
        eligible_profit=Decimal("6"),
        compound_amount=Decimal("6"),
        withdrawal_amount=Decimal("0"),
        reserve_amount=Decimal("0"),
        closing_campaign_capital=Decimal("31"),
        target_reached=True,
        status="REVIEW_REQUIRED",
        settlement_state="SETTLEMENT_UNKNOWN",
        calculation_snapshot={},
        calculation_fingerprint="fingerprint",
        calculated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        approved_at=None,
        completed_at=None,
        created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    )

    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="6")

    async def _allocated_stub(_db, _campaign_id):
        return Decimal("0")

    async def _audit_stub(**_kwargs):
        return None

    db = _FakeDb(scalar_results=[_policy(policy_type="FULL_COMPOUND", target_amount="5"), existing])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)
    monkeypatch.setattr(service, "_allocated_profit_to_date", _allocated_stub)
    monkeypatch.setattr(service, "_record_event", _audit_stub)
    monkeypatch.setattr(service, "_fingerprint", lambda _payload: "fingerprint")

    result = await service.evaluate_profit_cycle(db=db, campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"), actor="test")

    assert result.cycle_uuid == existing.cycle_uuid


@pytest.mark.asyncio
async def test_upsert_profit_policy_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _campaign_stub(_db, _campaign_uuid):
        return _campaign(realized_profit="0")

    db = _FakeDb(scalar_results=[None])
    monkeypatch.setattr(service, "_get_campaign_by_uuid", _campaign_stub)

    with pytest.raises(InvalidRequestError):
        await service.upsert_profit_policy(
            db=db,
            campaign_uuid=uuid.UUID("11111111-1111-1111-1111-111111111111"),
            request=CapitalCampaignProfitPolicyUpsertRequest(
                policy_type="PARTIAL_COMPOUND",
                compound_percent=Decimal("90"),
                withdraw_percent=Decimal("20"),
            ),
        )
