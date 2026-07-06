from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.models.trade import Trade
from app.services.paper.accounting import compute_account_snapshot


def test_compute_account_snapshot_rolls_cash_equity_and_positions() -> None:
    account_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    trade_time = datetime(2026, 7, 6, tzinfo=timezone.utc)

    trades = [
        Trade(
            paper_account_id=account_id,
            asset_id=asset_id,
            side="buy",
            quantity=Decimal("0.50"),
            price=Decimal("20"),
            fee=Decimal("0.10"),
            is_paper=True,
            execution_venue="internal_sim",
            executed_at=trade_time,
        ),
        Trade(
            paper_account_id=account_id,
            asset_id=asset_id,
            side="sell",
            quantity=Decimal("0.20"),
            price=Decimal("25"),
            fee=Decimal("0.05"),
            is_paper=True,
            execution_venue="internal_sim",
            executed_at=trade_time,
        ),
    ]

    snapshot = compute_account_snapshot(
        starting_balance=Decimal("25"),
        trades=trades,
        symbols_by_asset_id={asset_id: "BTCUSDT"},
        latest_prices_by_asset_id={asset_id: Decimal("30")},
    )

    # cash = 25 - (0.5*20 + 0.1) + (0.2*25 - 0.05) = 19.85
    assert snapshot.cash_balance == Decimal("19.85")
    # remaining quantity = 0.3, position value at 30 = 9
    assert snapshot.position_value == Decimal("9.0")
    assert snapshot.equity == Decimal("28.85")
    assert snapshot.equity_return_usd == Decimal("3.85")
    assert snapshot.equity_return_pct == Decimal("0.154")
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].quantity == Decimal("0.30")


def test_compute_account_snapshot_handles_no_trades() -> None:
    snapshot = compute_account_snapshot(
        starting_balance=Decimal("25"),
        trades=[],
        symbols_by_asset_id={},
        latest_prices_by_asset_id={},
    )

    assert snapshot.cash_balance == Decimal("25")
    assert snapshot.position_value == Decimal("0")
    assert snapshot.equity == Decimal("25")
    assert snapshot.equity_return_usd == Decimal("0")
    assert snapshot.equity_return_pct == Decimal("0")
    assert snapshot.positions == ()
