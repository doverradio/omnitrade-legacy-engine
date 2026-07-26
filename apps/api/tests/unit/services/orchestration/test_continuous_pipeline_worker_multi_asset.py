from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.orchestration import continuous_pipeline_worker as worker


def _settings(additional: str = "") -> SimpleNamespace:
    ns = SimpleNamespace(autonomous_cycle_additional_products=additional)
    ns.parsed_autonomous_cycle_additional_products = [  # type: ignore[attr-defined]
        item.strip().upper() for item in additional.split(",") if item.strip()
    ]
    return ns


@pytest.mark.asyncio
async def test_default_empty_config_resolves_to_btc_only() -> None:
    products = await worker._resolve_autonomous_cycle_products(settings=_settings(""), db=None)
    assert products == ["BTC-USD"]


@pytest.mark.asyncio
async def test_known_additional_products_are_appended_after_btc() -> None:
    products = await worker._resolve_autonomous_cycle_products(settings=_settings("ETH-USD,SOL-USD"), db=None)
    assert products == ["BTC-USD", "ETH-USD", "SOL-USD"]


@pytest.mark.asyncio
async def test_unknown_product_is_skipped_not_guessed(caplog) -> None:
    products = await worker._resolve_autonomous_cycle_products(settings=_settings("ETH-USD,DOGE-USD"), db=None)
    assert products == ["BTC-USD", "ETH-USD"]
    assert "DOGE-USD" not in products


@pytest.mark.asyncio
async def test_btc_and_duplicate_entries_in_config_are_deduplicated() -> None:
    products = await worker._resolve_autonomous_cycle_products(settings=_settings("BTC-USD,ETH-USD,ETH-USD"), db=None)
    assert products == ["BTC-USD", "ETH-USD"]


def test_btc_only_roster_uses_the_original_single_asset_trigger() -> None:
    trigger = worker._resolve_autonomous_cycle_trigger(products=["BTC-USD"])
    assert trigger == worker._AUTONOMOUS_CYCLE_TRIGGER
    assert trigger == "kraken_btc_15m_candle_close"


def test_multi_asset_roster_uses_the_shared_multi_asset_trigger() -> None:
    trigger = worker._resolve_autonomous_cycle_trigger(products=["BTC-USD", "ETH-USD"])
    assert trigger == worker._AUTONOMOUS_MULTI_ASSET_TRIGGER
    assert trigger != worker._AUTONOMOUS_CYCLE_TRIGGER


def test_multi_asset_trigger_does_not_collapse_to_a_single_instrument_when_scoped() -> None:
    """The whole multi-asset design depends on this: _trigger_to_instrument
    (capital_campaign_orchestration.authoritative) must not parse the shared
    trigger as identifying one specific instrument, or campaign composition
    would incorrectly scope down to just that one instrument instead of
    evaluating the full allowed_instruments roster."""
    from app.services.capital_campaign_orchestration.authoritative import _scoped_instruments_for_trigger

    scoped = _scoped_instruments_for_trigger(
        allowed_instruments=["BTC-USD", "ETH-USD", "SOL-USD"],
        trigger=worker._AUTONOMOUS_MULTI_ASSET_TRIGGER,
    )
    assert scoped == ["BTC-USD", "ETH-USD", "SOL-USD"]


def test_single_asset_trigger_still_scopes_to_btc_only() -> None:
    """Confirms the default (BTC-only) trigger's existing scoping behavior
    -- unchanged -- for a campaign whose allowed_instruments already
    includes other products (e.g. mid-rollout, before all cycles are
    switched to the multi-asset trigger)."""
    from app.services.capital_campaign_orchestration.authoritative import _scoped_instruments_for_trigger

    scoped = _scoped_instruments_for_trigger(
        allowed_instruments=["BTC-USD", "ETH-USD"],
        trigger=worker._AUTONOMOUS_CYCLE_TRIGGER,
    )
    assert scoped == ["BTC-USD"]


def test_asset_symbols_known_for_btc_and_configured_additional_products() -> None:
    assert worker._asset_symbols_for_product(product_id="BTC-USD") == ("BTC", "XBT", "XXBT")
    assert worker._asset_symbols_for_product(product_id="ETH-USD") == ("ETH", "XETH")
    assert worker._asset_symbols_for_product(product_id="SOL-USD") == ("SOL",)


def test_asset_symbols_unknown_product_returns_empty_not_a_guess() -> None:
    assert worker._asset_symbols_for_product(product_id="DOGE-USD") == ()


def _candle(asset_id: uuid.UUID | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        asset_id=asset_id or uuid.uuid4(),
        open_time=datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 7, 25, 12, 15, tzinfo=timezone.utc),
    )


# --- _trigger_autonomous_cycles_for_products: multi-asset iteration/isolation ---


@pytest.mark.asyncio
async def test_multi_asset_trigger_evaluates_every_configured_product(monkeypatch: pytest.MonkeyPatch) -> None:
    btc_candle = _candle()
    eth_candle = _candle()
    sol_candle = _candle()
    candles_by_product = {"BTC-USD": btc_candle, "ETH-USD": eth_candle, "SOL-USD": sol_candle}
    triggered: list[str] = []

    async def _fake_btc_cycle(*, db):
        triggered.append("BTC-USD")
        return uuid.uuid4(), btc_candle

    async def _fake_mandate(db):
        return SimpleNamespace(mandate_id=uuid.uuid4())

    async def _fake_candle_loader(db, *, product_id, symbols):
        return candles_by_product[product_id]

    async def _fake_preview_cycle(*, db, request):
        triggered.append(request.product_id)
        return SimpleNamespace(cycle_id=uuid.uuid4(), state="COMPLETE", replayed=False, idempotency_key="k")

    monkeypatch.setattr(worker, "_run_kraken_btc_autonomous_cycle_if_due", _fake_btc_cycle)
    monkeypatch.setattr(worker, "_load_single_active_kraken_mandate", _fake_mandate)
    monkeypatch.setattr(worker, "_load_latest_kraken_asset_15m_candle", _fake_candle_loader)
    monkeypatch.setattr(worker, "run_autonomous_preview_cycle", _fake_preview_cycle)

    results = await worker._trigger_autonomous_cycles_for_products(
        db=object(), products=["BTC-USD", "ETH-USD", "SOL-USD"], trigger=worker._AUTONOMOUS_MULTI_ASSET_TRIGGER,
    )

    assert set(triggered) == {"BTC-USD", "ETH-USD", "SOL-USD"}
    assert set(results.keys()) == {"BTC-USD", "ETH-USD", "SOL-USD"}
    assert all(cycle_id is not None for cycle_id, _identity in results.values())


@pytest.mark.asyncio
async def test_one_asset_failure_does_not_block_evaluation_of_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    btc_candle = _candle()
    sol_candle = _candle()
    triggered: list[str] = []

    async def _fake_btc_cycle(*, db):
        triggered.append("BTC-USD")
        return uuid.uuid4(), btc_candle

    async def _fake_mandate(db):
        return SimpleNamespace(mandate_id=uuid.uuid4())

    async def _fake_candle_loader(db, *, product_id, symbols):
        if product_id == "ETH-USD":
            raise RuntimeError("simulated ETH data outage")
        return sol_candle

    async def _fake_preview_cycle(*, db, request):
        triggered.append(request.product_id)
        return SimpleNamespace(cycle_id=uuid.uuid4(), state="COMPLETE", replayed=False, idempotency_key="k")

    rollback_calls = {"count": 0}

    async def _fake_rollback(*, db):
        rollback_calls["count"] += 1

    monkeypatch.setattr(worker, "_run_kraken_btc_autonomous_cycle_if_due", _fake_btc_cycle)
    monkeypatch.setattr(worker, "_load_single_active_kraken_mandate", _fake_mandate)
    monkeypatch.setattr(worker, "_load_latest_kraken_asset_15m_candle", _fake_candle_loader)
    monkeypatch.setattr(worker, "run_autonomous_preview_cycle", _fake_preview_cycle)
    monkeypatch.setattr(worker, "_rollback_active_session", _fake_rollback)

    results = await worker._trigger_autonomous_cycles_for_products(
        db=object(), products=["BTC-USD", "ETH-USD", "SOL-USD"], trigger=worker._AUTONOMOUS_MULTI_ASSET_TRIGGER,
    )

    # ETH failed and was isolated (no cycle_id); BTC and SOL both still
    # evaluated despite ETH's failure occurring in between them.
    assert triggered == ["BTC-USD", "SOL-USD"]
    assert results["ETH-USD"] == (None, None)
    assert results["BTC-USD"][0] is not None
    assert results["SOL-USD"][0] is not None
    assert rollback_calls["count"] == 1


@pytest.mark.asyncio
async def test_default_single_product_roster_delegates_to_the_original_btc_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    """The multi-asset generalization must be a true no-op for the default
    configuration: with products == ["BTC-USD"] and the original trigger,
    only _run_kraken_btc_autonomous_cycle_if_due is ever called -- the
    generalized per-product path (mandate lookup, generic candle loader) is
    never reached."""
    btc_candle = _candle()
    calls = {"btc_delegate": 0, "generic_mandate": 0, "generic_loader": 0}

    async def _fake_btc_cycle(*, db):
        calls["btc_delegate"] += 1
        return uuid.uuid4(), btc_candle

    async def _unexpected_mandate(db):
        calls["generic_mandate"] += 1
        raise AssertionError("generalized path should not run for the default BTC-only roster")

    async def _unexpected_loader(db, *, product_id, symbols):
        calls["generic_loader"] += 1
        raise AssertionError("generalized path should not run for the default BTC-only roster")

    monkeypatch.setattr(worker, "_run_kraken_btc_autonomous_cycle_if_due", _fake_btc_cycle)
    monkeypatch.setattr(worker, "_load_single_active_kraken_mandate", _unexpected_mandate)
    monkeypatch.setattr(worker, "_load_latest_kraken_asset_15m_candle", _unexpected_loader)

    results = await worker._trigger_autonomous_cycles_for_products(
        db=object(), products=["BTC-USD"], trigger=worker._AUTONOMOUS_CYCLE_TRIGGER,
    )

    assert calls == {"btc_delegate": 1, "generic_mandate": 0, "generic_loader": 0}
    assert results["BTC-USD"][0] is not None
