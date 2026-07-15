from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.risk import equity_evidence as ee
from app.services.risk.equity_evidence import EquityBaselineSnapshot, EquityValuationSnapshot


class _FakeAccount:
    def __init__(self, *, current_cash_balance: Decimal) -> None:
        self.id = UUID("905a408c-7d8e-4fc7-ad3b-9ff637005d73")
        self.starting_balance = Decimal("25")
        self.current_cash_balance = current_cash_balance


class _FakeDb:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("execute should not be called when loaders are monkeypatched")


def _positions_three_assets() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(asset_id=UUID("11111111-1111-1111-1111-111111111111"), symbol="BTCUSDT", quantity=Decimal("0.2")),
        SimpleNamespace(asset_id=UUID("22222222-2222-2222-2222-222222222222"), symbol="ETHUSDT", quantity=Decimal("1.5")),
        SimpleNamespace(asset_id=UUID("33333333-3333-3333-3333-333333333333"), symbol="SOLUSDT", quantity=Decimal("10")),
    ]


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_no_open_positions_is_ready_cash_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25"),
            cash_balance=Decimal("25"),
            position_value=Decimal("0"),
            positions=[],
        )

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", lambda **_kwargs: None)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("25")),
        max_price_age_seconds=120,
    )

    assert valuation.valuation_state == "ready"
    assert valuation.valuation_source == "paper_account_snapshot_cash_only"
    assert valuation.latest_price_timestamp is None
    assert valuation.current_equity == Decimal("25")
    assert valuation.position_value == Decimal("0")


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_uses_fresh_provider_quotes_for_all_open_positions(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 22, 44, 0, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25.51051984978665"),
            cash_balance=Decimal("4.34816564978665"),
            position_value=Decimal("21.1623542"),
            positions=_positions_three_assets(),
        )

    async def _provider_context(*_args, **_kwargs):
        return ("kraken_spot", "production", {"api_key": "k", "api_secret": "s"})

    async def _provider_quote(*_args, **kwargs):
        mapping = {
            "BTC-USD": (Decimal("60000"), now - timedelta(seconds=10), "/public/Ticker"),
            "ETH-USD": (Decimal("3000"), now - timedelta(seconds=5), "/public/Ticker"),
            "SOL-USD": (Decimal("150"), now - timedelta(seconds=1), "/public/Ticker"),
        }
        return mapping[kwargs["product_id"]]

    async def _candle_loader(*_args, **_kwargs):
        return {}

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return now

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_price_evidence_from_provider", _provider_quote)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)
    monkeypatch.setattr(ee, "datetime", _FrozenDateTime)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("4.34816564978665")),
        max_price_age_seconds=30,
    )

    assert valuation.valuation_state == "ready"
    assert valuation.valuation_source == "provider_quotes"
    assert valuation.missing_price_assets == []
    assert valuation.stale_price_assets == []
    assert len(valuation.price_evidence) == 3
    assert {item["state"] for item in valuation.price_evidence} == {"ready"}
    assert {item["source"] for item in valuation.price_evidence} == {"provider_quote"}


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_uses_fresh_candle_evidence_with_cadence_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 22, 44, 35, tzinfo=timezone.utc)
    close_time = datetime(2026, 7, 15, 22, 41, 59, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25.51051984978665"),
            cash_balance=Decimal("4.34816564978665"),
            position_value=Decimal("21.1623542"),
            positions=_positions_three_assets(),
        )

    async def _provider_context(*_args, **_kwargs):
        return None

    async def _candle_loader(*_args, **_kwargs):
        return {
            UUID("11111111-1111-1111-1111-111111111111"): (Decimal("60000"), close_time, "15m", "kraken_spot"),
            UUID("22222222-2222-2222-2222-222222222222"): (Decimal("3000"), close_time, "15m", "kraken_spot"),
            UUID("33333333-3333-3333-3333-333333333333"): (Decimal("150"), close_time, "15m", "kraken_spot"),
        }

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return now

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)
    monkeypatch.setattr(ee, "datetime", _FrozenDateTime)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("4.34816564978665")),
        max_price_age_seconds=30,
    )

    assert valuation.valuation_state == "ready"
    assert valuation.valuation_source == "candle_interval_bound"
    assert valuation.stale_price_assets == []
    assert valuation.missing_price_assets == []
    assert len(valuation.price_evidence) == 3


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_fails_when_candle_evidence_exceeds_interval_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 22, 58, 35, tzinfo=timezone.utc)
    close_time = datetime(2026, 7, 15, 22, 41, 59, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25"),
            cash_balance=Decimal("5"),
            position_value=Decimal("20"),
            positions=_positions_three_assets(),
        )

    async def _provider_context(*_args, **_kwargs):
        return None

    async def _candle_loader(*_args, **_kwargs):
        return {
            UUID("11111111-1111-1111-1111-111111111111"): (Decimal("60000"), close_time, "15m", "kraken_spot"),
            UUID("22222222-2222-2222-2222-222222222222"): (Decimal("3000"), close_time, "15m", "kraken_spot"),
            UUID("33333333-3333-3333-3333-333333333333"): (Decimal("150"), close_time, "15m", "kraken_spot"),
        }

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return now

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)
    monkeypatch.setattr(ee, "datetime", _FrozenDateTime)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("5")),
        max_price_age_seconds=30,
    )

    assert valuation.valuation_state == "stale_price_evidence"
    assert sorted(valuation.stale_price_assets) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_missing_and_partial_price_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 22, 44, 35, tzinfo=timezone.utc)
    close_time = datetime(2026, 7, 15, 22, 41, 59, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25"),
            cash_balance=Decimal("5"),
            position_value=Decimal("20"),
            positions=_positions_three_assets(),
        )

    async def _provider_context(*_args, **_kwargs):
        return None

    async def _candle_loader(*_args, **_kwargs):
        return {
            UUID("11111111-1111-1111-1111-111111111111"): (Decimal("60000"), close_time, "15m", "kraken_spot"),
            UUID("22222222-2222-2222-2222-222222222222"): (None, close_time, "15m", "kraken_spot"),
            UUID("33333333-3333-3333-3333-333333333333"): (Decimal("150"), None, "15m", "kraken_spot"),
        }

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return now

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)
    monkeypatch.setattr(ee, "datetime", _FrozenDateTime)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("5")),
        max_price_age_seconds=30,
    )

    assert valuation.valuation_state == "missing_price_evidence"
    assert sorted(valuation.missing_price_assets) == ["ETHUSDT", "SOLUSDT"]
    assert valuation.stale_price_assets == []


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_provider_failure_falls_back_to_fresh_candle_with_explicit_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 22, 44, 35, tzinfo=timezone.utc)
    close_time = datetime(2026, 7, 15, 22, 41, 59, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25"),
            cash_balance=Decimal("5"),
            position_value=Decimal("20"),
            positions=_positions_three_assets(),
        )

    async def _provider_context(*_args, **_kwargs):
        return ("kraken_spot", "production", {"api_key": "k", "api_secret": "s"})

    async def _provider_quote(*_args, **kwargs):
        if kwargs["product_id"] == "ETH-USD":
            raise RuntimeError("provider timeout")
        return (Decimal("60000"), now - timedelta(seconds=5), "/public/Ticker")

    async def _candle_loader(*_args, **_kwargs):
        return {
            UUID("11111111-1111-1111-1111-111111111111"): (Decimal("60000"), close_time, "15m", "kraken_spot"),
            UUID("22222222-2222-2222-2222-222222222222"): (Decimal("3000"), close_time, "15m", "kraken_spot"),
            UUID("33333333-3333-3333-3333-333333333333"): (Decimal("150"), close_time, "15m", "kraken_spot"),
        }

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return now

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_price_evidence_from_provider", _provider_quote)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)
    monkeypatch.setattr(ee, "datetime", _FrozenDateTime)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("5")),
        max_price_age_seconds=30,
    )

    assert valuation.valuation_state == "ready"
    assert valuation.valuation_source == "mixed_provider_quote_and_candle"
    fallback_rows = [row for row in valuation.price_evidence if row["symbol"] == "ETHUSDT"]
    assert len(fallback_rows) == 1
    assert "provider_quote_failed" in str(fallback_rows[0]["detail"])
    assert fallback_rows[0]["source"] == "candle"


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_timestamp_boundary_for_provider_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 22, 44, 35, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25"),
            cash_balance=Decimal("5"),
            position_value=Decimal("20"),
            positions=[SimpleNamespace(asset_id=UUID("11111111-1111-1111-1111-111111111111"), symbol="BTCUSDT", quantity=Decimal("0.2"))],
        )

    async def _provider_context(*_args, **_kwargs):
        return ("kraken_spot", "production", {"api_key": "k", "api_secret": "s"})

    async def _provider_quote(*_args, **_kwargs):
        return (Decimal("60000"), now - timedelta(seconds=30), "/public/Ticker")

    async def _candle_loader(*_args, **_kwargs):
        return {}

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return now

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_price_evidence_from_provider", _provider_quote)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)
    monkeypatch.setattr(ee, "datetime", _FrozenDateTime)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("5")),
        max_price_age_seconds=30,
    )

    assert valuation.valuation_state == "ready"
    assert valuation.stale_price_assets == []


@pytest.mark.asyncio
async def test_build_equity_valuation_snapshot_flags_inconsistent_account_state(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    async def _snapshot(*_args, **_kwargs):
        return SimpleNamespace(
            equity=Decimal("25"),
            cash_balance=Decimal("24.5"),
            position_value=Decimal("0.5"),
            positions=[SimpleNamespace(asset_id=UUID("11111111-1111-1111-1111-111111111111"), symbol="BTCUSDT", quantity=Decimal("1"))],
        )

    async def _provider_context(*_args, **_kwargs):
        return None

    async def _candle_loader(*_args, **_kwargs):
        return {
            UUID("11111111-1111-1111-1111-111111111111"): (Decimal("100"), now, "15m", "kraken_spot"),
        }

    monkeypatch.setattr(ee, "build_account_snapshot", _snapshot)
    monkeypatch.setattr(ee, "_resolve_provider_quote_context", _provider_context)
    monkeypatch.setattr(ee, "_load_open_position_latest_candle_points", _candle_loader)

    valuation = await ee.build_equity_valuation_snapshot(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("25")),
        max_price_age_seconds=60,
    )

    assert valuation.valuation_state == "inconsistent_account_state"


@pytest.mark.asyncio
async def test_resolve_equity_risk_evidence_fail_closed_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    valuation = EquityValuationSnapshot(
        generated_at=datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
        current_equity=Decimal("25"),
        cash_balance=Decimal("25"),
        position_value=Decimal("0"),
        latest_price_timestamp=None,
        valuation_source="paper_account_snapshot_cash_only",
        valuation_state="ready",
        missing_price_assets=[],
        stale_price_assets=[],
        stale_cutoff=datetime(2026, 7, 15, 11, 59, tzinfo=timezone.utc),
        price_evidence=[],
    )
    baseline = EquityBaselineSnapshot(
        start_of_day_equity=Decimal("25"),
        high_water_mark_equity=Decimal("25"),
        start_of_day_source="bootstrap_first_equity_observation",
        high_water_mark_source="bootstrap_first_equity_observation",
        session_date=date(2026, 7, 15),
        baseline_state="ready",
        baseline_ready=True,
    )

    async def _valuation(*_args, **_kwargs):
        return valuation

    async def _baseline(*_args, **_kwargs):
        return baseline

    monkeypatch.setattr(ee, "build_equity_valuation_snapshot", _valuation)
    monkeypatch.setattr(ee, "_upsert_equity_baseline", _baseline)

    async def _counts(*_args, **_kwargs):
        return (3, 0)

    monkeypatch.setattr(ee, "_count_reconciliation_uncertainty", _counts)
    evidence = await ee.resolve_equity_risk_evidence(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("25")),
        actor="test",
        max_price_age_seconds=60,
    )
    assert evidence.ready is False
    assert evidence.fail_closed_reason == "unresolved_reconciliation_state"

    async def _counts2(*_args, **_kwargs):
        return (0, 2)

    monkeypatch.setattr(ee, "_count_reconciliation_uncertainty", _counts2)
    evidence = await ee.resolve_equity_risk_evidence(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("25")),
        actor="test",
        max_price_age_seconds=60,
    )
    assert evidence.ready is False
    assert evidence.fail_closed_reason == "unknown_provider_order_state"

    baseline_bootstrap = EquityBaselineSnapshot(
        start_of_day_equity=Decimal("25"),
        high_water_mark_equity=Decimal("25"),
        start_of_day_source="bootstrap_first_equity_observation",
        high_water_mark_source="bootstrap_first_equity_observation",
        session_date=date(2026, 7, 15),
        baseline_state="bootstrap_first_observation",
        baseline_ready=False,
    )

    async def _baseline_bootstrap(*_args, **_kwargs):
        return baseline_bootstrap

    async def _counts_clear(*_args, **_kwargs):
        return (0, 0)

    monkeypatch.setattr(ee, "_upsert_equity_baseline", _baseline_bootstrap)
    monkeypatch.setattr(ee, "_count_reconciliation_uncertainty", _counts_clear)
    evidence = await ee.resolve_equity_risk_evidence(
        db=_FakeDb(),
        paper_account=_FakeAccount(current_cash_balance=Decimal("25")),
        actor="test",
        max_price_age_seconds=60,
    )
    assert evidence.ready is False
    assert evidence.fail_closed_reason == "baseline_bootstrap_required"
