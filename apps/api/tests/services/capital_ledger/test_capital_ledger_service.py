from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest

from app.services.capital_ledger import service
from app.services.paper.accounting import AccountAccountingSnapshot, PositionAccounting


class _DummySession:
    pass


def _run(*, run_id: str, name: str, status: str, paper_capital: str, strategies: list[str], created_at: datetime | None = None):
    return SimpleNamespace(
        validation_run_id=uuid.UUID(run_id),
        name=name,
        status=status,
        paper_capital=Decimal(paper_capital),
        started_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
        completed_at=None if status == "RUNNING" else datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
        enabled_strategies=strategies,
        created_at=created_at or datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
    )


def _account(*, account_id: str, name: str, starting: str, is_active: bool = True):
    return SimpleNamespace(
        id=uuid.UUID(account_id),
        name=name,
        starting_balance=Decimal(starting),
        is_active=is_active,
        created_at=datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc),
    )


def _campaign(*, campaign_id: int, campaign_uuid: str, name: str, status: str, paper_account_id: str | None, validation_run_id: str | None):
    return SimpleNamespace(
        id=campaign_id,
        uuid=uuid.UUID(campaign_uuid),
        name=name,
        status=status,
        paper_account_id=None if paper_account_id is None else uuid.UUID(paper_account_id),
        validation_run_id=None if validation_run_id is None else uuid.UUID(validation_run_id),
        created_at=datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_empty_ledger_returns_zero_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_runs(_db):
        return []

    async def _no_accounts(_db):
        return []

    async def _no_campaigns(_db):
        return []

    async def _no_metrics(_db):
        return {}

    async def _no_trades(_db):
        return {}

    monkeypatch.setattr(service, "_load_validation_runs", _no_runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _no_accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _no_campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _no_metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _no_trades)

    result = await service.build_capital_ledger(db=_DummySession())

    assert result.summary.total_managed_capital == Decimal("0")
    assert result.summary.total_current_equity == Decimal("0")
    assert result.summary.active_capital_pools == 0
    assert result.capital_pools == []


@pytest.mark.asyncio
async def test_validation_runs_managed_capital_and_no_double_counting(monkeypatch: pytest.MonkeyPatch) -> None:
    run_one = _run(
        run_id="11111111-1111-1111-1111-111111111111",
        name="Run A",
        status="RUNNING",
        paper_capital="25",
        strategies=["RSI"],
    )
    run_two = _run(
        run_id="22222222-2222-2222-2222-222222222222",
        name="Run B",
        status="RUNNING",
        paper_capital="25",
        strategies=["MA"],
    )

    async def _runs(_db):
        return [run_one, run_two]

    async def _accounts(_db):
        return []

    async def _campaigns(_db):
        return []

    async def _metrics(_db):
        return {
            run_one.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("26"), trades=8),
            run_two.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("24"), trades=5),
        }

    async def _trade_counts(_db):
        return {}

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)

    result = await service.build_capital_ledger(db=_DummySession())

    # Managed capital counts funded pools once: 25 + 25.
    assert result.summary.total_managed_capital == Decimal("50")
    assert result.summary.total_starting_capital == Decimal("50")
    # Current equity is mark-to-market across pools.
    assert result.summary.total_current_equity == Decimal("50")
    # Trade activity exists but does not increase managed capital.
    assert result.summary.total_trades == 13
    assert result.summary.total_managed_capital != Decimal("63")
    assert result.summary.active_capital_pools == 2


@pytest.mark.asyncio
async def test_single_25_validation_run_managed_capital(monkeypatch: pytest.MonkeyPatch) -> None:
    single_run = _run(
        run_id="aaaaaaaa-1111-1111-1111-111111111111",
        name="Single Run",
        status="RUNNING",
        paper_capital="25",
        strategies=["RSI"],
    )

    async def _runs(_db):
        return [single_run]

    async def _accounts(_db):
        return []

    async def _campaigns(_db):
        return []

    async def _metrics(_db):
        return {
            single_run.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("25"), trades=0),
        }

    async def _trade_counts(_db):
        return {}

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)

    result = await service.build_capital_ledger(db=_DummySession())

    assert result.summary.total_managed_capital == Decimal("25")
    assert result.summary.active_capital_pools == 1


@pytest.mark.asyncio
async def test_active_completed_and_related_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    run_active = _run(
        run_id="11111111-1111-1111-1111-111111111111",
        name="Active Run",
        status="RUNNING",
        paper_capital="25",
        strategies=["RSI"],
    )
    run_done = _run(
        run_id="33333333-3333-3333-3333-333333333333",
        name="Completed Run",
        status="COMPLETED",
        paper_capital="25",
        strategies=[],
    )
    account = _account(
        account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        name="Family Paper",
        starting="25",
        is_active=True,
    )

    async def _runs(_db):
        return [run_active, run_done]

    async def _accounts(_db):
        return [account]

    async def _campaigns(_db):
        return []

    async def _metrics(_db):
        return {
            run_active.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("25.5"), trades=2),
            run_done.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("24.0"), trades=3),
        }

    async def _trade_counts(_db):
        return {account.id: 2}

    async def _snapshot(*, db, paper_account_id, starting_balance):
        _ = (db, paper_account_id, starting_balance)
        return AccountAccountingSnapshot(
            cash_balance=Decimal("10"),
            position_value=Decimal("16"),
            equity=Decimal("26"),
            equity_return_usd=Decimal("1"),
            equity_return_pct=Decimal("0.04"),
            positions=(
                PositionAccounting(
                    asset_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                    symbol="BTCUSD",
                    quantity=Decimal("1"),
                    avg_entry_price=Decimal("15"),
                    position_value=Decimal("16"),
                    unrealized_pnl_usd=Decimal("1"),
                    unrealized_pnl_pct=Decimal("0.0666"),
                ),
            ),
        )

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)
    monkeypatch.setattr(service, "build_account_snapshot", _snapshot)

    result = await service.build_capital_ledger(db=_DummySession())

    active_pool = next(item for item in result.capital_pools if item.name == "Active Run")
    completed_pool = next(item for item in result.capital_pools if item.name == "Completed Run")
    paper_pool = next(item for item in result.capital_pools if item.name == "Family Paper")
    position_pool = next(item for item in result.capital_pools if item.capital_pool_type == "position")

    assert active_pool.status == "active"
    assert completed_pool.status == "completed"
    assert active_pool.related_page_url == "/validation-runs"
    assert paper_pool.related_page_url == "/paper-trading"
    assert position_pool.related_page_url == "/paper-trading"
    assert position_pool.parent_capital_pool_id == paper_pool.capital_pool_id


@pytest.mark.asyncio
async def test_pagination_and_partial_data_completeness(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [
        _run(
            run_id="11111111-1111-1111-1111-111111111111",
            name="Run A",
            status="RUNNING",
            paper_capital="25",
            strategies=["A", "B"],
        ),
        _run(
            run_id="22222222-2222-2222-2222-222222222222",
            name="Run B",
            status="RUNNING",
            paper_capital="25",
            strategies=["C"],
        ),
    ]

    async def _runs(_db):
        return runs

    async def _accounts(_db):
        return []

    async def _campaigns(_db):
        return [SimpleNamespace(campaign_id=uuid.uuid4())]

    async def _metrics(_db):
        # Missing Run B metric should lower completeness.
        return {
            runs[0].validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("25.2"), trades=1),
        }

    async def _trade_counts(_db):
        return {}

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)

    page_one = await service.build_capital_ledger(db=_DummySession(), page=1, page_size=2)

    assert page_one.page == 1
    assert page_one.page_size == 2
    assert page_one.has_more is True
    assert len(page_one.capital_pools) == 2
    assert page_one.summary.data_completeness_percent < 100
    assert any(source.startswith("validation_run_metrics") for source in page_one.summary.unavailable_sources)
    assert "research_campaign_allocations" in page_one.summary.unavailable_sources


@pytest.mark.asyncio
async def test_managed_capital_excludes_child_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _account(
        account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        name="Paper Parent",
        starting="25",
        is_active=True,
    )

    async def _runs(_db):
        return []

    async def _accounts(_db):
        return [account]

    async def _campaigns(_db):
        return []

    async def _metrics(_db):
        return {}

    async def _trade_counts(_db):
        return {account.id: 1}

    async def _snapshot(*, db, paper_account_id, starting_balance):
        _ = (db, paper_account_id, starting_balance)
        return AccountAccountingSnapshot(
            cash_balance=Decimal("10"),
            position_value=Decimal("20"),
            equity=Decimal("30"),
            equity_return_usd=Decimal("5"),
            equity_return_pct=Decimal("0.20"),
            positions=(
                PositionAccounting(
                    asset_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                    symbol="BTCUSD",
                    quantity=Decimal("1"),
                    avg_entry_price=Decimal("15"),
                    position_value=Decimal("20"),
                    unrealized_pnl_usd=Decimal("5"),
                    unrealized_pnl_pct=Decimal("0.33"),
                ),
            ),
        )

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)
    monkeypatch.setattr(service, "build_account_snapshot", _snapshot)

    result = await service.build_capital_ledger(db=_DummySession())

    # Managed capital must count only the parent funded pool, not child position rows.
    assert result.summary.total_managed_capital == Decimal("25")
    assert result.summary.total_current_equity == Decimal("30")
    position_pool = next(item for item in result.capital_pools if item.capital_pool_type == "position")
    assert position_pool.current_equity == Decimal("20")


@pytest.mark.asyncio
async def test_capital_ledger_links_pools_to_campaigns_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(
        run_id="11111111-1111-1111-1111-111111111111",
        name="Run A",
        status="RUNNING",
        paper_capital="25",
        strategies=["RSI"],
    )
    account = _account(
        account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        name="Paper A",
        starting="25",
        is_active=True,
    )
    campaign = _campaign(
        campaign_id=1,
        campaign_uuid="99999999-9999-9999-9999-999999999999",
        name="Campaign Link",
        status="RUNNING",
        paper_account_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        validation_run_id="11111111-1111-1111-1111-111111111111",
    )

    async def _runs(_db):
        return [run]

    async def _accounts(_db):
        return [account]

    async def _campaigns(_db):
        return []

    async def _capital_campaigns(_db):
        return [campaign]

    async def _metrics(_db):
        return {run.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("25"), trades=1)}

    async def _trade_counts(_db):
        return {account.id: 0}

    async def _snapshot(*, db, paper_account_id, starting_balance):
        _ = (db, paper_account_id, starting_balance)
        return AccountAccountingSnapshot(
            cash_balance=Decimal("25"),
            position_value=Decimal("0"),
            equity=Decimal("25"),
            equity_return_usd=Decimal("0"),
            equity_return_pct=Decimal("0"),
            positions=(),
        )

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_capital_campaigns", _capital_campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)
    monkeypatch.setattr(service, "build_account_snapshot", _snapshot)

    result = await service.build_capital_ledger(db=_DummySession())

    validation_pool = next(item for item in result.capital_pools if item.capital_pool_type == "validation_run")
    paper_pool = next(item for item in result.capital_pools if item.capital_pool_type == "paper_account")
    assert validation_pool.capital_campaign_uuid == "99999999-9999-9999-9999-999999999999"
    assert paper_pool.capital_campaign_name == "Campaign Link"
    assert validation_pool.capital_campaign_status == "RUNNING"


@pytest.mark.asyncio
async def test_capital_ledger_without_campaign_links_keeps_totals_and_null_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(
        run_id="11111111-1111-1111-1111-111111111111",
        name="Run A",
        status="RUNNING",
        paper_capital="25",
        strategies=["RSI"],
    )

    async def _runs(_db):
        return [run]

    async def _accounts(_db):
        return []

    async def _campaigns(_db):
        return []

    async def _capital_campaigns(_db):
        return []

    async def _metrics(_db):
        return {run.validation_run_id: service._ValidationMetricSnapshot(current_equity=Decimal("25"), trades=1)}

    async def _trade_counts(_db):
        return {}

    monkeypatch.setattr(service, "_load_validation_runs", _runs)
    monkeypatch.setattr(service, "_load_paper_accounts", _accounts)
    monkeypatch.setattr(service, "_load_research_campaigns", _campaigns)
    monkeypatch.setattr(service, "_load_capital_campaigns", _capital_campaigns)
    monkeypatch.setattr(service, "_load_validation_run_metrics", _metrics)
    monkeypatch.setattr(service, "_load_trade_counts_by_account", _trade_counts)

    result = await service.build_capital_ledger(db=_DummySession())

    validation_pool = next(item for item in result.capital_pools if item.capital_pool_type == "validation_run")
    assert validation_pool.capital_campaign_uuid is None
    assert validation_pool.capital_campaign_name is None
    assert validation_pool.capital_campaign_status is None
    assert result.summary.total_managed_capital == Decimal("25")
