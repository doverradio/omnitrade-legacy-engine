from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from app.core.errors import InvalidRequestError

from app.services.position_lifecycle.service import build_position_lifecycle_report


@dataclass(frozen=True)
class _Snapshot:
    position_id: str
    live_trading_profile_id: uuid.UUID
    account_id: uuid.UUID
    capital_campaign_id: int | None
    symbol: str
    asset_class: str
    position_size: Decimal
    entry_price: Decimal
    accumulated_entry_and_carry_costs: Decimal
    opened_at: datetime | None
    last_fill_at: datetime | None
    provider_order_ids: tuple[str, ...]
    provider_fill_ids: tuple[str, ...]
    accounting_record_count: int
    fail_closed_reason: str | None
    current_price: Decimal | None
    market_data_timestamp: datetime | None
    market_data_age_minutes: int | None
    market_data_interval: str | None
    market_data_source: str | None
    market_data_candle_id: int | None


class _StrictReadOnlyDb:
    def __init__(self):
        self.add_calls = 0
        self.flush_calls = 0
        self.commit_calls = 0

    def add(self, _obj):
        self.add_calls += 1
        raise AssertionError("write attempt: add")

    async def flush(self):
        self.flush_calls += 1
        raise AssertionError("write attempt: flush")

    async def commit(self):
        self.commit_calls += 1
        raise AssertionError("write attempt: commit")


@pytest.mark.asyncio
async def test_service_is_read_only_and_includes_policy_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _StrictReadOnlyDb()

    async def _fake_loader(*, db, account_id, campaign_id):
        _ = (db, account_id, campaign_id)
        return [
            _Snapshot(
                position_id="pos-1",
                live_trading_profile_id=uuid.uuid4(),
                account_id=uuid.uuid4(),
                capital_campaign_id=7,
                symbol="BTC-USD",
                asset_class="crypto",
                position_size=Decimal("1"),
                entry_price=Decimal("100"),
                accumulated_entry_and_carry_costs=Decimal("0.5"),
                opened_at=datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc),
                last_fill_at=datetime(2026, 7, 14, 10, 5, tzinfo=timezone.utc),
                provider_order_ids=("ord-1",),
                provider_fill_ids=("fill-1",),
                accounting_record_count=1,
                fail_closed_reason=None,
                current_price=Decimal("102"),
                market_data_timestamp=datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc),
                market_data_age_minutes=1,
                market_data_interval="15m",
                market_data_source="kraken_spot",
                market_data_candle_id=1,
            )
        ]

    monkeypatch.setattr("app.services.position_lifecycle.service.load_position_snapshots", _fake_loader)

    result = await build_position_lifecycle_report(
        db=db,
        position_id=None,
        account_id=None,
        campaign_id=None,
        asset_class="crypto",
        recommendation=None,
    )

    assert result.count == 1
    assert result.items[0].policy_id == "pl-policy-crypto-venue-neutral-v1"
    assert result.items[0].policy_version == "1.0.0"
    assert db.add_calls == 0
    assert db.flush_calls == 0
    assert db.commit_calls == 0


@pytest.mark.asyncio
async def test_service_fail_closed_when_policy_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_loader(*, db, account_id, campaign_id):
        _ = (db, account_id, campaign_id)
        return [
            _Snapshot(
                position_id="pos-x",
                live_trading_profile_id=uuid.uuid4(),
                account_id=uuid.uuid4(),
                capital_campaign_id=7,
                symbol="SPY-OPT",
                asset_class="options",
                position_size=Decimal("1"),
                entry_price=Decimal("10"),
                accumulated_entry_and_carry_costs=Decimal("0.1"),
                opened_at=None,
                last_fill_at=None,
                provider_order_ids=tuple(),
                provider_fill_ids=tuple(),
                accounting_record_count=0,
                fail_closed_reason=None,
                current_price=Decimal("10"),
                market_data_timestamp=datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc),
                market_data_age_minutes=1,
                market_data_interval="15m",
                market_data_source="kraken_spot",
                market_data_candle_id=1,
            )
        ]

    monkeypatch.setattr("app.services.position_lifecycle.service.load_position_snapshots", _fake_loader)

    with pytest.raises(InvalidRequestError):
        await build_position_lifecycle_report(
            db=_StrictReadOnlyDb(),
            position_id=None,
            account_id=None,
            campaign_id=None,
            asset_class=None,
            recommendation=None,
        )


def test_safety_no_execution_or_provider_imports() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[4] / "app" / "services" / "position_lifecycle"
    source = "\n".join((root / name).read_text() for name in ["service.py", "source_adapter.py", "evaluator.py", "policy_registry.py"])
    normalized = source.lower()

    assert "exchange_connections.providers" not in normalized
    assert "live_crypto_orders" not in normalized
    assert "create_order" not in normalized
    assert "submit_order" not in normalized
