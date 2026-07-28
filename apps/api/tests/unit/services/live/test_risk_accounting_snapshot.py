from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.live.risk_accounting_snapshot import RiskAccountingUnavailableError, build_risk_accounting_snapshot


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def _db(*, campaign, orders=(), reconciliations=(), accounting=()):
    result_sets = [_Rows(list(orders))]
    if orders:
        result_sets.append(_Rows(list(reconciliations)))
    result_sets.append(_Rows(list(accounting)))
    return SimpleNamespace(
        scalar=AsyncMock(return_value=campaign),
        scalars=AsyncMock(side_effect=result_sets),
    )


def _order(*, status: str, submitted_at: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        live_crypto_order_id=uuid4(), status=status,
        submitted_at=submitted_at, created_at=submitted_at or datetime.now(timezone.utc),
    )


def _reconciliation(*, order, status: str, now: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), live_crypto_order_id=order.live_crypto_order_id,
        reconciliation_status=status, recorded_at=now,
    )


async def _snapshot(db, now):
    return await build_risk_accounting_snapshot(
        db=db, campaign_id=db.scalar.return_value.uuid, campaign_version=1,
        account_id=db.scalar.return_value.paper_account_id, live_trading_profile_id=uuid4(),
        provider="kraken_spot", environment="production", product="BTC-USD", as_of=now,
    )


@pytest.mark.asyncio
async def test_complete_empty_ledger_is_authoritative_zero() -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=7, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    result = await _snapshot(_db(campaign=campaign), now)
    assert result.current_open_exposure_usd == 0
    assert result.current_position_count == 0
    assert result.evidence_ids["capital_campaign_ids"] == ["7"]


@pytest.mark.asyncio
async def test_open_position_daily_deployment_loss_and_drawdown_are_ledger_derived() -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=8, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=8)
    rows = [
        SimpleNamespace(id=uuid4(), symbol="BTC-USD", side="buy", filled_quantity=Decimal("2"), gross_notional=Decimal("10"), fee_amount=Decimal("1"), fill_price=Decimal("5"), recorded_at=now),
        SimpleNamespace(id=uuid4(), symbol="BTC-USD", side="sell", filled_quantity=Decimal("1"), gross_notional=Decimal("4"), fee_amount=Decimal("0.5"), fill_price=Decimal("4"), recorded_at=now),
    ]
    result = await _snapshot(_db(campaign=campaign, accounting=rows), now)
    assert result.current_open_exposure_usd == Decimal("4")
    assert result.current_position_count == 1
    assert result.daily_deployed_usd == Decimal("11")
    assert result.daily_realized_loss_usd == Decimal("2")
    assert result.campaign_drawdown_usd == Decimal("2")
    assert len(result.evidence_ids["accounting_record_ids"]) == 2


@pytest.mark.asyncio
async def test_unresolved_submission_prevents_optimistic_zero() -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=9, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    order = SimpleNamespace(live_crypto_order_id=uuid4(), status="RECONCILIATION_REQUIRED", submitted_at=now, created_at=now)
    db = SimpleNamespace(scalar=AsyncMock(return_value=campaign), scalars=AsyncMock(side_effect=[_Rows([order]), _Rows([])]))
    with pytest.raises(RiskAccountingUnavailableError) as exc:
        await _snapshot(db, now)
    assert exc.value.reason_code == "unresolved_provider_exposure"


@pytest.mark.asyncio
async def test_authoritative_rejection_without_reconciliation_is_zero_exposure() -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=11, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    order = _order(status="REJECTED", submitted_at=now)

    result = await _snapshot(_db(campaign=campaign, orders=[order]), now)

    assert result.current_open_exposure_usd == 0
    assert result.current_position_count == 0
    assert result.evidence_ids["live_crypto_order_ids"] == [str(order.live_crypto_order_id)]


@pytest.mark.asyncio
async def test_rejection_with_unresolved_reconciliation_remains_blocked() -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=12, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    order = _order(status="REJECTED", submitted_at=now)
    reconciliation = _reconciliation(order=order, status="reconciliation_required", now=now)

    with pytest.raises(RiskAccountingUnavailableError) as exc:
        await _snapshot(_db(campaign=campaign, orders=[order], reconciliations=[reconciliation]), now)

    assert exc.value.reason_code == "unresolved_provider_exposure"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["UNKNOWN", "RECONCILIATION_REQUIRED"])
async def test_explicitly_unresolved_order_status_remains_blocked(status: str) -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=13, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    order = _order(status=status, submitted_at=now)

    with pytest.raises(RiskAccountingUnavailableError) as exc:
        await _snapshot(_db(campaign=campaign, orders=[order]), now)

    assert exc.value.reason_code == "unresolved_provider_exposure"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["SUBMISSION_PENDING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED"])
async def test_submitted_order_without_authoritative_accounting_remains_blocked(status: str) -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=14, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    order = _order(status=status, submitted_at=now)

    with pytest.raises(RiskAccountingUnavailableError) as exc:
        await _snapshot(_db(campaign=campaign, orders=[order]), now)

    assert exc.value.reason_code == "unresolved_provider_exposure"


@pytest.mark.asyncio
async def test_contradictory_sell_without_position_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    campaign = SimpleNamespace(id=10, uuid=uuid4(), definition_version=1, paper_account_id=uuid4(), starting_capital=10, current_equity=10)
    row = SimpleNamespace(id=uuid4(), symbol="BTC-USD", side="sell", filled_quantity=Decimal("1"), gross_notional=Decimal("4"), fee_amount=0, fill_price=4, recorded_at=now)
    with pytest.raises(RiskAccountingUnavailableError) as exc:
        await _snapshot(_db(campaign=campaign, accounting=[row]), now)
    assert exc.value.reason_code == "position_evidence_inconsistent"


@pytest.mark.asyncio
async def test_missing_campaign_accounting_fails_closed() -> None:
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(RiskAccountingUnavailableError) as exc:
        await build_risk_accounting_snapshot(
            db=db, campaign_id=uuid4(), campaign_version=1, account_id=uuid4(), live_trading_profile_id=uuid4(),
            provider="kraken_spot", environment="production", product="BTC-USD",
        )
    assert exc.value.reason_code == "risk_accounting_incomplete"
